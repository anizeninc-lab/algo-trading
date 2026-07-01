# core/greeks_engine.py
# Portfolio-level Greeks aggregation using Black-Scholes via mibian.
# Computes per-trade Greeks and aggregates across all open positions.

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import mibian
    _MIBIAN_OK = True
except ImportError:
    _MIBIAN_OK = False
    logger.warning("[greeks_engine] mibian not installed — Greeks unavailable")

RISK_FREE_RATE = 6.5   # India 10yr approx %
SYMBOL_RE      = re.compile(r'[A-Z]+(\d{2})([A-Z]{3})(\d{2})(CE|PE)(\d+)')


@dataclass
class TradeGreeks:
    symbol:     str
    direction:  str        # CE or PE
    strike:     float
    dte:        int
    spot:       float
    quantity:   int
    delta:      float      # per unit
    gamma:      float
    theta:      float      # per unit per day
    vega:       float      # per unit per 1% vol
    net_delta:  float      # delta * quantity (sign-adjusted for SELL)
    net_theta:  float      # theta * quantity
    net_vega:   float


@dataclass
class PortfolioGreeks:
    trades:         list[TradeGreeks] = field(default_factory=list)
    total_delta:    float = 0.0
    total_gamma:    float = 0.0
    total_theta:    float = 0.0   # daily P&L from time decay
    total_vega:     float = 0.0   # P&L per 1% vol move
    error:          Optional[str] = None


def _parse_symbol(symbol: str):
    """Extract (expiry_date, opt_type, strike) from symbol string like NIFTY01JUL26PE23500."""
    m = SYMBOL_RE.search(symbol)
    if not m:
        return None, None, None
    day, mon, yr, opt_type, strike = m.groups()
    try:
        expiry = datetime.strptime(f"{day}{mon}20{yr}", "%d%b%Y").date()
    except ValueError:
        return None, None, None
    return expiry, opt_type, float(strike)


def compute_trade_greeks(
    symbol:    str,
    spot:      float,
    quantity:  int,
    vix:       float,
    order_type: str = "SELL",   # SELL = short position
) -> Optional[TradeGreeks]:
    """Compute BS Greeks for a single trade."""
    if not _MIBIAN_OK:
        return None

    expiry, opt_type, strike = _parse_symbol(symbol)
    if expiry is None:
        logger.warning(f"[greeks_engine] Could not parse symbol: {symbol}")
        return None

    dte = max((expiry - date.today()).days, 1)

    try:
        bs = mibian.BS([spot, strike, RISK_FREE_RATE, dte], volatility=vix)
    except Exception as e:
        logger.warning(f"[greeks_engine] BS computation failed for {symbol}: {e}")
        return None

    if opt_type == "CE":
        delta = bs.callDelta
        theta = bs.callTheta
    else:
        delta = bs.putDelta
        theta = bs.putTheta

    gamma = bs.gamma
    vega  = bs.vega

    # For SELL positions: we are short, so flip sign on delta/theta/vega
    sign = -1 if order_type == "SELL" else 1

    return TradeGreeks(
        symbol     = symbol,
        direction  = opt_type,
        strike     = strike,
        dte        = dte,
        spot       = spot,
        quantity   = quantity,
        delta      = round(delta, 6),
        gamma      = round(gamma, 6),
        theta      = round(theta, 4),
        vega       = round(vega,  4),
        net_delta  = round(sign * delta * quantity, 4),
        net_theta  = round(sign * theta * quantity, 4),
        net_vega   = round(sign * vega  * quantity, 4),
    )


def aggregate_portfolio_greeks(
    trades: list[dict],   # list of trade dicts with keys: symbol, quantity, order_type
    spot:   float,
    vix:    float,
) -> PortfolioGreeks:
    """
    Compute and aggregate Greeks across all open trades.

    trades: list of dicts like:
        {"symbol": "NIFTY01JUL26PE23500", "quantity": 65, "order_type": "SELL"}
    spot: current Nifty spot price
    vix:  current VIX (used as implied volatility proxy)
    """
    if not _MIBIAN_OK:
        return PortfolioGreeks(error="mibian not installed")

    if spot <= 0 or vix <= 0:
        return PortfolioGreeks(error=f"Invalid inputs: spot={spot} vix={vix}")

    portfolio = PortfolioGreeks()

    for t in trades:
        symbol     = t.get("symbol") or t.get("symbol")
        quantity   = t.get("quantity", 65)
        order_type = t.get("order_type", "SELL")

        tg = compute_trade_greeks(symbol, spot, quantity, vix, order_type)
        if tg is None:
            continue

        portfolio.trades.append(tg)
        portfolio.total_delta += tg.net_delta
        portfolio.total_gamma += tg.gamma * quantity * (-1 if order_type == "SELL" else 1)
        portfolio.total_theta += tg.net_theta
        portfolio.total_vega  += tg.net_vega

    portfolio.total_delta = round(portfolio.total_delta, 4)
    portfolio.total_gamma = round(portfolio.total_gamma, 6)
    portfolio.total_theta = round(portfolio.total_theta, 2)
    portfolio.total_vega  = round(portfolio.total_vega,  2)

    return portfolio
