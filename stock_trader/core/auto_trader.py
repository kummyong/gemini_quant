import os
import sys
import time
import logging
import pytz
import json
from datetime import datetime
from stock_trader.communication.telegram_utils import send_telegram_message

# 1. 경로 및 시간 설정
from stock_trader.config import STOCK_TRADER_DIR as BASE_DIR, DB_PATH
KST = pytz.timezone('Asia/Seoul')

# 2. 로깅 (KST 시간대 적용)
def kst_converter(*args):
    return datetime.now(KST).timetuple()

logging.Formatter.converter = kst_converter
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("AutoTrader")

# 3. 브로커 팩토리 임포트
import threading
from stock_trader.broker.broker_factory import BrokerFactory
from stock_trader.data.db_repository import DbRepository

# 캐시 및 안전장치 전역 변수
ATR_CACHE = {}  # {ticker: (date_str, atr_pct)} — 날짜가 바뀌면 재계산
last_circuit_breaker_check = 0.0
market_halt = False
last_holdings_monitoring_time = 0.0

_REPO = None
_HYPERPARAMS = {"ts": 0.0, "values": {}}
HYPERPARAM_DEFAULTS = {
    "HARD_STOP_LOSS": -5.0,
    "CHANDELIER_ATR_MULT": 2.5,   # 고전적 Chandelier(3.0)에 가깝게 완화 — 장중 흔들기 휩쏘 방지
    "TRAILING_MIN_DROP": 2.0,
    "TRAILING_MAX_DROP": 8.0,
    "MAX_CHASE_PCT": 2.0,         # 14:30 이후 마감 집행 시 목표가 대비 추격 허용 폭(%)
}

def get_repo() -> DbRepository:
    global _REPO
    if _REPO is None:
        _REPO = DbRepository(DB_PATH)
    return _REPO

def get_hyperparams() -> dict:
    """strategy_hyperparams 테이블을 5분 캐시로 로드합니다.
    텔레그램 [SYSTEM_UPDATE]로 튜닝한 값이 실거래 손절/트레일링 기준에 반영됩니다."""
    now_ts = time.time()
    if now_ts - _HYPERPARAMS["ts"] > 300.0 or not _HYPERPARAMS["values"]:
        try:
            db_params = get_repo().get_strategy_hyperparams()
            _HYPERPARAMS["values"] = {k: float(db_params.get(k, v)) for k, v in HYPERPARAM_DEFAULTS.items()}
        except Exception as e:
            logger.warning(f"⚠️ 하이퍼파라미터 로드 실패 (기본값/이전값 유지): {e}")
            if not _HYPERPARAMS["values"]:
                _HYPERPARAMS["values"] = dict(HYPERPARAM_DEFAULTS)
        _HYPERPARAMS["ts"] = now_ts
    return _HYPERPARAMS["values"]

def set_market_halt(active: bool, reason: str = None):
    """서킷 브레이커 상태를 메모리와 DB(market_lockout)에 함께 기록합니다.
    프로세스가 재시작되어도 락아웃 상태가 유지됩니다."""
    global market_halt
    market_halt = active
    try:
        since = datetime.now(KST).isoformat() if active else None
        get_repo().update_market_lockout(active, since=since, reason=reason)
    except Exception as e:
        logger.warning(f"⚠️ market_lockout DB 기록 실패: {e}")

def load_market_halt():
    global market_halt
    try:
        state = get_repo().get_market_lockout()
        market_halt = bool(state.get("active", 0))
        if market_halt:
            logger.warning(f"🚨 재시작 후 시장 락아웃 상태 복원됨 (사유: {state.get('reason')})")
    except Exception as e:
        logger.warning(f"⚠️ market_lockout DB 조회 실패: {e}")

