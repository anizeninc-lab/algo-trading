path = '/home/ubuntu/trading-algo/strategy/wave_extractor.py'
content = open(path).read()

old_sell = """                        risk_manager.register_trade(self.name, "SELL")
                        asyncio.create_task(self._cool_off_and_rebracket())
                        return
                    # Simulate BUY fill when price drops to buy level"""

new_sell = """                        risk_manager.register_trade(self.name, "SELL")
                        self._update_pnl(self._realised_pnl, self._unrealised_pnl)
                        self._update_position("SHORT")
                        asyncio.create_task(self._cool_off_and_rebracket())
                        return
                    # Simulate BUY fill when price drops to buy level"""

old_buy = """                        risk_manager.register_trade(self.name, "BUY")
                        asyncio.create_task(self._cool_off_and_rebracket())
                        return
            # ── End Paper Trade Fill Simulator"""

new_buy = """                        risk_manager.register_trade(self.name, "BUY")
                        self._update_pnl(self._realised_pnl, self._unrealised_pnl)
                        self._update_position("LONG")
                        asyncio.create_task(self._cool_off_and_rebracket())
                        return
            # ── End Paper Trade Fill Simulator"""

count1 = content.count(old_sell)
count2 = content.count(old_buy)
print(f'SELL block found: {count1}, BUY block found: {count2}')
content = content.replace(old_sell, new_sell).replace(old_buy, new_buy)
open(path, 'w').write(content)
print('Done')