# core/astro_calendar.py
# Hardcoded astro trading calendar — 6 weeks (21 Jul – 31 Aug 2026)
# Strength levels: Excellent, Very Strong, Strong, Moderate+, Recovery, High Risk, Risky
# Bot behaviour:
#   High Risk / Risky  → pause all new entries
#   Moderate+ / Recovery → reduce qty to 50%
#   Strong and above  → normal operation
#
# Usage:
#   from core.astro_calendar import astro_calendar
#   today = astro_calendar.today()   # returns AstroDay or None

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional
import pytz

IST = pytz.timezone("Asia/Kolkata")

STRENGTH_RANK = {
    "Excellent":   5,
    "Very Strong": 4,
    "Strong":      3,
    "Moderate+":   2,
    "Recovery":    1,
    "High Risk":   0,
    "Risky":       0,
}

# Qty multiplier per strength level
STRENGTH_QTY_MULT = {
    "Excellent":   1.0,
    "Very Strong": 1.0,
    "Strong":      1.0,
    "Moderate+":   0.5,
    "Recovery":    0.5,
    "High Risk":   0.0,   # pause
    "Risky":       0.0,   # pause
}


@dataclass
class AstroDay:
    date:         date
    strength:     str       # e.g. "Excellent"
    best_window:  str       # e.g. "9:35–11:15 AM"
    avoid:        str       # e.g. "2:45–3:15 PM"

    @property
    def rank(self) -> int:
        return STRENGTH_RANK.get(self.strength, 0)

    @property
    def qty_multiplier(self) -> float:
        return STRENGTH_QTY_MULT.get(self.strength, 0.0)

    @property
    def trading_allowed(self) -> bool:
        return self.qty_multiplier > 0

    @property
    def alert_level(self) -> str:
        """Returns 'green', 'amber', or 'red' for dashboard badge colour."""
        if self.rank >= 3:
            return "green"
        elif self.rank >= 1:
            return "amber"
        else:
            return "red"

    def to_dict(self) -> dict:
        return {
            "date":           self.date.isoformat(),
            "strength":       self.strength,
            "best_window":    self.best_window,
            "avoid":          self.avoid,
            "rank":           self.rank,
            "qty_multiplier": self.qty_multiplier,
            "trading_allowed": self.trading_allowed,
            "alert_level":    self.alert_level,
        }


# ─── Hardcoded calendar ───────────────────────────────────────────────────────
_RAW = [
    # Week 1: 21–27 Jul 2026
    ("2026-07-21", "Moderate+",   "9:35–10:40 AM",       "12–1 PM"),
    ("2026-07-22", "Excellent",   "9:40–11:15 AM",       "2:45–3:15 PM"),
    ("2026-07-24", "Very Good",   "1:15–2:50 PM",        "Noon"),

    # Week 2: 28 Jul–3 Aug 2026
    ("2026-07-29", "Strong",      "9:35–11:10 AM",       "Noon"),
    ("2026-07-30", "Very Strong", "1–3 PM",              "Opening spike"),
    ("2026-08-01", "Strong",      "9:45–10:50 AM",       "Midday"),

    # Week 3: 4–10 Aug 2026
    ("2026-08-04", "Excellent",   "9:40–11:20 AM",       "Late afternoon"),
    ("2026-08-06", "Excellent",   "9:35–11:15 AM",       "Midday"),
    ("2026-08-07", "Strong",      "10 AM–12 PM",         "Opening volatility"),

    # Week 4: 11–17 Aug 2026
    ("2026-08-11", "High Risk",   "Small trades only",   "Aggressive option buying"),
    ("2026-08-14", "Recovery",    "1–2:30 PM",           "Opening volatility"),
    ("2026-08-17", "Very Good",   "9:35–11:00 AM",       "Late afternoon"),

    # Week 5: 18–24 Aug 2026
    ("2026-08-18", "Excellent",   "9:35–11:10 AM",       "Noon"),
    ("2026-08-19", "Very Strong", "1–3 PM",              "Emotional exits"),
    ("2026-08-24", "Excellent",   "Full morning session", "Late risky trades"),

    # Week 6: 25–31 Aug 2026
    ("2026-08-25", "Strong",      "9:40–11 AM",          "Greed trades"),
    ("2026-08-27", "Excellent",   "9:35–11:20 AM",       "Overtrading"),
    ("2026-08-30", "Risky",       "Avoid large exposure", "Whole day confusion"),
]

# Build lookup dict
_CALENDAR: dict[date, AstroDay] = {}
for _row in _RAW:
    _d = date.fromisoformat(_row[0])
    _CALENDAR[_d] = AstroDay(
        date=_d,
        strength=_row[1],
        best_window=_row[2],
        avoid=_row[3],
    )


class AstroCalendar:
    def today(self) -> Optional[AstroDay]:
        """Returns today's astro data if available, else None."""
        today_ist = datetime.now(IST).date()
        return _CALENDAR.get(today_ist)

    def get(self, d: date) -> Optional[AstroDay]:
        return _CALENDAR.get(d)

    def all_days(self) -> list[AstroDay]:
        return sorted(_CALENDAR.values(), key=lambda x: x.date)

    def week_ahead(self) -> list[AstroDay]:
        """Returns next 7 calendar days that have astro data."""
        from datetime import timedelta
        today_ist = datetime.now(IST).date()
        result = []
        for i in range(8):
            d = today_ist + timedelta(days=i)
            if d in _CALENDAR:
                result.append(_CALENDAR[d])
        return result


# Singleton
astro_calendar = AstroCalendar()
