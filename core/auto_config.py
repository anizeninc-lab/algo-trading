# core/auto_config.py
# Auto-selects correct NIFTY option symbols using Upstox instruments CSV.
# NIFTY 50 expiry: TUESDAY (changed from Thursday in 2025)
# Weekly: Every Tuesday. Monthly: Last Tuesday of month.
# If Tuesday is holiday, shifts to previous trading day.

import logging
import os
import re
import csv
import gzip
import io
import requests
from datetime import datetime, date, timedelta
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.csv.gz"

NSE_HOLIDAYS_2026 = {
    date(2026, 1, 26),
    date(2026, 3, 25),
    date(2026, 4, 2),
    date(2026, 4, 14),
    date(2026, 4, 17),
    date(2026, 5, 1),
    date(2026, 8, 15),
    date(2026, 10, 2),
    date(2026, 10, 22),
    date(2026, 10, 23),
    date(2026, 11, 5),
    date(2026, 12, 25),
}


def is_trading_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    if d in NSE_HOLIDAYS_2026:
        return False
    return True


def get_previous_trading_day(d: date) -> date:
    candidate = d - timedelta(days=1)
    while not is_trading_day(candidate):
        candidate -= timedelta(days=1)
    return candidate


def get_expiry_date(d: date) -> date:
    if is_trading_day(d):
        return d
    return get_previous_trading_day(d)


def get_nearest_tuesday(from_date: date = None) -> date:
    if from_date is None:
        from_date = date.today()
    days_ahead = 1 - from_date.weekday()
    if days_ahead < 0:
        days_ahead += 7
    elif days_ahead == 0:
        if is_trading_day(from_date):
            return from_date
        else:
            days_ahead = 7
    next_tuesday = from_date + timedelta(days=days_ahead)
    return get_expiry_date(next_tuesday)


def get_last_tuesday_of_month(year: int, month: int) -> date:
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    last_day = next_month - timedelta(days=1)
    days_back = (last_day.weekday() - 1) % 7
    last_tuesday = last_day - timedelta(days=days_back)
    return get_expiry_date(last_tuesday)


def get_nearest_monthly_expiry(from_date: date = None) -> date:
    if from_date is None:
        from_date = date.today()
    monthly = get_last_tuesday_of_month(from_date.year, from_date.month)
    if monthly < from_date:
        if from_date.month == 12:
            monthly = get_last_tuesday_of_month(from_date.year + 1, 1)
        else:
            monthly = get_last_tuesday_of_month(from_date.year, from_date.month + 1)
    return monthly


def round_to_strike(price: float, step: int = 50) -> int:
    return int(round(price / step) * step)


def fetch_instruments() -> list:
    """Download and parse Upstox instruments CSV."""
    try:
        logger.info("[AutoConfig] Downloading instruments file...")
        r = requests.get(INSTRUMENTS_URL, timeout=30)
        content = gzip.decompress(r.content).decode("utf-8")
        reader = csv.DictReader(io.StringIO(content))
        rows = list(reader)
        logger.info(f"[AutoConfig] Loaded {len(rows)} instruments")
        return rows
    except Exception as e:
        logger.error(f"[AutoConfig] Failed to fetch instruments: {e}")
        return []


def find_symbol_from_instruments(instruments: list, expiry: date, strike: int, option_type: str) -> str:
    """Find instrument key from downloaded instruments list."""
    expiry_str = expiry.strftime("%Y-%m-%d")

    # Try exact strike first, then nearby
    strikes_to_try = [strike]
    for offset in [50, 100, 150, 200, 250]:
        strikes_to_try.append(strike + offset)
        strikes_to_try.append(strike - offset)

    for s in strikes_to_try:
        for row in instruments:
            name = row.get("tradingsymbol", "")
            ikey = row.get("instrument_key", "")
            exp  = row.get("expiry", "")
            if (
                "NIFTY" in name
                and "BANKNIFTY" not in name
                and "FINNIFTY" not in name
                and "MIDCPNIFTY" not in name
                and "NIFTYNXT" not in name
                and exp == expiry_str
                and name.endswith(f"{s}{option_type}")
            ):
                logger.info(f"[AutoConfig] Found {option_type}: {ikey} | {name} | expiry: {exp}")
                return ikey

    return ""


