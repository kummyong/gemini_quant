import os
import sqlite3
import random
import logging
import datetime
import pytz
from typing import List, Dict

# [설정] 경로 및 하이퍼파라미터
# 사용자 환경에 맞춰 조정 가능한 기본 경로 설정
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
DB_PATH = os.path.join(LOG_DIR, "system_monitor.db")
LOG_PATH = os.path.join(LOG_DIR, "strategy_engine.log")

# 가중치 설정 (합계 1.0)
WEIGHT_EARNINGS = 0.4      # 실적 모멘텀
WEIGHT_MACRO = 0.3         # 산업 트렌드/매크로
WEIGHT_INSTITUTIONAL = 0.3  # 수급(기관/외인)

# 로깅 설정 (KST 시간대 적용)
def kst_converter(*args):
    return datetime.datetime.now(pytz.timezone('Asia/Seoul')).timetuple()

logging.Formatter.converter = kst_converter

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
                # 기존 스키마 유지: ticker가 PRIMARY KEY, created_at 사용
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS trade_signals (
                        ticker TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        action TEXT NOT NULL,
                        quantity INTEGER DEFAULT 0,
                        reason TEXT,
                        status TEXT DEFAULT 'PENDING',
                        created_at DATETIME DEFAULT (datetime('now', 'localtime'))
                    )
                ''')
                conn.commit()
            logger.info(f"데이터베이스 연결 완료: {DB_PATH}")
        except Exception as e:
            logger.error(f"DB 초기화 중 오류 발생: {e}")

    def fetch_market_data(self) -> List[Dict]:
        """
        [Mock] 실제 종목 코드를 포함한 데이터 생성
        """
        logger.info("시장 데이터(Mock) 수집 중...")
        # 실제 종목명과 코드 매핑
        sample_data = [
            ("005930", "삼성전자"), ("000660", "SK하이닉스"), ("005380", "현대차"),
            ("035420", "NAVER"), ("035720", "카카오"), ("000270", "기아"),
            ("005490", "POSCO홀딩스"), ("105560", "KB금융"), ("068270", "셀트리온"),
            ("000810", "삼성화재"), ("051910", "LG화학"), ("032830", "삼성생명"),
            ("015760", "한국전력"), ("033780", "KT&G"), ("003550", "LG"),
            ("000100", "유한양행"), ("000700", "유수홀딩스"), ("017940", "E1"),
            ("277810", "레인보우로보틱스"), ("465770", "STX그린로지스")
        ]

        mock_stocks = []
        for ticker, name in sample_data:
            eps_growth = random.uniform(-20, 100)
            industry_score = random.uniform(20, 95)
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

    def fetch_current_holdings(self) -> List[Dict]:
        """DB 또는 API를 통해 현재 보유 종목 및 수익률 가져오기"""
        holdings = []
        try:
            # KiwoomApiCore를 임포트하여 직접 계좌 정보를 가져옴
            from kiwoom_api_core import KiwoomApiCore
            api = KiwoomApiCore(mode="MOCK")
            res = api.get_account_summary()
            
            if res and "acnt_evlt_remn_indv_tot" in res:
                for item in res["acnt_evlt_remn_indv_tot"]:
                    holdings.append({
                        "ticker": item["stk_cd"].replace("A", ""),
                        "name": item["stk_nm"],
                        "profit_rate": float(item["prft_rt"]),
                        "quantity": int(item["rmnd_qty"])
                    })
        except Exception as e:
            logger.error(f"보유 종목 조회 중 오류: {e}")
        return holdings

    def generate_management_signals(self, current_holdings: List[Dict], top_tickers: List[str]):
        """매도 시그널 생성 로직"""
        sell_signals = []
        
        for stock in current_holdings:
            ticker = stock["ticker"]
            profit = stock["profit_rate"]
            
            # 1. 익절 (+10% 이상)
            if profit >= 10.0:
                sell_signals.append({
                    "ticker": ticker, "name": stock["name"], "action": "SELL",
                    "reason": f"익절 달성 (수익률: {profit}%)"
                })
            # 2. 손절 (-5% 이하)
            elif profit <= -5.0:
                sell_signals.append({
                    "ticker": ticker, "name": stock["name"], "action": "SELL",
                    "reason": f"손절 가이드 준수 (수익률: {profit}%)"
                })
            # 3. 교체 매매 (전략 순위 밖 & 수익률 저조)
            elif ticker not in top_tickers and profit < 2.0:
                sell_signals.append({
                    "ticker": ticker, "name": stock["name"], "action": "SELL",
                    "reason": f"전략 제외 및 저효율 종목 교체 (수익률: {profit}%)"
                })
        
        return sell_signals

    def update_signals(self, buy_signals: List[Dict], sell_signals: List[Dict]):
        """DB 트랜잭션 처리: 매도 시그널 우선 처리 후 매수 시그널 삽입"""
        logger.info(f"데이터베이스 업데이트: 매도 {len(sell_signals)}건, 매수 {len(buy_signals)}건")
        try:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                
                # 기존 PENDING 시그널 만료
                cursor.execute("UPDATE trade_signals SET status = 'EXPIRED' WHERE status = 'PENDING'")

                # 매도 시그널 삽입 (최우선)
                for s in sell_signals:
                    cursor.execute('''
                        INSERT OR REPLACE INTO trade_signals (ticker, name, action, quantity, reason, status, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
                    ''', (s["ticker"], s["name"], "SELL", 0, s["reason"], "PENDING"))

                # 매수 시그널 삽입
                for b in buy_signals:
                    cursor.execute('''
                        INSERT OR REPLACE INTO trade_signals (ticker, name, action, quantity, reason, status, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
                    ''', (b["ticker"], b["name"], "BUY", 0, b["reason"], "PENDING"))
                
                conn.commit()
            logger.info("모든 매매 시그널이 DB에 성공적으로 반영되었습니다.")
        except Exception as e:
            logger.error(f"시그널 업데이트 중 오류: {e}")

    def run(self):
        """전략 실행 및 포트폴리오 관리 메인 프로세스"""
        logger.info("==========================================")
        logger.info("포트폴리오 관리 및 전략 엔진 가동")
        
        # 1. 신규 전략 종목 선정
        market_data = self.fetch_market_data()
        scored_stocks = self.calculate_scores(market_data)
        top_5 = scored_stocks[:5]
        top_tickers = [s["ticker"] for s in top_5]
        
        # 2. 현재 포트폴리오 분석
        holdings = self.fetch_current_holdings()
        
        # 3. 매도/매수 시그널 생성
        sell_signals = self.generate_management_signals(holdings, top_tickers)
        
        buy_signals = []
        for s in top_5:
            # 이미 보유 중인 종목은 제외 (추가 매수 비활성화 시)
            if s["ticker"] not in [h["ticker"] for h in holdings]:
                buy_signals.append({
                    "ticker": s["ticker"], "name": s["name"], "action": "BUY",
                    "reason": f"전략 상위 종목 신규 진입 (점수: {s['total_score']})"
                })

        # 4. DB 반영
        self.update_signals(buy_signals, sell_signals)
        
        logger.info("엔진 실행 완료")
        logger.info("==========================================")

if __name__ == "__main__":
    engine = StrategyEngine()
    engine.run()
