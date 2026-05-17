import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db

r = db.get_run(12)
pd_raw = r["pipeline_data"]
print(f"pipeline_data type: {type(pd_raw).__name__}, len: {len(pd_raw)}")

pd = json.loads(pd_raw)
print(f"pd type after loads: {type(pd).__name__}, keys: {list(pd.keys())}")

scout_val = pd["scout"]
print(f"scout type: {type(scout_val).__name__}, len: {len(str(scout_val))}")
print(f"scout first 50 chars repr: {repr(str(scout_val)[:50])}")

# Try direct json.loads
try:
    result = json.loads(scout_val)
    print(f"json.loads worked! type={type(result).__name__}")
except Exception as e:
    print(f"json.loads failed: {e}")

# Try bracket finding manually
if isinstance(scout_val, str):
    start = scout_val.find("[")
    end = scout_val.rfind("]")
    print(f"bracket positions: start={start}, end={end}")
    if start >= 0 and end > start:
        snippet = scout_val[start:start+50]
        print(f"snippet at start: {repr(snippet)}")
        try:
            parsed = json.loads(scout_val[start:end+1])
            print(f"Bracket parse worked! type={type(parsed).__name__}, len={len(parsed)}")
        except Exception as e:
            print(f"Bracket parse failed: {e}")
            # Show the problematic area
            print(f"Near error... end snippet: {repr(scout_val[max(0,end-50):end+1])}")
elif isinstance(scout_val, list):
    print(f"Already a list! len={len(scout_val)}")
    if scout_val and isinstance(scout_val[0], dict):
        print(f"First item keys: {list(scout_val[0].keys())}")