def get_cached_atr_pct(ticker: str) -> float:
    """종목의 14일 ATR% 값을 계산하고 당일 기준으로 캐시합니다."""
    today = datetime.now(KST).strftime('%Y-%m-%d')
    cached = ATR_CACHE.get(ticker)
    if cached and cached[0] == today:
        return cached[1]
    try:
        import pandas as pd
        repo = get_repo()
        df = repo.get_recent_ohlcv(ticker, limit=40)
        
        if df.empty or len(df) < 15:
            import FinanceDataReader as fdr
            from datetime import datetime, timedelta
            df = fdr.DataReader(ticker, start=(datetime.now(KST) - timedelta(days=40)).strftime('%Y-%m-%d'))
            
        if not df.empty and len(df) >= 15:
            closes = df['Close']
            highs = df['High']
            lows = df['Low']
            tr = pd.concat([highs - lows, (highs - closes.shift(1)).abs(), (lows - closes.shift(1)).abs()], axis=1).max(axis=1)
            atr = tr.rolling(window=14).mean().iloc[-1]
            atr_pct = (atr / closes.iloc[-1]) * 100
            ATR_CACHE[ticker] = (today, atr_pct)
            logger.info(f"📋 Cached ATR for {ticker} (using DB/FDR): {atr_pct:.2f}%")
            return atr_pct
    except Exception as e:
        logger.warning(f"⚠️ ATR 계산 실패 ({ticker}): {e}")
    return 3.0  # 기본값

def get_intraday_market_indices() -> dict:
    """네이버 금융의 실시간 지수 API를 호출하여 코스피/코스닥 당일 변동률을 리턴합니다. (REST API 제한 우회)"""
    import requests
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        url = "https://polling.finance.naver.com/api/realtime/domestic/index/KOSPI,KOSDAQ"
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json()
            result = {}
            for item in data.get("result", {}).get("areas", []):
                for index_data in item.get("datas", []):
                    code = index_data.get("cd")  # 'KOSPI' or 'KOSDAQ'
                    rate_str = index_data.get("cr", "0.0")  # 'cr'이 변동률 문자열
                    try:
                        result[code] = float(rate_str)
                    except ValueError:
                        pass
            return result
    except Exception as e:
        logger.warning(f"⚠️ 네이버 실시간 지수 조회 실패: {e}")
    return {}

