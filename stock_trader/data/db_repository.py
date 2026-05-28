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
                ("BULL_RSI", 30.0),
                ("BULL_BB", 2.0),
                ("BEAR_RSI", 25.0),
                ("BEAR_BB", 2.2)
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

    def get_strategy_hyperparams(self) -> dict:
        """전략 하이퍼파라미터 전량 조회"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT param_key, param_value FROM strategy_hyperparams")
            return {row[0]: float(row[1]) for row in cursor.fetchall()}

    def save_system_metric(self, timestamp: str, cpu_load_1m: float, cpu_usage: float, battery_level: str, cpu_temp: str, mem_total: int, mem_used: int, mem_avail: int, mem_pct: float):
        """시스템 하드웨어 메트릭 정보 저장"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO system_metrics 
                (timestamp, cpu_load_1m, cpu_usage, battery_level, cpu_temp, mem_total_kb, mem_used_kb, mem_available_kb, mem_usage_pct)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (timestamp, cpu_load_1m, cpu_usage, battery_level, cpu_temp, mem_total, mem_used, mem_avail, mem_pct))

    def sync_ohlcv_data(self, ticker: str, force_full: bool = False):
        """웹에서 종목의 최신 OHLCV 데이터를 조회하여 DB에 차분(Delta) 병합합니다."""
        try:
            import FinanceDataReader as fdr
            from datetime import datetime, timedelta
            import pytz
            
            KST = pytz.timezone('Asia/Seoul')
            now = datetime.now(KST)
            
            start_date = (now - timedelta(days=200)).strftime('%Y-%m-%d')
            
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

    def get_recent_ohlcv(self, ticker: str, limit: int = 150):
        """DB에서 가장 최신 OHLCV 데이터를 Pandas DataFrame으로 반환합니다."""
        import pandas as pd
        with self.get_connection() as conn:
            query = f"SELECT date as Date, open as Open, high as High, low as Low, close as Close, volume as Volume, change as Change FROM ohlcv_data WHERE ticker = '{ticker}' ORDER BY date DESC LIMIT {limit}"
            df = pd.read_sql_query(query, conn)
            if df.empty:
                return df
            
            # 날짜순 정렬 (과거 -> 최신)
            df = df.sort_values('Date').reset_index(drop=True)
            df['Date'] = pd.to_datetime(df['Date'])
            df.set_index('Date', inplace=True)
            return df

