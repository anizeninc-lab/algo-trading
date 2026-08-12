# core/gex_calculator.py
#
# OI-based synthetic GEX (Gamma Exposure) calculation for NIFTY, per
# NIFTY_GEX_STRATEGY_SPEC.md section 1-2.
#
# CONFIRMED (test_gex_data_apis.py, Aug 10 2026 session):
#   - Upstox's Option Greeks API (MarketQuoteV3Api.get_market_quote_option_greek)
#     returns gamma/delta/theta/vega/iv/oi DIRECTLY per contract. No Black-Scholes
#     round-trip needed -- this resolves spec open question #1.
#   - The same endpoint accepts a comma-separated instrument_key list (up to 500),
#     so a full strike chain can be fetched in ONE call, not one-per-strike.
#
# Formula (spec section 1):
#   GEX(strike) = (OI_call * Gamma_call - OI_put * Gamma_put) * spot^2 * 0.01 * lot_size
#   Sign convention: dealers net short calls / net long puts (industry-standard
#   assumption used whenever real dealer positioning isn't available).
#
# NOTE: "clean air zone" identification (spec section 2, strike ranges with no
# significant GEX level in between) is NOT implemented here yet -- deferred
# until the top-N level output is validated against real chain data.

import logging
from datetime import date

import upstox_client

logger = logging.getLogger(__name__)

NIFTY_LOT_SIZE = 65


def resolve_strike_chain(instruments: list, expiry: date, spot_price: float,
                          strike_range: int = 10, strike_step: int = 50,
                          underlying: str = "NIFTY") -> dict:
    """
    Resolve EXACT-match CE/PE instrument keys for ATM +/- strike_range strikes.

    Deliberately does NOT use auto_config.find_symbol_from_instruments' nearby-
    strike fallback -- for GEX, a silently substituted strike would corrupt the
    strike->GEX mapping. Every strike here must be an exact match or explicitly
    absent (logged, not guessed).

    Returns: {strike: {"CE": instrument_key_or_None, "PE": instrument_key_or_None}}
    """
    if underlying.upper() != "NIFTY":
        raise NotImplementedError("resolve_strike_chain currently supports NIFTY only")

    atm = round(spot_price / strike_step) * strike_step
    strikes = [atm + (i * strike_step) for i in range(-strike_range, strike_range + 1)]
    expiry_str = expiry.strftime("%Y-%m-%d")

    # Index instruments by (strike_suffix, expiry) once, rather than re-scanning
    # the full CSV rows for every strike -- instruments list is ~large.
    lookup = {}
    for row in instruments:
        name = row.get("tradingsymbol", "")
        exp = row.get("expiry", "")
        if exp != expiry_str:
            continue
        if "NIFTY" not in name or "BANKNIFTY" in name or "FINNIFTY" in name \
                or "MIDCPNIFTY" in name or "NIFTYNXT" in name:
            continue
        lookup.setdefault(exp, []).append((name, row.get("instrument_key", "")))

    chain = {}
    missing = []
    for strike in strikes:
        chain[strike] = {"CE": None, "PE": None}
        for opt_type in ("CE", "PE"):
            suffix = f"{strike}{opt_type}"
            match = None
            for name, ikey in lookup.get(expiry_str, []):
                if name.endswith(suffix):
                    match = ikey
                    break
            if match:
                chain[strike][opt_type] = match
            else:
                missing.append((strike, opt_type))

    if missing:
        logger.warning(f"[GEX] Could not resolve exact instrument key for: {missing}")

    return chain


