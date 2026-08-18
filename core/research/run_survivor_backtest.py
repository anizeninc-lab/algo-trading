"""
run_survivor_backtest.py

Replays archived NIFTY index spot candles through a REAL survivor
(SurvivorAlgo) instance to see what it would have done historically.
Unlike run_backtest.py (wave_extractor), this does NOT replay literal
historical option premiums -- survivor picks strikes dynamically as spot
moves, so there's no way to have pre-archived the "right" option's real
premium history. Instead, option premiums are computed with Black-Scholes
(mibian) from real archived spot + real archived VIX -- see
core/research/survivor_backtest.py's docstring for the full reasoning.

THIS IS A LOWER-FIDELITY BACKTEST THAN run_backtest.py's. It validates
survivor's entry/exit DECISION LOGIC against real historical spot
movement, and gives a directional P&L shape -- it does NOT capture real
bid-ask spread, liquidity, or slippage the way literal archived premiums
would. Treat results as "would this logic have fired, roughly how would
it have done" rather than "this is what really would have happened."

*** RUN THIS AS ITS OWN SEPARATE PROCESS ***
Never run this through PM2. Never import it from main.py. It is completely
isolated from your live bot -- it uses its own trade log DB and its own
risk-manager state file, so it can NEVER write to or corrupt your live
trading data. That isolation is set up at the very top of this file,
before survivor (or anything it depends on) is imported. Same pattern as
run_backtest.py.

INSTRUMENT-KEY LOOKUP IS DISABLED FOR THIS RUN: survivor's real strike
lookup (_get_instrument_key) calls fetch_instruments(), which needs a live
broker session your backtest process doesn't have (and shouldn't need --
a backtest should never touch your real broker). This script monkeypatches
_get_instrument_key to always return the deterministic text symbol
(_build_symbol()'s output) instead, which is exactly the format
core/research/survivor_backtest.py's pricer expects.

Usage:
    python3 run_survivor_backtest.py --start "2026-08-01 09:15" --end "2026-08-14 15:30"
    python3 run_survivor_backtest.py --start "2026-08-13 09:15" --end "2026-08-13 15:30" --fast
    python3 run_survivor_backtest.py --pe-quantity 0   # test the actual pending candidate proposal
"""
import argparse
import asyncio
import uuid
from pathlib import Path

RESEARCH_DB_PATH = "research_archive.db"

# ── STEP 1: isolate trade_logger and risk_manager BEFORE anything else ────
# imports them. Identical pattern to run_backtest.py -- see that file's
# comments for why this ordering matters.
import core.trade_log as _trade_log_module
import core.risk_manager as _risk_manager_module

BACKTEST_TRADE_DB = Path("backtest_survivor_trade_log.db")
BACKTEST_RISK_STATE = Path("configs/backtest_survivor_risk_state.json")

_trade_log_module.trade_logger.db_path = BACKTEST_TRADE_DB
_trade_log_module.trade_logger._init_db()
_trade_log_module.trade_logger._migrate_db()
_risk_manager_module.RISK_STATE_FILE = BACKTEST_RISK_STATE
_risk_manager_module.risk_manager._system_halted = False
_risk_manager_module.risk_manager._halt_reason = ""
_risk_manager_module.risk_manager._trade_counts = {}
_risk_manager_module.risk_manager._daily_pnl = {}
_risk_manager_module.risk_manager._deployed_capital = {}

# ── STEP 1b: force market_context into a tradeable regime ─────────────────
# survivor only trades REGIME_RANGE / REGIME_REVERSAL_WATCH (see
# core/strategy_filter.py STRATEGY_ALLOWED_REGIMES["survivor"]) -- NOT
# trending, which is what run_backtest.py forces for wave_extractor.
# Same caveat as run_backtest.py: this is a fixed approximation, not a
# historically accurate regime replay (real regime depends on live OI/PCR
# data we don't archive) -- results reflect survivor's spot-driven entry
# logic under an assumed-ranging market, not regime-timing accuracy.
import core.market_context as _market_context_module
_market_context_module.market_context._regime = _market_context_module.REGIME_RANGE
_market_context_module.market_context._opening_range.locked = True
_market_context_module.market_context._opening_range.high = _market_context_module.market_context._opening_range.high or 24400.0
_market_context_module.market_context._opening_range.low  = _market_context_module.market_context._opening_range.low  or 24200.0
print("[run_survivor_backtest] market_context regime forced to REGIME_RANGE + opening_range seeded (approximation, see docstring)")

