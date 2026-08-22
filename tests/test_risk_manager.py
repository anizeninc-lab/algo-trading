"""Unit tests for RiskManager — weekly drawdown, circuit breaker, capital gate."""
import sys
import os
import time
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("PAPER_TRADE", "true")
os.environ.setdefault("UPSTOX_ACCESS_TOKEN", "test_token")

import pytest


def make_risk_manager(**kwargs):
    from core.risk_manager import RiskManager
    with patch("core.risk_manager.RISK_STATE_FILE", Path(tempfile.mktemp(suffix=".json"))):
        rm = RiskManager.__new__(RiskManager)
        rm.max_daily_loss      = kwargs.get("max_daily_loss", -3000.0)
        rm.per_trade_loss      = kwargs.get("per_trade_loss", -800.0)
        rm.trailing_profit_pct = kwargs.get("trailing_profit_pct", 25.0)
        rm.trailing_activation_pct = kwargs.get("trailing_activation_pct", 8.0)
        rm.max_trades_per_day  = kwargs.get("max_trades_per_day", 3)
        rm.auto_stop_hour      = kwargs.get("auto_stop_hour", 15)
        rm.auto_stop_minute    = kwargs.get("auto_stop_minute", 10)
        rm.max_weekly_loss     = kwargs.get("max_weekly_loss", -10000.0)
        rm._trade_counts       = {}
        rm._daily_pnl          = {}
        rm._system_halted      = False
        rm._halt_reason        = ""
        rm._last_reset_day     = -1
        rm._pnl_watermarks     = {}
        rm._mfe_watermarks     = {}
        rm._mae_watermarks     = {}
        rm._deployed_capital   = {}
        rm._last_blocked       = {}
        rm._api_fail_times     = []
        rm._api_cb_tripped     = False
        rm._api_cb_tripped_at  = 0.0
        rm._CB_WINDOW          = 60
        rm._CB_THRESHOLD       = 5
        rm._CB_RESET           = 300
        rm._save_state         = lambda: None
        return rm


class TestWeeklyDrawdown:
    def test_paper_trades_excluded_from_weekly_pnl(self, tmp_path):
        db = tmp_path / "trades.db"
        conn = sqlite3.connect(db)
        conn.execute("""CREATE TABLE trades (
            id TEXT, strategy TEXT, broker TEXT, symbol TEXT, order_type TEXT,
            quantity INTEGER, entry_price REAL, exit_price REAL,
            entry_time TEXT, exit_time TEXT, realised_pnl REAL,
            status TEXT, broker_order_id TEXT, notes TEXT,
            client_order_id TEXT, gross_pnl REAL, total_costs REAL,
            parent_trade_id TEXT, paper_trade INTEGER DEFAULT 0
        )""")
        conn.execute("""INSERT INTO trades VALUES (
            '1','survivor','upstox','SYM','SELL',65,100,200,
            '2026-07-01','2026-07-01',-20000,'CLOSED','','','cid1',
            -20000,0,'',1
        )""")
        conn.execute("""INSERT INTO trades VALUES (
            '2','survivor','upstox','SYM','SELL',65,200,100,
            '2026-07-01','2026-07-01',500,'CLOSED','','','cid2',
            500,0,'',0
        )""")
        conn.commit()
        conn.close()

        rm = make_risk_manager(max_weekly_loss=-10000.0)
        import core.trade_log as _tl_mod
        orig_db = _tl_mod.trade_logger.db_path
        _tl_mod.trade_logger.db_path = db
        try:
            result = rm.check_weekly_drawdown()
        finally:
            _tl_mod.trade_logger.db_path = orig_db
        assert result is False, "Weekly breaker should NOT trip on paper-only losses"

    def test_live_losses_trigger_weekly_breaker(self, tmp_path):
        db = tmp_path / "trades.db"
        conn = sqlite3.connect(db)
        conn.execute("""CREATE TABLE trades (
            id TEXT, strategy TEXT, broker TEXT, symbol TEXT, order_type TEXT,
            quantity INTEGER, entry_price REAL, exit_price REAL,
            entry_time TEXT, exit_time TEXT, realised_pnl REAL,
            status TEXT, broker_order_id TEXT, notes TEXT,
            client_order_id TEXT, gross_pnl REAL, total_costs REAL,
            parent_trade_id TEXT, paper_trade INTEGER DEFAULT 0
        )""")
        conn.execute("""INSERT INTO trades VALUES (
            '1','survivor','upstox','SYM','SELL',65,100,200,
            '2026-07-01','2026-07-01',-15000,'CLOSED','','','cid1',
            -15000,0,'',0
        )""")
        conn.commit()
        conn.close()

        rm = make_risk_manager(max_weekly_loss=-10000.0)
        import core.trade_log as _tl_mod
        orig_db = _tl_mod.trade_logger.db_path
        _tl_mod.trade_logger.db_path = db
        try:
            result = rm.check_weekly_drawdown()
        finally:
            _tl_mod.trade_logger.db_path = orig_db
        assert result is True, "Weekly breaker SHOULD trip on live losses"

    def test_weekly_override_env_skips_check(self):
        rm = make_risk_manager()
        with patch.dict(os.environ, {"WEEKLY_LOSS_OVERRIDE": "1"}):
            assert rm.check_weekly_drawdown() is False


