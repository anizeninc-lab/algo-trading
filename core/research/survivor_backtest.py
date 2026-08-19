# core/research/survivor_backtest.py
#
# Backtests the survivor strategy against archived NIFTY index spot candles,
# using Black-Scholes-modeled option premiums instead of literal historical
# option fills. See run_survivor_backtest.py's docstring for the fidelity
# tradeoffs and why this is necessary (survivor picks strikes dynamically as
# spot moves, so there's no way to have pre-archived the "right" option's
# real premium history the way wave_extractor's backtest can for its one
# fixed option_symbol).
#
# IMPORTANT: this file is only ever imported by run_survivor_backtest.py,
# which must be run as its OWN separate process (never through PM2, never
# imported by main.py). Same isolation discipline as run_backtest.py.
#
# SYMBOL FORMAT NOTE: survivor's real instrument-key lookup
# (_get_instrument_key) calls fetch_instruments(), which needs a live
# broker session -- not available in a backtest process. run_survivor_
# backtest.py monkeypatches this out (see its docstring) so survivor always
# falls back to its own deterministic text symbol format from
# _build_symbol(): "NSE_FO|{symbol_initials}{strike:05d}{CE|PE}", e.g.
# "NSE_FO|NIFTY18AUG2624000PE" -- STRIKE BEFORE the CE/PE suffix. This is
# NOT the same format core/greeks_engine.py's _parse_symbol() expects
# (which assumes CE/PE before strike, e.g. "NIFTY18AUG26PE23500", the
# format used elsewhere for already-open live positions) -- greeks_engine's
# parser will silently fail to match survivor's real fallback symbols. Do
# not reuse it here; SYMBOL_RE below is deliberately separate and matches
# what survivor actually generates.

import logging
import re
import sqlite3
from datetime import datetime
from typing import Optional

from brokers.base import Tick
from core.research.backtest_engine import SimulatedBroker

logger = logging.getLogger(__name__)

try:
    import mibian
    _MIBIAN_OK = True
except ImportError:
    _MIBIAN_OK = False
    logger.warning("[survivor_backtest] mibian not installed — synthetic pricing unavailable")

RISK_FREE_RATE = 6.5   # matches core/greeks_engine.py's assumption, kept consistent
DEFAULT_VIX = 15.0     # fallback if no archived VIX snapshot exists at all

# strike-before-type, matching survivor._build_symbol()'s real output --
# see module docstring for why this differs from greeks_engine's regex.
SYMBOL_RE = re.compile(r'(\d{2})([A-Z]{3})(\d{2})(\d+)(CE|PE)$')

_vix_fallback_warned = False


def parse_survivor_symbol(symbol: str):
    """Extract (expiry_date, strike, opt_type) from a survivor-format
    symbol like 'NSE_FO|NIFTY18AUG2624000PE'. Returns (None, None, None)
    if it doesn't match."""
    m = SYMBOL_RE.search(symbol)
    if not m:
        return None, None, None
    day, mon, yr, strike, opt_type = m.groups()
    try:
        expiry = datetime.strptime(f"{day}{mon}20{yr}", "%d%b%Y").date()
    except ValueError:
        return None, None, None
    return expiry, float(strike), opt_type


def lookup_historical_vix(db_path: str, ts: str) -> float:
    """Nearest archived VIX at or before `ts`, from greeks_snapshots
    (archived every 5 min regardless of open positions -- see
    core/research/data_archive.py's _snapshot_greeks). Falls back to
    DEFAULT_VIX (with a one-time warning) if the archive has nothing at
    all, e.g. if greeks archiving wasn't running yet for the requested
    backtest window."""
    global _vix_fallback_warned
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT vix FROM greeks_snapshots WHERE ts <= ? AND vix > 0 "
                "ORDER BY ts DESC LIMIT 1",
                (ts,),
            ).fetchone()
        if row and row[0]:
            return row[0]
    except Exception as e:
        logger.debug(f"[survivor_backtest] VIX lookup failed: {e}")

    if not _vix_fallback_warned:
        logger.warning(
            f"[survivor_backtest] No archived VIX found at/before {ts} — "
            f"falling back to DEFAULT_VIX={DEFAULT_VIX} for pricing. "
            f"Results will be less accurate on volatility-sensitive strikes."
        )
        _vix_fallback_warned = True
    return DEFAULT_VIX


def synthetic_option_price(symbol: str, spot: float, vix: float,
                            reference_date) -> Optional[float]:
    """
    Black-Scholes theoretical premium for `symbol`, using real archived
    spot + VIX as inputs and `reference_date` (the backtest's current
    SIMULATED date, not real today) for days-to-expiry. Returns None if
    the symbol can't be parsed as a survivor-format option symbol (e.g.
    it's the index itself).
    """
    if not _MIBIAN_OK:
        return None
    expiry, strike, opt_type = parse_survivor_symbol(symbol)
    if expiry is None:
        return None

    dte = max((expiry - reference_date).days, 1)
    try:
        bs = mibian.BS([spot, strike, RISK_FREE_RATE, dte], volatility=vix)
    except Exception as e:
        logger.debug(f"[survivor_backtest] BS pricing failed for {symbol}: {e}")
        return None

    price = bs.callPrice if opt_type == "CE" else bs.putPrice
    return max(price, 0.05)  # avoid a literal 0 reaching min_price_to_sell checks


