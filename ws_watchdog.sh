#!/bin/bash
# Check if Nifty ticks are flowing — restart only if silent for 3+ minutes
LAST_TICK=$(grep "VixManager\|survivor\|WebSocket connected" /home/ubuntu/.pm2/logs/trading-bot-out.log | tail -1 | awk '{print $1, $2}')
LAST_SEC=$(date -d "$LAST_TICK" +%s 2>/dev/null || echo 0)
NOW=$(date +%s)
DIFF=$((NOW - LAST_SEC))
if [ $DIFF -gt 180 ]; then
    echo "$(date): No ticks for ${DIFF}s — restarting" >> /home/ubuntu/ws_restart.log
    cd /home/ubuntu/trading-algo && pm2 restart all --update-env
fi
