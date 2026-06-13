import os
import sys
import sqlite3
import logging
import json
from datetime import datetime

# 프로젝트 루트(gemini-quant)를 sys.path에 추가하여 절대 경로 임포트 지원
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# FinanceDataReader 설치 여부 확인 및 동적 임포트
try:
    import FinanceDataReader as fdr
    import pandas as pd
except ImportError:
    print("⚠️ FinanceDataReader 또는 pandas가 설치되어 있지 않습니다. 'pip install finance-datareader pandas'를 실행해 주세요.")
    sys.exit(1)

# --- 경로 및 환경 설정 ---
from stock_trader.config import LOG_DIR, DB_PATH

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

    def fetch_bluechip_universe(self, top_n=200):
        """FinanceDataReader를 활용하여 우량주 추출 (에러 발생 시 네이버 금융 시총 페이지 크롤링 폴백 적용)"""
        try:
            logging.info(f"🔍 KOSPI 시가총액 상위 {top_n} 종목 수집 시작...")
            
            # 1. 코스피 종목 리스팅 시도
            df = fdr.StockListing('KOSPI')
            
            # 2. 필터링 (우선주, 스팩, ETF/ETN 제외 로직)
            exclude_keywords = ['우', '스팩', '호', '1우', '2우', 'B', 'ETN', 'ETF']
            mask = df['Name'].str.contains('|'.join(exclude_keywords), na=False)
            df = df[~mask]
            
            marcap_col = 'Marcap' if 'Marcap' in df.columns else ('MarketCap' if 'MarketCap' in df.columns else None)
            if marcap_col:
                universe = df.sort_values(by=marcap_col, ascending=False).head(top_n)
                ticker_col = 'Symbol' if 'Symbol' in universe.columns else ('Code' if 'Code' in universe.columns else None)
                if ticker_col:
                    logging.info(f"✅ FDR 기반 유니버스 수집 완료: {len(universe)} 종목 선정")
                    return universe[[ticker_col, 'Name']].rename(columns={ticker_col: 'Symbol'})
        except Exception as e:
            logging.warning(f"⚠️ FinanceDataReader 수집 실패 (네이버 금융 크롤링 폴백 구동): {e}")
            
        # 네이버 금융 시총 크롤링 폴백 로직
        try:
            import requests
            from bs4 import BeautifulSoup
            import re
            import time
            
            tickers = []
            page = 1
            max_pages = (top_n // 50) + 1  # 200개면 4페이지 필요
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
            
            while len(tickers) < top_n and page <= max_pages + 1:
                url = f"https://finance.naver.com/sise/sise_market_sum.naver?sosok=0&page={page}" # sosok=0: KOSPI
                res = requests.get(url, headers=headers, timeout=10)
                res.encoding = 'euc-kr'
                soup = BeautifulSoup(res.text, 'lxml')
                
                # 종목 테이블 파싱
                table = soup.find('table', class_='type_2')
                if not table:
                    break
                    
                rows = table.find_all('tr')
                page_added = 0
                for row in rows:
                    a_tag = row.find('a', class_='tltle')
                    if a_tag:
                        name = a_tag.get_text().strip()
                        href = a_tag.get('href', '')
                        m = re.search(r'code=(\d+)', href)
                        if m:
                            code = m.group(1)
                            # 우선주 등 거름망
                            if not name.endswith('우') and not name.endswith('우B') and not name.endswith('우C') and '스팩' not in name:
                                tickers.append({'Symbol': code, 'Name': name})
                                page_added += 1
                                if len(tickers) >= top_n:
                                    break
                if page_added == 0:
                    break
                page += 1
                time.sleep(0.2)
                
            if tickers:
                universe_df = pd.DataFrame(tickers).head(top_n)
                logging.info(f"✅ 네이버 금융 폴백 기반 유니버스 수집 완료: {len(universe_df)} 종목 선정")
                return universe_df
        except Exception as naver_e:
            logging.error(f"❌ 네이버 금융 폴백 크롤링 실패: {naver_e}")
            
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
                
                # 2. 새로운 우량주 유니버스 삽입 (action='HOLD' 추가하여 DB NOT NULL 제약 회피)
                now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                insert_data = [
                    (row['Symbol'], row['Name'], 'HOLD', 'PENDING', 'Top 200 KOSPI Universe', now)
                    for _, row in universe_df.iterrows()
                ]
                
                cursor.executemany("""
                    INSERT OR REPLACE INTO trade_signals (ticker, name, action, status, reason, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, insert_data)
                
                conn.commit()
                logging.info(f"🎯 신규 유니버스 {len(insert_data)}개 종목 적재 완료 (상태: PENDING, 행동: HOLD)")

        except sqlite3.OperationalError as e:
            logging.error(f"❌ DB 잠금(Lock) 또는 운영 에러: {e}")
        except Exception as e:
            logging.error(f"❌ 데이터 적재 실패: {e}")

    def write_active_universe_json(self, universe_df):
        """active_universe.json 파일에 추출된 종목 리스트를 저장하여 전략 엔진과 연동합니다."""
        if universe_df.empty:
            return
        
        try:
            # logs 디렉토리 내 active_universe.json 경로
            json_path = os.path.join(LOG_DIR, "active_universe.json")
            
            tickers_list = []
            for _, row in universe_df.iterrows():
                tickers_list.append({
                    "ticker": row['Symbol'],
                    "name": row['Name']
                })
                
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump({"tickers": tickers_list}, f, ensure_ascii=False, indent=2)
                
            logging.info(f"💾 active_universe.json 업데이트 완료 ({len(tickers_list)}개 종목 적재)")
        except Exception as e:
            logging.error(f"❌ active_universe.json 저장 실패: {e}")

    def run(self):
        """피더 실행 메인 프로세스"""
        start_time = datetime.now()
        logging.info("🚀 [Universe Feeder] 과녁 공급 프로세스 시작")
        
        # 1. 코스피 시총 상위 200개 종목 수집
        universe = self.fetch_bluechip_universe(top_n=200)
        
        # 2. DB 업데이트
        self.update_trade_signals(universe)
        
        # 3. active_universe.json에 기록하여 strategy_engine.py 연동
        self.write_active_universe_json(universe)
        
        duration = datetime.now() - start_time
        logging.info(f"🏁 프로세스 종료 (소요시간: {duration.total_seconds():.2f}초)")

if __name__ == "__main__":
    feeder = UniverseFeeder(DB_PATH)
    feeder.run()
