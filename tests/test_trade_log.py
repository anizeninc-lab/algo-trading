"""Unit tests for TradeLogger — idempotency, paper_trade column, recovery."""
import sys
import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("PAPER_TRADE", "true")
os.environ.setdefault("UPSTOX_ACCESS_TOKEN", "test_token")

import pytest
from core.trade_log import TradeLogger


def make_logger(tmp_path):
    db = tmp_path / "test_trades.db"
    return TradeLogger(db_path=db)


class TestIdempotency:
    def test_duplicate_client_order_id_not_inserted(self, tmp_path):
        tl = make_logger(tmp_path)
        id1 = tl.open_trade("survivor", "upstox", "SYM", "SELL", 65, 100.0,
                             client_order_id="SURV_PE_24000_20260702")
        id2 = tl.open_trade("survivor", "upstox", "SYM", "SELL", 65, 100.0,
                             client_order_id="SURV_PE_24000_20260702")
        assert id1 == id2, "Duplicate client_order_id should return same trade id"

        with sqlite3.connect(tl.db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        assert count == 1, "Only one trade should exist in DB"

    def test_different_client_ids_create_separate_trades(self, tmp_path):
        tl = make_logger(tmp_path)
        id1 = tl.open_trade("survivor", "upstox", "SYM", "SELL", 65, 100.0,
                             client_order_id="ORDER_A")
        id2 = tl.open_trade("survivor", "upstox", "SYM", "SELL", 65, 100.0,
                             client_order_id="ORDER_B")
        assert id1 != id2
        with sqlite3.connect(tl.db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        assert count == 2


class TestPaperTradeColumn:
    def test_paper_trade_flag_stored_correctly(self, tmp_path):
        tl = make_logger(tmp_path)
        tl.open_trade("survivor", "upstox", "SYM", "SELL", 65, 100.0,
                      client_order_id="PAPER_1", paper_trade=True)
        tl.open_trade("survivor", "upstox", "SYM", "SELL", 65, 100.0,
                      client_order_id="LIVE_1", paper_trade=False)

        with sqlite3.connect(tl.db_path) as conn:
            rows = conn.execute(
                "SELECT client_order_id, paper_trade FROM trades ORDER BY entry_time"
            ).fetchall()

        assert rows[0][1] == 1, "PAPER_1 should have paper_trade=1"
        assert rows[1][1] == 0, "LIVE_1 should have paper_trade=0"

    def test_weekly_pnl_excludes_paper_trades(self, tmp_path):
        """Weekly drawdown query should ignore paper trades."""
        tl = make_logger(tmp_path)
        # Paper loss
        tid1 = tl.open_trade("survivor", "upstox", "SYM", "SELL", 65, 100.0,
                              client_order_id="P1", paper_trade=True)
        tl.close_trade(tid1, 300.0, "SL_HIT", net_pnl=-20000.0)
        # Live profit
        tid2 = tl.open_trade("survivor", "upstox", "SYM", "SELL", 65, 100.0,
                              client_order_id="L1", paper_trade=False)
        tl.close_trade(tid2, 80.0, "TP_HIT", net_pnl=1300.0)

        with sqlite3.connect(tl.db_path) as conn:
            row = conn.execute(
                "SELECT SUM(realised_pnl) FROM trades "
                "WHERE status='CLOSED' AND paper_trade=0"
            ).fetchone()
        assert row[0] == pytest.approx(1300.0), "Only live P&L should sum"


class TestPnlSummary:
    def test_today_only_scopes_correctly(self, tmp_path):
        tl = make_logger(tmp_path)
        tid = tl.open_trade("survivor", "upstox", "SYM", "SELL", 65, 100.0,
                             client_order_id="TODAY_1")
        tl.close_trade(tid, 80.0, "TP_HIT", net_pnl=1300.0)

        summary = tl.get_pnl_summary(today_only=True)
        assert summary["total_trades"] == 1
        assert summary["total_pnl"] == pytest.approx(1300.0)
        assert summary["winning"] == 1
        assert summary["losing"] == 0

    def test_empty_db_returns_zeros(self, tmp_path):
        tl = make_logger(tmp_path)
        summary = tl.get_pnl_summary(today_only=True)
        assert summary["total_trades"] == 0
        assert summary["total_pnl"] == 0.0
        assert summary["win_rate"] == 0.0
