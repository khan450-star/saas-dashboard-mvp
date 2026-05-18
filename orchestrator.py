"""
AI Dev Agency — Main Orchestrator
===================================
Wires all 5 agents + tools into a CrewAI pipeline and exposes a FastAPI
server so n8n (or any webhook) can trigger runs.

Usage (standalone):
    python orchestrator.py              # runs the full pipeline once in mock mode

Usage (API server):
    uvicorn orchestrator:app --reload   # starts the webhook listener
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from crewai import Crew, Process
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from crewai import LLM

from config import settings
import db

# CrewAI can include strict tool-schema flags that some Anthropic models reject.
# Strip those flags at runtime so tool-calling works with supported Claude models.
try:
    from crewai.llms.providers.anthropic.completion import AnthropicCompletion

    _orig_convert_tools = AnthropicCompletion._convert_tools_for_interference
    _orig_prepare_params = AnthropicCompletion._prepare_completion_params

    def _strip_strict_flags(obj):
        if isinstance(obj, dict):
            obj.pop("strict", None)
            for v in obj.values():
                _strip_strict_flags(v)
        elif isinstance(obj, list):
            for item in obj:
                _strip_strict_flags(item)

    def _patched_convert_tools_for_interference(self, tools):
        converted = _orig_convert_tools(self, tools)
        _strip_strict_flags(converted)
        return converted

    def _patched_prepare_completion_params(self, messages, system_message=None, tools=None, available_functions=None):
        params = _orig_prepare_params(
            self,
            messages,
            system_message=system_message,
            tools=tools,
            available_functions=available_functions,
        )
        _strip_strict_flags(params)
        return params

    AnthropicCompletion._convert_tools_for_interference = _patched_convert_tools_for_interference
    AnthropicCompletion._prepare_completion_params = _patched_prepare_completion_params
except Exception:
    pass

# ── Agents ───────────────────────────────────────────────────────────
from agents.scout import create_scout_agent
from agents.analyst import create_analyst_agent
from agents.closer import create_closer_agent
from agents.builder import create_builder_agent
from agents.auditor import create_auditor_agent

# ── Tools ────────────────────────────────────────────────────────────
from tools.scraper_tool import ScraperTool
from tools.email_tool import EmailTool
from tools.builder_tool import BuilderTool
from tools.testing_tool import TestingTool
from tools.fixer_tool import FixerTool

# ── Tasks ────────────────────────────────────────────────────────────
from tasks.pipeline import (
    create_scout_task,
    create_analyst_task,
    create_closer_task,
    create_builder_task,
    create_auditor_task,
)

# ─────────────────────────────────────────────────────────────────────
# Tool instances
# ─────────────────────────────────────────────────────────────────────
scraper = ScraperTool()
emailer = EmailTool()
builder_tool = BuilderTool()
tester = TestingTool()
fixer = FixerTool()


MAX_FIX_RETRIES = 2  # how many fix→retest cycles
OWNER_EMAIL = "khan450star@gmail.com"
API_REFRESH_VERSION = "2026-05-refresh"
SERVER_BOOT_AT = datetime.now(timezone.utc)

# Runtime override so project size mode can be switched without editing files.
_project_selection_mode_override: str | None = None


def _send_notification_email(subject: str, body: str) -> None:
    """Fire an email notification from support@ to the owner. Non-blocking."""
    if settings.email_mock or not (settings.resend_api_key or "").strip():
        print(f"  [MOCK NOTIFY] {subject}")
        return
    try:
        import httpx
        httpx.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {settings.resend_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": settings.email_from_support,
                "to": [OWNER_EMAIL],
                "subject": subject,
                "text": body,
            },
            timeout=15,
        )
    except Exception as e:
        print(f"  ⚠ Notification email failed: {e}")


def _get_llm(model_override: str | None = None):
    """Return the appropriate LLM instance.

    In mock_mode every agent uses the local mock server.
    In live mode each agent gets its specialist model from settings,
    falling back to the global llm_model if a key is missing.
    """
    if settings.mock_mode:
        import os
        os.environ.setdefault("OPENAI_API_KEY", "mock-key")
        return LLM(
            model="openai/mock-model",
            base_url="http://127.0.0.1:11434/v1",
            api_key="mock-key",
        )

    model = model_override or f"anthropic/{settings.llm_model}"

    # Ensure all models have the provider prefix
    if model and not any(model.startswith(p + "/") for p in ["anthropic", "openai", "gemini"]):
        model = f"anthropic/{model}"

    if model.startswith("anthropic/"):
        return LLM(model=model, api_key=settings.anthropic_api_key)
    if model.startswith("openai/"):
        return LLM(model=model, api_key=settings.openai_api_key)
    if model.startswith("gemini/"):
        return LLM(model=model, api_key=settings.gemini_api_key)
    # fallback
    return LLM(model=model, api_key=settings.anthropic_api_key)


def _get_project_selection_mode() -> str:
    mode = (_project_selection_mode_override or settings.project_selection_mode or "small").strip().lower()
    return mode if mode in {"small", "big"} else "small"


def _format_uptime(seconds: int) -> str:
    """Return human readable uptime from whole seconds."""
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, mins = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {mins}m"
    days, hrs = divmod(hours, 24)
    return f"{days}d {hrs}h"


def _budget_value(lead: dict) -> float:
    """Parse lead budget into a numeric USD value (best effort)."""
    raw = lead.get("budget_usd", 0)
    try:
        if isinstance(raw, str):
            digits = "".join(ch for ch in raw if ch.isdigit() or ch == ".")
            return float(digits or 0)
        return float(raw or 0)
    except Exception:
        return 0.0


def _load_sample_lead_fallback() -> list[dict]:
    """Load a single sample lead fallback if available and valid."""
    sample_path = Path(__file__).parent / "mock_data" / "sample_lead.json"
    if not sample_path.exists():
        return []
    try:
        sample = json.loads(sample_path.read_text(encoding="utf-8"))
        if isinstance(sample, dict) and _budget_value(sample) > 0:
            return [sample]
    except Exception:
        return []
    return []


def build_crew() -> Crew:
    """Assemble the full 5-phase crew with specialist models per agent."""
    global _current_activity
    _current_activity = "building crew"
    print("[Crew] Building 5-agent pipeline...")
    # Scout & Closer share the general/fallback model (communication-heavy)
    llm_general  = _get_llm()
    llm_architect = _get_llm(settings.architect_model)   # Claude Opus — system design
    llm_lead_dev  = _get_llm(settings.lead_dev_model)    # GPT-5.2-Codex — code gen
    llm_security  = _get_llm(settings.security_model)    # GPT-5.2 — OWASP reasoning
    llm_qa        = _get_llm(settings.qa_model)          # Gemini 3 Pro — Playwright

    # ── Agentic Long-Term Memory ──────────────────────────────────────
    # Inject past mistake-and-fix entries so agents remember lessons
    # across runs (avoids repeating the same errors month after month).
    memory_text = db.get_active_knowledge_text()

    # Agents (each gets only the tools it needs)
    print("[Crew] Creating agents...")
    scout   = create_scout_agent(tools=[scraper], llm=llm_general)
    analyst = create_analyst_agent(tools=[], llm=llm_architect)
    closer  = create_closer_agent(tools=[emailer], llm=llm_general)
    builder = create_builder_agent(tools=[builder_tool], llm=llm_lead_dev)
    auditor = create_auditor_agent(tools=[tester], llm=llm_qa)

    # Tasks (sequential — output of each feeds into the next)
    print("[Crew] Creating tasks...")
    t1 = create_scout_task(scout)
    t2 = create_analyst_task(analyst)
    t3 = create_closer_task(closer)
    t4 = create_builder_task(builder)
    t5 = create_auditor_task(auditor)

    # ── Project size policy (small test vs big project) ─────────────
    mode = _get_project_selection_mode()
    if mode == "small":
        t2.description += (
            "\n\n--- PROJECT MODE: SMALL TEST ---\n"
            "OVERRIDE PRIOR RULES: In small test mode, do NOT reject solely for budget < $2,000. "
            "Approve the smallest feasible pilot project, including low/unknown budget leads when technically valid.\n"
            "Goal: fast end-to-end validation with minimal scope.\n"
            "--- END PROJECT MODE ---\n"
        )
        t4.description += (
            "\n\n--- PROJECT MODE: SMALL TEST ---\n"
            "Choose ONLY one approved deal and pick the SMALLEST scope:\n"
            "1) Lowest estimated_hours\n"
            "2) Fewest pages/integrations\n"
            "3) Easiest MVP for quick QA cycle\n"
            "--- END PROJECT MODE ---\n"
        )
    else:
        t2.description += (
            "\n\n--- PROJECT MODE: BIG PROJECT ---\n"
            "Enforce strict qualification and prioritize high-value/high-complexity opportunities.\n"
            "--- END PROJECT MODE ---\n"
        )
        t4.description += (
            "\n\n--- PROJECT MODE: BIG PROJECT ---\n"
            "Choose ONLY one approved deal and pick the BIGGEST strategic opportunity:\n"
            "1) Highest estimated_cost_usd\n"
            "2) Highest feature/integration complexity\n"
            "3) Strongest long-term business value\n"
            "--- END PROJECT MODE ---\n"
        )

    # ── Inject knowledge base into task descriptions ─────────────────
    kb_text = memory_text  # already fetched above
    if kb_text:
        kb_block = (
            "\n\n--- IMPORTANT: AGENTIC LONG-TERM MEMORY ---\n"
            "The following Q&A entries were resolved by the team in past runs. "
            "You MUST use these learned answers instead of guessing or repeating "
            "mistakes that have already been corrected.\n\n"
            f"{kb_text}\n"
            "--- END LONG-TERM MEMORY ---\n"
        )
        for task in [t2, t3, t4, t5]:  # analyst, closer, builder, auditor all benefit
            task.description += kb_block

    print("[Crew] Assembly complete - ready to kickoff")
    return Crew(
        agents=[scout, analyst, closer, builder, auditor],
        tasks=[t1, t2, t3, t4, t5],
        process=Process.sequential,
        verbose=False,  # Reduce log output to prevent Railway rate limiting
    )


def run_pipeline() -> dict:
    """Execute the full pipeline, then self-heal if QA finds bugs."""
    global _running_run_id, _current_activity
    _current_activity = "starting pipeline"
    # Reuse pre-created run_id from trigger endpoint, or create a new one (CLI)
    with _running_lock:
        if _running_run_id is not None:
            run_id = _running_run_id
        else:
            run_id = db.start_run(mock_mode=settings.mock_mode)
    fix_attempts_used = 0


    phase_names = ["scout", "analyst", "closer", "builder", "auditor"]
    crew = build_crew()
    result = None
    result_str = ""
    try:
        for idx, phase in enumerate(phase_names):
            db.update_current_phase(run_id, phase)
            if idx == 0:
                # kickoff runs all tasks in CrewAI sequential process
                _current_activity = f"executing scout agent (run #{run_id})"
                print(f"[Run #{run_id}] Starting CrewAI kickoff - scout agent will call Anthropic API")
                import time
                start_time = time.time()
                result = crew.kickoff()
                elapsed = time.time() - start_time
                _current_activity = "processing scout results"
                print(f"[Run #{run_id}] CrewAI kickoff completed in {elapsed:.1f}s")
                result_str = str(result)
                break

        # --- Validation after Scout phase ---
        # Extract scout output and filter leads with required fields
        try:
            _raw_scout = result.tasks_output[0] if hasattr(result, 'tasks_output') and result.tasks_output else ""
            # TaskOutput objects need to be converted to string first
            if hasattr(_raw_scout, 'raw'):
                _raw_scout = _raw_scout.raw
            elif not isinstance(_raw_scout, str):
                _raw_scout = str(_raw_scout)
            scout_data = _extract_json(_raw_scout)
            if isinstance(scout_data, list):
                valid_leads = [lead for lead in scout_data if _budget_value(lead) > 0]
                if not valid_leads:
                    fallback_leads = _load_sample_lead_fallback()
                    if fallback_leads:
                        db.add_notification(
                            type="info",
                            title="Scout Fallback Applied",
                            message=f"Run #{run_id}: no budget-qualified live leads; used sample lead fallback.",
                            link=f"run:{run_id}",
                            source="pipeline",
                        )
                    else:
                        db.finish_run(run_id, status="fail", error_message="No budget-qualified leads (budget_usd > 0) found by Scout, and no fallback lead available.")
                        db.add_notification(
                            type="warning",
                            title="No Valid Leads",
                            message=f"Run #{run_id} failed: no budget-qualified leads found.",
                            link=f"run:{run_id}",
                            source="pipeline",
                        )
                        _send_notification_email(
                            f"⚠️ Pipeline Failed — Run #{run_id}",
                            f"No budget-qualified leads found by Scout in run #{run_id}, and fallback lead was unavailable.",
                        )
                        return {"run_id": run_id, "status": "fail", "error": "No budget-qualified leads found."}
        except Exception as scout_validation_exc:
            db.finish_run(run_id, status="error", error_message=f"Scout validation error: {str(scout_validation_exc)[:500]}")
            db.add_notification(
                type="error",
                title="Scout Validation Error",
                message=f"Run #{run_id} error: {str(scout_validation_exc)[:200]}",
                link=f"run:{run_id}",
                source="pipeline",
            )
            _send_notification_email(
                f"🚨 Scout Validation Error — Run #{run_id}",
                f"Scout validation error in run #{run_id}:\n\n{str(scout_validation_exc)[:500]}",
            )
            return {"run_id": run_id, "status": "error", "error": "Scout validation error."}
    except Exception as exc:
        db.finish_run(run_id, status="error", error_message=str(exc)[:2000])
        db.add_notification(
            type="error",
            title="Pipeline Error",
            message=f"Run #{run_id} crashed: {str(exc)[:200]}",
            link=f"run:{run_id}",
            source="pipeline",
        )
        _send_notification_email(
            f"🚨 Pipeline Error — Run #{run_id}",
            f"Run #{run_id} crashed:\n\n{str(exc)[:500]}",
        )
        # Failsafe: always update status to error if an exception occurs
        raise

    # ── Capture all intermediate task outputs ────────────────────────
    pipeline_chain = {}
    try:
        task_outputs = result.tasks_output if hasattr(result, 'tasks_output') else []
        stage_names = ["scout", "analyst", "closer", "builder", "auditor"]
        for i, name in enumerate(stage_names):
            if i < len(task_outputs):
                t = task_outputs[i]
                # TaskOutput objects must be converted to string
                raw = t.raw if hasattr(t, 'raw') else str(t)
                pipeline_chain[name] = raw[:15000]
    except Exception:
        pass

    # ── Self-healing loop ────────────────────────────────────────────
    # Parse the QA result and retry if bugs were found
    best_result_str = result_str  # Track the best result across attempts
    best_bug_count = 999
    for attempt in range(1, MAX_FIX_RETRIES + 1):
        qa_reports = _extract_qa_reports(result_str)
        if not qa_reports:
            break

        has_bugs = False
        for report in qa_reports:
            bugs = report.get("bugs", [])
            status = report.get("status", "pass")
            project_dir = report.get("project_dir", "")
            if status == "fail" and bugs and project_dir:
                has_bugs = True

                # Track best result — if current result is better than previous best, save it
                current_bug_count = len(bugs)
                if current_bug_count < best_bug_count:
                    best_bug_count = current_bug_count
                    best_result_str = result_str

                print(f"\n{'='*60}")
                print(f"  SELF-HEAL: Attempt {attempt}/{MAX_FIX_RETRIES}")
                print(f"  Fixing {current_bug_count} bug(s) in {project_dir}")
                print(f"{'='*60}")

                # Rate-limit pause between Claude API calls
                time.sleep(10)

                # Step 1: Fix the bugs
                fix_input = json.dumps({"project_dir": project_dir, "bugs": bugs})
                fix_result = fixer._run(fix_input)
                print(f"  Fix result: {fix_result[:200]}")

                # Step 2: Ensure .env exists, then re-run npm install
                import subprocess
                env_file = Path(project_dir) / ".env"
                if not env_file.exists():
                    env_local = Path(project_dir) / ".env.local"
                    if env_local.exists():
                        env_file.write_text(env_local.read_text(encoding="utf-8"), encoding="utf-8")
                    else:
                        env_file.write_text('DATABASE_URL="file:./dev.db"\n', encoding="utf-8")
                npm_path = shutil.which("npm")
                if not npm_path:
                    print("  [SELF-HEAL] npm not found in runtime PATH; skipping npm install step.")
                else:
                    # Use DEVNULL to avoid pipe deadlock on Windows when timeout kills shell
                    npm_proc = subprocess.Popen(
                        ["npm", "install", "--legacy-peer-deps"],
                        cwd=project_dir,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        shell=True,
                    )
                    try:
                        npm_proc.wait(timeout=120)
                    except subprocess.TimeoutExpired:
                        subprocess.run(["taskkill", "/F", "/T", "/PID", str(npm_proc.pid)],
                                       capture_output=True, timeout=10)
                        try:
                            npm_proc.wait(timeout=5)
                        except Exception:
                            pass

                # Step 3: Re-run QA tests
                test_input = json.dumps({"project_dir": project_dir})
                retest_result = tester._run(test_input)
                print(f"  Retest result: {retest_result[:300]}")

                # Compute bug count before any comparison/use.
                retest_reports = _extract_qa_reports(retest_result)
                retest_bugs = sum(len(r.get("bugs", [])) for r in retest_reports) if retest_reports else 999

                fix_attempts_used = attempt

                # ── Agentic Long-Term Memory: save resolved bugs ──────
                # When bugs are fixed, record the bug description + fix
                # in the knowledge base so future runs avoid the same mistake.
                if retest_bugs < current_bug_count:
                    for bug in bugs:
                        bug_desc = bug.get("description", "")
                        if bug_desc:
                            db.add_knowledge(
                                category="bug_fix",
                                question=f"Bug encountered: {bug_desc}",
                                answer=(
                                    f"Severity: {bug.get('severity', 'unknown')}. "
                                    f"This bug was automatically fixed by the fixer "
                                    f"agent in run #{run_id} (attempt {attempt}). "
                                    f"Avoid this pattern in future builds."
                                ),
                                source=f"self_heal_run_{run_id}",
                                run_id=run_id,
                            )
                    print(f"  [MEMORY] Saved {len(bugs)} bug fix(es) to long-term memory.")

                # Check for regression — if retest is worse, keep best result
                if retest_bugs <= best_bug_count:
                    best_bug_count = retest_bugs
                    best_result_str = retest_result
                else:
                    print(f"  ⚠ Regression detected: {retest_bugs} bugs vs previous best {best_bug_count}")

                # Always update result_str for the loop to try fixing new bugs
                result_str = retest_result

        if not has_bugs:
            break

    # Use the best result across all attempts (guards against regression)
    result_str = best_result_str

    # ── Save to history ──────────────────────────────────────────────
    qa_reports = _extract_qa_reports(result_str)
    totals = {"tests_run": 0, "tests_passed": 0, "tests_failed": 0,
              "files_generated": 0, "projects_built": 0, "leads_found": 0}
    final_status = "pass"
    run_error_message = ""
    project_name = ""
    project_dir = ""

    # Count leads from Scout output so "no lead" runs are tracked correctly.
    try:
        scout_data = _extract_json(pipeline_chain.get("scout", ""))
        if isinstance(scout_data, list):
            totals["leads_found"] = len(scout_data)
    except Exception:
        pass

    for rpt in qa_reports:
        totals["tests_run"] += rpt.get("tests_run", 0)
        totals["tests_passed"] += rpt.get("tests_passed", 0)
        totals["tests_failed"] += rpt.get("tests_failed", 0)
        totals["projects_built"] += 1
        if rpt.get("status") == "fail":
            final_status = "fail"
        # Capture project name and dir from report
        if rpt.get("project_name"):
            project_name = rpt["project_name"]
        if rpt.get("project_dir"):
            project_dir = rpt["project_dir"]

    # Guard against false "pass" when pipeline produced no usable output.
    if totals["projects_built"] == 0:
        if totals["leads_found"] == 0:
            final_status = "fail"
            run_error_message = "No qualifying leads found by Scout."
        else:
            final_status = "error"
            run_error_message = "No QA/build reports produced by pipeline."

    # Count actual files generated in the project directory
    if project_dir and Path(project_dir).is_dir():
        totals["files_generated"] = sum(
            1 for f in Path(project_dir).rglob("*")
            if f.is_file() and "node_modules" not in f.parts and ".next" not in f.parts
        )

    # ── Extract business fields from pipeline chain ──────────────────
    budget_usd = 0.0
    client_name = ""
    tech_stack = ""
    description = ""
    try:
        # Try to extract from closer output (has deal + TSD + client info)
        closer_raw = pipeline_chain.get("closer", "")
        closer_data = _extract_json(closer_raw)
        if isinstance(closer_data, list) and closer_data:
            deal = closer_data[0]
            client_name = deal.get("client_name", "") or deal.get("client_email", "")
            tsd = deal.get("tsd", {})
            if isinstance(tsd, dict):
                budget_usd = tsd.get("budget_usd", 0) or tsd.get("estimated_cost", 0) or 0
                tech_stack = ", ".join(tsd.get("stack", [])) if isinstance(tsd.get("stack"), list) else str(tsd.get("stack", ""))
                description = tsd.get("description", "") or tsd.get("summary", "")
                if not project_name:
                    project_name = deal.get("project_name", "") or tsd.get("project_name", "")
    except Exception:
        pass

    # Fallback: try extracting from scout output (lead data)
    if not budget_usd or not client_name:
        try:
            scout_raw = pipeline_chain.get("scout", "")
            scout_data = _extract_json(scout_raw)
            if isinstance(scout_data, list) and scout_data:
                lead = scout_data[0]
                if not budget_usd:
                    budget_usd = lead.get("budget_usd", 0) or 0
                if not client_name:
                    client_name = lead.get("client_name", "") or ""
                if not description:
                    description = (lead.get("description", "") or "")[:500]
                if not tech_stack:
                    tags = lead.get("tags", [])
                    tech_stack = ", ".join(tags) if isinstance(tags, list) else str(tags)
        except Exception:
            pass

    db.finish_run(
        run_id,
        status=final_status,
        leads_found=totals["leads_found"] or totals["projects_built"],
        projects_built=totals["projects_built"],
        files_generated=totals["files_generated"],
        tests_run=totals["tests_run"],
        tests_passed=totals["tests_passed"],
        tests_failed=totals["tests_failed"],
        fix_attempts=fix_attempts_used,
        result_json=result_str[:10000],
        error_message=run_error_message,
        project_name=project_name,
        project_dir=project_dir,
        budget_usd=budget_usd,
        client_name=client_name,
        tech_stack=tech_stack,
        description=description[:1000],
        pipeline_data=json.dumps(pipeline_chain)[:80000],
    )

    # ── Cloud deploy (if tokens configured) ────────────────────────
    deployed_url = ""
    github_url = ""
    if final_status == "pass" and project_dir:
        try:
            from deploy import deploy_project
            deploy_result = deploy_project(project_dir, project_name or "project")
            if deploy_result.get("deployed"):
                deployed_url = deploy_result.get("vercel_url", "")
                github_url = deploy_result.get("github_url", "")
                db.update_deploy_urls(run_id, deployed_url=deployed_url, github_url=github_url)
            elif deploy_result.get("github_url"):
                github_url = deploy_result["github_url"]
                db.update_deploy_urls(run_id, github_url=github_url)
            if deploy_result.get("error"):
                print(f"  ⚠ Deploy note: {deploy_result['error']}")
        except Exception as e:
            print(f"  ⚠ Deploy step skipped: {e}")

    # ── Auto-notifications ───────────────────────────────────────────
    # 1. Project delivered / failed
    if final_status == "pass":
        deploy_msg = ""
        if deployed_url:
            deploy_msg = f" Live at: {deployed_url}"
        elif github_url:
            deploy_msg = f" Code at: {github_url}"
        notif_title = "Project Delivered" + (" & Deployed" if deployed_url else "")
        notif_msg = f"\"{project_name or 'Project'}\" passed all {totals['tests_passed']}/{totals['tests_run']} tests and is ready for delivery.{deploy_msg}"
        db.add_notification(
            type="success",
            title=notif_title,
            message=notif_msg,
            link=f"run:{run_id}",
            source="pipeline",
        )
        _send_notification_email(
            f"✅ {notif_title} — {project_name or 'Project'}",
            f"{notif_msg}\n\nRun #{run_id}",
        )
    elif final_status == "fail":
        notif_title = "Project Has Failing Tests"
        notif_msg = f"\"{project_name or 'Project'}\" has {totals['tests_failed']} failing test(s) after {fix_attempts_used} fix attempt(s)."
        db.add_notification(
            type="warning",
            title=notif_title,
            message=notif_msg,
            link=f"run:{run_id}",
            source="pipeline",
        )
        _send_notification_email(
            f"⚠️ {notif_title} — {project_name or 'Project'}",
            f"{notif_msg}\n\nRun #{run_id}",
        )

    # 2. Scan pipeline chain for AI confusion / questions / unknowns
    _detect_ai_questions(pipeline_chain, run_id, project_name)

    return {
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mock_mode": settings.mock_mode,
        "result": result_str,
    }


def _detect_ai_questions(pipeline_chain: dict, run_id: int, project_name: str):
    """Scan pipeline outputs for signs the AI is confused or needs human input.

    Detects: payment/billing questions, unclear requirements, tech the AI
    doesn't know, requests for clarification, shopping cart / checkout
    complexity flags, etc.
    """
    import re

    # Patterns that indicate the AI is asking for help or flagging uncertainty
    question_patterns = [
        (r"(?:how|where)\s+(?:to|do|does|should|can|will)\s+(?:transfer|send|receive|accept)\s+(?:money|payment|fund)", "Payment/money transfer"),
        (r"(?:payment\s+gateway|stripe|paypal|razorpay)\s+(?:integration|setup|config)", "Payment gateway setup"),
        (r"(?:need|require|unclear|missing|please\s+(?:provide|clarify|specify))", "Unclear requirements"),
        (r"(?:I\s+(?:don'?t|do\s+not|cannot|can'?t)\s+(?:know|determine|figure|understand))", "AI doesn't know"),
        (r"(?:shopping\s+cart|checkout\s+(?:flow|process)|order\s+(?:processing|management))", "E-commerce cart/checkout"),
        (r"(?:authentication|auth|login|signup|oauth|jwt)\s+(?:flow|system|implementation)", "Auth system needed"),
        (r"(?:database\s+(?:schema|migration|design)|data\s+model)", "Database design question"),
        (r"(?:deploy|hosting|server|domain|ssl|https)\s+(?:setup|config|where)", "Deployment question"),
        (r"(?:api\s+(?:key|secret|credentials)|third[- ]party\s+(?:service|api))", "Third-party API needed"),
        (r"(?:not\s+(?:sure|certain)|uncertain|assumption|guess)", "AI uncertainty"),
        (r"(?:real[- ]time|websocket|socket\.io|push\s+notification)", "Real-time features"),
        (r"(?:file\s+upload|image\s+(?:processing|upload)|cloud\s+storage|s3|blob)", "File/media handling"),
    ]

    found_issues = []
    for stage_name, output in pipeline_chain.items():
        if not output:
            continue
        lower = output.lower()
        for pattern, label in question_patterns:
            if re.search(pattern, lower):
                # Extract a snippet around the match
                match = re.search(pattern, lower)
                start = max(0, match.start() - 80)
                end = min(len(output), match.end() + 80)
                snippet = output[start:end].strip().replace("\n", " ")
                found_issues.append((stage_name, label, snippet))
                break  # one notification per stage is enough

    for stage_name, label, snippet in found_issues:
        db.add_notification(
            type="question",
            title=f"AI Needs Input: {label}",
            message=f"[{stage_name.title()}] \"{project_name or 'Project'}\" — {snippet[:200]}",
            link=f"run:{run_id}",
            source="ai_question",
        )


def _extract_qa_reports(result_str: str) -> list[dict]:
    """Try to extract QA report JSON from the pipeline result string."""
    # Try parsing directly
    try:
        data = json.loads(result_str)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "bugs" in data:
            return [data]
    except (json.JSONDecodeError, TypeError):
        pass

    # Try extracting JSON from markdown fences
    import re
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", result_str, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "bugs" in data:
                return [data]
        except (json.JSONDecodeError, TypeError):
            pass

    # Try finding JSON array in the string
    start = result_str.find("[")
    end = result_str.rfind("]")
    if start >= 0 and end > start:
        try:
            data = json.loads(result_str[start:end + 1])
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, TypeError):
            pass

    return []


def _extract_json(raw: str):
    """Extract JSON (list or dict) from a raw LLM output string."""
    # Direct parse
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        pass
    # Brace/bracket finding
    for open_ch, close_ch in [("[", "]"), ("{", "}")]:
        start = raw.find(open_ch)
        end = raw.rfind(close_ch)
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start:end + 1])
            except (json.JSONDecodeError, TypeError):
                pass
    return None


# ─────────────────────────────────────────────────────────────────────
# FastAPI webhook server (for n8n integration)
# ─────────────────────────────────────────────────────────────────────
app = FastAPI(title="AI Dev Agency Orchestrator")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_methods=["*"],
    allow_headers=["*"],
)
DASHBOARD_PATH = Path(__file__).parent / "dashboard.html"

# Track in-flight pipeline run
_running_lock = threading.Lock()
_running_run_id: int | None = None
_current_activity: str = "idle"  # Tracks what the pipeline is doing in real-time
_scheduler_started = False


def _safe_parse_iso(dt: str | None) -> datetime | None:
    if not dt:
        return None
    try:
        return datetime.fromisoformat(dt)
    except Exception:
        return None


def _reconcile_stale_running_runs(max_age_minutes: int = 10) -> int:
    """Mark stale DB runs as error when no in-memory pipeline is active.

    This protects against deployments/restarts terminating background threads
    before they can finalize run status.
    """
    with _running_lock:
        current_run = _running_run_id

    # If an in-memory run is active, do not reconcile anything.
    if current_run is not None:
        return 0

    now = datetime.now(timezone.utc)
    stale_after = timedelta(minutes=max(1, int(max_age_minutes)))
    fixed = 0

    try:
        for run in db.list_runs(limit=50):
            if run.get("status") != "running":
                continue

            started = _safe_parse_iso(run.get("started_at"))
            if not started:
                continue

            # Ensure timezone-aware comparison
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)

            if (now - started) < stale_after:
                continue

            rid = int(run.get("id", 0) or 0)
            if rid <= 0:
                continue

            existing_error = (run.get("error_message") or "").strip()
            reconciliation_reason = (
                "Run auto-closed after process restart; background pipeline thread stopped before status finalization."
            )
            merged_error = (
                f"{existing_error} | {reconciliation_reason}" if existing_error else reconciliation_reason
            )

            db.finish_run(
                rid,
                status="error",
                leads_found=int(run.get("leads_found") or 0),
                projects_built=int(run.get("projects_built") or 0),
                files_generated=int(run.get("files_generated") or 0),
                tests_run=int(run.get("tests_run") or 0),
                tests_passed=int(run.get("tests_passed") or 0),
                tests_failed=int(run.get("tests_failed") or 0),
                fix_attempts=int(run.get("fix_attempts") or 0),
                result_json=(run.get("result_json") or "")[:10000],
                error_message=merged_error[:2000],
                project_name=(run.get("project_name") or "")[:255],
                project_dir=(run.get("project_dir") or "")[:1000],
                budget_usd=float(run.get("budget_usd") or 0),
                client_name=(run.get("client_name") or "")[:255],
                tech_stack=(run.get("tech_stack") or "")[:1000],
                description=(run.get("description") or "")[:1000],
                pipeline_data=(run.get("pipeline_data") or "")[:80000],
            )
            db.add_notification(
                type="warning",
                title="Recovered Stale Run",
                message=f"Run #{rid} was marked error after restart interrupted execution.",
                link=f"run:{rid}",
                source="reconciler",
            )
            fixed += 1
    except Exception as e:
        print(f"  ⚠ Stale-run reconciliation skipped: {e}")

    return fixed


def _auto_pipeline_scheduler_loop(interval_minutes: int) -> None:
    """Trigger pipeline runs on a fixed interval for online deployments."""
    global _running_run_id
    interval_seconds = max(1, int(interval_minutes)) * 60
    print(f"[scheduler] Auto pipeline enabled: every {interval_minutes} minute(s)")
    while True:
        time.sleep(interval_seconds)
        with _running_lock:
            if _running_run_id is not None:
                continue
            _running_run_id = db.start_run(mock_mode=settings.mock_mode)
            run_id = _running_run_id
        db.add_notification(
            type="info",
            title="Scheduled Pipeline Run",
            message=f"Auto-triggered run #{run_id} from online scheduler.",
            link=f"run:{run_id}",
            source="scheduler",
        )
        threading.Thread(target=_bg_pipeline, daemon=True).start()


@app.on_event("startup")
async def _startup_scheduler() -> None:
    global _scheduler_started
    # Heal stale DB rows from prior process restarts before accepting new runs.
    _reconcile_stale_running_runs()
    if _scheduler_started:
        return
    interval = max(0, int(settings.auto_pipeline_interval_minutes or 0))
    if interval <= 0:
        return
    _scheduler_started = True
    threading.Thread(target=_auto_pipeline_scheduler_loop, args=(interval,), daemon=True).start()


class LeadWebhook(BaseModel):
    """Payload that n8n sends when a new lead is detected."""
    source: str = "upwork"
    title: str = ""
    description: str = ""
    budget_usd: float = 0
    client_name: str = ""
    client_email: str = ""
    url: str = ""
    tags: list[str] = []


def _bg_pipeline():
    """Run pipeline in background thread."""
    global _running_run_id
    try:
        run_pipeline()
    finally:
        global _current_activity
        _current_activity = "idle"
        with _running_lock:
            _running_run_id = None


@app.post("/api/pipeline/trigger")
async def trigger_pipeline_endpoint(lead: LeadWebhook | None = None):
    """Trigger a full pipeline run in the background."""
    global _running_run_id
    # Ensure stale "running" rows do not block the operator's view.
    _reconcile_stale_running_runs()
    with _running_lock:
        if _running_run_id is not None:
            return {"error": "Pipeline already running", "run_id": _running_run_id}
        # Pre-create the run so we can return the ID immediately
        _running_run_id = db.start_run(mock_mode=settings.mock_mode)

    # Start it in a background thread so the HTTP response returns instantly
    t = threading.Thread(target=_bg_pipeline, daemon=True)
    t.start()
    return {"status": "started", "run_id": _running_run_id}


@app.get("/api/health")
async def health():
    global _current_activity
    reconciled = _reconcile_stale_running_runs()
    with _running_lock:
        running = _running_run_id
    now = datetime.now(timezone.utc)
    uptime_secs = max(0, int((now - SERVER_BOOT_AT).total_seconds()))
    output_dir = Path(settings.output_dir)
    if not output_dir.is_absolute():
        output_dir = Path(__file__).parent / output_dir
    projects_on_disk = 0
    if output_dir.is_dir():
        try:
            projects_on_disk = sum(1 for p in output_dir.iterdir() if p.is_dir())
        except Exception:
            projects_on_disk = 0
    return {
        "status": "ok",
        "agent_fix_version": "2026-04-22a",
        "api_refresh_version": API_REFRESH_VERSION,
        "server_time_utc": now.isoformat(),
        "uptime_secs": uptime_secs,
        "uptime_human": _format_uptime(uptime_secs),
        "project_selection_mode": _get_project_selection_mode(),
        "mock_mode": settings.mock_mode,
        "storage_backend": db.storage_backend(),
        "pipeline_running": running is not None,
        "current_run_id": running,
        "current_activity": _current_activity,  # Shows what process is running
        "stale_runs_reconciled": reconciled,
        "output_dir": str(output_dir),
        "projects_on_disk": projects_on_disk,
    }


class ProjectModePayload(BaseModel):
    mode: str


@app.get("/api/pipeline/project-size")
async def get_project_size_mode():
    """Get current runtime project selection mode (small|big)."""
    return {
        "mode": _get_project_selection_mode(),
        "override_active": _project_selection_mode_override is not None,
    }


@app.post("/api/pipeline/project-size")
async def set_project_size_mode(payload: ProjectModePayload):
    """Set runtime project selection mode (small|big)."""
    global _project_selection_mode_override
    mode = (payload.mode or "").strip().lower()
    if mode not in {"small", "big"}:
        return {"error": "Invalid mode. Use 'small' or 'big'."}
    _project_selection_mode_override = mode
    return {
        "status": "ok",
        "mode": _get_project_selection_mode(),
        "message": f"Project selection mode set to '{mode}'.",
    }


def _normalize_run_for_ui(run: dict | None) -> dict | None:
    """Normalize legacy/impossible run states for dashboard display."""
    if not run:
        return run
    if run.get("status") == "pass" and run.get("projects_built", 0) == 0 and run.get("tests_run", 0) == 0:
        run["status"] = "fail"
        if not run.get("error_message"):
            if run.get("leads_found", 0) == 0:
                run["error_message"] = "No qualifying leads found by Scout."
            else:
                run["error_message"] = "Run completed without build/QA output."
    return run


# ── Pipeline history endpoints ───────────────────────────────────────

@app.get("/api/runs")
async def list_runs(limit: int = 50, offset: int = 0):
    """List pipeline runs (newest first)."""
    safe_limit = max(1, min(int(limit), 200))
    safe_offset = max(0, int(offset))
    runs = db.list_runs(limit=safe_limit, offset=safe_offset)
    return [_normalize_run_for_ui(r) for r in runs]


@app.get("/api/runs/stats")
async def run_stats():
    """Aggregate stats across all pipeline runs."""
    return db.get_stats()


@app.get("/api/runs/latest")
async def latest_run():
    """Get the most recent pipeline run."""
    run = db.get_latest_run()
    if not run:
        return {"error": "No runs found"}
    return _normalize_run_for_ui(run)


@app.get("/api/runs/{run_id}")
async def get_run(run_id: int):
    """Get a specific pipeline run by ID."""
    run = db.get_run(run_id)
    if not run:
        return {"error": "Run not found"}
    return _normalize_run_for_ui(run)


@app.get("/")
async def dashboard():
    """Serve the pipeline dashboard."""
    return FileResponse(str(DASHBOARD_PATH), media_type="text/html")


# ── Project Requests (Client Intake) endpoints ──────────────────────

class ProjectRequestPayload(BaseModel):
    client_name: str
    client_email: str = ""
    company: str = ""
    project_title: str
    description: str = ""
    budget_range: str = ""
    timeline: str = ""
    tech_preferences: str = ""
    communication: str = "email"
    priority: str = "normal"


class MessagePayload(BaseModel):
    sender: str = "client"
    message: str


class StatusPayload(BaseModel):
    status: str
    notes: str = ""


@app.post("/api/requests")
async def create_request(payload: ProjectRequestPayload):
    req_id = db.create_request(
        client_name=payload.client_name,
        client_email=payload.client_email,
        company=payload.company,
        project_title=payload.project_title,
        description=payload.description,
        budget_range=payload.budget_range,
        timeline=payload.timeline,
        tech_preferences=payload.tech_preferences,
        communication=payload.communication,
        priority=payload.priority,
    )
    # Auto-notification: new project request
    db.add_notification(
        type="info",
        title="New Project Request",
        message=f"{payload.client_name} submitted \"{payload.project_title}\" — {payload.budget_range or 'no budget set'}",
        link=f"portal:{req_id}",
        source="intake",
    )
    return {"status": "created", "request_id": req_id}


@app.get("/api/requests")
async def list_requests_endpoint(status: str | None = None, limit: int = 50):
    return db.list_requests(status=status, limit=limit)


@app.get("/api/requests/stats")
async def request_stats():
    return db.get_request_stats()


@app.get("/api/requests/{request_id}")
async def get_request_endpoint(request_id: int):
    req = db.get_request(request_id)
    if not req:
        return {"error": "Request not found"}
    return req


@app.put("/api/requests/{request_id}/status")
async def update_request_status_endpoint(request_id: int, payload: StatusPayload):
    db.update_request_status(request_id, payload.status, payload.notes)
    return {"status": "updated"}


@app.get("/api/requests/{request_id}/messages")
async def get_messages_endpoint(request_id: int):
    return db.get_messages(request_id)


@app.post("/api/requests/{request_id}/messages")
async def add_message_endpoint(request_id: int, payload: MessagePayload):
    msg_id = db.add_message(request_id, payload.sender, payload.message)
    return {"status": "sent", "message_id": msg_id}


# ── Project Profile (aggregated view) ───────────────────────────────

@app.get("/api/projects/{run_id}/profile")
async def project_profile(run_id: int):
    """Get a full project profile: run data + linked request + messages + notifications + knowledge."""
    run = db.get_run(run_id)
    if not run:
        return {"error": "Run not found"}

    profile = dict(run)

    # Parse pipeline_data stages
    stages = {}
    if run.get("pipeline_data"):
        try:
            stages = json.loads(run["pipeline_data"])
        except (json.JSONDecodeError, TypeError):
            pass
    profile["stages"] = stages

    def _stage_data(key):
        """Safely parse a stage value — may already be parsed, a string, or truncated."""
        val = stages.get(key)
        if val is None:
            return None
        if isinstance(val, (list, dict)):
            return val
        if isinstance(val, str):
            parsed = _extract_json(val)
            if parsed is not None:
                return parsed
            # Strip markdown fences and try again
            stripped = val.strip()
            if stripped.startswith("```"):
                stripped = stripped.split("\n", 1)[-1]  # remove ```json line
                if stripped.endswith("```"):
                    stripped = stripped[:-3]
                stripped = stripped.strip()
            # Try to recover truncated JSON arrays: find the last complete object
            bracket_start = stripped.find("[")
            brace_start = stripped.find("{")
            start_ch = None
            if bracket_start >= 0 and (brace_start < 0 or bracket_start <= brace_start):
                start_ch = "["
                content = stripped[bracket_start:]
            elif brace_start >= 0:
                start_ch = "{"
                content = stripped[brace_start:]
            else:
                return None
            if start_ch == "[":
                last_brace = content.rfind("}")
                if last_brace > 0:
                    candidate = content[:last_brace + 1].rstrip().rstrip(",") + "]"
                    try:
                        return json.loads(candidate)
                    except (json.JSONDecodeError, TypeError):
                        pass
            elif start_ch == "{":
                # Try finding the last complete key-value
                last_brace = content.rfind("}")
                if last_brace > 0:
                    try:
                        return json.loads(content[:last_brace + 1])
                    except (json.JSONDecodeError, TypeError):
                        pass
        return None

    # Determine source: from pipeline_data scout output
    scout_data = _stage_data("scout")
    if isinstance(scout_data, list) and scout_data:
        lead = scout_data[0] if isinstance(scout_data[0], dict) else {}
        profile["lead_source"] = lead.get("source", "upwork")
        profile["lead_url"] = lead.get("url", "")
        profile["lead_title"] = lead.get("title", "")
        profile["lead_description"] = (lead.get("description") or "")[:500]
        profile["lead_budget"] = lead.get("budget_usd", 0)
        profile["lead_tags"] = lead.get("tags", [])

    # Extract client requirements from analyst output
    client_requirements = []
    analyst_data = _stage_data("analyst")
    if isinstance(analyst_data, list):
        for item in analyst_data:
            if isinstance(item, dict):
                req_entry = {}
                for k in ("project_name", "project_type", "requirements", "tech_stack",
                           "complexity", "timeline", "budget_estimate", "scope",
                           "features", "pages", "deliverables", "notes"):
                    if item.get(k):
                        req_entry[k] = item[k]
                if req_entry:
                    client_requirements.append(req_entry)
    elif isinstance(analyst_data, dict):
        client_requirements.append(analyst_data)
    # Also try extracting raw analyst text for display
    analyst_raw = stages.get("analyst", "")
    if isinstance(analyst_raw, str) and len(analyst_raw) > 20:
        profile["analyst_summary"] = analyst_raw[:2000]
    elif isinstance(analyst_raw, (list, dict)):
        profile["analyst_summary"] = json.dumps(analyst_raw, indent=2)[:2000]
    profile["client_requirements"] = client_requirements

    # Find linked portal request (by matching project title or client name)
    linked_request = None
    linked_messages = []
    all_requests = db.list_requests(limit=100)
    pname = (run.get("project_name") or "").lower()
    cname = (run.get("client_name") or "").lower()
    for req in all_requests:
        rtitle = (req.get("project_title") or "").lower()
        rclient = (req.get("client_name") or "").lower()
        if (pname and rtitle and (pname in rtitle or rtitle in pname)) or \
           (cname and rclient and cname == rclient):
            linked_request = req
            try:
                linked_messages = db.get_messages(req["id"])
            except Exception:
                pass
            break

    profile["linked_request"] = linked_request
    profile["messages"] = linked_messages

    # Linked notifications
    notifs = db.list_notifications(limit=200)
    profile["notifications"] = [n for n in notifs if n.get("link") == f"run:{run_id}"]

    # Linked knowledge entries
    kb = db.list_knowledge()
    profile["knowledge"] = [k for k in kb if k.get("run_id") == run_id]

    # Parse closer data for email info
    email_exchanges = []
    closer_data = _stage_data("closer")
    if isinstance(closer_data, list):
        for deal in closer_data:
            if isinstance(deal, dict) and deal.get("client_email"):
                email_exchanges.append({
                    "to": deal["client_email"],
                    "project": deal.get("project_name", ""),
                    "status": deal.get("deal_status", "confirmed"),
                })
    profile["email_exchanges"] = email_exchanges

    # List generated files in project directory
    project_files = []
    pdir = run.get("project_dir", "")
    if pdir and os.path.isdir(pdir):
        src_dir = os.path.join(pdir, "src")
        scan_dir = src_dir if os.path.isdir(src_dir) else pdir
        for root, dirs, files in os.walk(scan_dir):
            dirs[:] = [d for d in dirs if d not in ("node_modules", ".next", ".git")]
            for f in files:
                rel = os.path.relpath(os.path.join(root, f), pdir)
                project_files.append(rel.replace("\\", "/"))
        project_files.sort()

    profile["project_files"] = project_files[:200]

    return profile


# ── Project preview ──────────────────────────────────────────────────
import subprocess as _sp
_preview_processes: dict[int, dict] = {}  # run_id -> {proc, port}

@app.post("/api/projects/{run_id}/preview")
async def start_project_preview(run_id: int):
    """Start a Next.js dev server for the built project and return the URL."""
    run = db.get_run(run_id)
    if not run:
        return {"message": "Run not found"}
    pdir = run.get("project_dir", "")
    if not pdir or not os.path.isdir(pdir):
        return {"message": "Project directory not found on disk."}
    if not os.path.isfile(os.path.join(pdir, "package.json")):
        return {"message": "No package.json found — not a buildable project."}

    # If already running, return existing URL
    if run_id in _preview_processes:
        info = _preview_processes[run_id]
        if info["proc"].poll() is None:
            return {"url": f"http://localhost:{info['port']}"}
        else:
            del _preview_processes[run_id]

    # Find a free port (3010+)
    import socket
    port = 3010
    for rid, info in _preview_processes.items():
        if info["proc"].poll() is None and info["port"] >= port:
            port = info["port"] + 1
    # Quick check if port is free
    for _ in range(20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                break
            port += 1

    # Install deps if node_modules missing
    nm = os.path.join(pdir, "node_modules")
    if not os.path.isdir(nm):
        install = _sp.run("npm install", cwd=pdir, capture_output=True, timeout=120,
                          encoding="utf-8", errors="replace", shell=True)
        if install.returncode != 0:
            return {"message": f"npm install failed: {install.stderr[:300]}"}

    # Start dev server
    env = os.environ.copy()
    env["PORT"] = str(port)
    env["NEXT_TELEMETRY_DISABLED"] = "1"
    try:
        # Use node to run next directly (npx not found without shell on Windows)
        next_bin = os.path.join(pdir, "node_modules", "next", "dist", "bin", "next")
        proc = _sp.Popen(["node", next_bin, "dev", "-p", str(port)], cwd=pdir,
                          stdout=_sp.DEVNULL, stderr=_sp.DEVNULL, env=env)
    except FileNotFoundError:
        # Fallback: use shell=True
        proc = _sp.Popen(f"npx next dev -p {port}", cwd=pdir, shell=True,
                          stdout=_sp.DEVNULL, stderr=_sp.DEVNULL, env=env)
    _preview_processes[run_id] = {"proc": proc, "port": port}

    return {"url": f"http://localhost:{port}", "message": "Starting dev server..."}


# ── Cloud deploy endpoint ────────────────────────────────────────────

@app.post("/api/projects/{run_id}/deploy")
async def deploy_project_endpoint(run_id: int):
    """Deploy a built project to GitHub + Vercel."""
    run = db.get_run(run_id)
    if not run:
        return {"deployed": False, "error": "Run not found"}
    pdir = run.get("project_dir", "")
    if not pdir or not os.path.isdir(pdir):
        return {"deployed": False, "error": "Project directory not found on disk."}
    pname = run.get("project_name", "") or f"project-{run_id}"

    # Already deployed?
    if run.get("deployed_url"):
        return {"deployed": True, "vercel_url": run["deployed_url"], "github_url": run.get("github_url", ""), "message": "Already deployed."}

    try:
        from deploy import deploy_project
        result = deploy_project(pdir, pname)
        if result.get("vercel_url") or result.get("github_url"):
            db.update_deploy_urls(
                run_id,
                deployed_url=result.get("vercel_url", ""),
                github_url=result.get("github_url", ""),
            )
        return result
    except Exception as e:
        return {"deployed": False, "error": str(e)}


# ── Notifications endpoints ──────────────────────────────────────────

@app.get("/api/notifications")
async def list_notifications_endpoint(unread_only: bool = False, limit: int = 50):
    safe_limit = max(1, min(int(limit), 200))
    return db.list_notifications(unread_only=unread_only, limit=safe_limit)


@app.get("/api/notifications/count")
async def notification_count():
    return {"unread": db.get_unread_count()}


@app.post("/api/notifications/{notification_id}/read")
async def mark_read(notification_id: int):
    db.mark_notification_read(notification_id)
    return {"status": "read"}


@app.post("/api/notifications/read-all")
async def mark_all_read():
    db.mark_all_notifications_read()
    return {"status": "all_read"}


# ── Resolve notification → save to Knowledge Base ────────────────────

class ResolvePayload(BaseModel):
    answer: str
    category: str = ""

@app.post("/api/notifications/{notification_id}/resolve")
async def resolve_notification(notification_id: int, payload: ResolvePayload):
    """Answer a question-type notification and save it to the knowledge base.
    The AI will use this answer on future pipeline runs."""
    notifs = db.list_notifications(limit=200)
    notif = next((n for n in notifs if n["id"] == notification_id), None)
    if not notif:
        return {"error": "Notification not found"}

    # Extract run_id from link like "run:5"
    run_id = 0
    if notif.get("link", "").startswith("run:"):
        try:
            run_id = int(notif["link"].split(":")[1])
        except (IndexError, ValueError):
            pass

    # Determine category from notification title or user-supplied
    category = payload.category or _extract_category(notif.get("title", ""))

    kid = db.add_knowledge(
        category=category,
        question=notif["title"] + ": " + notif["message"],
        answer=payload.answer,
        source="resolved_notification",
        run_id=run_id,
        notif_id=notification_id,
    )

    # Mark notification as read
    db.mark_notification_read(notification_id)

    # Add a confirmation notification
    db.add_notification(
        type="success",
        title="Knowledge Base Updated",
        message=f"Answer saved for \"{notif['title']}\". AI will use it in future runs.",
        link="",
        source="knowledge",
    )
    return {"status": "resolved", "knowledge_id": kid}


def _extract_category(title: str) -> str:
    """Extract a short category slug from a notification title."""
    title_lower = title.lower()
    mapping = {
        "payment": "payment", "money": "payment", "billing": "payment",
        "cart": "ecommerce", "checkout": "ecommerce", "ecommerce": "ecommerce",
        "auth": "auth", "login": "auth", "signup": "auth",
        "database": "database", "schema": "database",
        "deploy": "deployment", "hosting": "deployment", "server": "deployment",
        "api": "integration", "third-party": "integration",
        "real-time": "realtime", "websocket": "realtime",
        "file": "media", "upload": "media", "image": "media",
    }
    for keyword, cat in mapping.items():
        if keyword in title_lower:
            return cat
    return "general"


# ── Knowledge Base endpoints ─────────────────────────────────────────

class KnowledgePayload(BaseModel):
    category: str = "general"
    question: str
    answer: str

class KnowledgeUpdatePayload(BaseModel):
    answer: str

@app.get("/api/knowledge")
async def list_knowledge_endpoint(category: str | None = None):
    return db.list_knowledge(category=category)

@app.post("/api/knowledge")
async def add_knowledge_endpoint(payload: KnowledgePayload):
    kid = db.add_knowledge(
        category=payload.category,
        question=payload.question,
        answer=payload.answer,
        source="manual",
    )
    return {"status": "created", "knowledge_id": kid}

@app.get("/api/knowledge/summary")
async def knowledge_summary():
    """Quick stats for the knowledge base."""
    entries = db.list_knowledge(active_only=True)
    categories = {}
    for e in entries:
        cat = e.get("category", "general")
        categories[cat] = categories.get(cat, 0) + 1
    all_notifs = db.list_notifications(limit=500)
    unanswered = [n for n in all_notifs if n.get("type") == "question" and not n.get("is_read")]
    return {"total": len(entries), "categories": categories, "unanswered_questions": len(unanswered)}

@app.get("/api/knowledge/questions")
async def ai_questions():
    """Return all AI question notifications, both unanswered and answered."""
    all_notifs = db.list_notifications(limit=500)
    questions = [n for n in all_notifs if n.get("type") == "question"]
    kb_entries = db.list_knowledge()
    resolved_notif_ids = {k.get("notif_id") for k in kb_entries if k.get("notif_id")}
    result = []
    for q in questions:
        result.append({
            **q,
            "resolved": q["id"] in resolved_notif_ids or bool(q.get("is_read")),
        })
    return result

@app.put("/api/knowledge/{kid}")
async def update_knowledge_endpoint(kid: int, payload: KnowledgeUpdatePayload):
    db.update_knowledge(kid, answer=payload.answer)
    return {"status": "updated"}

@app.delete("/api/knowledge/{kid}")
async def delete_knowledge_endpoint(kid: int):
    db.delete_knowledge(kid)
    return {"status": "deleted"}


# ── AI Chat endpoints ────────────────────────────────────────────────

class ChatPayload(BaseModel):
    message: str

@app.get("/api/chat")
async def list_chat():
    return db.list_chat_messages(limit=200)

@app.post("/api/chat")
async def chat_with_ai(payload: ChatPayload):
    """Send a message and get an AI response grounded in full project data."""
    import anthropic
    import re

    user_msg = payload.message.strip()
    if not user_msg:
        return {"error": "Empty message"}

    # Save user message
    db.add_chat_message(role="user", content=user_msg)

    # ── Gather FULL project context ──────────────────────────────────

    runs = db.list_runs(limit=30)
    stats = db.get_stats()
    kb_text = db.get_active_knowledge_text()
    recent_notifications = db.list_notifications(limit=30)
    all_requests = db.list_requests(limit=50)

    # 1) Detailed run data (with pipeline stages, client info, file lists)
    runs_detail = []
    for r in runs[:15]:
        dur = r.get('duration_secs') or 0
        entry = (
            f"Run #{r['id']}: status={r['status']} | project={r.get('project_name','?')} | "
            f"tests={r.get('tests_passed',0) or 0}/{r.get('tests_run',0) or 0} | "
            f"budget=${r.get('budget_usd',0) or 0} | client={r.get('client_name','?')} | "
            f"stack={r.get('tech_stack','')} | dir={r.get('project_dir','')} | "
            f"files={r.get('files_generated',0) or 0} | fixes={r.get('fix_attempts',0) or 0} | "
            f"duration={dur:.0f}s | {(r.get('description','') or '')[:150]}"
        )
        runs_detail.append(entry)

    # 2) Latest run — full pipeline stage data
    latest_stages = ""
    if runs:
        latest = runs[0]
        pd = latest.get("pipeline_data", "")
        if pd:
            try:
                stages = json.loads(pd) if isinstance(pd, str) else pd
                for stage_name, stage_val in stages.items():
                    text = json.dumps(stage_val, indent=1)[:2500] if not isinstance(stage_val, str) else stage_val[:2500]
                    latest_stages += f"\n--- {stage_name.upper()} stage output ---\n{text}\n"
            except (json.JSONDecodeError, TypeError):
                latest_stages = pd[:3000]

    # 3) File system — list generated projects with their file trees
    output_dir = Path(settings.output_dir)
    if not output_dir.is_absolute():
        output_dir = Path(__file__).parent / output_dir
    projects_on_disk = []
    if output_dir.is_dir():
        for proj_dir in sorted(output_dir.iterdir()):
            if proj_dir.is_dir() and proj_dir.name not in ("node_modules", ".next", "__pycache__"):
                files_list = []
                for root, dirs, files in os.walk(proj_dir):
                    dirs[:] = [d for d in dirs if d not in ("node_modules", ".next", ".git", "__pycache__", ".cache")]
                    for f in files:
                        rel = os.path.relpath(os.path.join(root, f), proj_dir)
                        files_list.append(rel.replace("\\", "/"))
                files_list.sort()
                pkg_json = proj_dir / "package.json"
                pkg_info = ""
                if pkg_json.is_file():
                    try:
                        pkg = json.loads(pkg_json.read_text(encoding="utf-8", errors="replace"))
                        deps = list((pkg.get("dependencies") or {}).keys())[:15]
                        pkg_info = f"  deps: {', '.join(deps)}"
                    except Exception:
                        pass
                projects_on_disk.append(
                    f"📁 {proj_dir.name}/ ({len(files_list)} files){pkg_info}\n"
                    f"  files: {', '.join(files_list[:40])}"
                    f"{'...' if len(files_list) > 40 else ''}"
                )

    # 4) Read specific code files if user asks about code
    code_context = ""
    code_keywords = re.findall(r'(?:show|read|look at|open|check|what.s in|contents? of)\s+["\']?([a-zA-Z0-9_./\\-]+\.\w{1,5})', user_msg, re.I)
    # Also check if they mention a project by name
    project_name_match = None
    for r in runs:
        pname = r.get("project_name", "")
        if pname and pname.lower().replace(" ", "") in user_msg.lower().replace(" ", ""):
            project_name_match = r
            break
    if not project_name_match and runs:
        # Default to latest if they say "the project" or "latest"
        if any(w in user_msg.lower() for w in ("the project", "latest project", "last project", "current project")):
            project_name_match = runs[0]

    if code_keywords and project_name_match:
        pdir = project_name_match.get("project_dir", "")
        if pdir and os.path.isdir(pdir):
            for kw in code_keywords[:3]:
                # Search for the file
                for root, dirs, files in os.walk(pdir):
                    dirs[:] = [d for d in dirs if d not in ("node_modules", ".next", ".git")]
                    for f in files:
                        if kw in f or kw == os.path.relpath(os.path.join(root, f), pdir).replace("\\", "/"):
                            fpath = os.path.join(root, f)
                            try:
                                content = Path(fpath).read_text(encoding="utf-8", errors="replace")[:3000]
                                rel = os.path.relpath(fpath, pdir).replace("\\", "/")
                                code_context += f"\n=== FILE: {rel} ===\n{content}\n"
                            except Exception:
                                pass
                            break

    # 5) Client portal requests
    requests_context = []
    for req in all_requests[:10]:
        requests_context.append(
            f"Request #{req['id']}: {req.get('project_title','?')} | "
            f"client={req.get('client_name','?')} | status={req.get('status','new')} | "
            f"type={req.get('project_type','?')} | {req.get('description','')[:100]}"
        )

    # 6) Deployment info
    deploy_info = []
    for r in runs:
        pdir = r.get("project_dir", "")
        if pdir and os.path.isdir(pdir):
            vercel_json = os.path.join(pdir, "vercel.json")
            netlify_toml = os.path.join(pdir, "netlify.toml")
            has_vercel = os.path.isfile(vercel_json)
            has_netlify = os.path.isfile(netlify_toml)
            if has_vercel or has_netlify:
                deploy_info.append(
                    f"{r.get('project_name','?')}: "
                    f"{'vercel.json ✓' if has_vercel else ''} "
                    f"{'netlify.toml ✓' if has_netlify else ''}"
                )
    deploy_tokens = []
    if settings.vercel_token:
        deploy_tokens.append("Vercel token: configured ✓")
    if settings.railway_token:
        deploy_tokens.append("Railway token: configured ✓")
    if settings.github_token:
        deploy_tokens.append("GitHub token: configured ✓")

    # 7) Notifications (typed)
    notif_lines = []
    for n in recent_notifications[:15]:
        notif_lines.append(f"[{n['type']}] {n['title']}: {n['message'][:150]}")

    # ── Build system prompt ──────────────────────────────────────────
    system_prompt = f"""You are the AI assistant for Aigentic Orbit's AI Dev Agency — an automated pipeline that:
