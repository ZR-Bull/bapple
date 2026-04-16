import requests
import time
import json
import os
from dotenv import load_dotenv
from pathlib import Path

#dotenv_path= Path('./env')
print()
load_dotenv()

print()
URL = "https://api.beertech.com/singularity/graphql"
webhook_url = os.getenv('WEBHOOK_URL')
# This is the exact string from your payload
# We use f-strings so you can change the zipCode or radius easily
QUERY = """
query LocateRetailers {
    locateRetailers(
        brandName: "BUSCH LT APPLE"
        limit: 100
        zipCode: "97333"
        radius: 25.0
        productDescriptions: ["BUSCH LIGHT APPLE 30/12 OZ CAN DSTK","BUSCH LIGHT APPLE 24/12 OZ CAN 2/12","BUSCH LIGHT APPLE 15/25 AL CAN SHRINK","BUSCH LIGHT APPLE 24/12 OZ CAN","BUSCH LIGHT APPLE 48/12 AL CAN","BUSCH LIGHT APPLE 24/16 OZ CAN 4/6","BUSCH LIGHT APPLE 1/2 BBL SV"]
    ) {
        retailers {
            vpid
            name
            address
            city
            state
            zipCode
            distance
        }
    }
}
"""

HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Origin": "https://www.busch.com",
    "Referer": "https://www.busch.com/"
}

def send_to_discord(retailers):
    if not webhook_url:
        return

    zip_code = "97333"
    locator_url = "https://www.busch.com/locator"

    if retailers:
        count = len(retailers)
        top_spots = retailers[:5]
        top_lines = [
            (
                f"- {shop.get('name', 'Unknown')} "
                f"({shop.get('distance', '?')} mi)"
            )
            for shop in top_spots
        ]

        payload = {
            "content": f"Bapple found at {count} place(s) near {zip_code}.",
            "embeds": [
                {
                    "title": "Open Busch Locator",
                    "url": locator_url,
                    "description": "Tap the title to view all stores.",
                    "color": 5763719,
                    "fields": [
                        {
                            "name": "Nearby Results",
                            "value": "\n".join(top_lines) if top_lines else "No store details",
                            "inline": False,
                        }
                    ],
                    "footer": {"text": time.strftime("Checked at %Y-%m-%d %H:%M:%S")},
                }
            ],
        }
    else:
        payload = {
            "content": f"No bapple stock near {zip_code} right now.",
            "embeds": [
                {
                    "title": "Open Busch Locator",
                    "url": locator_url,
                    "description": "Tap the title to check manually.",
                    "color": 15158332,
                    "footer": {"text": time.strftime("Checked at %Y-%m-%d %H:%M:%S")},
                }
            ],
        }

    try:
        requests.post(webhook_url, json=payload, timeout=15)
    except Exception as e:
        print(f"Discord webhook failed: {e}")

def check_stock():
    # Because they hardcoded arguments, we send 'variables' as an empty dict
    payload = {
        "query": QUERY,
        "variables": {}
    }
    
    try:
        response = requests.post(URL, json=payload, headers=HEADERS)
        
        if response.status_code != 200:
            print(f"Error {response.status_code}: {response.text}")
            return False
            
        data = response.json()
        
        # Drilling down: data -> locateRetailers -> retailers
        retailers = data.get('data', {}).get('locateRetailers', {}).get('retailers', [])

        print(json.dumps(retailers, indent=2))
        
        if retailers:
            print(f"🚨 FOUND THE APPLE! {len(retailers)} locations nearby.")
            for shop in retailers:
                print(f" - {shop['name']} at {shop['address']}, {shop['city']} ({shop['distance']} miles)")
            send_to_discord(retailers)
            return True
        else:
            print(f"[{time.strftime('%H:%M:%S')}] No stock in 97333. Checking again in 1 hour.")
            send_to_discord(retailers)
            return False

    except Exception as e:
        print(f"Request failed: {e}")
        return False

if __name__ == "__main__":
    while True:
        if check_stock():
            # Add your SMS notification logic here!
            # Example: send_text("Busch Apple found in Corvallis!")
            pass
        
        # Sleep for 1 hour (3600 seconds)
        time.sleep(30)
