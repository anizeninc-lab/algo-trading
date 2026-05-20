path = '/home/ubuntu/trading-algo/strategy/wave_extractor.py'
content = open(path).read()

old = "        self._unrealised_pnl = pnl\n\n    def get_config(self) -> dict:"

new = "        self._unrealised_pnl = pnl\n        self._update_pnl(self._realised_pnl, self._unrealised_pnl)\n\n    def get_config(self) -> dict:"

count = content.count(old)
print(f'Block found: {count}')
content = content.replace(old, new)
open(path, 'w').write(content)
print('Done')