"""
Lightweight pipeline scheduler — runs the pipeline on a schedule without n8n.

Usage:
  python scripts/scheduler.py               # Run every 6 hours (default)
  python scripts/scheduler.py --interval 2  # Run every 2 hours
  python scripts/scheduler.py --once        # Run once immediately and exit

Logs to: logs/scheduler.log
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

# ── Config ───────────────────────────────────────────────────────────
BASE_URL = os.getenv("PIPELINE_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
DEFAULT_INTERVAL_HOURS = 6
LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "scheduler.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("scheduler")


def trigger_pipeline() -> dict:
    """POST /api/pipeline/trigger and return the response JSON."""
    log.info("Triggering pipeline run...")
    resp = httpx.post(f"{BASE_URL}/api/pipeline/trigger", timeout=30)
    resp.raise_for_status()
    return resp.json()


def wait_for_run(run_id: int, poll_interval: int = 15, timeout_secs: int = 1800) -> dict:
    """Poll /api/runs/{run_id} until status != 'running', or timeout."""
    deadline = time.time() + timeout_secs
    while time.time() < deadline:
        time.sleep(poll_interval)
        try:
            resp = httpx.get(f"{BASE_URL}/api/runs/{run_id}", timeout=10)
            resp.raise_for_status()
            data = resp.json()
            status = data.get("status", "running")
            if status != "running":
                return data
        except Exception as e:
            log.warning(f"Polling error: {e}")
    log.warning(f"Run #{run_id} timed out after {timeout_secs}s")
    return {"run_id": run_id, "status": "timeout"}


def log_run_result(result: dict) -> None:
    run_id = result.get("id") or result.get("run_id")
    status = result.get("status", "unknown")
    files = result.get("files_generated", 0)
    tests_passed = result.get("tests_passed", 0)
    tests_run = result.get("tests_run", 0)
    project = result.get("project_name", "—")
    fix_attempts = result.get("fix_attempts", 0)
    error = result.get("error_message", "")

    if status == "pass":
        log.info(f"PASSED Run #{run_id} -- {project} | {files} files | {tests_passed}/{tests_run} tests | {fix_attempts} fixes")
    elif status == "fail":
        log.warning(f"FAILED Run #{run_id} -- {project} | {error[:200]}")
    else:
        log.error(f"ERROR  Run #{run_id} -- {error[:200]}")


def run_once() -> None:
    """Trigger one pipeline run and wait for completion."""
    try:
        trigger_resp = trigger_pipeline()
        run_id = trigger_resp.get("run_id")
        if not run_id:
            log.error(f"No run_id in trigger response: {trigger_resp}")
            return

        log.info(f"Run #{run_id} started. Waiting for completion...")
        result = wait_for_run(run_id)
        log_run_result(result)

    except httpx.ConnectError:
        log.error(f"Cannot connect to backend at {BASE_URL}. Is the server running?")
    except Exception as e:
        log.exception(f"Unexpected error: {e}")


def run_loop(interval_hours: float) -> None:
    """Run the pipeline repeatedly every `interval_hours` hours."""
    interval_secs = int(interval_hours * 3600)
    log.info(f"Scheduler started — interval: {interval_hours}h ({interval_secs}s)")
    log.info(f"Backend: {BASE_URL} | Logs: {LOG_DIR}/scheduler.log")
    log.info("Press Ctrl+C to stop.\n")

    while True:
        log.info(f"{'='*50}")
        log.info(f"Scheduled run at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        run_once()
        log.info(f"Next run in {interval_hours}h. Sleeping...\n")
        time.sleep(interval_secs)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Dev Agency Pipeline Scheduler")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_HOURS,
                        help=f"Run interval in hours (default: {DEFAULT_INTERVAL_HOURS})")
    parser.add_argument("--once", action="store_true",
                        help="Trigger one run immediately and exit")
    args = parser.parse_args()

    if args.once:
        run_once()
    else:
        run_loop(args.interval)
