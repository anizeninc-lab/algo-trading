# auto_token.py
# Server-side Upstox token refresh via Telegram.
#
# Flow:
#   1. Run this script (via cron at 8:30 AM IST)
#   2. It sends you a Telegram message with the Upstox login URL
#   3. You open the URL, log in, copy the `code=` from the failed redirect URL
#   4. Reply to the Telegram message with: /token <code>
#   5. Script exchanges code for token, saves to .env, confirms via Telegram
#
# Usage:
#   python3 auto_token.py          # send auth URL + wait for code via Telegram
#   python3 auto_token.py <code>   # directly exchange a code (skip Telegram wait)

import os
import sys
import time
import urllib.parse
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
ENV_PATH       = Path(__file__).parent / ".env"
API_KEY        = os.getenv("UPSTOX_API_KEY", "986662e3-727b-4343-8f0b-40eb8b1e5e0f")
API_SECRET     = os.getenv("UPSTOX_API_SECRET", "")
REDIRECT_URI   = os.getenv("UPSTOX_REDIRECT_URI", "http://127.0.0.1:8080/callback")
TELEGRAM_TOKEN = "8830735820:AAFxqjPAtRHcgK3Zcwotfm9szFGONYWXYpE"
TELEGRAM_CHAT  = "1196604785"
POLL_TIMEOUT   = 300   # seconds to wait for code reply (5 min)
POLL_INTERVAL  = 5     # seconds between Telegram poll attempts

# ── Telegram helpers ──────────────────────────────────────────────────────────
def tg_send(text: str) -> int:
    """Send a Telegram message. Returns message_id."""
    resp = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT, "text": text, "parse_mode": "HTML"},
        timeout=10,
    )
    data = resp.json()
    if data.get("ok"):
        return data["result"]["message_id"]
    raise RuntimeError(f"Telegram send failed: {data}")


def tg_get_updates(offset: int = 0) -> list:
    """Poll Telegram for new messages."""
    resp = requests.get(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
        params={"offset": offset, "timeout": 10},
        timeout=15,
    )
    data = resp.json()
    return data.get("result", [])


def wait_for_code() -> str:
    """Poll Telegram until user sends /token <code>. Returns the code."""
    print("Waiting for /token <code> reply on Telegram...")
    offset = 0

    # Get current update offset to ignore old messages
    updates = tg_get_updates()
    if updates:
        offset = updates[-1]["update_id"] + 1

    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        updates = tg_get_updates(offset)
        for u in updates:
            offset = u["update_id"] + 1
            msg = u.get("message", {})
            chat_id = str(msg.get("chat", {}).get("id", ""))
            text = msg.get("text", "").strip()
            if chat_id == TELEGRAM_CHAT and text.startswith("/token "):
                code = text.split("/token ", 1)[1].strip()
                if code:
                    return code
        time.sleep(POLL_INTERVAL)

    raise TimeoutError("No /token reply received within 5 minutes")


# ── Token exchange ────────────────────────────────────────────────────────────
def exchange_code(code: str) -> str:
    """Exchange auth code for access token."""
    if not API_SECRET:
        raise RuntimeError("UPSTOX_API_SECRET not set in .env")
    resp = requests.post(
        "https://api.upstox.com/v2/login/authorization/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "code":          code,
            "client_id":     API_KEY,
            "client_secret": API_SECRET,
            "redirect_uri":  REDIRECT_URI,
            "grant_type":    "authorization_code",
        },
        timeout=15,
    )
    data = resp.json()
    if "access_token" in data:
        return data["access_token"]
    raise RuntimeError(f"Token exchange failed: {data}")


def save_token(token: str) -> None:
    """Write access token to .env file."""
    lines = ENV_PATH.read_text().splitlines(keepends=True)
    with open(ENV_PATH, "w") as f:
        for line in lines:
            if line.startswith("UPSTOX_ACCESS_TOKEN="):
                f.write(f"UPSTOX_ACCESS_TOKEN={token}\n")
            else:
                f.write(line)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    # Build auth URL
    auth_url = (
        "https://api.upstox.com/v2/login/authorization/dialog"
        f"?client_id={API_KEY}"
        f"&redirect_uri={urllib.parse.quote(REDIRECT_URI)}"
        f"&response_type=code"
    )

    # If code passed as argument, skip Telegram wait
    if len(sys.argv) > 1:
        code = sys.argv[1].strip()
        print(f"Using provided code: {code}")
    else:
        # Send auth URL via Telegram
        msg = (
            "🔐 <b>Upstox Token Refresh</b>\n\n"
            "Tap the link below, log in, then copy the <code>code=</code> "
            "value from the browser URL after login:\n\n"
            f"<a href='{auth_url}'>👉 Login to Upstox</a>\n\n"
            "Then reply here with:\n"
            "<code>/token YOUR_CODE_HERE</code>"
        )
        tg_send(msg)
        print("Auth URL sent via Telegram. Waiting for /token reply...")
        code = wait_for_code()
        print(f"Code received: {code}")

    # Exchange and save
    print("Exchanging code for token...")
    token = exchange_code(code)
    save_token(token)

    # Confirm via Telegram
    tg_send("✅ <b>Token refreshed successfully!</b>\nBot is ready for today's session.")
    print("✅ Token saved to .env")

    # Restart bot with new token
    os.system("pm2 restart trading-bot --update-env")
    print("✅ Bot restarted with new token")


if __name__ == "__main__":
    main()
