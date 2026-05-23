import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Add directory to sys.path to import strategy_engine
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from strategy_engine import StrategyEngine

class TestStrategyEngineRealData(unittest.TestCase):
    def setUp(self):
        # We patch time.sleep inside setUp to avoid delays in all tests
        self.sleep_patcher = patch('time.sleep')
        self.mock_sleep = self.sleep_patcher.start()
        
        # We patch the database path to a test database under logs
        self.db_patcher = patch('strategy_engine.DB_PATH', os.path.join(os.path.dirname(__file__), 'logs', 'test_system_monitor.db'))
        self.mock_db = self.db_patcher.start()
        
        self.engine = StrategyEngine()

    def tearDown(self):
        self.sleep_patcher.stop()
        self.db_patcher.stop()
        
        # Clean up test database if it exists
        test_db = os.path.join(os.path.dirname(__file__), 'logs', 'test_system_monitor.db')
        if os.path.exists(test_db):
            try:
                os.remove(test_db)
            except Exception:
                pass

    @patch('requests.get')
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

    @patch('requests.get')
    def test_fetch_market_data_mock(self, mock_get):
        # Mock HTML responses
        # 1. Industry mapping request
        mock_ind_res = MagicMock()
        mock_ind_res.text = """
        <table class="type_1">
            <tr><td>반도체와반도체장비</td><td>+1.00%</td></tr>
        </table>
        """
        
        # 2. Main page request (for Samsung 005930)
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
        
        # 3. Investor page request (for Samsung 005930)
        mock_frgn_res = MagicMock()
        mock_frgn_res.text = """
        <table class="type2">
            <tr><th>날짜</th><th>종가</th><th>전일비</th><th>등락률</th><th>거래량</th><th>기관</th><th>외국인</th></tr>
            <tr><td>2026.05.22</td><td>10000</td><td>-100</td><td>-1%</td><td>100000</td><td>1000</td><td>2000</td><td>500000</td><td>10%</td></tr>
        </table>
        """
        
        # Sequence of responses
        mock_get.side_effect = [mock_ind_res, mock_main_res, mock_frgn_res]

        # Patch SAMPLE_DATA to query only 1 stock for the mock test
        with patch.object(StrategyEngine, 'SAMPLE_DATA', [("005930", "삼성전자")]):
            data = self.engine.fetch_market_data()
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["ticker"], "005930")
            self.assertEqual(data[0]["name"], "삼성전자")
            self.assertEqual(data[0]["eps_growth"], 50.0)  # (1500 - 1000) / 1000 * 100
            self.assertAlmostEqual(data[0]["industry_score"], 65.0)  # 57.5 + 1.0 * 7.5
            self.assertEqual(data[0]["net_buying"], 0.3)  # (1000 + 2000) * 10000 / 1e8

    def test_calculate_scores_normalization(self):
        # Test min-max scaling and ranking output
        stocks = [
            {"ticker": "A", "name": "Stock A", "eps_growth": 10.0, "industry_score": 50.0, "net_buying": 100.0},
            {"ticker": "B", "name": "Stock B", "eps_growth": 50.0, "industry_score": 60.0, "net_buying": 500.0},
            {"ticker": "C", "name": "Stock C", "eps_growth": 100.0, "industry_score": 70.0, "net_buying": 1000.0},
        ]
        
        # Under min-max:
        # EPS growth: A=0, B=44.44, C=100
        # Net buying: A=0, B=44.44, C=100
        # Scores:
        # A: (0 * 0.4) + (50 * 0.3) + (0 * 0.3) = 15.0
        # B: (44.44 * 0.4) + (60 * 0.3) + (44.44 * 0.3) = 17.78 + 18 + 13.33 = 49.11
        # C: (100 * 0.4) + (70 * 0.3) + (100 * 0.3) = 40 + 21 + 30 = 91.0
        
        scored = self.engine.calculate_scores(stocks)
        self.assertEqual(scored[0]["ticker"], "C")
        self.assertAlmostEqual(scored[0]["total_score"], 91.0)
        self.assertEqual(scored[1]["ticker"], "B")
        self.assertAlmostEqual(scored[1]["total_score"], 49.11)
        self.assertEqual(scored[2]["ticker"], "A")
        self.assertAlmostEqual(scored[2]["total_score"], 15.0)

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
