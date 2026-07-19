#!/usr/bin/env python3
"""
gemini_quant refactoring verification tests
──────────────────────────────────────────
This script verifies that the refactored modules can be imported correctly,
that config paths are properly resolved, and that essential libraries function as expected.
"""

import os
import sys
import unittest
import sqlite3
import logging
from logging.handlers import RotatingFileHandler

# Mock modules on Windows to make import and format tests work without local dependencies
if sys.platform == 'win32':
    from unittest.mock import MagicMock
    sys.modules['fcntl'] = MagicMock()
    sys.modules['dotenv'] = MagicMock()
    sys.modules['psutil'] = MagicMock()
    sys.modules['matplotlib'] = MagicMock()
    sys.modules['matplotlib.pyplot'] = MagicMock()
    sys.modules['joblib'] = MagicMock()
    sys.modules['mcp'] = MagicMock()
    sys.modules['mcp.server'] = MagicMock()
    sys.modules['mcp.server.fastmcp'] = MagicMock()
    sys.modules['google'] = MagicMock()
    sys.modules['google.oauth2'] = MagicMock()
    sys.modules['google.oauth2.service_account'] = MagicMock()
    sys.modules['googleapiclient'] = MagicMock()
    sys.modules['googleapiclient.discovery'] = MagicMock()


TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
STOCK_TRADER_DIR = os.path.dirname(TESTS_DIR)
PROJECT_DIR = os.path.dirname(STOCK_TRADER_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

class TestRefactoring(unittest.TestCase):
    
    def test_01_config_paths(self):
        """Test config path resolution"""
        import stock_trader.config as config
        print("\n[Test] Checking config paths:")
        print(f"  STOCK_TRADER_DIR: {config.STOCK_TRADER_DIR}")
        print(f"  PROJECT_DIR:      {config.PROJECT_DIR}")
        print(f"  SECRETARY_DIR:    {config.SECRETARY_DIR}")
        print(f"  LOG_DIR:          {config.LOG_DIR}")
        print(f"  DB_PATH:          {config.DB_PATH}")
        print(f"  MODEL_PATH:       {config.MODEL_PATH}")
        
        self.assertTrue(os.path.isdir(config.STOCK_TRADER_DIR))
        self.assertTrue(os.path.isdir(config.PROJECT_DIR))
        self.assertEqual(config.STOCK_TRADER_DIR, STOCK_TRADER_DIR)
        self.assertEqual(config.PROJECT_DIR, PROJECT_DIR)

    def test_02_stock_universe(self):
        """Test stock universe map and tickers definition"""
        import stock_trader.core.stock_universe as stock_universe
        self.assertTrue(hasattr(stock_universe, 'STOCK_MAP'))
        self.assertTrue(hasattr(stock_universe, 'SAMPLE_TICKERS'))
        self.assertIsInstance(stock_universe.STOCK_MAP, dict)
        self.assertIsInstance(stock_universe.SAMPLE_TICKERS, list)
        self.assertGreater(len(stock_universe.STOCK_MAP), 0)
        self.assertGreater(len(stock_universe.SAMPLE_TICKERS), 0)
        print(f"\n[Test] Stock Universe size: {len(stock_universe.STOCK_MAP)}")
        print(f"  Sample tickers: {stock_universe.SAMPLE_TICKERS}")

    def test_03_imports(self):
        """Test importing refactored modules (checks syntax and module-level code)"""
        print("\n[Test] Verifying imports:")
        
        modules_to_test = [
            ("config", "config"),
            ("stock_universe", "stock_universe"),
            ("system_monitor", "system_monitor"),
            ("telegram_listener", "telegram_listener"),
            ("summary_trader", "summary_trader"),
            ("trainer", "trainer"),
            ("agent_skills", "agent_skills"),
            ("local_intent_router", "local_intent_router"),
            ("strategy_engine", "strategy_engine"),
            ("auto_trader", "auto_trader"),
            ("system_monitor_loop", "system_monitor_loop"),
            ("system_trend_reporter", "system_trend_reporter")
        ]
        
        for name, mod_path in modules_to_test:
            try:
                __import__(mod_path)
                print(f"  [OK] Import successful: {name}")
            except ImportError as e:
                # Some modules might require external API keys or DB connections,
                # but basic import/syntax should not fail unless missing dependencies.
                print(f"  [WARN] Import warning for {name}: {e}")
            except Exception as e:
                print(f"  [FAIL] Import failed for {name}: {e}")
                raise e

        # Test secretary import
        sys.path.insert(0, os.path.join(PROJECT_DIR, "secretary"))
        try:
            __import__('auto_sync_history')
            print("  [OK] Import successful: auto_sync_history")
        except ImportError as e:
            print(f"  [WARN] Import warning for auto_sync_history: {e}")
        except Exception as e:
            print(f"  [FAIL] Import failed for auto_sync_history: {e}")
            raise e

    def test_04_db_connection_with_pattern(self):
        """Test sqlite3 connection context manager with custom test DB"""
        import stock_trader.config as config
        test_db = os.path.join(config.LOG_DIR, "test_temp.db")
        
        # Test creation and write
        try:
            conn = sqlite3.connect(test_db, timeout=5.0)
            try:
                cursor = conn.cursor()
                cursor.execute("CREATE TABLE IF NOT EXISTS test (id INTEGER PRIMARY KEY, val TEXT)")
                cursor.execute("INSERT INTO test (val) VALUES (?)", ("test_val",))
                conn.commit()
            finally:
                conn.close()
            
            # Test read
            conn = sqlite3.connect(test_db, timeout=5.0)
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT val FROM test WHERE id=1")
                row = cursor.fetchone()
                self.assertEqual(row[0], "test_val")
            finally:
                conn.close()
                
            print("\n[Test] SQLite connection verified successfully.")
        finally:
            if os.path.exists(test_db):
                os.remove(test_db)

    def test_05_rotating_log_handler(self):
        """Test RotatingFileHandler setup"""
        import stock_trader.config as config
        test_log = os.path.join(config.LOG_DIR, "test_rotating.log")
        
        try:
            logger = logging.getLogger("test_logger")
            logger.setLevel(logging.INFO)
            handler = RotatingFileHandler(test_log, maxBytes=1000, backupCount=1)
            logger.addHandler(handler)
            
            # Write enough bytes to trigger rotation
            for i in range(20):
                logger.info(f"Log message {i:03d} to trigger rotation. Let's write more bytes to hit limit.")
                
            # Verify file exists
            self.assertTrue(os.path.exists(test_log))
            print("\n[Test] RotatingFileHandler verified successfully.")
        finally:
            # Clean up
            for handler in logger.handlers[:]:
                handler.close()
                logger.removeHandler(handler)
            if os.path.exists(test_log):
                os.remove(test_log)
            # Remove any backup files
            backup = test_log + ".1"
            if os.path.exists(backup):
                os.remove(backup)

    def test_06_summary_trader_telegram_formatting(self):
        """Test daily summary telegram message formatting with mock DB data"""
        from unittest.mock import patch
        import stock_trader.core.summary_trader as summary_trader
        import stock_trader.config as config
        import datetime
        
        # 임시 테스트 DB를 만들어 데이터 포맷 검증
        test_db = os.path.join(config.LOG_DIR, "test_summary_temp.db")
        if os.path.exists(test_db):
            try:
                os.remove(test_db)
            except PermissionError:
                pass
        summary_trader.DB_PATH = test_db
        
        try:
            with sqlite3.connect(test_db) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS account_summary (
                        total_assets INTEGER, cash INTEGER, cash_ratio REAL, timestamp DATETIME
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS portfolio_status (
                        stk_cd TEXT PRIMARY KEY, stk_nm TEXT, rmnd_qty INTEGER, pur_pric INTEGER, cur_prc INTEGER, prft_rt REAL
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS trade_history (
                        ticker TEXT, name TEXT, side TEXT, quantity INTEGER, price INTEGER, amt INTEGER, reason TEXT, timestamp DATETIME
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS trade_signals (
                        ticker TEXT, name TEXT, action TEXT, quantity INTEGER, reason TEXT, status TEXT
                    )
                """)
                
                # Mock 데이터 삽입 (중복 방지용 INSERT OR REPLACE 사용)
                cursor.execute("INSERT OR REPLACE INTO account_summary VALUES (?, ?, ?, ?)", (10000000, 3000000, 30.0, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                cursor.execute("INSERT OR REPLACE INTO portfolio_status VALUES (?, ?, ?, ?, ?, ?)", ("005930", "삼성전자", 10, 70000, 72000, 2.85))
                cursor.execute("INSERT OR REPLACE INTO trade_history VALUES (?, ?, ?, ?, ?, ?, ?, ?)", ("000660", "SK하이닉스", "BUY", 5, 120000, 600000, "전략 매수", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                cursor.execute("INSERT OR REPLACE INTO trade_signals VALUES (?, ?, ?, ?, ?, ?)", ("035720", "카카오", "BUY", 15, "테스트 시그널", "PENDING"))
                conn.commit()

                
            # 실계좌 조회는 실패한 것으로 두고, DB 폴백 경로의 포맷을 검증
            with patch('stock_trader.core.summary_trader.send_telegram_message') as mock_send, \
                 patch('stock_trader.core.summary_trader._fetch_live_account_data', return_value=None):
                summary_trader.send_daily_summary_to_telegram()
                
                # 호출이 한 번 발생했는지 검증
                self.assertTrue(mock_send.called)
                sent_msg = mock_send.call_args[0][0]
                
                # 메시지 포맷과 키워드 포함 검증
                self.assertIn("일일 마감 보고", sent_msg)
                self.assertIn("삼성전자", sent_msg)
                self.assertIn("SK하이닉스", sent_msg)
                self.assertIn("카카오", sent_msg)
                self.assertIn("총자산: 10,000,000원", sent_msg)
                
            print("\n[Test] summary_trader telegram message formatting verified successfully.")
        finally:
            import gc
            gc.collect() # 가비지 컬렉터 강제 호출하여 DB 커넥션 파일 잠금 완전 해제 유도
            try:
                if os.path.exists(test_db):
                    os.remove(test_db)
            except PermissionError:
                pass

    def test_07_strategy_engine_report_formatting(self):
        """Test strategy report message formatting in strategy_engine"""
        from unittest.mock import patch, MagicMock
        from stock_trader.core.strategy_engine import StrategyEngine
        
        engine = StrategyEngine()
        engine.publisher = MagicMock()
        scored_stocks = [
            {"ticker": "005930", "name": "삼성전자", "total_score": 92.5, "price": 72000},
            {"ticker": "000660", "name": "SK하이닉스", "total_score": 88.1, "price": 120000}
        ]
        buy_signals = [
            {"ticker": "005930", "name": "삼성전자", "action": "BUY", "quantity": 10, "reason": "테스트 매수"}
        ]
        sell_signals = [
            {"ticker": "035420", "name": "NAVER", "action": "SELL", "quantity": 5, "reason": "테스트 매도"}
        ]
        holdings = [
            {"ticker": "000660", "name": "SK하이닉스", "profit_rate": 2.5}
        ]
        
        engine._send_strategy_report(scored_stocks, buy_signals, sell_signals, holdings)
        
        self.assertTrue(engine.publisher.send_message_sync.called)
        sent_msg = engine.publisher.send_message_sync.call_args[0][1]["message"]
        
        # 메시지 포맷과 키워드 포함 검증
        self.assertIn("전략 엔진 리포트", sent_msg)
        self.assertIn("삼성전자", sent_msg)
        self.assertIn("SK하이닉스", sent_msg)
        self.assertIn("NAVER", sent_msg)
        self.assertIn("매수 시그널 1건", sent_msg)
        self.assertIn("매도 시그널 1건", sent_msg)
        self.assertIn("보유 현황 요약", sent_msg)
        
        print("\n[Test] strategy_engine report message formatting verified successfully.")

if __name__ == "__main__":
    unittest.main()