# ── STEP 1c: neutralize real-wall-clock gates ──────────────────────────────
# Two separate checks in the live code path compare datetime.now() against
# real market hours -- correct for live trading, but they don't know or
# care that this is a backtest replaying a different (often past) date, so
# left alone they gate/kill the run based on whatever real time of day you
# happen to launch this script, not the simulated candle timestamps being
# replayed. Both found by actually running this against archived data and
# getting silent zero-trade / early-stop results -- not obvious from
# reading the strategy code alone.
#   1. market_context.trade_allowed (a property) blocks ALL entries outside
#      real 9:30-15:10 IST -- patched to always allow.
#   2. risk_manager.check_auto_stop() (real-time EOD check, polled every 3s
#      by survivor's _refresh_ltp_loop) would force-stop the strategy ~3
#      real seconds into ANY run launched after real 15:05 IST, regardless
#      of simulated progress -- patched to never fire; we force-close and
#      stop the strategy explicitly ourselves at the end of the replay
#      instead (see main()).
type(_market_context_module.market_context).trade_allowed = property(lambda self: True)
_risk_manager_module.risk_manager.check_auto_stop = lambda: False
print("[run_survivor_backtest] Patched market_context.trade_allowed and "
      "risk_manager.check_auto_stop to ignore real wall-clock time (approximation, see docstring)")

print(f"[run_survivor_backtest] Isolated: trades -> {BACKTEST_TRADE_DB}, risk state -> {BACKTEST_RISK_STATE}")

# ── STEP 2: now it's safe to import everything else ───────────────────────
from core.market_context import market_context
from strategy.survivor import SurvivorAlgo, SurvivorConfig
from core.research.backtest_engine import init_backtest_results_table, save_backtest_result
from core.research.survivor_backtest import SimulatedOptionBroker, IndexReplay


def _load_live_survivor_defaults() -> dict:
    """Mirrors main.py's Nifty survivor_cfg construction: reads
    configs/saviour_combo.json the same way, falling back to the same
    literal defaults main.py uses if a key is missing."""
    import json
    cfg_path = Path("configs/saviour_combo.json")
    live = {}
    if cfg_path.exists():
        with open(cfg_path) as f:
            live = json.load(f)
    return dict(
        symbol_initials=live.get("symbol_initials", "NIFTY26MAR25"),
        pe_gap=live.get("pe_gap", 15.0),
        ce_gap=live.get("ce_gap", 15.0),
        pe_symbol_gap=live.get("pe_symbol_gap", 300.0),
        ce_symbol_gap=live.get("ce_symbol_gap", 300.0),
        pe_reset_gap=live.get("pe_reset_gap", 90.0),
        ce_reset_gap=live.get("ce_reset_gap", 90.0),
        pe_quantity=live.get("pe_quantity", 65),
        ce_quantity=live.get("ce_quantity", 65),
        pe_start=live.get("pe_start", 0.0),
        ce_start=live.get("ce_start", 0.0),
        min_price_to_sell=live.get("min_price_to_sell", 15.0),
    )


