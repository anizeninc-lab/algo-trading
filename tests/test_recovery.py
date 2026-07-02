"""Integration tests for crash recovery."""
import sys
import os
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("PAPER_TRADE", "true")
os.environ.setdefault("UPSTOX_ACCESS_TOKEN", "test_token")

import pytest
from core.trade_log import TradeLogger


def make_mock_broker():
    broker = MagicMock()
    broker.get_positions     = AsyncMock(return_value=[])
    broker.place_order       = AsyncMock()
    broker.cancel_order      = AsyncMock(return_value=True)
    broker.get_ltp           = AsyncMock(return_value=100.0)
    broker.get_orders        = AsyncMock(return_value=[])
    broker.subscribe_ticks   = MagicMock()
    broker.unsubscribe_ticks = MagicMock()
    return broker


def make_mock_rm():
    rm = MagicMock()
    rm.is_trading_blocked.return_value  = (False, "")
    rm.can_trade.return_value           = (True, "")
    rm.check_auto_stop.return_value     = False
    rm._deployed_capital                = {}
    rm.check_capital_limit.return_value = (True, "")
    return rm


class TestCrashRecovery:
    @pytest.mark.asyncio
    async def test_open_trades_reloaded_from_db(self, tmp_path):
        """After restart, _open_trades_data should be repopulated from DB."""
        db = tmp_path / "trades.db"
        tl = TradeLogger(db_path=db)
        trade_id = tl.open_trade(
            "survivor", "upstox", "NSE_FO|44639", "SELL", 65, 120.0,
            client_order_id="SURV_PE_24000_20260702"
        )

        import strategy.survivor as surv_mod
        import strategy.base_strategy as base_mod
        import core.trade_log as tl_mod

        orig_db = tl_mod.trade_logger.db_path
        tl_mod.trade_logger.db_path = db

        try:
            with patch.object(surv_mod, "risk_manager", make_mock_rm()), \
                 patch.object(surv_mod, "strategy_filter", MagicMock()), \
                 patch.object(surv_mod, "state_store", MagicMock()), \
                 patch.object(surv_mod, "vix_manager", MagicMock()), \
                 patch("core.alerting.send_telegram", MagicMock()):

                from strategy.survivor import SurvivorAlgo, SurvivorConfig
                cfg  = SurvivorConfig(instrument_name="NIFTY")
                algo = SurvivorAlgo(make_mock_broker(), cfg)
                algo._is_paper = True
                await algo._reload_open_trades()

                assert len(algo._open_trades_data) == 1, \
                    "One open trade should be recovered from DB"
                assert algo._open_trades_data[0]["id"] == trade_id
        finally:
            tl_mod.trade_logger.db_path = orig_db

    @pytest.mark.asyncio
    async def test_no_duplicate_recovery_on_double_reload(self, tmp_path):
        """Calling _reload_open_trades twice should not duplicate trades."""
        db = tmp_path / "trades.db"
        tl = TradeLogger(db_path=db)
        tl.open_trade(
            "survivor", "upstox", "NSE_FO|44639", "SELL", 65, 120.0,
            client_order_id="SURV_PE_24000_20260702"
        )

        import strategy.survivor as surv_mod
        import core.trade_log as tl_mod

        orig_db = tl_mod.trade_logger.db_path
        tl_mod.trade_logger.db_path = db

        try:
            with patch.object(surv_mod, "risk_manager", make_mock_rm()), \
                 patch.object(surv_mod, "strategy_filter", MagicMock()), \
                 patch.object(surv_mod, "state_store", MagicMock()), \
                 patch.object(surv_mod, "vix_manager", MagicMock()), \
                 patch("core.alerting.send_telegram", MagicMock()):

                from strategy.survivor import SurvivorAlgo, SurvivorConfig
                cfg  = SurvivorConfig(instrument_name="NIFTY")
                algo = SurvivorAlgo(make_mock_broker(), cfg)
                await algo._reload_open_trades()
                await algo._reload_open_trades()

                assert len(algo._open_trades_data) == 1, \
                    "Double reload should not duplicate open trades"
        finally:
            tl_mod.trade_logger.db_path = orig_db
