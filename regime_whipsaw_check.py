#!/usr/bin/env python3
"""
Regime whipsaw diagnostic (audit A3).

Correlates regime_engine transition events (from trading.log*) against
survivor trades that exited via REGIME_CHANGE / HEDGE_EXIT_REGIME_CHANGE,
to test whether those losing exits cluster right after short-lived
("whipsaw") regime states, vs. long-held genuine shifts.

Run from /home/ubuntu/trading-algo:
    python3 regime_whipsaw_check.py
"""
import glob
import re
import sqlite3
from datetime import datetime

LOG_GLOB = "logs/trading.log*"
DB_PATH = "trade_log.db"

LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ .*core\.regime_engine.*"
    r"transition=(?P<transition>yes|no).*(?:->|\u2192)\s*(?P<regime>\S+)"
)


def parse_ts(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def load_transitions():
    events = []
    files = glob.glob(LOG_GLOB)
    print(f"Scanning {len(files)} log files: {files}")
    for fname in files:
        try:
            with open(fname, "r", errors="ignore") as f:
                for line in f:
                    if "regime_engine" not in line or "transition=" not in line:
                        continue
                    m = LINE_RE.search(line)
                    if not m:
                        continue
                    if m.group("transition") == "yes":
                        events.append((parse_ts(m.group("ts")), m.group("regime")))
        except FileNotFoundError:
            continue
    events.sort(key=lambda x: x[0])
    return events


def load_regime_exit_trades():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT id, entry_time, exit_time, realised_pnl, notes, entry_context
        FROM trades
        WHERE strategy = 'survivor'
          AND status != 'OPEN'
          AND (notes LIKE '%REGIME_CHANGE%')
        ORDER BY exit_time ASC
        """
    ).fetchall()
    con.close()
    return rows


def nearest_prior_transition(transitions, t):
    prior = None
    for ts, regime in transitions:
        if ts <= t:
            prior = (ts, regime)
        else:
            break
    return prior


def main():
    transitions = load_transitions()
    print(f"Found {len(transitions)} regime transitions across log window.")
    if not transitions:
        print("No transitions found -- check LINE_RE against actual log format.")
        return

    trades = load_regime_exit_trades()
    print(f"Found {len(trades)} survivor trades with a REGIME_CHANGE-related exit.\n")

    durations = []
    for i in range(len(transitions) - 1):
        ts, regime = transitions[i]
        next_ts, _ = transitions[i + 1]
        durations.append((ts, regime, (next_ts - ts).total_seconds()))

    print(f"{'trade_id':<10} {'entry':<19} {'exit':<19} {'pnl':>10} {'regime_at_entry':<14} {'state_age_at_entry_s':>20} {'flips_during_trade':>18}")
    whipsaw_flag_count = 0
    for row in trades:
        try:
            entry_t = parse_ts(row["entry_time"][:19])
            exit_t = parse_ts(row["exit_time"][:19])
        except Exception:
            continue

        prior = nearest_prior_transition(transitions, entry_t)
        if prior is None:
            continue
        trans_ts, regime = prior
        state_age = (entry_t - trans_ts).total_seconds()

        flips_during = sum(1 for ts, _ in transitions if entry_t < ts <= exit_t)

        is_whipsaw_entry = state_age < 300
        if is_whipsaw_entry or flips_during >= 2:
            whipsaw_flag_count += 1

        print(f"{row['id'][:8]:<10} {row['entry_time'][:19]:<19} {row['exit_time'][:19]:<19} "
              f"{row['realised_pnl']:>10.2f} {regime:<14} {state_age:>20.0f} {flips_during:>18}")

    print(f"\n{whipsaw_flag_count}/{len(trades)} regime-exit trades show whipsaw signature "
          f"(entered <5min after a flip, or saw 2+ flips before exiting).")

    if durations:
        secs = sorted(d[2] for d in durations)
        n = len(secs)
        median = secs[n // 2]
        under_5min = sum(1 for s in secs if s < 300)
        print(f"\nAll regime states in window: {n}")
        print(f"Median state duration: {median:.0f}s ({median/60:.1f} min)")
        print(f"States lasting <5 min: {under_5min}/{n} ({100*under_5min/n:.1f}%)")


if __name__ == "__main__":
    main()
