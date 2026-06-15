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
BULL_SCORE_THRESHOLD  =  60
BEAR_SCORE_THRESHOLD  = -60
TREND_CONFIRM_COUNT   =  2      # consecutive hits before regime flips
ADX_TREND_THRESHOLD   =  20
ADX_RANGE_THRESHOLD   =  18
OR_BREAKOUT_POINTS    =  30     # points beyond OR to confirm breakout
VWAP_SLOPE_LOOKBACK   =  5      # candles to measure VWAP slope


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
    oi_bull_confirm: bool  = False
    oi_bear_confirm: bool  = False


class RegimeEngine:
    """
    Stateful regime classifier. Call classify() every 30s during market hours.
    """

    def __init__(self):
        self._bull_count:  int = 0
        self._bear_count:  int = 0
        self._last_regime: str = "range"
        self._signals:     RegimeSignals = RegimeSignals()

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
    ) -> Tuple[str, RegimeSignals]:
        """
        Returns (regime_string, RegimeSignals).
        regime_string: 'trending_bull' | 'trending_bear' | 'range' | 'reversal_watch'
        """
        if len(candles) < 5:
            return self._last_regime, self._signals

        closes = [c.close for c in candles]
        highs  = [c.high  for c in candles]
        lows   = [c.low   for c in candles]

        vwap       = self._calc_vwap(candles)
        vwap_slope = self._calc_vwap_slope(candles)
        ema20      = self._calc_ema(closes, 20)
        ema50      = self._calc_ema(closes, 50)
        adx        = self._calc_adx(highs, lows, closes, 14)

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
        or_bull_break = spot > (or_high + OR_BREAKOUT_POINTS)
        or_bear_break = spot < (or_low  - OR_BREAKOUT_POINTS)
        if or_bull_break:
            score += 20
        elif or_bear_break:
            score -= 20
        elif spot > or_high:
            score += 10
        elif spot < or_low:
            score -= 10

        # ── ADX Trend Strength (+15 directional) ──────────────────────
        adx_trending = adx > ADX_TREND_THRESHOLD
        if adx_trending:
            score += 15 if score > 0 else -15  # amplifies the dominant direction

        # ── OI Confirmation (±10 points — confirmation only) ──────────
        oi_bull = pe_oi_delta > 0 and ce_oi_delta < 0   # PE building, CE covering
        oi_bear = ce_oi_delta > 0 and pe_oi_delta < 0   # CE building, PE covering
        oi_bull_confirm = oi_bull
        oi_bear_confirm = oi_bear
        if oi_bull:
            score += 10
        elif oi_bear:
            score -= 10

        # ── Persistence gate ──────────────────────────────────────────
        if score >= BULL_SCORE_THRESHOLD:
            self._bull_count += 1
            self._bear_count  = 0
        elif score <= BEAR_SCORE_THRESHOLD:
            self._bear_count += 1
            self._bull_count  = 0
        else:
            self._bull_count  = 0
            self._bear_count  = 0

        # ── Regime decision ───────────────────────────────────────────
        if self._bull_count >= TREND_CONFIRM_COUNT:
            regime = "trending_bull"
        elif self._bear_count >= TREND_CONFIRM_COUNT:
            regime = "trending_bear"
        else:
            regime = "range"

        # PCR spike → reversal_watch flag (does NOT override strong trend)
        reversal_risk = pcr_spike or pcr > 1.5 or pcr < 0.65
        if reversal_risk and regime == "range":
            regime = "reversal_watch"

        self._last_regime = regime

        self._signals = RegimeSignals(
            market_score    = round(score, 1),
            vwap            = round(vwap, 2),
            vwap_slope      = round(vwap_slope, 4),
            ema20           = round(ema20, 2),
            ema50           = round(ema50, 2),
            adx             = round(adx, 1),
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
            oi_bull_confirm = oi_bull_confirm,
            oi_bear_confirm = oi_bear_confirm,
        )

        logger.info(
            f"[regime_engine] score={score:+.0f} | vwap={'↑' if above_vwap else '↓'} "
            f"| ema={'bull' if ema_bullish else 'bear'} | adx={adx:.1f} "
            f"| or={'bull' if or_bull_break else 'bear' if or_bear_break else 'inside'} "
            f"| bull_count={self._bull_count} bear_count={self._bear_count} "
            f"→ {regime}"
        )

        return regime, self._signals

    @property
    def signals(self) -> RegimeSignals:
        return self._signals

    # ── Indicators ─────────────────────────────────────────────────────────

    @staticmethod
    def _calc_vwap(candles: List[Candle]) -> float:
        """Session VWAP from candle typical prices (no volume available from index)."""
        tp_sum = sum((c.high + c.low + c.close) / 3 for c in candles)
        return tp_sum / len(candles)

    @staticmethod
    def _calc_vwap_slope(candles: List[Candle], lookback: int = VWAP_SLOPE_LOOKBACK) -> float:
        """Slope of VWAP over last N candles. Positive = rising."""
        if len(candles) < lookback + 1:
            return 0.0
        recent = candles[-lookback:]
        older  = candles[-lookback*2:-lookback] if len(candles) >= lookback*2 else candles[:lookback]
        vwap_now  = sum((c.high + c.low + c.close) / 3 for c in recent)  / len(recent)
        vwap_prev = sum((c.high + c.low + c.close) / 3 for c in older)   / len(older)
        return vwap_now - vwap_prev

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
    def _calc_adx(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
        """Average Directional Index."""
        if len(closes) < period + 1:
            return 0.0
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

        atr   = smooth(tr_list,  period)
        pdm   = smooth(dm_plus,  period)
        ndm   = smooth(dm_minus, period)
        dx_list = []
        for a, p, n in zip(atr, pdm, ndm):
            if a == 0:
                continue
            pdi = 100 * p / a
            ndi = 100 * n / a
            dx  = 100 * abs(pdi - ndi) / (pdi + ndi) if (pdi + ndi) > 0 else 0
            dx_list.append(dx)
        if not dx_list:
            return 0.0
        return sum(dx_list[-period:]) / min(len(dx_list), period)


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
