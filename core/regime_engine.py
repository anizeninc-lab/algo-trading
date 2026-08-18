# core/regime_engine.py
"""
Institutional-grade regime classifier for Nifty intraday options trading.

Market Score: -100 (strong bear) to +100 (strong bull)
  >= +60  → TRENDING_BULL
  <= -60  → TRENDING_BEAR
  else    → RANGE

Signals (each contributes to score):
  VWAP position        ±25
  EMA structure        ±20
  OR breakout          ±20
  ADX trend strength   +15 (directional, added to winner)
  OI confirmation      ±10 (confirmation only)
  PCR extreme          flags only (does not override trend)

Trend persistence: 2 consecutive classifications required before regime changes.
"""

import logging
import os
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import requests

logger = logging.getLogger(__name__)

IST_OFFSET = 19800  # seconds

# ── Thresholds ─────────────────────────────────────────────────────────────
BULL_SCORE_THRESHOLD       =  60   # strong bull trend
BEAR_SCORE_THRESHOLD       = -60   # strong bear trend
# ── Hysteresis exit thresholds ──────────────────────────────────────────
# Entering trending_bull/bear requires score to reach the entry threshold
# above (with TREND_CONFIRM_COUNT persistence). But exiting requires a
# deeper retracement than the entry line — otherwise a score oscillating
# just above/below the entry threshold (e.g. 58 <-> 62) flips the regime
# on every other reading, since a single dip below entry previously reset
# the trend counters to 0 with no persistence required on the way down.
BULL_EXIT_THRESHOLD        =  45   # must fall below this to exit trending_bull
BEAR_EXIT_THRESHOLD        = -45   # must rise above this to exit trending_bear
WEAK_BULL_SCORE_THRESHOLD  =  30   # weak bull (above range, below strong trend)
WEAK_BEAR_SCORE_THRESHOLD  = -30   # weak bear
TREND_CONFIRM_COUNT        =  2    # consecutive hits before regime flips
REGIME_STABILITY_CONFIRM   =  4    # consecutive identical readings required before clearing the transition-block
ADX_TREND_THRESHOLD        =  20
ADX_RANGE_THRESHOLD        =  18
OR_BREAKOUT_POINTS         =  30   # fallback if ATR unavailable
VWAP_SLOPE_LOOKBACK        =  5    # candles to measure VWAP slope


@dataclass
class Candle:
    ts:    str
    open:  float
    high:  float
    low:   float
    close: float


@dataclass
class RegimeSignals:
    market_score:    float = 0.0
    vwap:            float = 0.0
    vwap_slope:      float = 0.0
    ema20:           float = 0.0
    ema50:           float = 0.0
    adx:             float = 0.0
    pdi:             float = 0.0
    ndi:             float = 0.0
    adx_bull:        bool  = False
    adx_bear:        bool  = False
    atr:             float = 0.0
    or_breakout_pts: float = 0.0
    or_high:         float = 0.0
    or_low:          float = 0.0
    spot:            float = 0.0
    above_vwap:      bool  = False
    ema_bullish:     bool  = False
    or_bull_break:   bool  = False
    or_bear_break:   bool  = False
    adx_trending:    bool  = False
    pcr_spike:       bool  = False
    reversal_risk:   bool  = False
    oi_bull_confirm:   bool  = False
    oi_bear_confirm:   bool  = False
    trend_exhaustion:  bool  = False  # True when trend may be running out of steam
    gap_pct:           float = 0.0    # Opening gap % vs previous close (+ = gap up)
    gap_up:            bool  = False  # True if gap up > 0.3%
    gap_down:          bool  = False  # True if gap down > 0.3%
    confidence:        float = 0.0    # 0-100 confidence in current regime classification
    confidence_label:  str   = "LOW"  # LOW / MEDIUM / HIGH
    prev_day_high:          float = 0.0
    prev_day_low:           float = 0.0
    prev_day_breakout_bull: bool  = False
    prev_day_breakout_bear: bool  = False
    pe_support_migrating_up:      bool = False
    pe_support_migrating_down:    bool = False
    ce_resistance_migrating_up:   bool = False
    ce_resistance_migrating_down: bool = False
    swing_structure_bullish:      bool = False
    swing_structure_bearish:      bool = False
    regime_transitioning:   bool  = False


