"""Task definitions for each pipeline phase.

Each function returns a CrewAI Task wired to the correct Agent.
"""

from __future__ import annotations

from crewai import Agent, Task


# ── Phase 1: Scout ───────────────────────────────────────────────────
def create_scout_task(agent: Agent) -> Task:
    return Task(
        description=(
            "Scan all configured job-board sources for new web-development leads. "
            "Use the web_lead_scraper tool exactly once and treat its output as authoritative. "
            "Do not invent, summarize, or filter the tool output in this phase. "
            "Return ONLY the exact JSON array produced by the tool, with no markdown or extra text."
        ),
        expected_output="A JSON array of lead objects with keys: source, title, description, budget_usd, client_name, client_email, client_location, client_total_spent, posted_at, url, tags, experience_level, proposals, job_type.",
        agent=agent,
    )


# ── Phase 2: Analyst (The Architect) ───────────────────────────────
def create_analyst_task(agent: Agent) -> Task:
    return Task(
        description=(
            "You will receive a JSON array of leads from the Scout.\n"
            "For EACH lead:\n"
            "1. Run the Feasibility Gate (reject if it needs hardware, 3D engines, "
            "blockchain, or illegal content, or budget < $2,000).\n"
            "2. For APPROVED leads, produce a full SRS-grade Technical Specification "
            "Document (TSD) following the Ironclad Discovery framework:\n"
            "   - Define every user flow, button, and API integration explicitly.\n"
            "   - Include a Definition of Done checklist (performance, security, SEO, "
            "responsiveness, analytics targets).\n"
            "   - Choose the tech stack based on the client's long-term maintenance "
            "budget, not just current popularity.\n"
            "   - Apply the 80/20 rule: identify the core 20% of features (MVP) that "
            "deliver 80% of the value — mark these as 'phase_1'.\n"
            "   - Define 2-week sprint milestones with weekly demo checkpoints.\n"
            "Return a JSON array where each element is either:\n"
            '  {"status": "rejected", "title": "...", "reason": "..."}\n'
            "  or a full TSD object with status 'approved'."
        ),
        expected_output=(
            "A JSON array of analysis results. Approved items include a full SRS-grade "
            "TSD with stack, pages, integrations, sprint milestones, definition_of_done "
            "checklist, mvp_features (phase_1), and risk assessment."
        ),
        agent=agent,
    )


# ── Phase 3: Closer ─────────────────────────────────────────────────
def create_closer_task(agent: Agent) -> Task:
    return Task(
        description=(
            "You will receive analysis results from the Analyst.\n"
            "For each APPROVED project:\n"
            "1. If client_email is available, draft a winning proposal email using "
            "the Hook → Solution → AI Advantage → Social Proof → Milestones → "
            "Security Guarantee → CTA structure. Use the send_email tool. "
            'Include \'from_alias\': \'hello\' so it sends from hello@shiftbite.ai.\n'
            "2. The proposal MUST include:\n"
            "   - Weekly demo checkpoints (every Friday) during the sprint.\n"
            "   - The Definition of Done checklist from the TSD so the client knows "
            "exactly what 'finished' means.\n"
            "   - Post-launch offer: 30 days free bug fixes + optional monthly "
            "maintenance retainer.\n"
            "   - Shared dashboard access (Notion/Linear) so client sees live progress.\n"
            "3. If client_email is missing, SKIP emailing but still confirm the deal.\n"
            "4. Assume all approved projects are confirmed (PoC mode).\n"
            "CRITICAL: Return a valid JSON array. Each deal MUST include the full TSD. "
            "Do NOT ask for more information.\n"
            "Return JSON array with keys: deal_status, client_email, project_name, tsd."
        ),
        expected_output=(
            "A JSON array of confirmed deal objects, each containing the full TSD, "
            "client contact info, and proposal details including milestones and "
            "post-launch support offer. Example:\n"
            '[{"deal_status":"confirmed","client_email":"","project_name":"...", '
            '"tsd":{...full TSD object...}}]'
        ),
        agent=agent,
    )


# ── Phase 4: Builder (Lead Developer) ──────────────────────────────
def create_builder_task(agent: Agent) -> Task:
    return Task(
        description=(
            "You will receive confirmed deals from the Closer.\n"
            "For each deal, follow the Agile Execution framework:\n"
            "1. Extract the TSD. Prioritise MVP features (phase_1) first — build the "
            "core 20% that delivers 80% of the value before extras.\n"
            "2. Use the build_and_deploy tool with the TSD JSON to generate code.\n"
            "3. Apply the full Production Quality Checklist to your code:\n"
            "   PERFORMANCE: lazy-load images, code-split routes, target <2s load time.\n"
            "   SECURITY: no hardcoded secrets, all env vars in .env/.env.local, "
            "SSL-ready config, inputs sanitized (OWASP A03).\n"
            "   SEO: meta tags, og:image, sitemap.xml, schema markup on all pages.\n"
            "   RESPONSIVENESS: mobile-first Tailwind, tested breakpoints sm/md/lg/xl.\n"
            "   ANALYTICS: install PostHog or Google Analytics snippet.\n"
            "   DOCUMENTATION: include a README with setup instructions clear enough "
            "for a developer who didn't build it.\n"
            "4. Return the build result with the local project path.\n"
            "CRITICAL: Pick the BEST single deal. Build ONLY that one project. "
            "Do NOT ask for clarification."
        ),
        expected_output=(
            "A JSON array of build results with keys: project_name, project_dir, "
            "files_generated, build_status, mvp_features_built, quality_checklist_applied."
        ),
        agent=agent,
    )


# ── Phase 5: Auditor (QA Tester + Security Auditor) ─────────────────
def create_auditor_task(agent: Agent) -> Task:
    return Task(
        description=(
            "You will receive build results from the Builder.\n"
            "For each built project, run the full quality gate:\n"
            "1. Use the run_qa_tests tool with the project_dir from the build result.\n"
            "   The tool installs dependencies, starts the dev server, and runs "
            "   Playwright browser tests against localhost.\n"
            "2. After Playwright tests, audit the source code against the Definition "
            "   of Done checklist:\n"
            "   PERFORMANCE: check for image optimization, code splitting, "
            "   no blocking scripts (target <2s load).\n"
            "   SECURITY: no hardcoded API keys or secrets, env vars used correctly, "
            "   OWASP Top 10 scan (A01-A09). Any finding = security-critical bug.\n"
            "   SEO: confirm meta tags, sitemap.xml, og:image present on landing page.\n"
            "   RESPONSIVENESS: confirm mobile breakpoints in Tailwind config.\n"
            "   ANALYTICS: confirm PostHog or GA snippet is present.\n"
            "   DOCUMENTATION: confirm README exists with setup instructions.\n"
            "3. If ALL checks pass → status: pass, project is ready for delivery.\n"
            "4. If ANY check fails → status: fail, list each failure as a bug with "
            "   appropriate severity (security-critical / high / medium / low).\n"
            "CRITICAL: You MUST call the run_qa_tests tool. Do NOT skip testing."
        ),
        expected_output=(
            "A JSON array of QA reports with keys: project_name, project_dir, "
            "status (pass/fail), tests_run, tests_passed, bugs, "
            "definition_of_done_results (object with pass/fail per category: "
            "performance, security, seo, responsiveness, analytics, documentation)."
        ),
        agent=agent,
    )
