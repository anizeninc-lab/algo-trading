# core/trade_journal.py
"""
Post-trade journal — generates daily summary report.
Run automatically at EOD or on demand.

Output:
- Console log
- Telegram message
- JSON file in /home/ubuntu/trading-algo/reports/
"""
import json
import logging
import os
from datetime import datetime, date
from pathlib import Path
from typing import List, Dict

import pytz

logger = logging.getLogger(__name__)
IST    = pytz.timezone("Asia/Kolkata")
REPORT_DIR = Path("/home/ubuntu/trading-algo/reports")


def generate_daily_report(target_date: date = None) -> dict:
    """Generate post-trade report for a given date (default: today)."""
    from core.trade_log import trade_logger

    if target_date is None:
        target_date = datetime.now(IST).date()

    date_str = target_date.isoformat()

    # Fetch all trades for the day
    all_trades = trade_logger.get_trades()
    day_trades = [
        t for t in all_trades
        if t.get("entry_time", "").startswith(date_str)
    ]

    if not day_trades:
        return {"date": date_str, "message": "No trades today"}

    # ── Metrics ───────────────────────────────────────────────────────────
    closed  = [t for t in day_trades if t["status"] == "CLOSED"]
    open_t  = [t for t in day_trades if t["status"] == "OPEN"]
    winners = [t for t in closed if t.get("realised_pnl", 0) > 0]
    losers  = [t for t in closed if t.get("realised_pnl", 0) < 0]

    total_pnl   = sum(t.get("realised_pnl", 0) for t in closed)
    win_rate    = len(winners) / len(closed) * 100 if closed else 0
    avg_win     = sum(t["realised_pnl"] for t in winners) / len(winners) if winners else 0
    avg_loss    = sum(t["realised_pnl"] for t in losers)  / len(losers)  if losers  else 0
    best_trade  = max((t["realised_pnl"] for t in closed), default=0)
    worst_trade = min((t["realised_pnl"] for t in closed), default=0)

    # ── Exit reason breakdown ─────────────────────────────────────────────
    exit_reasons = {}
    for t in closed:
        reason = t.get("notes", "UNKNOWN").split("|")[0].strip()
        exit_reasons[reason] = exit_reasons.get(reason, 0) + 1

    # ── Per-trade details ─────────────────────────────────────────────────
    trade_details = []
    for t in day_trades:
        entry_t = t.get("entry_time", "")[:16]
        exit_t  = t.get("exit_time",  "")[:16]
        pnl     = t.get("realised_pnl", 0)
        trade_details.append({
            "id":          t["id"][:8],
            "symbol":      t["symbol"],
            "direction":   "CE" if "CE" in t["symbol"] else "PE",
            "entry_price": t.get("entry_price", 0),
            "exit_price":  t.get("exit_price", 0),
            "pnl":         pnl,
            "status":      t["status"],
            "entry_time":  entry_t,
            "exit_time":   exit_t,
            "notes":       t.get("notes", ""),
        })

    report = {
        "date":          date_str,
        "total_trades":  len(day_trades),
        "closed_trades": len(closed),
        "open_trades":   len(open_t),
        "winners":       len(winners),
        "losers":        len(losers),
        "total_pnl":     round(total_pnl, 2),
        "win_rate":      round(win_rate, 1),
        "avg_win":       round(avg_win, 2),
        "avg_loss":      round(avg_loss, 2),
        "best_trade":    round(best_trade, 2),
        "worst_trade":   round(worst_trade, 2),
        "exit_reasons":  exit_reasons,
        "trades":        trade_details,
    }

    # ── Save to file ──────────────────────────────────────────────────────
    REPORT_DIR.mkdir(exist_ok=True)
    report_file = REPORT_DIR / f"report_{date_str}.json"
    with open(report_file, "w") as f:
        json.dump(report, f, indent=2)
    logger.info(f"[journal] Report saved: {report_file}")

    # ── Send Telegram summary ─────────────────────────────────────────────
    try:
        from core.alerting import send_telegram, LEVEL_INFO, LEVEL_PROFIT, LEVEL_LOSS
        level = LEVEL_PROFIT if total_pnl >= 0 else LEVEL_LOSS
        msg = (
            f"📋 *DAILY REPORT — {date_str}*\n\n"
            f"Total P&L: `₹{total_pnl:+.2f}`\n"
            f"Trades: {len(closed)} closed | {len(open_t)} open\n"
            f"Win Rate: {win_rate:.0f}% ({len(winners)}W / {len(losers)}L)\n"
            f"Avg Win: ₹{avg_win:.0f} | Avg Loss: ₹{avg_loss:.0f}\n"
            f"Best: ₹{best_trade:+.0f} | Worst: ₹{worst_trade:+.0f}\n\n"
        )
        for td in trade_details:
            emoji = "✅" if td["pnl"] > 0 else "🔴" if td["pnl"] < 0 else "⚪"
            msg += f"{emoji} {td['direction']} @ ₹{td['entry_price']} → ₹{td['exit_price']} = ₹{td['pnl']:+.0f}\n"
        send_telegram(msg, level)
    except Exception as e:
        logger.warning(f"[journal] Telegram report failed: {e}")

    return report


def print_report(report: dict) -> None:
    """Pretty print report to console."""
    if "message" in report:
        print(report["message"])
        return
    print(f"\n{'='*50}")
    print(f"DAILY TRADE REPORT — {report['date']}")
    print(f"{'='*50}")
    print(f"Total P&L:    ₹{report['total_pnl']:+.2f}")
    print(f"Trades:       {report['closed_trades']} closed, {report['open_trades']} open")
    print(f"Win Rate:     {report['win_rate']}% ({report['winners']}W/{report['losers']}L)")
    print(f"Avg Win/Loss: ₹{report['avg_win']:+.0f} / ₹{report['avg_loss']:+.0f}")
    print(f"Best/Worst:   ₹{report['best_trade']:+.0f} / ₹{report['worst_trade']:+.0f}")
    print(f"\nExit Reasons: {report['exit_reasons']}")
    print(f"\nTrade Details:")
    for t in report["trades"]:
        print(f"  {t['id']} | {t['direction']} | {t['entry_price']} → {t['exit_price']} | ₹{t['pnl']:+.0f} | {t['notes'][:30]}")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    report = generate_daily_report()
    print_report(report)