1. Scouts Upwork for web dev leads (Next.js, React, Tailwind)
2. Analyzes feasibility & creates Technical Spec Documents
3. Sends proposals to clients via Resend email
4. Builds full Next.js projects automatically using Claude codegen
5. Runs Playwright QA tests with self-healing (up to 2 fix retries)

YOU HAVE FULL ACCESS to: project files on disk, database records, pipeline stage outputs,
client portal requests, deployment configs, and notification history.
Answer accurately with specific data. If the user asks about code, quote the relevant files.
If YOU need clarification to improve the pipeline, ASK the user directly.
When the user teaches you something, acknowledge it and suggest saving to the Knowledge Base.

=== PIPELINE STATS ===
Total runs: {stats.get('total_runs', 0) or 0} | Passed: {stats.get('passed', 0) or 0} | Failed: {stats.get('failed', 0) or 0} | Errors: {stats.get('errors', 0) or 0}
Avg duration: {(stats.get('avg_duration_secs') or 0):.0f}s | Total files generated: {stats.get('total_files_generated', 0) or 0}
Total tests run: {stats.get('total_tests_run', 0) or 0} | Tests passed: {stats.get('total_tests_passed', 0) or 0}
Revenue (passed runs): ${stats.get('total_revenue', 0) or 0} | Pipeline value (all): ${stats.get('total_pipeline_value', 0) or 0}

