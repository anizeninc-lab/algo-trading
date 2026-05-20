path = '/home/ubuntu/trading-algo/strategy/wave_extractor.py'
content = open(path).read()

old = "        open_trades = trade_logger.get_trades(strategy=self.name, status=\"OPEN\")"
new = "        today = __import__('datetime').date.today().isoformat()\n        open_trades = [t for t in trade_logger.get_trades(strategy=self.name, status=\"OPEN\") if t.get('entry_time', '')[:10] == today]"

count = content.count(old)
print(f'Block found: {count}')
content = content.replace(old, new)
open(path, 'w').write(content)
print('Done')
