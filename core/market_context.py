"""
market_context.py — Layer 1: Market Context Engine
====================================================
Runs as a background thread from 9:00 AM IST.
Continuously monitors:
  - Opening Range (9:15–9:30 AM high/low, locked after 9:30)
  - PCR (Put/Call Ratio) from Upstox options chain
  - OI buildup (CE vs PE OI delta at key strikes)
  - Market regime classification every 2 minutes

Exposes a singleton `market_context` object that Layer 2
(strategy_filter.py) reads before every trade entry.

Usage (in main.py):
    from market_context import market_context
    market_context.start()        # call once at bot startup
    regime = market_context.regime
    pcr    = market_context.pcr
"""

import threading
import time
import logging
from datetime import datetime, time as dtime, timedelta
import pytz
import requests
import os
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")
MAX_PCR_STALENESS_MINUTES = 15.0  # alert if no successful OI fetch in this window during market hours


# ─────────────────────────────────────────────
# Config — edit these to match your Upstox setup
# ─────────────────────────────────────────────
NIFTY_INSTRUMENT_KEY = "NSE_INDEX|Nifty 50"   # Upstox instrument key for spot
NIFTY_OPTION_PREFIX  = "NIFTY"                 # Used to filter option chain
TOP_N_STRIKES        = 10                       # Strikes around ATM to consider
POLL_INTERVAL_SEC    = 30                       # Refresh every 2 minutes

# PCR thresholds
PCR_BULLISH_REVERSAL  = 1.7   # PCR above this → market oversold → bullish reversal watch
PCR_BEARISH_REVERSAL  = 0.6   # PCR below this → market overbought → bearish reversal watch
PCR_SPIKE_DELTA       = 0.5   # A move of this size in <5 min = institutional activity → freeze

# Opening range window
OR_START = dtime(9, 15)
OR_END   = dtime(9, 30)
TRADE_ALLOWED_FROM = dtime(9, 30)  # No entries before this
AUTO_STOP_TIME     = dtime(15, 10) # Hard stop (matches your existing config)


# ─────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────
@dataclass
class OpeningRange:
    high: Optional[float] = None
    low:  Optional[float] = None
    locked: bool = False       # True after 9:30 AM — values don't change

    @property
    def midpoint(self) -> Optional[float]:
        if self.high and self.low:
            return round((self.high + self.low) / 2, 2)
        return None

    @property
    def is_ready(self) -> bool:
        return self.locked and self.high is not None and self.low is not None


@dataclass
class OISnapshot:
    total_ce_oi: int = 0
    total_pe_oi: int = 0
    ce_oi_delta: int = 0   # Change vs previous snapshot
    pe_oi_delta: int = 0
    pcr: float = 1.0
    atm_strike: Optional[float] = None
    max_pain_strike: Optional[float] = None
    highest_ce_oi_strike: Optional[float] = None
    highest_pe_oi_strike: Optional[float] = None
    pe_support_migrating_up:      bool = False
    pe_support_migrating_down:    bool = False
    ce_resistance_migrating_up:   bool = False
    ce_resistance_migrating_down: bool = False
    timestamp: Optional[datetime] = None


# ─────────────────────────────────────────────
# Regime definitions
# ─────────────────────────────────────────────
REGIME_TRENDING_BULL   = "trending_bull"
REGIME_TRENDING_BEAR   = "trending_bear"
REGIME_RANGE           = "range"
REGIME_REVERSAL_WATCH  = "reversal_watch"
REGIME_OPENING         = "opening"       # Before 9:30 AM — no trades
REGIME_CLOSED          = "closed"        # Outside market hours


@dataclass
class PreviousDayLevels:
    """Previous trading day OHLC for Nifty spot — breakout/reversal context. Fetched once per day to respect API limits."""
    high:  float = 0.0
    low:   float = 0.0
    close: float = 0.0
    date:  str   = ""

    @property
    def is_ready(self) -> bool:
        return self.high > 0 and self.low > 0

