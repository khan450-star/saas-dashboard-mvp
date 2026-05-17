"""
Mock OpenAI-compatible LLM server for demo/testing.

Returns scripted responses that simulate what a real LLM would produce
for each pipeline phase.  Runs on port 11434.

Usage:
    python mock_llm_server.py  (runs in background)
"""

from __future__ import annotations

import json
import re
import time
import uuid

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

# ── Scripted responses for each agent phase ──────────────────────────

SCOUT_RESPONSE = json.dumps([
    {
        "source": "upwork",
        "title": "Build a Next.js SaaS Dashboard",
        "description": "Full-stack Next.js 14 app with Tailwind, Prisma, PostgreSQL, Stripe billing, NextAuth, landing page, and 3 internal pages.",
        "budget_usd": 3500,
        "client_name": "Sarah Chen",
        "client_email": "sarah.chen@example.com",
        "posted_at": "2026-03-29T14:22:00Z",
        "url": "https://www.upwork.com/jobs/~example123",
        "tags": ["next.js", "react", "tailwind", "prisma", "stripe", "saas"]
    }
], indent=2)

ANALYST_RESPONSE = json.dumps([
    {
        "status": "approved",
        "project_name": "SaaS Dashboard Pro",
        "stack": ["Next.js 14", "React 18", "Tailwind CSS", "Prisma ORM", "PostgreSQL", "NextAuth.js", "Stripe SDK"],
        "pages": ["Landing Page", "Dashboard", "Settings", "Billing"],
        "integrations": ["Stripe Checkout", "NextAuth (Google + GitHub)", "Prisma + PostgreSQL"],
        "database": "PostgreSQL via Prisma",
        "estimated_hours": 48,
        "estimated_cost_usd": 2400,
        "milestones": [
            {"name": "Scaffold & Auth", "hours": 10},
            {"name": "Dashboard UI", "hours": 12},
            {"name": "Billing Integration", "hours": 10},
            {"name": "Settings & Profile", "hours": 8},
            {"name": "Testing & Polish", "hours": 8}
        ],
        "risks": [
            "Stripe webhook testing requires ngrok or similar tunnel",
            "PostgreSQL connection string must be configured for deployment"
        ],
        "client_name": "Sarah Chen",
        "client_email": "sarah.chen@example.com"
    }
], indent=2)

CLOSER_RESPONSE = json.dumps([
    {
        "deal_status": "confirmed",
        "client_email": "sarah.chen@example.com",
        "client_name": "Sarah Chen",
        "project_name": "SaaS Dashboard Pro",
        "tsd": {
            "status": "approved",
            "project_name": "SaaS Dashboard Pro",
            "stack": ["Next.js 14", "React 18", "Tailwind CSS", "Prisma ORM", "PostgreSQL"],
            "pages": ["Landing Page", "Dashboard", "Settings", "Billing"],
            "integrations": ["Stripe Checkout", "NextAuth"],
            "database": "PostgreSQL via Prisma",
            "estimated_hours": 48,
            "estimated_cost_usd": 2400
        },
        "email_sent": True,
        "payment_link": "https://pay.stripe.com/demo/saas-dashboard-pro"
    }
], indent=2)

BUILDER_RESPONSE = json.dumps([
    {
        "project_name": "SaaS Dashboard Pro",
        "repo_url": "https://github.com/ai-agency/saas-dashboard-pro",
        "deploy_url": "https://saas-dashboard-pro.vercel.app",
        "files_generated": 42,
        "build_status": "success"
    }
], indent=2)

AUDITOR_RESPONSE = json.dumps([
    {
        "project_name": "SaaS Dashboard Pro",
        "deploy_url": "https://saas-dashboard-pro.vercel.app",
        "status": "pass",
        "tests_run": 18,
        "tests_passed": 18,
        "tests_failed": 0,
        "bugs": [],
        "lighthouse": {"performance": 92, "accessibility": 88, "best_practices": 95, "seo": 90},
        "recommendation": "Ready for client delivery."
    }
], indent=2)

# ── Route to the right scripted response based on message content ────

def pick_response(messages: list[dict]) -> str:
    """Inspect the conversation to determine which phase we're in."""
    combined = " ".join(m.get("content", "") for m in messages).lower()

    if "scan" in combined and ("lead" in combined or "scraper" in combined or "job" in combined):
        return SCOUT_RESPONSE
    if "feasibility" in combined or "tsd" in combined or "technical specification" in combined or "analyst" in combined:
        return ANALYST_RESPONSE
    if "closer" in combined or "proposal" in combined or "email" in combined or "deal" in combined or "confirmed" in combined:
        return CLOSER_RESPONSE
    if "builder" in combined or "deploy" in combined or "code" in combined or "build" in combined:
        return BUILDER_RESPONSE
    if "auditor" in combined or "qa" in combined or "test" in combined or "bug" in combined:
        return AUDITOR_RESPONSE

    # Fallback — generic acknowledgment
    return '{"status": "ok", "message": "Task acknowledged and completed."}'


# ── OpenAI-compatible /v1/chat/completions endpoint ──────────────────

class ChatMessage(BaseModel):
    role: str
    content: str = ""

class ChatRequest(BaseModel):
    model: str = "mock-model"
    messages: list[ChatMessage] = []
    temperature: float = 0.7
    max_tokens: int = 4096
    stream: bool = False

    class Config:
        extra = "allow"


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatRequest):
    messages = [{"role": m.role, "content": m.content} for m in req.messages]
    content = pick_response(messages)

    return {
        "id": f"chatcmpl-mock-{uuid.uuid4().hex[:8]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": req.model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 200,
            "total_tokens": 300,
        },
    }


# ── Also serve /v1/models for health checks ─────────────────────────

@app.get("/v1/models")
async def list_models():
    return {
        "data": [{"id": "mock-model", "object": "model", "owned_by": "ai-dev-agency"}]
    }


if __name__ == "__main__":
    import uvicorn
    print("Mock LLM server starting on http://localhost:11434")
    uvicorn.run(app, host="127.0.0.1", port=11434)
