"""Show all fields from the Upwork actor results."""
import httpx, json, os
from dotenv import load_dotenv
load_dotenv()

token = os.getenv("APIFY_API_TOKEN")
# Reuse the last dataset
items_resp = httpx.get(
    "https://api.apify.com/v2/datasets/Ic2pTfcZgVPROR2ab/items",
    params={"token": token, "limit": 3},
    timeout=30,
)
items = items_resp.json()

# Show all keys and sample values for first item
if items:
    print("=== ALL FIELDS (first result) ===")
    for key, val in items[0].items():
        val_str = str(val)[:150]
        print(f"  {key}: {val_str}")
    
    print(f"\n=== BUDGET/PAYMENT INFO ===")
    for i, item in enumerate(items):
        print(f"\nJob {i+1}: {item.get('title', 'N/A')[:60]}")
        for key in ['budget', 'hourlyRate', 'fixedPrice', 'amount', 'payment', 
                     'clientCountry', 'clientCity', 'clientName', 'clientEmail',
                     'postedOn', 'skills', 'category', 'subcategory',
                     'connectsRequired', 'proposals', 'experienceLevel']:
            if key in item:
                print(f"  {key}: {str(item[key])[:100]}")
