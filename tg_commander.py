# tg_commander.py
# Telegram command listener for emergency bot control.
# Runs as a separate PM2 process, always on.
#
# Commands:
#   /kill    — close all positions and halt trading
#   /status  — current P&L, open trades, regime
#   /resume  — clear halt, restart bot
#   /token <code> — exchange Upstox auth code (used by auto_token.py flow)

import os
import time
import requests
import subprocess
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = "8830735820:AAFxqjPAtRHcgK3Zcwotfm9szFGONYWXYpE"
TELEGRAM_CHAT  = "1196604785"
DASHBOARD_URL  = "http://localhost:8081"
POLL_INTERVAL  = 3   # seconds

# ── Telegram helpers ──────────────────────────────────────────────────────────
def tg_send(text: str) -> None:
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        print(f"[tg_commander] Send failed: {e}")


def tg_get_updates(offset: int) -> list:
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params={"offset": offset, "timeout": 10},
            timeout=15,
        )
        return resp.json().get("result", [])
    except Exception as e:
        print(f"[tg_commander] Poll failed: {e}")
        return []


# ── Command handlers ──────────────────────────────────────────────────────────
def handle_kill() -> str:
    try:
        resp = requests.post(
            f"{DASHBOARD_URL}/api/killswitch",
            json={"flatten": True},
            timeout=15,
        )
        data = resp.json()
        closed = data.get("closed", 0)
        return f"🚨 <b>KILL SWITCH ACTIVATED</b>\nClosed {closed} position(s). Trading halted."
    except Exception as e:
        return f"❌ Kill switch failed: {e}"


def handle_status() -> str:
    try:
        resp = requests.get(f"{DASHBOARD_URL}/api/bot-status", timeout=10)
        data = resp.json()
        status    = data.get("trading_status", "unknown")
        reason    = data.get("halt_reason", "")
        deployed  = data.get("capital_deployed", 0)
        remaining = data.get("capital_remaining", 0)
        daily_pnl = data.get("daily_loss_pct", 0)
        trades    = data.get("trades_today", 0)

        greeks = requests.get(f"{DASHBOARD_URL}/api/greeks", timeout=10).json()
        delta  = greeks.get("total_delta", 0)
        theta  = greeks.get("total_theta", 0)
        n_trades = greeks.get("trade_count", 0)

        lines = [
            f"📊 <b>Bot Status</b>",
            f"Status: <b>{status}</b>" + (f" — {reason}" if reason else ""),
            f"Capital deployed: ₹{deployed:,.0f} | Remaining: ₹{remaining:,.0f}",
            f"Trades today: {trades}",
            f"Open positions: {n_trades}",
            f"Portfolio delta: {delta:.3f} | Theta: ₹{theta:.0f}/day",
        ]
        return "\n".join(lines)
    except Exception as e:
        return f"❌ Status fetch failed: {e}"


def handle_resume() -> str:
    try:
        # Clear halt via killswitch reset endpoint
        requests.post(f"{DASHBOARD_URL}/api/killswitch/reset", timeout=10)
        # Restart bot
        subprocess.run(["pm2", "restart", "trading-bot", "--update-env"], timeout=15)
        return "✅ <b>Bot resumed</b> — halt cleared and restarted."
    except Exception as e:
        return f"❌ Resume failed: {e}"


def handle_token(code: str) -> str:
    try:
        result = subprocess.run(
            ["python3", "/home/ubuntu/trading-algo/auto_token.py", code],
            capture_output=True, text=True, timeout=30,
        )
        if "Token saved" in result.stdout:
            return "✅ <b>Token refreshed</b> — bot restarted with new token."
        return f"❌ Token exchange failed:\n{result.stdout}\n{result.stderr}"
    except Exception as e:
        return f"❌ Token exchange error: {e}"


# ── Main loop ─────────────────────────────────────────────────────────────────
def main():
    print("[tg_commander] Started — listening for commands...")
    tg_send("🤖 <b>Telegram Commander online</b>\nCommands: /kill /status /resume /token &lt;code&gt;")

    offset = 0
    # Skip old messages on startup
    updates = tg_get_updates(0)
    if updates:
        offset = updates[-1]["update_id"] + 1

    while True:
        updates = tg_get_updates(offset)
        for u in updates:
            offset = u["update_id"] + 1
            msg     = u.get("message", {})
            chat_id = str(msg.get("chat", {}).get("id", ""))
            text    = msg.get("text", "").strip()

            if chat_id != TELEGRAM_CHAT:
                continue  # ignore messages from other chats

            print(f"[tg_commander] Received: {text}")

            if text == "/kill":
                tg_send("⏳ Executing kill switch...")
                tg_send(handle_kill())

            elif text == "/status":
                tg_send(handle_status())

            elif text == "/resume":
                tg_send("⏳ Resuming bot...")
                tg_send(handle_resume())

            elif text.startswith("/token "):
                code = text.split("/token ", 1)[1].strip()
                tg_send("⏳ Exchanging token...")
                tg_send(handle_token(code))

            elif text.startswith("/"):
                tg_send(
                    "❓ Unknown command. Available:\n"
                    "/kill — close all positions\n"
                    "/status — P&amp;L and position summary\n"
                    "/resume — clear halt and restart\n"
                    "/token &lt;code&gt; — refresh Upstox token"
                )

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
