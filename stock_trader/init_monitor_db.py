import sqlite3
import os

# 데이터베이스 경로 설정
DB_PATH = "/root/workspace/gemini-quant/stock_trader/logs/system_monitor.db"

def init_db():
    # 저장 디렉토리 생성 확인
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    
    conn = sqlite3.connect(DB_PATH)
    
    try:
        # 1. [요구사항 반영] DB 동시성 에러 방지를 위한 WAL(Write-Ahead Logging) 모드 설정
        conn.execute("PRAGMA journal_mode=WAL;")
        
        cursor = conn.cursor()
        
        # --- [테이블 생성 섹션] ---
        
        # account_summary: 자산 변동 정보 (히스토리 누적)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS account_summary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT (datetime('now', 'localtime')),
            total_assets INTEGER,
            cash INTEGER,
            cash_ratio REAL
        )
        """)

        # portfolio_status: 실시간 보유 종목 현황 (현재 스냅샷)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_status (
            stk_cd TEXT PRIMARY KEY,
            stk_nm TEXT,
            rmnd_qty INTEGER CHECK(rmnd_qty >= 0),
            pur_pric INTEGER,
            cur_prc INTEGER,
            prft_rt REAL,
            pred_sellq INTEGER DEFAULT 0,
            tdy_sellq INTEGER DEFAULT 0,
            last_updated DATETIME DEFAULT (datetime('now', 'localtime'))
        )
        """)

        # trade_signals: 매매 전략 신호 관리
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS trade_signals (
            ticker TEXT PRIMARY KEY,
            name TEXT,
            action TEXT, -- BUY, SELL, HOLD
            quantity INTEGER DEFAULT 0,
            reason TEXT,
            status TEXT DEFAULT 'PENDING', -- PENDING, DONE, CANCELLED
            created_at DATETIME DEFAULT (datetime('now', 'localtime'))
        )
        """)
        # trade_history 테이블: 실제 매매 체결 이력 저장
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS trade_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT (datetime('now', 'localtime')),
            ticker TEXT,
            name TEXT,
            side TEXT, -- BUY, SELL
            quantity INTEGER,
            price INTEGER,
            amt INTEGER,
            reason TEXT
        )
        """)

        # scheduled_tasks 테이블: 지능형 예약 실행 비서 (v4.0 추가)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at DATETIME DEFAULT (datetime('now', 'localtime')),
            scheduled_at DATETIME,
            content TEXT,
            intent TEXT, -- BUY, SELL, STATUS, CMD, REMIND
            params TEXT, -- JSON 형식의 매개변수 (ticker, qty 등)
            status TEXT DEFAULT 'PENDING', -- PENDING, DONE, FAILED
            result_msg TEXT
        )
        """)

        # training_data 테이블: 자가 학습 데이터 (v5.0 추가)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS training_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_text TEXT NOT NULL,
            predicted_label TEXT,
            actual_label TEXT,
            confidence REAL,
            is_trained INTEGER DEFAULT 0, -- 0: 미학습, 1: 학습완료
            created_at DATETIME DEFAULT (datetime('now', 'localtime'))
        )
        """)

        # 인덱스 생성
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON scheduled_tasks (status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_time ON scheduled_tasks (scheduled_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_training_istrained ON training_data (is_trained)")

        # --- [인덱스 생성 섹션: 요구사항 반영] ---
        
        # 2-1. portfolio_status (stk_cd): 종목 코드로 잔고 조회 시 성능 향상
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_portfolio_stk_cd ON portfolio_status (stk_cd)")
        
        # 2-2. trade_signals (status): 'PENDING' 상태의 신호만 필터링할 때 성능 향상
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_status ON trade_signals (status)")
        
        # 2-3. trade_history (timestamp): 날짜별 매매 이력(오늘의 매매 등) 조회 시 성능 향상
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_history_timestamp ON trade_history (timestamp)")
        
        # 기존 인덱스 유지
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_account_ts ON account_summary (timestamp)")

        conn.commit()
        print(f"✅ [System v3.0] SQLite DB 최적화 초기화 완료")
        print(f"   - 모드: WAL (Write-Ahead Logging) 활성화")
        print(f"   - 경로: {DB_PATH}")
        print(f"   - 최적화 인덱스 3종 생성 완료")

    except Exception as e:
        print(f"❌ DB 초기화 중 오류 발생: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    init_db()