async def main():
    parser = argparse.ArgumentParser(description="Backtest survivor against archived NIFTY spot + synthetic BS premiums")
    parser.add_argument("--start", default=None, help="Start timestamp, e.g. '2026-08-13 09:15'")
    parser.add_argument("--end", default=None, help="End timestamp, e.g. '2026-08-13 15:30'")
    parser.add_argument("--tick-sleep", type=float, default=1.0,
                         help="Real seconds slept per replayed tick (4 ticks/candle). "
                              "Default 1.0 -> ~4 real sec/simulated minute, ~25 real min/session. "
                              "0 = as fast as possible, BUT survivor's own SL/trailing monitor "
                              "(_refresh_ltp_loop) is wall-clock-throttled every ~3s/10s, so very "
                              "fast replay under-samples those checks -- see docstring caveats.")
    parser.add_argument("--fast", action="store_true", help="Shortcut for --tick-sleep 0")
    parser.add_argument("--pe-quantity", type=int, default=None,
                         help="Override pe_quantity for this run, e.g. to test a candidate "
                              "proposal (pass 0 to test disabling the PE side entirely)")
    parser.add_argument("--ce-quantity", type=int, default=None, help="Override ce_quantity for this run")
    args = parser.parse_args()
    tick_sleep = 0.0 if args.fast else args.tick_sleep

    init_backtest_results_table(RESEARCH_DB_PATH)

    index_symbol = "NSE_INDEX|Nifty 50"
    broker = SimulatedOptionBroker(index_symbol=index_symbol, research_db_path=RESEARCH_DB_PATH)
    market_context.set_broker(broker)

    replay = IndexReplay(
        db_path=RESEARCH_DB_PATH,
        emit_symbol=index_symbol,
        broker=broker,
        start_ts=args.start,
        end_ts=args.end,
        tick_sleep_sec=tick_sleep,
    )

    first_candles = replay._load_candles()
    if not first_candles:
        print("[run_survivor_backtest] No archived NIFTY index candles found for this window. Nothing to replay.")
        return
    # Seed a starting spot price BEFORE the strategy starts, so on_start()'s
    # get_ltp() call doesn't come back as 0.0 (which raises RuntimeError).
    broker.set_last_price(index_symbol, first_candles[0][1])
    broker.note_index_tick(first_candles[0][0])

    live_defaults = _load_live_survivor_defaults()
    if args.pe_quantity is not None:
        live_defaults["pe_quantity"] = args.pe_quantity
    if args.ce_quantity is not None:
        live_defaults["ce_quantity"] = args.ce_quantity

    config = SurvivorConfig(paper_trade_override=True, **live_defaults)
    survivor = SurvivorAlgo(broker=broker, config=config)

    # Disable real broker instrument-key lookup -- see module docstring.
    # Always use the deterministic text symbol survivor_backtest.py's
    # pricer expects, never a real (and here, unreachable) broker session.
    async def _fallback_instrument_key(symbol: str, direction: str, strike: float) -> str:
        return survivor._build_symbol(direction, strike)
    survivor._get_instrument_key = _fallback_instrument_key

    # is_market_open() (from BaseStrategy) checks REAL current weekday/time
    # -- e.g. if you run this backtest on a real Saturday, it returns False
    # on every single tick regardless of what historical date is being
    # replayed, silently producing zero trades. Patched to always allow;
    # the replay's own start/end bounds are what actually constrain the
    # simulated trading window.
    survivor.is_market_open = lambda: True

    print(f"[run_survivor_backtest] Starting survivor "
          f"(pe_quantity={config.pe_quantity}, ce_quantity={config.ce_quantity})...")
    await survivor.start()

    candles_replayed = await replay.run()

    print("[run_survivor_backtest] Replay finished. Force-closing any open positions "
          "(survivor.on_stop()'s EOD check uses REAL wall-clock time, not simulated "
          "backtest time, so we close explicitly here instead of relying on it)...")
    await survivor._close_all_positions(reason="BACKTEST_COMPLETE")
    await survivor.stop(reason="BACKTEST_COMPLETE")

    summary = _trade_log_module.trade_logger.get_pnl_summary(
        strategy="survivor", today_only=False
    )
    total_trades = summary.get("total_trades", 0)
    realised_pnl = summary.get("total_pnl", 0.0)

    run_id = f"BT_SURV_{uuid.uuid4().hex[:10]}"
    save_backtest_result(
        RESEARCH_DB_PATH, run_id=run_id, strategy="survivor", symbol=index_symbol,
        start_ts=args.start, end_ts=args.end, candles_replayed=candles_replayed,
        total_trades=total_trades, realised_pnl=realised_pnl,
    )

    print(f"\n=== Survivor Backtest Summary ({run_id}) ===")
    print(f"pe_quantity:       {config.pe_quantity}   ce_quantity: {config.ce_quantity}")
    print(f"Candles replayed:  {candles_replayed}")
    print(f"Trades:            {total_trades}")
    print(f"Simulated P&L:     {realised_pnl:+.2f}")
    print(f"(Approximate -- Black-Scholes synthetic premiums, not literal historical fills. "
          f"See run_survivor_backtest.py docstring.)")
    print(f"Saved to research_archive.db -> backtest_runs (run_id={run_id})")
    print(f"Trade-level detail: {BACKTEST_TRADE_DB}")


if __name__ == "__main__":
    asyncio.run(main())