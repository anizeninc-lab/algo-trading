path = '/home/ubuntu/trading-algo/strategy/wave_extractor.py'
content = open(path).read()

old = '                        trade_logger.open_trade(trade)'
new = """                        trade_logger.open_trade(
                            strategy=self.name,
                            broker=type(self.broker).__name__,
                            symbol=trade['symbol'],
                            order_type=trade['order_type'],
                            quantity=trade['quantity'],
                            entry_price=trade['entry_price'],
                            broker_order_id=trade['order_id'],
                        )"""

count = content.count(old)
print(f'Found {count} occurrences to replace')
content = content.replace(old, new)
open(path, 'w').write(content)
print('Done')