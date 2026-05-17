"""Clean up stuck 'running' runs that were killed."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db

conn = db._connect()
conn.execute(
    "UPDATE pipeline_runs SET status = 'error', error_message = 'Server killed during run' "
    "WHERE status = 'running'"
)
conn.commit()
conn.close()
print("Cleaned up stuck runs")
