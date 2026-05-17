"""Quick test: run Upwork scraper actor with a small query to see output format."""
import httpx
import json
import time
import os
from dotenv import load_dotenv
load_dotenv()

token = os.getenv("APIFY_API_TOKEN")

# Run neatrat/upwork-job-scraper with minimal input
actor_id = "neatrat~upwork-job-scraper"
run_url = f"https://api.apify.com/v2/acts/{actor_id}/runs"

print("Starting Upwork Job Scraper...")
run_resp = httpx.post(
    run_url,
    params={"token": token},
    json={
        "searchQuery": "next.js react",
        "maxResults": 5,
    },
    timeout=30,
)
run_resp.raise_for_status()
run_data = run_resp.json()["data"]
dataset_id = run_data["defaultDatasetId"]
run_id = run_data["id"]
print(f"Run started: {run_id}")

# Poll for completion
for i in range(40):
    time.sleep(3)
    s = httpx.get(f"https://api.apify.com/v2/actor-runs/{run_id}", params={"token": token}, timeout=15)
    status = s.json()["data"]["status"]
    if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
        print(f"Run finished: {status}")
        break
    if i % 5 == 0:
        print(f"  Still running... ({i*3}s)")

if status != "SUCCEEDED":
    print(f"ERROR: Run {status}")
    exit(1)

# Fetch results
items_resp = httpx.get(
    f"https://api.apify.com/v2/datasets/{dataset_id}/items",
    params={"token": token, "limit": 3},
    timeout=30,
)
items = items_resp.json()
print(f"\nGot {len(items)} results. First item keys:")
if items:
    print(json.dumps(items[0], indent=2)[:2000])
