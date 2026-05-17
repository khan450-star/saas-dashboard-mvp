"""Test the Freelancer Jobs Scraper actor."""
import httpx, json, time, os
from dotenv import load_dotenv
load_dotenv()

token = os.getenv("APIFY_API_TOKEN")
actor_id = "piotrv1001~freelancer-jobs-scraper"

print("Starting Freelancer Jobs Scraper...")
run_resp = httpx.post(
    f"https://api.apify.com/v2/acts/{actor_id}/runs",
    params={"token": token},
    json={
        "searchTerm": "next.js react",
        "maxItems": 5,
    },
    timeout=30,
)
run_resp.raise_for_status()
run_data = run_resp.json()["data"]
dataset_id = run_data["defaultDatasetId"]
run_id = run_data["id"]
print(f"Run started: {run_id}")

for i in range(40):
    time.sleep(3)
    s = httpx.get(f"https://api.apify.com/v2/actor-runs/{run_id}", params={"token": token}, timeout=15)
    status = s.json()["data"]["status"]
    if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
        print(f"Run finished: {status}")
        break
    if i % 5 == 0:
        print(f"  Still running... ({i*3}s)")

if status == "SUCCEEDED":
    items_resp = httpx.get(
        f"https://api.apify.com/v2/datasets/{dataset_id}/items",
        params={"token": token, "limit": 3},
        timeout=30,
    )
    items = items_resp.json()
    print(f"\nGot {len(items)} items")
    if items:
        print("\n=== ALL FIELDS (first result) ===")
        for key, val in items[0].items():
            print(f"  {key}: {str(val)[:200]}")
else:
    print(f"ERROR: {status}")
