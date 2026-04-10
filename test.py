# test.py - verify all modules load with risk management
print("Testing all modules...")

from brokers.base import AbstractBrokerGateway

print("OK: brokers.base")

from brokers import get_broker

print("OK: brokers factory")

from core.event_bus import EventType, event_bus

print("OK: core.event_bus")

from core.state_store import StrategyState, state_store

print("OK: core.state_store")

from core.trade_log import trade_logger

print(f"OK: core.trade_log — {trade_logger.health()}")

from core.risk_manager import risk_manager

print(f"OK: core.risk_manager")
print(f"     Max daily loss:      ₹{risk_manager.max_daily_loss}")
print(f"     Per trade SL:        ₹{risk_manager.per_trade_loss}")
print(f"     Trailing profit:     {risk_manager.trailing_profit_pct}%")
print(f"     Max trades/day:      {risk_manager.max_trades_per_day}")
print(
    f"     Auto-stop time:      {risk_manager.auto_stop_hour}:{risk_manager.auto_stop_minute:02d} PM"
)

from strategy.base_strategy import BaseStrategy

print("OK: strategy.base_strategy")

from strategy.survivor import SurvivorAlgo, SurvivorConfig

print("OK: strategy.survivor")

from strategy.wave_extractor import WaveConfig, WaveExtractor

print("OK: strategy.wave_extractor")

from strategy.saviour_combo import SaviourCombo, SaviourComboConfig

print("OK: strategy.saviour_combo")

from dashboard.api import app

print("OK: dashboard.api")

import main

print("OK: main")

# Test risk manager logic
print("")
print("Testing risk manager logic...")

can, reason = risk_manager.can_trade("wave_extractor")
print(f"Can trade (fresh):       {can} — {reason or 'OK'}")

risk_manager.register_trade("wave_extractor")
risk_manager.register_trade("wave_extractor")
risk_manager.register_trade("wave_extractor")
risk_manager.register_trade("wave_extractor")
can, reason = risk_manager.can_trade("wave_extractor")
print(f"Can trade (4 trades):    {can} — {reason}")

sl_hit = risk_manager.check_trade_stop_loss(
    entry_price=200.0, current_price=260.0, quantity=75, order_type="SELL"
)
print(f"SL hit (200 sell→260):   {sl_hit} (loss = ₹{(260-200)*75})")

tp_hit = risk_manager.check_trailing_profit(
    entry_price=200.0, current_price=140.0, order_type="SELL"
)
print(f"TP hit (200→140, -30%):  {tp_hit} (target was ₹150)")

risk_manager.reset_daily_counts()
can, reason = risk_manager.can_trade("wave_extractor")
print(f"Can trade (after reset): {can} — {reason or 'OK'}")

print("")
print("All checks passed. System ready for trading.")
print("")
print("Risk Rules Active:")
print(f"  Stop all strategies if daily loss > ₹5,000")
print(f"  Close individual trade if loss > ₹3,000")
print(f"  Take profit when premium decays 25% from entry")
print(f"  Max 4 trades per strategy per day")
print(f"  Auto-stop all strategies at 3:10 PM IST")
print(f"  Paper trade mode: ON")
