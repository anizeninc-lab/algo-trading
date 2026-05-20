path = '/home/ubuntu/trading-algo/strategy/wave_extractor.py'
content = open(path).read()

old = """        self._sync_task = asyncio.create_task(self._position_sync_loop())

        self._signal(
            f"Started | Symbol: {self.cfg.option_symbol} | "
            f"Price: {self._current_price:.2f}"
        )"""

new = """        self._sync_task = asyncio.create_task(self._position_sync_loop())

        # Reload open trades from DB on startup (handles restarts)
        open_trades = trade_logger.get_trades(strategy=self.name, status="OPEN")
        for t in open_trades:
            if t.get("symbol") == self.cfg.option_symbol:
                self._open_trades_data.append({
                    "order_id":    t.get("broker_order_id", "RESTORED"),
                    "order_type":  t.get("order_type"),
                    "entry_price": t.get("entry_price"),
                    "quantity":    t.get("quantity"),
                    "symbol":      t.get("symbol"),
                })
                self._net_position += 1 if t.get("order_type") == "BUY" else -1
        if self._open_trades_data:
            self._signal(f"Restored {len(self._open_trades_data)} open trade(s) from DB")

        self._signal(
            f"Started | Symbol: {self.cfg.option_symbol} | "
            f"Price: {self._current_price:.2f}"
        )"""

count = content.count(old)
print(f'Block found: {count}')
content = content.replace(old, new)
open(path, 'w').write(content)
print('Done')