=== ALL RUNS (newest first) ===
{chr(10).join(runs_detail) if runs_detail else 'No runs yet.'}

=== LATEST RUN — PIPELINE STAGE OUTPUTS ===
{latest_stages if latest_stages else 'None available.'}

=== PROJECTS ON DISK ({output_dir}) ===
{chr(10).join(projects_on_disk) if projects_on_disk else 'No projects on disk.'}

=== DEPLOYMENT CONFIG ===
{chr(10).join(deploy_tokens) if deploy_tokens else 'No deployment tokens configured.'}
{chr(10).join(deploy_info) if deploy_info else 'No deployment configs found in projects.'}

=== CLIENT PORTAL REQUESTS ===
{chr(10).join(requests_context) if requests_context else 'No requests.'}

=== NOTIFICATIONS ===
{chr(10).join(notif_lines) if notif_lines else 'None.'}

{kb_text}
{code_context}
"""

    # Get recent chat history for context
    chat_history = db.list_chat_messages(limit=30)
    messages = []
    for m in chat_history:
        messages.append({"role": m["role"], "content": m["content"]})

    try:
        chat_model = settings.llm_model
        if chat_model.startswith("anthropic/"):
            chat_model = chat_model.split("/", 1)[1]
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.create(
            model=chat_model,
            max_tokens=2000,
            system=system_prompt,
            messages=messages,
        )
        ai_text = response.content[0].text

        # Save AI response
        db.add_chat_message(role="assistant", content=ai_text)

        # Auto-detect if the AI is asking the user something → save as notification
        ask_patterns = [
            r"(?:could you|can you|would you|do you|should I)\s+(?:tell|clarify|confirm|explain|provide|share|let me know)",
            r"what (?:is|are|would|should|do)",
            r"(?:I need|I'd need) (?:to know|you to|more info|clarification)",
        ]
        is_asking = any(re.search(p, ai_text, re.I) for p in ask_patterns)
        if is_asking and "?" in ai_text:
            lines = ai_text.split("\n")
            q_lines = [l for l in lines if "?" in l]
            q_text = q_lines[0] if q_lines else ai_text[:200]
            db.add_notification(
                type="question",
                title="AI Wants To Know",
                message=q_text.strip(),
                source="ai_chat",
            )

        return {"role": "assistant", "content": ai_text}

    except Exception as e:
        error_msg = f"Sorry, I couldn't process that: {str(e)}"
        db.add_chat_message(role="assistant", content=error_msg)
        return {"role": "assistant", "content": error_msg}


@app.post("/api/chat/save-to-kb")
async def save_chat_to_kb(payload: KnowledgePayload):
    """Save a Q&A pair from chat directly to knowledge base."""
    kid = db.add_knowledge(
        category=payload.category,
        question=payload.question,
        answer=payload.answer,
        source="chat",
    )
    return {"status": "saved", "knowledge_id": kid}

@app.delete("/api/chat")
async def clear_chat_history():
    db.clear_chat()
    return {"status": "cleared"}


# ─────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("  AI Dev Agency — Full Pipeline Run")
    print(f"  Mock mode: {settings.mock_mode}")
    print("=" * 60)
    output = run_pipeline()
    print("\n" + "=" * 60)
    print("  PIPELINE COMPLETE")
    print("=" * 60)
    print(json.dumps(output, indent=2))
