"""Email / CRM Tool — sends emails and manages client comms.

In MOCK mode, prints the email to stdout.
In LIVE mode, sends via Resend.com API.
"""

from __future__ import annotations

import json

import httpx
from crewai.tools import BaseTool

from config import settings


class EmailTool(BaseTool):
    name: str = "send_email"
    description: str = (
        "Send an email to a client. Input must be a JSON string with keys: "
        "to, subject, body. Optional key: from_alias ('hello', 'scout', or 'support')."
    )

    def _resolve_sender(self, alias: str | None = None) -> str:
        """Pick the sender address based on alias."""
        if alias == "scout":
            return settings.email_from_scout
        if alias == "support":
            return settings.email_from_support
        return settings.email_from  # default = hello@

    def _run(self, email_json: str) -> str:
        data = json.loads(email_json)
        to = data["to"]
        subject = data["subject"]
        body = data["body"]
        sender = self._resolve_sender(data.get("from_alias"))

        if settings.email_mock:
            return self._mock_send(to, subject, body, sender)
        return self._live_send(to, subject, body, sender)

    def _mock_send(self, to: str, subject: str, body: str, sender: str = "") -> str:
        return (
            f"[MOCK EMAIL]\n"
            f"From: {sender or settings.email_from}\n"
            f"To: {to}\n"
            f"Subject: {subject}\n"
            f"---\n{body}\n---\n"
            f"Status: sent (mock)"
        )

    def _live_send(self, to: str, subject: str, body: str, sender: str = "") -> str:
        if not (settings.resend_api_key or "").strip():
            return self._mock_send(to, subject, body, sender)

        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {settings.resend_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": sender or settings.email_from,
                "to": [to],
                "subject": subject,
                "text": body,
            },
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()
        return f"Email sent to {to} from {sender or settings.email_from} (Resend ID: {result.get('id', 'unknown')})"
