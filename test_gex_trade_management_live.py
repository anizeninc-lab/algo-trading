from dotenv import load_dotenv
load_dotenv()
import time
import os
from datetime import date

import upstox_client

from core.market_context import market_context
from core.auto_config import fetch_instruments, get_nearest_tuesday
from core.gex_calculator import (
    resolve_strike_chain, fetch_chain_greeks_and_oi,
    compute_signed_gex, classify_regime,
)
from core.gex_ema_stack import get_multi_timeframe_stack, seed_historical_15min_candles
from core.gex_trade_management import build_trade_plan, check_time_stop

ACCOUNT_EQUITY = 50000.0  # fixed capital pool, per handoff decision

print("Starting market_context (live NIFTY ticks)...")
market_context.start()
print("Waiting 30 seconds to let ticks accumulate...")
time.sleep(30)

candles = market_context.session_candles_1min
print(f"Session 1-min candles so far: {len(candles)}")

# ── Spot price ────────────────────────────────────────────────────────
cfg = upstox_client.Configuration()
cfg.access_token = os.getenv("UPSTOX_ACCESS_TOKEN")
client = upstox_client.ApiClient(cfg)
market_api = upstox_client.MarketQuoteApi(client)
resp = market_api.ltp("NSE_INDEX|Nifty 50", api_version="2.0")
spot = float(list(resp.data.values())[0].last_price)
print(f"Spot: {spot}")

# ── GEX regime (live) ────────────────────────────────────────────────
instruments = fetch_instruments()
expiry = get_nearest_tuesday(date.today())
chain_keys = resolve_strike_chain(instruments, expiry, spot)
chain_data = fetch_chain_greeks_and_oi(client, chain_keys)
gex = compute_signed_gex(chain_data, spot)
gex_regime = classify_regime(gex)
print(f"GEX regime: {gex_regime['regime']} | net_gex={gex_regime['net_gex']:,.0f}")

# ── EMA stack (live) ─────────────────────────────────────────────────
seed = seed_historical_15min_candles(os.getenv("UPSTOX_ACCESS_TOKEN"))
stack = get_multi_timeframe_stack(candles, seed)
print(f"EMA stack: 5min={stack.tf_5min} | agreement={stack.agreement} | direction={stack.direction}")

if stack.tf_5min is None:
    print("\nNot enough candles yet for EMA9 -- run again after market's been open longer.")
    market_context.stop()
    raise SystemExit(0)

ema50 = stack.tf_5min.ema50
direction = stack.direction if stack.direction in ("bullish", "bearish") else "bullish"
print(f"\nUsing direction={direction} (from stack agreement, or defaulted to 'bullish' for this test)")

# ── Build trade plan ─────────────────────────────────────────────────
plan = build_trade_plan(
    direction=direction,
    entry_price=spot,
    candles=candles,
    ema50=ema50,
    gex_regime=gex_regime,
    account_equity=ACCOUNT_EQUITY,
)
print("\n--- TradePlan ---")
print(plan)

# ── Time stop sanity check ───────────────────────────────────────────
import datetime as _dt
now = _dt.datetime.now()
hit, reason = check_time_stop(now, candles_elapsed=0)
print(f"\ncheck_time_stop (0 candles elapsed): hit={hit} reason='{reason}'")

market_context.stop()
