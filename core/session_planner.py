# core/session_planner.py
# ═══════════════════════════════════════════════════════════════════════════════
# SESSION PLANNER — Layer 0 (Pre-Trade Intelligence)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Runs from 9:15 AM to 9:30 AM every trading day.
# Analyzes the first 15 minutes deeply and produces a SESSION PLAN at 9:30 AM:
#   - Which regime is confirmed (RANGE or TRENDING)
#   - Confidence level (HIGH / MEDIUM / LOW)
#   - Dynamic parameters for each strategy
#   - Session risk budget (dynamic daily loss limit)
#   - Which strategy to activate / block
#
# Integration:
#   1. main.py starts session_planner at 9:00 AM
#   2. strategy_filter.py reads session_plan before every trade
#   3. risk_manager.py reads session_plan for dynamic daily loss limit
#   4. Dashboard reads session_plan for display
#
# Usage:
#   from core.session_planner import session_planner
#   session_planner.start()
#   plan = session_planner.current_plan   # SessionPlan dataclass
# ═══════════════════════════════════════════════════════════════════════════════

import threading
import time
import logging
import os
import json
import requests
from datetime import datetime, time as dtime, timedelta
from dataclasses import dataclass, field, asdict
from typing import Optional
from pathlib import Path

import pytz

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

# ─── Timing constants ─────────────────────────────────────────────────────────
OR_START         = dtime(9, 15)   # Opening range collection starts
OR_END           = dtime(9, 30)   # Opening range locks, plan produced
REEVAL_TIME      = dtime(10, 0)   # Re-evaluate if market reverses
AUTO_STOP        = dtime(15, 10)  # Hard stop

# ─── Scoring weights ──────────────────────────────────────────────────────────
# Each signal contributes +score to RANGE or TRENDING bucket.
# Final decision: whichever bucket has higher score wins.

# Opening range width thresholds (Nifty points)
OR_NARROW_THRESHOLD  = 40   # < 40 pts = strongly range-like
OR_MEDIUM_THRESHOLD  = 70   # 40-70 pts = neutral
OR_WIDE_THRESHOLD    = 100  # > 100 pts = strongly trending

# VIX thresholds
VIX_LOW    = 13.0
VIX_NORMAL = 18.0
VIX_HIGH   = 22.0

# Gap open threshold (%)
GAP_SMALL  = 0.3   # < 0.3% = no gap
GAP_MEDIUM = 0.6   # 0.3-0.6% = moderate gap
GAP_LARGE  = 1.0   # > 1.0% = large gap

# Midpoint crosses — how many times price crossed OR midpoint in first 15 min
CROSS_CHOPPY   = 4   # > 4 crosses = very choppy = range
CROSS_MODERATE = 2   # 2-4 crosses = moderate

# Confidence thresholds
HIGH_CONFIDENCE_THRESHOLD   = 5   # Score difference > 5 = HIGH confidence
MEDIUM_CONFIDENCE_THRESHOLD = 3   # Score difference 3-5 = MEDIUM confidence
# Score difference < 3 = LOW confidence → no trading until 10 AM re-eval

# ─── Nifty instrument key ─────────────────────────────────────────────────────
NIFTY_KEY = "NSE_INDEX|Nifty 50"


# ═══════════════════════════════════════════════════════════════════════════════
# Data Classes
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class RegimeScore:
    """Raw scoring data from each signal layer."""
    range_score:    float = 0.0
    trending_score: float = 0.0

    # Signal breakdown (for logging/dashboard)
    or_width:             Optional[float] = None
    or_width_signal:      str = ""
    vix_level:            Optional[float] = None
    vix_signal:           str = ""
    gap_pct:              Optional[float] = None
    gap_signal:           str = ""
    midpoint_crosses:     int = 0
    crosses_signal:       str = ""
    pcr_at_open:          Optional[float] = None
    pcr_signal:           str = ""
    prev_close:           Optional[float] = None
    today_open:           Optional[float] = None

    @property
    def winner(self) -> str:
        if self.range_score > self.trending_score:
            return "range"
        elif self.trending_score > self.range_score:
            return "trending"
        else:
            return "range"  # Default to range on tie (safer)

    @property
    def score_diff(self) -> float:
        return abs(self.range_score - self.trending_score)

    @property
    def confidence(self) -> str:
        if self.score_diff >= HIGH_CONFIDENCE_THRESHOLD:
            return "HIGH"
        elif self.score_diff >= MEDIUM_CONFIDENCE_THRESHOLD:
            return "MEDIUM"
        else:
            return "LOW"