class RegimeEngine:
    """
    Stateful regime classifier. Call classify() every 30s during market hours.
    """

    def __init__(self):
        self._bull_count:    int = 0
        self._bear_count:    int = 0
        self._last_regime:   str = "range"
        self._signals:       RegimeSignals = RegimeSignals()
        self._ce_oi_history:   List[float] = []  # rolling OI deltas for smoothing
        self._pe_oi_history:   List[float] = []
        self._OI_SMOOTH_PERIODS = 3
        self._regime_history: List[dict] = []  # last 10 regime classifications
        self._HISTORY_MAX = 10
        self._stable_since_count: int = 0  # consecutive classify() calls with unchanged regime since last flip
        self._load_state()

    def _save_state(self) -> None:
        """Persist trend-confirmation counters so a restart doesn't erase
        hysteresis progress mid-session (mirrors risk_manager.py's pattern)."""
        try:
            import json, os
            from pathlib import Path
            from datetime import datetime
            import pytz
            state_file = Path("configs/regime_state.json")
            now = datetime.now(pytz.timezone("Asia/Kolkata"))
            state = {
                "date":        now.strftime("%Y-%m-%d"),
                "bull_count":  self._bull_count,
                "bear_count":  self._bear_count,
                "last_regime": self._last_regime,
            }
            state_file.parent.mkdir(exist_ok=True)
            tmp_path = state_file.with_suffix(".json.tmp")
            with open(tmp_path, "w") as f:
                json.dump(state, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, state_file)
        except Exception as e:
            logger.warning(f"[regime_engine] Failed to save state: {e}")

    def _load_state(self) -> None:
        """Reload persisted trend-confirmation counters on startup, if from today."""
        try:
            import json
            from pathlib import Path
            from datetime import datetime
            import pytz
            state_file = Path("configs/regime_state.json")
            if not state_file.exists():
                return
            with open(state_file) as f:
                state = json.load(f)
            now = datetime.now(pytz.timezone("Asia/Kolkata"))
            today = now.strftime("%Y-%m-%d")
            if state.get("date") != today:
                logger.info("[regime_engine] State file is from previous day — ignoring")
                return
            self._bull_count  = state.get("bull_count", 0)
            self._bear_count  = state.get("bear_count", 0)
            self._last_regime = state.get("last_regime", "range")
            logger.info(
                f"[regime_engine] Restored state — bull_count={self._bull_count} "
                f"bear_count={self._bear_count} last_regime={self._last_regime}"
            )
        except Exception as e:
            logger.warning(f"[regime_engine] Failed to load state: {e}")

    # ── Public ─────────────────────────────────────────────────────────────

    def classify(
        self,
        candles:      List[Candle],
        or_high:      float,
        or_low:       float,
        spot:         float,
        pcr:          float,
        ce_oi_delta:  float,
        pe_oi_delta:  float,
        pcr_spike:    bool,
        prev_close:   float = 0.0,  # previous day close for gap detection
        prev_day_high: float = 0.0,  # previous day high — spot-structure breakout signal
        prev_day_low:  float = 0.0,  # previous day low — spot-structure breakout signal
        pe_support_migrating_up:      bool = False,
        pe_support_migrating_down:    bool = False,
        ce_resistance_migrating_up:   bool = False,
        ce_resistance_migrating_down: bool = False,
    ) -> Tuple[str, RegimeSignals]:
        """
        Returns (regime_string, RegimeSignals).
        regime_string: 'trending_bull' | 'trending_bear' | 'range' | 'reversal_watch'
        """
        if len(candles) < 5:
            return self._last_regime, self._signals

        previous_regime = self._last_regime  # captured before reassignment, for transition detection

        closes = [c.close for c in candles]
        highs  = [c.high  for c in candles]
        lows   = [c.low   for c in candles]

        # ── Gap Detection ─────────────────────────────────────────────
        gap_pct = 0.0
        gap_up = gap_down = False
        if prev_close > 0 and len(candles) > 0:
            session_open = candles[0].open
            gap_pct = round((session_open - prev_close) / prev_close * 100, 3)
            gap_up   = gap_pct >  0.3   # gap up > 0.3%
            gap_down = gap_pct < -0.3   # gap down > 0.3%
            if gap_up or gap_down:
                logger.info(f"[regime_engine] Gap detected: {gap_pct:+.2f}% | open={session_open:.1f} prev_close={prev_close:.1f}")

        # ATR-based breakout threshold — adapts to current volatility
        atr = self._calc_atr(highs, lows, closes, 14)
        or_breakout_pts = max(20.0, round(atr * 1.5, 1))  # min 20pts, scales with ATR

        vwap       = self._calc_vwap(candles)
        vwap_slope = self._calc_vwap_slope(candles)
        ema20      = self._calc_ema(closes, 20)
        ema50      = self._calc_ema(closes, 50)
        adx, pdi, ndi = self._calc_adx_directional(highs, lows, closes, 14)

        score = 0.0

        # ── VWAP (±25 points) ──────────────────────────────────────────
        above_vwap = spot > vwap
        if above_vwap and vwap_slope > 0:
            score += 25
        elif not above_vwap and vwap_slope < 0:
            score -= 25
        elif above_vwap:
            score += 12
        else:
            score -= 12

        # ── EMA Structure (±20 points) ─────────────────────────────────
        ema_bullish = ema20 > ema50
        if ema_bullish:
            score += 20
        else:
            score -= 20

        # ── OR Breakout (±20 points) ───────────────────────────────────
        or_bull_break = spot > (or_high + or_breakout_pts)
        or_bear_break = spot < (or_low  - or_breakout_pts)
        if or_bull_break:
            score += 20
        elif or_bear_break:
            score -= 20
        elif spot > or_high:
            score += 10
        elif spot < or_low:
            score -= 10

        # ── ADX Trend Strength (+15 directional) ──────────────────────
        # Use +DI/-DI for direction, ADX for strength
        adx, pdi, ndi = self._calc_adx_directional(highs, lows, closes, 14)
        adx_trending = adx > ADX_TREND_THRESHOLD
        adx_bull = adx_trending and pdi > ndi   # +DI above -DI = bullish trend
        adx_bear = adx_trending and ndi > pdi   # -DI above +DI = bearish trend
        if adx_bull:
            score += 15
        elif adx_bear:
            score -= 15

        # ── OI Confirmation (±10 points — confirmation only) ──────────
        # Smooth OI deltas over last 3 periods to prevent sign-flip noise
        self._ce_oi_history.append(ce_oi_delta)
        self._pe_oi_history.append(pe_oi_delta)
        if len(self._ce_oi_history) > self._OI_SMOOTH_PERIODS:
            self._ce_oi_history.pop(0)
        if len(self._pe_oi_history) > self._OI_SMOOTH_PERIODS:
            self._pe_oi_history.pop(0)
        smooth_ce_delta = sum(self._ce_oi_history) / len(self._ce_oi_history)
        smooth_pe_delta = sum(self._pe_oi_history) / len(self._pe_oi_history)

        oi_bull = smooth_pe_delta > 0 and smooth_ce_delta < 0  # PE building, CE covering
        oi_bear = smooth_ce_delta > 0 and smooth_pe_delta < 0  # CE building, PE covering
        oi_bull_confirm = oi_bull
        oi_bear_confirm = oi_bear
        if oi_bull:
            score += 10
        elif oi_bear:
            score -= 10

        # ── OI Migration (±10 points) — support/resistance strike movement ──
        if pe_support_migrating_up:
            score += 5
        elif pe_support_migrating_down:
            score -= 5
        if ce_resistance_migrating_down:
            score += 5
        elif ce_resistance_migrating_up:
            score -= 5

        # ── Previous Day High/Low Breakout (±15 points) — pure spot structure ──
        prev_day_breakout_bull = prev_day_high > 0 and spot > prev_day_high
        prev_day_breakout_bear = prev_day_low  > 0 and spot < prev_day_low
        if prev_day_breakout_bull:
            score += 15
        elif prev_day_breakout_bear:
            score -= 15

        # ── Swing Structure (±15 points) — HH-HL / LH-LL price action ──
        swing_structure_bullish, swing_structure_bearish = self._detect_swing_structure(highs, lows)
        if swing_structure_bullish:
            score += 15
        elif swing_structure_bearish:
            score -= 15

        # ── Trend Exhaustion Detection ────────────────────────────────
        # Flag when trend may be overextended:
        # 1. Price too far from VWAP (> 2x ATR)
        # 2. ADX was trending but now weakening (below 25 after being above 30)
        vwap_distance = abs(spot - vwap)
        price_extended = vwap_distance > (atr * 2.0) if atr > 0 else False
        adx_weak_after_trend = adx_trending and adx < 25  # was trending, now weakening
        trend_exhaustion = price_extended or adx_weak_after_trend

        # ── Persistence gate (with hysteresis) ─────────────────────────
        # Entry: score must reach the strong threshold to start counting.
        # Once already trending, stay counted-in until score falls past
        # the more lenient EXIT threshold — not the entry line — so a
        # score wobbling just above/below entry doesn't flap the regime.
        already_trending_bull = previous_regime == "trending_bull"
        already_trending_bear = previous_regime == "trending_bear"

        if score >= BULL_SCORE_THRESHOLD:
            self._bull_count += 1
            self._bear_count  = 0
        elif already_trending_bull and score >= BULL_EXIT_THRESHOLD:
            # Still above exit floor — hold the existing trend count, don't reset
            self._bear_count = 0
        elif score <= BEAR_SCORE_THRESHOLD:
            self._bear_count += 1
            self._bull_count  = 0
        elif already_trending_bear and score <= BEAR_EXIT_THRESHOLD:
            # Still below exit ceiling — hold the existing trend count, don't reset
            self._bull_count = 0
        else:
            # Genuinely weak signal — below exit floor/ceiling, safe to reset
            self._bull_count  = 0
            self._bear_count  = 0

        # ── Regime decision ───────────────────────────────────────────
        self._save_state()
        if self._bull_count >= TREND_CONFIRM_COUNT:
            regime = "trending_bull"
        elif self._bear_count >= TREND_CONFIRM_COUNT:
            regime = "trending_bear"
        elif score >= WEAK_BULL_SCORE_THRESHOLD:
            regime = "weak_bull"
        elif score <= WEAK_BEAR_SCORE_THRESHOLD:
            regime = "weak_bear"
        else:
            regime = "range"

        # reversal_watch is now a FLAG only — never overrides regime
        # This prevents the engine from blocking momentum entries on trend days
        # PCR spike or extreme PCR sets the flag but keeps the regime intact
        reversal_risk = pcr_spike or pcr > 1.5 or pcr < 0.65
        # regime stays as-is — reversal_risk is surfaced via RegimeSignals.reversal_risk

        # ── Regime Transition Detection (for entry-blocking during flips) ──
        # A bare "changed since last reading" check flaps constantly when the
        # score hovers near a threshold (e.g. trending_bull <-> weak_bull),
        # blocking entries almost permanently. Instead, require the regime to
        # be observed as unchanged for REGIME_STABILITY_CONFIRM consecutive
        # classify() calls before considering it settled again.
        if regime == previous_regime:
            self._stable_since_count += 1
        else:
            self._stable_since_count = 0
        regime_transitioning = self._stable_since_count < REGIME_STABILITY_CONFIRM

        self._last_regime = regime

        # ── Regime History ────────────────────────────────────────────
        import time as _time
        self._regime_history.append({
            "ts":         _time.strftime("%H:%M:%S"),
            "regime":     regime,
            "score":      round(score, 1),
            "confidence": confidence if "confidence" in dir() else 0.0,
        })
        if len(self._regime_history) > self._HISTORY_MAX:
            self._regime_history.pop(0)

        # ── Confidence Score (0-100) ──────────────────────────────────
        # Based on how many signals agree with the final regime
        raw_score_abs = abs(score)
        confidence = min(100.0, round(raw_score_abs / 130 * 100, 1))
        # Boost confidence if persistence counter is high
        if self._bull_count >= 2 or self._bear_count >= 2:
            confidence = min(100.0, confidence + 15)
        # Reduce confidence if trend exhaustion detected
        if trend_exhaustion:
            confidence = max(0.0, confidence - 20)
        # Reduce confidence if reversal risk
        if reversal_risk:
            confidence = max(0.0, confidence - 10)
        if confidence >= 70:
            confidence_label = "HIGH"
        elif confidence >= 40:
            confidence_label = "MEDIUM"
        else:
            confidence_label = "LOW"

        self._signals = RegimeSignals(
            market_score    = round(score, 1),
            vwap            = round(vwap, 2),
            vwap_slope      = round(vwap_slope, 4),
            ema20           = round(ema20, 2),
            ema50           = round(ema50, 2),
            adx             = round(adx, 1),
            pdi             = round(pdi, 1),
            ndi             = round(ndi, 1),
            adx_bull        = adx_bull,
            adx_bear        = adx_bear,
            atr             = round(atr, 2),
            or_breakout_pts = or_breakout_pts,
            or_high         = or_high,
            or_low          = or_low,
            spot            = spot,
            above_vwap      = above_vwap,
            ema_bullish     = ema_bullish,
            or_bull_break   = or_bull_break,
            or_bear_break   = or_bear_break,
            adx_trending    = adx_trending,
            pcr_spike       = pcr_spike,
            reversal_risk   = reversal_risk,
            oi_bull_confirm  = oi_bull_confirm,
            oi_bear_confirm  = oi_bear_confirm,
            trend_exhaustion = trend_exhaustion,
            gap_pct          = gap_pct,
            gap_up           = gap_up,
            gap_down         = gap_down,
            prev_day_high          = prev_day_high,
            prev_day_low           = prev_day_low,
            prev_day_breakout_bull = prev_day_breakout_bull,
            prev_day_breakout_bear = prev_day_breakout_bear,
            pe_support_migrating_up      = pe_support_migrating_up,
            pe_support_migrating_down    = pe_support_migrating_down,
            ce_resistance_migrating_up   = ce_resistance_migrating_up,
            ce_resistance_migrating_down = ce_resistance_migrating_down,
            swing_structure_bullish      = swing_structure_bullish,
            swing_structure_bearish      = swing_structure_bearish,
            regime_transitioning   = regime_transitioning,
            confidence       = confidence,
            confidence_label = confidence_label,
        )

        logger.info(
            f"[regime_engine] score={score:+.0f} | vwap={'↑' if above_vwap else '↓'} "
            f"| ema={'bull' if ema_bullish else 'bear'} | adx={adx:.1f} "
            f"| or={'bull' if or_bull_break else 'bear' if or_bear_break else 'inside'} "
            f"| pdh/pdl={'bull' if prev_day_breakout_bull else 'bear' if prev_day_breakout_bear else 'inside'} "
            f"| swing={'bull' if swing_structure_bullish else 'bear' if swing_structure_bearish else 'none'} "
            f"| transition={'YES' if regime_transitioning else 'no'} "
            f"| bull_count={self._bull_count} bear_count={self._bear_count} "
            f"→ {regime} [{confidence_label} {confidence:.0f}%]"
        )

        return regime, self._signals

    @property
    def signals(self) -> RegimeSignals:
        return self._signals

    @property
    def regime_history(self) -> List[dict]:
        """Returns last N regime classifications with timestamp, score, confidence."""
        return list(self._regime_history)

    def get_regime_stability(self) -> float:
        """
        Returns 0-100 stability score.
        100 = all recent regimes identical (very stable)
        0   = regime changing every poll (unstable)
        """
        if len(self._regime_history) < 2:
            return 50.0
        regimes = [h["regime"] for h in self._regime_history]
        most_common = max(set(regimes), key=regimes.count)
        return round(regimes.count(most_common) / len(regimes) * 100, 1)

    # ── Indicators ─────────────────────────────────────────────────────────

    @staticmethod
    def _calc_vwap(candles: List[Candle]) -> float:
        """
        Session VWAP using cumulative typical price method.
        Since Nifty index has no volume data, we use equal-weight cumulative TP
        from session start — this matches institutional practice for index VWAP.
        Each candle's TP is added progressively; the running average IS the VWAP.
        This is superior to simple average because it anchors to session open
        and updates progressively, just like volume-weighted VWAP would.
        """
        if not candles:
            return 0.0
        cumulative_tp = 0.0
        vwap_values = []
        for i, c in enumerate(candles):
            tp = (c.high + c.low + c.close) / 3
            cumulative_tp += tp
            vwap_values.append(cumulative_tp / (i + 1))
        return vwap_values[-1]  # current VWAP

    @staticmethod
    def _calc_vwap_series(candles: List[Candle]) -> List[float]:
        """Returns full VWAP series for slope calculation."""
        cumulative_tp = 0.0
        vwap_values = []
        for i, c in enumerate(candles):
            tp = (c.high + c.low + c.close) / 3
            cumulative_tp += tp
            vwap_values.append(cumulative_tp / (i + 1))
        return vwap_values

    @staticmethod
    def _calc_vwap_slope(candles: List[Candle], lookback: int = VWAP_SLOPE_LOOKBACK) -> float:
        """
        Slope of cumulative session VWAP over last N candles.
        Uses the VWAP series rather than raw prices for stability.
        """
        if len(candles) < lookback + 1:
            return 0.0
        cumulative_tp = 0.0
        vwap_series = []
        for i, c in enumerate(candles):
            tp = (c.high + c.low + c.close) / 3
            cumulative_tp += tp
            vwap_series.append(cumulative_tp / (i + 1))
        if len(vwap_series) < lookback + 1:
            return 0.0
        return vwap_series[-1] - vwap_series[-lookback - 1]

    @staticmethod
    def _calc_ema(closes: List[float], period: int) -> float:
        """Exponential moving average."""
        if len(closes) < period:
            return sum(closes) / len(closes)
        k   = 2 / (period + 1)
        ema = sum(closes[:period]) / period
        for price in closes[period:]:
            ema = price * k + ema * (1 - k)
        return ema

    @staticmethod
    def _calc_atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
        """Average True Range — measures current volatility."""
        if len(closes) < period + 1:
            return 0.0
        tr_list = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i-1]),
                abs(lows[i] - closes[i-1])
            )
            tr_list.append(tr)
        return sum(tr_list[-period:]) / min(len(tr_list), period)

    @staticmethod
    def _detect_swing_structure(highs: List[float], lows: List[float], k: int = 2) -> Tuple[bool, bool]:
        """
        Detects HH-HL (bullish) or LH-LL (bearish) swing structure using simple
        fractal pivots — a bar is a swing high/low only if it strictly exceeds
        all bars within k positions on both sides. Needs >=2 confirmed swings
        of each type to make a determination; returns (False, False) otherwise.
        """
        n = len(highs)
        swing_highs, swing_lows = [], []
        for i in range(k, n - k):
            left_h, right_h = highs[i-k:i], highs[i+1:i+k+1]
            if highs[i] > max(left_h + right_h):
                swing_highs.append(highs[i])
            left_l, right_l = lows[i-k:i], lows[i+1:i+k+1]
            if lows[i] < min(left_l + right_l):
                swing_lows.append(lows[i])
        bullish = bearish = False
        if len(swing_highs) >= 2 and len(swing_lows) >= 2:
            higher_high = swing_highs[-1] > swing_highs[-2]
            higher_low  = swing_lows[-1]  > swing_lows[-2]
            lower_high  = swing_highs[-1] < swing_highs[-2]
            lower_low   = swing_lows[-1]  < swing_lows[-2]
            bullish = higher_high and higher_low
            bearish = lower_high and lower_low
        return bullish, bearish

    @staticmethod
    def _calc_adx(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
        """Average Directional Index — scalar only (kept for compatibility)."""
        adx, _, _ = RegimeEngine._calc_adx_directional(highs, lows, closes, period)
        return adx

    @staticmethod
    def _calc_adx_directional(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> Tuple[float, float, float]:
        """
        Returns (ADX, +DI, -DI).
        +DI > -DI = bullish trend strength
        -DI > +DI = bearish trend strength
        ADX > 20  = trend is strong enough to act on
        """
        if len(closes) < period + 1:
            return 0.0, 0.0, 0.0
        tr_list, dm_plus, dm_minus = [], [], []
        for i in range(1, len(closes)):
            high, low, prev_close = highs[i], lows[i], closes[i-1]
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            tr_list.append(tr)
            up   = highs[i]  - highs[i-1]
            down = lows[i-1] - lows[i]
            dm_plus.append(up   if up > down and up > 0   else 0)
            dm_minus.append(down if down > up and down > 0 else 0)

        def smooth(lst, p):
            s = sum(lst[:p])
            result = [s]
            for v in lst[p:]:
                s = s - s/p + v
                result.append(s)
            return result

        atr = smooth(tr_list,  period)
        pdm = smooth(dm_plus,  period)
        ndm = smooth(dm_minus, period)

        dx_list, pdi_list, ndi_list = [], [], []
        for a, p, n in zip(atr, pdm, ndm):
            if a == 0:
                continue
            pdi = 100 * p / a
            ndi = 100 * n / a
            pdi_list.append(pdi)
            ndi_list.append(ndi)
            dx = 100 * abs(pdi - ndi) / (pdi + ndi) if (pdi + ndi) > 0 else 0
            dx_list.append(dx)

        if not dx_list:
            return 0.0, 0.0, 0.0

        adx = sum(dx_list[-period:]) / min(len(dx_list), period)
        pdi = pdi_list[-1] if pdi_list else 0.0
        ndi = ndi_list[-1] if ndi_list else 0.0
        return adx, pdi, ndi


# ── Candle fetcher ─────────────────────────────────────────────────────────

def fetch_intraday_candles(token: str, n: int = 60) -> List[Candle]:
    """Fetch last N 1-minute candles for Nifty 50 from Upstox."""
    try:
        url = "https://api.upstox.com/v2/historical-candle/intraday/NSE_INDEX%7CNifty%2050/1minute"
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        r = requests.get(url, headers=headers, timeout=5)
        data = r.json()
        if data.get("status") != "success":
            return []
        raw = data["data"]["candles"]
        candles = [
            Candle(ts=c[0], open=c[1], high=c[2], low=c[3], close=c[4])
            for c in reversed(raw)  # API returns newest first — reverse to oldest first
        ]
        return candles[-n:]
    except Exception as e:
        logger.error(f"[regime_engine] candle fetch error: {e}")
        return []


# Singleton
regime_engine = RegimeEngine()
