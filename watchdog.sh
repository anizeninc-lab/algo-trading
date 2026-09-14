#!/bin/bash
cd /home/ubuntu/trading-algo || exit 1

# ── Weekend guard ────────────────────────────────────────────────────────
# Markets are closed Sat/Sun — don't check or auto-restart trading-bot on
# these days, even if it's intentionally stopped. date +%u gives 1=Mon..7=Sun.
DOW=$(date +%u)
if [ "$DOW" -ge 6 ]; then
    exit 0
fi
TELEGRAM_TOKEN=$(grep TELEGRAM_TOKEN .env | cut -d '=' -f2)
TELEGRAM_CHAT_ID=$(grep TELEGRAM_CHAT .env | cut -d '=' -f2)
STATE_FILE="/tmp/trading_bot_last_restarts.txt"

send_alert() {
  curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage" \
    -d chat_id="${TELEGRAM_CHAT_ID}" -d text="🐕 WATCHDOG: $1" > /dev/null
}

TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
PM2_JSON=$(pm2 jlist)
STATUS=$(echo "$PM2_JSON" | python3 -c "
import json,sys
data=json.load(sys.stdin)
for p in data:
    if p['name']=='trading-bot': print(p['pm2_env']['status'])
")
RESTARTS=$(echo "$PM2_JSON" | python3 -c "
import json,sys
data=json.load(sys.stdin)
for p in data:
    if p['name']=='trading-bot': print(p['pm2_env']['restart_time'])
")

LAST_RESTARTS=$(cat "$STATE_FILE" 2>/dev/null || echo "$RESTARTS")
DIFF=$((RESTARTS - LAST_RESTARTS))
echo "$RESTARTS" > "$STATE_FILE"

# Maintenance-mode guard (added 2026-09-14): a deliberate /stop via
# Telegram (or manual `pm2 stop`) was being auto-resurrected within 5
# minutes by this exact block, with no way to tell "intentionally
# stopped" apart from "crashed" -- found live when a Monday intentional
# stop kept getting restarted every 5 min, repeatedly failing to get
# real ticks and spamming WEBSOCKET CRITICAL alerts. If this flag file
# exists, skip the restart-on-not-online logic entirely.
if [ -f "/home/ubuntu/trading-algo/.maintenance_mode" ]; then
    echo "[$TIMESTAMP] Maintenance mode active -- not auto-restarting."
    exit 0
fi

if [ "$STATUS" != "online" ]; then
    echo "[$TIMESTAMP] trading-bot status '$STATUS'. Restarting."
    pm2 restart trading-bot --update-env
    send_alert "trading-bot was '${STATUS}' — auto-restarted."
    exit 0
fi

if [ "$DIFF" -ge 2 ]; then
    send_alert "⚠️ trading-bot restarted ${DIFF}x since last check (total: ${RESTARTS}). Possible crash loop — check memory/logs."
    echo "[$TIMESTAMP] ALERT: ${DIFF} restarts since last check."
fi

echo "[$TIMESTAMP] OK — status: $STATUS | restarts: $RESTARTS (+$DIFF)"
