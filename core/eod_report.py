# core/eod_report.py
# Generates a structured EOD report (JSON + Markdown) at market close.
import json
import logging
import os
from datetime import datetime
from pathlib import Path

import pytz

logger = logging.getLogger(__name__)

REPORTS_DIR = Path("reports")


def generate_eod_report(reason: str = "AUTO_STOP", combo=None) -> Path | None:
    """
    Build and write EOD report to reports/YYYY-MM-DD.json and reports/YYYY-MM-DD.md
    Returns the JSON path on success, None on failure.
    """
    try:
        from core.trade_log import trade_logger
        from core.risk_manager import risk_manager
        from core.state_store import state_store

        REPORTS_DIR.mkdir(exist_ok=True)
        ist = pytz.timezone("Asia/Kolkata")
        now = datetime.now(ist)
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M:%S")

        # ── Trade summary ──────────────────────────────────────────────────────
        overall   = trade_logger.get_pnl_summary(today_only=True)
        by_strat  = {}
        for strat in ["survivor", "wave_extractor", "bn_survivor"]:
            by_strat[strat] = trade_logger.get_pnl_summary(strategy=strat, today_only=True)

        # ── Risk state ────────────────────────────────────────────────────────
        risk_state = {
            "daily_pnl":        dict(risk_manager._daily_pnl),
            "trade_counts":     dict(risk_manager._trade_counts),
            "deployed_capital": dict(risk_manager._deployed_capital),
            "system_halted":    risk_manager._system_halted,
            "halt_reason":      risk_manager._halt_reason,
        }

        # ── System state ──────────────────────────────────────────────────────
        global_summary = state_store.get_global_summary()

        # ── Combo P&L ─────────────────────────────────────────────────────────
        combined_pnl = 0.0
        if combo:
            try:
                combined_pnl = combo._get_combined_pnl()
            except Exception:
                pass

        # ── Build report dict ─────────────────────────────────────────────────
        report = {
            "date":             date_str,
            "generated_at":     f"{date_str} {time_str} IST",
            "stop_reason":      reason,
            "paper_trade":      os.getenv("PAPER_TRADE", "false").lower() == "true",
            "summary": {
                "total_trades":  overall.get("total_trades", 0),
                "winning":       overall.get("winning", 0),
                "losing":        overall.get("losing", 0),
                "win_rate_pct":  overall.get("win_rate", 0.0),
                "net_pnl":       overall.get("total_pnl", 0.0),
                "combined_pnl":  round(combined_pnl, 2),
            },
            "by_strategy":      by_strat,
            "risk_state":       risk_state,
            "system":           global_summary,
        }

        # ── Write JSON ────────────────────────────────────────────────────────
        json_path = REPORTS_DIR / f"{date_str}.json"
        with open(json_path, "w") as f:
            json.dump(report, f, indent=2, default=str)

        # ── Write Markdown ────────────────────────────────────────────────────
        md_path = REPORTS_DIR / f"{date_str}.md"
        mode = "📄 PAPER" if report["paper_trade"] else "💰 LIVE"
        lines = [
            f"# EOD Report — {date_str} {mode}",
            f"**Generated:** {report['generated_at']}  ",
            f"**Stop reason:** {reason}",
            "",
            "## Summary",
            f"| Metric | Value |",
            f"|---|---|",
            f"| Total trades | {report['summary']['total_trades']} |",
            f"| Winning | {report['summary']['winning']} |",
            f"| Losing | {report['summary']['losing']} |",
            f"| Win rate | {report['summary']['win_rate_pct']}% |",
            f"| Net P&L | ₹{report['summary']['net_pnl']:.2f} |",
            f"| Combined P&L | ₹{report['summary']['combined_pnl']:.2f} |",
            "",
            "## By Strategy",
            "| Strategy | Trades | Win Rate | Net P&L |",
            "|---|---|---|---|",
        ]
        for strat, s in by_strat.items():
            lines.append(
                f"| {strat} | {s.get('total_trades',0)} | "
                f"{s.get('win_rate',0.0)}% | ₹{s.get('total_pnl',0.0):.2f} |"
            )
        lines += [
            "",
            "## Risk State",
            f"- Halted: {risk_state['system_halted']}",
            f"- Halt reason: {risk_state['halt_reason'] or 'None'}",
            f"- Trade counts: {risk_state['trade_counts']}",
            f"- Daily P&L: {risk_state['daily_pnl']}",
        ]
        with open(md_path, "w") as f:
            f.write("\n".join(lines))

        logger.info(f"[EOD] Report written: {json_path} + {md_path}")

        # ── Telegram summary ──────────────────────────────────────────────────
        try:
            from core.alerting import send_telegram, LEVEL_INFO
            # 'reason' is often something like AUTO_STOP, MAX_DAILY_LOSS, or
            # SCHEDULED_EOD -- all contain underscores, which Telegram's
            # Markdown parse_mode treats as unmatched italic markers and
            # rejects with a 400 "can't parse entities" error. This has
            # likely been silently failing on every call (AUTO_STOP and
            # MAX_DAILY_LOSS both have underscores too, not just the new
            # SCHEDULED_EOD reason) -- same escaping pattern already used
            # for `reason` in alert_trade_closed() below, just missing here.
            safe_reason = reason.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`")
            tg_msg = (
                f"📊 EOD Report {date_str} {mode}\n"
                f"Trades: {report['summary']['total_trades']} | "
                f"Win: {report['summary']['win_rate_pct']}%\n"
                f"Net P&L: ₹{report['summary']['net_pnl']:.2f}\n"
                f"Stop: {safe_reason}"
            )
            send_telegram(tg_msg, LEVEL_INFO)
        except Exception:
            pass

        return json_path

    except Exception as e:
        logger.error(f"[EOD] Report generation failed: {e}")
        return None
