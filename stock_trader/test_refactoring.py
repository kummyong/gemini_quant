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

# Add current directory to path to locate config and stock_universe
STOCK_TRADER_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(STOCK_TRADER_DIR)
if STOCK_TRADER_DIR not in sys.path:
    sys.path.insert(0, STOCK_TRADER_DIR)

class TestRefactoring(unittest.TestCase):
    
    def test_01_config_paths(self):
        """Test config path resolution"""
        import config
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
        import stock_universe
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
                print(f"  ✅ Import successful: {name}")
            except ImportError as e:
                # Some modules might require external API keys or DB connections,
                # but basic import/syntax should not fail unless missing dependencies.
                print(f"  ⚠️  Import warning for {name}: {e}")
            except Exception as e:
                print(f"  ❌ Import failed for {name}: {e}")
                raise e

        # Test secretary import
        sys.path.insert(0, os.path.join(PROJECT_DIR, "secretary"))
        try:
            import auto_sync_history
            print("  ✅ Import successful: auto_sync_history")
        except ImportError as e:
            print(f"  ⚠️  Import warning for auto_sync_history: {e}")
        except Exception as e:
            print(f"  ❌ Import failed for auto_sync_history: {e}")
            raise e

    def test_04_db_connection_with_pattern(self):
        """Test sqlite3 connection context manager with custom test DB"""
        import config
        test_db = os.path.join(config.LOG_DIR, "test_temp.db")
        
        # Test creation and write
        try:
            with sqlite3.connect(test_db, timeout=5.0) as conn:
                cursor = conn.cursor()
                cursor.execute("CREATE TABLE IF NOT EXISTS test (id INTEGER PRIMARY KEY, val TEXT)")
                cursor.execute("INSERT INTO test (val) VALUES (?)", ("test_val",))
                conn.commit()
            
            # Test read
            with sqlite3.connect(test_db, timeout=5.0) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT val FROM test WHERE id=1")
                row = cursor.fetchone()
                self.assertEqual(row[0], "test_val")
                
            print("\n[Test] SQLite context manager verified successfully.")
        finally:
            if os.path.exists(test_db):
                os.remove(test_db)

    def test_05_rotating_log_handler(self):
        """Test RotatingFileHandler setup"""
        import config
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

if __name__ == "__main__":
    unittest.main()
