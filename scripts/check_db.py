import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db
runs = db.list_runs()
print(f"Total runs: {len(runs)}")
for r in runs:
    dur = r.get('duration_secs') or 0
    print(f"  #{r['id']} {r['status']} tests:{r['tests_passed']}/{r['tests_run']} files:{r['files_generated']} fixes:{r['fix_attempts']} dur:{dur:.0f}s")
stats = db.get_stats()
print(f"\nStats: {stats}")
