"""
backfill_banknifty_candles.py

One-time (or re-runnable) bulk backfill of real historical BankNifty index
1-minute candles into research_archive.db's candles_1min table, symbol =
'BANKNIFTY'.

WHY THIS EXISTS (2026-09-12): found live this week that bn_survivor's
min_regime_stability entry filter has never actually evaluated BankNifty's
own market behavior -- core/market_context.py's _classify_regime() only
ever fetches NIFTY spot (self._fetch_nifty_spot()) and feeds it to the one
shared regime_engine singleton. bn_survivor's regime gate has always been
driven by NIFTY's mood, not BankNifty's -- a real architecture gap, not a
mis-tuned number. This script is step 1 of fixing that properly: get real
historical BankNifty price data archived so a BankNifty-specific regime
threshold can eventually be honestly backtested and validated, the same
rigorous way survivor's min_regime_stability=65.0 was (see LESSON-002 and
candidate 795b6591).

candles_1min already stores NIFTY the same way (see run_survivor_backtest.py
/ core/research/survivor_backtest.py, which already replay it) -- this just
adds a second symbol to the same table, so the existing backtest harness
can eventually be pointed at either index with minimal change.

IMPORTANT CAVEAT, not yet resolved: this uses Upstox's v3 historical candle
API (HistoryV3Api.get_historical_candle_data1), which is NOT the same
endpoint as fetch_intraday_candles() in core/regime_engine.py (that one
only returns the CURRENT day's candles, no date range). The v3 endpoint's
exact accepted `unit`/`interval` values and any per-request date-range
limit were NOT verified against a live response before writing this --
Upstox's daily access token had already expired for the weekend
(auto_token.py's refresh cron only runs on trading days) when this was
written, so there was no way to test it live. Written using the
documented convention (unit="minutes", interval=1) and chunked into
weekly windows defensively, in case the API rejects wide date ranges in
one call -- but the exact failure mode, if any, is UNCONFIRMED. Run this
for real on Monday once the token refreshes, read whatever error Upstox
actually returns if it fails, and adjust CHUNK_DAYS / unit / interval
based on that real response rather than further guessing.

Usage:
    python3 backfill_banknifty_candles.py --days 30
    python3 backfill_banknifty_candles.py --days 7 --dry-run
"""
import argparse
import os
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path

import upstox_client
from dotenv import load_dotenv

load_dotenv()

DB_PATH = Path(__file__).parent / "research_archive.db"
INSTRUMENT_KEY = "NSE_INDEX|Nifty Bank"
SYMBOL = "BANKNIFTY"
CHUNK_DAYS = 7  # defensive weekly chunking -- UNCONFIRMED whether Upstox's
                # v3 endpoint actually needs this or can take a wider range
                # in one call. Safe either way, just possibly more requests
                # than strictly necessary.


def _get_api() -> "upstox_client.HistoryV3Api":
    cfg = upstox_client.Configuration()
    cfg.access_token = os.getenv("UPSTOX_ACCESS_TOKEN")
    return upstox_client.HistoryV3Api(upstox_client.ApiClient(cfg))


def _fetch_chunk(api, from_date: date, to_date: date) -> list:
    """
    Returns a list of (ts, open, high, low, close) tuples for one date-range
    chunk, oldest first. Empty list on any failure (logged, not raised) --
    caller should treat a failed chunk as "no data for this window" and
    keep going, same defensive posture as fetch_intraday_candles() in
    core/regime_engine.py.
    """
    try:
        resp = api.get_historical_candle_data1(
            instrument_key=INSTRUMENT_KEY,
            unit="minutes",
            interval=1,
            to_date=to_date.isoformat(),
            from_date=from_date.isoformat(),
        )
        raw = resp.data.candles if resp and resp.data else []
        # Same convention as fetch_intraday_candles(): API returns newest
        # first, reverse to oldest first for consistent replay order.
        return [
            (c[0], float(c[1]), float(c[2]), float(c[3]), float(c[4]))
            for c in reversed(raw)
        ]
    except Exception as e:
        print(f"[backfill] Chunk {from_date} to {to_date} FAILED: {e}")
        return []


def backfill(days: int, dry_run: bool = False) -> int:
    api = _get_api()
    today = date.today()
    start = today - timedelta(days=days)

    all_rows = []
    chunk_start = start
    while chunk_start <= today:
        chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS - 1), today)
        print(f"[backfill] Fetching {chunk_start} to {chunk_end}...")
        rows = _fetch_chunk(api, chunk_start, chunk_end)
        print(f"[backfill]   -> {len(rows)} candles")
        all_rows.extend(rows)
        chunk_start = chunk_end + timedelta(days=1)
        time.sleep(0.5)  # light self-throttling, not tied to UpstoxAdapter's
                          # own _throttle() since this is a standalone script

    print(f"[backfill] Total fetched: {len(all_rows)} candles")

    if dry_run:
        print("[backfill] --dry-run set, not writing to database.")
        if all_rows:
            print(f"[backfill] Sample row: {all_rows[0]}")
        return len(all_rows)

    if not all_rows:
        print("[backfill] Nothing to write.")
        return 0

    with sqlite3.connect(DB_PATH) as conn:
        before = conn.execute(
            "SELECT COUNT(*) FROM candles_1min WHERE symbol = ?", (SYMBOL,)
        ).fetchone()[0]
        conn.executemany(
            "INSERT OR IGNORE INTO candles_1min (ts, symbol, open, high, low, close) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(ts, SYMBOL, o, h, l, c) for ts, o, h, l, c in all_rows],
        )
        conn.commit()
        after = conn.execute(
            "SELECT COUNT(*) FROM candles_1min WHERE symbol = ?", (SYMBOL,)
        ).fetchone()[0]

    print(f"[backfill] {SYMBOL} rows in candles_1min: {before} -> {after} "
          f"({after - before} new, rest were duplicates or already present)")
    return after - before


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill historical BankNifty 1-min candles")
    parser.add_argument("--days", type=int, default=30, help="How many days back to fetch")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and print, but don't write to the database")
    args = parser.parse_args()
    backfill(args.days, dry_run=args.dry_run)