def fetch_chain_greeks_and_oi(client: "upstox_client.ApiClient", strike_chain: dict) -> dict:
    """
    Batched fetch of Greeks + OI for every resolved instrument key in strike_chain,
    via a single Option Greeks API call (comma-separated instrument_key list,
    confirmed to support up to 500 keys).

    Returns: {strike: {"CE": {greeks_dict}, "PE": {greeks_dict}}}
    Missing/unresolved legs are omitted from the per-strike dict.
    """
    all_keys = []
    key_to_strike_type = {}
    for strike, legs in strike_chain.items():
        for opt_type, ikey in legs.items():
            if ikey:
                all_keys.append(ikey)
                key_to_strike_type[ikey] = (strike, opt_type)

    if not all_keys:
        logger.error("[GEX] No resolved instrument keys to fetch -- aborting")
        return {}

    result = {strike: {} for strike in strike_chain}

    try:
        v3_api = upstox_client.MarketQuoteV3Api(client)
        # Batch in chunks of 500 (API limit) -- in practice ATM+/-10 is ~42 keys,
        # so this loop runs once, but chunking is defensive for wider ranges later.
        for i in range(0, len(all_keys), 500):
            chunk = all_keys[i:i + 500]
            resp = v3_api.get_market_quote_option_greek(instrument_key=",".join(chunk))
            if not resp or not resp.data:
                logger.warning(f"[GEX] Empty response for chunk starting at index {i}")
                continue
            for _quote_key, quote in resp.data.items():
                ikey = getattr(quote, "instrument_token", None)
                if ikey not in key_to_strike_type:
                    continue
                strike, opt_type = key_to_strike_type[ikey]
                result[strike][opt_type] = {
                    "gamma": getattr(quote, "gamma", None),
                    "delta": getattr(quote, "delta", None),
                    "theta": getattr(quote, "theta", None),
                    "vega": getattr(quote, "vega", None),
                    "iv": getattr(quote, "iv", None),
                    "oi": getattr(quote, "oi", None),
                    "last_price": getattr(quote, "last_price", None),
                    "volume": getattr(quote, "volume", None),
                }
    except Exception as e:
        logger.error(f"[GEX] Option Greeks batch fetch failed: {e}")
        return {}

    return result


def compute_signed_gex(chain_data: dict, spot_price: float,
                        lot_size: int = NIFTY_LOT_SIZE) -> dict:
    """
    Per spec section 1:
      GEX(strike) = (OI_call * Gamma_call - OI_put * Gamma_put) * spot^2 * 0.01 * lot_size

    Returns: {strike: gex_value}. Strikes missing gamma/OI on either leg are
    skipped (logged), not defaulted to zero -- a silent zero would misrepresent
    "no data" as "confirmed neutral," which is a different thing.
    """
    strike_gex = {}
    skipped = []

    for strike, legs in chain_data.items():
        ce = legs.get("CE")
        pe = legs.get("PE")
        if not ce or not pe or ce.get("gamma") is None or pe.get("gamma") is None \
                or ce.get("oi") is None or pe.get("oi") is None:
            skipped.append(strike)
            continue

        call_gex = ce["oi"] * ce["gamma"]
        put_gex = pe["oi"] * pe["gamma"]
        gex = (call_gex - put_gex) * (spot_price ** 2) * 0.01 * lot_size
        strike_gex[strike] = gex

    if skipped:
        logger.warning(f"[GEX] Skipped {len(skipped)} strikes missing gamma/OI data: {skipped}")

    return strike_gex


def classify_regime(strike_gex: dict, top_n: int = 5) -> dict:
    """
    Per spec section 2:
      - top 3-5 positive GEX strikes (dealer long gamma -> support-like, mean-reverting)
      - top 3-5 negative GEX strikes (dealer short gamma -> resistance-like, trending)
      - net regime label across all strikes

    Returns:
      {
        "net_gex": float,
        "regime": "net_positive" | "net_negative",
        "top_positive_strikes": [(strike, gex), ...],   # sorted desc by gex
        "top_negative_strikes": [(strike, gex), ...],   # sorted asc by gex (most negative first)
      }
    """
    if not strike_gex:
        logger.error("[GEX] classify_regime called with empty strike_gex -- cannot classify")
        return {}

    net_gex = sum(strike_gex.values())
    regime = "net_positive" if net_gex >= 0 else "net_negative"

    sorted_by_gex = sorted(strike_gex.items(), key=lambda kv: kv[1], reverse=True)
    top_positive = [(s, g) for s, g in sorted_by_gex if g > 0][:top_n]
    # Most-negative-first: sort ascending, take the first top_n.
    top_negative = sorted(
        [(s, g) for s, g in strike_gex.items() if g < 0],
        key=lambda kv: kv[1]
    )[:top_n]

    return {
        "net_gex": net_gex,
        "regime": regime,
        "top_positive_strikes": top_positive,
        "top_negative_strikes": top_negative,
    }
