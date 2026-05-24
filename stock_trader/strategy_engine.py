import os
import sqlite3
import random
import logging
from logging.handlers import RotatingFileHandler
import datetime
import pytz
from typing import List, Dict
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry
from bs4 import BeautifulSoup
import re
import time
from telegram_utils import send_telegram_message
from dart_api import DartAPI
from dart_financial_scorer import DartFinancialScorer

# [설정] 경로 및 하이퍼파라미터
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
DB_PATH = os.path.join(LOG_DIR, "system_monitor.db")
LOG_PATH = os.path.join(LOG_DIR, "strategy_engine.log")

# ── 기존 팩터 가중치 (네이버 금융 기반) ──
WEIGHT_EARNINGS = 0.15       # 기존 EPS 성장률 (네이버 금융)
WEIGHT_MACRO = 0.20          # 산업 트렌드/매크로

# ── DART 팩터 가중치 (공시 재무제표 기반) ──
WEIGHT_DART_REVENUE = 0.15   # DART: 매출 성장률
WEIGHT_DART_OP_PROFIT = 0.20 # DART: 영업이익 성장률
WEIGHT_DART_HEALTH = 0.05    # DART: 재무건전성 (부채비율/현금흐름)
WEIGHT_INSTITUTIONAL = 0.25  # 수급(기관/외인) — 대량보유 보너스 포함

# 합계: 0.15 + 0.20 + 0.15 + 0.20 + 0.05 + 0.25 = 1.00

# 로깅 설정 (KST 시간대 적용 및 RotatingFileHandler 파일 회전 적용)
def kst_converter(*args):
    return datetime.datetime.now(pytz.timezone('Asia/Seoul')).timetuple()

logging.Formatter.converter = kst_converter

if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

