"""
run_backtest.py

Replays archived option-premium candles through a REAL wave_extractor
instance to see what it would have done historically.

*** RUN THIS AS ITS OWN SEPARATE PROCESS ***
Never run this through PM2. Never import it from main.py. It is completely
isolated from your live bot -- it uses its own trade log DB and its own
risk-manager state file, so it can NEVER write to or corrupt your live
trading data. That isolation is set up at the very top of this file,
before wave_extractor (or anything it depends on) is imported.

Usage:
    python3 run_backtest.py --symbol "NSE_FO|45104"
    python3 run_backtest.py --symbol "NSE_FO|45104" --start "2026-08-13 09:15" --end "2026-08-13 15:30"
    python3 run_backtest.py --symbol "NSE_FO|45104" --fast   (skip real-time pacing)
"""
import argparse
import asyncio
import uuid
from pathlib import Path

RESEARCH_DB_PATH = "research_archive.db"

# ── STEP 1: isolate trade_logger and risk_manager BEFORE anything else ────
# imports them. This must happen first -- risk_manager reads its state file
# the moment it's constructed, which happens at import time.
import core.trade_log as _trade_log_module
import core.risk_manager as _risk_manager_module

BACKTEST_TRADE_DB = Path("backtest_trade_log.db")
BACKTEST_RISK_STATE = Path("configs/backtest_risk_state.json")

_trade_log_module.trade_logger.db_path = BACKTEST_TRADE_DB
_trade_log_module.trade_logger._init_db()   # create fresh tables in the new file
_trade_log_module.trade_logger._migrate_db()  # apply same column migrations live DB has
                                                # (readable_symbol, peak_pnl, trough_pnl,
                                                # entry_context, client_order_id) -- _init_db()
                                                # alone only has the original base schema.
_risk_manager_module.RISK_STATE_FILE = BACKTEST_RISK_STATE
# Force a clean slate for this run -- don't inherit any halt state at all
_risk_manager_module.risk_manager._system_halted = False
_risk_manager_module.risk_manager._halt_reason = ""
_risk_manager_module.risk_manager._trade_counts = {}
_risk_manager_module.risk_manager._daily_pnl = {}

# ── STEP 1b: force market_context into a tradeable regime ─────────────────
# market_context is a live-data-driven singleton (regime only updates from
# real NIFTY ticks, which this backtest never sends). Left alone it stays
# at its cold-start default (REGIME_CLOSED), which strategy_filter.can_trade()
# correctly refuses to trade in -- silently producing 0 orders regardless of
# the replayed option data. Safe to mutate here: run_backtest.py is always
# its own separate OS process, never sharing memory with the live bot.
# NOTE: this is a fixed approximation, not a historically accurate regime
# replay (real regime varies through the day and depends on OI data we
# don't archive yet) -- results reflect the strategy's option-price-driven
# logic, not regime-timing accuracy. See handoff notes.
import core.market_context as _market_context_module
# wave_extractor only trades REGIME_TRENDING_BULL / REGIME_TRENDING_BEAR
# (see core/strategy_filter.py STRATEGY_ALLOWED_REGIMES) -- NOT range.
_market_context_module.market_context._regime = _market_context_module.REGIME_TRENDING_BULL
# strategy_filter also hard-requires opening_range.is_ready (REQUIRE_OR_LOCKED=True),
# which needs locked=True + high/low set -- never happens in this fresh process
# since we never run real 9:15-9:30 opening-range collection.
_market_context_module.market_context._opening_range.locked = True
_market_context_module.market_context._opening_range.high = _market_context_module.market_context._opening_range.high or 24400.0
_market_context_module.market_context._opening_range.low  = _market_context_module.market_context._opening_range.low  or 24200.0
print("[run_backtest] market_context regime forced to REGIME_TRENDING_BULL + opening_range seeded (approximation, see docstring)")
_risk_manager_module.risk_manager._deployed_capital = {}

print(f"[run_backtest] Isolated: trades -> {BACKTEST_TRADE_DB}, risk state -> {BACKTEST_RISK_STATE}")

# ── STEP 2: now it's safe to import everything else ───────────────────────
from core.market_context import market_context
from strategy.wave_extractor import WaveExtractor, WaveConfig
from core.research.backtest_engine import (
    SimulatedBroker, CandleReplaySource, init_backtest_results_table, save_backtest_result
)


async def main():
    parser = argparse.ArgumentParser(description="Backtest wave_extractor against archived candles")
    parser.add_argument("--symbol", required=True, help="Option symbol, e.g. 'NSE_FO|45104'")
    parser.add_argument("--start", default=None, help="Start timestamp, e.g. '2026-08-13 09:15'")
    parser.add_argument("--end", default=None, help="End timestamp, e.g. '2026-08-13 15:30'")
    parser.add_argument("--fast", action="store_true", help="Skip real-time pacing, replay as fast as possible")
    args = parser.parse_args()

    init_backtest_results_table(RESEARCH_DB_PATH)

    broker = SimulatedBroker()
    market_context.set_broker(broker)

    replay = CandleReplaySource(
        db_path=RESEARCH_DB_PATH,
        symbol=args.symbol,
        broker=broker,
        start_ts=args.start,
        end_ts=args.end,
        real_time_pace=not args.fast,
    )

    # Seed a starting price BEFORE the strategy starts, so its first
    # get_ltp() call doesn't come back as 0.0
    first_candles = replay._load_candles()
    if not first_candles:
        print(f"[run_backtest] No archived candles found for symbol '{args.symbol}'. Nothing to replay.")
        return
    broker.set_last_price(args.symbol, first_candles[0][1])  # first candle's open price

    config = WaveConfig(option_symbol=args.symbol)
    wave = WaveExtractor(broker=broker, config=config)

    print(f"[run_backtest] Starting wave_extractor against {args.symbol}...")
    await wave.start()

    candles_replayed = await replay.run()

    print("[run_backtest] Replay finished. Stopping strategy...")
    await wave.stop(reason="BACKTEST_COMPLETE")

    # Paper-mode trades never touch SimulatedBroker (wave_extractor logs
    # [PAPER] fills and writes straight to trade_logger, bypassing
    # broker.place_order() entirely) -- broker._orders/get_positions() stay
    # empty regardless of how many trades actually happened. Read the real
    # numbers from trade_logger's own DB instead, which both paper and live
    # order paths write to consistently.
    summary = _trade_log_module.trade_logger.get_pnl_summary(
        strategy="wave_extractor", today_only=False
    )
    total_trades = summary.get("total_trades", 0)
    realised_pnl = summary.get("total_pnl", 0.0)

    run_id = f"BT_{uuid.uuid4().hex[:10]}"
    save_backtest_result(
        RESEARCH_DB_PATH, run_id=run_id, strategy="wave_extractor", symbol=args.symbol,
        start_ts=args.start, end_ts=args.end, candles_replayed=candles_replayed,
        total_trades=total_trades, realised_pnl=realised_pnl,
    )

    print(f"\n=== Backtest Summary ({run_id}) ===")
    print(f"Symbol:            {args.symbol}")
    print(f"Candles replayed:  {candles_replayed}")
    print(f"Orders placed:     {total_trades}")
    print(f"Simulated P&L:     {realised_pnl:+.2f}")
    print(f"Saved to research_archive.db -> backtest_runs (run_id={run_id})")


if __name__ == "__main__":
    asyncio.run(main())
