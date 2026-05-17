"""Show all fields from the last Upwork actor run."""
import httpx, json, os
from dotenv import load_dotenv
load_dotenv()

token = os.getenv("APIFY_API_TOKEN")

# Get last run for the actor
r = httpx.get(
    "https://api.apify.com/v2/acts/neatrat~upwork-job-scraper/runs/last",
    params={"token": token},
    timeout=15,
)
run_data = r.json()["data"]
dataset_id = run_data["defaultDatasetId"]
print(f"Dataset: {dataset_id}, Status: {run_data['status']}")

items_resp = httpx.get(
    f"https://api.apify.com/v2/datasets/{dataset_id}/items",
    params={"token": token, "limit": 3},
    timeout=30,
)
items = items_resp.json()
print(f"Got {len(items)} items\n")

if items:
    print("=== ALL FIELDS (first result) ===")
    for key, val in items[0].items():
        val_str = str(val)[:200]
        print(f"  {key}: {val_str}")
    
    print(f"\n=== KEY FIELDS FOR ALL RESULTS ===")
    for i, item in enumerate(items):
        print(f"\nJob {i+1}: {item.get('title', 'N/A')[:80]}")
        for key in ['budget', 'hourlyRate', 'fixedPrice', 'amount', 'payment',
                     'clientCountry', 'clientCity', 'clientName', 'clientEmail',
                     'postedOn', 'skills', 'category', 'subcategory', 'url',
                     'connectsRequired', 'proposals', 'experienceLevel',
                     'clientSpent', 'clientHires', 'clientReviews']:
            if key in item and item[key]:
                print(f"  {key}: {str(item[key])[:120]}")