# 최대 5MB 크기의 로그 파일을 최대 3개까지 로테이션하여 모바일 기기 저장공간 방어
log_formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
file_handler = RotatingFileHandler(LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8')
file_handler.setFormatter(log_formatter)

stream_handler = logging.StreamHandler()
stream_handler.setFormatter(log_formatter)

logging.basicConfig(
    level=logging.INFO,
    handlers=[file_handler, stream_handler]
)
logger = logging.getLogger("StrategyEngine")

class StrategyEngine:
    # 100억 운용 규모 자산 배분 설정
    TOTAL_EQUITY = 10000000000.0  # 가상의 총 운용 자산 (100억 원)
    TARGET_WEIGHT = 0.10          # 종목당 목표 비중 (10%)
    
    # 기본값 설정 (DB 로드 실패 시 예비용)
    RSI_BUY_THRES = 30.0
    RSI_SELL_THRES = 70.0
    BB_STD = 2.0
    TRAILING_STOP_DROP = 3.0
    HARD_STOP_LOSS = -5.0

    from stock_universe import SAMPLE_TICKERS
    SAMPLE_DATA = SAMPLE_TICKERS

    def __init__(self):
        logger.info("중장기 전략 엔진(Strategy Engine) 초기화 중...")
        self.is_system_locked = False
        self.lock_reason = ""
        self._init_db()
        self._init_session()
        self._init_dart()
        self._load_hyperparams()

    def _load_hyperparams(self):
        """DB에서 최신 하이퍼파라미터를 로드하여 매매 기준으로 사용합니다 (Phase 1)"""
        params = {
            "BULL_RSI": 30.0,
            "BULL_BB": 2.0,
            "BEAR_RSI": 25.0,
            "BEAR_BB": 2.2,
            "RSI_SELL_THRES": 70.0,
            "TRAILING_STOP_DROP": 3.0,
            "HARD_STOP_LOSS": -5.0
        }
        try:
            with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT param_key, param_value FROM strategy_hyperparams")
                rows = cursor.fetchall()
                for row in rows:
                    if row[0] in params:
                        params[row[0]] = float(row[1])
            logger.info(f"✅ DB에서 파라미터 로드 완료: {params}")
        except Exception as e:
            logger.error(f"⚠️ DB 파라미터 로드 실패 (기존 설정값 유지): {e}")
        
        self.BULL_RSI = params["BULL_RSI"]
        self.BULL_BB = params["BULL_BB"]
        self.BEAR_RSI = params["BEAR_RSI"]
        self.BEAR_BB = params["BEAR_BB"]
        
        # 임시 기본값 (이후 _determine_market_regime에서 덮어씀)
        self.RSI_BUY_THRES = self.BULL_RSI
        self.BB_STD = self.BULL_BB

        self.RSI_SELL_THRES = params["RSI_SELL_THRES"]
        self.TRAILING_STOP_DROP = params["TRAILING_STOP_DROP"]
        self.HARD_STOP_LOSS = params["HARD_STOP_LOSS"]

    def _determine_market_regime(self):
        """KOSPI 50일 이동평균선을 기준으로 BULL/BEAR 국면을 판독하고 해당 국면에 맞는 파라미터를 설정합니다."""
        logger.info("📈 실시간 KOSPI(KS11) 국면 판독 시작...")
        try:
            import FinanceDataReader as fdr
            import datetime
            # 최근 90일치 코스피 데이터 로드
            start_date = (datetime.datetime.now() - datetime.timedelta(days=90)).strftime('%Y-%m-%d')
            kospi = fdr.DataReader("KS11", start=start_date)
            
            if not kospi.empty and len(kospi) >= 50:
                kospi["50_MA"] = kospi["Close"].rolling(window=50).mean()
                latest_close = float(kospi["Close"].iloc[-1])
                latest_ma = float(kospi["50_MA"].iloc[-1])
                
                if latest_close > latest_ma:
                    self.current_regime = "BULL"
                    self.RSI_BUY_THRES = self.BULL_RSI
                    self.BB_STD = self.BULL_BB
                else:
                    self.current_regime = "BEAR"
                    self.RSI_BUY_THRES = self.BEAR_RSI
                    self.BB_STD = self.BEAR_BB
                    
                logger.info(f"⚖️ [현재 시장 국면] {self.current_regime} (코스피: {latest_close:,.2f} / 50일선: {latest_ma:,.2f})")
                logger.info(f"👉 [적용 파라미터] RSI 매수선: {self.RSI_BUY_THRES}, BB 하단배수: {self.BB_STD}")
            else:
                raise ValueError("코스피 데이터가 부족합니다.")
        except Exception as e:
            logger.error(f"❌ 국면 판독 실패 (안전장치 발동: 보수적 BEAR 파라미터 적용): {e}")
            self.current_regime = "BEAR (Fallback)"
            self.RSI_BUY_THRES = self.BEAR_RSI
            self.BB_STD = self.BEAR_BB


    def check_market_circuit_breaker(self) -> bool:
        """
        [최상위 안전장치] 시장의 극단적 패닉을 감시하고 결과와 이유를 상세히 로깅합니다.
        반환값: True (정상 가동 가능), False (시스템 셧다운/매수 금지)
        """
        logger.info("🛡️ 시장 서킷 브레이커(패닉 셀 감지) 검사 중...")
        try:
            import FinanceDataReader as fdr
            # 1. 오늘 자 코스피 데이터 확인
            df_kospi = fdr.DataReader('KS11')
            if df_kospi.empty or len(df_kospi) < 2:
                return True # 데이터 오류 시 기본 엔진 가동 보장
                
            current_close = float(df_kospi['Close'].iloc[-1])
            prev_close = float(df_kospi['Close'].iloc[-2])
            
            # 당일 지수 변동률 계산
            kospi_daily_change = ((current_close - prev_close) / prev_close) * 100
            
            # 🚨 비상 제어 조건 1: 코스피 하루 -3% 이상 폭락 시 (패닉 셀링 방지)
            if kospi_daily_change <= -3.0:
                self.is_system_locked = True
                self.lock_reason = f"코스피 당일 폭락 급류 발생 ({kospi_daily_change:.2f}%)"
                logger.warning(f"🚨 [CRITICAL LOCK] 시스템 서킷 브레이커 발동! 이유: {self.lock_reason}. 모든 신규 매수를 금지합니다.")
                return False
                
            # 정상인 경우 락 해제
            self.is_system_locked = False
            self.lock_reason = ""
            logger.info(f"✅ 서킷 브레이커 통과 (코스피 변동률: {kospi_daily_change:+.2f}%)")
            return True
            
        except Exception as e:
            logger.error(f"❌ 서킷 브레이커 체크 중 에러 발생: {e}")
            return True

    def _init_dart(self):
        """DART API 및 재무 점수 계산기 초기화"""
        try:
            self.dart_api = DartAPI()
            self.dart_scorer = DartFinancialScorer(self.dart_api)
            logger.info("✅ DART API 및 재무 분석기 초기화 완료")
        except Exception as e:
            logger.error(f"⚠️ DART 초기화 실패 (기존 네이버 데이터로 대체 운영): {e}")
            self.dart_api = None
            self.dart_scorer = None

    def _init_db(self):
        """데이터베이스 및 테이블 초기화 및 스키마 자동 마이그레이션"""
        try:
            # 다중 프로세스(Watchdog 등)의 동시 쓰기 잠금 방지를 위해 timeout=30.0 설정
            with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
                cursor = conn.cursor()
                
                # 1. trade_signals 테이블 생성
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
                
                # 2. portfolio_status 테이블 생성
                cursor.execute('''
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
                ''')
                
                # 3. portfolio_status에 max_profit_rate 컬럼 존재 여부 파악 및 마이그레이션 수행
                cursor.execute("PRAGMA table_info(portfolio_status)")
                columns = [col[1] for col in cursor.fetchall()]
                if "max_profit_rate" not in columns:
                    logger.info("⚙️ portfolio_status 테이블에 max_profit_rate 컬럼 추가 중 (마이그레이션)...")
                    cursor.execute("ALTER TABLE portfolio_status ADD COLUMN max_profit_rate REAL DEFAULT 0.0")
                
                conn.commit()
            logger.info(f"✅ 데이터베이스 연결 및 스키마 검증 완료: {DB_PATH}")
        except Exception as e:
            logger.error(f"❌ DB 초기화 및 마이그레이션 중 오류 발생: {e}")

    def _init_session(self):
        """requests.Session 커넥션 풀 및 자동 재시도 어댑터 초기화"""
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })
        
        # 임시 네트워크 단절에 대응하기 위해 지수적 백오프 재시도 설정 (3회 재시도)
        retries = Retry(
            total=3,
            backoff_factor=1.0,
            status_forcelist=[500, 502, 503, 504],
            raise_on_status=False,
            allowed_methods=["GET", "POST"]
        )
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)

    def _get_industry_map(self) -> Dict[str, float]:
        """네이버 금융 업종별 시세 페이지에서 업종명과 전일대비 등락률 매핑 수집 (Session & lxml 파싱 최적화)"""
        url = "https://finance.naver.com/sise/sise_group.naver?type=upjong"
        urllib3 = requests.packages.urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        
        try:
            res = self.session.get(url, verify=False, timeout=10)
            res.encoding = 'euc-kr'
            soup = BeautifulSoup(res.text, 'lxml')  # lxml 적용으로 CPU 사용량 감소
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
        """실제 종목 코드를 활용해 네이버 금융에서 실시간 실적(EPS), 업종, 수급, 현재가를 수집합니다."""
        logger.info("실제 시장 데이터 수집 시작...")
        
        sample_data = self.SAMPLE_DATA
        industry_map = self._get_industry_map()
        
        urllib3 = requests.packages.urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        
        real_stocks = []
        for ticker, name in sample_data:
            eps_growth = 0.0
            industry_score = 50.0
            net_buying = 0.0
            current_price = 0.0
            
            logger.info(f"⏳ 종목 데이터 수집 중: {name}({ticker})")
            
            # 1. Main 페이지 호출 (EPS 성장률, 업종명, 실시간 현재가 조회)
            try:
                main_url = f"https://finance.naver.com/item/main.naver?code={ticker}"
                res = self.session.get(main_url, verify=False, timeout=10)
                res.encoding = 'utf-8'
                soup = BeautifulSoup(res.text, 'lxml')
                
                # 1-1. 업종 정보 추출 및 정규화
                match = re.search(r'업종명\s*:\s*<a[^>]*>([^<]+)</a>', res.text)
                if match:
                    ind_name = match.group(1).strip()
                    change_rate = industry_map.get(ind_name, 0.0)
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
                
                # 1-3. 실시간 현재가 추출
                now_val_div = soup.find('p', class_='no_today')
                if now_val_div:
                    blind_span = now_val_div.find('span', class_='blind')
                    if blind_span:
                        current_price = float(blind_span.get_text(strip=True).replace(',', ''))
            except Exception as e:
                logger.error(f"[{name}] 메인 정보 파싱 실패: {e}")
                
            # 2. Investor 페이지 호출 (최근 5일간 수급 순매수 합산 조회)
            try:
                frgn_url = f"https://finance.naver.com/item/frgn.naver?code={ticker}"
                res = self.session.get(frgn_url, verify=False, timeout=10)
                res.encoding = 'euc-kr'
                soup = BeautifulSoup(res.text, 'lxml')
                
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
                                price_val = float(tds[1])
                                if current_price == 0.0 and day_count == 0:
                                    current_price = price_val  # 현재가 추출 실패 시 종가로 백업 적용
                                    
                                inst_vol = float(tds[5]) if tds[5] and tds[5] != '-' else 0.0
                                foreign_vol = float(tds[6]) if tds[6] and tds[6] != '-' else 0.0
                                
                                net_vol = inst_vol + foreign_vol
                                net_buying_krw = net_vol * price_val
                                total_net_buying_krw += net_buying_krw
                                day_count += 1
                                if day_count >= 5:
                                    break
                            except ValueError:
                                pass
                    net_buying = total_net_buying_krw / 100000000.0  # 억 원 단위 환산
            except Exception as e:
                logger.error(f"[{name}] 수급 정보 파싱 실패: {e}")
                
            # ──── DART 데이터 조회 ────
            dart_revenue_growth = 0.0
            dart_op_growth = 0.0
            dart_debt_ratio = 100.0
            dart_cf_quality = 50.0
            dart_dividend_yield = 0.0
            dart_major_shareholder_bonus = 0.0
            dart_available = False
            
            if self.dart_scorer:
                try:
                    fin_score = self.dart_scorer.get_financial_score(ticker)
                    dart_revenue_growth = fin_score["revenue_growth"]
                    dart_op_growth = fin_score["op_profit_growth"]
                    dart_debt_ratio = fin_score["debt_ratio"]
                    dart_cf_quality = fin_score["cash_flow_quality"]
                    dart_available = fin_score["dart_available"]
                    
                    dart_dividend_yield = self.dart_scorer.get_dividend_yield(ticker)
                    dart_major_shareholder_bonus = self.dart_scorer.get_major_shareholder_signal(ticker)
                    
                    # DART API 호출 간 0.3초 대기 (일일 10,000회 한도 준수)
                    time.sleep(0.3)
                except Exception as dart_e:
                    logger.warning(f"[{name}] DART 데이터 조회 실패 (무시): {dart_e}")
            
            # ──── RSI & Bollinger Bands 기술적 지표 계산 (FinanceDataReader 활용) ────
            rsi_val = 50.0
            lower_band = 0.0
            upper_band = 0.0
            
            try:
                import FinanceDataReader as fdr
                # S22 Ultra 발열 제어 및 RAM 보호를 위한 최소 분량(90일) 일봉 데이터만 조회 (경량화 아키텍처)
                df_hist = fdr.DataReader(ticker, start=(datetime.datetime.now() - datetime.timedelta(days=90)).strftime('%Y-%m-%d'))
                if not df_hist.empty and len(df_hist) >= 20:
                    closes = df_hist['Close']
                    # RSI (14)
                    delta = closes.diff()
                    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
                    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
                    rs = gain / (loss + 1e-9)
                    rsi_series = 100 - (100 / (1 + rs))
                    rsi_val = float(rsi_series.iloc[-1])
                    
                    # Bollinger Bands (20, BB_STD)
                    ma20 = closes.rolling(window=20).mean()
                    std20 = closes.rolling(window=20).std()
                    lower_band = float((ma20 - self.BB_STD * std20).iloc[-1])
                    upper_band = float((ma20 + self.BB_STD * std20).iloc[-1])
            except Exception as tech_e:
                logger.warning(f"[{name}] 기술 지표(RSI/BB) 계산 실패 (기본값 대체): {tech_e}")

            real_stocks.append({
                "ticker": ticker,
                "name": name,
                "eps_growth": eps_growth,
                "industry_score": industry_score,
                "net_buying": net_buying,
                "price": current_price,
                # DART 추가 데이터
                "dart_revenue_growth": dart_revenue_growth,
                "dart_op_growth": dart_op_growth,
                "dart_debt_ratio": dart_debt_ratio,
                "dart_cf_quality": dart_cf_quality,
                "dart_dividend_yield": dart_dividend_yield,
                "dart_major_shareholder_bonus": dart_major_shareholder_bonus,
                "dart_available": dart_available,
                # 기술적 지표 데이터 (Phase 1)
                "rsi": rsi_val,
                "lower_band": lower_band,
                "upper_band": upper_band
            })
            
            # API 요청 오남용 방지 및 IP 차단 방지를 위한 0.5초 정중한 대기
            time.sleep(0.5)
            
        return real_stocks

    def calculate_scores(self, stocks: List[Dict]) -> List[Dict]:
        """멀티 팩터 점수 계산 (네이버 + DART 팩터 통합, 동적 Min-Max 정규화)"""
        logger.info("멀티 팩터 스코어 계산 시작 (DART 팩터 통합)...")
        
        if not stocks:
            return []
        
        # ── 정규화를 위한 최대/최소값 파악 ──
        eps_values = [s["eps_growth"] for s in stocks]
        net_values = [s["net_buying"] for s in stocks]
        rev_values = [s.get("dart_revenue_growth", 0.0) for s in stocks]
        op_values = [s.get("dart_op_growth", 0.0) for s in stocks]
        
        min_eps, max_eps = min(eps_values), max(eps_values)
        min_net, max_net = min(net_values), max(net_values)
        min_rev, max_rev = min(rev_values), max(rev_values)
        min_op, max_op = min(op_values), max(op_values)
        
        eps_range = max_eps - min_eps if max_eps != min_eps else 1.0
        net_range = max_net - min_net if max_net != min_net else 1.0
        rev_range = max_rev - min_rev if max_rev != min_rev else 1.0
        op_range = max_op - min_op if max_op != min_op else 1.0
        
        for stock in stocks:
            # 1. 기존 실적 모멘텀 (네이버 EPS) — 정규화 0~100
            s_eps = ((stock["eps_growth"] - min_eps) / eps_range) * 100
            
            # 2. 산업 트렌드 — 이미 20~95점 범위
            s_macro = stock["industry_score"]
            
            # 3. DART 매출 성장률 — 정규화 0~100
            s_rev = ((stock.get("dart_revenue_growth", 0.0) - min_rev) / rev_range) * 100
            
            # 4. DART 영업이익 성장률 — 정규화 0~100
            s_op = ((stock.get("dart_op_growth", 0.0) - min_op) / op_range) * 100
            
            # 5. DART 재무건전성 점수 (부채비율 + 현금흐름 품질 종합)
            #    부채비율이 낮을수록, 현금흐름 품질이 높을수록 점수가 높다
            debt_score = max(0, 100 - stock.get("dart_debt_ratio", 100.0))  # 부채비율 100% → 0점, 0% → 100점
            debt_score = min(100, debt_score)
            health_score = (debt_score * 0.5 + stock.get("dart_cf_quality", 50.0) * 0.5)
            
            # 6. 수급 (기관/외인 순매수) — 정규화 0~100
            s_net = ((stock["net_buying"] - min_net) / net_range) * 100
            
            # ── 최종 점수 산출 ──
            final_score = (
                s_eps * WEIGHT_EARNINGS +
                s_macro * WEIGHT_MACRO +
                s_rev * WEIGHT_DART_REVENUE +
                s_op * WEIGHT_DART_OP_PROFIT +
                health_score * WEIGHT_DART_HEALTH +
                s_net * WEIGHT_INSTITUTIONAL
            )
            
            # ── 보너스/패널티 적용 ──
            
            # 대량보유(5% 룰) 지분변동 보너스 (수급 팩터에 가산)
            final_score += stock.get("dart_major_shareholder_bonus", 0.0)
            
            # 배당수익률 보너스 (3% 이상 시 최대 +8점)
            div_yield = stock.get("dart_dividend_yield", 0.0)
            if div_yield >= 3.0:
                dividend_bonus = min(8.0, div_yield * 2.0)
                final_score += dividend_bonus
            
            # 재무건전성 패널티 (부채비율 200% 이상 시 -10점)
            if stock.get("dart_debt_ratio", 100.0) > 200:
                final_score -= 10.0
                stock["_debt_penalty"] = True
            
            stock["total_score"] = round(final_score, 2)
        
        # 점수 기준 내림차순 정렬
        sorted_stocks = sorted(stocks, key=lambda x: x["total_score"], reverse=True)
        return sorted_stocks

    def fetch_current_holdings(self) -> List[Dict]:
        """DB 또는 API를 통해 현재 보유 종목 및 수익률 가져오기 (max_profit_rate 동적 추적 및 업데이트)"""
        holdings = []
        api_success = False
        
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
                        "quantity": int(item["rmnd_qty"]),
                        "purchase_price": float(item.get("pchs_amt", 0.0)) / max(1, int(item["rmnd_qty"])), # 평단가
                        "current_price": float(item.get("evlt_amt", 0.0)) / max(1, int(item["rmnd_qty"]))  # 현재가
                    })
                api_success = True
                logger.info(f"Kiwoom API로부터 {len(holdings)}개의 보유 종목을 조회했습니다.")
        except Exception as e:
            logger.error(f"보유 종목 API 조회 중 오류 (로컬 DB 백업으로 대체): {e}")

        # 로컬 DB 연결 (timeout 적용)
        try:
            with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                # API 조회 실패 시 로컬 DB에서 불러오기
                if not api_success:
                    logger.info("Kiwoom API 조회 실패로 로컬 DB portfolio_status에서 보유 종목을 로드합니다.")
                    cursor.execute("SELECT stk_cd, stk_nm, prft_rt, rmnd_qty, pur_pric, cur_prc, max_profit_rate FROM portfolio_status WHERE rmnd_qty > 0")
                    rows = cursor.fetchall()
                    for row in rows:
                        holdings.append({
                            "ticker": row["stk_cd"].replace("A", ""),
                            "name": row["stk_nm"],
                            "profit_rate": float(row["prft_rt"]) if row["prft_rt"] is not None else 0.0,
                            "quantity": int(row["rmnd_qty"]),
                            "purchase_price": float(row["pur_pric"]) if row["pur_pric"] is not None else 0.0,
                            "current_price": float(row["cur_prc"]) if row["cur_prc"] is not None else 0.0,
                            "max_profit_rate": float(row["max_profit_rate"]) if row["max_profit_rate"] is not None else 0.0
                        })

                # 각 종목의 max_profit_rate를 추적 및 업데이트
                for stock in holdings:
                    ticker = stock["ticker"]
                    current_profit = stock["profit_rate"]
                    
                    # DB에 저장된 이전 max_profit_rate 조회
                    cursor.execute("SELECT max_profit_rate FROM portfolio_status WHERE stk_cd = ?", (ticker,))
                    row = cursor.fetchone()
                    
                    stored_max_profit = float(row[0]) if row and row[0] is not None else 0.0
                    
                    # 고점 갱신 확인
                    if current_profit > stored_max_profit:
                        new_max_profit = current_profit
                        logger.info(f"📈 [{stock['name']}] 고점 수익률 갱신: {stored_max_profit:.2f}% -> {new_max_profit:.2f}%")
                    else:
                        new_max_profit = stored_max_profit
                    
                    stock["max_profit_rate"] = new_max_profit
                    
                    # DB에 실시간 보유 현황 및 max_profit_rate 업데이트/저장
                    cursor.execute("""
                        INSERT INTO portfolio_status (stk_cd, stk_nm, rmnd_qty, pur_pric, cur_prc, prft_rt, max_profit_rate, last_updated)
                        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
                        ON CONFLICT(stk_cd) DO UPDATE SET
                            rmnd_qty = excluded.rmnd_qty,
                            pur_pric = excluded.pur_pric,
                            cur_prc = excluded.cur_prc,
                            prft_rt = excluded.prft_rt,
                            max_profit_rate = excluded.max_profit_rate,
                            last_updated = excluded.last_updated
                    """, (
                        ticker, stock["name"], stock["quantity"], 
                        stock.get("purchase_price", 0.0), stock.get("current_price", 0.0), 
                        current_profit, new_max_profit
                    ))
                
                conn.commit()
        except Exception as db_e:
            logger.error(f"보유 종목 DB 동기화/갱신 중 오류 발생: {db_e}")

        return holdings

    def generate_management_signals(self, current_holdings: List[Dict], top_tickers: List[str], market_data: List[Dict] = None):
        """Trailing Stop 및 절대 손절선(Hard Stop Loss) 기반 매도 시그널 생성 + RSI/BB 오버슈팅 매도 (Phase 1)"""
        sell_signals = []
        
        if not current_holdings:
            return sell_signals

        # market_data 매핑 생성 {ticker: stock_dict}
        market_map = {}
        if market_data:
            market_map = {s["ticker"]: s for s in market_data}

        # 1. 계좌 전체 생존을 위한 글로벌 손절선 (Account-wide Hard Stop)
        # 전체 보유 종목의 평가 이익/손실 합산 계산
        total_purchase_amt = 0.0
        total_eval_amt = 0.0
        for stock in current_holdings:
            qty = stock["quantity"]
            purchase_price = stock.get("purchase_price", 0.0)
            current_price = stock.get("current_price", 0.0)
            
            total_purchase_amt += purchase_price * qty
            total_eval_amt += current_price * qty
            
        total_portfolio_profit_rate = 0.0
        if total_purchase_amt > 0:
            total_portfolio_profit_rate = ((total_eval_amt - total_purchase_amt) / total_purchase_amt) * 100

        # 계좌 단위 절대 손절 (포트폴리오 전체 수익률이 -5.0% 이하로 내려가면 전체 종목 전량 청산)
        if total_purchase_amt > 0 and total_portfolio_profit_rate <= self.HARD_STOP_LOSS:
            logger.critical(f"🚨 [글로벌 리스크] 계좌 전체 손실 한도 도달! (전체 수익률: {total_portfolio_profit_rate:.2f}%). 전량 청산(Hard Stop)을 단행합니다.")
            for stock in current_holdings:
                sell_signals.append({
                    "ticker": stock["ticker"],
                    "name": stock["name"],
                    "action": "SELL",
                    "quantity": stock["quantity"],  # 전량 매도
                    "reason": f"계좌 전체 Hard Stop Loss 작동 (전체 수익률: {total_portfolio_profit_rate:.2f}%, 기준: {self.HARD_STOP_LOSS}%)"
                })
            
            # 긴급 텔레그램 알림: 글로벌 손절
            try:
                alert_msg = (
                    f"🚨🚨🚨 *[긴급] 글로벌 손절 발동*\n"
                    f"━━━━━━━━━━━━━━\n"
                    f"📉 포트폴리오 전체 수익률: {total_portfolio_profit_rate:.2f}%\n"
                    f"⚠️ 손절선: {self.HARD_STOP_LOSS}%\n"
                    f"🔴 전 보유종목 {len(current_holdings)}건 전량 매도 시그널 생성\n"
                    f"━━━━━━━━━━━━━━"
                )
                send_telegram_message(alert_msg)
            except Exception as tg_e:
                logger.warning(f"글로벌 손절 텔레그램 알림 전송 실패: {tg_e}")
            return sell_signals

        # 2. 개별 종목 리스크 검토
        for stock in current_holdings:
            ticker = stock["ticker"]
            profit = stock["profit_rate"]
            max_profit = stock.get("max_profit_rate", 0.0)
            quantity = stock["quantity"]
            current_price = stock.get("current_price", 0.0)
            
            # market_data로부터 최신 기술적 지표 조회
            s_data = market_map.get(ticker, {})
            rsi_val = s_data.get("rsi", 50.0)
            upper_band = s_data.get("upper_band", 999999999.0)
            
            # 2-1. 개별 종목 Hard Stop Loss (절대 손절선: 예: -5% 이하)
            if profit <= self.HARD_STOP_LOSS:
                sell_signals.append({
                    "ticker": ticker,
                    "name": stock["name"],
                    "action": "SELL",
                    "quantity": quantity,
                    "reason": f"개별 종목 Hard Stop Loss (수익률: {profit:.2f}%, 기준: {self.HARD_STOP_LOSS}%)"
                })
                logger.warning(f"⚠️ [개별 손절] {stock['name']} 절대 손절선 도달로 전량 청산 (수익률: {profit:.2f}%)")
                
            # 2-2. 개별 종목 Trailing Stop (고점 대비 하락 익절선: 고점이 +2.0% 이상이었고 고점 대비 trailing_stop_drop 하락 시)
            elif max_profit >= 2.0 and (max_profit - profit) >= self.TRAILING_STOP_DROP:
                sell_signals.append({
                    "ticker": ticker,
                    "name": stock["name"],
                    "action": "SELL",
                    "quantity": quantity,
                    "reason": f"Trailing Stop 작동 (고점: {max_profit:.2f}%, 현재: {profit:.2f}%, 하락폭: {max_profit-profit:.2f}%p, 기준: {self.TRAILING_STOP_DROP}%p)"
                })
                logger.info(f"🎯 [트레일링스탑] {stock['name']} 수익 확정 전량 청산 (고점: {max_profit:.2f}%, 현재: {profit:.2f}%)")
                
            # 2-3. 개별 종목 RSI 오버슈팅 & BB 상단 도달 (익절/대피 시그널) (Phase 1)
            elif rsi_val >= self.RSI_SELL_THRES or current_price >= upper_band:
                sell_signals.append({
                    "ticker": ticker,
                    "name": stock["name"],
                    "action": "SELL",
                    "quantity": quantity,
                    "reason": f"오버슈팅 청산 (RSI: {rsi_val:.1f}, BB상단: {upper_band:,.0f}원, 현재가: {current_price:,.0f}원)"
                })
                logger.info(f"📈 [오버슈팅 익절] {stock['name']} RSI {rsi_val:.1f} / BB 상단 도달로 청산")

            # 2-4. 리밸런싱 교체 매매 (전략 상위 순위 밖 & 수익률 저조)
            elif ticker not in top_tickers and profit < 2.0:
                sell_signals.append({
                    "ticker": ticker,
                    "name": stock["name"],
                    "action": "SELL",
                    "quantity": quantity,
                    "reason": f"전략 제외 및 저효율 종목 교체 (수익률: {profit:.2f}%)"
                })
                logger.info(f"🔄 [교체 매도] {stock['name']} 순위 제외 및 저효율 교체 매도 (수익률: {profit:.2f}%)")
                
        return sell_signals

    def update_signals(self, buy_signals: List[Dict], sell_signals: List[Dict]):
        """DB 트랜잭션 처리: 매도 시그널 우선 처리 후 매수 시그널 삽입 (timeout=30.0 적용)"""
        logger.info(f"데이터베이스 업데이트: 매도 {len(sell_signals)}건, 매수 {len(buy_signals)}건")
        try:
            with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
                cursor = conn.cursor()
                
                # 기존 PENDING 시그널 만료
                cursor.execute("UPDATE trade_signals SET status = 'EXPIRED' WHERE status = 'PENDING'")

                # 매도 시그널 삽입 (최우선)
                for s in sell_signals:
                    cursor.execute('''
                        INSERT OR REPLACE INTO trade_signals (ticker, name, action, quantity, reason, status, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
                    ''', (s["ticker"], s["name"], "SELL", s["quantity"], s["reason"], "PENDING"))

                # 매수 시그널 삽입
                for b in buy_signals:
                    cursor.execute('''
                        INSERT OR REPLACE INTO trade_signals (ticker, name, action, quantity, reason, status, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
                    ''', (b["ticker"], b["name"], "BUY", b["quantity"], b["reason"], "PENDING"))
                
                conn.commit()
            logger.info("모든 매매 시그널이 DB에 성공적으로 반영되었습니다.")
        except Exception as e:
            logger.error(f"시그널 업데이트 중 오류: {e}")

    def _send_strategy_report(self, scored_stocks, buy_signals, sell_signals, holdings):
        """전략 엔진 실행 결과를 텔레그램으로 전송"""
        try:
            now = datetime.datetime.now(pytz.timezone('Asia/Seoul')).strftime('%Y-%m-%d %H:%M')
            
            # 1. 헤더
            lines = [f"🤖 *[전략 엔진 리포트]*", f"⏰ {now}", "━━━━━━━━━━━━━━"]
            
            # 2. 상위 5종목 랭킹
            lines.append("📊 *[오늘의 전략 종목 TOP 5]*")
            top_5 = scored_stocks[:5]
            for i, s in enumerate(top_5):
                held = "📌" if s["ticker"] in [h["ticker"] for h in holdings] else "  "
                dart_tag = "📋" if s.get("dart_available") else ""
                lines.append(
                    f"{i+1}. {held}{s['name']} ({s['ticker']}) "
                    f"| 점수: {s['total_score']:.1f} {dart_tag}"
                )
                # DART 재무 상세 (조회 성공한 종목만)
                if s.get("dart_available"):
                    lines.append(
                        f"   └ 매출{s.get('dart_revenue_growth', 0):+.1f}% "
                        f"| 영업이익{s.get('dart_op_growth', 0):+.1f}% "
                        f"| 부채{s.get('dart_debt_ratio', 0):.0f}% "
                        f"| 배당{s.get('dart_dividend_yield', 0):.1f}%"
                    )
            lines.append("")
            
            # 3. 매도 시그널
            if sell_signals:
                lines.append(f"🔴 *[매도 시그널 {len(sell_signals)}건]*")
                for s in sell_signals:
                    lines.append(f"  · {s['name']} {s['quantity']:,}주")
                    lines.append(f"    └ {s['reason']}")
                lines.append("")
            
            # 4. 매수 시그널
            if buy_signals:
                lines.append(f"🟢 *[매수 시그널 {len(buy_signals)}건]*")
                for b in buy_signals:
                    lines.append(f"  · {b['name']} {b['quantity']:,}주")
                    lines.append(f"    └ {b['reason']}")
                lines.append("")
            
            # 5. 시그널 없음
            if not sell_signals and not buy_signals:
                lines.append("ℹ️ 금일 신규 매매 시그널 없음 (포트폴리오 유지)")
                lines.append("")
            
            # 6. 현재 보유 현황 요약
            if holdings:
                lines.append(f"📂 *[현재 보유 {len(holdings)}종목]*")
                for h in holdings:
                    emoji = "📈" if h["profit_rate"] >= 0 else "📉"
                    lines.append(
                        f"  {emoji} {h['name']}: {h['profit_rate']:+.2f}%"
                    )
            
            lines.append("━━━━━━━━━━━━━━")
            
            msg = "\n".join(lines)
            send_telegram_message(msg)
            logger.info("전략 리포트 텔레그램 전송 완료")
        except Exception as e:
            logger.error(f"전략 리포트 텔레그램 전송 실패: {e}")

    def run(self):
        """전략 실행 및 포트폴리오 관리 메인 프로세스"""
        logger.info("==========================================")
        logger.info("포트폴리오 관리 및 전략 엔진 가동")
        
        # 0. 실시간 국면 판독 및 매매 파라미터 결정
        self._determine_market_regime()
        
        # 0-1. 시장 서킷 브레이커 작동 확인
        self.check_market_circuit_breaker()
        
        # 1. 신규 전략 종목 선정 및 실시간 데이터 수집
        market_data = self.fetch_market_data()
        scored_stocks = self.calculate_scores(market_data)
        top_5 = scored_stocks[:5]
        top_tickers = [s["ticker"] for s in top_5]
        
        # 2. 현재 포트폴리오 분석 및 고점/수익률 추적
        holdings = self.fetch_current_holdings()
        
        # 3. 매도/매수 시그널 생성 (market_data 전달)
        sell_signals = self.generate_management_signals(holdings, top_tickers, market_data)
        
        # 4. 신규 매수 시그널 포지션 사이징 계산 (RSI / Bollinger Band 하단 바운스 필터링 적용)
        buy_signals = []
        for s in top_5:
            ticker = s["ticker"]
            name = s["name"]
            
            # 이미 보유 중이거나 금일 매도 시그널이 발생한 종목은 매수 제외
            is_held = ticker in [h["ticker"] for h in holdings]
            is_selling = ticker in [sel["ticker"] for sel in sell_signals]
            
            if is_held or is_selling:
                continue
                
            # 가장 먼저 서킷 브레이커 통과 여부 검사
            if getattr(self, 'is_system_locked', False):
                logger.info(f"⏭️ [{name}({ticker})] 매수 검사 생략: 현재 시스템이 셧다운 상태입니다. (사유: {self.lock_reason})")
                continue
            
            rsi_val = s.get("rsi", 50.0)
            lower_band = s.get("lower_band", 0.0)
            price = s["price"]
            
            regime_mode = getattr(self, 'current_regime', 'UNKNOWN')
            
            # 기술적 타점 판별 (AND 조건)
            is_rsi_match = rsi_val <= self.RSI_BUY_THRES
            is_bb_match = (lower_band > 0) and (price <= lower_band)
            pass_technical_filter = is_rsi_match and is_bb_match
            
            if pass_technical_filter:
                logger.info(
                    f"🎯 [타점 포착] 종목: {name}({ticker}) | 국면: {regime_mode} | "
                    f"RSI: {rsi_val:.1f}(기준:{self.RSI_BUY_THRES}) MATCH | "
                    f"현재가: {price:,} <= BB하단: {lower_band:,.0f} MATCH -> 매수 주문 승인"
                )
                if price > 0:
                    # 종목당 배정 금액: 총자산 * 타겟비중
                    target_amt = self.TOTAL_EQUITY * self.TARGET_WEIGHT
                    quantity = int(target_amt / price)
                else:
                    quantity = 0
                    
                buy_signals.append({
                    "ticker": ticker,
                    "name": name,
                    "action": "BUY",
                    "quantity": quantity,
                    "reason": f"전략 진입 & 기술적 타점 부합 (국면: {regime_mode}, RSI: {rsi_val:.1f}, BB하단: {lower_band:,.0f}원, 현재가: {price:,.0f}원)"
                })
            else:
                fail_reason = []
                if not is_rsi_match: fail_reason.append(f"RSI 미달({rsi_val:.1f}>{self.RSI_BUY_THRES})")
                if not is_bb_match: fail_reason.append(f"가격이 BB하단선 위({price:,}>{lower_band:,.0f})")
                
                if rsi_val <= (self.RSI_BUY_THRES + 5):
                    logger.info(f"🔍 [{name}({ticker})] 타점 근접 및 탈락 사유: {', '.join(fail_reason)}")

        # 5. DB 반영
        self.update_signals(buy_signals, sell_signals)
        
        # 6. 전략 결과 텔레그램 알림
        self._send_strategy_report(scored_stocks, buy_signals, sell_signals, holdings)
        
        logger.info("엔진 실행 완료")
        logger.info("==========================================")

if __name__ == "__main__":
    engine = StrategyEngine()
    engine.run()
