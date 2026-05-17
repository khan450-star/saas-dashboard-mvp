"""Central configuration — loads .env and exposes typed settings."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv(Path(__file__).parent / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # LLM — global fallback
    anthropic_api_key: str = Field(default="MOCK_KEY")
    openai_api_key: str = Field(default="")
    gemini_api_key: str = Field(default="")
    llm_model: str = Field(default="claude-sonnet-4-20250514")

    # Per-agent model overrides (2026 specialist assignment)
    # Architect (analyst): Claude Opus — best at system design & PRD reasoning
    architect_model: str = Field(default="anthropic/claude-sonnet-4-20250514")
    # Lead Dev (builder): Claude 3.5 Sonnet — best at code generation
    lead_dev_model: str = Field(default="anthropic/claude-sonnet-4-20250514")
    # Security Auditor: Claude Opus — strict OWASP analysis
    security_model: str = Field(default="anthropic/claude-sonnet-4-20250514")
    # QA Tester (auditor): Claude 3.5 Sonnet — best at browser/Playwright test plans
    qa_model: str = Field(default="anthropic/claude-sonnet-4-20250514")

    # Scout
    apify_api_token: str = Field(default="")
    browse_ai_api_key: str = Field(default="")

    # Closer / Email
    resend_api_key: str = Field(default="")
    email_from: str = Field(default="hello@shiftbite.ai")
    email_from_scout: str = Field(default="scout@shiftbite.ai")
    email_from_support: str = Field(default="support@shiftbite.ai")
    email_mock: bool = Field(default=True)
    smtp_host: str = Field(default="smtp.gmail.com")
    smtp_port: int = Field(default=587)
    smtp_user: str = Field(default="")
    smtp_password: str = Field(default="")
    stripe_secret_key: str = Field(default="")

    # Builder
    builder_mock: bool = Field(default=True)
    output_dir: str = Field(default="output")
    database_url: str = Field(default="")
    vercel_token: str = Field(default="")
    railway_token: str = Field(default="")
    github_token: str = Field(default="")

    # Auditor
    qa_mock: bool = Field(default=True)
    browserstack_user: str = Field(default="")
    browserstack_key: str = Field(default="")

    # Webhooks
    n8n_webhook_url: str = Field(default="http://localhost:5678/webhook/new-lead")
    crewai_callback_url: str = Field(default="http://localhost:8000/api/pipeline/result")
    cors_origins: str = Field(default="http://127.0.0.1:8000,http://localhost:8000")

    # Mock mode — when True, no real LLM calls are made (uses local mock server)
    mock_mode: bool = Field(default=True)

    # Tools mock mode — when True, external tools (scraper, email, deploy, QA)
    # return simulated data.  Allows real LLM + fake external services.
    tools_mock_mode: bool = Field(default=True)

    # Project selection behavior:
    # - "small": default for testing, prefer smallest/simplest approved project
    # - "big": prefer highest-value/highest-complexity approved project
    project_selection_mode: str = Field(default="small")

    # Online automation
    auto_pipeline_interval_minutes: int = Field(default=0)

    @property
    def cors_origins_list(self) -> list[str]:
        raw = (self.cors_origins or "").strip()
        if raw == "*":
            return ["*"]
        return [origin.strip() for origin in raw.split(",") if origin.strip()]


settings = Settings()
