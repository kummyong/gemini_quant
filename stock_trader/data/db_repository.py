import os
import sqlite3
import json
import logging
from contextlib import contextmanager

# 전역 로거 설정
logger = logging.getLogger("DbRepository")

class DbRepository:
    def __init__(self, db_path: str):
        self.db_path = db_path
        # 데이터베이스 디렉토리 자동 생성
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.init_db()

    @contextmanager
    def get_connection(self):
        """커넥션 획득 및 컨텍스트 관리 (WAL 설정 및 최적화 PRAGMA 일괄 적용)"""
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            conn.execute("PRAGMA cache_size=-64000;")  # 64MB 캐시 설정
            conn.execute("PRAGMA foreign_keys=ON;")
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"❌ 데이터베이스 트랜잭션 에러: {e}")
            raise e
        finally:
            conn.close()

    def init_db(self):
        """데이터베이스 스키마 초기화 및 마이그레이션 통합 관리"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # 1. account_summary
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS account_summary (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT (datetime('now', 'localtime')),
                total_assets INTEGER,
                cash INTEGER,
                cash_ratio REAL
            )
            """)

            # 2. portfolio_status 테이블 체크 및 마이그레이션
            cursor.execute("PRAGMA table_info(portfolio_status)")
            cols = [col[1] for col in cursor.fetchall()]
            
            if len(cols) > 0 and "broker_id" not in cols:
                logger.info("⚙️ portfolio_status 테이블에 broker_id 추가 마이그레이션 진행 중...")
                # 기존 데이터 임시 백업 후 테이블 구조 재정의
                cursor.execute("ALTER TABLE portfolio_status RENAME TO _portfolio_status_old")
                cursor.execute("""
                CREATE TABLE portfolio_status (
                    broker_id TEXT NOT NULL DEFAULT 'KIWOOM',
                    stk_cd TEXT NOT NULL,
                    stk_nm TEXT,
                    rmnd_qty INTEGER CHECK(rmnd_qty >= 0),
                    pur_pric INTEGER,
                    cur_prc INTEGER,
                    prft_rt REAL,
                    max_profit_rate REAL DEFAULT 0.0,
                    pred_sellq INTEGER DEFAULT 0,
                    tdy_sellq INTEGER DEFAULT 0,
                    last_updated DATETIME DEFAULT (datetime('now', 'localtime')),
                    PRIMARY KEY (broker_id, stk_cd)
                )
                """)
                cursor.execute("""
                INSERT INTO portfolio_status (broker_id, stk_cd, stk_nm, rmnd_qty, pur_pric, cur_prc, prft_rt, max_profit_rate, pred_sellq, tdy_sellq, last_updated)
                SELECT 'KIWOOM', stk_cd, stk_nm, rmnd_qty, pur_pric, cur_prc, prft_rt, max_profit_rate, pred_sellq, tdy_sellq, last_updated FROM _portfolio_status_old
                """)
                cursor.execute("DROP TABLE _portfolio_status_old")
                logger.info("✅ portfolio_status 테이블 마이그레이션 완료")
            else:
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS portfolio_status (
                    broker_id TEXT NOT NULL DEFAULT 'KIWOOM',
                    stk_cd TEXT NOT NULL,
                    stk_nm TEXT,
                    rmnd_qty INTEGER CHECK(rmnd_qty >= 0),
                    pur_pric INTEGER,
                    cur_prc INTEGER,
                    prft_rt REAL,
                    max_profit_rate REAL DEFAULT 0.0,
                    pred_sellq INTEGER DEFAULT 0,
                    tdy_sellq INTEGER DEFAULT 0,
                    last_updated DATETIME DEFAULT (datetime('now', 'localtime')),
                    PRIMARY KEY (broker_id, stk_cd)
                )
                """)

            # 3. trade_signals 테이블 체크 및 마이그레이션
            cursor.execute("PRAGMA table_info(trade_signals)")
            sig_cols = [col[1] for col in cursor.fetchall()]
            if len(sig_cols) > 0 and "broker_id" not in sig_cols:
                logger.info("⚙️ trade_signals 테이블에 broker_id 컬럼 추가 중...")
                cursor.execute("ALTER TABLE trade_signals ADD COLUMN broker_id TEXT DEFAULT 'KIWOOM'")
            if len(sig_cols) > 0 and "features" not in sig_cols:
                logger.info("⚙️ trade_signals 테이블에 features 컬럼 추가 중...")
                cursor.execute("ALTER TABLE trade_signals ADD COLUMN features TEXT")

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS trade_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                broker_id TEXT DEFAULT 'KIWOOM',
                ticker TEXT NOT NULL,
                name TEXT NOT NULL,
                action TEXT NOT NULL CHECK(action IN ('BUY', 'SELL', 'HOLD')),
                quantity INTEGER DEFAULT 0 CHECK(quantity >= 0),
                reason TEXT,
                status TEXT DEFAULT 'PENDING' CHECK(status IN ('PENDING', 'DONE', 'CANCELLED', 'EXPIRED')),
                created_at DATETIME DEFAULT (datetime('now', 'localtime')),
                features TEXT
            )
            """)

            # 4. trade_history
            cursor.execute("PRAGMA table_info(trade_history)")
            hist_cols = [col[1] for col in cursor.fetchall()]
            if len(hist_cols) > 0 and "features" not in hist_cols:
                logger.info("⚙️ trade_history 테이블에 features 컬럼 추가 중...")
                cursor.execute("ALTER TABLE trade_history ADD COLUMN features TEXT")

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS trade_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT (datetime('now', 'localtime')),
                ticker TEXT,
                name TEXT,
                side TEXT CHECK(side IN ('BUY', 'SELL')),
                quantity INTEGER,
                price INTEGER,
                amt INTEGER,
                reason TEXT,
                features TEXT
            )
            """)

            # 5. scheduled_tasks
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at DATETIME DEFAULT (datetime('now', 'localtime')),
                scheduled_at DATETIME,
                content TEXT,
                intent TEXT CHECK(intent IN ('BUY', 'SELL', 'STATUS', 'CMD', 'REMIND')),
                params TEXT, -- JSON 포맷
                status TEXT DEFAULT 'PENDING' CHECK(status IN ('PENDING', 'DONE', 'FAILED')),
                result_msg TEXT
            )
            """)

            # 6. training_data
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS training_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                raw_text TEXT NOT NULL,
                predicted_label TEXT,
                actual_label TEXT,
                confidence REAL,
                is_trained INTEGER DEFAULT 0 CHECK(is_trained IN (0, 1)),
                created_at DATETIME DEFAULT (datetime('now', 'localtime'))
            )
            """)

            # 7. strategy_hyperparams
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS strategy_hyperparams (
                param_key TEXT PRIMARY KEY,
                param_value REAL,
                updated_at DATETIME DEFAULT (datetime('now', 'localtime'))
            )
            """)

            # 8. system_metrics (신규 추가 및 인덱싱 처리)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS system_metrics (
                timestamp DATETIME PRIMARY KEY,
                cpu_load_1m REAL,
                cpu_usage REAL,
                battery_level TEXT,
                cpu_temp TEXT,
                mem_total_kb INTEGER,
                mem_used_kb INTEGER,
                mem_available_kb INTEGER,
                mem_usage_pct REAL
            )
            """)

            # 9. ohlcv_data (로컬 시계열 데이터 캐싱)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS ohlcv_data (
                ticker TEXT NOT NULL,
                date TEXT NOT NULL,
                open INTEGER,
                high INTEGER,
                low INTEGER,
                close INTEGER,
                volume INTEGER,
                change REAL,
                PRIMARY KEY (ticker, date)
            )
            """)

            # 10. stopped_positions (샹들리에 스탑 청산된 포지션 관리 - 쿨다운용)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS stopped_positions (
                ticker TEXT NOT NULL,
                stop_date TEXT NOT NULL,
                stop_price REAL,
                profile TEXT NOT NULL,
                PRIMARY KEY (ticker, stop_date)
            )
            """)

            # 11. market_lockout (시장 락아웃 상태 기록)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS market_lockout (
                id INTEGER PRIMARY KEY DEFAULT 1,
                active INTEGER DEFAULT 0 CHECK(active IN (0, 1)),
                since TEXT,
                reason TEXT
            )
            """)
            cursor.execute("INSERT OR IGNORE INTO market_lockout (id, active, since, reason) VALUES (1, 0, NULL, NULL)")

            # --- 인덱스 설정 최적화 ---
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON scheduled_tasks (status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_time ON scheduled_tasks (scheduled_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_status ON trade_signals (status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_ticker ON trade_signals (ticker)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_history_timestamp ON trade_history (timestamp)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_account_ts ON account_summary (timestamp)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_metrics_timestamp ON system_metrics (timestamp)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_training_untrained ON training_data (is_trained) WHERE is_trained = 0")

            # 하이퍼파라미터 초기 값 셋업
            initial_params = [
                ("RSI_BUY_THRES", 30.0),
                ("RSI_SELL_THRES", 70.0),
                ("BB_STD", 2.0),
                ("TRAILING_STOP_DROP", 3.0),
                ("HARD_STOP_LOSS", -5.0),
                ("CHANDELIER_ATR_MULT", 1.5),
                ("TRAILING_MIN_DROP", 2.0),
                ("TRAILING_MAX_DROP", 5.0),
                ("BULL_RSI", 30.0),
                ("BULL_BB", 2.0),
                ("BEAR_RSI", 25.0),
                ("BEAR_BB", 2.2),
                ("ETF_ATR_PERIOD", 20.0),
                ("ETF_ATR_MULTIPLIER", 3.0),
                ("ETF_TREND_SMA_PERIOD", 200.0),
                ("ETF_REENTRY_SMA_PERIOD", 50.0),
                ("ETF_STOP_COOLDOWN_DAYS", 5.0)
            ]
            for key, val in initial_params:
                cursor.execute("""
                    INSERT OR IGNORE INTO strategy_hyperparams (param_key, param_value)
                    VALUES (?, ?)
                """, (key, val))

            logger.info("✅ 데이터베이스 스키마 최적화 및 인덱싱 구조화 완료")

    # --- 데이터 엑세스 API 구현 ---

    def save_trade_signal(self, ticker: str, name: str, action: str, quantity: int, reason: str, status: str = 'PENDING', broker_id: str = 'KIWOOM', features: str = None) -> int:
        """신규 트레이딩 시그널을 이력과 함께 삽입 (과거 데이터 보존 가능)"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO trade_signals (ticker, name, action, quantity, reason, status, broker_id, features)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (ticker, name, action, quantity, reason, status, broker_id, features))
            return cursor.lastrowid

    def expire_pending_signals(self):
        """대기 중인 이전 PENDING 시그널들을 만료 처리"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE trade_signals SET status = 'EXPIRED' WHERE status = 'PENDING'")

    def get_portfolio_holdings(self):
        """보유 수량이 있는 종목의 목록 조회"""
        with self.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT broker_id, stk_cd, stk_nm, prft_rt, rmnd_qty, pur_pric, cur_prc, max_profit_rate FROM portfolio_status WHERE rmnd_qty > 0")
            return [dict(row) for row in cursor.fetchall()]

    def update_portfolio_holding(self, stk_cd: str, stk_nm: str, rmnd_qty: int, pur_pric: float, cur_prc: float, prft_rt: float, max_profit_rate: float, broker_id: str = 'KIWOOM'):
        """보유 현황 업데이트 (Upsert 패턴)"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO portfolio_status (broker_id, stk_cd, stk_nm, rmnd_qty, pur_pric, cur_prc, prft_rt, max_profit_rate, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
                ON CONFLICT(broker_id, stk_cd) DO UPDATE SET
                    rmnd_qty = excluded.rmnd_qty,
                    pur_pric = excluded.pur_pric,
                    cur_prc = excluded.cur_prc,
                    prft_rt = excluded.prft_rt,
                    max_profit_rate = excluded.max_profit_rate,
                    last_updated = excluded.last_updated
            """, (broker_id, stk_cd, stk_nm, rmnd_qty, pur_pric, cur_prc, prft_rt, max_profit_rate))

    def reconcile_portfolio_holding(self, broker_id: str, stk_cd: str, stk_nm: str, rmnd_qty: int, pur_pric: float, cur_prc: float, prft_rt: float):
        """브로커 실계좌 값으로 포지션을 갱신합니다 (계좌가 진실, DB는 사본).
        신규 등록 시 peak_close(=max_profit_rate 0.0, 즉 평균단가 자체)로 초기화하고,
        기존 레코드가 있으면 전략 상태인 max_profit_rate는 절대 덮어쓰지 않습니다."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO portfolio_status (broker_id, stk_cd, stk_nm, rmnd_qty, pur_pric, cur_prc, prft_rt, max_profit_rate, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0.0, datetime('now', 'localtime'))
                ON CONFLICT(broker_id, stk_cd) DO UPDATE SET
                    stk_nm = excluded.stk_nm,
                    rmnd_qty = excluded.rmnd_qty,
                    pur_pric = excluded.pur_pric,
                    cur_prc = excluded.cur_prc,
                    prft_rt = excluded.prft_rt,
                    last_updated = excluded.last_updated
            """, (broker_id, stk_cd, stk_nm, rmnd_qty, pur_pric, cur_prc, prft_rt))

    def delete_portfolio_holding(self, broker_id: str, stk_cd: str):
        """지정된 브로커/종목의 포지션 기록을 삭제합니다 (유령 포지션 정리 등)."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM portfolio_status WHERE broker_id = ? AND stk_cd = ?", (broker_id, stk_cd))

    def clear_all_portfolio_holdings(self, broker_id: str):
        """지정된 브로커의 모든 포지션 기록을 일괄 삭제합니다 (계좌 리셋 감지 시 사용).
        stopped_positions, market_lockout은 별도 테이블이므로 영향받지 않습니다."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM portfolio_status WHERE broker_id = ?", (broker_id,))

    def get_strategy_hyperparams(self) -> dict:
        """전략 하이퍼파라미터 전량 조회"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT param_key, param_value FROM strategy_hyperparams")
            return {row[0]: float(row[1]) for row in cursor.fetchall()}

    def upsert_hyperparam(self, param_key: str, param_value: float):
        """하이퍼파라미터 단건 저장/갱신"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO strategy_hyperparams (param_key, param_value, updated_at)
                VALUES (?, ?, datetime('now', 'localtime'))
                ON CONFLICT(param_key) DO UPDATE SET
                    param_value = excluded.param_value,
                    updated_at = excluded.updated_at
            """, (param_key, param_value))

    def get_max_profit_rates(self, broker_id: str) -> dict:
        """지정 브로커 보유 종목의 stk_cd -> max_profit_rate 매핑 조회"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT stk_cd, max_profit_rate FROM portfolio_status WHERE broker_id = ?", (broker_id,))
            return {row[0]: float(row[1]) if row[1] is not None else 0.0 for row in cursor.fetchall()}

    def mark_holding_sold(self, broker_id: str, stk_cd: str):
        """전량 청산된 포지션의 보유 수량을 0으로 갱신합니다."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE portfolio_status SET rmnd_qty = 0, last_updated = datetime('now', 'localtime') WHERE broker_id = ? AND stk_cd = ?",
                (broker_id, stk_cd))

    def save_trade_history(self, ticker: str, name: str, side: str, quantity: int, price: int, amt: int, reason: str, features: str = None):
        """체결 이력 저장"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO trade_history (ticker, name, side, quantity, price, amt, reason, features) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (ticker, name, side, quantity, price, amt, reason, features))

    def get_pending_signals(self) -> list:
        """PENDING 상태의 매매 신호 전량 조회"""
        with self.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT id, ticker, name, action, quantity, reason, broker_id, features FROM trade_signals WHERE status = 'PENDING'")
            return [dict(row) for row in cursor.fetchall()]

    def complete_signal(self, signal_id: int):
        """매매 신호를 DONE 상태로 갱신"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE trade_signals SET status = 'DONE' WHERE id = ?", (signal_id,))

    def cancel_signal(self, signal_id: int, note: str = None):
        """매매 신호를 CANCELLED 상태로 갱신 (note는 reason 뒤에 덧붙임)"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if note:
                cursor.execute("UPDATE trade_signals SET status = 'CANCELLED', reason = reason || ? WHERE id = ?", (f" [{note}]", signal_id))
            else:
                cursor.execute("UPDATE trade_signals SET status = 'CANCELLED' WHERE id = ?", (signal_id,))

    def save_account_snapshot(self, total_assets: int, cash: int, cash_ratio: float):
        """계좌 요약 스냅샷 저장 (일일 마감 보고 시점 등에 기록)"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO account_summary (total_assets, cash, cash_ratio) VALUES (?, ?, ?)",
                (total_assets, cash, cash_ratio))

    def get_trades_on_date(self, date_str: str) -> list:
        """지정 날짜(YYYY-MM-DD)의 체결 이력 조회"""
        with self.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM trade_history WHERE timestamp LIKE ? ORDER BY timestamp DESC",
                (f"{date_str}%",))
            return [dict(row) for row in cursor.fetchall()]

    def get_recent_training_data(self, limit: int = 100) -> list:
        """최근 학습 피드백 (raw_text, actual_label) 조회"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT raw_text, actual_label FROM training_data ORDER BY created_at DESC LIMIT ?", (limit,))
            return cursor.fetchall()

    def save_system_metric(self, timestamp: str, cpu_load_1m: float, cpu_usage: float, battery_level: str, cpu_temp: str, mem_total: int, mem_used: int, mem_avail: int, mem_pct: float):
        """시스템 하드웨어 메트릭 정보 저장"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO system_metrics 
                (timestamp, cpu_load_1m, cpu_usage, battery_level, cpu_temp, mem_total_kb, mem_used_kb, mem_available_kb, mem_usage_pct)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (timestamp, cpu_load_1m, cpu_usage, battery_level, cpu_temp, mem_total, mem_used, mem_avail, mem_pct))

    def sync_ohlcv_data(self, ticker: str, force_full: bool = False, backfill_days: int = 200):
        """웹에서 종목의 최신 OHLCV 데이터를 조회하여 DB에 차분(Delta) 병합합니다."""
        try:
            import FinanceDataReader as fdr
            from datetime import datetime, timedelta
            import pytz
            
            KST = pytz.timezone('Asia/Seoul')
            now = datetime.now(KST)
            
            start_date = (now - timedelta(days=backfill_days)).strftime('%Y-%m-%d')
            
            if not force_full:
                with self.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT MAX(date) FROM ohlcv_data WHERE ticker = ?", (ticker,))
                    row = cursor.fetchone()
                    if row and row[0]:
                        # 최신 날짜가 오늘과 같다면 조회를 생략하지 않고 갱신(당일 데이터 갱신을 위함)
                        # 단, 시작일을 최근 저장된 날짜부터 가져옴
                        last_date = datetime.strptime(row[0], '%Y-%m-%d')
                        # 최소 3영업일 전 데이터부터 다시 받아서 수정치(수정주가 등)나 오늘 장중 데이터를 덮어씀
                        start_date = (last_date - timedelta(days=3)).strftime('%Y-%m-%d')
            
            df = fdr.DataReader(ticker, start=start_date)
            if df.empty:
                return
                
            with self.get_connection() as conn:
                cursor = conn.cursor()
                for idx, row in df.iterrows():
                    date_str = idx.strftime('%Y-%m-%d')
                    _open = int(row.get('Open', 0))
                    _high = int(row.get('High', 0))
                    _low = int(row.get('Low', 0))
                    _close = int(row.get('Close', 0))
                    _volume = int(row.get('Volume', 0))
                    _change = float(row.get('Change', 0.0))
                    
                    cursor.execute("""
                        INSERT INTO ohlcv_data (ticker, date, open, high, low, close, volume, change)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(ticker, date) DO UPDATE SET
                            open = excluded.open,
                            high = excluded.high,
                            low = excluded.low,
                            close = excluded.close,
                            volume = excluded.volume,
                            change = excluded.change
                    """, (ticker, date_str, _open, _high, _low, _close, _volume, _change))
        except Exception as e:
            logger.error(f"[{ticker}] OHLCV 동기화 중 오류: {e}")

    def get_recent_ohlcv(self, ticker: str, limit: int = 300):
        """DB에서 가장 최신 OHLCV 데이터를 Pandas DataFrame으로 반환합니다."""
        import pandas as pd
        with self.get_connection() as conn:
            query = "SELECT date as Date, open as Open, high as High, low as Low, close as Close, volume as Volume, change as Change FROM ohlcv_data WHERE ticker = ? ORDER BY date DESC LIMIT ?"
            df = pd.read_sql_query(query, conn, params=(ticker, limit))
            if df.empty:
                return df
            
            # 날짜순 정렬 (과거 -> 최신)
            df = df.sort_values('Date').reset_index(drop=True)
            df['Date'] = pd.to_datetime(df['Date'])
            df.set_index('Date', inplace=True)
            return df

    def save_stopped_position(self, ticker: str, stop_date: str, stop_price: float, profile: str):
        """스탑 청산된 포지션을 저장합니다 (쿨다운 추적용)."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO stopped_positions (ticker, stop_date, stop_price, profile)
                VALUES (?, ?, ?, ?)
            """, (ticker, stop_date, stop_price, profile))

    def get_stopped_positions(self, profile: str) -> list:
        """지정된 프로파일의 모든 stopped_positions 조회"""
        with self.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT ticker, stop_date, stop_price, profile FROM stopped_positions WHERE profile = ?", (profile,))
            return [dict(row) for row in cursor.fetchall()]

    def clear_stopped_position(self, ticker: str, profile: str):
        """재진입이 확정된 티커의 stopped_positions 기록을 삭제합니다 (쿨다운 해제)."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM stopped_positions WHERE ticker = ? AND profile = ?", (ticker, profile))

    def update_market_lockout(self, active: bool, since: str = None, reason: str = None):
        """시장 락아웃 상태를 업데이트합니다."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO market_lockout (id, active, since, reason)
                VALUES (1, ?, ?, ?)
            """, (1 if active else 0, since, reason))

    def get_market_lockout(self) -> dict:
        """시장 락아웃 상태를 조회합니다."""
        with self.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT active, since, reason FROM market_lockout WHERE id = 1")
            row = cursor.fetchone()
            if row:
                return dict(row)
            return {"active": 0, "since": None, "reason": None}

