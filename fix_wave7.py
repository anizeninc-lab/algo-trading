path = '/home/ubuntu/trading-algo/strategy/wave_extractor.py'
content = open(path).read()

# Fix 1: add id to restored trade dict
old1 = """                self._open_trades_data.append({
                    "order_id":    t.get("broker_order_id", "RESTORED"),
                    "order_type":  t.get("order_type"),
                    "entry_price": t.get("entry_price"),
                    "quantity":    t.get("quantity"),
                    "symbol":      t.get("symbol"),
                })"""

new1 = """                self._open_trades_data.append({
                    "id":          t.get("trade_id", ""),
                    "order_id":    t.get("broker_order_id", "RESTORED"),
                    "order_type":  t.get("order_type"),
                    "entry_price": t.get("entry_price"),
                    "quantity":    t.get("quantity"),
                    "symbol":      t.get("symbol"),
                })"""

# Fix 2: safe access in _calculate_pnl
old2 = """                pnl_registry[trade["id"]] = round(pnl, 2)
                ltp_registry[trade["id"]] = self._current_price"""

new2 = """                tid = trade.get("id", "")
                if tid:
                    pnl_registry[tid] = round(pnl, 2)
                    ltp_registry[tid] = self._current_price"""

c1 = content.count(old1)
c2 = content.count(old2)
print(f'Fix1 found: {c1}, Fix2 found: {c2}')
content = content.replace(old1, new1).replace(old2, new2)
open(path, 'w').write(content)
print('Done')