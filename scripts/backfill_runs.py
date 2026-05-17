"""Backfill project_name and project_dir from result_json for existing runs."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db

conn = db._connect()
rows = conn.execute("SELECT id, result_json, project_dir FROM pipeline_runs").fetchall()
for row in rows:
    run_id = row["id"]
    rj = row["result_json"] or ""
    existing_dir = row["project_dir"] or ""
    if existing_dir:
        continue  # already backfilled

    project_name = ""
    project_dir = ""
    files_count = 0
    try:
        data = json.loads(rj)
        if isinstance(data, list) and data:
            project_name = data[0].get("project_name", "")
            project_dir = data[0].get("project_dir", "")
    except (json.JSONDecodeError, TypeError):
        pass

    # Count files in project_dir if it exists
    from pathlib import Path
    if project_dir and Path(project_dir).is_dir():
        files_count = sum(
            1 for f in Path(project_dir).rglob("*")
            if f.is_file() and "node_modules" not in f.parts and ".next" not in f.parts
        )

    if project_name or project_dir or files_count:
        updates = []
        params = []
        if project_name:
            updates.append("project_name = ?")
            params.append(project_name)
        if project_dir:
            updates.append("project_dir = ?")
            params.append(project_dir)
        if files_count:
            updates.append("files_generated = ?")
            params.append(files_count)
        params.append(run_id)
        conn.execute(f"UPDATE pipeline_runs SET {', '.join(updates)} WHERE id = ?", params)
        print(f"  #{run_id}: {project_name} ({files_count} files) -> {project_dir}")
    else:
        print(f"  #{run_id}: (no data to backfill)")

conn.commit()
conn.close()
print("\nBackfill complete!")
