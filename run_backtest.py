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
_risk_manager_module.RISK_STATE_FILE = BACKTEST_RISK_STATE
# Force a clean slate for this run -- don't inherit any halt state at all
_risk_manager_module.risk_manager._system_halted = False
_risk_manager_module.risk_manager._halt_reason = ""
_risk_manager_module.risk_manager._trade_counts = {}
_risk_manager_module.risk_manager._daily_pnl = {}
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

    positions = await broker.get_positions()
    total_trades = len(broker._orders)
    realised_pnl = sum(p.pnl for p in positions)

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
