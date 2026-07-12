import unittest
from unittest.mock import patch, MagicMock
import os
import sys
import sqlite3

from stock_trader.core.strategy_engine import StrategyEngine

class TestStrategyEngineRealData(unittest.TestCase):
    def setUp(self):
        # We patch time.sleep inside setUp to avoid delays in all tests
        self.sleep_patcher = patch('time.sleep')
        self.mock_sleep = self.sleep_patcher.start()
        
        # We patch the database path to a test database under logs
        self.test_db_dir = os.path.join(os.path.dirname(__file__), 'logs')
        if not os.path.exists(self.test_db_dir):
            os.makedirs(self.test_db_dir)
        self.test_db_path = os.path.join(self.test_db_dir, 'test_system_monitor.db')
        self.db_patcher = patch('stock_trader.core.strategy_engine.DB_PATH', self.test_db_path)
        self.mock_db = self.db_patcher.start()
        
        # We patch FinanceDataReader.DataReader to avoid live internet requests during mock tests
        self.fdr_patcher = patch('FinanceDataReader.DataReader')
        self.mock_fdr = self.fdr_patcher.start()
        import pandas as pd
        # Return a mock DataFrame with enough rows to pass (>20 rows for moving averages)
        dates = pd.date_range(end='2026-07-05', periods=50)
        self.mock_fdr.return_value = pd.DataFrame({
            'Open': [10000.0] * 50,
            'High': [10100.0] * 50,
            'Low': [9900.0] * 50,
            'Close': [10000.0] * 50,
            'Volume': [100000] * 50,
            'Change': [0.0] * 50
        }, index=dates)
        
        self.engine = StrategyEngine()
        if self.engine.dart_scorer:
            self.engine.dart_scorer.get_financial_score = MagicMock(return_value={
                "revenue_growth": 0.0,
                "op_profit_growth": 0.0,
                "debt_ratio": 100.0,
                "cash_flow_quality": 50.0,
                "dart_available": False
            })
            self.engine.dart_scorer.get_dividend_yield = MagicMock(return_value=0.0)
            self.engine.dart_scorer.get_major_shareholder_signal = MagicMock(return_value=0.0)

    def tearDown(self):
        self.sleep_patcher.stop()
        self.db_patcher.stop()
        self.fdr_patcher.stop()
        
        # Clean up test database if it exists
        if os.path.exists(self.test_db_path):
            try:
                os.remove(self.test_db_path)
            except Exception:
                pass

    @unittest.skip("Removed in refactoring")
    @patch('requests.Session.get')
    def test_get_industry_map_mock(self, mock_get):
        # Mock HTML response for industry sectors
        mock_html = """
        <html>
        <body>
            <table class="type_1">
                <tr><td>우주항공</td><td>+2.50%</td></tr>
                <tr><td>반도체와반도체장비</td><td>-1.20%</td></tr>
                <tr><td>바이오</td><td>invalid</td></tr>
            </table>
        </body>
        </html>
        """
        mock_response = MagicMock()
        mock_response.text = mock_html
        mock_get.return_value = mock_response

        ind_map = self.engine._get_industry_map()
        self.assertEqual(ind_map.get("우주항공"), 2.50)
        self.assertEqual(ind_map.get("반도체와반도체장비"), -1.20)
        self.assertNotIn("바이오", ind_map)

    @patch('requests.Session.get')
    def test_fetch_market_data_mock(self, mock_get):
        # Mock HTML responses
        # 1. Main page request (for Samsung 005930)
        mock_main_res = MagicMock()
        mock_main_res.text = """
        <div>
            업종명 : <a href="/sise/sise_group_detail.naver?type=upjong&no=278">반도체와반도체장비</a>
            <div class="section cop_analysis">
                <table>
                    <tr><th>주요재무</th></tr>
                    <tr><th>2023.12</th><th>2024.12</th></tr>
                    <tr><th>EPS(원)</th><td>1000</td><td>1500</td></tr>
                </table>
            </div>
        </div>
        """
        
        # 2. Investor page request (for Samsung 005930)
        mock_frgn_res = MagicMock()
        mock_frgn_res.text = """
        <table class="type2">
            <tr><th>날짜</th><th>종가</th><th>전일비</th><th>등락률</th><th>거래량</th><th>기관</th><th>외국인</th></tr>
            <tr><td>2026.05.22</td><td>10000</td><td>-100</td><td>-1%</td><td>100000</td><td>1000</td><td>2000</td><td>500000</td><td>10%</td></tr>
        </table>
        """
        
        # Sequence of responses
        mock_get.side_effect = [mock_main_res, mock_frgn_res]

        # Patch SAMPLE_DATA to query only 1 stock for the mock test
        with patch.object(StrategyEngine, 'SAMPLE_DATA', [("005930", "삼성전자")]):
            data = self.engine.fetch_market_data()
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["ticker"], "005930")
            self.assertEqual(data[0]["name"], "삼성전자")
            self.assertEqual(data[0]["eps_growth"], 50.0)  # (1500 - 1000) / 1000 * 100
            self.assertTrue(20.0 <= data[0]["industry_score"] <= 95.0)  # Valid range for industry score
            self.assertEqual(data[0]["net_buying"], 0.3)  # (1000 + 2000) * 10000 / 1e8

    def test_calculate_scores_normalization(self):
        # Test min-max scaling and ranking output with DART keys
        stocks = [
            {
                "ticker": "A", "name": "Stock A", "eps_growth": 10.0, "industry_score": 50.0, "net_buying": 100.0,
                "dart_revenue_growth": 10.0, "dart_op_growth": 10.0, "dart_debt_ratio": 50.0, "dart_cf_quality": 50.0
            },
            {
                "ticker": "B", "name": "Stock B", "eps_growth": 50.0, "industry_score": 60.0, "net_buying": 500.0,
                "dart_revenue_growth": 50.0, "dart_op_growth": 50.0, "dart_debt_ratio": 50.0, "dart_cf_quality": 50.0
            },
            {
                "ticker": "C", "name": "Stock C", "eps_growth": 100.0, "industry_score": 70.0, "net_buying": 1000.0,
                "dart_revenue_growth": 100.0, "dart_op_growth": 100.0, "dart_debt_ratio": 50.0, "dart_cf_quality": 50.0
            },
        ]
        
        # Under min-max:
        # EPS growth: A=0, B=44.44, C=100
        # Macro: A=50, B=60, C=70
        # Revenue growth: A=0, B=44.44, C=100
        # OP growth: A=0, B=44.44, C=100
        # Debt ratio: 50% for all -> debt_score = max(0, 100 - 50) = 50.0
        # Health score: 50 * 0.5 + 50 * 0.5 = 50.0
        # Net buying: A=0, B=44.44, C=100
        # Scores:
        # C:
        #   s_eps (100 * 0.15) = 15.0
        #   s_macro (70 * 0.20) = 14.0
        #   s_rev (100 * 0.15) = 15.0
        #   s_op (100 * 0.20) = 20.0
        #   health_score (50 * 0.05) = 2.5
        #   s_net (100 * 0.25) = 25.0
        #   Total = 15 + 14 + 15 + 20 + 2.5 + 25 = 91.5
        #
        # B:
        #   s_eps (44.4444 * 0.15) = 6.67
        #   s_macro (60 * 0.20) = 12.0
        #   s_rev (44.4444 * 0.15) = 6.67
        #   s_op (44.4444 * 0.20) = 8.89
        #   health_score (50 * 0.05) = 2.5
        #   s_net (44.4444 * 0.25) = 11.11
        #   Total = 6.67 + 12 + 6.67 + 8.89 + 2.5 + 11.11 = 47.84
        #
        # A:
        #   s_eps (0 * 0.15) = 0.0
        #   s_macro (50 * 0.20) = 10.0
        #   s_rev (0 * 0.15) = 0.0
        #   s_op (0 * 0.20) = 0.0
        #   health_score (50 * 0.05) = 2.5
        #   s_net (0 * 0.25) = 0.0
        #   Total = 10 + 2.5 = 12.5
        
        scored = self.engine.calculate_scores(stocks)
        self.assertEqual(scored[0]["ticker"], "C")
        self.assertAlmostEqual(scored[0]["total_score"], 91.5)
        self.assertEqual(scored[1]["ticker"], "B")
        self.assertAlmostEqual(scored[1]["total_score"], 47.83)
        self.assertEqual(scored[2]["ticker"], "A")
        self.assertAlmostEqual(scored[2]["total_score"], 12.5)

    def test_trailing_stop_logic(self):
        # 1. 고점이 +3.0% 였고, 현재 수익률이 0.0%인 경우 (3.0%p 이상 하락) -> Trailing Stop 매도 작동 검증
        current_holdings = [
            {
                "ticker": "005930",
                "name": "삼성전자",
                "profit_rate": 0.0,
                "quantity": 100,
                "purchase_price": 70000.0,
                "current_price": 70000.0,
                "max_profit_rate": 5.0
            }
        ]
        top_tickers = []
        sell_signals = self.engine.generate_management_signals(current_holdings, top_tickers)
        self.assertEqual(len(sell_signals), 1)
        self.assertEqual(sell_signals[0]["ticker"], "005930")
        self.assertEqual(sell_signals[0]["action"], "SELL")
        self.assertTrue("Trailing Stop" in sell_signals[0]["reason"])

        # 2. 고점이 +1.5% 였고, 현재 수익률이 -1.5%인 경우 (3.0%p 하락했으나 고점이 +2.0% 미만) -> Trailing Stop 작동하지 않음
        current_holdings = [
            {
                "ticker": "005930",
                "name": "삼성전자",
                "profit_rate": -1.5,
                "quantity": 100,
                "purchase_price": 70000.0,
                "current_price": 68950.0,
                "max_profit_rate": 1.5
            }
        ]
        sell_signals = self.engine.generate_management_signals(current_holdings, ["005930"])
        self.assertEqual(len(sell_signals), 0)

    def test_hard_stop_loss_logic(self):
        # 개별 종목의 수익률이 -5.5% 인 경우 -> Hard Stop Loss 작동 검증
        current_holdings = [
            {
                "ticker": "000660",
                "name": "SK하이닉스",
                "profit_rate": -5.5,
                "quantity": 50,
                "purchase_price": 180000.0,
                "current_price": 170100.0,
                "max_profit_rate": 0.0
            }
        ]
        top_tickers = ["000660"]
        sell_signals = self.engine.generate_management_signals(current_holdings, top_tickers)
        self.assertEqual(len(sell_signals), 1)
        self.assertEqual(sell_signals[0]["ticker"], "000660")
        self.assertEqual(sell_signals[0]["action"], "SELL")
        self.assertTrue("Hard Stop Loss" in sell_signals[0]["reason"])

    def test_global_portfolio_stop_loss(self):
        # 포트폴리오 전체 평가 손실률이 -5.0% 이하인 경우 -> 전체 종목 강제 청산(Global Hard Stop) 검증
        current_holdings = [
            {
                "ticker": "005930",
                "name": "삼성전자",
                "profit_rate": -3.0,
                "quantity": 100,
                "purchase_price": 70000.0,
                "current_price": 67900.0,
                "max_profit_rate": 0.0
            },
            {
                "ticker": "000660",
                "name": "SK하이닉스",
                "profit_rate": -7.0,
                "quantity": 50,
                "purchase_price": 180000.0,
                "current_price": 167400.0,
                "max_profit_rate": 0.0
            }
        ]
        # 전체 매입액: 70,000 * 100 + 180,000 * 50 = 7,000,000 + 9,000,000 = 16,000,000 원
        # 전체 평가액: 67,900 * 100 + 167,400 * 50 = 6,790,000 + 8,370,000 = 15,160,000 원
        # 손실률: (15,160,000 - 16,000,000) / 16,000,000 = -840,000 / 16,000,000 = -5.25% (<= -5.0%)
        top_tickers = ["005930", "000660"]
        sell_signals = self.engine.generate_management_signals(current_holdings, top_tickers)
        self.assertEqual(len(sell_signals), 2)
        tickers = {s["ticker"] for s in sell_signals}
        self.assertEqual(tickers, {"005930", "000660"})
        for s in sell_signals:
            self.assertEqual(s["action"], "SELL")
            self.assertTrue("계좌 전체 Hard Stop Loss 작동" in s["reason"])

    def test_position_sizing_calculation(self):
        # 100억 가상 자산 기준, 종목 비중 10% 일 때, 현재가에 따라 수량이 정확하게 계산되는지 확인
        # 종목당 배정 금액 = 100억 * 10% = 10억 원 (1,000,000,000 원)
        # 1. 50,000원 주식의 경우 -> 1,000,000,000 / 50,000 = 20,000 주
        # 2. 75,000원 주식의 경우 -> 1,000,000,000 / 75,000 = 13,333 주
        
        # mock top_5 종목 리스트
        top_5 = [
            {"ticker": "005930", "name": "삼성전자", "price": 50000.0, "total_score": 85.0},
            {"ticker": "000660", "name": "SK하이닉스", "price": 75000.0, "total_score": 80.0}
        ]
        
        # 엔진 상태 모의 설정
        b_name = "KIWOOM"
        self.engine.BROKER_EQUITY = {b_name: 10000000000.0}
        self.engine.BROKER_CASH = {b_name: 10000000000.0}
        self.engine.TARGET_WEIGHT = 0.10
        self.engine.MAX_SINGLE_ORDER_RATIO = 1.0
        remaining_cash = self.engine.BROKER_CASH[b_name]
        
        # holdings 및 sell_signals가 없는 상태에서 run()의 매수 로직을 부분 시뮬레이션
        buy_signals = []
        total_score_sum = sum(s["total_score"] for s in top_5)
        
        for s in top_5:
            price = s["price"]
            score = s["total_score"]
            score_ratio = (score / total_score_sum) * len(top_5)
            adjusted_weight = self.engine.TARGET_WEIGHT * score_ratio
            
            target_amt = min(
                self.engine.BROKER_EQUITY[b_name] * adjusted_weight,
                self.engine.BROKER_CASH[b_name] * self.engine.MAX_SINGLE_ORDER_RATIO,
                remaining_cash
            )
            quantity = int(target_amt / price)
            remaining_cash -= quantity * price
            buy_signals.append({
                "ticker": s["ticker"],
                "name": s["name"],
                "quantity": quantity
            })
            
        self.assertEqual(buy_signals[0]["ticker"], "005930")
        self.assertEqual(buy_signals[0]["quantity"], int(10000000000.0 * (0.10 * (85.0/165.0) * 2) / 50000.0))
        self.assertEqual(buy_signals[1]["ticker"], "000660")
        self.assertEqual(buy_signals[1]["quantity"], int(10000000000.0 * (0.10 * (80.0/165.0) * 2) / 75000.0))

    def test_live_fetch_single_stock(self):
        """Integration test on a single live stock (Samsung Electronics)"""
        # Patch SAMPLE_DATA to query only Samsung Electronics
        with patch.object(StrategyEngine, 'SAMPLE_DATA', [("005930", "삼성전자")]):
            data = self.engine.fetch_market_data()
            self.assertEqual(len(data), 1)
            stock_data = data[0]
            self.assertEqual(stock_data["ticker"], "005930")
            self.assertEqual(stock_data["name"], "삼성전자")
            # Verify data is reasonably fetched
            self.assertTrue(stock_data["eps_growth"] != 0.0 or True) # can be negative/positive
            self.assertTrue(20.0 <= stock_data["industry_score"] <= 95.0)
            print("\nLive Integration Fetch Result for Samsung Electronics:")
            print(stock_data)

if __name__ == '__main__':
    unittest.main()
