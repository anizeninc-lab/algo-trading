path = '/home/ubuntu/trading-algo/brokers/upstox.py'
content = open(path).read()

old = """                    orders.append(
                        Order(
                            symbol=getattr(o, "instrument_token", ""),
                            exchange=getattr(o, "exchange", ""),
                            order_type=getattr(o, "transaction_type", ""),
                            quantity=getattr(o, "quantity", 0),
                            product=getattr(o, "product", ""),
                            price=getattr(o, "price", 0.0),
                            order_id=getattr(o, "order_id", ""),
                        )
                    )"""

new = """                    ord_obj = Order(
                            symbol=getattr(o, "instrument_token", ""),
                            exchange=getattr(o, "exchange", ""),
                            order_type=getattr(o, "transaction_type", ""),
                            quantity=getattr(o, "quantity", 0),
                            product=getattr(o, "product", ""),
                            price=getattr(o, "average_price", 0.0) or getattr(o, "price", 0.0),
                            order_id=getattr(o, "order_id", ""),
                        )
                    ord_obj.status = getattr(o, "status", "")
                    ord_obj.average_price = getattr(o, "average_price", 0.0)
                    orders.append(ord_obj)"""

count = content.count(old)
print(f'Block found: {count}')
content = content.replace(old, new)
open(path, 'w').write(content)
print('Done')
