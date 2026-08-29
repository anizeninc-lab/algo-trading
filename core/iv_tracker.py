# core/iv_tracker.py
#
# Minimal IV-percentile tracker for the put_calendar strategy.
#
# Upstox's option chain already returns per-strike IV in option_greeks.iv
# (confirmed via their API docs) -- this module does NOT compute IV itself,
# it just persists a daily history of front-week ATM IV so a *percentile*
# (not just an absolute IV level) can be computed, since "IV percentile < 40"
# is a rank against the underlying's own recent history, not a fixed number.
#
# Storage: flat JSON file, one float per trading day, capped at a rolling
# window (default 252 trading days ~ 1yr). Deliberately NOT a database table --
# this is a small, single-purpose series and JSON keeps it dependency-free
# and easy to inspect/edit by hand if it ever needs correcting.
#
# KNOWN LIMITATION (flagged, not silently hidden): on a fresh install there
# is no history yet, so percentile() returns None until MIN_HISTORY_DAYS
# of data has accumulated. put_calendar.py's entry logic treats "unknown
# percentile" as "percentile condition not met" -- it will only be able to
# enter via the backwardation condition until enough history builds up.
# There is no bootstrap/backfill from historical IV here; consider fetching
# a real historical IV series for backtesting if you need day-1 percentile
# data (out of scope for this simple version).

import json
import logging
import os
from datetime import date

logger = logging.getLogger(__name__)

DEFAULT_PATH = "configs/iv_history_nifty.json"
MAX_HISTORY_DAYS = 252
MIN_HISTORY_DAYS = 10  # below this, percentile is considered unreliable -> None


class IVTracker:
    def __init__(self, path: str = DEFAULT_PATH):
        self._path = path
        self._history: dict = self._load()

    def _load(self) -> dict:
        if not os.path.exists(self._path):
            return {}
        try:
            with open(self._path, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"[iv_tracker] Failed to load {self._path}: {e}")
            return {}

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
            with open(self._path, "w") as f:
                json.dump(self._history, f, indent=2)
        except Exception as e:
            logger.error(f"[iv_tracker] Failed to save {self._path}: {e}")

    def record_today(self, iv: float) -> None:
        """
        Record today's front-week ATM IV. Idempotent per calendar date --
        calling this repeatedly intraday just overwrites today's entry with
        the latest reading, so the stored series is effectively "last IV
        seen today" rather than a true daily close. Good enough for a
        rolling percentile; not intended as a tick-accurate IV archive.
        """
        if iv is None or iv <= 0:
            return
        today = date.today().isoformat()
        self._history[today] = round(float(iv), 4)
        # Trim to rolling window
        if len(self._history) > MAX_HISTORY_DAYS:
            for k in sorted(self._history.keys())[: len(self._history) - MAX_HISTORY_DAYS]:
                del self._history[k]
        self._save()

    def percentile(self, current_iv: float) -> float | None:
        """
        Returns the percentile rank (0-100) of current_iv against stored
        history, EXCLUDING today's own reading (so a strategy can't trivially
        rank itself against a value that includes itself as of the same call).
        Returns None if there isn't enough history yet -- callers must treat
        None as "cannot confirm low-IV condition", not as 0.
        """
        today = date.today().isoformat()
        values = [v for k, v in self._history.items() if k != today]
        if len(values) < MIN_HISTORY_DAYS:
            return None
        below_or_equal = sum(1 for v in values if v <= current_iv)
        return round(100.0 * below_or_equal / len(values), 1)

    @property
    def history_days(self) -> int:
        return len(self._history)


# Singleton -- import this everywhere, don't instantiate directly
iv_tracker = IVTracker()