"""Check the input schema and sample output for Upwork & Freelancer Apify actors."""
import httpx
import json

token = None  # Public store API doesn't need a token for actor info

actors = [
    "neatrat/upwork-job-scraper",
    "piotrv1001/freelancer-jobs-scraper",
]

for actor_id in actors:
    print(f"\n{'='*60}")
    print(f"ACTOR: {actor_id}")
    print('='*60)
    
    # Get actor info
    r = httpx.get(f"https://api.apify.com/v2/acts/{actor_id}", timeout=15)
    data = r.json()["data"]
    print(f"Description: {data.get('description', 'N/A')[:200]}")
    
    # Get default run input from the latest build's input schema
    version = data.get("versions", [])
    if version:
        latest = version[-1]
        print(f"Latest version: {latest.get('versionNumber')}")
    
    # Get the input schema 
    r2 = httpx.get(
        f"https://api.apify.com/v2/acts/{actor_id}/input-schema",
        timeout=15,
    )
    if r2.status_code == 200:
        schema = r2.json()
        print(f"\nInput Schema Properties:")
        for prop_name, prop_val in schema.get("properties", {}).items():
            print(f"  {prop_name}: {prop_val.get('description', prop_val.get('title', ''))[:100]}")
    else:
        print(f"\nInput schema response: {r2.status_code}")
        # Try alternative endpoint
        r3 = httpx.get(
            f"https://api.apify.com/v2/acts/{actor_id}",
            params={"token": ""},
            timeout=15,
        )
        if r3.status_code == 200:
            d = r3.json()["data"]
            print(f"  defaultRunOptions: {json.dumps(d.get('defaultRunOptions', {}), indent=4)[:500]}")
    
    print()
