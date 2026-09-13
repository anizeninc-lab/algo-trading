"""
core/banknifty_regime_feed.py

Gives bn_survivor a real, independent BankNifty regime reading instead of
silently reusing NIFTY's (see LESSONS.md LESSON-F7 / HANDOFF 2026-09-12
for the full root-cause writeup: core/market_context.py's
_classify_regime() has only ever fetched NIFTY spot and fed it to the one
shared regime_engine singleton, so bn_survivor's min_regime_stability gate
has never, in its whole history, evaluated BankNifty's own market
behavior).

WHY THIS IS A SEPARATE MODULE, NOT A REWRITE OF market_context.py:
core/market_context.py is deeply entangled with the live tick-subscription
pipeline, opening-range tracking, real option-chain OI/PCR, previous-day
levels, and the dashboard/state-store surface -- all built around one
NIFTY-only instance. The 2026-09-12 session deliberately deferred
touching that file, in these words: "rushing this on a weekend with no
way to test against live ticks (market closed) was a deliberate decision
not to do, not an oversight. This needs its own properly-scoped session,
ideally starting on a day with live market data to actually verify
against." That reasoning still holds -- this module respects it by
staying completely isolated: it does not import, subclass, or modify
MarketContextEngine or the shared `market_context` singleton in any way.
Zero shared state, zero blast radius to the NIFTY path.

WHAT THIS MODULE DOES, DELIBERATELY SIMPLIFIED VS NIFTY'S VERSION:
- Polls BankNifty spot via REST (mirrors _fetch_nifty_spot()'s pattern,
  same instrument key convention as backfill_banknifty_candles.py:
  "NSE_INDEX|Nifty Bank"), and self-aggregates 1-minute OHLC candles from
  those polls -- it does NOT subscribe to live BankNifty ticks. Simpler,
  safer to reason about, and avoids touching the live tick pipeline at
  all, at the cost of slightly coarser candles than a true tick feed.
- OI/PCR-derived regime signals (pcr, ce_oi_delta, pe_oi_delta, pcr_spike,
  the pe/ce migration flags) are passed as neutral defaults -- there is
  no BankNifty option-chain OI pipeline wired up yet. This is the same
  category of approximation already accepted and documented in
  run_survivor_backtest.py's IndexReplay for backtesting; it is NOT a
  hidden simplification, it is a known, flagged gap. Building a real
  BankNifty OI/PCR feed is future work, not done here.
- Opening range (or_high/or_low) is tracked as a simple running
  high/low over the polls collected between market open and OR_END,
  not NIFTY's more elaborate opening_range object.

STATUS AS OF 2026-09-13: written and unit-testable (classify() itself is
pure, given candles), but NOT YET VERIFIED against a live BankNifty feed
or a live Upstox token -- written without one, same caveat as
backfill_banknifty_candles.py. Per the explicit rule in LESSONS.md /
HANDOFF: do NOT wire this into bn_survivor's live min_regime_stability
gate, and do NOT trust any BankNifty-specific threshold derived from it,
until it has been (a) run live for real against actual BankNifty data
and sanity-checked, and (b) ideally replayed against
backfill_banknifty_candles.py's archived history through the (already
regime-aware, as of 2026-09-12) backtest harness for an honest gate test.
Wiring it into survivor.py's gate checks (see accompanying patch) is
gated behind an ENABLE_BANKNIFTY_OWN_REGIME feature flag, default OFF,
for exactly this reason -- so this can be committed and reviewed without
silently changing bn_survivor's live behavior the moment it's deployed.
"""
import os
import threading
import time
from collections import deque
from datetime import datetime, date
from typing import Optional

import requests
import pytz

from core.regime_engine import regime_engine_banknifty, Candle

IST = pytz.timezone("Asia/Kolkata")
BANKNIFTY_INSTRUMENT_KEY = "NSE_INDEX|Nifty Bank"
POLL_INTERVAL_SEC = 15          # how often we hit the REST quote endpoint
CANDLE_BUCKET_SECONDS = 60      # 1-minute candles, aggregated from polls
MAX_SESSION_CANDLES = 500       # plenty for a full trading day at 1min
OR_END_MINUTE = (9, 30)         # same convention as market_context's OR window