def auto_select_symbols(access_token: str = None) -> dict:
    """Auto-select correct NIFTY option symbols using Tuesday expiry."""
    import upstox_client

    if not access_token:
        access_token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
    if not access_token:
        logger.error("[AutoConfig] No access token available")
        return {}

    try:
        cfg = upstox_client.Configuration()
        cfg.access_token = access_token
        client = upstox_client.ApiClient(cfg)
        market_api = upstox_client.MarketQuoteApi(client)

        # Get current NIFTY price
        resp = market_api.ltp("NSE_INDEX|Nifty 50", api_version="2.0")
        keys = list(resp.data.keys()) if resp.data else []
        if not keys:
            logger.error("[AutoConfig] Could not fetch NIFTY price")
            return {}

        nifty_price = float(resp.data[keys[0]].last_price)
        atm_strike  = round_to_strike(nifty_price, 50)
        logger.info(f"[AutoConfig] NIFTY: {nifty_price} | ATM Strike: {atm_strike}")

        today          = date.today()
        weekly_expiry  = get_nearest_tuesday(today)
        monthly_expiry = get_nearest_monthly_expiry(today)

        logger.info(f"[AutoConfig] Weekly expiry (Tuesday): {weekly_expiry} ({weekly_expiry.strftime('%A')})")
        logger.info(f"[AutoConfig] Monthly expiry (Last Tuesday): {monthly_expiry} ({monthly_expiry.strftime('%A')})")

        # Download instruments
        instruments = fetch_instruments()
        if not instruments:
            logger.error("[AutoConfig] No instruments data")
            return {}

        # Find ATM symbols for weekly expiry
        ce_symbol = find_symbol_from_instruments(instruments, weekly_expiry, atm_strike, "CE")
        pe_symbol = find_symbol_from_instruments(instruments, weekly_expiry, atm_strike, "PE")

        # Fallback to monthly
        if not ce_symbol:
            ce_symbol = find_symbol_from_instruments(instruments, monthly_expiry, atm_strike, "CE")
        if not pe_symbol:
            pe_symbol = find_symbol_from_instruments(instruments, monthly_expiry, atm_strike, "PE")

        symbol_initials = f"NIFTY{weekly_expiry.strftime('%d%b%y').upper()}"

        result = {
            "option_symbol":   ce_symbol or pe_symbol,
            "symbol_initials": symbol_initials,
            "nifty_price":     nifty_price,
            "atm_strike":      atm_strike,
            "weekly_expiry":   str(weekly_expiry),
            "monthly_expiry":  str(monthly_expiry),
            "ce_symbol":       ce_symbol,
            "pe_symbol":       pe_symbol,
        }

        logger.info(f"[AutoConfig] Result: {result}")
        return result

    except Exception as e:
        logger.error(f"[AutoConfig] Failed: {e}")
        return {}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = auto_select_symbols()
    if result:
        print("\n=== AUTO CONFIG RESULT ===")
        for k, v in result.items():
            print(f"  {k}: {v}")
        print("\nUpdate configs/saviour_combo.json with:")
        print(f'  "option_symbol":   "{result.get("option_symbol", "")}"')
        print(f'  "symbol_initials": "{result.get("symbol_initials", "")}"')
    else:
        print("Auto config failed")

# ─── BankNifty Support ────────────────────────────────────────────────────────

def get_nearest_wednesday(from_date: date = None) -> date:
    """BankNifty expires on Wednesdays."""
    if from_date is None:
        from_date = date.today()
    days_ahead = 2 - from_date.weekday()  # 2 = Wednesday
    if days_ahead < 0:
        days_ahead += 7
    elif days_ahead == 0:
        if is_trading_day(from_date):
            return from_date
        else:
            days_ahead = 7
    next_wednesday = from_date + timedelta(days=days_ahead)
    return get_expiry_date(next_wednesday)


def get_last_wednesday_of_month(year: int, month: int) -> date:
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    last_day = next_month - timedelta(days=1)
    days_back = (last_day.weekday() - 2) % 7
    last_wednesday = last_day - timedelta(days=days_back)
    return get_expiry_date(last_wednesday)


def get_nearest_banknifty_monthly_expiry(from_date: date = None) -> date:
    if from_date is None:
        from_date = date.today()
    monthly = get_last_wednesday_of_month(from_date.year, from_date.month)
    if monthly < from_date:
        if from_date.month == 12:
            monthly = get_last_wednesday_of_month(from_date.year + 1, 1)
        else:
            monthly = get_last_wednesday_of_month(from_date.year, from_date.month + 1)
    return monthly


