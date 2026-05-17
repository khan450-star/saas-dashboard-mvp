"""
Pipeline storage.
=================
Stores runs, notifications, requests, and knowledge in either:
- PostgreSQL (when DATABASE_URL is configured)
- SQLite (fallback for local development)
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from config import settings

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except Exception:  # optional dependency for local sqlite-only mode
    psycopg2 = None
    RealDictCursor = None

DB_PATH = Path(__file__).parent / "pipeline_history.db"
DATABASE_URL = (settings.database_url or "").strip()


def storage_backend() -> str:
    return "postgresql" if DATABASE_URL.lower().startswith(("postgres://", "postgresql://")) else "sqlite"


def _is_postgres() -> bool:
    return storage_backend() == "postgresql"


def _pgify_sql(sql: str) -> str:
    converted = sql.replace("?", "%s")
    return converted.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")


class _PgCursorAdapter:
    def __init__(self, cursor, lastrowid=None):
        self._cursor = cursor
        self.lastrowid = lastrowid

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()


class _PgConnectionAdapter:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql: str, params: tuple = ()):  # sqlite-like API
        pg_sql = _pgify_sql(sql)
        cur = self._conn.cursor(cursor_factory=RealDictCursor)
        lastrowid = None
        is_insert = pg_sql.lstrip().lower().startswith("insert ") and " returning " not in pg_sql.lower()
        if is_insert:
            pg_sql = pg_sql.rstrip().rstrip(";") + " RETURNING id"
        cur.execute(pg_sql, params)
        if is_insert:
            row = cur.fetchone() or {}
            lastrowid = row.get("id") if isinstance(row, dict) else None
        return _PgCursorAdapter(cur, lastrowid=lastrowid)

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


def _new_connection():
    if _is_postgres():
        if psycopg2 is None:
            raise RuntimeError("DATABASE_URL is set but psycopg2 is not installed")
        raw = psycopg2.connect(DATABASE_URL)
        return _PgConnectionAdapter(raw)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

# ── Schema ───────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT    NOT NULL,
    finished_at     TEXT,
    duration_secs   REAL,
    mock_mode       INTEGER NOT NULL DEFAULT 0,
    status          TEXT    NOT NULL DEFAULT 'running',   -- running | pass | fail | error
    leads_found     INTEGER DEFAULT 0,
    projects_built  INTEGER DEFAULT 0,
    files_generated INTEGER DEFAULT 0,
    tests_run       INTEGER DEFAULT 0,
    tests_passed    INTEGER DEFAULT 0,
    tests_failed    INTEGER DEFAULT 0,
    fix_attempts    INTEGER DEFAULT 0,
    result_json     TEXT,
    error_message   TEXT,
    project_name    TEXT    DEFAULT '',
    project_dir     TEXT    DEFAULT '',
    budget_usd      REAL    DEFAULT 0,
    client_name     TEXT    DEFAULT '',
    tech_stack      TEXT    DEFAULT '',
    description     TEXT    DEFAULT '',
    pipeline_data   TEXT    DEFAULT '',
    deployed_url    TEXT    DEFAULT '',
    github_url      TEXT    DEFAULT '',
    current_phase   TEXT    DEFAULT ''
);
"""

_MIGRATIONS = [
    "ALTER TABLE pipeline_runs ADD COLUMN project_name TEXT DEFAULT ''",
    "ALTER TABLE pipeline_runs ADD COLUMN project_dir TEXT DEFAULT ''",
    "ALTER TABLE pipeline_runs ADD COLUMN budget_usd REAL DEFAULT 0",
    "ALTER TABLE pipeline_runs ADD COLUMN client_name TEXT DEFAULT ''",
    "ALTER TABLE pipeline_runs ADD COLUMN tech_stack TEXT DEFAULT ''",
    "ALTER TABLE pipeline_runs ADD COLUMN description TEXT DEFAULT ''",
    "ALTER TABLE pipeline_runs ADD COLUMN pipeline_data TEXT DEFAULT ''",
    "ALTER TABLE pipeline_runs ADD COLUMN deployed_url TEXT DEFAULT ''",
    "ALTER TABLE pipeline_runs ADD COLUMN github_url TEXT DEFAULT ''",
    "ALTER TABLE pipeline_runs ADD COLUMN current_phase TEXT DEFAULT ''",
]
# ── Write helpers ────────────────────────────────────────────────────

