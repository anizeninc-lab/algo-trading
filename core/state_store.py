# core/state_store.py
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class StrategyState:
    IDLE     = "IDLE"
    RUNNING  = "RUNNING"
    STOPPED  = "STOPPED"
    ERROR    = "ERROR"


class Direction:
    LONG  = "LONG"
    SHORT = "SHORT"
    FLAT  = "FLAT"


@dataclass
class StrategyStatus:
    name:             str
    state:            str   = StrategyState.IDLE
    position:         str   = Direction.FLAT
    realised_pnl:     float = 0.0
    unrealised_pnl:   float = 0.0
    open_orders:      int   = 0
    total_trades:     int   = 0
    open_trades:      int   = 0
    closed_trades:    int   = 0
    last_signal:      str   = ""
    last_updated:     str   = field(default_factory=lambda: datetime.now().isoformat())
    error_message:    str   = ""
    broker:           str   = ""
    pnl_history:      list  = field(default_factory=list)


class StateStore:
    def __init__(self):
        self._states: dict         = {}
        self._broker_status: dict  = {}
        self._system_start: str    = datetime.now().isoformat()
        self._nifty_price: float   = 0.0
        self._nifty_updated: str   = ""
        self._option_price: float  = 0.0
        self._option_symbol: str   = ""

    # --- NIFTY Price ----------------------------------------------------------

    def update_nifty_price(self, price: float) -> None:
        self._nifty_price   = price
        self._nifty_updated = datetime.now().strftime("%H:%M:%S")

    def update_option_price(self, symbol: str, price: float) -> None:
        self._option_symbol = symbol
        self._option_price  = price

    def get_market_data(self) -> dict:
        return {
            "nifty_price":   self._nifty_price,
            "nifty_updated": self._nifty_updated,
            "option_symbol": self._option_symbol,
            "option_price":  self._option_price,
        }

    # --- Strategy State -------------------------------------------------------

    def register_strategy(self, name: str, broker: str = "") -> None:
        # Seed today's realised P&L from DB so daily loss limit survives restarts
        seeded_pnl = 0.0
        try:
            import sqlite3 as _sq, pytz as _pytz
            from datetime import datetime as _dt
            from core.trade_log import trade_logger as _tl
            _today = _dt.now(_pytz.timezone("Asia/Kolkata")).strftime("%Y-%m-%d")
            with _sq.connect(_tl.db_path) as _conn:
                _row = _conn.execute(
                    "SELECT SUM(realised_pnl) FROM trades "
                    "WHERE strategy=? AND status='CLOSED' AND DATE(exit_time)=?",  # ORPHANED% no longer excluded -- real losses must count toward halt (see enhancement #2)
                    (name, _today)
                ).fetchone()
            seeded_pnl = float(_row[0]) if _row and _row[0] is not None else 0.0
            if seeded_pnl != 0.0:
                logger.info(f"StateStore: seeded today P&L for '{name}': ₹{seeded_pnl:.2f}")
        except Exception as _e:
            logger.warning(f"StateStore: could not seed P&L for '{name}': {_e}")
        self._states[name] = StrategyStatus(name=name, broker=broker, realised_pnl=seeded_pnl)
        logger.info(f"StateStore: registered strategy '{name}'")

    def get_strategy(self, name: str) -> Optional[StrategyStatus]:
        return self._states.get(name)

    def get_all_strategies(self) -> dict:
        return dict(self._states)

    def update_state(self, name: str, state: str, error_message: str = "") -> None:
        if name not in self._states:
            logger.warning(f"StateStore: unknown strategy '{name}'")
            return
        self._states[name].state         = state
        self._states[name].error_message = error_message
        self._states[name].last_updated  = datetime.now().isoformat()
        logger.info(f"StateStore: {name} -> {state}")

    def update_position(self, name: str, direction: str) -> None:
        if name in self._states:
            self._states[name].position     = direction
            self._states[name].last_updated = datetime.now().isoformat()

    def update_pnl(self, name: str, realised: float, unrealised: float) -> None:
        if name not in self._states:
            return
        self._states[name].realised_pnl   = realised
        self._states[name].unrealised_pnl = unrealised
        self._states[name].last_updated   = datetime.now().isoformat()
        history = self._states[name].pnl_history
        history.append({
            "time": datetime.now().strftime("%H:%M"),
            "pnl":  round(realised + unrealised, 2)
        })
        if len(history) > 60:
            history.pop(0)

    def update_orders(self, name: str, open_orders: int) -> None:
        if name in self._states:
            self._states[name].open_orders  = open_orders
            self._states[name].last_updated = datetime.now().isoformat()

    def update_trades(self, name: str, total: int, open_count: int, closed: int) -> None:
        if name in self._states:
            self._states[name].total_trades  = total
            self._states[name].open_trades   = open_count
            self._states[name].closed_trades = closed
            self._states[name].last_updated  = datetime.now().isoformat()

    def update_last_signal(self, name: str, signal: str) -> None:
        if name in self._states:
            self._states[name].last_signal  = signal
            self._states[name].last_updated = datetime.now().isoformat()

    # --- Broker Status --------------------------------------------------------

    def set_broker_status(self, broker: str, status: str) -> None:
        self._broker_status[broker] = status
        logger.info(f"StateStore: broker '{broker}' -> {status}")

    def get_broker_status(self) -> dict:
        return dict(self._broker_status)

    # --- Global Summary -------------------------------------------------------

    def get_global_summary(self) -> dict:
        total_pnl    = 0.0
        active_count = 0
        health       = "OK"
        for status in self._states.values():
            # saviour_combo is a derived rollup of wave_extractor/survivor/bn_survivor,
            # not independent capital — exclude it from the sum to avoid double-counting.
            if status.name != "saviour_combo":
                total_pnl += status.realised_pnl
            if status.state == StrategyState.RUNNING:
                active_count += 1
            if status.state == StrategyState.ERROR:
                health = "CRITICAL"
        return {
            "total_pnl":         round(total_pnl, 2),
            "active_strategies": active_count,
            "total_strategies":  len(self._states),
            "system_health":     health,
            "broker_status":     self.get_broker_status(),
            "system_start":      self._system_start,
            "last_updated":      datetime.now().isoformat(),
        }


state_store = StateStore()
