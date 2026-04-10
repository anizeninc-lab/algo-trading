# core/auto_config.py
import logging
import os
from datetime import date, datetime, timedelta

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


def get_nearest_thursday() -> date:
    today = date.today()
    days_ahead = 3 - today.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return today + timedelta(days=days_ahead)


def get_nearest_monthly_expiry() -> date:
    today = date.today()
    if today.month == 12:
        next_month = date(today.year + 1, 1, 1)
    else:
        next_month = date(today.year, today.month + 1, 1)
    last_day = next_month - timedelta(days=1)
    days_back = (last_day.weekday() - 3) % 7
    last_thursday = last_day - timedelta(days=days_back)
    if last_thursday < today:
        if today.month == 12:
            next2 = date(today.year + 1, 2, 1)
        else:
            next2 = date(today.year, today.month + 2, 1)
        last_day2 = next2 - timedelta(days=1)
        days_back2 = (last_day2.weekday() - 3) % 7
        last_thursday = last_day2 - timedelta(days=days_back2)
    return last_thursday


def format_expiry_for_symbol(expiry: date) -> str:
    """
    Upstox weekly format: YYMDd — month has NO leading zero
    e.g. 13 Apr 2026 = 26413
    e.g. 03 Nov 2026 = 261103
    """
    year = expiry.strftime("%y")  # 26
    month = str(expiry.month)  # 4 (no leading zero)
    day = expiry.strftime("%d")  # 13
    return f"{year}{month}{day}"


def round_to_strike(price: float, step: int = 50) -> int:
    return int(round(price / step) * step)


def find_atm_option_symbol(
    market_api, nifty_price: float, expiry: date, option_type: str = "CE"
) -> str:
    import upstox_client

    atm_strike = round_to_strike(nifty_price, 50)
    expiry_str = format_expiry_for_symbol(expiry)

    strikes_to_try = [atm_strike]
    for offset in [50, 100, 150, 200, 250, 300]:
        strikes_to_try.append(atm_strike + offset)
        strikes_to_try.append(atm_strike - offset)

    for strike in strikes_to_try:
        symbol = f"NSE_FO|NIFTY{expiry_str}{strike}{option_type}"
        try:
            resp = market_api.ltp(symbol, api_version="2.0")
            if resp.data:
                keys = list(resp.data.keys())
                if keys and resp.data[keys[0]].last_price > 0:
                    ltp = resp.data[keys[0]].last_price
                    logger.info(
                        f"[AutoConfig] Found {option_type}: {symbol} | LTP: {ltp}"
                    )
                    return symbol
        except Exception:
            continue

    logger.warning(f"[AutoConfig] Could not find {option_type} for expiry {expiry_str}")
    return ""


def auto_select_symbols(access_token: str = None) -> dict:
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

        resp = market_api.ltp("NSE_INDEX|Nifty 50", api_version="2.0")
        keys = list(resp.data.keys()) if resp.data else []
        if not keys:
            logger.error("[AutoConfig] Could not fetch NIFTY price")
            return {}

        nifty_price = float(resp.data[keys[0]].last_price)
        atm_strike = round_to_strike(nifty_price, 50)
        logger.info(f"[AutoConfig] NIFTY: {nifty_price} | ATM Strike: {atm_strike}")

        weekly_expiry = get_nearest_thursday()
        monthly_expiry = get_nearest_monthly_expiry()
        logger.info(f"[AutoConfig] Weekly: {weekly_expiry} | Monthly: {monthly_expiry}")

        ce_symbol = find_atm_option_symbol(market_api, nifty_price, weekly_expiry, "CE")
        pe_symbol = find_atm_option_symbol(market_api, nifty_price, weekly_expiry, "PE")

        if not ce_symbol:
            ce_symbol = find_atm_option_symbol(
                market_api, nifty_price, monthly_expiry, "CE"
            )
        if not pe_symbol:
            pe_symbol = find_atm_option_symbol(
                market_api, nifty_price, monthly_expiry, "PE"
            )

        expiry_str = weekly_expiry.strftime("%d%b%y").upper()
        symbol_initials = f"NIFTY{expiry_str}"

        result = {
            "option_symbol": ce_symbol or pe_symbol,
            "symbol_initials": symbol_initials,
            "nifty_price": nifty_price,
            "atm_strike": atm_strike,
            "weekly_expiry": str(weekly_expiry),
            "monthly_expiry": str(monthly_expiry),
            "ce_symbol": ce_symbol,
            "pe_symbol": pe_symbol,
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
        print(f'  "option_symbol": "{result.get("option_symbol", "")}"')
        print(f'  "symbol_initials": "{result.get("symbol_initials", "")}"')
    else:
        print("Auto config failed — check logs")
