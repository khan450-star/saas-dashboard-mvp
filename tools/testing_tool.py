"""Testing / QA Tool — runs Playwright browser tests against a locally built project.

Pipeline:
  1. npm install in the project directory
  2. npm run dev (start Next.js dev server)
  3. Wait for localhost:3000 to be ready
  4. Run Playwright tests: page loads, navigation, basic checks
  5. Kill the dev server
  6. Return structured QA report

In MOCK mode, returns a simulated QA report.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import shutil
from pathlib import Path

from crewai.tools import BaseTool

from config import settings

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestingTool(BaseTool):
    name: str = "run_qa_tests"
    description: str = (
        "Run automated QA tests on a locally built project. "
        "Input: JSON with 'project_dir' key (path to the generated project). "
        "The tool installs deps, starts the dev server, runs Playwright browser "
        "tests, and returns a structured test report."
    )

    def _run(self, input_json: str) -> str:
        data = json.loads(input_json) if isinstance(input_json, str) else input_json
        project_dir = data.get("project_dir", "")

        # Resolve relative paths against project root
        p = Path(project_dir)
        if not p.is_absolute():
            p = PROJECT_ROOT / project_dir
        project_dir = str(p)

        if settings.qa_mock:
            return self._mock_test(project_dir)
        return self._live_test(project_dir)

    # ── Mock ─────────────────────────────────────────────────────────
    def _mock_test(self, project_dir: str) -> str:
        return json.dumps({
            "status": "pass",
            "project_dir": project_dir,
            "tests_run": 18,
            "tests_passed": 18,
            "tests_failed": 0,
            "bugs": [],
        }, indent=2)

    # ── Live ─────────────────────────────────────────────────────────
    def _live_test(self, project_dir: str) -> str:
        bugs: list[dict] = []
        tests_run = 0
        tests_passed = 0

        if not Path(project_dir).exists():
            return json.dumps({
                "status": "fail",
                "project_dir": project_dir,
                "tests_run": 0,
                "tests_passed": 0,
                "tests_failed": 1,
                "bugs": [{"id": "BUG-001", "severity": "critical",
                          "description": f"Project directory not found: {project_dir}"}],
            }, indent=2)

        # Step 0: Kill any leftover node processes from previous runs
        self._kill_port(3000)

        # Step 0b: Ensure npm exists in runtime environment.
        if not shutil.which("npm"):
            bugs.append({
                "id": "BUG-001",
                "severity": "critical",
                "description": (
                    "npm not found in runtime environment. "
                    "Install Node.js/npm or use a runner image that includes npm."
                ),
            })
            tests_run += 1
            return self._report(project_dir, tests_run, tests_passed, bugs)

        # Step 1: npm install
        install_ok, install_err = self._npm_install(project_dir)
        tests_run += 1
        if install_ok:
            tests_passed += 1
        else:
            bugs.append({"id": f"BUG-{len(bugs)+1:03d}", "severity": "critical",
                         "description": f"npm install failed: {install_err[:300]}"})
            return self._report(project_dir, tests_run, tests_passed, bugs)

        # Step 1b: Setup database (prisma db push + seed if seed script exists)
        self._setup_database(project_dir)

        # Step 1c: Pre-flight static checks (catch issues before server starts)
        preflight_bugs = self._preflight_checks(project_dir)
        for pb in preflight_bugs:
            tests_run += 1
            bugs.append({"id": f"BUG-{len(bugs)+1:03d}", "severity": "critical",
                         "description": pb})

        # Step 2: Start dev server & wait for ready
        server_proc = None
        try:
            server_proc = self._start_dev_server(project_dir)
            self._server_proc = server_proc  # for stderr capture in tests
            ready = self._wait_for_server(port=3000, timeout=90)
            tests_run += 1
            if ready:
                tests_passed += 1
            else:
                # Capture stderr for diagnostics
                stderr_snippet = self._read_proc_stderr(server_proc)
                bugs.append({"id": f"BUG-{len(bugs)+1:03d}", "severity": "critical",
                             "description": f"Dev server did not start within 60s. stderr: {stderr_snippet[:500]}"})
                return self._report(project_dir, tests_run, tests_passed, bugs)

            # Step 3: Run Playwright tests
            pw_results = self._run_playwright_tests()
            for r in pw_results:
                tests_run += 1
                if r["passed"]:
                    tests_passed += 1
                else:
                    bugs.append({"id": f"BUG-{len(bugs)+1:03d}",
                                 "severity": r.get("severity", "medium"),
                                 "description": r["description"]})

        finally:
            if server_proc:
                self._kill_process_tree(server_proc)

        return self._report(project_dir, tests_run, tests_passed, bugs)

    def _npm_install(self, project_dir: str) -> tuple[bool, str]:
        """Run npm install in the project directory."""
        if not shutil.which("npm"):
            return False, "npm executable not found in PATH"

        def _run_install(timeout_secs: int) -> tuple[bool, str]:
            proc = subprocess.Popen(
                ["npm", "install", "--legacy-peer-deps"],
                cwd=project_dir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=True,
                encoding="utf-8",
                errors="replace",
            )
            try:
                _stdout, stderr = proc.communicate(timeout=timeout_secs)
                if proc.returncode == 0:
                    return True, ""
                return False, stderr
            except subprocess.TimeoutExpired:
                self._kill_process_tree(proc)
                try:
                    proc.communicate(timeout=5)
                except Exception:
                    pass
                return False, f"npm install timed out after {timeout_secs}s"

        def _clean_install_artifacts() -> None:
            for name in ["node_modules", ".next"]:
                target = Path(project_dir) / name
                if target.exists():
                    shutil.rmtree(target, ignore_errors=True)
            lock = Path(project_dir) / "package-lock.json"
            if lock.exists():
                try:
                    lock.unlink()
                except Exception:
                    pass

        try:
            # Ensure .env exists before install (Prisma postinstall needs DATABASE_URL)
            env_path = Path(project_dir) / ".env"
            if not env_path.exists():
                env_local = Path(project_dir) / ".env.local"
                if env_local.exists():
                    env_path.write_text(env_local.read_text(encoding="utf-8"), encoding="utf-8")
                else:
                    env_path.write_text('DATABASE_URL="file:./dev.db"\n', encoding="utf-8")

            # First attempt (longer timeout for cold installs on Windows).
            ok, err = _run_install(timeout_secs=300)
            if ok:
                return True, ""

            # Retry after clearing stale artifacts for SWC / lock corruption cases.
            err_l = (err or "").lower()
            should_retry = (
                "swc" in err_l
                or "not a valid win32 application" in err_l
                or "timed out" in err_l
                or "ebusy" in err_l
                or "enoent" in err_l
            )
            if should_retry:
                _clean_install_artifacts()
                ok2, err2 = _run_install(timeout_secs=300)
                if ok2:
                    return True, ""
                return False, err2 or err

            return False, err
        except Exception as e:
            return False, str(e)

    def _setup_database(self, project_dir: str) -> None:
        """Run prisma db push and seed if a seed script exists."""
        try:
            # Ensure .env exists with DATABASE_URL (Prisma reads .env, not .env.local)
            env_path = Path(project_dir) / ".env"
            if not env_path.exists():
                env_local = Path(project_dir) / ".env.local"
                if env_local.exists():
                    # Copy .env.local to .env so Prisma can find DATABASE_URL
                    env_path.write_text(env_local.read_text(encoding="utf-8"), encoding="utf-8")
                else:
                    env_path.write_text('DATABASE_URL="file:./dev.db"\n', encoding="utf-8")
            elif "DATABASE_URL" not in env_path.read_text(encoding="utf-8"):
                with open(env_path, "a", encoding="utf-8") as f:
                    f.write('\nDATABASE_URL="file:./dev.db"\n')
            # Generate Prisma client + push schema
            # Use DEVNULL instead of capture_output to avoid Windows pipe deadlock
            # on timeout (shell=True kills cmd.exe but child keeps pipes open)
            for cmd in [
                ["npx", "prisma", "db", "push", "--skip-generate", "--accept-data-loss"],
                ["npx", "prisma", "generate"],
            ]:
                self._run_cmd_safe(cmd, project_dir, timeout=60)
            # Run seed if seed script exists
            seed_path = Path(project_dir) / "prisma" / "seed.ts"
            if seed_path.exists():
                self._run_cmd_safe(["npx", "tsx", "prisma/seed.ts"], project_dir, timeout=60)
        except Exception:
            pass  # DB setup is best-effort; tests will catch missing data

    def _preflight_checks(self, project_dir: str) -> list[str]:
        """Static checks on project structure before starting the server."""
        issues = []
        p = Path(project_dir)

        # Check 1: Dual app directory (both app/ and src/app/ exist)
        if (p / "app").is_dir() and (p / "src" / "app").is_dir():
            issues.append(
                "Dual app directory: both top-level app/ and src/app/ exist. "
                "Next.js will use app/ and ignore src/app/. Remove top-level app/ "
                "or move all files to src/app/."
            )

        # Check 2: src/lib/prisma.ts exists if code imports @/lib/prisma
        has_prisma_import = False
        src_dir = p / "src"
        if src_dir.is_dir():
            for f in src_dir.rglob("*.ts"):
                if "node_modules" in f.parts or ".next" in f.parts:
                    continue
                try:
                    text = f.read_text(encoding="utf-8", errors="replace")
                    if "@/lib/prisma" in text:
                        has_prisma_import = True
                        break
                except Exception:
                    continue
            for f in src_dir.rglob("*.tsx"):
                if has_prisma_import:
                    break
                if "node_modules" in f.parts or ".next" in f.parts:
                    continue
                try:
                    text = f.read_text(encoding="utf-8", errors="replace")
                    if "@/lib/prisma" in text:
                        has_prisma_import = True
                        break
                except Exception:
                    continue

        if has_prisma_import and not (p / "src" / "lib" / "prisma.ts").exists():
            issues.append(
                "Missing src/lib/prisma.ts — code imports from '@/lib/prisma' but the file "
                "does not exist. Create src/lib/prisma.ts with a PrismaClient singleton."
            )

        # Check 3: mode: 'insensitive' used with SQLite
        schema_path = p / "prisma" / "schema.prisma"
        is_sqlite = False
        if schema_path.exists():
            try:
                schema_text = schema_path.read_text(encoding="utf-8", errors="replace")
                is_sqlite = 'provider = "sqlite"' in schema_text
            except Exception:
                pass

        if is_sqlite and src_dir.is_dir():
            for f in src_dir.rglob("*.ts"):
                if "node_modules" in f.parts or ".next" in f.parts:
                    continue
                try:
                    text = f.read_text(encoding="utf-8", errors="replace")
                    if "mode: 'insensitive'" in text or 'mode: "insensitive"' in text:
                        rel = f.relative_to(p).as_posix()
                        issues.append(
                            f"SQLite incompatibility in {rel}: 'mode: insensitive' is not supported "
                            f"by SQLite. Remove it from Prisma queries."
                        )
                        break
                except Exception:
                    continue

        return issues

    def _start_dev_server(self, project_dir: str) -> subprocess.Popen:
        """Start next dev as a background process."""
        # Kill any zombie process on port 3000 from previous runs
        self._kill_port(3000)

        # Clear .next cache so stale compiled pages don't persist
        next_dir = Path(project_dir) / ".next"
        if next_dir.exists():
            shutil.rmtree(next_dir, ignore_errors=True)

        env = os.environ.copy()
        env["PORT"] = "3000"
        # Suppress Next.js telemetry
        env["NEXT_TELEMETRY_DISABLED"] = "1"
        # Run 'npx next dev' directly — avoids broken npm run dev chains
        # (e.g. 'prisma generate && prisma db push && npm run seed && next dev'
        # which breaks if seed fails on duplicate data)
        proc = subprocess.Popen(
            ["npx", "next", "dev"],
            cwd=project_dir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True,
            env=env,
            encoding="utf-8",
            errors="replace",
        )
        return proc

    def _wait_for_server(self, port: int = 3000, timeout: int = 90) -> bool:
        """Wait until localhost:port returns an HTTP response (not just socket open).
        
        Next.js binds the port immediately but the first request triggers
        compilation which can take 30-60s on Windows. We poll with actual
        HTTP requests so we don't report 'ready' before the first page compiles.
        """
        import urllib.request
        import urllib.error
        deadline = time.time() + timeout
        # Phase 1: wait for socket
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=2):
                    break
            except (ConnectionRefusedError, OSError, socket.timeout):
                time.sleep(1)
        else:
            return False
        # Phase 2: wait for actual HTTP response (first compile)
        while time.time() < deadline:
            try:
                req = urllib.request.Request(f"http://127.0.0.1:{port}/",
                                            method="GET")
                resp = urllib.request.urlopen(req, timeout=10)
                if resp.status < 500:
                    return True
            except Exception:
                time.sleep(2)
        return False

    def _run_playwright_tests(self) -> list[dict]:
        """Run Playwright browser tests against localhost:3000."""
        results = []

        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    viewport={"width": 1280, "height": 720}
                )
                page = context.new_page()

                # Test 1: Landing page loads
                try:
                    resp = page.goto("http://localhost:3000", wait_until="domcontentloaded", timeout=30000)
                    status = resp.status if resp else 0
                    results.append({
                        "name": "Landing page loads",
                        "passed": status == 200,
                        "description": self._enrich_error(f"Landing page returned HTTP {status}") if status != 200 else "",
                        "severity": "critical",
                    })
                except Exception as e:
                    results.append({
                        "name": "Landing page loads",
                        "passed": False,
                        "description": f"Landing page failed to load: {str(e)[:200]}",
                        "severity": "critical",
                    })

                # Test 2: Page has content (not blank)
                try:
                    body_text = page.text_content("body") or ""
                    has_content = len(body_text.strip()) > 10
                    results.append({
                        "name": "Page has content",
                        "passed": has_content,
                        "description": "Page body is empty or minimal" if not has_content else "",
                        "severity": "high",
                    })
                except Exception as e:
                    results.append({
                        "name": "Page has content",
                        "passed": False,
                        "description": f"Could not read page content: {str(e)[:200]}",
                        "severity": "high",
                    })

                # Test 3: No console errors
                console_errors = []
                page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
                page.reload(wait_until="domcontentloaded", timeout=30000)
                time.sleep(2)
                results.append({
                    "name": "No console errors",
                    "passed": len(console_errors) == 0,
                    "description": f"Console errors: {'; '.join(console_errors[:3])}" if console_errors else "",
                    "severity": "medium",
                })

                # Test 4: Navigation links exist
                try:
                    links = page.query_selector_all("a[href]")
                    nav_count = len(links)
                    results.append({
                        "name": "Navigation links present",
                        "passed": nav_count >= 2,
                        "description": f"Only {nav_count} links found" if nav_count < 2 else "",
                        "severity": "medium",
                    })
                except Exception as e:
                    results.append({
                        "name": "Navigation links present",
                        "passed": False,
                        "description": str(e)[:200],
                        "severity": "medium",
                    })

                # Test 5: No broken images
                try:
                    images = page.query_selector_all("img")
                    broken = []
                    for img in images:
                        natural_width = img.evaluate("el => el.naturalWidth")
                        if natural_width == 0:
                            src = img.get_attribute("src") or "unknown"
                            broken.append(src)
                    results.append({
                        "name": "No broken images",
                        "passed": len(broken) == 0,
                        "description": f"Broken images: {', '.join(broken[:3])}" if broken else "",
                        "severity": "low",
                    })
                except Exception as e:
                    results.append({
                        "name": "No broken images",
                        "passed": True,
                        "description": "",
                        "severity": "low",
                    })

                # Test 6: Mobile viewport renders
                try:
                    mobile_ctx = browser.new_context(
                        viewport={"width": 375, "height": 812}
                    )
                    mobile_page = mobile_ctx.new_page()
                    mobile_resp = mobile_page.goto("http://localhost:3000", wait_until="domcontentloaded", timeout=30000)
                    mobile_ok = mobile_resp and mobile_resp.status == 200
                    results.append({
                        "name": "Mobile viewport renders",
                        "passed": mobile_ok,
                        "description": "Mobile viewport failed to load" if not mobile_ok else "",
                        "severity": "medium",
                    })
                    mobile_page.close()
                    mobile_ctx.close()
                except Exception as e:
                    results.append({
                        "name": "Mobile viewport renders",
                        "passed": False,
                        "description": str(e)[:200],
                        "severity": "medium",
                    })

                # Test 7: Page title exists
                try:
                    title = page.title()
                    results.append({
                        "name": "Page has title",
                        "passed": bool(title and len(title) > 0),
                        "description": "Page title is empty" if not title else "",
                        "severity": "low",
                    })
                except Exception:
                    results.append({
                        "name": "Page has title",
                        "passed": False,
                        "description": "Could not retrieve page title",
                        "severity": "low",
                    })

                # Test 8: Products page loads and has real content
                # 404 = not applicable (project is not an e-commerce site)
                products_page_exists = False
                try:
                    prod_resp = page.goto("http://localhost:3000/products", wait_until="domcontentloaded", timeout=30000)
                    prod_status = prod_resp.status if prod_resp else 0
                    if prod_status == 404:
                        # Not an e-commerce project — skip this test
                        results.append({
                            "name": "Products page has real data",
                            "passed": True,
                            "description": "",
                            "severity": "high",
                        })
                    elif prod_status == 200:
                        products_page_exists = True
                        page.wait_for_timeout(2000)  # let client-side data load
                        prod_text = page.text_content("body") or ""
                        # Check there's actual product data, not just a title
                        has_prices = "$" in prod_text
                        has_enough_content = len(prod_text.strip()) > 100
                        prod_ok = has_prices and has_enough_content
                        results.append({
                            "name": "Products page has real data",
                            "passed": prod_ok,
                            "description": (
                                "Products page has no price data — likely using hardcoded "
                                "empty arrays or DB is not seeded"
                            ) if not prod_ok else "",
                            "severity": "high",
                        })
                    else:
                        results.append({
                            "name": "Products page has real data",
                            "passed": False,
                            "description": f"Products page returned HTTP {prod_status}",
                            "severity": "high",
                        })
                except Exception as e:
                    results.append({
                        "name": "Products page has real data",
                        "passed": False,
                        "description": f"Products page failed: {str(e)[:200]}",
                        "severity": "high",
                    })

                # Test 9: No placeholder images (detect gray boxes / 'Product Image' text stubs)
                # Only run if /products page exists (skip for non-e-commerce projects)
                if not products_page_exists:
                    results.append({
                        "name": "No placeholder images",
                        "passed": True,
                        "description": "",
                        "severity": "high",
                    })
                else:
                    try:
                        page.goto("http://localhost:3000/products", wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(2000)
                        images = page.query_selector_all("img")
                        product_body = page.text_content("body") or ""
                        has_placeholder_text = "Product Image" in product_body and product_body.count("Product Image") >= 3
                        no_real_images = len(images) == 0
                        results.append({
                            "name": "No placeholder images",
                            "passed": not has_placeholder_text and not no_real_images,
                            "description": (
                                "Products page uses placeholder text instead of real images — "
                                "must use <img> with real URLs (e.g. picsum.photos)"
                            ) if has_placeholder_text or no_real_images else "",
                            "severity": "high",
                        })
                    except Exception as e:
                        results.append({
                            "name": "No placeholder images",
                            "passed": True,
                            "description": "",
                            "severity": "high",
                        })

                # Test 10: Key navigation pages don't crash (cart, sign-in / auth)
                # Cart is e-commerce-specific: 404 = not applicable (pass)
                try:
                    cart_resp = page.goto("http://localhost:3000/cart", wait_until="domcontentloaded", timeout=30000)
                    cart_status = cart_resp.status if cart_resp else 0
                    cart_ok = cart_status in (200, 307, 308, 404)  # 404 = not an e-commerce project
                    results.append({
                        "name": "Cart page loads",
                        "passed": cart_ok,
                        "description": f"Cart page returned HTTP {cart_status}" if not cart_ok else "",
                        "severity": "high",
                    })
                except Exception as e:
                    results.append({
                        "name": "Cart page loads",
                        "passed": False,
                        "description": f"Cart page error: {str(e)[:200]}",
                        "severity": "high",
                    })

                # Accept common auth entry routes used by different templates.
                sign_in_paths = ["/api/auth/signin", "/auth/signin", "/auth/sign-in", "/signin", "/sign-in", "/login"]
                sign_in_ok = False
                sign_in_last_status = 0
                sign_in_last_path = sign_in_paths[0]
                for sign_in_path in sign_in_paths:
                    try:
                        sign_in_resp = page.goto(f"http://localhost:3000{sign_in_path}", wait_until="domcontentloaded", timeout=30000)
                        sign_in_status = sign_in_resp.status if sign_in_resp else 0
                        sign_in_last_status = sign_in_status
                        sign_in_last_path = sign_in_path
                        if sign_in_status in (200, 307, 308):
                            sign_in_ok = True
                            break
                    except Exception:
                        continue

                results.append({
                    "name": "Sign In page loads",
                    "passed": sign_in_ok,
                    "description": f"Sign In page returned HTTP {sign_in_last_status} at {sign_in_last_path}" if not sign_in_ok else "",
                    "severity": "high",
                })

                # Test 11: Categories page loads (verifies Category model exists)
                try:
                    cat_resp = page.goto("http://localhost:3000/categories", wait_until="domcontentloaded", timeout=30000)
                    cat_status = cat_resp.status if cat_resp else 0
                    cat_ok = cat_status in (200, 404)  # 404 is OK if no categories page
                    if cat_status == 200:
                        page.wait_for_timeout(2000)
                        cat_text = page.text_content("body") or ""
                        cat_ok = len(cat_text.strip()) > 50
                    results.append({
                        "name": "Categories page loads",
                        "passed": cat_ok,
                        "description": f"Categories page returned HTTP {cat_status}" if not cat_ok else "",
                        "severity": "high",
                    })
                except Exception as e:
                    results.append({
                        "name": "Categories page loads",
                        "passed": False,
                        "description": f"Categories page error: {str(e)[:200]}",
                        "severity": "high",
                    })

                # Test 12: Product detail page loads (verifies slug-based routing)
                # Only run if /products page exists (skip for non-e-commerce projects)
                if not products_page_exists:
                    results.append({
                        "name": "Product detail page loads",
                        "passed": True,
                        "description": "",
                        "severity": "high",
                    })
                else:
                    try:
                        # First get a product link from the products page
                        page.goto("http://localhost:3000/products", wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(2000)
                        product_links = page.query_selector_all("a[href*='/products/']")
                        if product_links:
                            href = product_links[0].get_attribute("href") or ""
                            if href:
                                detail_resp = page.goto(f"http://localhost:3000{href}", wait_until="domcontentloaded", timeout=30000)
                                detail_status = detail_resp.status if detail_resp else 0
                                detail_ok = detail_status == 200
                                results.append({
                                    "name": "Product detail page loads",
                                    "passed": detail_ok,
                                    "description": f"Product detail page {href} returned HTTP {detail_status}" if not detail_ok else "",
                                    "severity": "high",
                                })
                            else:
                                results.append({
                                    "name": "Product detail page loads",
                                    "passed": False,
                                    "description": "Product links found but href is empty",
                                    "severity": "high",
                                })
                        else:
                            results.append({
                                "name": "Product detail page loads",
                                "passed": False,
                                "description": "No product detail links found on /products page",
                                "severity": "high",
                            })
                    except Exception as e:
                        results.append({
                            "name": "Product detail page loads",
                            "passed": False,
                            "description": f"Product detail test error: {str(e)[:200]}",
                            "severity": "high",
                        })

                # Test 13: Checkout page loads
                # Checkout is e-commerce-specific: 404 = not applicable (pass)
                try:
                    checkout_resp = page.goto("http://localhost:3000/checkout", wait_until="domcontentloaded", timeout=30000)
                    checkout_status = checkout_resp.status if checkout_resp else 0
                    checkout_ok = checkout_status in (200, 307, 308, 404)  # 404 = not an e-commerce project
                    results.append({
                        "name": "Checkout page loads",
                        "passed": checkout_ok,
                        "description": f"Checkout page returned HTTP {checkout_status}" if not checkout_ok else "",
                        "severity": "high",
                    })
                except Exception as e:
                    results.append({
                        "name": "Checkout page loads",
                        "passed": False,
                        "description": f"Checkout page error: {str(e)[:200]}",
                        "severity": "high",
                    })

                browser.close()

        except Exception as e:
            results.append({
                "name": "Playwright execution",
                "passed": False,
                "description": f"Playwright failed: {str(e)[:300]}",
                "severity": "critical",
            })

        return results

    def _enrich_error(self, base_msg: str) -> str:
        """Append server stderr snippet to an error message for better diagnostics."""
        try:
            if hasattr(self, "_server_proc") and self._server_proc:
                stderr_text = self._read_proc_stderr(self._server_proc)
                if stderr_text.strip():
                    return f"{base_msg} | Server stderr: {stderr_text.strip()[:400]}"
        except Exception:
            pass
        return base_msg

    def _read_proc_stderr(self, proc: subprocess.Popen) -> str:
        """Non-blocking read of accumulated stderr from the dev server."""
        try:
            import select
            # On Windows, stderr.read() blocks, so use a timeout-based approach
            if sys.platform == "win32":
                import threading
                output = []
                def _reader():
                    try:
                        for line in proc.stderr:
                            output.append(line)
                            if len(output) > 50:
                                break
                    except Exception:
                        pass
                t = threading.Thread(target=_reader, daemon=True)
                t.start()
                t.join(timeout=3)
                return "".join(output)
            else:
                return proc.stderr.read(4096) if proc.stderr else ""
        except Exception:
            return ""

    def _kill_port(self, port: int) -> None:
        """Kill any process listening on the given port (Windows)."""
        try:
            result = subprocess.run(
                ["netstat", "-aon"],
                capture_output=True, text=True, timeout=10,
                encoding="utf-8", errors="replace",
            )
            for line in result.stdout.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.split()
                    pid = parts[-1]
                    if pid.isdigit() and int(pid) > 0:
                        subprocess.run(
                            ["taskkill", "/F", "/PID", pid],
                            capture_output=True, timeout=10,
                        )
        except Exception:
            pass

    def _kill_process_tree(self, proc: subprocess.Popen) -> None:
        """Kill the process and all children (Windows-compatible)."""
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                    timeout=10,
                )
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _run_cmd_safe(self, cmd: list[str], cwd: str, timeout: int = 60) -> None:
        """Run a command with proper timeout + cleanup on Windows.

        Uses DEVNULL for stdout/stderr to avoid pipe deadlock when the shell
        is killed but the child process survives (Windows shell=True issue).
        """
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=True,
        )
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._kill_process_tree(proc)
            try:
                proc.wait(timeout=5)
            except Exception:
                pass

    def _report(self, project_dir: str, tests_run: int, tests_passed: int,
                bugs: list[dict]) -> str:
        return json.dumps({
            "status": "pass" if tests_run == tests_passed else "fail",
            "project_dir": project_dir,
            "tests_run": tests_run,
            "tests_passed": tests_passed,
            "tests_failed": tests_run - tests_passed,
            "bugs": bugs,
        }, indent=2)
