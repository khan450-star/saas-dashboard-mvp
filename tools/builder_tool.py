"""Builder Tool — generates code via Claude API and writes to local output folder.

Pipeline:
  1. Claude API generates the full codebase from the TSD.
  2. Files are written to output/<project-slug>/.

In MOCK mode, returns a simulated result.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
from crewai.tools import BaseTool

from config import settings

# Resolve output dir relative to project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class BuilderTool(BaseTool):
    name: str = "build_and_deploy"
    description: str = (
        "Given a Technical Specification Document (TSD) as JSON, generate the "
        "full codebase with Claude and write it to a local output folder. "
        "Returns the local path and file count."
    )

    def _run(self, tsd_json: str) -> str:
        if settings.builder_mock:
            return self._mock_build(tsd_json)
        return self._live_build(tsd_json)

    # ── Mock ─────────────────────────────────────────────────────────
    def _mock_build(self, tsd_json: str) -> str:
        tsd = json.loads(tsd_json) if isinstance(tsd_json, str) else tsd_json
        name = tsd.get("project_name", "demo-project")
        slug = self._slugify(name)
        return json.dumps({
            "project_dir": f"output/{slug}",
            "files_generated": 42,
            "build_status": "success",
        }, indent=2)

    # ── Live ─────────────────────────────────────────────────────────
    def _live_build(self, tsd_json: str) -> str:
        tsd = json.loads(tsd_json) if isinstance(tsd_json, str) else tsd_json
        name = tsd.get("project_name", "project")
        slug = self._slugify(name)

        # Step 1: Ask Claude to generate the full project files
        code_files = self._generate_code(tsd)

        # Step 2: Write files to output/<slug>/
        project_dir = self._write_files(code_files, slug)

        return json.dumps({
            "project_dir": str(project_dir),
            "files_generated": len(code_files),
            "build_status": "success",
        }, indent=2)

    def _generate_code(self, tsd: dict) -> dict[str, str]:
        """Call Claude API to generate project files from the TSD."""
        prompt = (
            "You are a senior full-stack code generator for Aigentic Orbit.\n"
            "Given the TSD below, produce EVERY file needed for a complete, "
            "locally-runnable project.\n\n"
            "CRITICAL RULES — the project MUST start with `npm run dev` and "
            "render at localhost:3000 with ZERO configuration:\n"
            "- Output ONLY a single JSON object (no markdown fences, no explanation).\n"
            "- Keys = relative file paths (e.g. 'src/app/page.tsx', 'package.json').\n"
            "- Values = the full file contents as strings.\n"
            "- Include: package.json, tsconfig.json, tailwind.config.js, postcss.config.js, "
            "all pages, all components, API routes, layout, globals.css, .env.local, README.md.\n"
            "- Use the exact stack from the TSD.\n"
            "- Make the code complete and runnable — no TODOs or placeholders.\n"
            "- Keep files concise but functional. Aim for ~25-35 files total.\n\n"
            "DATA & COMPLETENESS RULES (these are mandatory):\n"
            "- Pages that display data (products, categories, etc.) MUST fetch from the database "
            "using Prisma — NEVER use hardcoded arrays or mock data in components.\n"
            "- Include a prisma/seed.ts script that populates the database with at least 8-12 "
            "realistic sample items (products, categories, etc.) with real placeholder images "
            "from https://picsum.photos/seed/<item-name>/600/600. "
            "The seed script MUST be idempotent — use upsert() instead of create() so it "
            "can run multiple times without failing on duplicate data.\n"
            "- Product/item images MUST use <img> or next/image with real URLs (picsum.photos) — "
            "NEVER render placeholder text like 'Product Image' or empty gray boxes. "
            "NEVER use a <div> with text as a fake image. Every product MUST have an <img> tag "
            "with a working src URL like https://picsum.photos/seed/product-name/400/400.\n"
            "- Every page linked in the navigation MUST be fully implemented and functional — "
            "not just a stub with a title. If a page exists in the nav, it must work.\n"
            "- Cart: add-to-cart must work, cart page must show items, quantities, totals.\n"
            "- Search/filter: if the TSD includes search, it must actually filter results.\n\n"
            "AUTH & ERROR HANDLING RULES (these are mandatory):\n"
            "- The NextAuth [...nextauth]/route.ts MUST handle missing/dummy credentials gracefully — "
            "wrap the NextAuth() call so the app does not crash when NEXTAUTH_SECRET is a placeholder.\n"
            "- Auth pages (sign-in, sign-up) must render without errors even with dummy env vars.\n"
            "- API routes must return proper JSON error responses, never crash with unhandled exceptions.\n"
            "- If Stripe keys are dummy, the checkout page should show a message or disable payment — "
            "never throw an unhandled Stripe error.\n\n"
            "NEXT.JS APP ROUTER RULES (these are mandatory):\n"
            "- Every component that uses useState, useEffect, onClick, onChange, "
            "or any React hook MUST have 'use client' as the very first line.\n"
            "- Do NOT use `experimental: { appDir: true }` in next.config.js — "
            "App Router is stable in Next.js 14+.\n"
            "- Include src/app/api/auth/[...nextauth]/route.ts with proper exports. "
            "The NextAuth route MUST export GET and POST handlers. "
            "The sign-in page is automatically served at /api/auth/signin by NextAuth — "
            "do NOT create a custom /auth/signin page unless it redirects there.\n"
            "- Include BOTH .env AND .env.local with dummy/placeholder values for ALL env vars "
            "the code references. Both files MUST contain ALL of these:\n"
            "  DATABASE_URL=\"file:./dev.db\"\n"
            "  NEXTAUTH_SECRET=\"dev-secret-key-change-in-production\"\n"
            "  NEXTAUTH_URL=http://localhost:3000\n"
            "  STRIPE_SECRET_KEY=\"sk_test_placeholder\"\n"
            "  STRIPE_PUBLISHABLE_KEY=\"pk_test_placeholder\"\n"
            "- .env MUST contain at minimum: DATABASE_URL=\"file:./dev.db\" (Prisma reads .env, not .env.local).\n"
            "- For Prisma: use 'file:./dev.db' as DATABASE_URL (SQLite) so no external DB is needed.\n"
            "- Remove the postinstall script from package.json (no prisma generate at install time).\n"
            "- ALL dependencies used in import statements MUST appear in package.json. "
            "Common ones to include: @auth/prisma-adapter, bcryptjs, class-variance-authority, "
            "clsx, tailwind-merge. NEVER use @next-auth/prisma-adapter (invalid/nonexistent package).\n"
            "- The landing page (src/app/page.tsx) should be a simple server component "
            "that renders static UI — no auth gating, no DB calls, no hooks.\n"
            "- Use next.config.js with: module.exports = { /* empty or minimal */ }.\n"
            "- The 'dev' script in package.json MUST be just 'next dev' \u2014 do NOT chain "
            "prisma generate, prisma db push, or seed commands in the dev script. "
            "Database setup is handled separately.\n"
            "- Put ALL app files under src/app/ — do NOT create a top-level app/ directory.\n"
            "- tailwind.config.js: include ALL color shades used in globals.css and components "
            "(e.g. primary-50 through primary-900, gray-50 through gray-900). "
            "If globals.css uses @apply with a color like bg-primary-600, that shade MUST exist in the config.\n"
            "- globals.css: do NOT use @apply border-border, bg-background, or text-foreground "
            "unless you also define 'border', 'background', 'foreground' colors in tailwind.config.js "
            "mapped to CSS custom properties.\n"
            "- Use default exports for all components (export default function ComponentName). "
            "Import them WITHOUT curly braces: `import Providers from '@/components/Providers'`, "
            "NOT `import { Providers } from '@/components/Providers'`. Mismatched default/named "
            "exports cause 'is not exported' runtime errors that crash the entire app.\n"
            "- Include a 'prisma' section in package.json with: \"seed\": \"npx tsx prisma/seed.ts\".\n"
            "- Include 'tsx' in devDependencies for the seed script.\n"
            "- next.config.js: add images.remotePatterns to allow picsum.photos (or use <img> tags).\n"
            "- Prisma seed scripts: upsert() WHERE clause MUST use a field marked @unique or @id. "
            "If you upsert by name, add @unique to the name field in the Prisma schema. "
            "Example: name String @unique. Then: upsert({ where: { name: 'X' }, update: {}, create: {...} }).\n\n"
            "SCHEMA-CODE CONSISTENCY RULES (these are mandatory):\n"
            "- EVERY field referenced in code MUST exist in prisma/schema.prisma. "
            "Before writing any page/component/API that accesses a field like product.slug, "
            "product.image, product.stock, product.categoryId, or category.slug — "
            "make sure that EXACT field is defined in the Prisma schema. "
            "Do NOT reference fields like 'featured', 'isAdmin', or 'imageUrl' in code "
            "unless they are defined in the schema. This causes runtime crashes.\n"
            "- If a page needs related data (e.g. product.category.name), define a RELATION "
            "in the Prisma schema (Category model with id, name, slug, image; Product has "
            "categoryId Int + category Category @relation). Do NOT use a plain String field "
            "for category and then try to do 'include: { category: true }' — that crashes.\n"
            "- The Prisma schema field names MUST match what the code uses. "
            "If code says 'product.image', the schema field must be 'image' (not 'imageUrl'). "
            "If code says 'product.slug', the schema must have 'slug String @unique'.\n"
            "- Include src/lib/prisma.ts that exports a singleton PrismaClient instance "
            "(using global caching for dev mode). Every file that needs Prisma should import "
            "from '@/lib/prisma'. This file is REQUIRED — without it, all DB pages crash.\n"
            "- SQLite DOES NOT support 'mode: insensitive' in Prisma queries. "
            "NEVER use { contains: search, mode: 'insensitive' } — just use { contains: search }. "
            "SQLite string comparisons are case-insensitive by default for ASCII.\n"
            "- Do NOT put files in BOTH a top-level app/ AND src/app/. Only use src/app/. "
            "Also do NOT put components in a top-level components/ dir — only use src/components/. "
            "Having both causes Next.js to use the wrong one and crash with import errors.\n"
            "- The seed script must create ALL related records (e.g. Categories first, then "
            "Products with categoryId). Products need: name, slug, description, price, image, "
            "stock, categoryId. Categories need: name, slug, image.\n"
            "- Do NOT reference GraphQL fields or API query parameters that don't exist in the "
            "Prisma schema (e.g. 'featured: Boolean' in GraphQL when Product has no 'featured' field).\n\n"
            "AUTH & SUBSCRIPTION SCHEMA RULES (these are mandatory if present in TSD):\n"
            "- If the project uses password-based authentication (email/password sign-up/sign-in), "
            "the User model MUST include: hashedPassword String? field. "
            "API routes that accept passwords must hash them with bcrypt before storing.\n"
            "- If the TSD includes billing/subscriptions or Stripe integration, the schema MUST include "
            "a Subscription model with: id String @id, userId String @unique, plan String (default 'free'), "
            "status String (default 'active'), stripeCustomerId String?, stripeSubscriptionId String?, "
            "currentPeriodEnd DateTime?, and timestamps createdAt/updatedAt. "
            "Include a relation: user User @relation(fields: [userId], references: [id], onDelete: Cascade).\n"
            "- NextAuth configuration (lib/auth.ts or app/api/auth/[...nextauth]/route.ts) MUST use "
            "@auth/prisma-adapter (NOT @next-auth/prisma-adapter, which is invalid). "
            "Version: @auth/prisma-adapter@^1.0.0.\n"
            "- Do NOT include invalid NextAuth pages options like 'signUp'. "
            "NextAuth does not provide a custom signUp page option — handle sign-up in a separate /auth/signup route.\n\n"
            f"TSD:\n{json.dumps(tsd, indent=2)}"
        )
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": settings.anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": settings.llm_model,
                "max_tokens": 32000,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=600,
        )
        resp.raise_for_status()
        data = resp.json()

        content = data["content"][0]["text"]
        stop = data.get("stop_reason", "")

        # Save full raw response for debugging
        debug_path = PROJECT_ROOT / "output" / "_debug_last_response.txt"
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        debug_path.write_text(
            f"stop_reason: {stop}\nlength: {len(content)}\n\n{content}", encoding="utf-8"
        )

        # Extract JSON: find first { and last } in the response
        # (more robust than regex fence-stripping which fails when file
        # contents themselves contain triple backticks)
        content = content.strip()
        first_brace = content.find("{")
        last_brace = content.rfind("}")
        if first_brace >= 0 and last_brace > first_brace:
            content = content[first_brace:last_brace + 1]

        # If response was truncated (end_turn not reached), try to repair JSON
        content = content.strip()
        if stop == "max_tokens" or not content.endswith("}"):
            # Truncated — find last complete key-value pair and close the object
            last_good = content.rfind('",')
            if last_good > 0:
                content = content[:last_good + 1] + "}"

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # Last resort: find the first { and last } and try again
            start = content.find("{")
            end = content.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(content[start:end + 1])
                except json.JSONDecodeError:
                    pass
            # Save full response for debugging
            debug_path.write_text(
                f"PARSE FAILED\nstop_reason: {stop}\n\n{content}", encoding="utf-8"
            )
            raise

    def _write_files(self, files: dict[str, str], slug: str) -> Path:
        """Write generated files to output/<slug>/."""
        import shutil
        output_base = PROJECT_ROOT / settings.output_dir
        project_dir = output_base / slug

        # Clean old source files but preserve node_modules for faster installs
        if project_dir.exists():
            for item in project_dir.iterdir():
                if item.name in ("node_modules", ".next"):
                    continue
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink(missing_ok=True)
        project_dir.mkdir(parents=True, exist_ok=True)

        for rel_path, content in files.items():
            # Sanitize: prevent path traversal
            clean = Path(rel_path).as_posix().lstrip("/")
            if ".." in clean:
                continue
            full = project_dir / clean
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")

        return project_dir

    @staticmethod
    def _slugify(name: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
