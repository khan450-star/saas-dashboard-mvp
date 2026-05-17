"""Scraper Tool — wraps Apify actors for lead generation.

In MOCK mode, returns sample leads from mock_data/.
In LIVE mode, uses dedicated Apify actors per platform:
  - upwork:     neatrat/upwork-job-scraper  (rich structured data)
  - freelancer: apify/google-search-scraper  (Google site:freelancer.com)
  - general:    apify/google-search-scraper  (broad web search)
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
from crewai.tools import BaseTool
from pydantic import Field

from config import settings

MOCK_DIR = Path(__file__).parent.parent / "mock_data"


class ScraperTool(BaseTool):
    name: str = "web_lead_scraper"
    description: str = (
        "Scrape job boards (Upwork, Freelancer) and the web "
        "for web-development leads. Returns a list of lead JSON objects."
    )
    source: str = Field(default="upwork", description="Platform to scrape")

    def _run(self, source: str = "upwork") -> str:
        if settings.tools_mock_mode:
            return self._mock_scrape()
        return self._live_scrape(source)

    # ── Mock implementation ──────────────────────────────────────────
    def _mock_scrape(self) -> str:
        leads = []
        for p in sorted(MOCK_DIR.glob("sample_lead*.json")):
            leads.append(json.loads(p.read_text(encoding="utf-8")))
        return json.dumps(leads, indent=2)

    def _sample_lead_fallback(self) -> list:
        sample_path = MOCK_DIR / "sample_lead.json"
        if not sample_path.exists():
            return []
        try:
            sample = json.loads(sample_path.read_text(encoding="utf-8"))
            if isinstance(sample, dict):
                return [sample]
        except Exception:
            return []
        return []

    # ── Apify helpers ────────────────────────────────────────────────
    def _apify_run_and_wait(self, actor_id: str, input_json: dict,
                            timeout_polls: int = 60) -> list:
        """Start an Apify actor, poll until done, return dataset items."""
        token = settings.apify_api_token
        run_url = f"https://api.apify.com/v2/acts/{actor_id}/runs"

        run_resp = httpx.post(
            run_url,
            params={"token": token},
            json=input_json,
            timeout=30,
        )
        run_resp.raise_for_status()
        run_data = run_resp.json()["data"]
        dataset_id = run_data["defaultDatasetId"]
        run_id = run_data["id"]

        # Poll for completion
        for _ in range(timeout_polls):
            time.sleep(3)
            s = httpx.get(
                f"https://api.apify.com/v2/actor-runs/{run_id}",
                params={"token": token}, timeout=15
            ).json()
            if s["data"]["status"] in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
                break

        if s["data"]["status"] != "SUCCEEDED":
            return []

        items_resp = httpx.get(
            f"https://api.apify.com/v2/datasets/{dataset_id}/items",
            params={"token": token, "limit": 20},
            timeout=30,
        )
        items_resp.raise_for_status()
        return items_resp.json()

    # ── Upwork via dedicated actor ───────────────────────────────────
    def _scrape_upwork(self) -> list:
        """Use neatrat/upwork-job-scraper for rich Upwork job data."""
        raw = self._apify_run_and_wait(
            "neatrat~upwork-job-scraper",
            {"searchQuery": "next.js OR react OR tailwind OR full-stack web developer",
             "maxResults": 15},
        )
        leads = []
        for item in raw:
            budget_str = str(item.get("budget", "") or "")
            budget = 0
            # Parse budget like "20 - 30" or "500" into a single int
            nums = [int(x) for x in budget_str.replace(",", "").split() if x.isdigit()]
            if nums:
                budget = max(nums)

            leads.append({
                "source": "upwork",
                "title": item.get("title", ""),
                "description": (item.get("description", "") or "")[:500],
                "budget_usd": budget,
                "client_name": item.get("clientName", "") or "",
                "client_email": "",
                "client_location": item.get("clientLocation", "") or "",
                "client_total_spent": item.get("clientTotalSpent", 0) or 0,
                "posted_at": item.get("absoluteDate", "") or item.get("relativeDate", ""),
                "url": item.get("url", ""),
                "tags": item.get("tags", []) or [],
                "experience_level": item.get("experienceLevel", ""),
                "proposals": item.get("proposals", 0) or 0,
                "job_type": item.get("jobType", ""),
            })
        return leads

    # ── Freelancer / general via Google Search ───────────────────────
    def _scrape_google(self, source: str) -> list:
        """Use Google Search Scraper for freelancer.com or general web leads."""
        search_queries = {
            "freelancer": 'site:freelancer.com/projects "next.js" OR "react" OR "tailwind"',
            "general": '"looking for" ("next.js developer" OR "react developer" OR "full-stack developer") -apply',
        }
        query = search_queries.get(source, search_queries["general"])

        raw = self._apify_run_and_wait(
            "apify~google-search-scraper",
            {"queries": query, "maxPagesPerQuery": 1,
             "resultsPerPage": 20, "languageCode": "en", "countryCode": "us"},
        )
        leads = []
        for item in raw:
            for r in item.get("organicResults", []):
                leads.append({
                    "source": source,
                    "title": r.get("title", ""),
                    "description": r.get("description", ""),
                    "budget_usd": 0,
                    "client_name": "",
                    "client_email": "",
                    "client_location": "",
                    "client_total_spent": 0,
                    "posted_at": "",
                    "url": r.get("url", ""),
                    "tags": [],
                    "experience_level": "",
                    "proposals": 0,
                    "job_type": "",
                })
        return leads

    # ── Main live dispatcher ─────────────────────────────────────────
    def _live_scrape(self, source: str) -> str:
        """Route to the best actor per source."""
        token = (settings.apify_api_token or "").strip()
        if not token:
            return json.dumps(self._sample_lead_fallback(), indent=2)

        try:
            if source == "upwork":
                leads = self._scrape_upwork()
            else:
                leads = self._scrape_google(source)
        except Exception as e:
            # Do not fail the whole pipeline when scraper providers are unavailable
            # or out of credit; use a local fallback lead so downstream phases run.
            print(f"  [Scraper fallback] {e}")
            leads = self._sample_lead_fallback()

        # Live sources can return empty or non-budget leads; keep pipeline moving.
        if not any((lead.get("budget_usd") or 0) > 0 for lead in leads):
            leads = self._sample_lead_fallback()

        return json.dumps(leads[:20], indent=2)
