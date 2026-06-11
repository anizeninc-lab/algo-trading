# core/strategy_recommender.py
# Strategy recommendation engine based on live market regime data

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

@dataclass
class StrategyLeg:
    action: str        # BUY or SELL
    option_type: str   # CE or PE
    strike: float
    quantity: int

@dataclass
class StrategyRecommendation:
    id: str
    name: str
    type: str
    score: int         # 0-100 regime fit score
    recommended: bool
    badge: str         # RECOMMENDED / CONSIDER / HIGH RISK / WRONG REGIME
    badge_cls: str
    legs: list[dict]
    margin: float
    capital: float
    max_risk: float
    max_profit: float
    rr: str
    when: str
    conditions: list[dict]
    score_color: str

def get_recommendations(
    nifty: float,
    vix: float,
    pcr: float,
    atm: float,
    regime: str,
    or_width: Optional[float],
    max_pain: Optional[float],
    lot_size: int = 75,
) -> list[dict]:
    """Score and rank all strategies based on current market conditions."""

    atm = round(atm / 50) * 50  # Round to nearest 50
    is_range     = regime in ("range", "closed")
    is_bullish   = regime == "trending_bull"
    is_bearish   = regime == "trending_bear"
    is_volatile  = vix > 20
    vix_low      = vix < 14
    vix_normal   = 14 <= vix <= 18
    pcr_neutral  = 0.85 <= pcr <= 1.15
    pcr_bullish  = pcr > 1.2
    pcr_bearish  = pcr < 0.8
    or_locked    = or_width is not None and or_width > 0
    or_tight     = or_locked and or_width < 50
    price_above_mp = max_pain is not None and nifty > max_pain
    price_below_mp = max_pain is not None and nifty < max_pain

    strategies = []

    # ── 1. Short Strangle ─────────────────────────────────────────────────────
    ss_score = 0
    if is_range:   ss_score += 35
    if vix_normal: ss_score += 25
    if pcr_neutral: ss_score += 20
    if or_locked:  ss_score += 10
    if vix < 18:   ss_score += 10
    ss_conds = [
        {"label": "VIX < 18",    "met": vix < 18},
        {"label": "PCR 0.9–1.1", "met": pcr_neutral},
        {"label": "Range regime", "met": is_range},
        {"label": "OR locked",   "met": or_locked},
    ]
    ce_strike = atm + 200
    pe_strike = atm - 200
    ss_margin = 120000
    ss_max_profit = 8000
    ss_max_risk = 35000
    strategies.append({
        "id": "short_strangle",
        "name": "Short Strangle",
        "type": "SELL OTM CE + PE",
        "score": min(ss_score, 100),
        "recommended": ss_score >= 70,
        "badge": "RECOMMENDED" if ss_score >= 70 else "CONSIDER" if ss_score >= 50 else "WRONG REGIME",
        "badge_cls": "badge-green" if ss_score >= 70 else "badge-blue" if ss_score >= 50 else "badge-amber",
        "legs": [
            {"action":"SELL","type":"CE","strike":ce_strike,"qty":lot_size},
            {"action":"SELL","type":"PE","strike":pe_strike,"qty":lot_size},
        ],
        "margin": ss_margin,
        "capital": 180000,
        "max_risk": ss_max_risk,
        "max_profit": ss_max_profit,
        "rr": f"1:{round(ss_max_risk/ss_max_profit,1)}",
        "when": f"Nifty stays between {pe_strike}–{ce_strike} till expiry",
        "conditions": ss_conds,
    })

    # ── 2. Iron Condor ────────────────────────────────────────────────────────
    ic_score = 0
    if is_range:    ic_score += 30
    if vix_normal:  ic_score += 20
    if pcr_neutral: ic_score += 20
    if or_tight:    ic_score += 20
    if vix < 18:    ic_score += 10
    ic_conds = [
        {"label": "VIX < 18",    "met": vix < 18},
        {"label": "PCR neutral", "met": pcr_neutral},
        {"label": "Range regime","met": is_range},
        {"label": "Tight OR",   "met": or_tight},
    ]
    strategies.append({
        "id": "iron_condor",
        "name": "Iron Condor",
        "type": "4-LEG SPREAD",
        "score": min(ic_score, 100),
        "recommended": ic_score >= 70,
        "badge": "RECOMMENDED" if ic_score >= 70 else "CONSIDER" if ic_score >= 50 else "WRONG REGIME",
        "badge_cls": "badge-green" if ic_score >= 70 else "badge-blue" if ic_score >= 50 else "badge-amber",
        "legs": [
            {"action":"SELL","type":"CE","strike":atm+150,"qty":lot_size},
            {"action":"BUY", "type":"CE","strike":atm+250,"qty":lot_size},
            {"action":"SELL","type":"PE","strike":atm-150,"qty":lot_size},
            {"action":"BUY", "type":"PE","strike":atm-250,"qty":lot_size},
        ],
        "margin": 55000,
        "capital": 60000,
        "max_risk": 45000,
        "max_profit": 5500,
        "rr": "1:8.2",
        "when": f"Nifty stays inside {atm-150}–{atm+150}, loss capped",
        "conditions": ic_conds,
    })

    # ── 3. Bull Put Spread ────────────────────────────────────────────────────
    bp_score = 0
    if is_bullish:      bp_score += 40
    if pcr_bullish:     bp_score += 30
    if price_above_mp:  bp_score += 20
    if vix_normal:      bp_score += 10
    bp_conds = [
        {"label": "PCR > 1.2",       "met": pcr_bullish},
        {"label": "Price > Max Pain", "met": price_above_mp},
        {"label": "Bullish regime",   "met": is_bullish},
        {"label": "VIX normal",       "met": vix_normal},
    ]
    strategies.append({
        "id": "bull_put_spread",
        "name": "Bull Put Spread",
        "type": "BULLISH SPREAD",
        "score": min(bp_score, 100),
        "recommended": bp_score >= 70,
        "badge": "RECOMMENDED" if bp_score >= 70 else "CONSIDER" if bp_score >= 50 else "WRONG REGIME",
        "badge_cls": "badge-green" if bp_score >= 70 else "badge-blue" if bp_score >= 50 else "badge-red",
        "legs": [
            {"action":"SELL","type":"PE","strike":atm-100,"qty":lot_size},
            {"action":"BUY", "type":"PE","strike":atm-200,"qty":lot_size},
        ],
        "margin": 48000,
        "capital": 50000,
        "max_risk": 40000,
        "max_profit": 10000,
        "rr": "1:4",
        "when": f"Nifty stays above {atm-100} till expiry",
        "conditions": bp_conds,
    })

    # ── 4. Bear Call Spread ───────────────────────────────────────────────────
    bc_score = 0
    if is_bearish:      bc_score += 40
    if pcr_bearish:     bc_score += 30
    if price_below_mp:  bc_score += 20
    if vix_normal:      bc_score += 10
    bc_conds = [
        {"label": "PCR < 0.8",        "met": pcr_bearish},
        {"label": "Price < Max Pain",  "met": price_below_mp},
        {"label": "Bearish regime",    "met": is_bearish},
        {"label": "VIX normal",        "met": vix_normal},
    ]
    strategies.append({
        "id": "bear_call_spread",
        "name": "Bear Call Spread",
        "type": "BEARISH SPREAD",
        "score": min(bc_score, 100),
        "recommended": bc_score >= 70,
        "badge": "RECOMMENDED" if bc_score >= 70 else "CONSIDER" if bc_score >= 50 else "WRONG REGIME",
        "badge_cls": "badge-green" if bc_score >= 70 else "badge-blue" if bc_score >= 50 else "badge-red",
        "legs": [
            {"action":"SELL","type":"CE","strike":atm+100,"qty":lot_size},
            {"action":"BUY", "type":"CE","strike":atm+200,"qty":lot_size},
        ],
        "margin": 48000,
        "capital": 50000,
        "max_risk": 40000,
        "max_profit": 10000,
        "rr": "1:4",
        "when": f"Nifty stays below {atm+100} till expiry",
        "conditions": bc_conds,
    })

    # ── 5. Short Straddle ─────────────────────────────────────────────────────
    std_score = 0
    if is_range:    std_score += 25
    if vix_low:     std_score += 40
    if pcr_neutral: std_score += 20
    if or_tight:    std_score += 15
    std_conds = [
        {"label": "VIX < 14",    "met": vix_low},
        {"label": "PCR neutral", "met": pcr_neutral},
        {"label": "Range regime","met": is_range},
        {"label": "Tight OR",   "met": or_tight},
    ]
    strategies.append({
        "id": "short_straddle",
        "name": "Short Straddle",
        "type": "SELL ATM CE + PE",
        "score": min(std_score, 100),
        "recommended": std_score >= 70,
        "badge": "RECOMMENDED" if std_score >= 70 else "HIGH RISK" if std_score >= 40 else "WRONG REGIME",
        "badge_cls": "badge-green" if std_score >= 70 else "badge-amber",
        "legs": [
            {"action":"SELL","type":"CE","strike":atm,"qty":lot_size},
            {"action":"SELL","type":"PE","strike":atm,"qty":lot_size},
        ],
        "margin": 160000,
        "capital": 200000,
        "max_risk": -1,  # unlimited
        "max_profit": 12000,
        "rr": "Unlimited risk",
        "when": f"Nifty stays near {atm} — requires VIX < 14",
        "conditions": std_conds,
    })

    # ── 6. Long Straddle ──────────────────────────────────────────────────────
    ls_score = 0
    if is_volatile: ls_score += 50
    if vix > 20:    ls_score += 30
    if not is_range: ls_score += 20
    ls_conds = [
        {"label": "VIX > 20",       "met": vix > 20},
        {"label": "Volatile regime", "met": is_volatile},
        {"label": "Big move expected","met": vix > 18},
    ]
    strategies.append({
        "id": "long_straddle",
        "name": "Long Straddle",
        "type": "BUY ATM CE + PE",
        "score": min(ls_score, 100),
        "recommended": ls_score >= 70,
        "badge": "RECOMMENDED" if ls_score >= 70 else "CONSIDER" if ls_score >= 40 else "WRONG REGIME",
        "badge_cls": "badge-green" if ls_score >= 70 else "badge-blue" if ls_score >= 40 else "badge-red",
        "legs": [
            {"action":"BUY","type":"CE","strike":atm,"qty":lot_size},
            {"action":"BUY","type":"PE","strike":atm,"qty":lot_size},
        ],
        "margin": 40000,
        "capital": 40000,
        "max_risk": 40000,
        "max_profit": -1,  # unlimited
        "rr": "Limited risk, unlimited profit",
        "when": "Big directional move expected in either direction",
        "conditions": ls_conds,
    })

    # Sort by score descending
    strategies.sort(key=lambda x: x["score"], reverse=True)
    return strategies
