"""Search Apify Store for Upwork and Freelancer scraper actors."""
import httpx
import json

for term in ["upwork jobs", "freelancer jobs"]:
    print(f"\n=== {term.upper()} ===")
    r = httpx.get(
        "https://api.apify.com/v2/store",
        params={"search": term, "limit": 5},
        timeout=15,
    )
    items = r.json()["data"]["items"]
    for i in items:
        print(f"  {i['username']}/{i['name']}")
        print(f"    Title:  {i['title']}")
        print(f"    Runs:   {i.get('stats', {}).get('totalRuns', '?')}")
        print(f"    Users:  {i.get('stats', {}).get('totalUsers', '?')}")
        print()
