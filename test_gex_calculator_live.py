from core.auto_config import fetch_instruments, get_nearest_tuesday
from core.gex_calculator import resolve_strike_chain, fetch_chain_greeks_and_oi, compute_signed_gex, classify_regime
from datetime import date
import upstox_client, os

cfg = upstox_client.Configuration()
cfg.access_token = os.getenv("UPSTOX_ACCESS_TOKEN")
client = upstox_client.ApiClient(cfg)

market_api = upstox_client.MarketQuoteApi(client)
resp = market_api.ltp("NSE_INDEX|Nifty 50", api_version="2.0")
spot = float(list(resp.data.values())[0].last_price)
print(f"Spot: {spot}")

instruments = fetch_instruments()
expiry = get_nearest_tuesday(date.today())
print(f"Expiry: {expiry}")

chain_keys = resolve_strike_chain(instruments, expiry, spot)
print(f"Resolved {sum(1 for legs in chain_keys.values() for k in legs.values() if k)} of {len(chain_keys)*2} contracts")

chain_data = fetch_chain_greeks_and_oi(client, chain_keys)
gex = compute_signed_gex(chain_data, spot)
regime = classify_regime(gex)

print()
print(f"Net GEX: {regime['net_gex']:,.0f}")
print(f"Regime: {regime['regime']}")
print(f"Top positive strikes: {regime['top_positive_strikes']}")
print(f"Top negative strikes: {regime['top_negative_strikes']}")
