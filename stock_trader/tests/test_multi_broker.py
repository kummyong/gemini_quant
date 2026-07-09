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
from stock_trader.broker.broker_factory import BrokerFactory
from stock_trader.data.db_repository import DbRepository
from stock_trader.core.strategy_engine import StrategyEngine

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

    @patch("stock_trader.broker.broker_factory.BrokerFactory.get_active_brokers")
    @patch("stock_trader.broker.broker_factory.BrokerFactory.get_broker")
    def test_fetch_current_holdings(self, mock_get_broker, mock_get_active_brokers):
        mock_get_active_brokers.return_value = ["KIWOOM", "KOREA_INVEST", "NH_INVEST"]
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

    @patch("stock_trader.broker.broker_factory.BrokerFactory.get_active_brokers")
    @patch("stock_trader.broker.broker_factory.BrokerFactory.get_broker")
    def test_fetch_current_holdings_merges_max_profit_on_api_success(self, mock_get_broker, mock_get_active_brokers):
        """API 조회 성공 경로에서도 DB의 고점 수익률(max_profit_rate)이 병합되어야 한다.
        (회귀 테스트: 과거에는 API 성공 시 max_profit_rate가 누락되어
        트레일링 스탑/ETF 샹들리에 스탑이 매입가 기준으로 퇴화했음)"""
        mock_get_active_brokers.return_value = ["KOREA_INVEST"]
        mock_broker = MagicMock()
        # 현재 수익률 8.0% < DB 저장 고점 12.0% -> 고점 유지되어야 함
        mock_broker.get_account_summary.return_value = {
            "acnt_evlt_remn_indv_tot": [{
                "stk_cd": "A005930",
                "stk_nm": "삼성전자",
                "prft_rt": "8.0",
                "rmnd_qty": "5",
                "pchs_amt": "2500000",
                "evlt_amt": "2700000"
            }]
        }
        mock_get_broker.return_value = mock_broker

        holdings = self.engine.fetch_current_holdings()
        self.assertEqual(len(holdings), 1)
        self.assertEqual(holdings[0]["max_profit_rate"], 12.0)

        # 현재 수익률 15.0% > 고점 12.0% -> 고점 갱신 및 DB 반영되어야 함
        mock_broker.get_account_summary.return_value = {
            "acnt_evlt_remn_indv_tot": [{
                "stk_cd": "A005930",
                "stk_nm": "삼성전자",
                "prft_rt": "15.0",
                "rmnd_qty": "5",
                "pchs_amt": "2500000",
                "evlt_amt": "2875000"
            }]
        }
        holdings = self.engine.fetch_current_holdings()
        self.assertEqual(holdings[0]["max_profit_rate"], 15.0)
        self.assertEqual(self.repo.get_max_profit_rates("KOREA_INVEST").get("005930"), 15.0)

    def _lockout_test_top5(self):
        return [{
            "ticker": "005930", "name": "삼성전자", "price": 50000.0,
            "total_score": 90.0, "rsi": 20.0, "lower_band": 0.0, "atr_pct": 3.0
        }]

    def test_hard_stop_lockout_blocks_stock_buys(self):
        """글로벌 하드스탑 락아웃이 활성이면 주식 프로파일 신규 매수가 차단되어야 한다."""
        import datetime
        from stock_trader.data.db_repository import HARD_STOP_LOCKOUT_PREFIX
        today = datetime.date.today().strftime("%Y-%m-%d")
        self.repo.update_market_lockout(
            active=True, since=today,
            reason=f"{HARD_STOP_LOCKOUT_PREFIX} 글로벌 Hard Stop 작동 (테스트)"
        )
        self.engine.current_regime = "BULL"
        self.engine.BROKER_EQUITY = {"KIWOOM": 10_000_000.0}
        self.engine.BROKER_CASH = {"KIWOOM": 10_000_000.0}

        buys = self.engine._generate_stock_buy_signals(["KIWOOM"], [], [], self._lockout_test_top5())
        self.assertEqual(buys, [])
        # 쿨다운 미경과이므로 락아웃은 해제되지 않아야 함 (BULL 국면이어도)
        self.assertTrue(self.repo.get_market_lockout().get("active"))

    def test_hard_stop_lockout_releases_after_cooldown_in_bull(self):
        """쿨다운 경과 + BULL 국면이면 하드스탑 락아웃이 해제되고 매수가 재개되어야 한다."""
        import datetime
        from stock_trader.data.db_repository import HARD_STOP_LOCKOUT_PREFIX
        old_date = (datetime.date.today() - datetime.timedelta(days=10)).strftime("%Y-%m-%d")
        self.repo.update_market_lockout(
            active=True, since=old_date,
            reason=f"{HARD_STOP_LOCKOUT_PREFIX} 글로벌 Hard Stop 작동 (테스트)"
        )
        self.engine.current_regime = "BULL"
        self.engine.BROKER_EQUITY = {"KIWOOM": 10_000_000.0}
        self.engine.BROKER_CASH = {"KIWOOM": 10_000_000.0}

        buys = self.engine._generate_stock_buy_signals(["KIWOOM"], [], [], self._lockout_test_top5())
        self.assertFalse(self.repo.get_market_lockout().get("active"))
        self.assertEqual(len(buys), 1)
        self.assertEqual(buys[0]["ticker"], "005930")

    def test_hard_stop_lockout_not_released_in_bear(self):
        """쿨다운이 지나도 BEAR 국면이면 하드스탑 락아웃이 유지되어야 한다."""
        import datetime
        from stock_trader.data.db_repository import HARD_STOP_LOCKOUT_PREFIX
        old_date = (datetime.date.today() - datetime.timedelta(days=10)).strftime("%Y-%m-%d")
        self.repo.update_market_lockout(
            active=True, since=old_date,
            reason=f"{HARD_STOP_LOCKOUT_PREFIX} 글로벌 Hard Stop 작동 (테스트)"
        )
        self.engine.current_regime = "BEAR"
        self.engine.BROKER_EQUITY = {"KIWOOM": 10_000_000.0}
        self.engine.BROKER_CASH = {"KIWOOM": 10_000_000.0}

        buys = self.engine._generate_stock_buy_signals(["KIWOOM"], [], [], self._lockout_test_top5())
        self.assertEqual(buys, [])
        self.assertTrue(self.repo.get_market_lockout().get("active"))

    def test_global_hard_stop_uses_account_equity_basis(self):
        """글로벌 하드스탑은 계좌 전체(현금 포함) 대비 미실현 손실률로 판정해야 한다.
        현금 비중이 높으면 보유분이 -6%여도 계좌 대비 -0.6%이므로 전량 청산이 발동하면 안 된다.
        (개별 종목 하드스탑은 별개로 동작 가능)"""
        holdings = [{
            "broker_id": "KIWOOM",
            "ticker": "005930",
            "name": "삼성전자",
            "profit_rate": -6.0,
            "quantity": 10,
            "purchase_price": 100000.0,
            "current_price": 94000.0,
            "max_profit_rate": 0.0,
        }]
        self.engine.HARD_STOP_LOSS = -5.0

        # 1) 계좌 총자산 정보가 있고 현금 비중이 높은 경우 -> 글로벌 스탑 미발동
        self.engine.BROKER_EQUITY = {"KIWOOM": 10_000_000.0}
        signals = self.engine.generate_management_signals(holdings, top_tickers=["005930"])
        self.assertFalse(
            any("계좌 전체 Hard Stop Loss" in s["reason"] for s in signals),
            f"현금 90% 상태에서 글로벌 스탑이 발동하면 안 됨: {signals}"
        )

        # 2) 계좌 총자산 정보가 없는 경우 -> 기존(보유분 매입금) 기준으로 보수적 폴백 -> 발동
        self.engine.BROKER_EQUITY = {}
        signals = self.engine.generate_management_signals(holdings, top_tickers=["005930"])
        self.assertTrue(
            any("계좌 전체 Hard Stop Loss" in s["reason"] for s in signals),
            "총자산 정보 부재 시 보유분 기준 폴백으로 발동해야 함"
        )

    def test_replacement_respects_min_holding_days(self):
        """교체 매도는 매수 후 MIN_HOLDING_DAYS 미경과 시 유보되어야 한다.
        (리스크 청산이 아닌 replacement 분기에만 적용)"""
        holdings = [{
            "broker_id": "KIWOOM",
            "ticker": "005930",
            "name": "삼성전자",
            "profit_rate": 1.0,   # 하드스탑/트레일링/오버슈팅 모두 미해당, 교체 조건(<2.0)만 해당
            "quantity": 10,
            "purchase_price": 100000.0,
            "current_price": 101000.0,
            "max_profit_rate": 1.0,
        }]
        # 오늘 매수 이력 기록 -> 보유 0일차 -> 교체 유보
        self.repo.save_trade_history("005930", "삼성전자", "BUY", 10, 100000, 1000000, "테스트 매수")
        signals = self.engine.generate_management_signals(holdings, top_tickers=[])
        self.assertEqual(signals, [], f"최소 보유일 미경과 시 교체 매도가 유보되어야 함: {signals}")

        # 매수 이력을 10일 전으로 소급 -> 교체 매도 발동
        with self.repo.get_connection() as conn:
            conn.execute("UPDATE trade_history SET timestamp = datetime('now', 'localtime', '-10 day') WHERE ticker = '005930'")
        signals = self.engine.generate_management_signals(holdings, top_tickers=[])
        self.assertEqual(len(signals), 1)
        self.assertIn("교체", signals[0]["reason"])

    def test_overshoot_partial_exit_marks_runner(self):
        """오버슈팅 시 OVERSHOOT_EXIT_FRACTION 비율만 익절하고 잔여 물량을 러너로 마킹해야 하며,
        러너에는 오버슈팅이 재발동하지 않아야 한다."""
        self.repo.update_portfolio_holding(
            stk_cd="005930", stk_nm="삼성전자", rmnd_qty=10,
            pur_pric=100000, cur_prc=105000, prft_rt=5.0, max_profit_rate=5.0,
            broker_id="KIWOOM")
        holding = {
            "broker_id": "KIWOOM",
            "ticker": "005930",
            "name": "삼성전자",
            "profit_rate": 5.0,   # 트레일링(고점-현재=0)/하드스탑 미해당
            "quantity": 10,
            "purchase_price": 100000.0,
            "current_price": 105000.0,
            "max_profit_rate": 5.0,
        }
        market_data = [{"ticker": "005930", "rsi": 75.0, "upper_band": 999_999_999.0, "atr_pct": 3.0}]

        # 1) 오버슈팅(RSI 75) -> 절반(5주) 부분 익절 + 러너 마킹
        signals = self.engine.generate_management_signals([dict(holding)], top_tickers=["005930"], market_data=market_data)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["quantity"], 5)
        self.assertIn("부분 익절", signals[0]["reason"])
        db_rows = self.repo.get_portfolio_holdings()
        runner_row = next(r for r in db_rows if r["broker_id"] == "KIWOOM" and r["stk_cd"] == "005930")
        self.assertEqual(runner_row["is_runner"], 1)

        # 2) 러너 상태의 보유분에는 오버슈팅이 재발동하지 않음
        runner_holding = dict(holding, quantity=5, is_runner=True)
        signals = self.engine.generate_management_signals([runner_holding], top_tickers=["005930"], market_data=market_data)
        self.assertEqual(signals, [], f"러너에 오버슈팅이 재발동하면 안 됨: {signals}")

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