def update_current_phase(run_id: int, phase: str) -> None:
    """Update the current phase for a running pipeline."""
    conn = _connect()
    conn.execute(
        "UPDATE pipeline_runs SET current_phase = ? WHERE id = ?",
        (phase, run_id),
    )
    conn.commit()
    conn.close()


def _connect():
    conn = _new_connection()
    conn.execute(_pgify_sql(_SCHEMA) if _is_postgres() else _SCHEMA)
    if not _is_postgres():
        for mig in _MIGRATIONS:
            try:
                conn.execute(mig)
            except sqlite3.OperationalError:
                pass  # column already exists
    conn.commit()
    return conn


# ── Write helpers ────────────────────────────────────────────────────

def start_run(mock_mode: bool) -> int:
    """Insert a new run row and return its id."""
    conn = _connect()
    cur = conn.execute(
        "INSERT INTO pipeline_runs (started_at, mock_mode) VALUES (?, ?)",
        (datetime.now(timezone.utc).isoformat(), int(mock_mode)),
    )
    conn.commit()
    run_id = cur.lastrowid
    conn.close()
    return run_id


def finish_run(
    run_id: int,
    *,
    status: str,
    leads_found: int = 0,
    projects_built: int = 0,
    files_generated: int = 0,
    tests_run: int = 0,
    tests_passed: int = 0,
    tests_failed: int = 0,
    fix_attempts: int = 0,
    result_json: str = "",
    error_message: str = "",
    project_name: str = "",
    project_dir: str = "",
    budget_usd: float = 0,
    client_name: str = "",
    tech_stack: str = "",
    description: str = "",
    pipeline_data: str = "",
) -> None:
    """Finalize a run with results."""
    # Normalize impossible "pass" cases so dashboard reflects reality.
    if status == "pass" and projects_built == 0 and tests_run == 0:
        status = "fail"
        if not error_message:
            if leads_found == 0:
                error_message = "No qualifying leads found by Scout."
            else:
                error_message = "Run completed without build/QA output."

    conn = _connect()
    now = datetime.now(timezone.utc).isoformat()

    # Calculate duration from started_at
    row = conn.execute(
        "SELECT started_at FROM pipeline_runs WHERE id = ?", (run_id,)
    ).fetchone()
    duration = 0.0
    if row:
        started = datetime.fromisoformat(row["started_at"])
        duration = (datetime.now(timezone.utc) - started).total_seconds()

    conn.execute(
        """UPDATE pipeline_runs SET
            finished_at = ?, duration_secs = ?, status = ?,
            leads_found = ?, projects_built = ?, files_generated = ?,
            tests_run = ?, tests_passed = ?, tests_failed = ?,
            fix_attempts = ?, result_json = ?, error_message = ?,
            project_name = ?, project_dir = ?,
            budget_usd = ?, client_name = ?, tech_stack = ?,
            description = ?, pipeline_data = ?
           WHERE id = ?""",
        (
            now, duration, status,
            leads_found, projects_built, files_generated,
            tests_run, tests_passed, tests_failed,
            fix_attempts, result_json, error_message,
            project_name, project_dir,
            budget_usd, client_name, tech_stack,
            description, pipeline_data,
            run_id,
        ),
    )
    conn.commit()
    conn.close()


def update_deploy_urls(run_id: int, *, deployed_url: str = "", github_url: str = "") -> None:
    """Update the deploy URLs for a completed run."""
    conn = _connect()
    conn.execute(
        "UPDATE pipeline_runs SET deployed_url = ?, github_url = ? WHERE id = ?",
        (deployed_url, github_url, run_id),
    )
    conn.commit()
    conn.close()


# ── Read helpers ─────────────────────────────────────────────────────

def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    d["mock_mode"] = bool(d["mock_mode"])

    # Defensive normalization for legacy rows and edge cases.
    if d.get("status") == "pass" and d.get("projects_built", 0) == 0 and d.get("tests_run", 0) == 0:
        d["status"] = "fail"
        if not d.get("error_message"):
            if d.get("leads_found", 0) == 0:
                d["error_message"] = "No qualifying leads found by Scout."
            else:
                d["error_message"] = "Run completed without build/QA output."
    return d


def get_run(run_id: int) -> dict | None:
    conn = _connect()
    row = conn.execute(
        "SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)
    ).fetchone()
    conn.close()
    return _row_to_dict(row)


