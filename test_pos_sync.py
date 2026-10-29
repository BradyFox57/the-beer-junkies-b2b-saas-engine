import json
import requests
from datetime import datetime, timezone

# 1. Target your live Make.com automation canvas webhook listener
MAKE_WEBHOOK_URL = "https://hook.us2.make.com/go4wyxp0qagpqagut0vbrbc9rjs5cepb"

# 2. Construct a simulated tokenized Square transaction entry for your taproom
mock_square_transaction = {
    "brewery_id": "8aa7b5e4-230d-40c2-b36b-73347b746860",               # Your exact Supabase Project ID
    "square_order_id": "sq_ord_20261024_011XF",
    "product_name": "Irish Red Ale",
    "quantity_sold": 2,                                  # A round of two pints
    "gross_revenue": 14.72,                              # $7.36 per pint x 2
    "sold_at_timestamp": datetime.now(timezone.utc).isoformat(),
    "serving_type": "16oz Pint"
}

print("Dispatching tokenized Square POS transaction stream payload to Make.com...")

try:
    response = requests.post(MAKE_WEBHOOK_URL, json=mock_square_transaction, timeout=5)
    if response.status_code == 200:
        print("[SUCCESS] Payload ingested by automation server cleanly!")
    else:
        print(f"Network Notice: Target responded with status code {response.status_code}")
except Exception as e:
    print(f"Handshake error: {e}")