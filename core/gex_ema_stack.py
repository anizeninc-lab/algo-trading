# core/gex_ema_stack.py
#
# EMA(9/21/50) stack computation on 5-min AND 15-min timeframes, per
# NIFTY_GEX_STRATEGY_SPEC.md section 3. This is deliberately separate from
# regime_engine.classify() -- that's a general-purpose multi-signal regime
# scorer (VWAP/ADX/OI/swing structure); this module implements the specific,
# narrower Ninja-GEX-method EMA stack gate the spec calls for. Reuses
# RegimeEngine's static indicator math (_calc_ema, _calc_atr) rather than
# reimplementing it.
#
# KNOWN STRUCTURAL CONSTRAINT (confirmed while building this):
#   market_context._session_candles is 1-min and SESSION-ANCHORED -- it
#   resets to empty every day. 15-min EMA-50 needs 50*15 = 750 minutes
#   (12.5 hours) of history, which no single session (~375 min) can ever
#   supply on its own. Without seeding, 15-min EMA-50 would silently fall
#   back to a simple average of whatever's available (RegimeEngine._calc_ema's
#   existing behavior when len(closes) < period) until day 2+ of accumulated
#   history -- which is a materially different, weaker signal than a real
#   EMA-50, not a rounding error. seed_historical_15min_candles() fixes this
#   with a one-time-per-day backfill, mirroring
#   market_context._fetch_previous_day_levels()'s existing pattern.
#
# ASSUMPTION FLAGGED, NOT SILENTLY DECIDED:
#   Spec section 3 requires EMA stack "meaningful spacing (not clustered)"
#   but does not define the threshold. Default here: the gap between EMA9
#   and EMA50 must exceed SPACING_ATR_MULTIPLE * ATR(14) on that timeframe.
#   This is a tunable, not a fixed rule -- revisit once real paper-trade
#   data shows whether it's too strict/loose.

import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import List, Optional

from core.regime_engine import Candle, RegimeEngine

logger = logging.getLogger(__name__)

SPACING_ATR_MULTIPLE = 0.15   # tunable -- see module docstring
EMA_PERIODS = (9, 21, 50)


@dataclass
class EmaStackResult:
    timeframe:   str             # "5min" or "15min"
    ema9:        float
    ema21:       float
    ema50:       float
    stack:       str             # "bullish" | "bearish" | "mixed"
    spacing_ok:  bool            # False = EMAs too clustered to trust direction
    candle_count: int            # how many candles fed the calc (low count = low confidence)


@dataclass
class MultiTimeframeStackResult:
    tf_5min:   Optional[EmaStackResult]
    tf_15min:  Optional[EmaStackResult]
    agreement: bool              # True only if both timeframes agree AND both spacing_ok
    direction: str               # "bullish" | "bearish" | "none"


def resample_candles(candles_1min: List[Candle], minutes: int) -> List[Candle]:
    """
    Groups sequential 1-min candles into `minutes`-sized buckets by simple
    positional grouping (session-anchored candles are already contiguous
    and gap-free during market hours, so index-based bucketing is safe here
    -- no need to parse/align on wall-clock boundaries).
    """
    if not candles_1min:
        return []
    out = []
    for i in range(0, len(candles_1min), minutes):
        chunk = candles_1min[i:i + minutes]
        if not chunk:
            continue
        out.append(Candle(
            ts=chunk[0].ts,
            open=chunk[0].open,
            high=max(c.high for c in chunk),
            low=min(c.low for c in chunk),
            close=chunk[-1].close,
        ))
    return out


