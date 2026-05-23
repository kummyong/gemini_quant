import os
import sqlite3
import random
import logging
import datetime
import pytz
from typing import List, Dict
import requests
from bs4 import BeautifulSoup
import urllib3
import re
import time

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
    SAMPLE_DATA = [
        ("005930", "삼성전자"), ("000660", "SK하이닉스"), ("005380", "현대차"),
        ("035420", "NAVER"), ("035720", "카카오"), ("000270", "기아"),
        ("005490", "POSCO홀딩스"), ("105560", "KB금융"), ("068270", "셀트리온"),
        ("000810", "삼성화재"), ("051910", "LG화학"), ("032830", "삼성생명"),
        ("015760", "한국전력"), ("033780", "KT&G"), ("003550", "LG"),
        ("000100", "유한양행"), ("000700", "유수홀딩스"), ("017940", "E1"),
        ("277810", "레인보우로보틱스"), ("465770", "STX그린로지스")
    ]

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

    def _get_industry_map(self) -> Dict[str, float]:
        """네이버 금융 업종별 시세 페이지에서 업종명과 전일대비 등락률 매핑 수집"""
        url = "https://finance.naver.com/sise/sise_group.naver?type=upjong"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        
        try:
            res = requests.get(url, headers=headers, verify=False, timeout=10)
            res.encoding = 'euc-kr'
            soup = BeautifulSoup(res.text, 'html.parser')
            table = soup.find('table', class_='type_1')
            ind_map = {}
            if table:
                for tr in table.find_all('tr'):
                    tds = tr.find_all('td')
                    if len(tds) >= 2:
                        name = tds[0].get_text(strip=True)
                        change_str = tds[1].get_text(strip=True).replace('%', '')
                        try:
                            change_val = float(change_str)
                            ind_map[name] = change_val
                        except ValueError:
                            pass
            logger.info(f"성공적으로 {len(ind_map)}개의 업종 등락률 정보를 수집했습니다.")
            return ind_map
        except Exception as e:
            logger.error(f"업종 맵 수집 중 오류 발생: {e}")
            return {}

    def fetch_market_data(self) -> List[Dict]:
        """
        실제 종목 코드를 활용해 네이버 금융에서 실시간 실적(EPS), 업종, 수급 데이터를 수집합니다.
        """
        logger.info("실제 시장 데이터 수집 시작...")
        
        sample_data = self.SAMPLE_DATA
        
        # 업종 정보 가져오기
        industry_map = self._get_industry_map()
        
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        
        real_stocks = []
        for ticker, name in sample_data:
            eps_growth = 0.0
            industry_score = 50.0
            net_buying = 0.0
            
            logger.info(f"⏳ 종목 데이터 수집 중: {name}({ticker})")
            
            # 1. Main 페이지 호출 (EPS 성장률, 업종명 조회)
            try:
                main_url = f"https://finance.naver.com/item/main.naver?code={ticker}"
                res = requests.get(main_url, headers=headers, verify=False, timeout=10)
                res.encoding = 'utf-8'
                soup = BeautifulSoup(res.text, 'html.parser')
                
                # 1-1. 업종 정보 추출
                match = re.search(r'업종명\s*:\s*<a[^>]*>([^<]+)</a>', res.text)
                if match:
                    ind_name = match.group(1).strip()
                    change_rate = industry_map.get(ind_name, 0.0)
                    # 등락률 정규화: -5.0% -> 20점, 0% -> 57.5점, +5.0% -> 95점
                    score = 57.5 + change_rate * 7.5
                    industry_score = max(20.0, min(95.0, score))
                
                # 1-2. EPS 성장률 추출
                div = soup.find('div', class_='section cop_analysis')
                if div:
                    table = div.find('table')
                    if table:
                        tr_years = table.find_all('tr')[1]
                        years = [th.get_text(strip=True) for th in tr_years.find_all('th')]
                        
                        eps_row = None
                        for tr in table.find_all('tr'):
                            th_text = tr.find('th')
                            if th_text and 'EPS(원)' in th_text.get_text(strip=True):
                                eps_row = [td.get_text(strip=True).replace(',', '') for td in tr.find_all('td')]
                                break
                                
                        if eps_row and len(years) >= 2 and len(eps_row) >= 2:
                            annual_years = []
                            annual_eps = []
                            for y, eps in zip(years, eps_row):
                                if re.match(r'\d{4}\.\d{2}', y) and '(E)' not in y:
                                    try:
                                        annual_years.append(y)
                                        annual_eps.append(float(eps) if eps and eps != '-' else 0.0)
                                    except ValueError:
                                        pass
                            if len(annual_eps) >= 2:
                                latest_eps = annual_eps[-1]
                                prev_eps = annual_eps[-2]
                                if prev_eps != 0:
                                    eps_growth = ((latest_eps - prev_eps) / abs(prev_eps)) * 100
            except Exception as e:
                logger.error(f"[{name}] 메인 정보 파싱 실패: {e}")
                
            # 2. Investor 페이지 호출 (최근 5일간 수급 순매수 합산 조회)
            try:
                frgn_url = f"https://finance.naver.com/item/frgn.naver?code={ticker}"
                res = requests.get(frgn_url, headers=headers, verify=False, timeout=10)
                res.encoding = 'euc-kr'
                soup = BeautifulSoup(res.text, 'html.parser')
                
                tables = soup.find_all('table', class_='type2')
                target_table = None
                for t in tables:
                    text = t.get_text()
                    if '기관' in text and '외국인' in text:
                        target_table = t
                        break
                        
                if target_table:
                    total_net_buying_krw = 0.0
                    day_count = 0
                    for tr in target_table.find_all('tr'):
                        tds = [td.get_text(strip=True).replace(',', '') for td in tr.find_all('td')]
                        if len(tds) >= 9 and re.match(r'\d{4}\.\d{2}\.\d{2}', tds[0]):
                            try:
                                price = float(tds[1])
                                inst_vol = float(tds[5]) if tds[5] and tds[5] != '-' else 0.0
                                foreign_vol = float(tds[6]) if tds[6] and tds[6] != '-' else 0.0
                                
                                net_vol = inst_vol + foreign_vol
                                net_buying_krw = net_vol * price
                                total_net_buying_krw += net_buying_krw
                                day_count += 1
                                if day_count >= 5:
                                    break
                            except ValueError:
                                pass
                    net_buying = total_net_buying_krw / 100000000.0  # 억 원 단위로 환산
            except Exception as e:
                logger.error(f"[{name}] 수급 정보 파싱 실패: {e}")
                
            real_stocks.append({
                "ticker": ticker,
                "name": name,
                "eps_growth": eps_growth,
                "industry_score": industry_score,
                "net_buying": net_buying
            })
            
            # API 요청 오남용 방지 및 IP 차단 방지를 위한 정중한 대기
            time.sleep(0.5)
            
        return real_stocks

    def calculate_scores(self, stocks: List[Dict]) -> List[Dict]:
        """멀티 팩터 점수 계산 및 랭킹 산출 (동적 Min-Max 정규화 적용)"""
        logger.info("멀티 팩터 스코어 계산 시작...")
        
        if not stocks:
            return []
            
        # 정규화를 위해 최대/최소값 파악
        eps_values = [s["eps_growth"] for s in stocks]
        net_values = [s["net_buying"] for s in stocks]
        
        min_eps, max_eps = min(eps_values), max(eps_values)
        min_net, max_net = min(net_values), max(net_values)
        
        eps_range = max_eps - min_eps if max_eps != min_eps else 1.0
        net_range = max_net - min_net if max_net != min_net else 1.0
        
        for stock in stocks:
            # 1. 실적 모멘텀 동적 정규화 (0 ~ 100점)
            s1 = ((stock["eps_growth"] - min_eps) / eps_range) * 100
            
            # 2. 산업 트렌드는 이미 업종 등락률 기반으로 20~95점으로 매핑되어 있음
            s2 = stock["industry_score"]
            
            # 3. 수급 동적 정규화 (0 ~ 100점)
            s3 = ((stock["net_buying"] - min_net) / net_range) * 100

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
            logger.error(f"보유 종목 API 조회 중 오류: {e}")
            
        # Fallback to local DB portfolio_status if holdings is empty
        if not holdings:
            logger.info("Kiwoom API로부터 계좌 정보를 가져오지 못했거나 보유 종목이 없습니다. 로컬 DB에서 보유 종목을 조회합니다.")
            try:
                with sqlite3.connect(DB_PATH) as conn:
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()
                    # Check if portfolio_status table exists
                    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='portfolio_status'")
                    if cursor.fetchone():
                        cursor.execute("SELECT stk_cd, stk_nm, prft_rt, rmnd_qty FROM portfolio_status WHERE rmnd_qty > 0")
                        rows = cursor.fetchall()
                        for row in rows:
                            holdings.append({
                                "ticker": row["stk_cd"].replace("A", ""),
                                "name": row["stk_nm"],
                                "profit_rate": float(row["prft_rt"]) if row["prft_rt"] is not None else 0.0,
                                "quantity": int(row["rmnd_qty"])
                            })
            except Exception as db_e:
                logger.error(f"로컬 DB portfolio_status 조회 실패: {db_e}")
                
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
