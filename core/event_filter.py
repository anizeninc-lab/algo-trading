# core/event_filter.py
"""
News and high-impact event filter.
Blocks new entries 30 minutes before and after known high-impact events.

Sources:
- Hardcoded RBI policy dates (updated quarterly)
- F&O expiry days (auto-detected — every Tuesday for weekly)
- Budget day (hardcoded annually)
- US Fed meeting dates (hardcoded)
"""
import logging
import pytz
from datetime import datetime, date, time as dtime, timedelta
from typing import Optional, Tuple

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

# ── High-impact event dates (IST) ─────────────────────────────────────────────
# Update these quarterly
HIGH_IMPACT_DATES = {
    # RBI MPC decisions 2026
    date(2026, 4, 9):  "RBI MPC Decision",
    date(2026, 6, 6):  "RBI MPC Decision",
    date(2026, 8, 6):  "RBI MPC Decision",
    date(2026, 10, 1): "RBI MPC Decision",
    date(2026, 12, 3): "RBI MPC Decision",
    # Budget
    date(2026, 2, 1):  "Union Budget",
    # US Fed FOMC 2026
    date(2026, 1, 29): "US Fed FOMC",
    date(2026, 3, 19): "US Fed FOMC",
    date(2026, 5, 7):  "US Fed FOMC",
    date(2026, 6, 18): "US Fed FOMC",
    date(2026, 7, 30): "US Fed FOMC",
    date(2026, 9, 17): "US Fed FOMC",
    date(2026, 11, 5): "US Fed FOMC",
    date(2026, 12, 16):"US Fed FOMC",
}

# Block window around events (minutes)
BLOCK_BEFORE_MINUTES = 30
BLOCK_AFTER_MINUTES  = 30

# High-impact event time (most RBI/Fed decisions announced around these times IST)
RBI_ANNOUNCEMENT_TIME  = dtime(10, 0)   # 10:00 AM IST
FED_ANNOUNCEMENT_TIME  = dtime(23, 30)  # 11:30 PM IST (next day impact)
BUDGET_ANNOUNCEMENT_TIME = dtime(11, 0) # 11:00 AM IST


def _get_event_time(event_name: str) -> dtime:
    if "RBI" in event_name:
        return RBI_ANNOUNCEMENT_TIME
    if "Budget" in event_name:
        return BUDGET_ANNOUNCEMENT_TIME
    return dtime(9, 15)  # default: market open


def is_expiry_day() -> Tuple[bool, str]:
    """Returns (True, reason) if today is weekly/monthly F&O expiry."""
    today = datetime.now(IST).date()
    # Weekly Nifty expiry = every Tuesday
    if today.weekday() == 1:  # Tuesday
        return True, "Weekly Nifty F&O Expiry (Tuesday)"
    # Weekly BankNifty expiry = every Wednesday
    if today.weekday() == 2:  # Wednesday
        return True, "Weekly BankNifty F&O Expiry (Wednesday)"
    return False, ""


def check_event_block() -> Tuple[bool, str]:
    """
    Returns (blocked, reason).
    blocked=True means do not open new positions.
    """
    now = datetime.now(IST)
    today = now.date()

    # Check high-impact dates
    if today in HIGH_IMPACT_DATES:
        event_name = HIGH_IMPACT_DATES[today]
        event_time = _get_event_time(event_name)
        event_dt   = datetime.combine(today, event_time).replace(tzinfo=IST)
        block_start = event_dt - timedelta(minutes=BLOCK_BEFORE_MINUTES)
        block_end   = event_dt + timedelta(minutes=BLOCK_AFTER_MINUTES)

        if block_start <= now <= block_end:
            return True, f"HIGH IMPACT EVENT: {event_name} — entries blocked {BLOCK_BEFORE_MINUTES}min before/after"

    # Expiry day warning (don't block, just log)
    expiry, expiry_reason = is_expiry_day()
    if expiry:
        logger.debug(f"[event_filter] Expiry day: {expiry_reason}")

    return False, ""


def get_event_info() -> dict:
    """Returns current event status for dashboard display."""
    now   = datetime.now(IST)
    today = now.date()
    blocked, reason = check_event_block()
    expiry, expiry_reason = is_expiry_day()

    upcoming = None
    for d, name in sorted(HIGH_IMPACT_DATES.items()):
        if d >= today:
            upcoming = {"date": d.isoformat(), "name": name}
            break

    return {
        "blocked":        blocked,
        "reason":         reason,
        "expiry_day":     expiry,
        "expiry_reason":  expiry_reason,
        "upcoming_event": upcoming,
    }


# Singleton check function
def can_trade_event_filter() -> Tuple[bool, str]:
    blocked, reason = check_event_block()
    if blocked:
        logger.warning(f"[event_filter] BLOCKED: {reason}")
        return False, reason
    return True, ""
