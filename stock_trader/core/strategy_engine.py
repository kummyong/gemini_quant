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
from stock_trader.communication.telegram_utils import send_telegram_message
from stock_trader.data.dart_api import DartAPI
from stock_trader.data.dart_financial_scorer import DartFinancialScorer
from stock_trader.data.db_repository import DbRepository
from stock_trader.communication.ipc_messenger import IpcPublisher
from stock_trader.broker.broker_factory import BrokerFactory
from stock_trader.config import STOCK_TRADER_DIR as BASE_DIR, LOG_DIR, DB_PATH
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
    # 자산 배분 설정 (실제 계좌 잔고 기반으로 증권사별 동적 로드)
    BROKER_EQUITY = {}            # 증권사별 실제 계좌 총자산
    BROKER_CASH = {}              # 증권사별 실제 예수금
    TARGET_WEIGHT = 0.10          # 포트폴리오 기준 종목당 평균 목표 비중 (10%)
    MAX_SINGLE_ORDER_RATIO = 0.20 # 1회 주문 최대 비율 (예수금의 20%)
    
    # 기본값 설정 (DB 로드 실패 시 예비용)
    RSI_BUY_THRES = 30.0
    RSI_SELL_THRES = 70.0
    BB_STD = 2.0
    TRAILING_STOP_DROP = 3.0
    HARD_STOP_LOSS = -5.0

    from stock_trader.core.stock_universe import SAMPLE_TICKERS
    SAMPLE_DATA = SAMPLE_TICKERS

    def __init__(self, db_repository: DbRepository = None, ipc_publisher: IpcPublisher = None):
        logger.info("중장기 전략 엔진(Strategy Engine) 초기화 중 (다중 증권사 지원)...")
        self.repo = db_repository
        self.publisher = ipc_publisher
        self.is_system_locked = False
        self.lock_reason = ""
        self._init_session()
        self._init_dart()
        self._load_hyperparams()

    def _fetch_account_equity(self):
        """모든 활성 증권사 API에서 실제 총자산과 예수금을 개별 조회하여 동적으로 설정합니다."""
        self.BROKER_EQUITY = {}
        self.BROKER_CASH = {}
        
        active_brokers = BrokerFactory.get_active_brokers()
        for b_name in active_brokers:
            try:
                broker = BrokerFactory.get_broker(b_name)
                res = broker.get_account_summary()
                if res:
                    total_assets = float(res.get("tot_evlt_amt", 0)) + float(res.get("prsm_dpst_aset_amt", 0))
                    cash = float(res.get("prsm_dpst_aset_amt", 0))
                    if total_assets > 0:
                        self.BROKER_EQUITY[b_name] = total_assets
                        self.BROKER_CASH[b_name] = cash
                        logger.info(f"💰 [{b_name}] 총자산: {total_assets:,.0f}원, 예수금: {cash:,.0f}원")
                        continue
            except Exception as e:
                logger.error(f"❌ {b_name} 계좌 잔고 API 조회 실패: {e}")
            
            # DB 폴백 (API 실패 시)
            try:
                if self.repo:
                    import sqlite3
                    with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
                        row = conn.execute("SELECT total_assets, cash FROM account_summary ORDER BY id DESC LIMIT 1").fetchone()
                        if row:
                            self.BROKER_EQUITY[b_name] = float(row[0])
                            self.BROKER_CASH[b_name] = float(row[1])
                            logger.info(f"💰 [{b_name}] (DB 폴백) 총자산: {self.BROKER_EQUITY[b_name]:,.0f}원, 예수금: {self.BROKER_CASH[b_name]:,.0f}원")
                            continue
            except Exception as e:
                logger.error(f"❌ {b_name} DB 폴백 잔고 조회 실패: {e}")
            
            # 최종 폴백
            logger.warning(f"⚠️ {b_name} 계좌 잔고를 확인할 수 없어 자금을 0으로 설정합니다.")
            self.BROKER_EQUITY[b_name] = 0.0
            self.BROKER_CASH[b_name] = 0.0

    def _load_hyperparams(self):
        """DB에서 최신 하이퍼파라미터를 로드하여 매매 기준으로 사용합니다"""
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
            if self.repo:
                db_params = self.repo.get_strategy_hyperparams()
                for k, v in db_params.items():
                    if k in params:
                        params[k] = float(v)
            logger.info(f"✅ DB에서 파라미터 로드 완료: {params}")
        except Exception as e:
            logger.error(f"⚠️ DB 파라미터 로드 실패 (기존 설정값 유지): {e}")
        
        self.BULL_RSI = params["BULL_RSI"]
        self.BULL_BB = params["BULL_BB"]
        self.BEAR_RSI = params["BEAR_RSI"]
        self.BEAR_BB = params["BEAR_BB"]
        
        # 임시 기본값
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
        """
        logger.info("🛡️ 시장 서킷 브레이커(패닉 셀 감지) 검사 중...")
        try:
            import FinanceDataReader as fdr
            df_kospi = fdr.DataReader('KS11')
            if df_kospi.empty or len(df_kospi) < 2:
                return True
                
            current_close = float(df_kospi['Close'].iloc[-1])
            prev_close = float(df_kospi['Close'].iloc[-2])
            
            kospi_daily_change = ((current_close - prev_close) / prev_close) * 100
            
            if kospi_daily_change <= -3.0:
                self.is_system_locked = True
                self.lock_reason = f"코스피 당일 폭락 급류 발생 ({kospi_daily_change:.2f}%)"
                logger.warning(f"🚨 [CRITICAL LOCK] 시스템 서킷 브레이커 발동! 이유: {self.lock_reason}. 모든 신규 매수를 금지합니다.")
                return False
                
            self.is_system_locked = False
            self.lock_reason = ""
            logger.info(f"✅ 서킷 브레이커 통과 (코스피 변동률: {kospi_daily_change:+.2f}%)")
            return True
        except Exception as e:
            logger.error(f"❌ 서킷 브레이커 체크 중 에러 발생: {e}")
            return True

    def _init_dart(self):
        try:
            self.dart_api = DartAPI()
            self.dart_scorer = DartFinancialScorer(self.dart_api)
            logger.info("✅ DART API 및 재무 분석기 초기화 완료")
        except Exception as e:
            logger.error(f"⚠️ DART 초기화 실패: {e}")
            self.dart_api = None
            self.dart_scorer = None

    def _init_session(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })
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
        url = "https://finance.naver.com/sise/sise_group.naver?type=upjong"
        try:
            res = self.session.get(url, verify=False, timeout=10)
            res.encoding = 'euc-kr'
            soup = BeautifulSoup(res.text, 'lxml')
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
            return ind_map
        except Exception as e:
            logger.error(f"업종 맵 수집 중 오류 발생: {e}")
            return {}

    def fetch_market_data(self) -> List[Dict]:
        logger.info("실제 시장 데이터 수집 시작...")
        sample_data = self.SAMPLE_DATA
        industry_map = self._get_industry_map()
        
        real_stocks = []
        for ticker, name in sample_data:
            eps_growth = 0.0
            industry_score = 50.0
            net_buying = 0.0
            current_price = 0.0
            
            try:
                main_url = f"https://finance.naver.com/item/main.naver?code={ticker}"
                res = self.session.get(main_url, verify=False, timeout=10)
                res.encoding = 'utf-8'
                soup = BeautifulSoup(res.text, 'lxml')
                
                match = re.search(r'업종명\s*:\s*<a[^>]*>([^<]+)</a>', res.text)
                if match:
                    ind_name = match.group(1).strip()
                    change_rate = industry_map.get(ind_name, 0.0)
                    score = 57.5 + change_rate * 7.5
                    industry_score = max(20.0, min(95.0, score))
                
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
                            annual_eps = []
                            for y, eps in zip(years, eps_row):
                                if re.match(r'\d{4}\.\d{2}', y) and '(E)' not in y:
                                    try:
                                        annual_eps.append(float(eps) if eps and eps != '-' else 0.0)
                                    except ValueError:
                                        pass
                            if len(annual_eps) >= 2:
                                latest_eps = annual_eps[-1]
                                prev_eps = annual_eps[-2]
                                if prev_eps != 0:
                                    eps_growth = ((latest_eps - prev_eps) / abs(prev_eps)) * 100
                
                now_val_div = soup.find('p', class_='no_today')
                if now_val_div:
                    blind_span = now_val_div.find('span', class_='blind')
                    if blind_span:
                        current_price = float(blind_span.get_text(strip=True).replace(',', ''))
            except Exception as e:
                logger.error(f"[{name}] 메인 정보 파싱 실패: {e}")
                
            try:
                frgn_url = f"https://finance.naver.com/item/frgn.naver?code={ticker}"
                res = self.session.get(frgn_url, verify=False, timeout=10)
                res.encoding = 'euc-kr'
                soup = BeautifulSoup(res.text, 'lxml')
                tables = soup.find_all('table', class_='type2')
                target_table = None
                for t in tables:
                    if '기관' in t.get_text() and '외국인' in t.get_text():
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
                                    current_price = price_val
                                inst_vol = float(tds[5]) if tds[5] and tds[5] != '-' else 0.0
                                foreign_vol = float(tds[6]) if tds[6] and tds[6] != '-' else 0.0
                                total_net_buying_krw += (inst_vol + foreign_vol) * price_val
                                day_count += 1
                                if day_count >= 5:
                                    break
                            except ValueError:
                                pass
                    net_buying = total_net_buying_krw / 100000000.0
            except Exception as e:
                logger.error(f"[{name}] 수급 정보 파싱 실패: {e}")
                
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
                    time.sleep(0.3)
                except Exception as dart_e:
                    logger.warning(f"[{name}] DART 데이터 조회 실패 (무시): {dart_e}")
            
            rsi_val = 50.0
            lower_band = 0.0
            upper_band = 0.0
            try:
                import FinanceDataReader as fdr
                df_hist = fdr.DataReader(ticker, start=(datetime.datetime.now() - datetime.timedelta(days=90)).strftime('%Y-%m-%d'))
                if not df_hist.empty and len(df_hist) >= 20:
                    closes = df_hist['Close']
                    delta = closes.diff()
                    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
                    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
                    rs = gain / (loss + 1e-9)
                    rsi_val = float((100 - (100 / (1 + rs))).iloc[-1])
                    ma20 = closes.rolling(window=20).mean()
                    std20 = closes.rolling(window=20).std()
                    lower_band = float((ma20 - self.BB_STD * std20).iloc[-1])
                    upper_band = float((ma20 + self.BB_STD * std20).iloc[-1])
            except Exception as tech_e:
                logger.warning(f"[{name}] 기술 지표 계산 실패: {tech_e}")

            real_stocks.append({
                "ticker": ticker,
                "name": name,
                "eps_growth": eps_growth,
                "industry_score": industry_score,
                "net_buying": net_buying,
                "price": current_price,
                "dart_revenue_growth": dart_revenue_growth,
                "dart_op_growth": dart_op_growth,
                "dart_debt_ratio": dart_debt_ratio,
                "dart_cf_quality": dart_cf_quality,
                "dart_dividend_yield": dart_dividend_yield,
                "dart_major_shareholder_bonus": dart_major_shareholder_bonus,
                "dart_available": dart_available,
                "rsi": rsi_val,
                "lower_band": lower_band,
                "upper_band": upper_band
            })
            time.sleep(0.5)
            
        return real_stocks

    def calculate_scores(self, stocks: List[Dict]) -> List[Dict]:
        if not stocks: return []
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
            s_eps = ((stock["eps_growth"] - min_eps) / eps_range) * 100
            s_macro = stock["industry_score"]
            s_rev = ((stock.get("dart_revenue_growth", 0.0) - min_rev) / rev_range) * 100
            s_op = ((stock.get("dart_op_growth", 0.0) - min_op) / op_range) * 100
            debt_score = max(0, 100 - stock.get("dart_debt_ratio", 100.0))
            health_score = (debt_score * 0.5 + stock.get("dart_cf_quality", 50.0) * 0.5)
            s_net = ((stock["net_buying"] - min_net) / net_range) * 100
            
            final_score = (
                s_eps * WEIGHT_EARNINGS +
                s_macro * WEIGHT_MACRO +
                s_rev * WEIGHT_DART_REVENUE +
                s_op * WEIGHT_DART_OP_PROFIT +
                health_score * WEIGHT_DART_HEALTH +
                s_net * WEIGHT_INSTITUTIONAL
            )
            
            final_score += stock.get("dart_major_shareholder_bonus", 0.0)
            div_yield = stock.get("dart_dividend_yield", 0.0)
            if div_yield >= 3.0:
                final_score += min(8.0, div_yield * 2.0)
            if stock.get("dart_debt_ratio", 100.0) > 200:
                final_score -= 10.0
            
            stock["total_score"] = round(final_score, 2)
        
        return sorted(stocks, key=lambda x: x["total_score"], reverse=True)

    def fetch_current_holdings(self) -> List[Dict]:
        """활성화된 모든 증권사 API 또는 DB를 통해 현재 보유 종목 및 수익률 가져오기 (max_profit_rate 동적 추적 및 업데이트)"""
        holdings = []
        active_brokers = BrokerFactory.get_active_brokers()
        
        for b_name in active_brokers:
            api_success = False
            b_holdings = []
            try:
                broker = BrokerFactory.get_broker(b_name)
                res = broker.get_account_summary()
                
                if res and "acnt_evlt_remn_indv_tot" in res:
                    for item in res["acnt_evlt_remn_indv_tot"]:
                        b_holdings.append({
                            "broker_id": b_name,
                            "ticker": item["stk_cd"].replace("A", ""),
                            "name": item["stk_nm"],
                            "profit_rate": float(item["prft_rt"]),
                            "quantity": int(item["rmnd_qty"]),
                            "purchase_price": float(item.get("pchs_amt", 0.0)) / max(1, int(item["rmnd_qty"])),
                            "current_price": float(item.get("evlt_amt", 0.0)) / max(1, int(item["rmnd_qty"]))
                        })
                    api_success = True
                    logger.info(f"{b_name} API로부터 {len(b_holdings)}개의 보유 종목을 조회했습니다.")
            except Exception as e:
                logger.error(f"{b_name} 보유 종목 API 조회 중 오류: {e}")

            if not api_success:
                logger.info(f"{b_name} API 조회 실패로 로컬 DB에서 보유 종목을 로드합니다.")
                try:
                    if self.repo:
                        db_all = self.repo.get_portfolio_holdings()
                        for row in db_all:
                            if row.get("broker_id") == b_name:
                                b_holdings.append({
                                    "broker_id": b_name,
                                    "ticker": row["stk_cd"].replace("A", ""),
                                    "name": row["stk_nm"],
                                    "profit_rate": float(row["prft_rt"]) if row["prft_rt"] is not None else 0.0,
                                    "quantity": int(row["rmnd_qty"]),
                                    "purchase_price": float(row["pur_pric"]) if row["pur_pric"] is not None else 0.0,
                                    "current_price": float(row["cur_prc"]) if row["cur_prc"] is not None else 0.0,
                                    "max_profit_rate": float(row["max_profit_rate"]) if row["max_profit_rate"] is not None else 0.0
                                })
                except Exception as db_e:
                    logger.error(f"{b_name} 로컬 DB 로드 실패: {db_e}")

            # Sync with DB only when API fails; if API succeeds we trust its data for this run.
            if not api_success:
                try:
                    if self.repo:
                        db_all = self.repo.get_portfolio_holdings()
                        for stock in b_holdings:
                            ticker = stock["ticker"]
                            current_profit = stock["profit_rate"]
                            
                            stored_max_profit = 0.0
                            for row in db_all:
                                if row.get("broker_id") == b_name and row["stk_cd"] == ticker:
                                    stored_max_profit = float(row["max_profit_rate"]) if row["max_profit_rate"] is not None else 0.0
                                    break
                            
                            if current_profit > stored_max_profit:
                                new_max_profit = current_profit
                                logger.info(f"📈 [{b_name}][{stock['name']}] 고점 수익률 갱신: {stored_max_profit:.2f}% -> {new_max_profit:.2f}%")
                            else:
                                new_max_profit = stored_max_profit
                            
                            stock["max_profit_rate"] = new_max_profit
                            
                            self.repo.update_portfolio_holding(
                                stk_cd=ticker,
                                stk_nm=stock["name"],
                                rmnd_qty=stock["quantity"],
                                pur_pric=stock.get("purchase_price", 0.0),
                                cur_prc=stock.get("current_price", 0.0),
                                prft_rt=current_profit,
                                max_profit_rate=new_max_profit,
                                broker_id=b_name
                            )
                except Exception as db_e:
                    logger.error(f"{b_name} 보유 종목 DB 동기화/갱신 중 오류 발생: {db_e}")

            holdings.extend(b_holdings)
        return holdings
# Ensure broker_id is attached to every sell signal for multi-broker support
    def generate_management_signals(self, current_holdings: List[Dict], top_tickers: List[str], market_data: List[Dict] = None):
        """Trailing Stop 및 절대 손절선(Hard Stop Loss) 기반 매도 시그널 생성 + RSI/BB 오버슈팅 매도 (Phase 1)"""
        sell_signals = []
        if not current_holdings: return sell_signals
        
        market_map = {}
        if market_data:
            market_map = {s["ticker"]: s for s in market_data}
            
        total_purchase_amt = 0.0
        total_eval_amt = 0.0
        for stock in current_holdings:
            qty = stock["quantity"]
            total_purchase_amt += stock.get("purchase_price", 0.0) * qty
            total_eval_amt += stock.get("current_price", 0.0) * qty
            
        total_portfolio_profit_rate = 0.0
        if total_purchase_amt > 0:
            total_portfolio_profit_rate = ((total_eval_amt - total_purchase_amt) / total_purchase_amt) * 100
            
        if total_purchase_amt > 0 and total_portfolio_profit_rate <= self.HARD_STOP_LOSS:
            logger.critical(f"🚨 [글로벌 리스크] 계좌 전체 손실 한도 도달! (전체 수익률: {total_portfolio_profit_rate:.2f}%). 전량 청산(Hard Stop)을 단행합니다.")
            for stock in current_holdings:
                sell_signals.append({
                    "broker_id": stock.get("broker_id", "KIWOOM"),
                    "ticker": stock["ticker"],
                    "name": stock["name"],
                    "action": "SELL",
                    "quantity": stock["quantity"],
                    "reason": f"계좌 전체 Hard Stop Loss 작동 (전체 수익률: {total_portfolio_profit_rate:.2f}%, 기준: {self.HARD_STOP_LOSS}%)"
                })
            try:
                if self.publisher:
                    self.publisher.send_message_sync("GLOBAL_STOP_LOSS", {
                        "profit_rate": total_portfolio_profit_rate,
                        "threshold": self.HARD_STOP_LOSS
                    })
            except Exception as tg_e:
                logger.warning(f"글로벌 손절 IPC 알림 전송 실패: {tg_e}")
            return sell_signals

        for stock in current_holdings:
            ticker = stock["ticker"]
            profit = stock["profit_rate"]
            max_profit = stock.get("max_profit_rate", 0.0)
            quantity = stock["quantity"]
            current_price = stock.get("current_price", 0.0)
            b_id = stock.get("broker_id", "KIWOOM")
            
            s_data = market_map.get(ticker, {})
            rsi_val = s_data.get("rsi", 50.0)
            upper_band = s_data.get("upper_band", 999999999.0)
            
            if profit <= self.HARD_STOP_LOSS:
                sell_signals.append({
                    "broker_id": b_id,
                    "ticker": ticker,
                    "name": stock["name"],
                    "action": "SELL",
                    "quantity": quantity,
                    "reason": f"개별 종목 Hard Stop Loss (수익률: {profit:.2f}%, 기준: {self.HARD_STOP_LOSS}%)"
                })
                logger.warning(f"⚠️ [{b_id}][개별 손절] {stock['name']} 절대 손절선 도달로 전량 청산 (수익률: {profit:.2f}%)")
            elif max_profit >= 2.0 and (max_profit - profit) >= self.TRAILING_STOP_DROP:
                sell_signals.append({
                    "broker_id": b_id,
                    "ticker": ticker,
                    "name": stock["name"],
                    "action": "SELL",
                    "quantity": quantity,
                    "reason": f"Trailing Stop 작동 (고점: {max_profit:.2f}%, 현재: {profit:.2f}%, 하락폭: {max_profit-profit:.2f}%p, 기준: {self.TRAILING_STOP_DROP}%p)"
                })
                logger.info(f"🎯 [{b_id}][트레일링스탑] {stock['name']} 수익 확정 전량 청산 (고점: {max_profit:.2f}%, 현재: {profit:.2f}%)")
            elif rsi_val >= self.RSI_SELL_THRES or current_price >= upper_band:
                sell_signals.append({
                    "broker_id": b_id,
                    "ticker": ticker,
                    "name": stock["name"],
                    "action": "SELL",
                    "quantity": quantity,
                    "reason": f"오버슈팅 청산 (RSI: {rsi_val:.1f}, BB상단: {upper_band:,.0f}원, 현재가: {current_price:,.0f}원)"
                })
                logger.info(f"📈 [{b_id}][오버슈팅 익절] {stock['name']} RSI {rsi_val:.1f} / BB 상단 도달로 청산")
            elif ticker not in top_tickers and profit < 2.0:
                sell_signals.append({
                    "broker_id": b_id,
                    "ticker": ticker,
                    "name": stock["name"],
                    "action": "SELL",
                    "quantity": quantity,
                    "reason": f"전략 제외 및 저효율 종목 교체 (수익률: {profit:.2f}%)"
                })
                logger.info(f"🔄 [{b_id}][교체 매도] {stock['name']} 순위 제외 및 저효율 교체 매도 (수익률: {profit:.2f}%)")
                
        return sell_signals

    def update_signals(self, buy_signals: List[Dict], sell_signals: List[Dict]):
        logger.info(f"데이터베이스 업데이트: 매도 {len(sell_signals)}건, 매수 {len(buy_signals)}건")
        try:
            if self.repo:
                self.repo.expire_pending_signals()
                
            for s in sell_signals:
                b_id = s.get("broker_id", "KIWOOM")
                if self.repo:
                    self.repo.save_trade_signal(
                        ticker=s["ticker"],
                        name=s["name"],
                        action="SELL",
                        quantity=s["quantity"],
                        reason=s["reason"],
                        status="PENDING",
                        broker_id=b_id
                    )
                if self.publisher:
                    self.publisher.send_message_sync("TRADE_SIGNAL", {
                        "broker_id": b_id,
                        "ticker": s["ticker"],
                        "name": s["name"],
                        "action": "SELL",
                        "quantity": s["quantity"],
                        "reason": s["reason"]
                    })
            for b in buy_signals:
                b_id = b.get("broker_id", "KIWOOM")
                if self.repo:
                    self.repo.save_trade_signal(
                        ticker=b["ticker"],
                        name=b["name"],
                        action="BUY",
                        quantity=b["quantity"],
                        reason=b["reason"],
                        status="PENDING",
                        broker_id=b_id
                    )
                if self.publisher:
                    self.publisher.send_message_sync("TRADE_SIGNAL", {
                        "broker_id": b_id,
                        "ticker": b["ticker"],
                        "name": b["name"],
                        "action": "BUY",
                        "quantity": b["quantity"],
                        "reason": b["reason"]
                    })
        except Exception as e:
            logger.error(f"시그널 업데이트 중 오류: {e}")

    def _send_strategy_report(self, scored_stocks, buy_signals, sell_signals, holdings):
        try:
            now = datetime.datetime.now(pytz.timezone('Asia/Seoul')).strftime('%Y-%m-%d %H:%M')
            lines = [f"🤖 *[다중 증권사 전략 엔진 리포트]*", f"⏰ {now}", "━━━━━━━━━━━━━━"]
            
            lines.append("📊 *[오늘의 전략 종목 TOP 5]*")
            for i, s in enumerate(scored_stocks[:5]):
                held = "📌" if s["ticker"] in [h["ticker"] for h in holdings] else "  "
                lines.append(f"{i+1}. {held}{s['name']} ({s['ticker']}) | 점수: {s['total_score']:.1f}")
            lines.append("")
            
            if sell_signals:
                lines.append(f"🔴 *[매도 시그널 {len(sell_signals)}건]*")
                for s in sell_signals:
                    lines.append(f"  · [{s.get('broker_id')}] {s['name']} {s['quantity']:,}주")
            if buy_signals:
                lines.append(f"🟢 *[매수 시그널 {len(buy_signals)}건]*")
                for b in buy_signals:
                    lines.append(f"  · [{b.get('broker_id')}] {b['name']} {b['quantity']:,}주")
            if not sell_signals and not buy_signals:
                lines.append("ℹ️ 금일 신규 매매 시그널 없음")
                
            if holdings:
                lines.append("")
                lines.append(f"📂 *[보유 현황 요약]*")
                for h in holdings:
                    emoji = "📈" if h["profit_rate"] >= 0 else "📉"
                    lines.append(f"  {emoji} [{h.get('broker_id')}] {h['name']}: {h['profit_rate']:+.2f}%")
            
            lines.append("━━━━━━━━━━━━━━")
            msg = "\n".join(lines)
            if self.publisher:
                self.publisher.send_message_sync("STRATEGY_REPORT", {"message": msg})
        except Exception as e:
            logger.error(f"리포트 송신 중 오류: {e}")

    def run(self):
        logger.info("==========================================")
        logger.info("포트폴리오 관리 및 전략 엔진 가동 (다중 증권사)")
        
        # 0. 실제 계좌 잔고 로드 (핵심: 하드코딩 대신 실제 예수금 사용)
        self._fetch_account_equity()
        
        self._determine_market_regime()
        self.check_market_circuit_breaker()
        
        market_data = self.fetch_market_data()
        scored_stocks = self.calculate_scores(market_data)
        top_5 = scored_stocks[:5]
        top_tickers = [s["ticker"] for s in top_5]
        
        holdings = self.fetch_current_holdings()
        sell_signals = self.generate_management_signals(holdings, top_tickers, market_data)
        
        buy_signals = []
        active_brokers = BrokerFactory.get_active_brokers()
        
        # 스코어 기반 차등 분배를 위한 총합 스코어 계산
        total_score_sum = sum(s["total_score"] for s in top_5) if top_5 else 1.0
        
        for b_name in active_brokers:
            remaining_cash = self.BROKER_CASH.get(b_name, 0.0)
            broker_equity = self.BROKER_EQUITY.get(b_name, 0.0)
            
            if remaining_cash <= 0:
                logger.warning(f"⚠️ [{b_name}] 예수금이 0원입니다. 해당 계좌의 매수 신호를 스킵합니다.")
                continue
                
            for s in top_5:
                ticker = s["ticker"]
                name = s["name"]
                
                is_held = any(h["ticker"] == ticker and h["broker_id"] == b_name for h in holdings)
                is_selling = any(sel["ticker"] == ticker and sel.get("broker_id") == b_name for sel in sell_signals)
                
                if is_held or is_selling:
                    continue
                if getattr(self, 'is_system_locked', False):
                    continue
                    
                rsi_val = s.get("rsi", 50.0)
                lower_band = s.get("lower_band", 0.0)
                price = s["price"]
                score = s["total_score"]
                
                is_rsi_match = rsi_val <= self.RSI_BUY_THRES
                is_bb_match = (lower_band > 0) and (price <= lower_band)
                
                if is_rsi_match and is_bb_match:
                    if price > 0 and remaining_cash > 0:
                        # 차등 가중치: (내 점수 / 전체 점수) * (평균목표비중 * 종목수)
                        # 예: 점수가 가장 높으면 10%보다 많은 비중, 낮으면 적은 비중
                        score_ratio = (score / total_score_sum) * len(top_5)
                        adjusted_weight = self.TARGET_WEIGHT * score_ratio
                        
                        target_amt = min(
                            broker_equity * adjusted_weight,
                            self.BROKER_CASH.get(b_name, 0.0) * self.MAX_SINGLE_ORDER_RATIO,
                            remaining_cash
                        )
                        quantity = int(target_amt / price)
                        
                        if quantity <= 0:
                            logger.info(f"⏭️ [{b_name}] {name}: 예수금 대비 단가가 높아 매수 스킵 (단가: {price:,.0f}원, 가용금: {target_amt:,.0f}원)")
                            continue
                        
                        order_cost = quantity * price
                        if order_cost > remaining_cash:
                            quantity = int(remaining_cash / price)
                            order_cost = quantity * price
                        
                        if quantity <= 0:
                            logger.info(f"⏭️ [{b_name}] {name}: 잔여 예수금 부족으로 매수 스킵")
                            continue
                        
                        remaining_cash -= order_cost  # 증권사 예수금 차감
                        
                        buy_signals.append({
                            "broker_id": b_name,
                            "ticker": ticker,
                            "name": name,
                            "action": "BUY",
                            "quantity": quantity,
                            "reason": f"기술적 타점 부합 (RSI: {rsi_val:.1f}, BB하단: {lower_band:,.0f}원) [스코어 차등적용]"
                        })
                        logger.info(f"📋 [{b_name}] 매수 신호 생성: {name} {quantity}주 × {price:,.0f}원 = {order_cost:,.0f}원 (잔여: {remaining_cash:,.0f}원)")
                    else:
                        if remaining_cash <= 0:
                            logger.info(f"⏭️ [{b_name}] {name}: 예수금 소진으로 매수 스킵")
                        else:
                            logger.info(f"⏭️ [{b_name}] {name}: 가격 정보 없음으로 매수 스킵")

        self.update_signals(buy_signals, sell_signals)
        self._send_strategy_report(scored_stocks, buy_signals, sell_signals, holdings)
        logger.info("엔진 실행 완료")
        logger.info("==========================================")

if __name__ == "__main__":
    repo = DbRepository(DB_PATH)
    publisher = IpcPublisher()
    engine = StrategyEngine(db_repository=repo, ipc_publisher=publisher)
    engine.run()
