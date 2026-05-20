path = '/home/ubuntu/trading-algo/strategy/wave_extractor.py'
content = open(path).read()

old_sell = """                        self._sell_order_id = ""
                        self._buy_order_id  = ""
                        risk_manager.register_trade(self.name, "SELL")
                        asyncio.create_task(self._cool_off_and_rebracket())"""

new_sell = """                        self._sell_order_id = ""
                        self._buy_order_id  = ""
                        risk_manager.register_trade(self.name, "SELL")
                        self._update_pnl(self._realised_pnl, self._unrealised_pnl)
                        self._update_position("SHORT")
                        asyncio.create_task(self._cool_off_and_rebracket())"""

count = content.count(old_sell)
print(f'SELL block found: {count}')
content = content.replace(old_sell, new_sell)
open(path, 'w').write(content)
print('Done')