#!/bin/bash
# Check if Nifty ticks are flowing — restart only if silent for 3+ minutes
# Only runs during market hours (Mon-Fri, 09:00-15:35 IST)

IST_HOUR=$(TZ="Asia/Kolkata" date +%H)
IST_MIN=$(TZ="Asia/Kolkata" date +%M)
IST_DOW=$(TZ="Asia/Kolkata" date +%u)  # 1=Mon, 7=Sun
IST_TIME=$((IST_HOUR * 60 + IST_MIN))
MARKET_OPEN=$((9 * 60))      # 09:00
MARKET_CLOSE=$((15 * 60 + 35))  # 15:35

# Skip outside market hours or on weekends
if [ "$IST_DOW" -ge 6 ] || [ "$IST_TIME" -lt "$MARKET_OPEN" ] || [ "$IST_TIME" -gt "$MARKET_CLOSE" ]; then
    exit 0
fi

LAST_TICK=$(grep "VixManager\|survivor\|WebSocket connected" /home/ubuntu/.pm2/logs/trading-bot-out.log | tail -1 | awk '{print $1, $2}')
LAST_SEC=$(date -d "$LAST_TICK" +%s 2>/dev/null || echo 0)
NOW=$(date +%s)
DIFF=$((NOW - LAST_SEC))

if [ $DIFF -gt 180 ]; then
    echo "$(date): No ticks for ${DIFF}s — restarting" >> /home/ubuntu/ws_restart.log
    cd /home/ubuntu/trading-algo && pm2 restart all --update-env
fi