def get_latest_run() -> dict | None:
    conn = _connect()
    row = conn.execute(
        "SELECT * FROM pipeline_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return _row_to_dict(row)


def list_runs(limit: int = 50, offset: int = 0) -> list[dict]:
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM pipeline_runs ORDER BY id DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def get_stats() -> dict:
    """Aggregate stats across all runs."""
    conn = _connect()
    if _is_postgres():
        stats_sql = """
            SELECT
                COUNT(*)                            AS total_runs,
                SUM(CASE WHEN status='pass' THEN 1 ELSE 0 END) AS passed,
                SUM(CASE WHEN status='fail' THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS errors,
                ROUND(CAST(AVG(duration_secs) AS NUMERIC), 1) AS avg_duration_secs,
                SUM(files_generated)                AS total_files_generated,
                SUM(tests_run)                      AS total_tests_run,
                SUM(tests_passed)                   AS total_tests_passed,
                SUM(fix_attempts)                   AS total_fix_attempts,
                ROUND(CAST(SUM(CASE WHEN status='pass' THEN budget_usd ELSE 0 END) AS NUMERIC), 2) AS total_revenue,
                ROUND(CAST(SUM(budget_usd) AS NUMERIC), 2) AS total_pipeline_value
            FROM pipeline_runs
            WHERE status != 'running'
        """
    else:
        stats_sql = """
            SELECT
                COUNT(*)                            AS total_runs,
                SUM(CASE WHEN status='pass' THEN 1 ELSE 0 END) AS passed,
                SUM(CASE WHEN status='fail' THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS errors,
                ROUND(AVG(duration_secs), 1)        AS avg_duration_secs,
                SUM(files_generated)                AS total_files_generated,
                SUM(tests_run)                      AS total_tests_run,
                SUM(tests_passed)                   AS total_tests_passed,
                SUM(fix_attempts)                   AS total_fix_attempts,
                ROUND(SUM(CASE WHEN status='pass' THEN budget_usd ELSE 0 END), 2) AS total_revenue,
                ROUND(SUM(budget_usd), 2)           AS total_pipeline_value
            FROM pipeline_runs
            WHERE status != 'running'
        """
    row = conn.execute(stats_sql).fetchone()
    conn.close()
    return dict(row) if row else {}


# ══════════════════════════════════════════════════════════════════════
# Notifications
# ══════════════════════════════════════════════════════════════════════

_NOTIFICATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT    NOT NULL,
    type        TEXT    NOT NULL DEFAULT 'info',
    title       TEXT    NOT NULL,
    message     TEXT    NOT NULL DEFAULT '',
    link        TEXT    DEFAULT '',
    is_read     INTEGER NOT NULL DEFAULT 0,
    source      TEXT    DEFAULT ''
);
"""


def _connect_notifications():
    conn = _new_connection()
    conn.execute(_pgify_sql(_NOTIFICATION_SCHEMA) if _is_postgres() else _NOTIFICATION_SCHEMA)
    conn.commit()
    return conn


def add_notification(
    *,
    type: str = "info",
    title: str,
    message: str = "",
    link: str = "",
    source: str = "",
) -> int:
    """Create a notification. Types: info, success, warning, error, question."""
    conn = _connect_notifications()
    cur = conn.execute(
        """INSERT INTO notifications (created_at, type, title, message, link, source)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (datetime.now(timezone.utc).isoformat(), type, title, message, link, source),
    )
    conn.commit()
    nid = cur.lastrowid
    conn.close()
    return nid


