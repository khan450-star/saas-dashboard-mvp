import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
from config import settings

SQLITE_DB_PATH = db.DB_PATH
TABLES = [
    (
        "pipeline_runs",
        [
            "id", "started_at", "finished_at", "duration_secs", "mock_mode", "status",
            "leads_found", "projects_built", "files_generated", "tests_run", "tests_passed",
            "tests_failed", "fix_attempts", "result_json", "error_message", "project_name",
            "project_dir", "budget_usd", "client_name", "tech_stack", "description",
            "pipeline_data", "deployed_url", "github_url", "current_phase",
        ],
    ),
    (
        "notifications",
        ["id", "created_at", "type", "title", "message", "link", "is_read", "source"],
    ),
    (
        "knowledge_base",
        ["id", "created_at", "updated_at", "category", "question", "answer", "source", "run_id", "notif_id", "active"],
    ),
    (
        "chat_messages",
        ["id", "created_at", "role", "content", "saved_to_kb"],
    ),
    (
        "project_requests",
        [
            "id", "created_at", "client_name", "client_email", "company", "project_title",
            "description", "budget_range", "timeline", "tech_preferences", "communication",
            "status", "priority", "assigned_run_id", "notes",
        ],
    ),
    (
        "request_messages",
        ["id", "request_id", "created_at", "sender", "message"],
    ),
]


def _ensure_postgres_schema() -> None:
    conns = [
        db._connect(),
        db._connect_notifications(),
        db._connect_knowledge(),
        db._connect_chat(),
        db._connect_requests(),
    ]
    for conn in conns:
        conn.close()



def _fetch_sqlite_rows(table_name: str, columns: list[str]) -> list[tuple]:
    conn = sqlite3.connect(str(SQLITE_DB_PATH))
    try:
        cur = conn.execute(f"SELECT {', '.join(columns)} FROM {table_name} ORDER BY id ASC")
        return cur.fetchall()
    finally:
        conn.close()



def _upsert_table(pg_conn, table_name: str, columns: list[str], rows: list[tuple]) -> int:
    if not rows:
        return 0
    from psycopg2.extras import execute_values

    cols_csv = ", ".join(columns)
    conflict_updates = ", ".join(
        f"{col} = EXCLUDED.{col}" for col in columns if col != "id"
    )
    sql = (
        f"INSERT INTO {table_name} ({cols_csv}) VALUES %s "
        f"ON CONFLICT (id) DO UPDATE SET {conflict_updates}"
    )
    with pg_conn.cursor() as cur:
        execute_values(cur, sql, rows, page_size=200)
    return len(rows)



def _reset_sequence(pg_conn, table_name: str) -> None:
    sql = (
        f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), "
        f"COALESCE((SELECT MAX(id) FROM {table_name}), 1), true)"
    )
    with pg_conn.cursor() as cur:
        cur.execute(sql)



def main() -> int:
    database_url = (settings.database_url or os.getenv("DATABASE_URL") or "").strip()
    if not database_url:
        print("ERROR: DATABASE_URL is not set. Set it to your PostgreSQL connection string first.")
        return 1
    if not database_url.startswith(("postgres://", "postgresql://")):
        print("ERROR: DATABASE_URL must point to PostgreSQL for this migration.")
        return 1
    if not SQLITE_DB_PATH.exists():
        print(f"ERROR: Local SQLite DB not found at {SQLITE_DB_PATH}")
        return 1

    try:
        import psycopg2
    except ModuleNotFoundError:
        print("ERROR: psycopg2 is not installed. Run `pip install -r requirements.txt` first.")
        return 1

    print(f"Using local SQLite source: {SQLITE_DB_PATH}")
    print("Ensuring PostgreSQL schema exists...")
    _ensure_postgres_schema()

    migrated_counts = {}
    pg_conn = psycopg2.connect(database_url)
    try:
        for table_name, columns in TABLES:
            rows = _fetch_sqlite_rows(table_name, columns)
            count = _upsert_table(pg_conn, table_name, columns, rows)
            _reset_sequence(pg_conn, table_name)
            migrated_counts[table_name] = count
            print(f"Migrated {count} row(s) into {table_name}")
        pg_conn.commit()
    finally:
        pg_conn.close()

    print("Migration complete.")
    total = sum(migrated_counts.values())
    print(f"Total rows migrated: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