def find_banknifty_symbol_from_instruments(
    instruments: list, expiry: date, strike: int, option_type: str
) -> str:
    """Find BankNifty instrument key from downloaded instruments list."""
    expiry_str = expiry.strftime("%Y-%m-%d")
    strikes_to_try = [strike]
    for offset in [100, 200, 300, 400, 500]:
        strikes_to_try.append(strike + offset)
        strikes_to_try.append(strike - offset)
    for s in strikes_to_try:
        for row in instruments:
            name = row.get("tradingsymbol", "")
            ikey = row.get("instrument_key", "")
            exp  = row.get("expiry", "")
            if (
                "BANKNIFTY" in name
                and exp == expiry_str
                and name.endswith(f"{s}{option_type}")
            ):
                logger.info(f"[AutoConfig] BankNifty Found {option_type}: {ikey} | {name} | expiry: {exp}")
                return ikey
    return ""


def auto_select_banknifty_symbols(instruments: list = None) -> dict:
    """Auto-select BankNifty option symbols using Wednesday expiry."""
    import upstox_client
    access_token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
    if not access_token:
        logger.error("[AutoConfig] No access token for BankNifty")
        return {}
    try:
        cfg = upstox_client.Configuration()
        cfg.access_token = access_token
        client = upstox_client.ApiClient(cfg)
        market_api = upstox_client.MarketQuoteApi(client)

        resp = market_api.ltp("NSE_INDEX|Nifty Bank", api_version="2.0")
        keys = list(resp.data.keys()) if resp.data else []
        if not keys:
            logger.error("[AutoConfig] Could not fetch BankNifty price")
            return {}

        bnf_price  = float(resp.data[keys[0]].last_price)
        atm_strike = round_to_strike(bnf_price, 100)
        logger.info(f"[AutoConfig] BANKNIFTY: {bnf_price} | ATM Strike: {atm_strike}")

        today          = date.today()
        weekly_expiry  = get_nearest_wednesday(today)
        monthly_expiry = get_nearest_banknifty_monthly_expiry(today)

        logger.info(f"[AutoConfig] BNF Weekly expiry (Wednesday): {weekly_expiry}")
        logger.info(f"[AutoConfig] BNF Monthly expiry: {monthly_expiry}")

        if instruments is None:
            instruments = fetch_instruments()
        if not instruments:
            return {}

        ce_symbol = find_banknifty_symbol_from_instruments(instruments, weekly_expiry, atm_strike, "CE")
        pe_symbol = find_banknifty_symbol_from_instruments(instruments, weekly_expiry, atm_strike, "PE")

        if not ce_symbol:
            ce_symbol = find_banknifty_symbol_from_instruments(instruments, monthly_expiry, atm_strike, "CE")
        if not pe_symbol:
            pe_symbol = find_banknifty_symbol_from_instruments(instruments, monthly_expiry, atm_strike, "PE")

        symbol_initials = f"BANKNIFTY{weekly_expiry.strftime('%d%b%y').upper()}"

        result = {
            "bn_option_symbol":   ce_symbol or pe_symbol,
            "bn_symbol_initials": symbol_initials,
            "bn_price":           bnf_price,
            "bn_atm_strike":      atm_strike,
            "bn_weekly_expiry":   str(weekly_expiry),
            "bn_ce_symbol":       ce_symbol,
            "bn_pe_symbol":       pe_symbol,
        }
        logger.info(f"[AutoConfig] BankNifty Result: {result}")
        return result
    except Exception as e:
        logger.error(f"[AutoConfig] BankNifty auto_select failed: {e}")
        return {}


# ─── BankNifty Support ────────────────────────────────────────────────────────

def get_nearest_wednesday(from_date: date = None) -> date:
    """BankNifty expires on Wednesdays."""
    if from_date is None:
        from_date = date.today()
    days_ahead = 2 - from_date.weekday()  # 2 = Wednesday
    if days_ahead < 0:
        days_ahead += 7
    elif days_ahead == 0:
        if is_trading_day(from_date):
            return from_date
        else:
            days_ahead = 7
    next_wednesday = from_date + timedelta(days=days_ahead)
    return get_expiry_date(next_wednesday)


