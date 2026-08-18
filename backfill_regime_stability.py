"""
Reconstructs what regime_engine.get_regime_stability() WOULD have returned
at each survivor trade's entry_time, using the same formula:
    - last 10 regime classify() readings before/at entry_time
    - stability = (count of most-common regime in that window / window size) * 100

Cross-references against each trade's actual outcome (loss via REGIME_CHANGE
exit vs. other) to see whether entries that later got caught by a regime
flip had systematically lower stability scores than entries that didn't.

Run from ~/trading-algo:
    python3 backfill_regime_stability.py
"""
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime

LOG_FILES = [
    "logs/trading.log.4",
    "logs/trading.log.3",
    "logs/trading.log.2",
    "logs/trading.log.1",
    "logs/trading.log",
]

REGIME_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ \[INFO\] core\.regime_engine:.*→ (\w+) \["
)

HISTORY_MAX = 10


def load_regime_readings():
    readings = []  # list of (datetime, regime_label)
    for path in LOG_FILES:
        try:
            with open(path, "r", errors="replace") as f:
                for line in f:
                    m = REGIME_LINE_RE.match(line)
                    if m:
                        ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                        readings.append((ts, m.group(2)))
        except FileNotFoundError:
            print(f"[warn] {path} not found, skipping")
    readings.sort(key=lambda x: x[0])
    print(f"Loaded {len(readings)} regime readings spanning "
          f"{readings[0][0] if readings else 'N/A'} to {readings[-1][0] if readings else 'N/A'}")
    return readings


def stability_at(readings, entry_dt):
    """Replicates get_regime_stability(): last HISTORY_MAX readings at/before entry_dt."""
    window = [r for ts, r in readings if ts <= entry_dt][-HISTORY_MAX:]
    if len(window) < 2:
        return 50.0  # matches the engine's own fallback
    most_common, count = Counter(window).most_common(1)[0]
    return round(count / len(window) * 100, 1)


def load_survivor_trades(db_path="trade_log.db"):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT entry_time, exit_time, realised_pnl, notes FROM trades "
            "WHERE strategy='survivor' AND status='CLOSED' "
            "AND exit_time >= date('now', '-20 days') "
            "ORDER BY entry_time"
        ).fetchall()
    return rows


def main():
    readings = load_regime_readings()
    if not readings:
        print("No regime readings found — cannot proceed.")
        sys.exit(1)

    trades = load_survivor_trades()
    print(f"\n{len(trades)} closed survivor trades in the 20-day window.\n")

    regime_change_group = []
    other_group = []

    print(f"{'entry_time':<26} {'stability':>9} {'pnl':>10}  notes")
    print("-" * 70)
    for t in trades:
        entry_dt = datetime.fromisoformat(t["entry_time"])
        stab = stability_at(readings, entry_dt)
        pnl = t["realised_pnl"]
        notes = t["notes"] or ""
        is_regime_exit = "REGIME_CHANGE" in notes
        (regime_change_group if is_regime_exit else other_group).append(stab)
        print(f"{t['entry_time']:<26} {stab:>9.1f} {pnl:>10.2f}  {notes}")

    def summarize(label, group):
        if not group:
            print(f"{label}: no trades")
            return
        avg = sum(group) / len(group)
        print(f"{label}: n={len(group)}  avg_stability={avg:.1f}  "
              f"min={min(group):.1f}  max={max(group):.1f}  "
              f"sorted={sorted(group)}")

    print("\n" + "=" * 70)
    print("SUMMARY: stability-at-entry, grouped by eventual outcome")
    print("=" * 70)
    summarize("Later exited via REGIME_CHANGE (loss)", regime_change_group)
    summarize("Other exits", other_group)


if __name__ == "__main__":
    main()
