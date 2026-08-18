with open('strategy/survivor.py', 'r', encoding='utf-8') as f:
    content = f.read()

old_block = '''                if not trade.get("_be_locked") and risk_manager.check_trade_stop_loss(
                    trade["entry"], curr_price, trade["quantity"], trade["order_type"],
                    hedge_entry_price=hedge_entry_price,
                    hedge_current_price=hedge_current_price,
                    hedge_quantity=trade.get("hedge_quantity", 0),
                ):
                    self._signal(
                        f"🛑 STOP LOSS hit | {trade['symbol']} | "
                        f"Entry: {trade['entry']:.2f} | "
                        f"Current: {curr_price:.2f} | P&L: ₹{curr_pnl:.0f}"
                    )
                    await self._close_trade(trade, "SL_HIT", curr_price)
                    continue  # trade closed, move to next

                # ── Profit target ─────────────────────────────────────────
                _fixed_tp = 800.0 if "BANKNIFTY" in self.cfg.instrument_name.upper() else 0.0
                if risk_manager.check_trailing_profit(
                    trade["entry"], curr_price, trade["order_type"], trade["quantity"],
                    fixed_target=_fixed_tp, trade_id=trade.get("id", ""),
                    hedge_entry_price=hedge_entry_price,
                    hedge_current_price=hedge_current_price,
                    hedge_quantity=trade.get("hedge_quantity", 0),
                    hedge_entry_cost=trade.get("hedge_entry_cost", 0.0),
                ):
                    self._signal(
                        f"✅ PROFIT TARGET hit | {trade['symbol']} | "
                        f"Entry: {trade['entry']:.2f} | "
                        f"Current: {curr_price:.2f} | P&L: ₹{curr_pnl:.0f}"
                    )
                    await self._close_trade(trade, "TP_HIT", curr_price)'''

new_block = '''                # ── Profit target (checked first so an armed trailing floor
                # can never be undercut by the stop-loss check below) ──────
                _fixed_tp = 800.0 if "BANKNIFTY" in self.cfg.instrument_name.upper() else 0.0
                if risk_manager.check_trailing_profit(
                    trade["entry"], curr_price, trade["order_type"], trade["quantity"],
                    fixed_target=_fixed_tp, trade_id=trade.get("id", ""),
                    hedge_entry_price=hedge_entry_price,
                    hedge_current_price=hedge_current_price,
                    hedge_quantity=trade.get("hedge_quantity", 0),
                    hedge_entry_cost=trade.get("hedge_entry_cost", 0.0),
                ):
                    self._signal(
                        f"✅ PROFIT TARGET hit | {trade['symbol']} | "
                        f"Entry: {trade['entry']:.2f} | "
                        f"Current: {curr_price:.2f} | P&L: ₹{curr_pnl:.0f}"
                    )
                    await self._close_trade(trade, "TP_HIT", curr_price)
                    continue  # trade closed, move to next

                elif not trade.get("_be_locked") and risk_manager.check_trade_stop_loss(
                    trade["entry"], curr_price, trade["quantity"], trade["order_type"],
                    hedge_entry_price=hedge_entry_price,
                    hedge_current_price=hedge_current_price,
                    hedge_quantity=trade.get("hedge_quantity", 0),
                ):
                    self._signal(
                        f"🛑 STOP LOSS hit | {trade['symbol']} | "
                        f"Entry: {trade['entry']:.2f} | "
                        f"Current: {curr_price:.2f} | P&L: ₹{curr_pnl:.0f}"
                    )
                    await self._close_trade(trade, "SL_HIT", curr_price)
                    continue  # trade closed, move to next'''

count = content.count(old_block)
print(f"Found {count} occurrence(s) of the old block.")

if count == 1:
    content = content.replace(old_block, new_block)
    with open('strategy/survivor.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print("SUCCESS: File updated.")
elif count == 0:
    print("NOT FOUND: The text didn't match exactly. No changes made. This is safe -- nothing was touched.")
else:
    print(f"WARNING: Found {count} matches, expected exactly 1. No changes made for safety.")
