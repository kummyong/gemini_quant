import os
import sqlite3
import random
import logging
import datetime
from typing import List, Dict

# [설정] 경로 및 하이퍼파라미터
# 사용자 환경에 맞춰 조정 가능한 기본 경로 설정
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
DB_PATH = os.path.join(LOG_DIR, "system_monitor.db")
LOG_PATH = os.path.join(LOG_DIR, "strategy_engine.log")

# 팩터 가중치 (총합 100%)
WEIGHT_EARNINGS = 0.40  # 실적 모멘텀
WEIGHT_MACRO = 0.30     # 매크로/산업 트렌드
WEIGHT_INSTITUTIONAL = 0.30  # 수급/기관 확신

# 로깅 설정
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_PATH, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("StrategyEngine")

class StrategyEngine:
    def __init__(self):
        logger.info("중장기 전략 엔진(Strategy Engine) 초기화 중...")
        self._init_db()

    def _init_db(self):
        """데이터베이스 및 테이블 초기화"""
        try:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS trade_signals (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ticker TEXT NOT NULL,
                        name TEXT NOT NULL,
                        action TEXT NOT NULL,
                        quantity INTEGER DEFAULT 0,
                        reason TEXT,
                        status TEXT DEFAULT 'PENDING',
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                conn.commit()
            logger.info(f"데이터베이스 연결 완료: {DB_PATH}")
        except Exception as e:
            logger.error(f"DB 초기화 중 오류 발생: {e}")

    def fetch_market_data(self) -> List[Dict]:
        """
        [Mock] KOSPI 50개 종목 데이터 생성
        실제 API(키움, KRX 등) 연동 시 이 부분을 해당 API 호출로 교체
        """
        logger.info("시장 데이터(Mock) 수집 중...")
        mock_stocks = []
        sample_names = [
            "삼성전자", "SK하이닉스", "LG에너지솔루션", "삼성바이오로직스", "현대차",
            "기아", "셀트리온", "POSCO홀딩스", "KB금융", "NAVER", "삼성물산",
            "신한지주", "삼성SDI", "LG화학", "카카오", "현대모비스", "포스코퓨처엠",
            "하나금융지주", "LG전자", "메리츠금융지주", "삼성생명", "삼성화재",
            "한국전력", "HMM", "에코프로머티", "두산에너빌리티", "고려아연",
            "삼성에스디에스", "대한항공", "카카오뱅크", "삼성전기", "아모레퍼시픽",
            "HD현대중공업", "SK", "KT&G", "포스코인터내셔널", "우리금융지주",
            "SK이노베이션", "S-Oil", "한화에어로스페이스", "LG", "KT", "한미반도체",
            "코웨이", "롯데지주", "엔씨소프트", "하이브", "금호석유", "HD현대"
        ]

        for i, name in enumerate(sample_names):
            ticker = f"{100000 + i:06d}"
            # 팩터 1: 실적 모멘텀 (YoY EPS 성장률 %)
            eps_growth = random.uniform(-20, 100)
            # 팩터 2: 매크로/산업 트렌드 스코어 (0-100)
            industry_score = random.uniform(20, 95)
            # 팩터 3: 수급 (최근 20일 외인/기관 순매수액, 단위: 억)
            net_buying = random.uniform(-500, 2000)

            mock_stocks.append({
                "ticker": ticker,
                "name": name,
                "eps_growth": eps_growth,
                "industry_score": industry_score,
                "net_buying": net_buying
            })
        
        return mock_stocks

    def calculate_scores(self, stocks: List[Dict]) -> List[Dict]:
        """멀티 팩터 점수 계산 및 랭킹 산출"""
        logger.info("멀티 팩터 스코어 계산 시작...")
        
        # 정규화를 위해 최대/최소값 파악 (단순 구현을 위해 Mock 범위 활용 가능)
        for stock in stocks:
            # 실적 모멘텀 정규화 (예: -20%~100% -> 0~100점)
            s1 = max(0, min(100, (stock["eps_growth"] + 20) / 120 * 100))
            # 산업 트렌드는 이미 스코어 형태
            s2 = stock["industry_score"]
            # 수급 정규화 (예: -500억~2000억 -> 0~100점)
            s3 = max(0, min(100, (stock["net_buying"] + 500) / 2500 * 100))

            final_score = (s1 * WEIGHT_EARNINGS) + (s2 * WEIGHT_MACRO) + (s3 * WEIGHT_INSTITUTIONAL)
            stock["total_score"] = round(final_score, 2)

        # 점수 기준 내림차순 정렬
        sorted_stocks = sorted(stocks, key=lambda x: x["total_score"], reverse=True)
        return sorted_stocks

    def update_signals(self, top_stocks: List[Dict]):
        """DB 트랜잭션 처리: 기존 시그널 만료 및 신규 시그널 삽입"""
        logger.info("데이터베이스 트랜잭션 시작: 시그널 업데이트...")
        try:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                
                # 1. 기존 PENDING 상태의 시그널을 EXPIRED로 변경 (만료 처리)
                cursor.execute("UPDATE trade_signals SET status = 'EXPIRED' WHERE status = 'PENDING'")
                expired_count = cursor.rowcount
                if expired_count > 0:
                    logger.info(f"기존 미체결 시그널 {expired_count}건 만료 처리 완료.")

                # 2. 신규 Top 5 종목 삽입
                for stock in top_stocks:
                    reason = f"중장기 팩터 스코어 상위 5종목 (점수: {stock['total_score']})"
                    cursor.execute('''
                        INSERT INTO trade_signals (ticker, name, action, quantity, reason, status)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (stock["ticker"], stock["name"], "BUY", 0, reason, "PENDING"))
                
                conn.commit()
            logger.info("신규 매수 시그널(Top 5) DB 기록 완료.")
        except Exception as e:
            logger.error(f"DB 시그널 업데이트 중 오류 발생: {e}")

    def run(self):
        """전략 실행 메인 프로세스"""
        logger.info("==========================================")
        logger.info("전략 엔진 실행 프로세스 시작")
        
        # 1. 데이터 수집
        market_data = self.fetch_market_data()
        
        # 2. 스코어링 및 종목 선정
        scored_stocks = self.calculate_scores(market_data)
        top_5 = scored_stocks[:5]
        
        logger.info("--- [선정된 Top 5 종목] ---")
        for i, s in enumerate(top_5, 1):
            logger.info(f"{i}위: {s['name']}({s['ticker']}) - 총점: {s['total_score']}")
        
        # 3. DB 연동
        self.update_signals(top_5)
        
        logger.info("전략 엔진 실행 프로세스 완료")
        logger.info("==========================================")

if __name__ == "__main__":
    engine = StrategyEngine()
    engine.run()
