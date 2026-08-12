# core/post_mortem.py
#
# Descriptive post-mortem analysis of closed trades. Reads trade_log.db,
# groups by exit reason / entry_context features / time-of-day, and prints
# a plain-language report.
#
# EXPLICITLY NOT INCLUDED, ON PURPOSE (see handoff discussion, Aug 12 2026):
#   - No autonomous parameter changes. This module only reads and reports.
#   - No walk-forward optimization or backtesting -- this repo has no
#     historical backtesting engine yet. Building a fake one here would
#     create false statistical confidence, which is worse than not having
#     the feature at all.
#   - No config promotion of any kind. See configs/candidate_config.json
#     for where human-reviewed proposed changes should be written, manually,
#     by whoever reads this report -- never by code.
#
# This module answers "what happened and does it cluster around anything
# recognisable" -- nothing more. Treat every finding here as a hypothesis
# to think about, not a conclusion to act on automatically.

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta

from core.trade_log import trade_logger

logger = logging.getLogger(__name__)


def _parse_entry_context(raw: str) -> dict:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _holding_minutes(entry_time: str, exit_time: str) -> float:
    try:
        t1 = datetime.fromisoformat(entry_time)
        t2 = datetime.fromisoformat(exit_time)
        return round((t2 - t1).total_seconds() / 60.0, 1)
    except Exception:
        return 0.0


def _summarize(trades: list) -> dict:
    """Basic win-rate/avg-win/avg-loss/profit-factor summary for a group of trades."""
    if not trades:
        return {"count": 0, "win_rate": None, "avg_win": None, "avg_loss": None, "profit_factor": None, "net_pnl": 0.0}
    pnls = [t.get("realised_pnl") or 0.0 for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "count":         len(trades),
        "win_rate":      round(len(wins) / len(trades) * 100, 1) if trades else None,
        "avg_win":       round(gross_win / len(wins), 2) if wins else None,
        "avg_loss":      round(gross_loss / len(losses), 2) if losses else None,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else (float("inf") if gross_win > 0 else None),
        "net_pnl":       round(sum(pnls), 2),
    }


def generate_report(strategy: str = None, lookback_days: int = 14, limit: int = 500) -> str:
    """
    Builds a plain-text post-mortem report for closed trades in the lookback
    window. Purely descriptive -- see module docstring.
    """
    all_trades = trade_logger.get_trades(strategy=strategy, status="CLOSED", limit=limit)

    cutoff = datetime.now() - timedelta(days=lookback_days)
    trades = []
    for t in all_trades:
        try:
            if datetime.fromisoformat(t.get("entry_time", "")) >= cutoff:
                trades.append(t)
        except Exception:
            continue

    lines = []
    lines.append("=" * 70)
    lines.append(f"POST-MORTEM REPORT | last {lookback_days} days | {len(trades)} closed trades")
    if strategy:
        lines.append(f"Strategy filter: {strategy}")
    lines.append("=" * 70)

    if not trades:
        lines.append("No closed trades in this window.")
        return "\n".join(lines)

    overall = _summarize(trades)
    lines.append("")
    lines.append(f"OVERALL: {overall['count']} trades | net P&L: \u20b9{overall['net_pnl']:,.2f} | "
                 f"win rate: {overall['win_rate']}% | avg win: \u20b9{overall['avg_win']} | "
                 f"avg loss: \u20b9{overall['avg_loss']} | profit factor: {overall['profit_factor']}")

    # ── By strategy ──────────────────────────────────────────────────────
    by_strategy = defaultdict(list)
    for t in trades:
        by_strategy[t.get("strategy", "unknown")].append(t)
    lines.append("")
    lines.append("-- By strategy --")
    for strat, group in sorted(by_strategy.items()):
        s = _summarize(group)
        lines.append(f"  {strat:20s} | {s['count']:3d} trades | net \u20b9{s['net_pnl']:>10,.2f} | "
                     f"win rate {s['win_rate']}% | PF {s['profit_factor']}")

    # ── By exit reason (parsed from notes) ──────────────────────────────
    by_reason = defaultdict(list)
    for t in trades:
        reason = (t.get("notes") or "UNKNOWN").split("|")[0].strip()
        by_reason[reason].append(t)
    lines.append("")
    lines.append("-- By exit reason --")
    for reason, group in sorted(by_reason.items(), key=lambda kv: -len(kv[1])):
        s = _summarize(group)
        lines.append(f"  {reason:25s} | {s['count']:3d} trades | net \u20b9{s['net_pnl']:>10,.2f} | "
                     f"win rate {s['win_rate']}%")

    # ── By hour of day (entry time) ──────────────────────────────────────
    by_hour = defaultdict(list)
    for t in trades:
        try:
            hour = datetime.fromisoformat(t.get("entry_time", "")).hour
            by_hour[hour].append(t)
        except Exception:
            continue
    lines.append("")
    lines.append("-- By entry hour (IST, server-local) --")
    for hour in sorted(by_hour.keys()):
        s = _summarize(by_hour[hour])
        lines.append(f"  {hour:02d}:00 | {s['count']:3d} trades | net \u20b9{s['net_pnl']:>10,.2f} | win rate {s['win_rate']}%")

    # ── By regime / entry_context features (where present) ──────────────
    by_regime = defaultdict(list)
    has_context = 0
    for t in trades:
        ctx = _parse_entry_context(t.get("entry_context", ""))
        if ctx:
            has_context += 1
        regime = ctx.get("gex_regime") or ctx.get("direction") or "no_context_captured"
        by_regime[regime].append(t)
    lines.append("")
    lines.append(f"-- By entry_context regime/direction ({has_context}/{len(trades)} trades have context captured) --")
    for regime, group in sorted(by_regime.items(), key=lambda kv: -len(kv[1])):
        s = _summarize(group)
        lines.append(f"  {str(regime):25s} | {s['count']:3d} trades | net \u20b9{s['net_pnl']:>10,.2f} | win rate {s['win_rate']}%")

    # ── Holding time distribution ─────────────────────────────────────────
    holding_times = [
        _holding_minutes(t.get("entry_time", ""), t.get("exit_time", ""))
        for t in trades if t.get("exit_time")
    ]
    if holding_times:
        avg_hold = round(sum(holding_times) / len(holding_times), 1)
        lines.append("")
        lines.append(f"-- Holding time -- avg: {avg_hold} min | min: {min(holding_times)} | max: {max(holding_times)}")

    lines.append("")
    lines.append("(Descriptive only -- no automatic changes made. Review and decide manually.)")
    lines.append("=" * 70)

    return "\n".join(lines)


def print_report(strategy: str = None, lookback_days: int = 14) -> None:
    report = generate_report(strategy=strategy, lookback_days=lookback_days)
    print(report)
    logger.info(f"[post_mortem] Report generated for strategy={strategy or 'ALL'}, lookback={lookback_days}d")


if __name__ == "__main__":
    import sys
    strat = sys.argv[1] if len(sys.argv) > 1 else None
    print_report(strategy=strat)