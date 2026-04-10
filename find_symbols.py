import os

import upstox_client
from dotenv import load_dotenv

load_dotenv()
cfg = upstox_client.Configuration()
cfg.access_token = os.getenv("UPSTOX_ACCESS_TOKEN")
api = upstox_client.MarketQuoteApi(upstox_client.ApiClient(cfg))
print("Scanning near 54628...")
for i in range(54620, 54800):
    try:
        r = api.ltp(f"NSE_FO|{i}", api_version="2.0")
        keys = list(r.data.keys()) if r.data else []
        if keys and r.data[keys[0]].last_price > 0:
            print(f"NSE_FO|{i} | key: {keys[0]} | LTP: {r.data[keys[0]].last_price}")
    except:
        pass
