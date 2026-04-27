from core.trade_log import trade_logger

trades = trade_logger.get_trades(limit=10)
print(f"Total trades found: {len(trades)}")
for t in trades:
    print(t)