@dataclass
class StrategyParams:
    """Dynamic parameters for each strategy based on session plan."""
    # Survivor params
    pe_gap:           float = 10.0   # Points Nifty must move to trigger PE sell
    ce_gap:           float = 10.0   # Points Nifty must move to trigger CE sell
    pe_symbol_gap:    float = 350.0  # Points OTM for PE strike selection
    ce_symbol_gap:    float = 350.0  # Points OTM for CE strike selection
    min_premium:      float = 15.0   # Minimum option premium to sell

    # Wave Extractor params
    sell_gap:         float = 20.0   # Points above current price to place SELL bracket
    buy_gap:          float = 20.0   # Points below current price to place BUY bracket
    cool_off_time:    float = 5.0    # Seconds to wait between brackets

    # Position sizing
    position_size:    float = 1.0    # Multiplier: 1.0 = full lot, 0.5 = half lot


@dataclass
class SessionPlan:
    """
    The complete session plan produced at 9:30 AM.
    Read by strategy_filter and risk_manager before every trade.
    """
    # Core decision
    regime:           str   = "range"     # "range" or "trending"
    confidence:       str   = "LOW"       # "HIGH", "MEDIUM", "LOW"
    trending_direction: str = ""          # "bull" or "bear" (only if regime=trending)

    # Which strategies are active
    survivor_active:  bool  = False
    wave_active:      bool  = False

    # Dynamic risk budget
    daily_loss_limit: float = -3000.0    # Overrides risk_manager default
    max_capital:      float = 150000.0   # Max capital to deploy this session

    # Dynamic parameters
    params:           StrategyParams = field(default_factory=StrategyParams)

    # Raw scores (for transparency/logging)
    scores:           RegimeScore = field(default_factory=RegimeScore)

    # Metadata
    produced_at:      Optional[str] = None   # ISO timestamp when plan was made
    plan_version:     int = 1                # Increments on re-evaluation
    notes:            list = field(default_factory=list)  # Human-readable reasoning

    # State
    is_ready:         bool = False   # False until 9:30 AM plan is produced
    low_confidence_hold_until: Optional[str] = None  # ISO time of 10 AM re-eval

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    def summary(self) -> str:
        lines = [
            f"═══ SESSION PLAN v{self.plan_version} ═══",
            f"Regime:      {self.regime.upper()} ({self.confidence} confidence)",
            f"Direction:   {self.trending_direction or 'N/A'}",
            f"Survivor:    {'✅ ACTIVE' if self.survivor_active else '❌ BLOCKED'}",
            f"Wave:        {'✅ ACTIVE' if self.wave_active else '❌ BLOCKED'}",
            f"Daily limit: ₹{abs(self.daily_loss_limit):,.0f}",
            f"OR width:    {self.scores.or_width or 'N/A'} pts → {self.scores.or_width_signal}",
            f"VIX:         {self.scores.vix_level or 'N/A'} → {self.scores.vix_signal}",
            f"Gap:         {self.scores.gap_pct or 0:.2f}% → {self.scores.gap_signal}",
            f"Crosses:     {self.scores.midpoint_crosses} → {self.scores.crosses_signal}",
            f"PCR:         {self.scores.pcr_at_open or 'N/A'} → {self.scores.pcr_signal}",
            f"Range score: {self.scores.range_score:.1f} | Trending score: {self.scores.trending_score:.1f}",
        ]
        if self.notes:
            lines.append("Notes: " + " | ".join(self.notes))
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# Session Planner Engine
# ═══════════════════════════════════════════════════════════════════════════════