def monitor_holdings_and_stops():
    """보유 종목에 대한 Trailing Stop 및 Hard Stop 실시간 감시"""
    global last_holdings_monitoring_time
    now_ts = time.time()
    # 1분(60초) 간격으로 감시 실행
    if now_ts - last_holdings_monitoring_time < 60.0:
        return
    last_holdings_monitoring_time = now_ts
    
    logger.info("🛡️ [실시간 보유 종목 감시] Trailing Stop 및 Hard Stop 체크 중...")
    
    active_brokers = BrokerFactory.get_active_brokers()
    for b_name in active_brokers:
        try:
            api = BrokerFactory.get_broker(b_name)
            summary = api.get_account_summary()
            if not summary or "acnt_evlt_remn_indv_tot" not in summary:
                continue
                
            holdings = summary["acnt_evlt_remn_indv_tot"]
            if not holdings:
                continue
                
            # DB의 기존 max_profit_rate 정보 조회
            db_holdings = {}
            try:
                db_holdings = get_repo().get_max_profit_rates(b_name)
            except Exception as db_e:
                logger.error(f"❌ 감시 중 DB 조회 에러: {db_e}")
                
            for h in holdings:
                ticker = h["stk_cd"].replace("A", "")
                name = h["stk_nm"]
                profit = float(h["prft_rt"])
                qty = int(h["rmnd_qty"])
                purchase_price = float(h.get("pchs_amt", 0.0)) / max(1, qty)
                current_price = float(h.get("evlt_amt", 0.0)) / max(1, qty)
                
                # 1. max_profit_rate 업데이트
                stored_max = db_holdings.get(ticker, 0.0)
                new_max = max(stored_max, profit)
                
                # DB 업데이트
                try:
                    get_repo().update_portfolio_holding(
                        stk_cd=ticker, stk_nm=name, rmnd_qty=qty,
                        pur_pric=purchase_price, cur_prc=current_price,
                        prft_rt=profit, max_profit_rate=new_max, broker_id=b_name)
                except Exception as db_e:
                    logger.error(f"❌ 감시 중 DB 업데이트 에러: {db_e}")
                
                # 2. Hard Stop Loss 판정 (기준: strategy_hyperparams.HARD_STOP_LOSS)
                hard_stop_loss_limit = get_hyperparams()["HARD_STOP_LOSS"]
                if profit <= hard_stop_loss_limit:
                    logger.warning(f"🚨 [개별 손절] {name} 절대 손절선 도달! (수익률: {profit:.2f}%) 즉시 전량 청산 주문을 집행합니다.")
                    sell_feat = {
                        "exit_reason": "hard_stop",
                        "profit_rate": profit,
                        "max_profit_rate": stored_max,
                        "hard_stop_limit": hard_stop_loss_limit
                    }
                    trigger_realtime_sell(b_name, api, ticker, name, qty, f"실시간 Hard Stop Loss 작동 (수익률: {profit:.2f}%)", json.dumps(sell_feat, ensure_ascii=False), est_price=current_price)
                    continue

                # 3. Trailing Stop (Chandelier Exit) 판정
                if new_max >= 2.0:
                    hp = get_hyperparams()
                    atr_pct = get_cached_atr_pct(ticker)
                    # Chandelier Exit: 고점 수익률 대비 ATR×배수 하락 시 청산 (하한/상한은 하이퍼파라미터)
                    stop_threshold = max(hp["TRAILING_MIN_DROP"], min(hp["TRAILING_MAX_DROP"], hp["CHANDELIER_ATR_MULT"] * atr_pct))
                    
                    if (new_max - profit) >= stop_threshold:
                        logger.warning(f"🎯 [트레일링스탑] {name} Chandelier Exit 작동! (고점: {new_max:.2f}%, 현재: {profit:.2f}%, 하락폭: {new_max-profit:.2f}%, 기준: {stop_threshold:.2f}%) 즉시 전량 청산합니다.")
                        sell_feat = {
                            "exit_reason": "trailing_stop",
                            "profit_rate": profit,
                            "max_profit_rate": new_max,
                            "stop_threshold": stop_threshold,
                            "atr_pct": atr_pct
                        }
                        trigger_realtime_sell(b_name, api, ticker, name, qty, f"실시간 Trailing Stop 작동 (고점: {new_max:.2f}%, 현재: {profit:.2f}%, 하락폭: {new_max-profit:.2f}%p)", json.dumps(sell_feat, ensure_ascii=False), est_price=current_price)
                        
        except Exception as e:
            logger.error(f"❌ 보유 종목 실시간 감시 루프 에러: {e}")
 
def trigger_realtime_sell(broker_id: str, api, ticker: str, name: str, qty: int, reason: str, features: str = None, est_price: float = 0.0):
    """실시간 청산 매도 주문 전송 및 알림 (est_price: 주문 시점 현재가 — 체결가 추정 기록용)"""
    try:
        logger.info(f"🛒 [{broker_id}] 실시간 매도 주문 실행: {name} - {qty}주")
        res = api.place_order(ticker, qty, 0, side="SELL")
        logger.info(f"📡 실시간 매도 응답: {res}")
        
        is_success = False
        if res:
            if res.get("return_code") == 0 or res.get("status") == "success" or res.get("rt_cd") == "0":
                is_success = True
                
        if is_success:
            # DB 기록: 포지션 수량 0 처리 + 체결 이력
            get_repo().mark_holding_sold(broker_id, ticker)
            get_repo().save_trade_history(ticker, name, "SELL", qty, int(est_price), int(est_price * qty), f"[실시간감시] {reason}", features)
                
            # 텔레그램 알림
            trade_msg = (
                f"🔴 *[실시간 매도 청산 체결]*\n"
                f"━━━━━━━━━━━━━━\n"
                f"📌 *종목:* {name} ({ticker})\n"
                f"🔢 *수량:* {qty:,}주\n"
                f"📋 *사유:* {reason}\n"
                f"⏰ *시각:* {datetime.now(KST).strftime('%H:%M:%S')}\n"
                f"━━━━━━━━━━━━━━"
            )
            try:
                send_telegram_message(trade_msg)
            except Exception as tg_e:
                logger.warning(f"텔레그램 알림 전송 실패: {tg_e}")
        else:
            fail_reason = str(res.get('return_msg', '')) if res else 'No response'
            logger.error(f"❌ 실시간 매도 주문 실패: {name} - {fail_reason}")
    except Exception as e:
        logger.error(f"❌ trigger_realtime_sell 도중 오류: {e}")