def get_last_wednesday_of_month(year: int, month: int) -> date:
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    last_day = next_month - timedelta(days=1)
    days_back = (last_day.weekday() - 2) % 7
    last_wednesday = last_day - timedelta(days=days_back)
    return get_expiry_date(last_wednesday)


def get_nearest_banknifty_monthly_expiry(from_date: date = None) -> date:
    if from_date is None:
        from_date = date.today()
    monthly = get_last_wednesday_of_month(from_date.year, from_date.month)
    if monthly < from_date:
        if from_date.month == 12:
            monthly = get_last_wednesday_of_month(from_date.year + 1, 1)
        else:
            monthly = get_last_wednesday_of_month(from_date.year, from_date.month + 1)
    return monthly


def find_banknifty_symbol_from_instruments(
    instruments: list, expiry: date, strike: int, option_type: str
) -> str:
    """Find BankNifty instrument key from downloaded instruments list."""
    expiry_str = expiry.strftime("%Y-%m-%d")
    strikes_to_try = [strike]
    for offset in [100, 200, 300, 400, 500]:
        strikes_to_try.append(strike + offset)
        strikes_to_try.append(strike - offset)
    for s in strikes_to_try:
        for row in instruments:
            name = row.get("tradingsymbol", "")
            ikey = row.get("instrument_key", "")
            exp  = row.get("expiry", "")
            if (
                "BANKNIFTY" in name
                and exp == expiry_str
                and name.endswith(f"{s}{option_type}")
            ):
                logger.info(f"[AutoConfig] BankNifty Found {option_type}: {ikey} | {name} | expiry: {exp}")
                return ikey
    return ""


def auto_select_banknifty_symbols(instruments: list = None) -> dict:
    """Auto-select BankNifty option symbols using Wednesday expiry."""
    import upstox_client
    access_token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
    if not access_token:
        logger.error("[AutoConfig] No access token for BankNifty")
        return {}
    try:
        cfg = upstox_client.Configuration()
        cfg.access_token = access_token
        client = upstox_client.ApiClient(cfg)
        market_api = upstox_client.MarketQuoteApi(client)

        resp = market_api.ltp("NSE_INDEX|Nifty Bank", api_version="2.0")
        keys = list(resp.data.keys()) if resp.data else []
        if not keys:
            logger.error("[AutoConfig] Could not fetch BankNifty price")
            return {}

        bnf_price  = float(resp.data[keys[0]].last_price)
        atm_strike = round_to_strike(bnf_price, 100)
        logger.info(f"[AutoConfig] BANKNIFTY: {bnf_price} | ATM Strike: {atm_strike}")

        today          = date.today()
        weekly_expiry  = get_nearest_wednesday(today)
        monthly_expiry = get_nearest_banknifty_monthly_expiry(today)

        logger.info(f"[AutoConfig] BNF Weekly expiry (Wednesday): {weekly_expiry}")
        logger.info(f"[AutoConfig] BNF Monthly expiry: {monthly_expiry}")

        if instruments is None:
            instruments = fetch_instruments()
        if not instruments:
            return {}

        ce_symbol = find_banknifty_symbol_from_instruments(instruments, weekly_expiry, atm_strike, "CE")
        pe_symbol = find_banknifty_symbol_from_instruments(instruments, weekly_expiry, atm_strike, "PE")

        if not ce_symbol:
            ce_symbol = find_banknifty_symbol_from_instruments(instruments, monthly_expiry, atm_strike, "CE")
        if not pe_symbol:
            pe_symbol = find_banknifty_symbol_from_instruments(instruments, monthly_expiry, atm_strike, "PE")

        symbol_initials = f"BANKNIFTY{weekly_expiry.strftime('%d%b%y').upper()}"

        result = {
            "bn_option_symbol":   ce_symbol or pe_symbol,
            "bn_symbol_initials": symbol_initials,
            "bn_price":           bnf_price,
            "bn_atm_strike":      atm_strike,
            "bn_weekly_expiry":   str(weekly_expiry),
            "bn_ce_symbol":       ce_symbol,
            "bn_pe_symbol":       pe_symbol,
        }
        logger.info(f"[AutoConfig] BankNifty Result: {result}")
        return result
    except Exception as e:
        logger.error(f"[AutoConfig] BankNifty auto_select failed: {e}")
        return {}
