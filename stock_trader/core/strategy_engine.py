import os
import json
import pandas as pd
import logging
from logging.handlers import RotatingFileHandler
import datetime
import pytz
from typing import List, Dict
import time
from stock_trader.data.dart_api import DartAPI
from stock_trader.data.dart_financial_scorer import DartFinancialScorer
from stock_trader.data.db_repository import DbRepository, HARD_STOP_LOCKOUT_PREFIX, is_hard_stop_lockout
from stock_trader.data.naver_finance import NaverFinanceProvider
from stock_trader.communication.ipc_messenger import IpcPublisher
from stock_trader.broker.broker_factory import BrokerFactory
import stock_trader.core.indicators as ind
import stock_trader.core.signals as sig
import stock_trader.core.reconciler as reconciler
from stock_trader.core.market_features import compute_technical_features
from stock_trader.core.scorer import (
    MultiFactorScorer,
    apply_sector_momentum,
    apply_sector_flow_score,
    WEIGHT_EARNINGS,
    WEIGHT_MACRO,
    WEIGHT_DART_REVENUE,
    WEIGHT_DART_OP_PROFIT,
    WEIGHT_DART_HEALTH,
    WEIGHT_INSTITUTIONAL,
)
from stock_trader.core.korean_market_calendar import is_first_trading_day_of_month
from stock_trader import config as trader_config
from stock_trader.config import STOCK_TRADER_DIR as BASE_DIR, LOG_DIR, DB_PATH, ACTIVE_STRATEGY_PROFILE, STOCK_MULTIFACTOR, ETF_TREND, ETF_UNIVERSE
LOG_PATH = os.path.join(LOG_DIR, "strategy_engine.log")

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
        
        self.profile = ACTIVE_STRATEGY_PROFILE
        if self.profile == ETF_TREND:
            self.SAMPLE_DATA = ETF_UNIVERSE
            logger.info(f"🛡️ [ETF_TREND] 프로파일 로드됨. 유니버스 크기: {len(self.SAMPLE_DATA)}")
        else:
            if StrategyEngine.SAMPLE_DATA:
                self.SAMPLE_DATA = StrategyEngine.SAMPLE_DATA
            else:
                from stock_trader.core.stock_universe import SAMPLE_TICKERS
                self.SAMPLE_DATA = SAMPLE_TICKERS
            logger.info(f"💼 [STOCK_MULTIFACTOR] 프로파일 로드됨. 유니버스 크기: {len(self.SAMPLE_DATA)}")

        self.naver = NaverFinanceProvider()
        self.scorer = MultiFactorScorer()
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
                logger.exception(f"❌ {b_name} 계좌 잔고 API 조회 실패: {e}")
            
            # DB 폴백 (API 실패 시) — Repository를 거쳐 WAL/busy_timeout 등 커넥션 설정을 일관되게 적용
            try:
                if self.repo:
                    row = self.repo.get_latest_account_summary()
                    if row:
                        self.BROKER_EQUITY[b_name] = float(row["total_assets"])
                        self.BROKER_CASH[b_name] = float(row["cash"])
                        logger.info(f"💰 [{b_name}] (DB 폴백) 총자산: {self.BROKER_EQUITY[b_name]:,.0f}원, 예수금: {self.BROKER_CASH[b_name]:,.0f}원")
                        continue
            except Exception as e:
                logger.exception(f"❌ {b_name} DB 폴백 잔고 조회 실패: {e}")
            
            # 최종 폴백
            logger.warning(f"⚠️ {b_name} 계좌 잔고를 확인할 수 없어 자금을 0으로 설정합니다.")
            self.BROKER_EQUITY[b_name] = 0.0
            self.BROKER_CASH[b_name] = 0.0

    def _load_hyperparams(self):
        """DB에서 최신 하이퍼파라미터를 로드하여 매매 기준으로 사용합니다"""
        params = {
            "BULL_RSI": 30.0,             # 엄격한 타점(30) 원복
            "BULL_BB": 2.0,
            "BEAR_RSI": 25.0,             # 엄격한 타점(25) 원복
            "BEAR_BB": 2.2,
            "RSI_SELL_THRES": 70.0,
            "TRAILING_STOP_DROP": 3.0,
            "HARD_STOP_LOSS": -5.0,
            "BEAR_POSITION_FACTOR": 0.5,  # BEAR 국면 역추세 매수 비중 배수 (0이면 신규 매수 전면 중지)
            "HARD_STOP_COOLDOWN_DAYS": 5.0,  # 글로벌 하드스탑 락아웃 최소 유지 일수 (해제에는 BULL 국면도 필요)
            "MIN_HOLDING_DAYS": 5.0,      # 교체 매도 규율: 매수 후 최소 보유 달력일 (리스크 청산에는 미적용)
            # 추세추종 진입 문턱 (백테스트 B1 검증: 기존 하드코딩 RSI<=65 / 상대강도>3% 에서 완화)
            "TF_RSI_MIN": 45.0,
            "TF_RSI_MAX": 70.0,
            "TF_REL_MOMENTUM_MIN": 0.0,
            # 오버슈팅 익절 비율 (1.0 = 전량 익절. 0.5면 절반 익절 후 잔여 물량 러너 전환 — 백테스트 O1 검증)
            "OVERSHOOT_EXIT_FRACTION": 0.5,
            # Chandelier Exit 트레일링 — auto_trader 실시간 감시와 동일 키 공유 (백테스트 실험② 검증: 1.5→2.5)
            "CHANDELIER_ATR_MULT": 2.5,
            "TRAILING_MIN_DROP": 2.0,
            "TRAILING_MAX_DROP": 8.0,
            # 교체 유예일 (백테스트 실험 ④ 검증: 3일 유예 시 노이즈성 교체 감소 효과)
            "REPLACEMENT_GRACE_DAYS": 3.0,
            # 배치 확대 (백테스트 실험⑥ 검증: top5/10% → top8/15%, MDD -4%→-8% 트레이드오프 수반)
            "TOP_N": 8.0,
            "TARGET_WEIGHT": 0.15,
            # 1회 주문 최대 비율(예수금 대비). 기존엔 클래스 상수로 고정돼 SYSTEM_UPDATE로 튜닝이
            # 불가능했음 — 소액 실전 계좌(예: 100만원)에서는 이 값이 사실상의 병목이라 튜닝 가능하게 노출.
            "MAX_SINGLE_ORDER_RATIO": 0.20,
            "ETF_ATR_PERIOD": 20.0,
            "ETF_ATR_MULTIPLIER": 3.0,
            "ETF_TREND_SMA_PERIOD": 200.0,
            "ETF_REENTRY_SMA_PERIOD": 50.0,
            "ETF_STOP_COOLDOWN_DAYS": 5.0
        }
        try:
            if self.repo:
                db_params = self.repo.get_strategy_hyperparams()
                for k, v in db_params.items():
                    if k in params:
                        params[k] = float(v)
            logger.info(f"✅ DB에서 파라미터 로드 완료: {params}")
        except Exception as e:
            logger.exception(f"⚠️ DB 파라미터 로드 실패 (기존 설정값 유지): {e}")
        
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
        self.BEAR_POSITION_FACTOR = float(params["BEAR_POSITION_FACTOR"])
        self.HARD_STOP_COOLDOWN_DAYS = int(params["HARD_STOP_COOLDOWN_DAYS"])
        self.MIN_HOLDING_DAYS = int(params["MIN_HOLDING_DAYS"])
        self.TF_RSI_MIN = float(params["TF_RSI_MIN"])
        self.TF_RSI_MAX = float(params["TF_RSI_MAX"])
        self.TF_REL_MOMENTUM_MIN = float(params["TF_REL_MOMENTUM_MIN"])
        self.OVERSHOOT_EXIT_FRACTION = float(params["OVERSHOOT_EXIT_FRACTION"])
        self.CHANDELIER_ATR_MULT = float(params["CHANDELIER_ATR_MULT"])
        self.TRAILING_MIN_DROP = float(params["TRAILING_MIN_DROP"])
        self.TRAILING_MAX_DROP = float(params["TRAILING_MAX_DROP"])
        self.REPLACEMENT_GRACE_DAYS = int(params.get("REPLACEMENT_GRACE_DAYS", 3.0))
        self.TOP_N = int(params["TOP_N"])
        # ETF_TREND 프로파일의 비중 산정은 별도 검증 전이므로 주식 프로파일에만 적용
        if self.profile != ETF_TREND:
            self.TARGET_WEIGHT = float(params["TARGET_WEIGHT"])
            self.MAX_SINGLE_ORDER_RATIO = float(params["MAX_SINGLE_ORDER_RATIO"])

        self.ETF_ATR_PERIOD = int(params["ETF_ATR_PERIOD"])
        self.ETF_ATR_MULTIPLIER = float(params["ETF_ATR_MULTIPLIER"])
        self.ETF_TREND_SMA_PERIOD = int(params["ETF_TREND_SMA_PERIOD"])
        self.ETF_REENTRY_SMA_PERIOD = int(params["ETF_REENTRY_SMA_PERIOD"])
        self.ETF_STOP_COOLDOWN_DAYS = int(params["ETF_STOP_COOLDOWN_DAYS"])

    def _calculate_adx(self, df, period=14):
        try:
            adx_series = ind.adx(df, period)
            return float(adx_series.iloc[-1]) if not adx_series.empty and not pd.isna(adx_series.iloc[-1]) else 25.0
        except Exception as e:
            logger.warning(f"ADX 계산 실패 (기본값 25.0 적용): {e}")
            return 25.0

    def _determine_market_regime(self):
        """KOSPI 50일 이동평균선 및 ADX 지표를 기준으로 BULL/BEAR/SIDEWAY 국면을 판독하고 파라미터를 설정합니다."""
        logger.info("📈 실시간 KOSPI(KS11) 국면 판독 시작...")
        self.kospi_return_90d = 0.0
        try:
            import FinanceDataReader as fdr
            import datetime
            # 최근 180일치 코스피 데이터 로드
            start_date = (datetime.datetime.now() - datetime.timedelta(days=180)).strftime('%Y-%m-%d')
            kospi = fdr.DataReader("KS11", start=start_date)
            
            if not kospi.empty and len(kospi) >= 50:
                kospi["50_MA"] = kospi["Close"].rolling(window=50).mean()
                latest_close = float(kospi["Close"].iloc[-1])
                latest_ma = float(kospi["50_MA"].iloc[-1])
                
                # ADX 계산
                adx_val = self._calculate_adx(kospi)
                logger.info(f"📊 KOSPI ADX(14): {adx_val:.2f}")
                
                # 90일(영업일 기준 약 60일) 수익률 계산
                if len(kospi) >= 60:
                    self.kospi_return_90d = ((latest_close - float(kospi["Close"].iloc[-60])) / float(kospi["Close"].iloc[-60])) * 100
                else:
                    self.kospi_return_90d = 0.0
                
                # ADX < 20 이면 횡보장(SIDEWAY)으로 판단하되,
                # 코스피가 50일선 대비 +8% 이상 위에 있으면 추세 상승 중으로 간주해 BULL 적용
                ma_gap_pct = ((latest_close - latest_ma) / latest_ma) * 100 if latest_ma > 0 else 0.0
                if adx_val < 20.0 and ma_gap_pct < 8.0:
                    self.current_regime = "SIDEWAY"
                    self.RSI_BUY_THRES = 45.0  # 횡보장: 추세추종 진입을 위해 45까지 허용
                    self.BB_STD = 1.8
                    logger.info(f"📊 SIDEWAY 판정 (ADX: {adx_val:.1f}, 50일선 갭: {ma_gap_pct:+.1f}%)")
                else:
                    if latest_close > latest_ma:
                        self.current_regime = "BULL"
                        self.RSI_BUY_THRES = self.BULL_RSI
                        self.BB_STD = self.BULL_BB
                        if adx_val < 20.0:
                            logger.info(f"📊 ADX 낮으나 50일선 갭 {ma_gap_pct:+.1f}% → BULL 적용")
                    else:
                        self.current_regime = "BEAR"
                        self.RSI_BUY_THRES = self.BEAR_RSI
                        self.BB_STD = self.BEAR_BB
                    
                logger.info(f"⚖️ [현재 시장 국면] {self.current_regime} (코스피: {latest_close:,.2f} / 50일선: {latest_ma:,.2f}, 90일 수익률: {self.kospi_return_90d:+.2f}%)")
                logger.info(f"👉 [적용 파라미터] RSI 매수선: {self.RSI_BUY_THRES}, BB 하단배수: {self.BB_STD}")
            else:
                raise ValueError("코스피 데이터가 부족합니다.")
        except Exception as e:
            logger.exception(f"❌ 국면 판독 실패 (안전장치 발동: 보수적 BEAR 파라미터 적용): {e}")
            self.current_regime = "BEAR (Fallback)"
            self.RSI_BUY_THRES = self.BEAR_RSI
            self.BB_STD = self.BEAR_BB
            self.kospi_return_90d = 0.0

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
            logger.exception(f"❌ 서킷 브레이커 체크 중 에러 발생: {e}")
            return True

    def _init_dart(self):
        try:
            self.dart_api = DartAPI()
            self.dart_scorer = DartFinancialScorer(self.dart_api)
            logger.info("✅ DART API 및 재무 분석기 초기화 완료")
        except Exception as e:
            logger.exception(f"⚠️ DART 초기화 실패: {e}")
            self.dart_api = None
            self.dart_scorer = None

    def fetch_market_data(self) -> List[Dict]:
        logger.info("실제 시장 데이터 수집 시작...")
        sample_data = StrategyEngine.SAMPLE_DATA if StrategyEngine.SAMPLE_DATA else self.SAMPLE_DATA

        # 매월 첫 거래일에는 ETF 분배금/수정주가 소급 반영을 위해 전체 재조회(force_full) 수행
        now_kst = datetime.datetime.now(pytz.timezone('Asia/Seoul'))
        force_full_sync = is_first_trading_day_of_month(now_kst)
        if force_full_sync:
            logger.info("🗓️ 이번 달 첫 거래일 감지: OHLCV 전체 재조회(force_full)로 수정주가/분배금을 반영합니다.")

        raw_stocks_data = []
        for ticker, name in sample_data:
            df_hist = pd.DataFrame()
            try:
                if self.repo:
                    self.repo.sync_ohlcv_data(ticker, force_full=force_full_sync)
                    df_hist = self.repo.get_recent_ohlcv(ticker, limit=150)
            except Exception as db_e:
                logger.exception(f"[{name}] DB 동기화 또는 조회 중 오류: {db_e}")
                
            if df_hist.empty:
                try:
                    import FinanceDataReader as fdr
                    df_hist = fdr.DataReader(ticker, start=(datetime.datetime.now() - datetime.timedelta(days=150)).strftime('%Y-%m-%d'))
                except Exception as fdr_e:
                    logger.exception(f"[{name}] FinanceDataReader 조회 실패: {fdr_e}")
            
            raw_stocks_data.append((ticker, name, df_hist))
            time.sleep(0.1) # Termux 리소스 방어용 짧은 대기

        real_stocks = []
        for i, (ticker, name, df_hist) in enumerate(raw_stocks_data):
            # API Rate Limit 방어 (20개마다 3초 지연 추가)
            if i > 0 and i % 20 == 0:
                logger.info(f"⏳ Rate Limit 방어용 지연 처리: 3초간 대기... ({i}/{len(raw_stocks_data)})")
                time.sleep(3.0)

            eps_growth = 0.0
            industry_name = "기타"
            net_buying = 0.0
            current_price = 0.0
            
            dart_revenue_growth = 0.0
            dart_op_growth = 0.0
            dart_debt_ratio = 100.0
            dart_cf_quality = 50.0
            dart_dividend_yield = 0.0
            dart_major_shareholder_bonus = 0.0
            dart_available = False
            
            # STOCK_MULTIFACTOR 프로파일일 때만 거래대금/동전주 필터링 적용
            if self.profile != ETF_TREND:
                try:
                    if df_hist.empty or len(df_hist) < 5:
                        logger.info(f"⏭️ [{name}] 데이터 부족으로 1차 필터 스킵")
                        continue
                    
                    latest_close = float(df_hist['Close'].iloc[-1])
                    avg_vol_5d = float(df_hist['Volume'].tail(5).mean())
                    avg_value_5d = latest_close * avg_vol_5d
                    
                    if latest_close < 1000:
                        logger.info(f"⏭️ [{name}] 동전주 필터링 (최신가: {latest_close:,.0f}원 < 1,000원)")
                        continue
                        
                    if avg_value_5d < 500_000_000: # 5억 원
                        logger.info(f"⏭️ [{name}] 거래대금 부족 필터링 (5일 평균: {avg_value_5d/1e8:.1f}억 < 5억)")
                        continue
                except Exception as filter_e:
                    logger.warning(f"⚠️ [{name}] 1차 필터링 검사 실패: {filter_e}")
                
                # 네이버 금융 크롤링 (업종명/EPS 성장률/현재가)
                main_info = self.naver.fetch_main_info(ticker, name)
                eps_growth = main_info["eps_growth"]
                industry_name = main_info["industry_name"]
                current_price = main_info["current_price"]

                # Fallback for current price from local DB hist
                if current_price == 0.0 and not df_hist.empty:
                    current_price = float(df_hist['Close'].iloc[-1])
                    logger.info(f"ℹ️ [{name}] 실시간 시세 파싱 실패로 로컬 DB 최신 종가로 대체: {current_price:,.0f}원")

                # 네이버 금융 크롤링 (기관/외국인 수급)
                net_buying, current_price = self.naver.fetch_investor_net_buying(ticker, name, current_price)

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
            else:
                industry_name = "ETF"
                if not df_hist.empty:
                    current_price = float(df_hist['Close'].iloc[-1])
            
            # 기술 지표 피처 계산 (indicators.py 순수 함수 재사용)
            features = compute_technical_features(
                df_hist,
                current_price=current_price,
                bb_std=self.BB_STD,
                kospi_return_90d=getattr(self, 'kospi_return_90d', 0.0),
                name=name
            )

            real_stocks.append({
                "ticker": ticker,
                "name": name,
                "eps_growth": eps_growth,
                "industry_name": industry_name,
                "net_buying": net_buying,
                "price": current_price,
                "dart_revenue_growth": dart_revenue_growth,
                "dart_op_growth": dart_op_growth,
                "dart_debt_ratio": dart_debt_ratio,
                "dart_cf_quality": dart_cf_quality,
                "dart_dividend_yield": dart_dividend_yield,
                "dart_major_shareholder_bonus": dart_major_shareholder_bonus,
                "dart_available": dart_available,
                **features,
                "industry_score": 50.0
            })
            time.sleep(0.1)

        # 주간(5일) 섹터 모멘텀 계산 → industry_score 갱신
        apply_sector_momentum(real_stocks)

        # 섹터 수급 로테이션 신호 병합 (선택적 — 매핑/조회 실패 시 가격 모멘텀만으로 계속 진행)
        if trader_config.ENABLE_SECTOR_FLOW_BLEND:
            try:
                from stock_trader.core.sector_flow import get_flow_score_map
                flow_score_map = get_flow_score_map()
                apply_sector_flow_score(real_stocks, flow_score_map)
            except Exception as flow_e:
                logger.error(f"⚠️ 섹터 수급 점수 병합 실패 — 가격 모멘텀만으로 진행: {flow_e}")

        return real_stocks

    def calculate_scores(self, stocks: List[Dict]) -> List[Dict]:
        """멀티팩터 스코어링 (core/scorer.py의 MultiFactorScorer에 위임)"""
        return self.scorer.calculate_scores(stocks)

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
                logger.exception(f"{b_name} 보유 종목 API 조회 중 오류: {e}")

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
                                    "max_profit_rate": float(row["max_profit_rate"]) if row["max_profit_rate"] is not None else 0.0,
                                    "is_runner": bool(row.get("is_runner") or 0),
                                    "out_of_top_streak": int(row.get("out_of_top_streak") or 0)
                                })
                except Exception as db_e:
                    logger.exception(f"{b_name} 로컬 DB 로드 실패: {db_e}")

            # 고점 수익률(max_profit_rate) 병합/갱신 — API 성공 경로에서도 반드시 수행.
            # API 응답에는 max_profit_rate가 없으므로 DB의 고수위(high-water mark)를 병합하지 않으면
            # 트레일링 스탑과 ETF 샹들리에 스탑(peak_close)이 매입가 기준으로 퇴화한다.
            try:
                if self.repo:
                    db_all = self.repo.get_portfolio_holdings()
                    for stock in b_holdings:
                        ticker = stock["ticker"]
                        current_profit = stock["profit_rate"]

                        stored_max_profit = 0.0
                        stock.setdefault("is_runner", False)
                        stock.setdefault("out_of_top_streak", 0)
                        for row in db_all:
                            if row.get("broker_id") == b_name and row["stk_cd"] == ticker:
                                stored_max_profit = float(row["max_profit_rate"]) if row["max_profit_rate"] is not None else 0.0
                                stock["is_runner"] = bool(row.get("is_runner") or 0)
                                stock["out_of_top_streak"] = int(row.get("out_of_top_streak") or 0)
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
                logger.exception(f"{b_name} 보유 종목 DB 동기화/갱신 중 오류 발생: {db_e}")

            holdings.extend(b_holdings)
        return holdings
    def _build_feature_dict(self, s: Dict, additional: Dict = None) -> Dict:
        """종목 데이터와 시장 상태를 취합하여 ML 학습용 피처 사전 구축"""
        if not s:
            s = {}

        def to_python_type(val, default):
            if val is None:
                return default
            if hasattr(val, "item"):
                return val.item()
            return val

        feat = {
            "market_regime": getattr(self, "current_regime", "UNKNOWN"),
            "kospi_return_90d": float(to_python_type(getattr(self, "kospi_return_90d", 0.0), 0.0)),
            "technical": {
                "price": float(to_python_type(s.get("price", 0.0), 0.0)),
                "rsi_14": float(to_python_type(s.get("rsi", 50.0), 50.0)),
                "lower_band": float(to_python_type(s.get("lower_band", 0.0), 0.0)),
                "upper_band": float(to_python_type(s.get("upper_band", 0.0), 0.0)),
                "atr_pct": float(to_python_type(s.get("atr_pct", 3.0), 3.0)),
                "is_aligned": bool(to_python_type(s.get("is_aligned", False), False)),
                "is_under_ma120": bool(to_python_type(s.get("is_under_ma120", False), False)),
                "is_vcp": bool(to_python_type(s.get("is_vcp", False), False)),
                "momentum_5d": float(to_python_type(s.get("return_5d", 0.0), 0.0)),
                "relative_momentum": float(to_python_type(s.get("relative_momentum", 0.0), 0.0))
            },
            "fundamental": {
                "eps_growth": float(to_python_type(s.get("eps_growth", 0.0), 0.0)),
                "net_buying": float(to_python_type(s.get("net_buying", 0.0), 0.0)),
                "dart_revenue_growth": float(to_python_type(s.get("dart_revenue_growth", 0.0), 0.0)),
                "dart_op_growth": float(to_python_type(s.get("dart_op_growth", 0.0), 0.0)),
                "dart_debt_ratio": float(to_python_type(s.get("dart_debt_ratio", 100.0), 100.0)),
                "dart_cf_quality": float(to_python_type(s.get("dart_cf_quality", 50.0), 50.0)),
                "dart_dividend_yield": float(to_python_type(s.get("dart_dividend_yield", 0.0), 0.0)),
                "dart_major_shareholder_bonus": float(to_python_type(s.get("dart_major_shareholder_bonus", 0.0), 0.0))
            },
            "score": float(to_python_type(s.get("total_score", 0.0), 0.0))
        }
        if additional:
            feat.update(additional)
        return feat

    def _is_within_min_holding(self, ticker: str) -> bool:
        """교체 매도 규율: 마지막 매수 후 MIN_HOLDING_DAYS(달력일) 미경과 여부.

        trade_history의 최근 BUY 체결 시각으로 판정하며, 매수 이력을 확인할 수
        없거나 조회에 실패하면 False(기존 동작: 교체 허용)로 폴백한다."""
        if not self.repo or getattr(self, 'MIN_HOLDING_DAYS', 0) <= 0:
            return False
        try:
            ts = self.repo.get_last_buy_timestamp(ticker)
            if not ts:
                return False
            buy_date = datetime.datetime.strptime(str(ts)[:10], "%Y-%m-%d").date()
            days_held = (datetime.datetime.now(pytz.timezone('Asia/Seoul')).date() - buy_date).days
            return days_held < self.MIN_HOLDING_DAYS
        except Exception as e:
            logger.warning(f"[{ticker}] 최근 매수일 조회 실패 (교체 규율 미적용): {e}")
            return False

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
            
        # 글로벌 하드스탑: 미실현 손익을 계좌 전체(현금 포함) 총자산 대비 %로 평가한다.
        # 과거 보유분 매입금 대비 기준은 현금 비중이 높을 때 소수 포지션의 손실만으로
        # 전량 청산+락아웃이 발동하는 과민 문제가 있었다 (backtest_multifactor A/B 검증:
        # 기준 변경만으로 강제 청산 18회 -> 0회, Sharpe 0.36 -> 0.43).
        # 계좌 총자산을 알 수 없으면 기존(보유분 매입금) 기준으로 보수적으로 폴백한다.
        total_portfolio_profit_rate = 0.0
        if total_purchase_amt > 0:
            unrealized_pnl = total_eval_amt - total_purchase_amt
            account_equity = sum(self.BROKER_EQUITY.values()) if self.BROKER_EQUITY else 0.0
            basis_amt = account_equity if account_equity > 0 else total_purchase_amt
            total_portfolio_profit_rate = (unrealized_pnl / basis_amt) * 100

        if total_purchase_amt > 0 and total_portfolio_profit_rate <= self.HARD_STOP_LOSS:
            logger.critical(f"🚨 [글로벌 리스크] 계좌 전체 손실 한도 도달! (미실현 손익/총자산: {total_portfolio_profit_rate:.2f}%). 전량 청산(Hard Stop)을 단행합니다.")
            for stock in current_holdings:
                ticker = stock["ticker"]
                s_data = market_map.get(ticker, {})
                feat = self._build_feature_dict(s_data, {
                    "exit_reason": "global_hard_stop",
                    "profit_rate": stock.get("profit_rate", 0.0),
                    "max_profit_rate": stock.get("max_profit_rate", 0.0),
                    "portfolio_profit_rate": total_portfolio_profit_rate
                })
                sell_signals.append({
                    "broker_id": stock.get("broker_id", "KIWOOM"),
                    "ticker": stock["ticker"],
                    "name": stock["name"],
                    "action": "SELL",
                    "quantity": stock["quantity"],
                    "reason": f"계좌 전체 Hard Stop Loss 작동 (전체 수익률: {total_portfolio_profit_rate:.2f}%, 기준: {self.HARD_STOP_LOSS}%)",
                    "features": json.dumps(feat, ensure_ascii=False)
                })
            try:
                if self.publisher:
                    self.publisher.send_message_sync("GLOBAL_STOP_LOSS", {
                        "profit_rate": total_portfolio_profit_rate,
                        "threshold": self.HARD_STOP_LOSS
                    })
            except Exception as tg_e:
                logger.warning(f"글로벌 손절 IPC 알림 전송 실패: {tg_e}")
            if self.repo:
                self.repo.update_market_lockout(
                    active=True,
                    since=datetime.datetime.now().strftime("%Y-%m-%d"),
                    reason=f"{HARD_STOP_LOCKOUT_PREFIX} 글로벌 Hard Stop 작동 (전체 수익률: {total_portfolio_profit_rate:.2f}%)"
                )
            return sell_signals

        if self.profile == ETF_TREND:
            for stock in current_holdings:
                ticker = stock["ticker"]
                quantity = stock["quantity"]
                b_id = stock.get("broker_id", "KIWOOM")
                
                df_hist = pd.DataFrame()
                s_data = market_map.get(ticker, {})
                try:
                    if self.repo:
                        df_hist = self.repo.get_recent_ohlcv(ticker, limit=300)
                except Exception as e:
                    logger.exception(f"[{ticker}] ohlcv 데이터 조회 실패: {e}")
                
                if df_hist.empty:
                    logger.warning(f"⚠️ [{stock['name']}] OHLCV 데이터가 없어 청산 검사를 스킵합니다.")
                    continue
                
                sig_params = sig.StrategyParams(
                    atr_period=self.ETF_ATR_PERIOD,
                    atr_multiplier=self.ETF_ATR_MULTIPLIER,
                    trend_sma_period=self.ETF_TREND_SMA_PERIOD,
                    reentry_sma_period=self.ETF_REENTRY_SMA_PERIOD,
                    stop_cooldown_days=self.ETF_STOP_COOLDOWN_DAYS
                )
                
                pos_status = {
                    "pur_pric": stock.get("purchase_price", 0.0),
                    "max_profit_rate": stock.get("max_profit_rate", 0.0),
                    "peak_close": stock.get("purchase_price", 0.0) * (1 + stock.get("max_profit_rate", 0.0) / 100.0)
                }
                
                exit_sig = sig.check_exit(df_hist, pos_status, sig_params)
                if exit_sig:
                    feat = self._build_feature_dict(s_data, {
                        "exit_reason": "chandelier_stop",
                        "profit_rate": stock["profit_rate"],
                        "max_profit_rate": stock.get("max_profit_rate", 0.0),
                        "stop_level": exit_sig["stop_level"]
                    })
                    sell_signals.append({
                        "broker_id": b_id,
                        "ticker": ticker,
                        "name": stock["name"],
                        "action": "SELL",
                        "quantity": quantity,
                        "reason": exit_sig["reason"],
                        "features": json.dumps(feat, ensure_ascii=False)
                    })
                    logger.warning(f"⚠️ [{b_id}][ETF 청산] {stock['name']} 샹들리에 스탑 도달 청산: {exit_sig['reason']}")
                    
                    if self.repo:
                        today_str = datetime.datetime.now().strftime("%Y-%m-%d")
                        self.repo.save_stopped_position(ticker, today_str, float(df_hist['Close'].iloc[-1]), self.profile)
            return sell_signals

        for stock in current_holdings:
            ticker = stock["ticker"]
            profit = stock["profit_rate"]
            max_profit = stock.get("max_profit_rate", 0.0)
            quantity = stock["quantity"]
            current_price = stock.get("current_price", 0.0)
            b_id = stock.get("broker_id", "KIWOOM")
            
            # 교체 규율용: top_n 연속 이탈 거래일 추적 (주식 프로파일 한정)
            out_of_top_streak = stock.get("out_of_top_streak", 0)
            if self.profile != ETF_TREND:
                if ticker not in top_tickers:
                    out_of_top_streak += 1
                else:
                    out_of_top_streak = 0
                if self.repo:
                    try:
                        self.repo.update_out_of_top_streak(b_id, ticker, out_of_top_streak)
                    except Exception as e:
                        logger.exception(f"[{b_id}][{ticker}] out_of_top_streak 갱신 실패: {e}")
            
            s_data = market_map.get(ticker, {})
            rsi_val = s_data.get("rsi", 50.0)
            upper_band = s_data.get("upper_band", 999999999.0)
            
            # 종목별 변동성(ATR%) 로드하여 동적 손절선/트레일링스탑 계산 (Chandelier Exit)
            # 트레일링은 auto_trader 실시간 감시와 동일한 DB 파라미터를 사용 — 이중 기준 불일치 방지
            atr_pct = s_data.get("atr_pct", 3.0)
            dynamic_hard_stop = -max(3.0, min(8.0, 1.5 * atr_pct))
            dynamic_trailing_stop = max(self.TRAILING_MIN_DROP, min(self.TRAILING_MAX_DROP, self.CHANDELIER_ATR_MULT * atr_pct))
            
            if profit <= dynamic_hard_stop:
                feat = self._build_feature_dict(s_data, {
                    "exit_reason": "hard_stop",
                    "profit_rate": profit,
                    "max_profit_rate": max_profit,
                    "dynamic_hard_stop": dynamic_hard_stop
                })
                sell_signals.append({
                    "broker_id": b_id,
                    "ticker": ticker,
                    "name": stock["name"],
                    "action": "SELL",
                    "quantity": quantity,
                    "reason": f"개별 종목 Hard Stop Loss (수익률: {profit:.2f}%, 기준: {dynamic_hard_stop:.2f}%)",
                    "features": json.dumps(feat, ensure_ascii=False)
                })
                logger.warning(f"⚠️ [{b_id}][개별 손절] {stock['name']} 절대 손절선 도달로 전량 청산 (수익률: {profit:.2f}%, ATR 기준: {dynamic_hard_stop:.2f}%)")
            elif max_profit >= 2.0 and (max_profit - profit) >= dynamic_trailing_stop:
                feat = self._build_feature_dict(s_data, {
                    "exit_reason": "trailing_stop",
                    "profit_rate": profit,
                    "max_profit_rate": max_profit,
                    "dynamic_trailing_stop": dynamic_trailing_stop
                })
                sell_signals.append({
                    "broker_id": b_id,
                    "ticker": ticker,
                    "name": stock["name"],
                    "action": "SELL",
                    "quantity": quantity,
                    "reason": f"Trailing Stop 작동 (고점: {max_profit:.2f}%, 현재: {profit:.2f}%, 하락폭: {max_profit-profit:.2f}%p, 기준: {dynamic_trailing_stop:.2f}%p)",
                    "features": json.dumps(feat, ensure_ascii=False)
                })
                logger.info(f"🎯 [{b_id}][트레일링스탑] {stock['name']} 수익 확정 전량 청산 (고점: {max_profit:.2f}%, 현재: {profit:.2f}%)")
            elif (rsi_val >= self.RSI_SELL_THRES or current_price >= upper_band) and not stock.get("is_runner", False):
                # 러너 관리: 오버슈팅 시 일부만 익절하고 잔여 물량은 러너로 전환하여
                # 트레일링/하드스탑으로만 관리한다 (러너는 오버슈팅 재발동 제외).
                # backtest_multifactor O1 검증: 트레일링 청산 평균 +0.33% -> +8.25%, Sharpe 0.89 -> 1.07.
                # repo가 없으면 러너 상태를 영속할 수 없으므로 기존(전량 익절) 동작 유지.
                fraction = getattr(self, 'OVERSHOOT_EXIT_FRACTION', 1.0)
                sell_qty = quantity
                is_partial = False
                if fraction < 1.0 and self.repo:
                    part_qty = int(quantity * fraction)
                    if 0 < part_qty < quantity:
                        sell_qty = part_qty
                        is_partial = True
                feat = self._build_feature_dict(s_data, {
                    "exit_reason": "overshooting_partial" if is_partial else "overshooting",
                    "profit_rate": profit,
                    "max_profit_rate": max_profit,
                    "rsi": rsi_val,
                    "upper_band": upper_band
                })
                if is_partial:
                    reason_txt = f"오버슈팅 부분 익절 {sell_qty}/{quantity}주, 잔여 러너 전환 (RSI: {rsi_val:.1f}, BB상단: {upper_band:,.0f}원, 현재가: {current_price:,.0f}원)"
                else:
                    reason_txt = f"오버슈팅 청산 (RSI: {rsi_val:.1f}, BB상단: {upper_band:,.0f}원, 현재가: {current_price:,.0f}원)"
                sell_signals.append({
                    "broker_id": b_id,
                    "ticker": ticker,
                    "name": stock["name"],
                    "action": "SELL",
                    "quantity": sell_qty,
                    "reason": reason_txt,
                    "features": json.dumps(feat, ensure_ascii=False)
                })
                if is_partial:
                    try:
                        self.repo.set_position_runner(b_id, ticker, True)
                    except Exception as runner_e:
                        logger.exception(f"[{ticker}] 러너 상태 기록 실패: {runner_e}")
                    logger.info(f"📈 [{b_id}][오버슈팅 부분 익절] {stock['name']} {sell_qty}/{quantity}주 익절, 잔여 물량 러너 전환 (RSI {rsi_val:.1f})")
                else:
                    logger.info(f"📈 [{b_id}][오버슈팅 익절] {stock['name']} RSI {rsi_val:.1f} / BB 상단 도달로 청산")
            elif ticker not in top_tickers and profit < 2.0:
                # 교체 규율: 매수 후 MIN_HOLDING_DAYS 미경과 시 교체 매도를 유보한다.
                # 순위 노이즈에 따른 잦은 교체가 손실+거래비용의 주범이었음
                # (backtest_multifactor R1 검증: 수익률 +3.74% -> +9.54%, Sharpe 0.43 -> 0.89).
                # 리스크 청산(하드스탑/트레일링/오버슈팅)은 위 분기에서 규율과 무관하게 동작한다.
                if self._is_within_min_holding(ticker):
                    logger.info(f"⏳ [{b_id}][교체 유보] {stock['name']} 매수 후 {self.MIN_HOLDING_DAYS}일 미경과로 교체 매도 유보 (수익률: {profit:.2f}%)")
                    continue
                
                grace_days = getattr(self, 'REPLACEMENT_GRACE_DAYS', 0)
                if out_of_top_streak <= grace_days:
                    logger.info(f"⏳ [{b_id}][교체 유보] {stock['name']} 순위 이탈 유예기간({out_of_top_streak}/{grace_days}일) 이내로 교체 매도 유보 (수익률: {profit:.2f}%)")
                    continue
                    
                feat = self._build_feature_dict(s_data, {
                    "exit_reason": "replacement",
                    "profit_rate": profit,
                    "max_profit_rate": max_profit
                })
                sell_signals.append({
                    "broker_id": b_id,
                    "ticker": ticker,
                    "name": stock["name"],
                    "action": "SELL",
                    "quantity": quantity,
                    "reason": f"전략 제외 및 저효율 종목 교체 (수익률: {profit:.2f}%)",
                    "features": json.dumps(feat, ensure_ascii=False)
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
                features_str = s.get("features")
                if self.repo:
                    self.repo.save_trade_signal(
                        ticker=s["ticker"],
                        name=s["name"],
                        action="SELL",
                        quantity=s["quantity"],
                        reason=s["reason"],
                        status="PENDING",
                        broker_id=b_id,
                        features=features_str
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
                features_str = b.get("features")
                if self.repo:
                    self.repo.save_trade_signal(
                        ticker=b["ticker"],
                        name=b["name"],
                        action="BUY",
                        quantity=b["quantity"],
                        reason=b["reason"],
                        status="PENDING",
                        broker_id=b_id,
                        features=features_str
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
            logger.exception(f"시그널 업데이트 중 오류: {e}")

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
                    
            if self.profile == ETF_TREND and self.repo:
                lines.append("")
                lines.append("🛡️ *[ETF 샹들리에 스탑 현황]*")
                sig_params = getattr(self, 'strategy_params', sig.StrategyParams())
                for h in holdings:
                    ticker = h["ticker"]
                    df_hist = self.repo.get_recent_ohlcv(ticker, limit=150)
                    if not df_hist.empty:
                        purchase_price = float(h.get("pur_pric", 0.0))
                        max_profit_rate = float(h.get("max_profit_rate", 0.0))
                        peak_close = purchase_price * (1 + max_profit_rate / 100.0)
                        stop_level = sig.chandelier_stop_level(df_hist, peak_close, sig_params)
                        current_close = float(df_hist['Close'].iloc[-1])
                        dist_pct = (current_close - stop_level) / current_close * 100.0
                        lines.append(f"  · {h['name']} ({ticker}): 스탑가 {stop_level:,.0f}원 (거리 {dist_pct:+.2f}%)")
                
                # Check Lockout status
                lockout = self.repo.get_market_lockout()
                status_str = "활성" if lockout.get("active") else "비활성"
                lines.append(f"🔒 *시장 락아웃 상태*: {status_str}")
                if lockout.get("active"):
                    lines.append(f"  · 사유: {lockout.get('reason')} (시작일: {lockout.get('since')})")
                
                # Check Cooldown status
                cooldowns = self.repo.get_stopped_positions(self.profile)
                active_cooldowns = []
                for c in cooldowns:
                    ticker = c["ticker"]
                    df_hist = self.repo.get_recent_ohlcv(ticker, limit=150)
                    if not df_hist.empty:
                        dates = df_hist.index.strftime('%Y-%m-%d').tolist()
                        stop_date = c.get("stop_date")
                        if stop_date in dates:
                            stop_idx = dates.index(stop_date)
                            elapsed = len(df_hist) - 1 - stop_idx
                            if elapsed < sig_params.stop_cooldown_days:
                                remaining = sig_params.stop_cooldown_days - elapsed
                                active_cooldowns.append(f"{ticker} ({remaining}일)")
                if active_cooldowns:
                    lines.append(f"⏳ *쿨다운 제한 중인 종목*: {', '.join(active_cooldowns)}")
                else:
                    lines.append("⏳ *쿨다운 제한 중인 종목*: 없음")
            
            lines.append("━━━━━━━━━━━━━━")
            msg = "\n".join(lines)
            if self.publisher:
                self.publisher.send_message_sync("STRATEGY_REPORT", {"message": msg})
        except Exception as e:
            logger.exception(f"리포트 송신 중 오류: {e}")

    def run(self):
        logger.info("==========================================")
        logger.info("포트폴리오 관리 및 전략 엔진 가동 (다중 증권사)")
        
        # 0. 실제 계좌 잔고 로드 (핵심: 하드코딩 대신 실제 예수금 사용)
        self._fetch_account_equity()
        
        self._determine_market_regime()
        self.check_market_circuit_breaker()
        
        market_data = self.fetch_market_data()
        scored_stocks = self.calculate_scores(market_data)
        top_5 = scored_stocks[:self.TOP_N]
        top_tickers = [s["ticker"] for s in top_5]

        active_brokers = BrokerFactory.get_active_brokers()

        # 신호 생성 직전 포지션 대사: 어긋난 원장 위에서 매매 판단을 내리지 않는다.
        if not self._reconcile_positions(active_brokers):
            logger.critical("🚨 포지션 대사 실패로 금일 신호 생성을 중단합니다.")
            logger.info("==========================================")
            return

        holdings = self.fetch_current_holdings()
        sell_signals = self.generate_management_signals(holdings, top_tickers, market_data)

        if self.profile == ETF_TREND:
            buy_signals = self._generate_etf_buy_signals(active_brokers, holdings, sell_signals, market_data)
            self.update_signals(buy_signals, sell_signals)
            self._send_strategy_report(scored_stocks, buy_signals, sell_signals, holdings)
            logger.info("엔진 실행 완료")
            logger.info("==========================================")
            return
        else:
            buy_signals = self._generate_stock_buy_signals(active_brokers, holdings, sell_signals, top_5)

        self.update_signals(buy_signals, sell_signals)
        self._send_strategy_report(scored_stocks, buy_signals, sell_signals, holdings)
        logger.info("엔진 실행 완료")
        logger.info("==========================================")

    def _reconcile_positions(self, active_brokers) -> bool:
        """일일 사이클에서 신호 생성 직전 브로커 실계좌와 DB 포지션 기록을 대사한다.
        API/DB 오류 등으로 대사에 실패하면 False를 반환하여 어긋난 원장 위에서
        매매 신호를 생성하지 않도록 호출부(run)가 해당일 사이클을 중단시킨다."""
        if not self.repo:
            return True
        all_ok = True
        for b_name in active_brokers:
            try:
                broker = BrokerFactory.get_broker(b_name)
            except Exception as e:
                logger.exception(f"❌ [Reconcile] {b_name} 브로커 초기화 실패: {e}")
                all_ok = False
                continue
            result = reconciler.reconcile(b_name, broker, self.repo)
            if not result.get("success"):
                all_ok = False
        return all_ok

    def _generate_etf_buy_signals(self, active_brokers, holdings, sell_signals, market_data):
        buy_signals = []
        lockout_active = False
        sig_params = sig.StrategyParams(
            atr_period=self.ETF_ATR_PERIOD,
            atr_multiplier=self.ETF_ATR_MULTIPLIER,
            trend_sma_period=self.ETF_TREND_SMA_PERIOD,
            reentry_sma_period=self.ETF_REENTRY_SMA_PERIOD,
            stop_cooldown_days=self.ETF_STOP_COOLDOWN_DAYS
        )
        if self.repo:
            lockout_state = self.repo.get_market_lockout()
            kodex_df = pd.DataFrame()
            try:
                kodex_df = self.repo.get_recent_ohlcv("069500", limit=300)
            except Exception as e:
                logger.exception(f"KODEX 200 데이터 로드 실패: {e}")
            lockout_active = sig.market_lockout(kodex_df, lockout_state, sig_params)
            if lockout_state.get("active") and not lockout_active:
                if is_hard_stop_lockout(lockout_state) and not self._hard_stop_cooldown_elapsed(lockout_state):
                    # 글로벌 하드스탑 락아웃은 추세 회복(SMA50)만으로 해제하지 않고 최소 쿨다운을 요구
                    lockout_active = True
                    logger.warning(f"🚨 글로벌 하드스탑 락아웃 쿨다운 미경과 — 지수 회복에도 락아웃을 유지합니다 (시작일: {lockout_state.get('since')})")
                else:
                    logger.info("🔓 시장 락아웃 해제 조건 충족 (KODEX 200 종가 > SMA50). 락아웃을 비활성화합니다.")
                    self.repo.update_market_lockout(active=False)

        if getattr(self, 'is_system_locked', False) or lockout_active:
            logger.warning(f"🚨 [ETF 매수 중단] 시스템 락 또는 시장 락아웃 상태입니다. (system_locked={self.is_system_locked}, lockout={lockout_active})")
            return buy_signals

        today_str = datetime.datetime.now().strftime("%Y-%m-%d")
        for b_name in active_brokers:
            remaining_cash = self.BROKER_CASH.get(b_name, 0.0)
            broker_equity = self.BROKER_EQUITY.get(b_name, 0.0)
            if remaining_cash <= 0:
                continue
            
            for s in market_data:
                ticker = s["ticker"]
                name = s["name"]
                price = s["price"]
                atr_pct = s.get("atr_pct", 3.0)
                
                is_held = any(h["ticker"] == ticker and h["broker_id"] == b_name for h in holdings)
                is_selling = any(sel["ticker"] == ticker and sel.get("broker_id") == b_name for sel in sell_signals)
                
                if is_held or is_selling:
                    continue
                    
                df_hist = pd.DataFrame()
                try:
                    if self.repo:
                        df_hist = self.repo.get_recent_ohlcv(ticker, limit=300)
                except Exception as e:
                    logger.exception(f"[{ticker}] ohlcv 데이터 조회 실패: {e}")
                
                if df_hist.empty:
                    continue
                    
                last_stop_record = None
                if self.repo:
                    stopped_list = self.repo.get_stopped_positions(self.profile)
                    ticker_stops = [st for st in stopped_list if st["ticker"] == ticker]
                    if ticker_stops:
                        ticker_stops = sorted(ticker_stops, key=lambda x: x["stop_date"], reverse=True)
                        last_stop_record = ticker_stops[0]
                
                if last_stop_record:
                    is_buy_eligible = sig.can_reenter(df_hist, last_stop_record, sig_params, today_str)
                    signal_type = "ETF 재진입"
                else:
                    is_buy_eligible = sig.trend_ok(df_hist, sig_params)
                    signal_type = "ETF 신규진입"
                    
                if not is_buy_eligible:
                    continue
                    
                if price > 0 and remaining_cash > 0:
                    size_factor = max(0.5, min(1.5, 3.0 / atr_pct))
                    adjusted_weight = self.TARGET_WEIGHT * size_factor
                    target_amt = min(
                        broker_equity * adjusted_weight,
                        self.BROKER_CASH.get(b_name, 0.0) * self.MAX_SINGLE_ORDER_RATIO,
                        remaining_cash
                    )
                    quantity = int(target_amt / price)
                    
                    if quantity <= 0:
                        continue
                        
                    order_cost = quantity * price
                    if order_cost > remaining_cash:
                        quantity = int(remaining_cash / price)
                        order_cost = quantity * price
                        
                    if quantity <= 0:
                        continue
                        
                    remaining_cash -= order_cost
                    reason = f"[{signal_type}] 종가 {price:,.0f}원 | ATR_PCT:{atr_pct:.2f}"
                    feat = self._build_feature_dict(s, {
                        "signal_type": signal_type,
                        "atr_pct": atr_pct
                    })
                    buy_signals.append({
                        "broker_id": b_name,
                        "ticker": ticker,
                        "name": name,
                        "action": "BUY",
                        "quantity": quantity,
                        "reason": reason,
                        "features": json.dumps(feat, ensure_ascii=False)
                    })
                    logger.info(f"📋 [{b_name}] [{signal_type}] 매수 신호: {name} {quantity}주 × {price:,.0f}원 = {order_cost:,.0f}원 (ATR%: {atr_pct:.1f}%, 가중치: {size_factor:.2f}x, 잔여: {remaining_cash:,.0f}원)")

                    if last_stop_record and self.repo:
                        try:
                            self.repo.clear_stopped_position(ticker, self.profile)
                            logger.info(f"🔓 [{ticker}] 재진입 확정으로 쿨다운 기록 해제 (stopped_positions 삭제)")
                        except Exception as clear_e:
                            logger.exception(f"[{ticker}] 쿨다운 기록 해제 실패: {clear_e}")
        return buy_signals

    def _check_hard_stop_lockout(self) -> bool:
        """글로벌 하드스탑 락아웃 상태를 확인합니다. True면 신규 매수 금지.

        해제 조건: 락아웃 시작 후 HARD_STOP_COOLDOWN_DAYS 경과 AND 시장 국면이 BULL(코스피 > 50일선).
        장중 지수 급락 서킷 브레이커와 달리 지수의 단순 회복만으로는 해제되지 않는다."""
        if not self.repo:
            return False
        try:
            state = self.repo.get_market_lockout()
        except Exception as e:
            logger.exception(f"❌ 락아웃 상태 조회 실패 (안전을 위해 매수 차단): {e}")
            return True
        if not is_hard_stop_lockout(state):
            return False

        regime = getattr(self, 'current_regime', '')
        if self._hard_stop_cooldown_elapsed(state) and regime == "BULL":
            self.repo.update_market_lockout(active=False)
            logger.info(f"🔓 글로벌 하드스탑 락아웃 해제 (쿨다운 {getattr(self, 'HARD_STOP_COOLDOWN_DAYS', 5)}일 경과 + BULL 국면)")
            return False

        logger.warning(
            f"🚨 글로벌 하드스탑 락아웃 활성 — 신규 매수를 중지합니다 "
            f"(시작일: {state.get('since')}, 해제 조건: {getattr(self, 'HARD_STOP_COOLDOWN_DAYS', 5)}일 경과 + BULL 국면, 현재 국면: {regime or 'UNKNOWN'})"
        )
        return True

    def _hard_stop_cooldown_elapsed(self, state: Dict) -> bool:
        """하드스탑 락아웃 시작일로부터 HARD_STOP_COOLDOWN_DAYS 경과 여부 (시작일 파싱 실패 시 미경과 처리)"""
        since_str = str(state.get("since") or "")[:10]
        try:
            since_date = datetime.datetime.strptime(since_str, "%Y-%m-%d").date()
        except ValueError:
            logger.warning(f"⚠️ 하드스탑 락아웃 시작일 파싱 실패: '{since_str}' (락아웃 유지)")
            return False
        days_elapsed = (datetime.datetime.now(pytz.timezone('Asia/Seoul')).date() - since_date).days
        return days_elapsed >= getattr(self, 'HARD_STOP_COOLDOWN_DAYS', 5)

    def _generate_stock_buy_signals(self, active_brokers, holdings, sell_signals, top_5):
        buy_signals = []
        if self._check_hard_stop_lockout():
            return buy_signals
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
                atr_pct = s.get("atr_pct", 3.0)
                
                is_rsi_match = rsi_val <= self.RSI_BUY_THRES
                is_bb_match = (lower_band > 0) and (price <= lower_band)
                is_mean_reversion = is_rsi_match or is_bb_match
                
                regime_now = getattr(self, 'current_regime', '')
                is_trend_following = (
                    regime_now in ("BULL", "SIDEWAY") and
                    s.get("is_aligned", False) and
                    self.TF_RSI_MIN <= rsi_val <= self.TF_RSI_MAX and
                    s.get("relative_momentum", 0.0) > self.TF_REL_MOMENTUM_MIN and
                    s.get("is_bottoming", True)
                )
                
                if is_mean_reversion:
                    signal_type = "역추세"
                    weight_multiplier = 1.0
                    if is_rsi_match and not is_bb_match and not s.get("is_bottoming", True):
                        logger.info(f"⏭️ [{b_name}] {name}: RSI 조건 부합하나 하락세 미멈춤으로 매수 스킵")
                        continue
                elif is_trend_following:
                    weight_multiplier = 0.5 if regime_now == "SIDEWAY" else 0.7
                    signal_type = f"추세추종({'SIDEWAY' if regime_now == 'SIDEWAY' else 'BULL'})"
                    logger.info(f"📈 [{b_name}] {name}: 추세추종 진입 조건 부합 (RSI: {rsi_val:.1f}, MA정배열, 상대강도: {s.get('relative_momentum', 0.0):+.1f}%, 국면: {regime_now})")
                else:
                    logger.info(
                        f"⏭️ [{b_name}] {name}: 매수 조건 미충족 "
                        f"(RSI: {rsi_val:.1f} | 역추세RSI≤{self.RSI_BUY_THRES} | BB하단: {is_bb_match} | "
                        f"MA정배열: {s.get('is_aligned', False)} | 상대강도: {s.get('relative_momentum', 0.0):+.1f}% | 국면: {regime_now})"
                    )
                    continue

                # BEAR 국면 방어: 역추세 매수 비중 축소 (추세추종은 위에서 이미 BULL/SIDEWAY 한정)
                if regime_now.startswith("BEAR"):
                    bear_factor = getattr(self, 'BEAR_POSITION_FACTOR', 0.5)
                    if bear_factor <= 0.0:
                        logger.info(f"⏭️ [{b_name}] {name}: BEAR 국면 신규 매수 전면 중지 (BEAR_POSITION_FACTOR=0)")
                        continue
                    weight_multiplier *= bear_factor
                    logger.info(f"🛡️ [{b_name}] {name}: BEAR 국면 매수 비중 {bear_factor:.0%}로 축소 적용")

                if price > 0 and remaining_cash > 0:
                    score_ratio = (score / total_score_sum) * len(top_5)
                    size_factor = max(0.5, min(1.5, 3.0 / atr_pct)) * weight_multiplier
                    adjusted_weight = self.TARGET_WEIGHT * score_ratio * size_factor
                    
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
                        
                    remaining_cash -= order_cost
                    if signal_type == "역추세":
                        reason = f"[역추세] RSI: {rsi_val:.1f}, BB하단: {lower_band:,.0f}원 | ATR_PCT:{atr_pct:.2f} | TARGET_PRICE:{lower_band:.2f}"
                    else:
                        reason = f"[추세추종] RSI: {rsi_val:.1f}, MA정배열, 상대강도: {s.get('relative_momentum', 0.0):+.1f}% | ATR_PCT:{atr_pct:.2f} | TARGET_PRICE:{price:.2f}"
                        
                    buy_signals.append({
                        "broker_id": b_name,
                        "ticker": ticker,
                        "name": name,
                        "action": "BUY",
                        "quantity": quantity,
                        "reason": reason,
                        "features": json.dumps(self._build_feature_dict(s), ensure_ascii=False)
                    })
                    logger.info(f"📋 [{b_name}] [{signal_type}] 매수 신호: {name} {quantity}주 × {price:,.0f}원 = {order_cost:,.0f}원 (ATR%: {atr_pct:.1f}%, 비중: {size_factor:.2f}x, 잔여: {remaining_cash:,.0f}원)")
                else:
                    if remaining_cash <= 0:
                        logger.info(f"⏭️ [{b_name}] {name}: 예수금 소진으로 매수 스킵")
                    else:
                        logger.info(f"⏭️ [{b_name}] {name}: 가격 정보 없음으로 매수 스킵")
        return buy_signals

if __name__ == "__main__":
    repo = DbRepository(DB_PATH)
    publisher = IpcPublisher()
    engine = StrategyEngine(db_repository=repo, ipc_publisher=publisher)
    engine.run()
