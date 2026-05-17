"""Backfill project_name/project_dir from result_json for older runs."""
import sqlite3, json
from pathlib import Path

DB = Path(__file__).parent.parent / "pipeline_history.db"
conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row

rows = conn.execute("SELECT id, result_json, project_name, project_dir FROM pipeline_runs").fetchall()
for r in rows:
    rj = r["result_json"] or ""
    pname = r["project_name"] or ""
    pdir = r["project_dir"] or ""
    if pname:
        print(f"  #{r['id']}: already has name={pname}")
        continue
    if not rj:
        print(f"  #{r['id']}: no result_json")
        continue
    try:
        d = json.loads(rj)
        if isinstance(d, list) and d:
            d = d[0]
        if isinstance(d, dict):
            new_name = d.get("project_name", "")
            new_dir = d.get("project_dir", "")
            if new_name or new_dir:
                conn.execute(
                    "UPDATE pipeline_runs SET project_name=?, project_dir=? WHERE id=?",
                    (new_name or pname, new_dir or pdir, r["id"]),
                )
                print(f"  #{r['id']}: backfilled -> {new_name}")
            else:
                print(f"  #{r['id']}: no name in result_json")
        else:
            print(f"  #{r['id']}: result_json not dict")
    except Exception as e:
        print(f"  #{r['id']}: parse error: {e}")

conn.commit()
conn.close()
print("Backfill complete!")
