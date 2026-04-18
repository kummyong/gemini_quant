import os
import sqlite3
import logging
import sys
from datetime import datetime

# FinanceDataReader 설치 여부 확인 및 동적 임포트
try:
    import FinanceDataReader as fdr
    import pandas as pd
except ImportError:
    print("⚠️ FinanceDataReader 또는 pandas가 설치되어 있지 않습니다. 'pip install finance-datareader pandas'를 실행해 주세요.")
    sys.exit(1)

# --- 경로 및 환경 설정 ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, 'logs')
DB_PATH = os.path.join(LOG_DIR, "system_monitor.db")

# 로그 디렉토리가 없으면 생성
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

# 로깅 설정 (Quant Standard)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'universe_feeder.log'), encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

class UniverseFeeder:
    def __init__(self, db_path):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """데이터베이스 및 테이블 초기화 (Schema Enforcement)"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                # trade_signals 테이블 생성 (ticker를 PK로 하여 중복 방지)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS trade_signals (
                        ticker TEXT PRIMARY KEY,
                        name TEXT,
                        status TEXT,
                        reason TEXT,
                        created_at DATETIME
                    )
                """)
                conn.commit()
                logging.info("🏛️ DB 테이블 세팅 완료")
        except Exception as e:
            logging.error(f"❌ DB 초기화 실패: {e}")

    def fetch_bluechip_universe(self, top_n=50):
        """FinanceDataReader를 활용한 KOSPI 시총 상위 우량주 추출"""
        try:
            logging.info(f"🔍 KOSPI 시가총액 상위 {top_n} 종목 수집 시작...")
            
            # 1. 코스피 종목 리스팅
            df = fdr.StockListing('KOSPI')
            
            # 2. 필터링 (우선주, 스팩, ETF/ETN 제외 로직)
            exclude_keywords = ['우', '스팩', '호', '1우', '2우', 'B', 'ETN', 'ETF']
            mask = df['Name'].str.contains('|'.join(exclude_keywords), na=False)
            df = df[~mask]
            
            # 3. 시가총액 기준 내림차순 정렬 후 상위 N개 추출
            # FinanceDataReader 버전에 따라 컬럼명이 'Marcap' 또는 'MarketCap'일 수 있음
            marcap_col = 'Marcap' if 'Marcap' in df.columns else ('MarketCap' if 'MarketCap' in df.columns else None)
            if not marcap_col:
                logging.error("❌ 시가총액 컬럼(Marcap/MarketCap)을 찾을 수 없습니다.")
                return pd.DataFrame()

            universe = df.sort_values(by=marcap_col, ascending=False).head(top_n)
            
            # 4. 티커 컬럼명 확인 ('Symbol' 또는 'Code')
            ticker_col = 'Symbol' if 'Symbol' in universe.columns else ('Code' if 'Code' in universe.columns else None)
            if not ticker_col:
                logging.error("❌ 티커 컬럼(Symbol/Code)을 찾을 수 없습니다.")
                return pd.DataFrame()

            logging.info(f"✅ 유니버스 수집 완료: {len(universe)} 종목 선정")
            # 표준 컬럼명으로 리네임하여 반환
            return universe[[ticker_col, 'Name']].rename(columns={ticker_col: 'Symbol'})
            
        except Exception as e:
            logging.error(f"❌ 데이터 수집 중 예외 발생: {e}")
            return pd.DataFrame()

    def update_trade_signals(self, universe_df):
        """DB의 PENDING 신호를 초기화하고 새로운 유니버스를 적재"""
        if universe_df.empty:
            logging.warning("⚠️ 입력 데이터가 비어 있어 업데이트를 중단합니다.")
            return

        try:
            with sqlite3.connect(self.db_path, timeout=30) as conn:
                cursor = conn.cursor()
                
                # 1. 기존 PENDING 상태인 낡은 데이터 삭제 (DONE 상태는 보존)
                cursor.execute("DELETE FROM trade_signals WHERE status = 'PENDING'")
                deleted_count = cursor.rowcount
                logging.info(f"🗑️ 기존 대기 신호 {deleted_count}건 초기화 완료")
                
                # 2. 새로운 우량주 유니버스 삽입 (PK 충돌 시 무시하거나 업데이트)
                now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                insert_data = [
                    (row['Symbol'], row['Name'], 'PENDING', 'Top 50 Bluechip', now)
                    for _, row in universe_df.iterrows()
                ]
                
                cursor.executemany("""
                    INSERT OR REPLACE INTO trade_signals (ticker, name, status, reason, created_at)
                    VALUES (?, ?, ?, ?, ?)
                """, insert_data)
                
                conn.commit()
                logging.info(f"🎯 신규 유니버스 {len(insert_data)}개 종목 적재 완료 (상태: PENDING)")

        except sqlite3.OperationalError as e:
            logging.error(f"❌ DB 잠금(Lock) 또는 운영 에러: {e}")
        except Exception as e:
            logging.error(f"❌ 데이터 적재 실패: {e}")

    def run(self):
        """피더 실행 메인 프로세스"""
        start_time = datetime.now()
        logging.info("🚀 [Universe Feeder] 과녁 공급 프로세스 시작")
        
        # 1. 우량주 리스트 가져오기
        universe = self.fetch_bluechip_universe(top_n=50)
        
        # 2. DB 업데이트
        self.update_trade_signals(universe)
        
        duration = datetime.now() - start_time
        logging.info(f"🏁 프로세스 종료 (소요시간: {duration.total_seconds():.2f}초)")

if __name__ == "__main__":
    feeder = UniverseFeeder(DB_PATH)
    feeder.run()