class MarketContextEngine:
    """
    Singleton that holds live market context.
    Thread-safe reads via properties.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()

        # State
        self._regime: str = REGIME_CLOSED
        self._opening_range = OpeningRange()
        self._oi_snapshot = OISnapshot()
        self._prev_pcr: Optional[float] = None
        self._pcr_spike_time: Optional[datetime] = None
        self._last_successful_oi_fetch: Optional[datetime] = None
        self._pcr_is_stale: bool = False
        self._pcr_stale_alert_fired: bool = False
        self._prev_day_levels = PreviousDayLevels()

        # OI migration tracking — rolling history of support/resistance strikes
        self._pe_support_history:    list = []
        self._ce_resistance_history: list = []
        self._OI_MIGRATION_WINDOW = 5  # ~10 minutes at 2-min refresh interval

        # Spot price tracking for OR
        self._or_ticks_high: float = 0.0
        self._or_ticks_low:  float = float("inf")
        self._regime_change_callbacks = []  # list of fn(old, new)

        # ── Live tick-driven candle aggregation ─────────────────────────
        # Instead of re-fetching a rolling 60-candle window from Upstox's
        # REST API every 30s (~664 calls/day observed), build a real
        # session-anchored 1-minute candle series locally from the NIFTY
        # tick stream the bot already subscribes to. A separate lock keeps
        # this independent of self._lock to avoid any nested-lock risk
        # between the tick thread and the main poll loop.
        self._broker = None
        self._candle_lock = threading.Lock()
        self._session_candles: list = []       # completed 1-min Candle objects, oldest first
        self._current_bucket: Optional[dict] = None       # in-progress minute: {open,high,low,close}
        self._current_bucket_minute: Optional[str] = None  # "YYYY-MM-DD HH:MM" key of in-progress bucket
        self._SESSION_CANDLE_CAP = 400  # ~ full trading day of 1-min candles, bounds memory

    # ── Public read-only properties (Layer 2 reads these) ──────────────────

    def register_regime_callback(self, fn) -> None:
        """Register a callback fn(old_regime, new_regime) on regime change."""
        self._regime_change_callbacks.append(fn)

    def set_broker(self, broker) -> None:
        """
        Wire in the broker so market_context can subscribe to live NIFTY
        ticks and build its own session-anchored candle series locally,
        instead of re-fetching a rolling window from Upstox's REST API
        every poll cycle. Mirrors the existing vix_manager.set_broker()
        pattern already used in main.py. Safe to call once at startup,
        before or after start() — the broker's subscribe_ticks() supports
        multiple callbacks per symbol, so this coexists cleanly with
        survivor/saviour_combo's own NIFTY tick subscriptions.
        """
        self._broker = broker
        try:
            broker.subscribe_ticks(
                symbols=[NIFTY_INSTRUMENT_KEY],
                callback=self._on_nifty_tick,
            )
            logger.info(
                "[market_context] Subscribed to live NIFTY ticks — "
                "session candles now build locally instead of via REST poll"
            )
        except Exception as e:
            logger.error(f"[market_context] Failed to subscribe to NIFTY ticks: {e}")

    def _on_nifty_tick(self, tick) -> None:
        """
        Aggregate incoming NIFTY ticks into session-anchored 1-minute
        candles. Runs synchronously on the broker's tick thread — kept
        fast and lock-scoped so it never blocks the WebSocket feed.
        Bucketing uses local IST receipt time (matches the rest of this
        file's now(IST) convention) rather than parsing tick timestamps.
        """
        try:
            price = getattr(tick, "last_price", None)
            if not price:
                return
            now = datetime.now(IST)
            minute_key = now.strftime("%Y-%m-%d %H:%M")
            with self._candle_lock:
                if self._current_bucket_minute is None:
                    self._current_bucket_minute = minute_key
                    self._current_bucket = {
                        "open": price, "high": price, "low": price, "close": price
                    }
                    return
                if minute_key == self._current_bucket_minute:
                    b = self._current_bucket
                    b["high"]  = max(b["high"], price)
                    b["low"]   = min(b["low"],  price)
                    b["close"] = price
                    return
                # Minute rolled over — finalize the completed bucket, start fresh
                from core.regime_engine import Candle
                finished = self._current_bucket
                self._session_candles.append(Candle(
                    ts=self._current_bucket_minute,
                    open=finished["open"], high=finished["high"],
                    low=finished["low"], close=finished["close"],
                ))
                if len(self._session_candles) > self._SESSION_CANDLE_CAP:
                    self._session_candles.pop(0)
                self._current_bucket_minute = minute_key
                self._current_bucket = {
                    "open": price, "high": price, "low": price, "close": price
                }
        except Exception as e:
            logger.debug(f"[market_context] Tick aggregation error: {e}")

    def _backfill_session_candles(self) -> None:
        """
        One-time REST backfill of today's session candles. Covers the gap
        between actual market open and whenever set_broker()/ticks start
        flowing (or a restart mid-session, when the tick-built series
        resets to empty). After this, ticks take over and no further REST
        calls are needed for candle data — replacing what was previously
        ~664 calls/day with essentially 1 per session (plus rare
        reconnect-triggered backfills if the local series runs dry).
        """
        try:
            from core.regime_engine import fetch_intraday_candles
            token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
            if not token:
                return
            candles = fetch_intraday_candles(token, self._SESSION_CANDLE_CAP)
            with self._candle_lock:
                if not self._session_candles:  # don't clobber ticks that arrived first
                    self._session_candles = list(candles)
            logger.info(
                f"[market_context] Backfilled {len(candles)} session candles (one-time REST call)"
            )
        except Exception as e:
            logger.error(f"[market_context] Session candle backfill failed: {e}")

    @property
    def regime(self) -> str:
        with self._lock:
            return self._regime

    @property
    def pcr(self) -> float:
        with self._lock:
            return self._oi_snapshot.pcr

    @property
    def opening_range(self) -> OpeningRange:
        with self._lock:
            return self._opening_range

    @property
    def oi(self) -> OISnapshot:
        with self._lock:
            return self._oi_snapshot

    @property
    def previous_day_levels(self) -> PreviousDayLevels:
        with self._lock:
            return self._prev_day_levels

    @property
    def session_candles_1min(self) -> list:
        """
        Public read-only access to the live tick-built 1-min session candles.
        """
        from core.regime_engine import Candle
        with self._candle_lock:
            candles = list(self._session_candles)
            if self._current_bucket_minute is not None:
                b = self._current_bucket
                candles = candles + [Candle(
                    ts=self._current_bucket_minute, open=b["open"],
                    high=b["high"], low=b["low"], close=b["close"],
                )]
        return candles

    @property
    def is_pcr_spike(self) -> bool:
        """True if a sudden PCR spike was detected in last 15 minutes."""
        with self._lock:
            if self._pcr_spike_time is None:
                return False
            elapsed = (datetime.now(IST) - self._pcr_spike_time).total_seconds()
            return elapsed < 900  # 15-minute freeze window

    @property
    def trade_allowed(self) -> bool:
        """Master check: is the clock in the trading window?"""
        now = datetime.now(IST).time()
        return TRADE_ALLOWED_FROM <= now <= AUTO_STOP_TIME

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def _fetch_previous_day_levels(self) -> None:
        """
        Fetch yesterday's daily OHLC for Nifty spot — called ONCE at startup.
        Uses the daily-candle endpoint (1 API call/day) to stay well within
        Upstox rate limits.
        """
        try:
            import os
            import requests
            token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
            if not token:
                logger.warning("[market_context] No token — skipping previous day levels fetch")
                return
            now_ist   = datetime.now(IST)
            to_date   = now_ist.strftime("%Y-%m-%d")
            from_date = (now_ist - timedelta(days=10)).strftime("%Y-%m-%d")
            url = f"https://api.upstox.com/v2/historical-candle/NSE_INDEX%7CNifty%2050/day/{to_date}/{from_date}"
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            r = requests.get(url, headers=headers, timeout=5)
            data = r.json()
            if data.get("status") != "success":
                logger.warning(f"[market_context] Previous day levels fetch failed: {data}")
                return
            candles = data.get("data", {}).get("candles", [])
            if not candles:
                logger.warning("[market_context] No daily candles returned")
                return
            # Most recent completed daily candle = yesterday's session
            latest = candles[0]  # [ts, open, high, low, close, vol, oi]
            with self._lock:
                self._prev_day_levels = PreviousDayLevels(
                    high=latest[2], low=latest[3], close=latest[4], date=latest[0][:10]
                )
            logger.info(
                f"[market_context] Previous day levels: H={latest[2]:.1f} "
                f"L={latest[3]:.1f} C={latest[4]:.1f} ({latest[0][:10]})"
            )
        except Exception as e:
            logger.error(f"[market_context] Previous day levels fetch error: {e}")

    def start(self):
        """Call once from main.py at bot startup."""
        if self._thread and self._thread.is_alive():
            logger.warning("MarketContextEngine already running")
            return
        self._fetch_previous_day_levels()  # one-time, before loop starts
        self._backfill_session_candles()   # one-time REST backfill; ticks take over after this
        self._stop_flag.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="market-context",
            daemon=True
        )
        self._thread.start()
        logger.info("MarketContextEngine started")

    def stop(self):
        self._stop_flag.set()
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("MarketContextEngine stopped")

    # ── Internal loop ──────────────────────────────────────────────────────

    def _run_loop(self):
        logger.info("Market context loop running")
        while not self._stop_flag.is_set():
            try:
                now_ist = datetime.now(IST)
                now_t   = now_ist.time()

                if now_t < OR_START:
                    # Pre-market: just wait
                    with self._lock:
                        self._regime = REGIME_CLOSED
                    self._stop_flag.wait(timeout=30)
                    continue

                if OR_START <= now_t < OR_END:
                    # Opening range collection window
                    with self._lock:
                        self._regime = REGIME_OPENING
                    self._collect_opening_range_tick()
                    self._stop_flag.wait(timeout=15)  # poll every 15s during OR window
                    continue

                # Late start: already past OR, lock immediately with spot price
                if now_t >= OR_END and not self._opening_range.locked:
                    spot = self._fetch_nifty_spot()
                    if spot:
                        self._or_ticks_high = spot
                        self._or_ticks_low = spot
                    self._lock_opening_range()
                if not self._opening_range.locked:
                    # Just crossed 9:30 — lock the range
                    self._lock_opening_range()

                if now_t > AUTO_STOP_TIME:
                    with self._lock:
                        self._regime = REGIME_CLOSED
                    self._stop_flag.wait(timeout=60)
                    continue

                # Main trading session: fetch OI + classify
                self._fetch_and_update_oi()
                self._classify_regime()

            except Exception as e:
                logger.exception(f"MarketContextEngine error: {e}")

            self._stop_flag.wait(timeout=POLL_INTERVAL_SEC)

    # ── Opening Range ──────────────────────────────────────────────────────

    def _collect_opening_range_tick(self):
        spot = self._fetch_nifty_spot()
        if spot is None:
            return
        with self._lock:
            if spot > self._or_ticks_high:
                self._or_ticks_high = spot
            if spot < self._or_ticks_low:
                self._or_ticks_low = spot
        logger.debug(f"OR tick: spot={spot} high={self._or_ticks_high} low={self._or_ticks_low}")

    def _lock_opening_range(self):
        with self._lock:
            if self._or_ticks_high > 0 and self._or_ticks_low < float("inf"):
                self._opening_range.high   = self._or_ticks_high
                self._opening_range.low    = self._or_ticks_low
                self._opening_range.locked = True
                logger.info(
                    f"Opening Range LOCKED — High: {self._opening_range.high} "
                    f"Low: {self._opening_range.low} "
                    f"Mid: {self._opening_range.midpoint}"
                )
            else:
                logger.warning("Opening range lock failed — no ticks collected")

    # ── OI + PCR ───────────────────────────────────────────────────────────

    def _fetch_and_update_oi(self):
        """Fetch Upstox options chain and compute OI / PCR."""
        token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
        if not token:
            logger.warning("UPSTOX_ACCESS_TOKEN not set — skipping OI fetch")
            return

        spot = self._fetch_nifty_spot()
        if spot is None:
            return

        atm = self._round_to_strike(spot)

        try:
            # Fetch option chain for current expiry
            url = "https://api.upstox.com/v2/option/chain"
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            }
            params = {
                "instrument_key": NIFTY_INSTRUMENT_KEY,
                "expiry_date": self._get_current_expiry(),
            }
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json().get("data", [])

        except Exception as e:
            logger.error(f"Option chain fetch failed: {e}")
            return

        # Parse CE and PE OI around ATM
        total_ce_oi = 0
        total_pe_oi = 0
        strike_oi = {}   # strike → {ce, pe} for max pain

        for item in data:
            strike = item.get("strike_price", 0)
            if abs(strike - atm) > (TOP_N_STRIKES // 2) * 50:
                continue  # Only look at strikes within TOP_N range

            ce_oi = item.get("call_options", {}).get("market_data", {}).get("oi", 0) or 0
            pe_oi = item.get("put_options",  {}).get("market_data", {}).get("oi", 0) or 0
            total_ce_oi += ce_oi
            total_pe_oi += pe_oi
            strike_oi[strike] = {"ce": ce_oi, "pe": pe_oi}

        if total_ce_oi == 0:
            minutes_stale = (
                999.0 if self._last_successful_oi_fetch is None
                else (datetime.now(IST) - self._last_successful_oi_fetch).total_seconds() / 60.0
            )
            logger.warning(
                f"Zero CE OI returned — using last known PCR ({self._oi_snapshot.pcr}) | "
                f"Minutes since last success: {minutes_stale:.1f}"
            )
            now_time = datetime.now(IST).time()
            in_market_hours = dtime(9, 15) <= now_time <= dtime(15, 30)
            if in_market_hours and minutes_stale >= MAX_PCR_STALENESS_MINUTES:
                if not self._pcr_is_stale:
                    logger.critical(
                        f"PCR FEED STALE for {minutes_stale:.1f} min during market hours — "
                        f"strike selection and regime filters using stale data"
                    )
                self._pcr_is_stale = True
                if not self._pcr_stale_alert_fired:
                    try:
                        from core.alerting import alert_pcr_stale
                        alert_pcr_stale(minutes_stale)
                    except Exception as e:
                        logger.error(f"Failed to send PCR stale alert: {e}")
                    self._pcr_stale_alert_fired = True
            return
        # Successful fetch — reset staleness tracking
        if self._pcr_is_stale:
            logger.warning("PCR feed recovered — fresh OI data received")
        self._last_successful_oi_fetch = datetime.now(IST)
        self._pcr_is_stale = False
        self._pcr_stale_alert_fired = False
        new_pcr = round(total_pe_oi / total_ce_oi, 3)
        max_pain = self._compute_max_pain(strike_oi)

        with self._lock:
            prev = self._oi_snapshot
            ce_delta = total_ce_oi - prev.total_ce_oi
            pe_delta = total_pe_oi - prev.total_pe_oi

            # Detect sudden PCR spike
            if self._prev_pcr is not None:
                delta = abs(new_pcr - self._prev_pcr)
                if delta >= PCR_SPIKE_DELTA:
                    self._pcr_spike_time = datetime.now(IST)
                    logger.warning(
                        f"PCR SPIKE detected: {self._prev_pcr} → {new_pcr} "
                        f"(Δ{delta:.2f}) — freezing new entries for 15 min"
                    )

            self._prev_pcr = self._oi_snapshot.pcr

            # ── OI Migration Tracking ────────────────────────────────────
            highest_ce_strike = max(strike_oi, key=lambda k: strike_oi[k]["ce"]) if strike_oi else None
            highest_pe_strike = max(strike_oi, key=lambda k: strike_oi[k]["pe"]) if strike_oi else None

            self._pe_support_history.append(highest_pe_strike)
            self._ce_resistance_history.append(highest_ce_strike)
            if len(self._pe_support_history) > self._OI_MIGRATION_WINDOW:
                self._pe_support_history.pop(0)
            if len(self._ce_resistance_history) > self._OI_MIGRATION_WINDOW:
                self._ce_resistance_history.pop(0)

            pe_support_migrating_up = pe_support_migrating_down = False
            ce_resistance_migrating_up = ce_resistance_migrating_down = False
            oldest_pe = next((s for s in self._pe_support_history if s is not None), None)
            oldest_ce = next((s for s in self._ce_resistance_history if s is not None), None)
            if oldest_pe is not None and highest_pe_strike is not None:
                pe_support_migrating_up   = highest_pe_strike > oldest_pe
                pe_support_migrating_down = highest_pe_strike < oldest_pe
            if oldest_ce is not None and highest_ce_strike is not None:
                ce_resistance_migrating_up   = highest_ce_strike > oldest_ce
                ce_resistance_migrating_down = highest_ce_strike < oldest_ce

            self._oi_snapshot = OISnapshot(
                total_ce_oi=total_ce_oi,
                total_pe_oi=total_pe_oi,
                ce_oi_delta=ce_delta,
                pe_oi_delta=pe_delta,
                pcr=new_pcr,
                atm_strike=atm,
                max_pain_strike=max_pain,
                highest_ce_oi_strike=highest_ce_strike,
                highest_pe_oi_strike=highest_pe_strike,
                pe_support_migrating_up=pe_support_migrating_up,
                pe_support_migrating_down=pe_support_migrating_down,
                ce_resistance_migrating_up=ce_resistance_migrating_up,
                ce_resistance_migrating_down=ce_resistance_migrating_down,
                timestamp=datetime.now(IST),
            )

        logger.info(
            f"OI update — CE: {total_ce_oi:,} PE: {total_pe_oi:,} "
            f"PCR: {new_pcr} ATM: {atm} MaxPain: {max_pain}"
        )

    # ── Regime Classification ──────────────────────────────────────────────

    def _classify_regime(self):
        """Delegates to regime_engine for institutional-grade classification."""
        from core.regime_engine import regime_engine, Candle

        with self._candle_lock:
            candles = list(self._session_candles)
            if self._current_bucket_minute is not None:
                # Include the still-forming current-minute bucket so regime
                # scoring reacts to the latest price, not just fully-closed
                # candles from a minute or more ago.
                b = self._current_bucket
                candles = candles + [Candle(
                    ts=self._current_bucket_minute, open=b["open"],
                    high=b["high"], low=b["low"], close=b["close"],
                )]

        if len(candles) < 5:
            # Not enough locally-built data yet (e.g. right after a restart,
            # before ticks have had time to accumulate) — fall back to a
            # one-time REST backfill rather than blocking classification.
            self._backfill_session_candles()
            with self._candle_lock:
                candles = list(self._session_candles)

        with self._lock:
            pcr      = self._oi_snapshot.pcr
            snap     = self._oi_snapshot
            or_      = self._opening_range
            pdl      = self._prev_day_levels
            spike    = self._pcr_spike_time is not None and (
                datetime.now(IST) - self._pcr_spike_time
            ).total_seconds() < 900
        spot = self._fetch_nifty_spot() or 0.0
        new_regime, signals = regime_engine.classify(
            candles     = candles,
            or_high     = or_.high if or_.is_ready else spot,
            or_low      = or_.low  if or_.is_ready else spot,
            spot        = spot,
            pcr         = pcr,
            ce_oi_delta = snap.ce_oi_delta,
            pe_oi_delta = snap.pe_oi_delta,
            pcr_spike   = spike,
            prev_close    = pdl.close if pdl.is_ready else 0.0,
            prev_day_high = pdl.high  if pdl.is_ready else 0.0,
            prev_day_low  = pdl.low   if pdl.is_ready else 0.0,
            pe_support_migrating_up      = snap.pe_support_migrating_up,
            pe_support_migrating_down    = snap.pe_support_migrating_down,
            ce_resistance_migrating_up   = snap.ce_resistance_migrating_up,
            ce_resistance_migrating_down = snap.ce_resistance_migrating_down,
        )
        with self._lock:
            if new_regime != self._regime:
                logger.info(f"Regime change: {self._regime} → {new_regime} | score={signals.market_score:+.0f}")
                old_regime = self._regime
                self._regime = new_regime
                for cb in list(self._regime_change_callbacks):
                    try:
                        cb(old_regime, new_regime)
                    except Exception as cb_e:
                        logger.error(f"Regime change callback error: {cb_e}")
            else:
                self._regime = new_regime
    def _fetch_nifty_spot(self) -> Optional[float]:
        token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
        if not token:
            return None
        try:
            url = "https://api.upstox.com/v2/market-quote/quotes"
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            params  = {"instrument_key": NIFTY_INSTRUMENT_KEY}
            resp = requests.get(url, headers=headers, params=params, timeout=5)
            resp.raise_for_status()
            data = resp.json().get("data", {})
            # Upstox returns data keyed by instrument key (with | replaced by _)
            key  = list(data.keys())[0] if data else None
            if key:
                return data[key].get("last_price")
        except Exception as e:
            logger.debug(f"Spot fetch error: {e}")
        return None

    @staticmethod
    def _round_to_strike(spot: float, step: int = 50) -> float:
        """Round spot price to nearest Nifty strike (multiples of 50)."""
        return round(round(spot / step) * step, 2)

    @staticmethod
    def _get_current_expiry() -> str:
        """
        Returns the nearest weekly Nifty expiry date as 'YYYY-MM-DD'.
        Nifty weekly expiry is every Tuesday (changed from Thursday).
        If today is past Tuesday, returns next Tuesday.
        """
        today = datetime.now(IST).date()
        days_ahead = (1 - today.weekday()) % 7  # 1 = Tuesday
        if days_ahead == 0 and datetime.now(IST).time() > dtime(15, 30):
            days_ahead = 7  # Current Tuesday is expired, use next
        expiry = today + timedelta(days=days_ahead)
        return expiry.strftime("%Y-%m-%d")

    @staticmethod
    def _compute_max_pain(strike_oi: dict) -> Optional[float]:
        """
        Max pain = strike where total option buyer losses are maximized.
        For each candidate strike S, sum losses for all CE holders (S > strike)
        and all PE holders (S < strike). Min of this sum = max pain for market makers.
        """
        if not strike_oi:
            return None
        strikes = sorted(strike_oi.keys())
        min_loss = float("inf")
        max_pain = strikes[0]

        for candidate in strikes:
            total_loss = 0
            for s, oi in strike_oi.items():
                # CE holders lose if candidate > their strike (ITM CEs expire worthless for buyers)
                if candidate > s:
                    total_loss += (candidate - s) * oi["ce"]
                # PE holders lose if candidate < their strike
                if candidate < s:
                    total_loss += (s - candidate) * oi["pe"]
            if total_loss < min_loss:
                min_loss = total_loss
                max_pain = candidate

        return max_pain


# ── Singleton ──────────────────────────────────────────────────────────────
# Import this object everywhere — don't instantiate MarketContextEngine directly.
market_context = MarketContextEngine()


# ── Quick diagnostics (run directly to test) ───────────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    print("Starting MarketContextEngine in test mode...")
    print("Ctrl+C to stop\n")
    market_context.start()

    try:
        while True:
            time.sleep(5)
            print(
                f"  regime={market_context.regime:20s} | "
                f"pcr={market_context.pcr:.3f} | "
                f"OR locked={market_context.opening_range.locked} | "
                f"OR high={market_context.opening_range.high} "
                f"low={market_context.opening_range.low} | "
                f"trade_ok={market_context.trade_allowed} | "
                f"pcr_spike={market_context.is_pcr_spike}"
            )
    except KeyboardInterrupt:
        market_context.stop()
        print("\nStopped.")
