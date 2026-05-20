path = '/home/ubuntu/trading-algo/strategy/wave_extractor.py'
content = open(path).read()

old = """        if self._open_trades_data:
            self._signal(f"Restored {len(self._open_trades_data)} open trade(s) from DB")"""

new = """        if self._open_trades_data:
            self._signal(f"Restored {len(self._open_trades_data)} open trade(s) from DB")
            pos = "LONG" if self._net_position > 0 else "SHORT" if self._net_position < 0 else "FLAT"
            self._update_position(pos)
            self._update_pnl(self._realised_pnl, self._unrealised_pnl)"""

count = content.count(old)
print(f'Block found: {count}')
content = content.replace(old, new)
open(path, 'w').write(content)
print('Done')