class SessionPlannerEngine:
    """
    Background engine that:
    1. Collects OR data from 9:15-9:30 AM
    2. Produces SessionPlan at 9:30 AM
    3. Optionally re-evaluates at 10:00 AM if market reverses
    """

    def __init__(self):
        self._lock             = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_flag        = threading.Event()
        self._plan             = SessionPlan()

        # OR data collection
        self._or_ticks:        list  = []   # list of (time, price) tuples
        self._or_high:         float = 0.0
        self._or_low:          float = float("inf")
        self._midpoint:        Optional[float] = None
        self._midpoint_crosses: int  = 0
        self._last_side:       Optional[str] = None  # "above" or "below"

        # Plan output path
        self._plan_path = Path("configs/session_plan.json")

    # ── Public interface ───────────────────────────────────────────────────

    @property
    def current_plan(self) -> SessionPlan:
        with self._lock:
            return self._plan

    @property
    def is_ready(self) -> bool:
        with self._lock:
            return self._plan.is_ready

    def start(self):
        if self._thread and self._thread.is_alive():
            logger.warning("[SessionPlanner] Already running")
            return
        self._stop_flag.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="session-planner",
            daemon=True
        )
        self._thread.start()
        logger.info("[SessionPlanner] Started")

    def stop(self):
        self._stop_flag.set()
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("[SessionPlanner] Stopped")

    def force_replan(self):
        """Manually trigger a re-evaluation (call from dashboard or main)."""
        logger.info("[SessionPlanner] Force re-plan requested")
        self._produce_plan(is_reeval=True)

    # ── Internal loop ──────────────────────────────────────────────────────

    def _run_loop(self):
        logger.info("[SessionPlanner] Loop started")
        plan_produced = False
        reeval_done   = False

        while not self._stop_flag.is_set():
            try:
                now     = datetime.now(IST)
                now_t   = now.time()
                weekday = now.weekday()

                # Skip weekends
                if weekday >= 5:
                    self._stop_flag.wait(timeout=300)
                    continue

                # Before market opens — reset for new day
                if now_t < OR_START:
                    plan_produced = False
                    reeval_done   = False
                    with self._lock:
                        self._plan = SessionPlan()
                    self._reset_or_data()
                    self._stop_flag.wait(timeout=60)
                    continue

                # Opening range collection: 9:15 - 9:30
                if OR_START <= now_t < OR_END:
                    self._collect_or_tick()
                    self._stop_flag.wait(timeout=10)  # Poll every 10 seconds
                    continue

                # Produce plan at 9:30 (once)
                if now_t >= OR_END and not plan_produced:
                    self._produce_plan(is_reeval=False)
                    plan_produced = True
                    self._stop_flag.wait(timeout=60)
                    continue

                # Re-evaluate at 10:00 AM if LOW confidence
                if now_t >= REEVAL_TIME and not reeval_done:
                    with self._lock:
                        conf = self._plan.confidence
                    if conf == "LOW":
                        logger.info("[SessionPlanner] LOW confidence — re-evaluating at 10:00 AM")
                        self._produce_plan(is_reeval=True)
                    reeval_done = True
                    self._stop_flag.wait(timeout=300)
                    continue

                # After auto-stop
                if now_t > AUTO_STOP:
                    self._stop_flag.wait(timeout=300)
                    continue

                # Normal running — just sleep
                self._stop_flag.wait(timeout=120)

            except Exception as e:
                logger.exception(f"[SessionPlanner] Loop error: {e}")
                self._stop_flag.wait(timeout=30)

        logger.info("[SessionPlanner] Loop ended")

    # ── OR Data Collection ─────────────────────────────────────────────────

    def _reset_or_data(self):
        self._or_ticks         = []
        self._or_high          = 0.0
        self._or_low           = float("inf")
        self._midpoint         = None
        self._midpoint_crosses = 0
        self._last_side        = None

    def _collect_or_tick(self):
        spot = self._fetch_nifty_spot()
        if spot is None:
            return

        now = datetime.now(IST)
        self._or_ticks.append((now, spot))

        # Track high/low
        if spot > self._or_high:
            self._or_high = spot
        if spot < self._or_low:
            self._or_low = spot

        # Track midpoint crosses (updated continuously)
        if self._or_high > 0 and self._or_low < float("inf"):
            mid = (self._or_high + self._or_low) / 2
            side = "above" if spot > mid else "below"
            if self._last_side and self._last_side != side:
                self._midpoint_crosses += 1
            self._last_side = side

        logger.debug(
            f"[SessionPlanner] OR tick: {spot:.2f} | "
            f"H:{self._or_high:.2f} L:{self._or_low:.2f} | "
            f"Crosses:{self._midpoint_crosses}"
        )

    # ── Plan Production ────────────────────────────────────────────────────

    def _produce_plan(self, is_reeval: bool = False):
        logger.info(f"[SessionPlanner] Producing {'RE-EVAL' if is_reeval else 'INITIAL'} plan...")

        scores = RegimeScore()

        # ── Layer 1: Gap Analysis ────────────────────────────────────────
        prev_close, today_open = self._fetch_gap_data()
        scores.prev_close  = prev_close
        scores.today_open  = today_open

        if prev_close and today_open and prev_close > 0:
            gap_pct = abs((today_open - prev_close) / prev_close) * 100
            scores.gap_pct = round(gap_pct, 3)

            if gap_pct < GAP_SMALL:
                scores.range_score    += 2
                scores.gap_signal      = "flat open → range likely"
            elif gap_pct < GAP_MEDIUM:
                scores.range_score    += 1
                scores.trending_score += 1
                scores.gap_signal      = "small gap → neutral"
            elif gap_pct < GAP_LARGE:
                scores.trending_score += 2
                scores.gap_signal      = "moderate gap → trending possible"
            else:
                scores.trending_score += 3
                scores.range_score    -= 1
                scores.gap_signal      = "large gap → trending likely"
        else:
            scores.gap_signal = "gap data unavailable"

        # ── Layer 2: VIX Analysis ────────────────────────────────────────
        vix = self._fetch_vix()
        scores.vix_level = vix

        if vix:
            if vix < VIX_LOW:
                scores.range_score    += 3
                scores.vix_signal      = f"VIX={vix:.1f} LOW → calm market, range likely"
            elif vix < VIX_NORMAL:
                scores.range_score    += 2
                scores.trending_score += 1
                scores.vix_signal      = f"VIX={vix:.1f} NORMAL → slight range bias"
            elif vix < VIX_HIGH:
                scores.trending_score += 2
                scores.range_score    += 1
                scores.vix_signal      = f"VIX={vix:.1f} ELEVATED → trending possible"
            else:
                scores.trending_score += 3
                scores.range_score    -= 1
                scores.vix_signal      = f"VIX={vix:.1f} HIGH → volatile, trending likely"
        else:
            scores.vix_signal = "VIX data unavailable"

        # ── Layer 3: Opening Range Width ─────────────────────────────────
        or_width = None
        if self._or_high > 0 and self._or_low < float("inf"):
            or_width = round(self._or_high - self._or_low, 2)
        scores.or_width = or_width

        if or_width is not None:
            if or_width < OR_NARROW_THRESHOLD:
                scores.range_score    += 3
                scores.or_width_signal = f"OR={or_width:.0f}pts NARROW → range confirmed"
            elif or_width < OR_MEDIUM_THRESHOLD:
                scores.range_score    += 2
                scores.trending_score += 1
                scores.or_width_signal = f"OR={or_width:.0f}pts MEDIUM → slight range bias"
            elif or_width < OR_WIDE_THRESHOLD:
                scores.trending_score += 2
                scores.range_score    += 1
                scores.or_width_signal = f"OR={or_width:.0f}pts WIDE → trending possible"
            else:
                scores.trending_score += 3
                scores.or_width_signal = f"OR={or_width:.0f}pts VERY WIDE → trending confirmed"
        else:
            scores.or_width_signal = "OR data not collected"

        # ── Layer 4: Midpoint Crosses ────────────────────────────────────
        crosses = self._midpoint_crosses
        scores.midpoint_crosses = crosses

        if crosses > CROSS_CHOPPY:
            scores.range_score     += 3
            scores.crosses_signal   = f"{crosses} crosses → very choppy, range"
        elif crosses > CROSS_MODERATE:
            scores.range_score     += 2
            scores.crosses_signal   = f"{crosses} crosses → choppy, range likely"
        elif crosses == 1:
            scores.trending_score  += 2
            scores.crosses_signal   = f"{crosses} cross → directional move"
        elif crosses == 0:
            scores.trending_score  += 3
            scores.crosses_signal   = "0 crosses → strong directional move"
        else:
            scores.range_score     += 1
            scores.trending_score  += 1
            scores.crosses_signal   = f"{crosses} crosses → neutral"

        # ── Layer 5: PCR at Open ─────────────────────────────────────────
        pcr = self._fetch_pcr()
        scores.pcr_at_open = pcr

        if pcr:
            if 0.85 <= pcr <= 1.15:
                scores.range_score    += 2
                scores.pcr_signal      = f"PCR={pcr:.2f} NEUTRAL → range bias"
            elif 1.15 < pcr <= 1.3:
                scores.range_score    += 1
                scores.pcr_signal      = f"PCR={pcr:.2f} SLIGHTLY BULLISH"
            elif 0.7 <= pcr < 0.85:
                scores.range_score    += 1
                scores.pcr_signal      = f"PCR={pcr:.2f} SLIGHTLY BEARISH"
            elif pcr > 1.3:
                scores.trending_score += 1
                scores.pcr_signal      = f"PCR={pcr:.2f} EXTREME BULLISH → reversal watch"
            elif pcr < 0.7:
                scores.trending_score += 1
                scores.pcr_signal      = f"PCR={pcr:.2f} EXTREME BEARISH → reversal watch"
        else:
            scores.pcr_signal = "PCR data unavailable"

        # ── Produce Final Plan ───────────────────────────────────────────
        plan = self._build_plan(scores, is_reeval)
        plan.produced_at = datetime.now(IST).isoformat()

        with self._lock:
            self._plan = plan

        # Save to disk so dashboard can read it
        self._save_plan(plan)

        # Log summary
        logger.info("\n" + plan.summary())

    def _build_plan(self, scores: RegimeScore, is_reeval: bool) -> SessionPlan:
        regime     = scores.winner
        confidence = scores.confidence
        notes      = []

        # Determine trending direction if trending
        trending_direction = ""
        if regime == "trending":
            spot = self._fetch_nifty_spot()
            if spot and self._or_high > 0:
                if spot > self._or_high:
                    trending_direction = "bull"
                elif spot < self._or_low:
                    trending_direction = "bear"
                else:
                    trending_direction = "bull"  # Default

        # Strategy activation based on regime + confidence
        if confidence == "LOW" and (self._or_high is not None and self._or_high > 0):
            survivor_active = False
            wave_active     = False
            notes.append("LOW confidence — all strategies BLOCKED until 10:00 AM re-eval")
        elif confidence == "LOW":
            # OR data not collected — use regime score directly, default to range
            confidence = "MEDIUM"
            survivor_active = True
            wave_active     = False
            notes.append("OR data unavailable — defaulting to RANGE MEDIUM, Survivor ACTIVE")
        elif regime == "range":
            survivor_active = True
            wave_active     = False
            notes.append("RANGE confirmed — Survivor ACTIVE, Wave BLOCKED")
        else:  # trending
            survivor_active = False
            wave_active     = True
            notes.append(f"TRENDING {trending_direction.upper()} confirmed — Wave ACTIVE, Survivor BLOCKED")

        # Dynamic parameters based on regime + VIX + confidence
        vix    = scores.vix_level or 17.0
        params = self._build_params(regime, confidence, vix, trending_direction)

        # Dynamic risk budget
        daily_loss_limit = self._calc_daily_loss(confidence, vix)
        max_capital      = self._calc_max_capital(confidence, vix)

        # Medium confidence — reduce position size
        if confidence == "MEDIUM":
            params.position_size = 0.75
            notes.append("MEDIUM confidence — position size reduced to 75%")

        # Low VIX bonus — tighter triggers
        if vix < VIX_LOW and regime == "range":
            notes.append(f"Low VIX ({vix:.1f}) — tighter triggers applied")

        # High VIX warning
        if vix > VIX_HIGH:
            notes.append(f"⚠️ High VIX ({vix:.1f}) — elevated risk, reduced capital")

        with self._lock:
            prev_version = self._plan.plan_version if self._plan else 0

        return SessionPlan(
            regime              = regime,
            confidence          = confidence,
            trending_direction  = trending_direction,
            survivor_active     = survivor_active,
            wave_active         = wave_active,
            daily_loss_limit    = daily_loss_limit,
            max_capital         = max_capital,
            params              = params,
            scores              = scores,
            plan_version        = prev_version + 1 if is_reeval else 1,
            notes               = notes,
            is_ready            = True,
            low_confidence_hold_until = (
                datetime.now(IST).replace(hour=10, minute=0).isoformat()
                if confidence == "LOW" else None
            ),
        )

    def _build_params(
        self,
        regime: str,
        confidence: str,
        vix: float,
        direction: str
    ) -> StrategyParams:
        p = StrategyParams()

        if regime == "range":
            # Tighter triggers in low VIX (more trades), wider in high VIX (safer)
            if vix < VIX_LOW:
                p.pe_gap        = 5.0
                p.ce_gap        = 5.0
                p.pe_symbol_gap = 300.0
                p.ce_symbol_gap = 300.0
                p.min_premium   = 12.0
            elif vix < VIX_NORMAL:
                p.pe_gap        = 6.0
                p.ce_gap        = 6.0
                p.pe_symbol_gap = 350.0
                p.ce_symbol_gap = 350.0
                p.min_premium   = 15.0
            else:
                p.pe_gap        = 8.0
                p.ce_gap        = 8.0
                p.pe_symbol_gap = 400.0
                p.ce_symbol_gap = 400.0
                p.min_premium   = 20.0

        else:  # trending
            # Tighter Wave brackets in high VIX (more movement)
            if vix < VIX_NORMAL:
                p.sell_gap     = 20.0
                p.buy_gap      = 20.0
                p.cool_off_time = 5.0
            else:
                p.sell_gap     = 15.0
                p.buy_gap      = 15.0
                p.cool_off_time = 3.0

        # Reduce size if LOW confidence (already handled in build_plan)
        if confidence == "HIGH":
            p.position_size = 1.0
        elif confidence == "MEDIUM":
            p.position_size = 0.75
        else:
            p.position_size = 0.0  # Blocked

        return p

    def _calc_daily_loss(self, confidence: str, vix: float) -> float:
        """Dynamic daily loss limit based on confidence and VIX."""
        base = -5000.0

        if confidence == "LOW":
            return -2000.0   # Minimal risk on uncertain days
        elif confidence == "MEDIUM":
            base = -3500.0
        else:
            base = -5000.0

        # Reduce limit further on high-VIX days
        if vix > VIX_HIGH:
            base = base * 0.6   # 40% reduction on very high VIX days
        elif vix > VIX_NORMAL:
            base = base * 0.8   # 20% reduction on elevated VIX days

        return round(base, 0)

    def _calc_max_capital(self, confidence: str, vix: float) -> float:
        """Dynamic max capital based on confidence and VIX."""
        if confidence == "LOW":
            return 0.0         # No capital deployed on uncertain days
        elif confidence == "MEDIUM":
            base = 80000.0
        else:
            base = 150000.0

        if vix > VIX_HIGH:
            base = base * 0.5
        elif vix > VIX_NORMAL:
            base = base * 0.7

        return round(base, 0)

    # ── Data Fetchers ──────────────────────────────────────────────────────

    def _fetch_nifty_spot(self) -> Optional[float]:
        token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
        if not token:
            return None
        try:
            url     = "https://api.upstox.com/v2/market-quote/quotes"
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            params  = {"instrument_key": NIFTY_KEY}
            resp    = requests.get(url, headers=headers, params=params, timeout=5)
            resp.raise_for_status()
            data = resp.json().get("data", {})
            key  = list(data.keys())[0] if data else None
            if key:
                return float(data[key].get("last_price", 0))
        except Exception as e:
            logger.debug(f"[SessionPlanner] Spot fetch error: {e}")
        return None

    def _fetch_gap_data(self):
        """Returns (prev_close, today_open) tuple."""
        token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
        if not token:
            return None, None
        try:
            url     = "https://api.upstox.com/v2/market-quote/ohlc"
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            params  = {"instrument_key": NIFTY_KEY, "interval": "1d"}
            resp    = requests.get(url, headers=headers, params=params, timeout=5)
            resp.raise_for_status()
            data = resp.json().get("data", {})
            key  = list(data.keys())[0] if data else None
            if key:
                ohlc       = data[key].get("ohlc", {})
                prev_close = float(ohlc.get("close", 0))
                today_open = float(ohlc.get("open", 0))
                return prev_close, today_open
        except Exception as e:
            logger.debug(f"[SessionPlanner] Gap data fetch error: {e}")
        return None, None

    def _fetch_vix(self) -> Optional[float]:
        """Fetch India VIX from Upstox."""
        token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
        if not token:
            return None
        try:
            url     = "https://api.upstox.com/v2/market-quote/quotes"
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            params  = {"instrument_key": "NSE_INDEX|India VIX"}
            resp    = requests.get(url, headers=headers, params=params, timeout=5)
            resp.raise_for_status()
            data = resp.json().get("data", {})
            key  = list(data.keys())[0] if data else None
            if key:
                return float(data[key].get("last_price", 0))
        except Exception as e:
            logger.debug(f"[SessionPlanner] VIX fetch error: {e}")
        return None

    def _fetch_pcr(self) -> Optional[float]:
        """Fetch current PCR from market_context if available."""
        try:
            from core.market_context import market_context
            pcr = market_context.pcr
            if pcr and pcr > 0:
                return pcr
        except Exception:
            pass
        return None

    # ── Persistence ────────────────────────────────────────────────────────

    def _save_plan(self, plan: SessionPlan):
        """Save session plan to disk for dashboard and other processes."""
        try:
            self._plan_path.parent.mkdir(exist_ok=True)
            with open(self._plan_path, "w") as f:
                json.dump(plan.to_dict(), f, indent=2, default=str)
            logger.info(f"[SessionPlanner] Plan saved to {self._plan_path}")
        except Exception as e:
            logger.error(f"[SessionPlanner] Failed to save plan: {e}")

    @staticmethod
    def load_plan_from_disk() -> Optional[SessionPlan]:
        """Load last saved plan from disk (useful for dashboard startup)."""
        path = Path("configs/session_plan.json")
        try:
            if path.exists():
                with open(path) as f:
                    d = json.load(f)
                scores = RegimeScore(**d.get("scores", {}))
                params = StrategyParams(**d.get("params", {}))
                plan   = SessionPlan(
                    regime              = d.get("regime", "range"),
                    confidence          = d.get("confidence", "LOW"),
                    trending_direction  = d.get("trending_direction", ""),
                    survivor_active     = d.get("survivor_active", False),
                    wave_active         = d.get("wave_active", False),
                    daily_loss_limit    = d.get("daily_loss_limit", -3000.0),
                    max_capital         = d.get("max_capital", 150000.0),
                    params              = params,
                    scores              = scores,
                    plan_version        = d.get("plan_version", 1),
                    notes               = d.get("notes", []),
                    is_ready            = d.get("is_ready", False),
                    produced_at         = d.get("produced_at"),
                )
                return plan
        except Exception as e:
            logger.warning(f"[SessionPlanner] Could not load plan from disk: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# Singleton
# ═══════════════════════════════════════════════════════════════════════════════
session_planner = SessionPlannerEngine()


# ═══════════════════════════════════════════════════════════════════════════════
# Quick test (run directly)
# ═══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    print("Starting SessionPlanner in test mode...")
    print("Will force-produce a plan immediately for testing.\n")

    session_planner._or_high = 24050.0
    session_planner._or_low  = 24010.0
    session_planner._midpoint_crosses = 2

    session_planner._produce_plan(is_reeval=False)
    plan = session_planner.current_plan
    print("\n" + plan.summary())