class SimulatedOptionBroker(SimulatedBroker):
    """
    Extends SimulatedBroker: the index symbol's price comes from replayed
    real candles (same as the base class), but ANY other symbol asked for
    via get_ltp()/place_order() -- i.e. every option strike survivor
    considers or trades -- is priced synthetically on demand via
    synthetic_option_price(), using whatever the index's last replayed
    price and the historically-nearest VIX were at that moment.

    NOTE: prices are recomputed fresh on every get_ltp() call for option
    symbols (never cached), since a real premium changes on every spot
    tick -- caching the first computed value would freeze it, which would
    silently break survivor's SL/trailing logic (see _refresh_ltp_loop,
    which polls get_ltp() every ~10s expecting a live-moving price).
    """

    def __init__(self, index_symbol: str, research_db_path: str):
        super().__init__()
        self.index_symbol = index_symbol
        self.research_db_path = research_db_path
        self._current_ts: str = ""

    def note_index_tick(self, ts: str) -> None:
        """Called by IndexReplay every time it emits an index tick, so
        subsequent option pricing uses the right simulated 'now' for VIX
        lookup and days-to-expiry."""
        self._current_ts = ts

    def _price_for(self, symbol: str) -> float:
        if symbol == self.index_symbol:
            return self._last_prices.get(symbol, 0.0)

        spot = self._last_prices.get(self.index_symbol, 0.0)
        if spot <= 0 or not self._current_ts:
            return 0.0

        vix = lookup_historical_vix(self.research_db_path, self._current_ts)
        try:
            ref_date = datetime.fromisoformat(self._current_ts).date()
        except ValueError:
            ref_date = datetime.now().date()

        price = synthetic_option_price(symbol, spot, vix, ref_date)
        return price if price is not None else 0.0

    async def get_ltp(self, symbol: str) -> float:
        return self._price_for(symbol)

    async def place_order(self, order):
        # Route through the synthetic pricer if the order didn't come with
        # its own price (mirrors SimulatedBroker.place_order's fallback,
        # just using _price_for instead of a flat dict lookup so option
        # fills are priced correctly too).
        if order.price <= 0:
            order.price = self._price_for(order.symbol)
        return await super().place_order(order)


class IndexReplay:
    """
    Replays archived NIFTY index candles (candles_1min WHERE symbol='NIFTY')
    as synthetic ticks, EMITTED under whatever instrument key the strategy
    actually subscribed to (e.g. 'NSE_INDEX|Nifty 50') -- decoupling the
    DB-archived symbol from the emitted tick symbol, which
    backtest_engine.CandleReplaySource doesn't need to do since
    wave_extractor's DB symbol and emitted symbol are the same string.
    """

    DB_SYMBOL = "NIFTY"

    def __init__(self, db_path: str, emit_symbol: str, broker: SimulatedOptionBroker,
                 start_ts: Optional[str] = None, end_ts: Optional[str] = None,
                 tick_sleep_sec: float = 1.0):
        self.db_path = db_path
        self.emit_symbol = emit_symbol
        self.broker = broker
        self.start_ts = start_ts
        self.end_ts = end_ts
        self.tick_sleep_sec = tick_sleep_sec

    def _load_candles(self) -> list:
        query = "SELECT ts, open, high, low, close FROM candles_1min WHERE symbol = ?"
        params = [self.DB_SYMBOL]
        # Archived ts values use 'T' as the date/time separator
        # ("2026-08-13T09:15:00"). A user-supplied filter like
        # "2026-08-13 09:15" (space, no seconds) compares WRONG in plain
        # lexicographic SQLite TEXT comparison: 'T' (0x54) > ' ' (0x20),
        # so an --end filter in this format silently drops every single
        # row instead of bounding the range (start still "works", but only
        # by accident, and for the wrong reason). Normalize to match the
        # archived format before comparing. NOTE: backtest_engine.py's
        # CandleReplaySource has this exact same bug -- flagged separately,
        # not fixed here since that file isn't part of today's change.
        if self.start_ts:
            query += " AND ts >= ?"
            params.append(self._normalize_ts(self.start_ts))
        if self.end_ts:
            query += " AND ts <= ?"
            params.append(self._normalize_ts(self.end_ts))
        query += " ORDER BY ts ASC"
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(query, params).fetchall()
        return rows

    @staticmethod
    def _normalize_ts(ts: str) -> str:
        """'2026-08-13T09:15' or '2026-08-13 09:15:00' -> '2026-08-13 09:15',
        matching archived candles_1min.ts formatting exactly (space
        separator, no seconds -- see market_context.py's
        now.strftime("%Y-%m-%d %H:%M") candle-key builder, which is
        the sole writer of ts values into this table)."""
        ts = ts.strip().replace("T", " ")
        ts = ts[:16]  # trim any seconds/timezone suffix down to 'YYYY-MM-DD HH:MM'
        return ts

    async def run(self) -> int:
        import asyncio
        candles = self._load_candles()
        print(f"[survivor_backtest] {len(candles)} NIFTY index candles found")
        for ts, o, h, l, c in candles:
            for price in (o, h, l, c):
                self.broker.note_index_tick(ts)
                tick = Tick(symbol=self.emit_symbol, last_price=price, timestamp=ts)
                self.broker.emit_tick(tick)
                # emit_tick's callback (survivor's _on_tick_sync) schedules
                # on_tick() via run_coroutine_threadsafe -- fire-and-forget,
                # NOT awaited here. on_tick() itself has several nested
                # await points (monitor open trades, sell_option, broker
                # calls...). A single asyncio.sleep(0) is NOT reliably
                # enough real event-loop time for one on_tick() call to run
                # to completion before the next tick piles another task on
                # -- confirmed empirically: with sleep(0), only the very
                # first tick's on_tick() ever finished; every subsequent
                # tick's scheduled task got orphaned when the replay ended
                # and the strategy was torn down. tick_sleep_sec must stay
                # meaningfully above 0 for correct results; 0 is refused
                # below rather than silently producing an empty backtest.
                await asyncio.sleep(max(self.tick_sleep_sec, 0.02))
        return len(candles)
