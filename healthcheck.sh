#!/bin/bash
# One-shot health check for trading-bot. Run manually anytime.
cd /home/ubuntu/trading-algo || exit 1
echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="

PM2_JSON=$(pm2 jlist)
STATUS=$(echo "$PM2_JSON" | python3 -c "
import json,sys
data=json.load(sys.stdin)
for p in data:
    if p['name']=='trading-bot':
        print(p['pm2_env']['status'])
")
RESTARTS=$(echo "$PM2_JSON" | python3 -c "
import json,sys
data=json.load(sys.stdin)
for p in data:
    if p['name']=='trading-bot':
        print(p['pm2_env']['restart_time'])
")
MEM_MB=$(echo "$PM2_JSON" | python3 -c "
import json,sys
data=json.load(sys.stdin)
for p in data:
    if p['name']=='trading-bot':
        print(round(p['monit']['memory']/1024/1024,1))
")
DASH=$(curl -s -o /dev/null -w "%{http_code}" --max-time 8 http://127.0.0.1:8081/)

echo "PM2 status     : $STATUS"
echo "Total restarts  : $RESTARTS"
echo "Memory (MB)     : $MEM_MB"
echo "Dashboard HTTP  : $DASH"

if [ "$STATUS" = "online" ] && [ "$DASH" = "200" ]; then
    echo "RESULT: ✅ HEALTHY"
else
    echo "RESULT: ❌ CHECK NEEDED"
fi