def seed_historical_15min_candles(access_token: str, days_back: int = 10) -> List[Candle]:
    """
    One-time-per-day backfill of prior trading days' 15-min candles for
    Nifty spot, via the Historical Candle Data V3 API -- gives 15-min
    EMA-50 real cross-session continuity from session start, instead of
    silently degrading to a simple average until enough intraday history
    accumulates.

    Fetches `days_back` calendar days back (comfortably covers >=50 15-min
    candles even accounting for weekends/holidays reducing actual trading
    days) and returns them oldest-first, ready to prepend to today's
    resampled session candles.
    """
    try:
        import upstox_client
        cfg = upstox_client.Configuration()
        cfg.access_token = access_token
        client = upstox_client.ApiClient(cfg)
        history_api = upstox_client.HistoryV3Api(client)

        today = date.today()
        from_date = (today - timedelta(days=days_back)).strftime("%Y-%m-%d")
        to_date = (today - timedelta(days=1)).strftime("%Y-%m-%d")  # exclude today -- session data covers today

        resp = history_api.get_historical_candle_data1(
            "NSE_INDEX|Nifty 50", "minutes", "15", to_date, from_date
        )
        raw = resp.data.candles if resp and resp.data else []
        if not raw:
            logger.warning("[gex_ema_stack] Historical 15-min seed returned no candles")
            return []

        candles = [
            Candle(ts=c[0], open=c[1], high=c[2], low=c[3], close=c[4])
            for c in reversed(raw)  # API returns newest-first, per confirmed shape
        ]
        logger.info(f"[gex_ema_stack] Seeded {len(candles)} historical 15-min candles ({from_date} to {to_date})")
        return candles
    except Exception as e:
        logger.error(f"[gex_ema_stack] Historical 15-min seed failed: {e}")
        return []


def compute_ema_stack(candles: List[Candle], timeframe_label: str) -> Optional[EmaStackResult]:
    """
    Computes EMA(9/21/50) on the given candle series and classifies the
    stack. Returns None if there isn't even enough data for the shortest
    EMA (9) -- anything less isn't a signal, it's noise.
    """
    if len(candles) < EMA_PERIODS[0]:
        logger.warning(
            f"[gex_ema_stack] Only {len(candles)} {timeframe_label} candles -- "
            f"insufficient even for EMA9, cannot compute stack"
        )
        return None

    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]

    ema9 = RegimeEngine._calc_ema(closes, 9)
    ema21 = RegimeEngine._calc_ema(closes, 21)
    ema50 = RegimeEngine._calc_ema(closes, 50)
    atr = RegimeEngine._calc_atr(highs, lows, closes, 14)

    if ema9 > ema21 > ema50:
        stack = "bullish"
    elif ema50 > ema21 > ema9:
        stack = "bearish"
    else:
        stack = "mixed"

    spacing = abs(ema9 - ema50)
    spacing_ok = spacing > (atr * SPACING_ATR_MULTIPLE) if atr > 0 else False

    if len(candles) < EMA_PERIODS[2]:
        logger.info(
            f"[gex_ema_stack] {timeframe_label}: only {len(candles)} candles, "
            f"< EMA50 period -- EMA50 is a simple-average approximation, not a true EMA"
        )

    return EmaStackResult(
        timeframe=timeframe_label,
        ema9=round(ema9, 2),
        ema21=round(ema21, 2),
        ema50=round(ema50, 2),
        stack=stack,
        spacing_ok=spacing_ok,
        candle_count=len(candles),
    )


def get_multi_timeframe_stack(session_candles_1min: List[Candle],
                               historical_15min_seed: List[Candle] = None) -> MultiTimeframeStackResult:
    """
    Per spec section 3: requires 15-min AND 5-min agreement before allowing
    entry. Builds both timeframes from the live session 1-min series;
    15-min is prepended with a historical seed (if provided) for real
    cross-session EMA-50 continuity.
    """
    candles_5min = resample_candles(session_candles_1min, 5)
    tf_5min = compute_ema_stack(candles_5min, "5min")

    candles_15min_today = resample_candles(session_candles_1min, 15)
    seed = historical_15min_seed or []
    candles_15min = seed + candles_15min_today
    tf_15min = compute_ema_stack(candles_15min, "15min")

    agreement = False
    direction = "none"
    if tf_5min and tf_15min and tf_5min.spacing_ok and tf_15min.spacing_ok:
        if tf_5min.stack == tf_15min.stack and tf_5min.stack in ("bullish", "bearish"):
            agreement = True
            direction = tf_5min.stack

    return MultiTimeframeStackResult(
        tf_5min=tf_5min,
        tf_15min=tf_15min,
        agreement=agreement,
        direction=direction,
    )
