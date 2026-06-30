# core/alerting.py
# Centralized alerting system — Telegram + Dashboard exec log
# Fires on: order reject, fill mismatch, GTT failure, WS disconnect,
#           state mismatch, margin breach, daily loss breach

import logging
import os
import requests
from datetime import datetime
from typing import Optional
import pytz

logger = logging.getLogger(__name__)

# ── Telegram config ────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = "8830735820:AAFxqjPAtRHcgK3Zcwotfm9szFGONYWXYpE"
TELEGRAM_CHAT_ID = "1196604785"
TELEGRAM_URL     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

# ── Alert levels ───────────────────────────────────────────────────────────────
LEVEL_INFO     = "ℹ️"
LEVEL_WARNING  = "⚠️"
LEVEL_CRITICAL = "🚨"
LEVEL_PROFIT   = "✅"
LEVEL_LOSS     = "🔴"
LEVEL_TRADE    = "📊"


def _ist_now() -> str:
    return datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%H:%M:%S")


def send_telegram(message: str, level: str = LEVEL_INFO) -> bool:
    """Send a Telegram message. Returns True if successful."""
    try:
        text = f"{level} *Rahul Trading Bot*\n`{_ist_now()} IST`\n\n{message}"
        resp = requests.post(
            TELEGRAM_URL,
            json={
                "chat_id":    TELEGRAM_CHAT_ID,
                "text":       text,
                "parse_mode": "Markdown",
            },
            timeout=5,
        )
        if resp.status_code == 200:
            return True
        logger.warning(f"[alerting] Telegram failed: {resp.status_code} {resp.text[:100]}")
        return False
    except Exception as e:
        logger.warning(f"[alerting] Telegram error: {e}")
        return False


def alert_trade_opened(symbol: str, direction: str, entry: float, qty: int, strike: int) -> None:
    send_telegram(
        f"{LEVEL_TRADE} *TRADE OPENED*\n"
        f"Direction: `{direction}`\n"
        f"Strike: `{strike}`\n"
        f"Entry: `₹{entry:.2f}`\n"
        f"Qty: `{qty}`\n"
        f"Premium: `₹{entry * qty:.0f}`",
        LEVEL_TRADE
    )


def alert_trade_closed(symbol: str, entry: float, exit_price: float, qty: int, pnl: float, reason: str) -> None:
    level = LEVEL_PROFIT if pnl >= 0 else LEVEL_LOSS
    send_telegram(
        f"{level} *TRADE CLOSED* — {reason}\n"
        f"Entry: `₹{entry:.2f}` → Exit: `₹{exit_price:.2f}`\n"
        f"Qty: `{qty}`\n"
        f"P&L: `₹{pnl:+.2f}`",
        level
    )


def alert_order_rejected(symbol: str, reason: str) -> None:
    send_telegram(
        f"*ORDER REJECTED*\n"
        f"Symbol: `{symbol}`\n"
        f"Reason: `{reason}`",
        LEVEL_CRITICAL
    )


def alert_gtt_failed(symbol: str, error: str) -> None:
    send_telegram(
        f"*GTT PLACEMENT FAILED*\n"
        f"Symbol: `{symbol}`\n"
        f"Error: `{error}`\n"
        f"⚠️ Manual stop loss required!",
        LEVEL_CRITICAL
    )


def alert_daily_loss_hit(pnl: float, limit: float) -> None:
    send_telegram(
        f"*DAILY LOSS LIMIT HIT*\n"
        f"P&L: `₹{pnl:.2f}`\n"
        f"Limit: `₹{limit:.2f}`\n"
        f"🛑 Bot halted for today",
        LEVEL_CRITICAL
    )


def alert_websocket_down(error: str) -> None:
    send_telegram(
        f"*WEBSOCKET DISCONNECTED*\n"
        f"Error: `{error}`\n"
        f"⚠️ LTP feed interrupted — SL/TP may not fire",
        LEVEL_WARNING
    )

def alert_vix_stale(minutes_stale: float, last_known_vix: float) -> None:
    send_telegram(
        f"*VIX FEED STALE*\n"
        f"No successful VIX fetch (broker or NSE) in {minutes_stale:.1f} min\n"
        f"Last known VIX: {last_known_vix:.2f}\n"
        f"🚨 Forcing EXTREME regime (trading halted) until feed recovers",
        LEVEL_WARNING
    )


def alert_reconcile_mismatch(trade_id: str, symbol: str) -> None:
    send_telegram(
        f"*POSITION MISMATCH*\n"
        f"Trade `{trade_id[:8]}` in DB but NOT in broker\n"
        f"Symbol: `{symbol}`\n"
        f"Auto-closed in DB",
        LEVEL_WARNING
    )


def alert_system_start(nifty_price: float, regime: str, paper: bool) -> None:
    mode = "📄 PAPER" if paper else "💰 LIVE"
    send_telegram(
        f"*BOT STARTED* {mode}\n"
        f"Nifty: `{nifty_price:.2f}`\n"
        f"Regime: `{regime}`",
        LEVEL_INFO
    )


def alert_eod_close(total_pnl: float, trades: int) -> None:
    level = LEVEL_PROFIT if total_pnl >= 0 else LEVEL_LOSS
    send_telegram(
        f"*EOD — ALL POSITIONS CLOSED*\n"
        f"Today's P&L: `₹{total_pnl:+.2f}`\n"
        f"Trades: `{trades}`",
        level
    )


def alert_breakeven_locked(symbol: str, pnl: float) -> None:
    send_telegram(
        f"*🔒 BREAKEVEN LOCKED*\n"
        f"Symbol: `{symbol}`\n"
        f"P&L at lock: `₹{pnl:.0f}`\n"
        f"SL moved to entry — cannot lose",
        LEVEL_INFO
    )