def list_notifications(unread_only: bool = False, limit: int = 50) -> list[dict]:
    conn = _connect_notifications()
    if unread_only:
        rows = conn.execute(
            "SELECT * FROM notifications WHERE is_read = 0 ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM notifications ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_notification_read(notification_id: int) -> None:
    conn = _connect_notifications()
    conn.execute("UPDATE notifications SET is_read = 1 WHERE id = ?", (notification_id,))
    conn.commit()
    conn.close()


def mark_all_notifications_read() -> None:
    conn = _connect_notifications()
    conn.execute("UPDATE notifications SET is_read = 1 WHERE is_read = 0")
    conn.commit()
    conn.close()


def get_unread_count() -> int:
    conn = _connect_notifications()
    row = conn.execute("SELECT COUNT(*) AS cnt FROM notifications WHERE is_read = 0").fetchone()
    conn.close()
    return row["cnt"] if row else 0


# ══════════════════════════════════════════════════════════════════════
# Knowledge Base  —  AI learns from answered questions
# ══════════════════════════════════════════════════════════════════════

_KNOWLEDGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_base (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    category    TEXT    NOT NULL DEFAULT 'general',
    question    TEXT    NOT NULL,
    answer      TEXT    NOT NULL,
    source      TEXT    DEFAULT '',
    run_id      INTEGER DEFAULT 0,
    notif_id    INTEGER DEFAULT 0,
    active      INTEGER NOT NULL DEFAULT 1
);
"""


def _connect_knowledge():
    conn = _new_connection()
    conn.execute(_pgify_sql(_KNOWLEDGE_SCHEMA) if _is_postgres() else _KNOWLEDGE_SCHEMA)
    conn.commit()
    return conn


def add_knowledge(
    *,
    category: str = "general",
    question: str,
    answer: str,
    source: str = "",
    run_id: int = 0,
    notif_id: int = 0,
) -> int:
    """Store a new knowledge entry (AI learns from the answer)."""
    now = datetime.now(timezone.utc).isoformat()
    conn = _connect_knowledge()
    cur = conn.execute(
        """INSERT INTO knowledge_base (created_at, updated_at, category, question, answer, source, run_id, notif_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (now, now, category, question, answer, source, run_id, notif_id),
    )
    conn.commit()
    kid = cur.lastrowid
    conn.close()
    return kid


def update_knowledge(knowledge_id: int, *, answer: str) -> None:
    """Update an existing knowledge entry."""
    now = datetime.now(timezone.utc).isoformat()
    conn = _connect_knowledge()
    conn.execute(
        "UPDATE knowledge_base SET answer = ?, updated_at = ? WHERE id = ?",
        (answer, now, knowledge_id),
    )
    conn.commit()
    conn.close()


def delete_knowledge(knowledge_id: int) -> None:
    conn = _connect_knowledge()
    conn.execute("DELETE FROM knowledge_base WHERE id = ?", (knowledge_id,))
    conn.commit()
    conn.close()


def list_knowledge(category: str | None = None, active_only: bool = True) -> list[dict]:
    conn = _connect_knowledge()
    if category:
        rows = conn.execute(
            "SELECT * FROM knowledge_base WHERE category = ? AND active >= ? ORDER BY id DESC",
            (category, 1 if active_only else 0),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM knowledge_base WHERE active >= ? ORDER BY id DESC",
            (1 if active_only else 0,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_knowledge(knowledge_id: int) -> dict | None:
    conn = _connect_knowledge()
    row = conn.execute("SELECT * FROM knowledge_base WHERE id = ?", (knowledge_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_active_knowledge_text() -> str:
    """Build a text block of all active knowledge for injecting into prompts.

    Returns a compact string the AI agents read before each pipeline run.
    """
    entries = list_knowledge(active_only=True)
    if not entries:
        return ""
    lines = ["=== KNOWLEDGE BASE (learned from past runs) ==="]
    for e in entries:
        lines.append(f"[{e['category'].upper()}] Q: {e['question']}")
        lines.append(f"  A: {e['answer']}")
        lines.append("")
    lines.append("=== END KNOWLEDGE BASE ===")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
# AI Chat  —  conversational Q&A about the project
# ══════════════════════════════════════════════════════════════════════

_CHAT_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT    NOT NULL,
    role        TEXT    NOT NULL,
    content     TEXT    NOT NULL,
    saved_to_kb INTEGER NOT NULL DEFAULT 0
);
"""


def _connect_chat():
    conn = _new_connection()
    conn.execute(_pgify_sql(_CHAT_SCHEMA) if _is_postgres() else _CHAT_SCHEMA)
    conn.commit()
    return conn


def add_chat_message(*, role: str, content: str) -> int:
    conn = _connect_chat()
    cur = conn.execute(
        "INSERT INTO chat_messages (created_at, role, content) VALUES (?, ?, ?)",
        (datetime.now(timezone.utc).isoformat(), role, content),
    )
    conn.commit()
    mid = cur.lastrowid
    conn.close()
    return mid


def list_chat_messages(limit: int = 100) -> list[dict]:
    conn = _connect_chat()
    rows = conn.execute(
        "SELECT * FROM chat_messages ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]


def clear_chat() -> None:
    conn = _connect_chat()
    conn.execute("DELETE FROM chat_messages")
    conn.commit()
    conn.close()


# ══════════════════════════════════════════════════════════════════════
# Project Requests (Client Intake)
# ══════════════════════════════════════════════════════════════════════

_REQUEST_SCHEMA = """
CREATE TABLE IF NOT EXISTS project_requests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT    NOT NULL,
    client_name     TEXT    NOT NULL,
    client_email    TEXT    NOT NULL DEFAULT '',
    company         TEXT    DEFAULT '',
    project_title   TEXT    NOT NULL,
    description     TEXT    DEFAULT '',
    budget_range    TEXT    DEFAULT '',
    timeline        TEXT    DEFAULT '',
    tech_preferences TEXT   DEFAULT '',
    communication   TEXT    NOT NULL DEFAULT 'email',   -- email | chat | both
    status          TEXT    NOT NULL DEFAULT 'new',      -- new | reviewing | in-progress | completed | archived
    priority        TEXT    DEFAULT 'normal',            -- low | normal | high | urgent
    assigned_run_id INTEGER DEFAULT NULL,
    notes           TEXT    DEFAULT ''
);
"""

_MESSAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS request_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id  INTEGER NOT NULL,
    created_at  TEXT    NOT NULL,
    sender      TEXT    NOT NULL DEFAULT 'client',       -- client | team
    message     TEXT    NOT NULL,
    FOREIGN KEY (request_id) REFERENCES project_requests(id)
);
"""


def _connect_requests():
    conn = _new_connection()
    conn.execute(_pgify_sql(_REQUEST_SCHEMA) if _is_postgres() else _REQUEST_SCHEMA)
    conn.execute(_pgify_sql(_MESSAGE_SCHEMA) if _is_postgres() else _MESSAGE_SCHEMA)
    conn.commit()
    return conn


def create_request(
    *,
    client_name: str,
    client_email: str = "",
    company: str = "",
    project_title: str,
    description: str = "",
    budget_range: str = "",
    timeline: str = "",
    tech_preferences: str = "",
    communication: str = "email",
    priority: str = "normal",
) -> int:
    conn = _connect_requests()
    cur = conn.execute(
        """INSERT INTO project_requests
           (created_at, client_name, client_email, company, project_title,
            description, budget_range, timeline, tech_preferences,
            communication, priority)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            datetime.now(timezone.utc).isoformat(),
            client_name, client_email, company, project_title,
            description, budget_range, timeline, tech_preferences,
            communication, priority,
        ),
    )
    conn.commit()
    req_id = cur.lastrowid
    # Auto-create a welcome message from team
    conn.execute(
        """INSERT INTO request_messages (request_id, created_at, sender, message)
           VALUES (?, ?, 'team', ?)""",
        (req_id, datetime.now(timezone.utc).isoformat(),
         f"Thanks for submitting your project, {client_name}! We've received your request and will review it shortly."),
    )
    conn.commit()
    conn.close()
    return req_id


def update_request_status(request_id: int, status: str, notes: str = "") -> None:
    conn = _connect_requests()
    if notes:
        conn.execute(
            "UPDATE project_requests SET status = ?, notes = ? WHERE id = ?",
            (status, notes, request_id),
        )
    else:
        conn.execute(
            "UPDATE project_requests SET status = ? WHERE id = ?",
            (status, request_id),
        )
    conn.commit()
    conn.close()


def list_requests(status: str | None = None, limit: int = 50) -> list[dict]:
    conn = _connect_requests()
    if status:
        rows = conn.execute(
            "SELECT * FROM project_requests WHERE status = ? ORDER BY id DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM project_requests ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_request(request_id: int) -> dict | None:
    conn = _connect_requests()
    row = conn.execute(
        "SELECT * FROM project_requests WHERE id = ?", (request_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def add_message(request_id: int, sender: str, message: str) -> int:
    conn = _connect_requests()
    cur = conn.execute(
        """INSERT INTO request_messages (request_id, created_at, sender, message)
           VALUES (?, ?, ?, ?)""",
        (request_id, datetime.now(timezone.utc).isoformat(), sender, message),
    )
    conn.commit()
    msg_id = cur.lastrowid
    conn.close()
    return msg_id


def get_messages(request_id: int) -> list[dict]:
    conn = _connect_requests()
    rows = conn.execute(
        "SELECT * FROM request_messages WHERE request_id = ? ORDER BY id ASC",
        (request_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_request_stats() -> dict:
    conn = _connect_requests()
    row = conn.execute("""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN status='new' THEN 1 ELSE 0 END) AS new_count,
            SUM(CASE WHEN status='reviewing' THEN 1 ELSE 0 END) AS reviewing,
            SUM(CASE WHEN status='in-progress' THEN 1 ELSE 0 END) AS in_progress,
            SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed,
            SUM(CASE WHEN status='archived' THEN 1 ELSE 0 END) AS archived
        FROM project_requests
    """).fetchone()
    conn.close()
    return dict(row) if row else {}
