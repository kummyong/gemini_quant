# test_multi_broker.py
"""Tests for the multi‑broker architecture.
Ensures that:
- BrokerFactory returns proper adapters with a valid `broker_id`.
- StrategyEngine.fetch_current_holdings aggregates holdings from all brokers.
- Sell signal generation includes the correct `broker_id`.
"""

import os
import tempfile
import unittest
from pathlib import Path

# Add stock_trader and project root to sys.path for imports
import sys
project_root = Path(__file__).resolve().parents[2]
stock_trader_dir = Path(__file__).resolve().parents[1]
sys.path.append(str(project_root))
sys.path.append(str(stock_trader_dir))

from unittest.mock import patch, MagicMock
from broker_factory import BrokerFactory
from db_repository import DbRepository
from strategy_engine import StrategyEngine

class MultiBrokerTests(unittest.TestCase):
    def setUp(self):
        # Create a temporary SQLite DB
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = os.path.join(self.temp_dir.name, "test.db")
        self.repo = DbRepository(db_path)
        # Insert a mock holding for Korea Investment broker
        self.repo.update_portfolio_holding(
            stk_cd="005930",
            stk_nm="삼성전자",
            rmnd_qty=5,
            pur_pric=500000,
            cur_prc=550000,
            prft_rt=10.0,
            max_profit_rate=12.0,
            broker_id="KOREA_INVEST",
        )
        # Insert a mock holding for NH Investment broker
        self.repo.update_portfolio_holding(
            stk_cd="000660",
            stk_nm="SK하이닉스",
            rmnd_qty=3,
            pur_pric=300000,
            cur_prc=320000,
            prft_rt=6.67,
            max_profit_rate=8.0,
            broker_id="NH_INVEST",
        )
        self.engine = StrategyEngine(db_repository=self.repo, ipc_publisher=None)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_broker_factory_instances(self):
        kiwoom = BrokerFactory.get_broker("KIWOOM")
        korea = BrokerFactory.get_broker("KOREA_INVEST")
        nh = BrokerFactory.get_broker("NH_INVEST")
        self.assertTrue(hasattr(kiwoom, "get_account_summary"))
        self.assertTrue(hasattr(korea, "get_account_summary"))
        self.assertTrue(hasattr(nh, "get_account_summary"))

    @patch("broker_factory.BrokerFactory.get_broker")
    def test_fetch_current_holdings(self, mock_get_broker):
        # Make the brokers raise Exception on get_account_summary to force DB fallback
        mock_broker = MagicMock()
        mock_broker.get_account_summary.side_effect = Exception("API offline")
        mock_get_broker.return_value = mock_broker

        holdings = self.engine.fetch_current_holdings()
        # Expect three brokers: KIWOOM (empty), KOREA_INVEST and NH_INVEST (from DB)
        broker_ids = {h["broker_id"] for h in holdings}
        self.assertIn("KOREA_INVEST", broker_ids)
        self.assertIn("NH_INVEST", broker_ids)
        # Verify quantity matches DB entries
        korea_hold = next(h for h in holdings if h["broker_id"] == "KOREA_INVEST")
        self.assertEqual(korea_hold["quantity"], 5)
        nh_hold = next(h for h in holdings if h["broker_id"] == "NH_INVEST")
        self.assertEqual(nh_hold["quantity"], 3)

    def test_generate_management_signals_includes_broker_id(self):
        # Create a holding that triggers a hard stop sell
        holdings = [
            {
                "broker_id": "KOREA_INVEST",
                "ticker": "005930",
                "name": "삼성전자",
                "profit_rate": -10.0,
                "quantity": 5,
                "purchase_price": 500000,
                "current_price": 450000,
                "max_profit_rate": -10.0,
            }
        ]
        # Force hard stop threshold low to trigger
        self.engine.HARD_STOP_LOSS = -5.0
        signals = self.engine.generate_management_signals(holdings, top_tickers=[])
        self.assertTrue(any(s["broker_id"] == "KOREA_INVEST" for s in signals))
        self.assertTrue(all(s["action"] == "SELL" for s in signals))

if __name__ == "__main__":
    unittest.main()