def estimate_fill_price(api, ticker: str, fallback_price: float) -> float:
    """시장가 주문 직후 계좌를 재조회하여 실제 평균 매입단가를 확인합니다.
    (직전 보유분이 있으면 평단가가 섞이는 한계가 있음) 확인 실패 시 주문 직전 현재가로 대체."""
    try:
        time.sleep(1.5)  # 시장가 체결 반영 대기
        summary = api.get_account_summary()
        holdings = (summary or {}).get("acnt_evlt_remn_indv_tot") or []
        for h in holdings:
            if str(h.get("stk_cd", "")).replace("A", "") == ticker:
                q = int(h.get("rmnd_qty", 0))
                if q > 0:
                    return float(h.get("pchs_amt", 0.0)) / q
    except Exception as e:
        logger.warning(f"⚠️ 체결가 확인 실패 ({ticker}) — 주문 직전 현재가로 기록: {e}")
    return fallback_price

def risk_monitor_loop():
    """보유 종목 Hard/Trailing Stop 전용 감시 스레드.
    주문 집행 루프의 레이트리밋 백오프(최대 수십 초 sleep)에 리스크 감시가 블로킹되지 않도록 분리."""
    from stock_trader.core.korean_market_calendar import is_market_holiday
    while True:
        try:
            now = datetime.now(KST)
            is_trading_time = (9 <= now.hour < 15 or (now.hour == 15 and now.minute < 30))
            if not is_market_holiday(now) and is_trading_time:
                monitor_holdings_and_stops()  # 내부에서 60초 간격 자체 제한
                time.sleep(15)
            else:
                time.sleep(300)
        except Exception as e:
            logger.error(f"❌ 리스크 감시 스레드 오류: {e}")
            time.sleep(30)

