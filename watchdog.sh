#!/bin/bash
# watchdog.sh — runs every 5 min via cron. Restarts trading-bot if PM2
# reports it as down. Dashboard health is logged but does NOT trigger restart.
cd /home/ubuntu/trading-algo || exit 1
TELEGRAM_TOKEN="8830735820:AAFxqjPAtRHcgK3Zcwotfm9szFGONYWXYpE"
TELEGRAM_CHAT_ID="1196604785"
send_alert() {
  local msg="$1"
  curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage" \
    -d chat_id="${TELEGRAM_CHAT_ID}" \
    -d text="🐕 WATCHDOG: ${msg}" > /dev/null
}
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
# Check dashboard health — log only, never restart on this alone
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 8 http://127.0.0.1:8081/)
if [ "$HTTP_CODE" != "200" ]; then
    echo "[$TIMESTAMP] Dashboard not responding (HTTP $HTTP_CODE) — noted but NOT restarting."
fi
# Only restart if PM2 reports trading-bot as not online
PM2_STATUS=$(pm2 jlist | python3 -c "
import json,sys
try:
    data = json.load(sys.stdin)
    for p in data:
        if p['name'] == 'trading-bot':
            print(p['pm2_env']['status'])
            sys.exit(0)
    print('not_found')
except Exception:
    print('error')
")
if [ "$PM2_STATUS" != "online" ]; then
    echo "[$TIMESTAMP] trading-bot PM2 status is '$PM2_STATUS'. Restarting."
    pm2 restart trading-bot --update-env
    send_alert "trading-bot PM2 status was '${PM2_STATUS}' — auto-restarted by watchdog."
    exit 0
fi
echo "[$TIMESTAMP] OK — PM2 status: $PM2_STATUS | Dashboard: HTTP $HTTP_CODE"
