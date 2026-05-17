"""Fixer Tool — takes QA bug reports + project source, calls Claude to fix the code.

Used in the self-healing loop: Auditor finds bugs → Fixer patches files → Auditor retests.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import httpx
from crewai.tools import BaseTool

from config import settings

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class FixerTool(BaseTool):
    name: str = "fix_bugs"
    description: str = (
        "Given a JSON object with 'project_dir' and 'bugs' (array of bug reports), "
        "read the project source files, call Claude to generate fixes, and write "
        "the patched files. Returns a summary of files changed."
    )

    def _run(self, input_json: str) -> str:
        data = json.loads(input_json) if isinstance(input_json, str) else input_json
        project_dir = data.get("project_dir", "")
        bugs = data.get("bugs", [])

        p = Path(project_dir)
        if not p.is_absolute():
            p = PROJECT_ROOT / project_dir

        if not p.exists():
            return json.dumps({"status": "error", "message": f"Project dir not found: {p}"})

        if not bugs:
            return json.dumps({"status": "no_bugs", "message": "No bugs to fix"})

        # Gather source files (skip node_modules, .next, binary files)
        source_files = self._read_source_files(p)

        # Ask Claude to fix the bugs
        fixes = self._get_fixes(source_files, bugs)

        # Write fixed files
        files_changed = 0
        for rel_path, content in fixes.items():
            clean = Path(rel_path).as_posix().lstrip("/")
            if ".." in clean:
                continue
            # Ensure content is a string (Claude might return nested objects)
            if not isinstance(content, str):
                content = json.dumps(content, indent=2)
            full = p / clean
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")
            files_changed += 1

        return json.dumps({
            "status": "fixed",
            "files_changed": files_changed,
            "files": list(fixes.keys()),
        }, indent=2)

    def _read_source_files(self, project_dir: Path) -> dict[str, str]:
        """Read all source files from the project (limited to common web extensions)."""
        files = {}
        extensions = {".ts", ".tsx", ".js", ".jsx", ".json", ".css", ".prisma", ".md", ".mjs"}
        skip_dirs = {"node_modules", ".next", ".git", "dist", "build"}

        for f in project_dir.rglob("*"):
            if f.is_file() and f.suffix in extensions:
                # Skip excluded directories
                if any(part in skip_dirs for part in f.parts):
                    continue
                rel = f.relative_to(project_dir).as_posix()
                try:
                    files[rel] = f.read_text(encoding="utf-8")
                except Exception:
                    continue
        return files

    def _get_fixes(self, source_files: dict[str, str], bugs: list[dict]) -> dict[str, str]:
        """Call Claude to fix the bugs in the source files."""
        # Build a compact view of files
        file_listing = ""
        for path, content in source_files.items():
            file_listing += f"\n--- {path} ---\n{content}\n"

        bug_listing = "\n".join(
            f"- [{b.get('severity', 'medium')}] {b.get('id', 'BUG')}: {b.get('description', '')}"
            for b in bugs
        )

        prompt = (
            "You are a senior bug-fixer for Aigentic Orbit.\n"
            "Below are the BUGS found by QA testing, followed by ALL source files.\n"
            "Fix ONLY the files that need changes to resolve these bugs.\n\n"
            "CRITICAL:\n"
            "- Output ONLY a JSON object (no markdown fences, no explanation).\n"
            "- Keys = relative file paths of files you changed.\n"
            "- Values = the complete fixed file contents.\n"
            "- Only include files that ACTUALLY need changes for these specific bugs.\n"
            "- BE MINIMAL: Change as FEW files as possible. Do NOT touch files that work. "
            "Do NOT change prisma/schema.prisma unless the bug explicitly mentions a schema error. "
            "Do NOT change .env/.env.local unless the bug mentions missing environment variables. "
            "Do NOT change package.json unless the bug mentions missing dependencies.\n"
            "- Common Next.js fixes: add 'use client' for interactive components, "
            "ensure all imports exist in package.json, provide .env.local with dummy values, "
            "use next.config.js without deprecated options.\n"
            "- Put ALL app files under src/app/ — do NOT create a top-level app/ directory.\n"
            "- ALWAYS include a .env file (not just .env.local) with DATABASE_URL=\"file:./dev.db\" — "
            "Prisma reads .env, not .env.local, and npm install will fail without it.\n"
            "- The 'dev' script in package.json MUST remain just 'next dev' — do NOT add "
            "prisma generate, prisma db push, or seed commands to the dev script.\n"
            "- If products/items show placeholder text instead of images, replace with <img> using "
            "real URLs from https://picsum.photos/seed/<item>/600/600. "
            "NEVER use a <div> with text as a fake image. Every product MUST have an <img> tag.\n"
            "- If products use hardcoded arrays in components, rewrite to fetch from Prisma DB.\n"
            "- If the DB is empty and there is no seed script, create prisma/seed.ts that inserts "
            "8-12 sample items with categories and real image URLs. Use upsert() not create() "
            "so the seed script is idempotent and can run multiple times.\n"
            "- Prisma upsert() WHERE clause MUST use a @unique or @id field. "
            "If upserting by name, add @unique to the name field in schema.prisma.\n"
            "- If auth pages crash or return 404, ensure src/app/api/auth/[...nextauth]/route.ts exists "
            "and exports GET and POST. NextAuth serves sign-in at /api/auth/signin automatically. "
            "For a 404 on sign-in, you ONLY need to create the [...nextauth]/route.ts file — "
            "do NOT change schema.prisma, .env, or package.json for this fix.\n"
            "- BOTH .env AND .env.local MUST contain: DATABASE_URL, NEXTAUTH_SECRET=\\\"dev-secret-key-change-in-production\\\", "
            "NEXTAUTH_URL=http://localhost:3000, STRIPE_SECRET_KEY, STRIPE_PUBLISHABLE_KEY.\n"
            "- If auth pages crash, wrap NextAuth config to handle missing/dummy credentials gracefully.\n"
            "- If API routes throw unhandled errors, add try/catch and return JSON error responses.\n"
            "- If an import error like 'X is not exported', check if the file uses default export "
            "(export default function X) but the import uses braces ({ X }), or vice versa. "
            "Fix the import to match the export style. Default exports: import X from '...'. "
            "Named exports: import { X } from '...'.\n"
            "- The app MUST start with 'npm run dev' and render at localhost:3000.\n"
            "- If 'Module not found: @/lib/prisma' — create src/lib/prisma.ts with a singleton "
            "PrismaClient (global caching for dev mode). This file is required for all DB access.\n"
            "- If 'Cannot read properties of undefined (reading findMany)' for a model like "
            "'prisma.category.findMany()' — the Prisma schema is missing that model. "
            "Add the missing model to schema.prisma AND update the seed script to populate it. "
            "Ensure all fields referenced in code exist in the schema (e.g. slug, image, stock, categoryId).\n"
            "- If code references product.slug, product.image, product.stock, product.categoryId "
            "but schema uses different names (e.g. imageUrl instead of image), rename the schema fields "
            "to match the code, NOT the other way around.\n"
            "- SQLite does NOT support 'mode: insensitive'. Remove it from ALL Prisma queries. "
            "Change { contains: search, mode: 'insensitive' } to just { contains: search }.\n"
            "- Remove references to non-existent schema fields (e.g. 'featured' on Product, "
            "'isAdmin' on User) from API routes, GraphQL schemas, and components.\n"
            "- Do NOT create files in a top-level app/ directory — only use src/app/. "
            "Do NOT create files in a top-level components/ directory — only use src/components/.\n\n"
            f"BUGS:\n{bug_listing}\n\n"
            f"SOURCE FILES:\n{file_listing}"
        )

        # Truncate if too long (Claude context limit)
        if len(prompt) > 180000:
            prompt = prompt[:180000] + "\n\n[TRUNCATED — fix the most critical bugs first]"

        # Retry with backoff for rate limits
        for attempt in range(3):
            try:
                resp = httpx.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": settings.anthropic_api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": settings.llm_model,
                        "max_tokens": 16000,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                    timeout=300,
                )
                if resp.status_code == 429:
                    wait = 30 * (attempt + 1)
                    print(f"  Rate limited, waiting {wait}s...")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                break
            except httpx.HTTPStatusError:
                if attempt == 2:
                    raise
                time.sleep(15)
        data = resp.json()

        content = data["content"][0]["text"]

        # Extract JSON: find first { and last }
        content = content.strip()
        first_brace = content.find("{")
        last_brace = content.rfind("}")
        if first_brace >= 0 and last_brace > first_brace:
            content = content[first_brace:last_brace + 1]

        # Repair truncated JSON
        stop = data.get("stop_reason", "")
        if stop == "max_tokens" or not content.endswith("}"):
            last_good = content.rfind('",')
            if last_good > 0:
                content = content[:last_good + 1] + "}"

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            start = content.find("{")
            end = content.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(content[start:end + 1])
                except json.JSONDecodeError:
                    pass
            # Save debug info
            debug_path = PROJECT_ROOT / "output" / "_debug_fixer_response.txt"
            debug_path.write_text(f"PARSE FAILED\n\n{content[:5000]}", encoding="utf-8")
            return {}
