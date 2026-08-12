from dotenv import load_dotenv
load_dotenv()

import time
import os
from core.market_context import market_context
from core.gex_ema_stack import get_multi_timeframe_stack, seed_historical_15min_candles

print("Starting market_context (this subscribes to live NIFTY ticks)...")
market_context.start()

print("Waiting 30 seconds to let some ticks accumulate...")
time.sleep(30)

candles = market_context.session_candles_1min
print(f"Session 1-min candles so far: {len(candles)}")

print("Fetching historical 15-min seed...")
seed = seed_historical_15min_candles(os.getenv("UPSTOX_ACCESS_TOKEN"))
print(f"Historical 15-min candles seeded: {len(seed)}")

result = get_multi_timeframe_stack(candles, seed)
print()
print("5-min result:", result.tf_5min)
print("15-min result:", result.tf_15min)
print("Agreement:", result.agreement, "| Direction:", result.direction)

market_context.stop()
