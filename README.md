cat > ~/trading-algo/README.md << 'README'
# 🤖 Algo Trading Bot — Operations Guide
> Give this file to Claude at the start of every new chat for full context.
> Last updated: 2026-05-13

## 🖥️ Server Details
- Server IP: 92.4.90.188
- Dashboard URL: http://92.4.90.188:8081
- SSH Command: ssh -i ~/.ssh/oci_trading ubuntu@92.4.90.188
- Project Folder: /home/ubuntu/trading-algo
- GitHub Repo: https://github.com/anizeninc-lab/algo-trading.git

## 🌅 Every Morning
1. cd "C:/Users/Prince/Desktop/trading-algo-backup-20260504"
2. python get_token.py
3. scp -i ~/.ssh/oci_trading ".env" ubuntu@92.4.90.188:/home/ubuntu/trading-algo/token_update.env
4. SSH in and run token update script
5. pm2 restart all
6. Check dashboard: http://92.4.90.188:8081

## ⚙️ Risk Settings
- Per trade stop loss: -1500
- Daily loss limit: -5000
- Trailing profit: 25%
- Max trades/day: 3
- Max capital: 150000
- Qty hardcap: 65 (1 lot)
- Auto stop: 3:10 PM IST