class BankNiftyRegimeFeed:
    """
    Minimal, independent regime feed for BankNifty. Mirrors just enough of
    MarketContextEngine's public interface (`.regime`, `register_regime_
    callback`, `.start()`/`.stop()`) that survivor.py's bn_survivor path
    can query it the same way it queries `market_context` today -- without
    either object knowing about the other.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._regime: str = "closed"
        self._regime_change_callbacks = []
        self._stop_flag = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._session_candles: deque = deque(maxlen=MAX_SESSION_CANDLES)
        self._current_bucket_minute: Optional[int] = None
        self._current_bucket: dict = {}
        self._or_high: Optional[float] = None
        self._or_low: Optional[float] = None
        self._or_locked = False
        self._session_date: Optional[date] = None

    # ── Public interface (mirrors market_context's shape) ──────────────
    @property
    def regime(self) -> str:
        with self._lock:
            return self._regime

    def register_regime_callback(self, fn) -> None:
        self._regime_change_callbacks.append(fn)

    def get_regime_stability(self) -> float:
        return regime_engine_banknifty.get_regime_stability()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_flag.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_flag.set()

    # ── Internal loop ───────────────────────────────────────────────────
    def _run_loop(self) -> None:
        while not self._stop_flag.is_set():
            try:
                self._reset_session_if_new_day()
                spot = self._fetch_banknifty_spot()
                if spot is not None:
                    self._update_candle_bucket(spot)
                    self._update_opening_range(spot)
                    self._classify_regime()
            except Exception as e:
                import logging
                logging.getLogger(__name__).exception(
                    f"[banknifty_regime_feed] loop error: {e}"
                )
            self._stop_flag.wait(timeout=POLL_INTERVAL_SEC)

    def _reset_session_if_new_day(self) -> None:
        today = datetime.now(IST).date()
        if self._session_date != today:
            with self._lock:
                self._session_date = today
                self._session_candles.clear()
                self._current_bucket_minute = None
                self._current_bucket = {}
                self._or_high = None
                self._or_low = None
                self._or_locked = False

    def _fetch_banknifty_spot(self) -> Optional[float]:
        """Mirrors market_context._fetch_nifty_spot()'s pattern exactly,
        just pointed at BankNifty's instrument key instead."""
        token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
        if not token:
            return None
        try:
            url = "https://api.upstox.com/v2/market-quote/quotes"
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            params = {"instrument_key": BANKNIFTY_INSTRUMENT_KEY}
            resp = requests.get(url, headers=headers, params=params, timeout=5)
            resp.raise_for_status()
            data = resp.json().get("data", {})
            key = BANKNIFTY_INSTRUMENT_KEY.replace("|", ":")
            for k, v in data.items():
                if k.replace("|", ":") == key or BANKNIFTY_INSTRUMENT_KEY in k:
                    return float(v.get("last_price", 0.0)) or None
            return None
        except Exception:
            return None

    def _update_candle_bucket(self, spot: float) -> None:
        now = datetime.now(IST)
        bucket_minute = int(now.timestamp() // CANDLE_BUCKET_SECONDS)
        with self._lock:
            if self._current_bucket_minute is None:
                self._current_bucket_minute = bucket_minute
                self._current_bucket = {"open": spot, "high": spot, "low": spot, "close": spot}
                return
            if bucket_minute != self._current_bucket_minute:
                b = self._current_bucket
                self._session_candles.append(Candle(
                    ts=self._current_bucket_minute, open=b["open"],
                    high=b["high"], low=b["low"], close=b["close"],
                ))
                self._current_bucket_minute = bucket_minute
                self._current_bucket = {"open": spot, "high": spot, "low": spot, "close": spot}
            else:
                b = self._current_bucket
                b["high"] = max(b["high"], spot)
                b["low"] = min(b["low"], spot)
                b["close"] = spot

    def _update_opening_range(self, spot: float) -> None:
        now_t = datetime.now(IST).time()
        or_end = now_t.replace(hour=OR_END_MINUTE[0], minute=OR_END_MINUTE[1], second=0, microsecond=0)
        with self._lock:
            if now_t < or_end:
                self._or_high = spot if self._or_high is None else max(self._or_high, spot)
                self._or_low = spot if self._or_low is None else min(self._or_low, spot)
            elif not self._or_locked:
                self._or_locked = True
                if self._or_high is None:
                    self._or_high = self._or_low = spot

    def _classify_regime(self) -> None:
        with self._lock:
            candles = list(self._session_candles)
            if self._current_bucket_minute is not None and self._current_bucket:
                b = self._current_bucket
                candles = candles + [Candle(
                    ts=self._current_bucket_minute, open=b["open"],
                    high=b["high"], low=b["low"], close=b["close"],
                )]
            or_high = self._or_high
            or_low = self._or_low

        if len(candles) < 5:
            return  # matches regime_engine.classify()'s own guard

        spot = candles[-1].close
        new_regime, _signals = regime_engine_banknifty.classify(
            candles=candles,
            or_high=or_high if or_high is not None else spot,
            or_low=or_low if or_low is not None else spot,
            spot=spot,
            # Neutral defaults -- no BankNifty OI/PCR pipeline yet, see
            # module docstring. Not a hidden shortcut, a flagged gap.
            pcr=1.0,
            ce_oi_delta=0.0,
            pe_oi_delta=0.0,
            pcr_spike=False,
        )
        with self._lock:
            if new_regime != self._regime:
                old_regime = self._regime
                self._regime = new_regime
                for cb in list(self._regime_change_callbacks):
                    try:
                        cb(old_regime, new_regime)
                    except Exception:
                        pass
            else:
                self._regime = new_regime


# Module-level singleton, mirroring core.market_context's `market_context`.
# Only ever started from main.py, and only when bn_survivor is actually
# enabled (see main.py wiring) -- no cost/risk when bn_survivor is off.
banknifty_context = BankNiftyRegimeFeed()