class TestCircuitBreaker:
    def test_trips_after_threshold_failures(self):
        rm = make_risk_manager()
        rm._CB_THRESHOLD = 3
        for _ in range(3):
            rm.record_api_failure()
        assert rm._api_cb_tripped is True
        assert rm._system_halted is True

    def test_does_not_trip_below_threshold(self):
        rm = make_risk_manager()
        rm._CB_THRESHOLD = 5
        for _ in range(4):
            rm.record_api_failure()
        assert rm._api_cb_tripped is False

    def test_success_clears_failures(self):
        rm = make_risk_manager()
        rm._CB_THRESHOLD = 5
        for _ in range(3):
            rm.record_api_failure()
        rm.record_api_success()
        assert rm._api_fail_times == []
        assert rm._api_cb_tripped is False

    def test_auto_resets_after_cooldown(self):
        rm = make_risk_manager()
        rm._CB_THRESHOLD   = 2
        rm._CB_RESET       = 1
        for _ in range(2):
            rm.record_api_failure()
        assert rm._api_cb_tripped is True
        rm._api_cb_tripped_at = time.time() - 2
        tripped, _ = rm.check_api_circuit_breaker()
        assert tripped is False

    def test_is_trading_blocked_includes_circuit_breaker(self):
        rm = make_risk_manager()
        rm._CB_THRESHOLD = 2
        with patch("core.risk_manager.trade_logger"), \
             patch("core.vix_manager.vix_manager") as mock_vm:
            mock_vm.get_params.return_value = {"halt_trading": False}
            for _ in range(2):
                rm.record_api_failure()
            blocked, reason = rm.is_trading_blocked()
        assert blocked is True
        assert "circuit breaker" in reason.lower()


class TestCapitalGate:
    def test_blocks_when_capital_exceeded(self):
        from core.risk_manager import MAX_CAPITAL_DEPLOYED
        rm = make_risk_manager()
        rm._deployed_capital = {"survivor": MAX_CAPITAL_DEPLOYED}
        with patch("core.risk_manager.state_store"):
            ok, reason = rm.check_capital_limit("SELL")
        assert ok is False

    def test_allows_when_capital_available(self):
        rm = make_risk_manager()
        rm._deployed_capital = {}
        with patch("core.risk_manager.state_store"):
            ok, _ = rm.check_capital_limit("SELL")
        assert ok is True

        
class TestTrailingProfitFastReversal:
    """Regression tests for the Aug 2026 bug: once trailing is armed, the
    giveback-floor check must run on every subsequent tick, independent of
    whether that tick's net_pnl still clears the (lower) activation
    threshold. Previously the floor check was nested inside the activation
    check, so a single fast adverse tick that jumped straight past
    activation -- skipping the floor entirely -- silently returned False
    and handed the trade to the plain stop-loss with no memory it had ever
    been trailing. Cost ~Rs 11.5K across 7 trades in the first two weeks
    of live trading before this fix.
    """

    def test_arms_on_favorable_move_past_activation(self):
        rm = make_risk_manager(trailing_activation_pct=8.0, trailing_profit_pct=50.0)
        entry, qty, trade_id = 126.0, 65, "test-arm"
        result = rm.check_trailing_profit(entry, 145.0, "BUY", qty, trade_id=trade_id)
        assert result is False, "Should not exit while still climbing"
        assert rm.get_watermark(trade_id) > 0, "Trailing should be armed after clearing activation"

    def test_floor_check_survives_a_tick_that_jumps_past_activation(self):
        """The core regression: after arming on a favorable tick, a single
        large adverse tick drops net_pnl from above-activation to
        below-activation in one step (no intermediate tick in between).
        The floor check must still fire using the already-recorded peak."""
        rm = make_risk_manager(trailing_activation_pct=8.0, trailing_profit_pct=50.0)
        entry, qty, trade_id = 126.0, 65, "test-fast-reversal"

        assert rm.check_trailing_profit(entry, 170.0, "BUY", qty, trade_id=trade_id) is False
        peak_after_arm = rm.get_watermark(trade_id)
        assert peak_after_arm > 0

        result = rm.check_trailing_profit(entry, 132.0, "BUY", qty, trade_id=trade_id)
        assert result is True, (
            "Trailing stop failed to fire on a fast reversal that jumped "
            "straight past the activation threshold -- this is the exact "
            "bug that cost ~Rs 11.5K in Aug 2026"
        )
        assert trade_id not in rm._pnl_watermarks, "Watermark should be cleared on exit"

    def test_no_false_trigger_while_still_above_floor(self):
        """Sanity check: a minor pullback that stays above the giveback
        floor should NOT trigger an exit."""
        rm = make_risk_manager(trailing_activation_pct=8.0, trailing_profit_pct=50.0)
        entry, qty, trade_id = 126.0, 65, "test-minor-pullback"

        assert rm.check_trailing_profit(entry, 170.0, "BUY", qty, trade_id=trade_id) is False
        peak = rm.get_watermark(trade_id)
        assert peak > 0

        result = rm.check_trailing_profit(entry, 155.0, "BUY", qty, trade_id=trade_id)
        assert result is False, "Should not exit on a minor pullback still above the floor"
        assert rm.get_watermark(trade_id) == peak, "Peak should be unchanged, not reset"