def run_trade():
    global last_circuit_breaker_check, market_halt
    logger.info("🚀 [KST 정밀 매매 엔진] 가동 시작 (다중 증권사 지원)")

    load_market_halt()  # 재시작 시 서킷 브레이커 상태 복원
    threading.Thread(target=risk_monitor_loop, daemon=True, name="RiskMonitor").start()

    while True:
        try:
            now = datetime.now(KST)
            from stock_trader.core.korean_market_calendar import is_market_holiday
            
            # 장중 시간 (09:00 ~ 15:30) 이고 휴일이 아닌 경우
            is_trading_time = (9 <= now.hour < 15 or (now.hour == 15 and now.minute < 30))
            if not is_market_holiday(now) and is_trading_time:
                
                # 1. 지수 서킷 브레이커 (패닉 셸 감지) - 3분 주기로 네이버 금융 조회
                now_ts = time.time()
                if now_ts - last_circuit_breaker_check > 180.0:
                    last_circuit_breaker_check = now_ts
                    indices = get_intraday_market_indices()
                    if indices:
                        kospi_chg = indices.get("KOSPI", 0.0)
                        kosdaq_chg = indices.get("KOSDAQ", 0.0)
                        
                        logger.info(f"📊 [시장 지수 체크] KOSPI: {kospi_chg:+.2f}%, KOSDAQ: {kosdaq_chg:+.2f}%")
                        
                        # 지수가 -3.0% 이하로 폭락하는 경우
                        if kospi_chg <= -3.0 or kosdaq_chg <= -3.0:
                            if not market_halt:
                                set_market_halt(True, f"KOSPI {kospi_chg:+.2f}%, KOSDAQ {kosdaq_chg:+.2f}% 급락")
                                halt_msg = f"🚨 *[시장 급락 서킷 브레이커 발동]*\nKOSPI: {kospi_chg:+.2f}%, KOSDAQ: {kosdaq_chg:+.2f}%\n모든 신규 매수를 중단합니다!"
                                logger.critical(halt_msg)
                                try:
                                    send_telegram_message(halt_msg)
                                except Exception as tg_e:
                                    logger.warning(f"텔레그램 알림 실패: {tg_e}")
                        else:
                            if market_halt:
                                set_market_halt(False)
                                resume_msg = f"✅ *[시장 지수 회복 - 매매 해제]*\nKOSPI: {kospi_chg:+.2f}%, KOSDAQ: {kosdaq_chg:+.2f}%\n신규 매수제한을 해제합니다."
                                logger.info(resume_msg)
                                try:
                                    send_telegram_message(resume_msg)
                                except Exception as tg_e:
                                    logger.warning(f"텔레그램 알림 실패: {tg_e}")
                
                # (보유 종목 Trailing/Hard Stop 감시는 전용 스레드 risk_monitor_loop에서 수행)

                # 3. 매매 신호 로드
                signals = get_repo().get_pending_signals()
                
                if signals:
                    logger.info(f"🧠 {len(signals)}개의 매매 신호 분석 중...")

                    broker_apis = {}
                    broker_cash = {}
                    # 브로커별 미처리 BUY 신호 수 — 예수금 부족 시 남은 신호들과 공평 분할하기 위함
                    pending_buy_counts = {}
                    for s in signals:
                        if s['action'] == 'BUY':
                            b = s['broker_id'] if s.get('broker_id') else "KIWOOM"
                            pending_buy_counts[b] = pending_buy_counts.get(b, 0) + 1

                    for sig in signals:
                        broker_id = sig['broker_id'] if 'broker_id' in sig.keys() and sig['broker_id'] else "KIWOOM"
                        
                        # API 초기화 및 예수금 캐싱 (해당 루프 내 1회)
                        if broker_id not in broker_apis:
                            try:
                                api = BrokerFactory.get_broker(broker_id)
                                broker_apis[broker_id] = api
                                account = api.get_account_summary()
                                if account:
                                    output = account.get("output") if isinstance(account.get("output"), dict) else account
                                    broker_cash[broker_id] = float(output.get("prsm_dpst_aset_amt", 0))
                                    logger.info(f"💰 [{broker_id}] 현재 예수금: {broker_cash[broker_id]:,.0f}원")
                            except Exception as e:
                                logger.warning(f"⚠️ [{broker_id}] API/예수금 조회 실패: {e}")
                                broker_apis[broker_id] = None
                                broker_cash[broker_id] = 0.0
                                
                        api = broker_apis[broker_id]
                        available_cash = broker_cash.get(broker_id, 0.0)
                        
                        if not api:
                            logger.warning(f"⚠️ [{broker_id}] API를 불러올 수 없어 {sig['name']} 주문 스킵")
                            continue

                        qty = int(sig['quantity']) if sig['quantity'] is not None and int(sig['quantity']) > 0 else 1
                        
                        # BUY 주문 시 추가 필터링 및 스마트 주문 집행
                        if sig['action'] == 'BUY':
                            remaining_buys = max(1, pending_buy_counts.get(broker_id, 1))
                            pending_buy_counts[broker_id] = remaining_buys - 1
                            if market_halt:
                                logger.warning(f"⏭️ [매수 보류] {sig['name']}: 시장 서킷 브레이커 작동 중으로 매수 스킵")
                                continue
                                
                            # 스마트 분할 매수 / 눌림목 대기 로직 적용
                            try:
                                current_price = api.get_current_price(sig['ticker'])
                                if current_price <= 0:
                                    logger.warning(f"⚠️ [{sig['name']}] 현재가 조회 실패 — 매수 보류 (다음 루프에서 재시도)")
                                    continue

                                target_price = None
                                if "TARGET_PRICE:" in sig['reason']:
                                    try:
                                        target_price = float(sig['reason'].split("TARGET_PRICE:")[1].split()[0])
                                    except:
                                        pass
                                
                                # 스마트 매수 집행 판단:
                                # 1. 타겟가(볼밴 하단) 이하로 내려오는 눌림목일 때 매수진입
                                # 2. 장 마감 임박(14시 30분 이후)에는 목표가 미도달이어도 집행하되,
                                #    추격 허용 폭(MAX_CHASE_PCT) 이내일 때만 — 엣지 없는 고가 추격 매수 방지
                                if target_price and current_price > 0:
                                    is_target_reached = current_price <= target_price * 1.005 # 0.5% 안전마진 이내
                                    is_late_afternoon = now.hour > 14 or (now.hour == 14 and now.minute >= 30)

                                    if not is_target_reached and not is_late_afternoon:
                                        logger.info(f"⏳ [눌림목 대기] {sig['name']}: 현재가 {current_price:,.0f}원 > 목표가 {target_price:,.0f}원 (14시 30분 이후 자동체결 대기)")
                                        continue
                                    elif is_late_afternoon and not is_target_reached:
                                        max_chase_pct = get_hyperparams()["MAX_CHASE_PCT"]
                                        chase_pct = (current_price - target_price) / target_price * 100.0
                                        if chase_pct > max_chase_pct:
                                            logger.info(f"⛔ [추격 상한 초과] {sig['name']}: 현재가 {current_price:,.0f}원 = 목표가 대비 {chase_pct:+.2f}% (허용 {max_chase_pct:.1f}%) — 매수 보류")
                                            continue
                                        logger.info(f"⏰ [마감 임박 집행] {sig['name']}: 목표가 대비 {chase_pct:+.2f}% 추격 허용 범위 내 — 현재가 {current_price:,.0f}원 매수 집행")
                                    else:
                                        logger.info(f"🎯 [목표가 터치 진입] {sig['name']}: 현재가 {current_price:,.0f}원 <= 목표가 {target_price:,.0f}원 만족")
                                
                                # 예수금 최종 검증
                                if current_price > 0:
                                    estimated_cost = qty * current_price
                                    if estimated_cost > available_cash:
                                        # 잔여 예수금을 남은 매수 신호 수로 분할 — 앞 순번이 전부 가져가 뒤 순번이 굶는 것 방지
                                        fair_cash = available_cash / remaining_buys
                                        old_qty = qty
                                        qty = int(fair_cash * 0.95 / current_price)  # 95% 안전마진
                                        if qty <= 0:
                                            logger.warning(f"⚠️ [{broker_id}] 예수금 부족으로 매수 스킵: {sig['name']} (필요: {estimated_cost:,.0f}원, 예수금: {available_cash:,.0f}원)")
                                            get_repo().cancel_signal(sig['id'], "예수금 부족")
                                            continue
                                        else:
                                            logger.info(f"📉 [{broker_id}] 수량 조정: {sig['name']} {old_qty}주 → {qty}주 (예수금 제한)")
                                            
                            except Exception as price_e:
                                logger.warning(f"⚠️ 스마트 매수 조건 검증 실패 ({sig['ticker']}) — 매수 보류 (다음 루프에서 재시도): {price_e}")
                                continue
                        
                        # 체결가 추정용 기준가 확보 (BUY는 위에서 조회한 현재가 사용)
                        if sig['action'] == 'BUY':
                            est_price = current_price
                        else:
                            try:
                                est_price = float(api.get_current_price(sig['ticker']))
                            except Exception:
                                est_price = 0.0

                        logger.info(f"🛒 [{broker_id}] 주문 요청: {sig['name']} ({sig['ticker']}) - {sig['action']} {qty}주")
                        res = api.place_order(sig['ticker'], qty, 0, side=sig['action'])
                        
                        logger.info(f"📡 API 응답: {res}")
                        
                        is_success = False
                        if res:
                            if res.get("return_code") == 0 or res.get("status") == "success" or res.get("rt_cd") == "0":
                                is_success = True
                        
                        if is_success:
                            logger.info(f"✅ 주문 접수 성공: {sig['name']}")

                            if sig['action'] == 'BUY':
                                # 계좌 재조회로 실제 평균 매입단가 확인 (실패 시 주문 직전 현재가로 추정)
                                fill_price = estimate_fill_price(api, sig['ticker'], est_price)
                                # 예수금 차감 — 같은 루프의 다음 매수 신호가 이미 소진된 예수금을 중복 사용하지 않도록
                                broker_cash[broker_id] = max(0.0, broker_cash[broker_id] - qty * fill_price)
                            else:
                                fill_price = est_price

                            try:
                                get_repo().complete_signal(sig['id'])
                                get_repo().save_trade_history(sig['ticker'], sig['name'], sig['action'], qty, int(fill_price), int(fill_price * qty), sig['reason'], sig['features'])
                                logger.info(f"✅ DB 기록 완료: {sig['name']} (체결단가 추정: {fill_price:,.0f}원)")
                                
                                # 텔레그램 매매 알림 전송
                                action_emoji = "🟢" if sig['action'] == "BUY" else "🔴"
                                action_text = "매수" if sig['action'] == "BUY" else "매도"
                                trade_msg = (
                                    f"{action_emoji} *[{action_text} 체결]*\n"
                                    f"━━━━━━━━━━━━━━\n"
                                    f"📌 *종목:* {sig['name']} ({sig['ticker']})\n"
                                    f"🔢 *수량:* {qty:,}주\n"
                                    f"💵 *단가:* 약 {fill_price:,.0f}원 (시장가, 계좌 재조회 기준)\n"
                                    f"📋 *사유:* {sig['reason']}\n"
                                    f"⏰ *시각:* {datetime.now(KST).strftime('%H:%M:%S')}\n"
                                    f"━━━━━━━━━━━━━━"
                                )
                                try:
                                    send_telegram_message(trade_msg)
                                except Exception as tg_e:
                                    logger.warning(f"텔레그램 알림 전송 실패 (매매는 정상 처리됨): {tg_e}")
                            except Exception as db_e:
                                logger.error(f"❌ DB 업데이트 실패: {db_e}")
                        else:
                            fail_reason = str(res.get('return_msg', '')) if res else 'No response'
                            logger.warning(f"⚠️ 주문 실패: {sig['name']} - {fail_reason}")
                            try:
                                get_repo().cancel_signal(sig['id'], f"실패: {fail_reason[:100]}")
                                logger.info(f"📝 FAILED 상태 업데이트 완료: {sig['name']}")
                            except Exception as db_e:
                                logger.error(f"❌ FAILED 상태 DB 업데이트 실패: {db_e}")
                            
                            fail_msg = (
                                f"⚠️ *[주문 실패]*\n"
                                f"━━━━━━━━━━━━━━\n"
                                f"📌 *종목:* {sig['name']} ({sig['ticker']})\n"
                                f"🔢 *수량:* {qty:,}주\n"
                                f"📡 *사유:* {fail_reason[:200]}\n"
                                f"━━━━━━━━━━━━━━"
                            )
                            try:
                                send_telegram_message(fail_msg)
                            except Exception as tg_e:
                                logger.warning(f"실패 알림 전송 실패: {tg_e}")
                        
                        time.sleep(2) # 레이트 리밋 방지 (2초 대기)
                
                # 4. 계좌 요약 (주문 후 갱신)
                try:
                    api = BrokerFactory.get_broker("KIWOOM")
                    api.get_account_summary()
                except:
                    pass
                wait_sec = 30  # 장중 대기시간 단축 (실시간 trailing stop 감시 정확도 향상)
            else:
                logger.info(f"💤 장외 대기 중... ({now.strftime('%H:%M:%S')})")
                wait_sec = 600
        except Exception as e:
            logger.error(f"❌ 루프 오류: {e}")
            wait_sec = 10
            
        time.sleep(wait_sec)

if __name__ == "__main__":
    run_trade()
