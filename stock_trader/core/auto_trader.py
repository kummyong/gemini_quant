import os
import sys
import time
import logging
import pytz
import json
import re
from datetime import datetime, timedelta
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
from stock_trader.data.db_repository import DbRepository, is_hard_stop_lockout

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
    "MARKET_REGIME_CODE": 0.0,    # 전략 엔진이 기록한 시장 국면 (1=BULL, 0=NEUTRAL, -1=BEAR)
}

# 종가 베팅(15:00 신호 생성) 체제에서 당일 미체결 신호가 다음날 아침 스테일 가격으로
# 집행되는 것을 막는 신선도 한도 (시간)
SIGNAL_MAX_AGE_HOURS = 4.0

def _regime_from_code(code: float) -> str:
    if code >= 0.5:
        return "BULL"
    if code <= -0.5:
        return "BEAR"
    return "NEUTRAL"

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
    프로세스가 재시작되어도 락아웃 상태가 유지됩니다.

    단, 전략 엔진이 기록한 글로벌 하드스탑 락아웃([HARD_STOP] 접두사)은
    장중 서킷 브레이커가 덮어쓰거나 해제할 수 없습니다 — 해제는 전략 엔진의
    쿨다운+BULL 국면 조건에서만 이뤄집니다."""
    global market_halt
    try:
        state = get_repo().get_market_lockout()
        if is_hard_stop_lockout(state):
            market_halt = True
            if not active:
                logger.warning("⛔ 글로벌 하드스탑 락아웃 활성 — 서킷 브레이커 해제 요청을 무시하고 매수 제한을 유지합니다.")
            return
    except Exception as e:
        logger.warning(f"⚠️ market_lockout 상태 확인 실패: {e}")

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
    """종목의 14일 ATR% 값을 계산하고 당일 기준으로 캐시합니다.
    인메모리 캐시(ATR_CACHE)는 프로세스 재시작 시 사라지므로, DB(atr_cache 테이블)에도
    영속 저장해 워치독 재시작 직후에도 당일 재계산 없이 즉시 읽어올 수 있게 한다."""
    today = datetime.now(KST).strftime('%Y-%m-%d')
    cached = ATR_CACHE.get(ticker)
    if cached and cached[0] == today:
        return cached[1]

    try:
        db_cached = get_repo().get_atr_value(ticker, today)
        if db_cached is not None:
            ATR_CACHE[ticker] = (today, db_cached)
            return db_cached
    except Exception as e:
        logger.warning(f"⚠️ ATR DB 캐시 조회 실패 ({ticker}): {e}")

    try:
        import pandas as pd
        repo = get_repo()
        df = repo.get_recent_ohlcv(ticker, limit=40)

        if df.empty or len(df) < 15:
            # (주의) datetime/timedelta는 파일 상단에서만 import할 것 — 함수 내부에서 다시
            # import하면 그 순간부터 함수 전체에서 해당 이름이 지역 변수로 취급되어,
            # 이 분기보다 앞서 나온 `datetime.now(KST)` 참조가 UnboundLocalError로 깨진다
            # (실제로 이 버그가 있었고, 트레일링 스탑 판정이 조용히 실패하고 있었음).
            import FinanceDataReader as fdr
            df = fdr.DataReader(ticker, start=(datetime.now(KST) - timedelta(days=40)).strftime('%Y-%m-%d'))

        if not df.empty and len(df) >= 15:
            closes = df['Close']
            highs = df['High']
            lows = df['Low']
            tr = pd.concat([highs - lows, (highs - closes.shift(1)).abs(), (lows - closes.shift(1)).abs()], axis=1).max(axis=1)
            atr = tr.rolling(window=14).mean().iloc[-1]
            atr_pct = (atr / closes.iloc[-1]) * 100
            ATR_CACHE[ticker] = (today, atr_pct)
            try:
                get_repo().save_atr_value(ticker, today, atr_pct)
            except Exception as db_e:
                logger.warning(f"⚠️ ATR DB 캐시 저장 실패 ({ticker}): {db_e}")
            logger.info(f"📋 Cached ATR for {ticker} (using DB/FDR): {atr_pct:.2f}%")
            return atr_pct
    except Exception as e:
        logger.warning(f"⚠️ ATR 계산 실패 ({ticker}): {e}")
    return 3.0  # 기본값

def _get_intraday_indices_naver() -> dict:
    """네이버 금융의 실시간 지수 폴링 API 호출 (1차 소스, 진짜 장중 실시간)."""
    import requests
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    url = "https://polling.finance.naver.com/api/realtime/domestic/index/KOSPI,KOSDAQ"
    res = requests.get(url, headers=headers, timeout=5)
    if res.status_code != 200:
        return {}
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


def _get_intraday_indices_fdr() -> dict:
    """FinanceDataReader 폴백 (2차 소스). 네이버 비공식 API가 막히거나 구조가 바뀌었을 때 대비.
    FDR의 최신 행이 '오늘' 날짜가 아니면(주말/데이터 지연 등) 장중 실시간이 아니므로
    신뢰할 수 없다고 보고 빈 dict를 반환한다 — 오래된 등락률을 오늘 것처럼 잘못 판단하면
    서킷브레이커가 엉뚱하게 발동하거나 반대로 놓칠 수 있다."""
    import FinanceDataReader as fdr
    today_str = datetime.now(KST).strftime('%Y-%m-%d')
    result = {}
    for code, label in [("KS11", "KOSPI"), ("KQ11", "KOSDAQ")]:
        try:
            df = fdr.DataReader(code)
            if df.empty:
                continue
            last_date = df.index[-1].strftime('%Y-%m-%d')
            if last_date != today_str:
                logger.warning(f"⚠️ FDR {label} 최신 데이터가 오늘({today_str})이 아님(마지막: {last_date}) — 폴백에서 제외")
                continue
            result[label] = float(df['Change'].iloc[-1]) * 100.0
        except Exception as e:
            logger.warning(f"⚠️ FDR {label} 지수 조회 실패: {e}")
    return result


def get_intraday_market_indices() -> dict:
    """코스피/코스닥 당일 변동률을 조회합니다. 네이버 실시간 API를 우선 시도하고,
    실패하면(구조 변경, 일시 장애 등) FinanceDataReader로 폴백합니다.
    두 소스 모두 실패하면 빈 dict를 반환하며, 호출부는 이번 주기의 서킷브레이커
    체크를 건너뛰고 다음 주기에 재시도한다(단일 장애점으로 인한 무한 정지 방지)."""
    try:
        result = _get_intraday_indices_naver()
        if result:
            return result
        logger.warning("⚠️ 네이버 실시간 지수 응답이 비어있어 FDR로 폴백합니다.")
    except Exception as e:
        logger.warning(f"⚠️ 네이버 실시간 지수 조회 실패: {e} — FDR로 폴백합니다.")

    try:
        result = _get_intraday_indices_fdr()
        if result:
            return result
    except Exception as e:
        logger.warning(f"⚠️ FDR 지수 폴백 실패: {e}")

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
                logger.exception(f"❌ 감시 중 DB 조회 에러: {db_e}")
                
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
                    logger.exception(f"❌ 감시 중 DB 업데이트 에러: {db_e}")
                
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

                # 3. Trailing Stop (Chandelier Exit) 판정 — 전략 엔진과 동일한 RiskManager 기준 사용.
                # 엔진이 DB에 기록한 시장 국면(MARKET_REGIME_CODE)을 반영해 BULL 이완/BEAR 타이트가
                # 실시간 감시에도 일관되게 적용된다 (이중 기준 불일치 방지).
                if new_max >= 2.0:
                    hp = get_hyperparams()
                    atr_pct = get_cached_atr_pct(ticker)
                    from stock_trader.core.risk_manager import RiskManager
                    rm = RiskManager(
                        trailing_min_drop=hp["TRAILING_MIN_DROP"],
                        trailing_max_drop=hp["TRAILING_MAX_DROP"],
                        chandelier_atr_mult=hp["CHANDELIER_ATR_MULT"],
                        market_regime=_regime_from_code(hp["MARKET_REGIME_CODE"]),
                    )
                    _, stop_threshold = rm.calculate_stops(atr_pct)
                    
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
            logger.exception(f"❌ 보유 종목 실시간 감시 루프 에러: {e}")
 
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
            exit_reason = "hard_stop"
            try:
                if features:
                    exit_reason = json.loads(features).get("exit_reason", exit_reason)
            except Exception:
                pass

            # 주문 접수 성공 != 전량 체결 확정. 계좌를 재조회해 실제 잔여 수량을 확인한 뒤에만
            # 포지션을 정리한다 (부분체결이면 잔여 물량의 peak/entry 스냅샷을 보존).
            filled_qty, is_full_close = apply_sell_result(broker_id, ticker, name, qty, api, est_price, exit_reason)
            record_position_outcome(broker_id, ticker, name, est_price, exit_reason, filled_qty, position_closed=is_full_close)

            get_repo().save_trade_history(ticker, name, "SELL", filled_qty, int(est_price), int(est_price * filled_qty), f"[실시간감시] {reason}", features)
                
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
        logger.exception(f"❌ trigger_realtime_sell 도중 오류: {e}")

def parse_entry_signal_type(reason: str) -> str:
    """매수 신호의 reason 문자열에서 전략 유형 라벨을 추출합니다.
    예: "[역추세] RSI: 28.0, ..." -> "역추세", "ETF 신규진입" -> "ETF 신규진입" (대괄호 없으면 그대로)."""
    if not reason:
        return "UNKNOWN"
    m = re.match(r'^\[(.+?)\]', reason)
    return m.group(1) if m else reason.split(',')[0].strip()[:30]

def record_position_outcome(broker_id: str, ticker: str, fallback_name: str, exit_price: float,
                            exit_reason: str, sold_qty: int, position_closed: bool):
    """포지션의 진입 스냅샷을 조회해 실현 손익을 trade_outcomes에 기록합니다.
    진입 스냅샷이 없으면(과거 데이터이거나 브로커 대사로 발견된 미기록 포지션 등) 조용히 건너뜁니다."""
    try:
        entry = get_repo().get_position_entry(broker_id, ticker)
        if not entry.get("entry_date") or not entry.get("entry_price"):
            logger.info(f"ℹ️ [{ticker}] 진입 스냅샷이 없어 trade_outcomes 기록 생략 (학습 기반 미적용 대상)")
            return
        entry_price = float(entry["entry_price"])
        if entry_price <= 0:
            return
        return_pct = (exit_price - entry_price) / entry_price * 100.0
        try:
            entry_dt = datetime.fromisoformat(entry["entry_date"])
            holding_days = max(0, (datetime.now(KST).replace(tzinfo=None) - entry_dt.replace(tzinfo=None)).days)
        except Exception:
            holding_days = 0
        get_repo().record_trade_outcome(
            broker_id=broker_id, ticker=ticker, name=entry.get("stk_nm") or fallback_name,
            entry_date=entry["entry_date"], entry_price=entry_price,
            entry_signal_type=entry.get("entry_signal_type"), entry_features=entry.get("entry_features"),
            exit_price=exit_price, exit_reason=exit_reason, quantity=sold_qty,
            return_pct=return_pct, holding_days=holding_days, position_closed=position_closed
        )
        logger.info(f"📊 [학습기반] {ticker} 실현결과 기록 (진입:{entry_price:,.0f} → 청산:{exit_price:,.0f}, 수익률:{return_pct:+.2f}%, 완전청산:{position_closed})")
    except Exception as e:
        logger.warning(f"⚠️ [{ticker}] trade_outcomes 기록 실패: {e}")

def confirm_sell_fill(api, ticker: str):
    """매도 주문 접수 성공 응답 직후 계좌를 재조회해 실제 잔여 수량을 확인합니다.
    '주문 접수 성공'이 '요청 수량 전량 체결'을 보장하지 않는다 — 특히 트레일링/하드스탑이
    발동하는 패닉성 매도 상황일수록 호가 물량 부족으로 인한 부분체결 가능성이 높아진다.
    반환: (잔여수량, 계좌상의 보유 행 dict) — 계좌에 더 이상 없으면 (0, {}).
          재조회 자체가 실패하면 (None, None) — 호출부가 기존 낙관적 처리(전량 매도 간주)로
          폴백할 수 있게 구분해서 반환한다."""
    try:
        time.sleep(1.5)  # 시장가 체결 반영 대기 (estimate_fill_price와 동일 패턴)
        summary = api.get_account_summary()
        holdings = (summary or {}).get("acnt_evlt_remn_indv_tot") or []
        for h in holdings:
            if str(h.get("stk_cd", "")).replace("A", "") == ticker:
                return int(h.get("rmnd_qty", 0)), h
        return 0, {}  # 보유 목록에 더 이상 없음 = 전량 청산됨
    except Exception as e:
        logger.warning(f"⚠️ 매도 후 잔여수량 확인 실패 ({ticker}) — 낙관적 전량청산으로 처리: {e}")
        return None, None

def apply_sell_result(broker_id: str, ticker: str, name: str, requested_qty: int, api,
                      est_price: float, exit_reason: str):
    """매도 성공 응답 이후 실제 잔여 수량을 확인해 DB를 정확히 반영합니다.
    - 확인 결과 잔여 0(전량 체결): 기존과 동일하게 mark_holding_sold로 정리.
    - 잔여 수량 있음(부분체결): 포지션을 삭제하지 않고 실제 잔여 수량으로 갱신하며,
      트레일링 스탑 고점(max_profit_rate)과 진입 스냅샷(entry_*)은 그대로 보존한다 —
      부분체결됐다고 나머지 물량의 리스크 관리 상태를 초기화할 이유가 없다.
    - 재조회 자체가 실패: 기존 낙관적 처리(요청 수량 전량 매도 간주)로 폴백한다.
    반환: (실제 체결된 것으로 간주할 수량, 완전청산 여부) — 학습 기반 기록에 그대로 사용."""
    remaining_qty, holding_row = confirm_sell_fill(api, ticker)

    if remaining_qty is None or remaining_qty <= 0:
        get_repo().mark_holding_sold(broker_id, ticker)
        return requested_qty, True

    filled_qty = max(0, requested_qty - remaining_qty)
    logger.warning(
        f"⚠️ [{name}] 매도 부분체결 감지: 요청 {requested_qty}주 중 {remaining_qty}주 잔여 "
        f"(체결 {filled_qty}주) — 잔여 물량의 트레일링 고점/진입 스냅샷은 보존합니다."
    )
    try:
        stored_max = get_repo().get_max_profit_rates(broker_id).get(ticker, 0.0)
        new_pur_pric = float(holding_row.get("pchs_amt", 0.0)) / max(1, remaining_qty)
        new_cur_prc = float(holding_row.get("evlt_amt", 0.0)) / max(1, remaining_qty)
        prft_rt_raw = holding_row.get("prft_rt", 0.0)
        new_prft_rt = float(prft_rt_raw) if prft_rt_raw not in (None, "") else 0.0
        get_repo().update_portfolio_holding(
            stk_cd=ticker, stk_nm=name, rmnd_qty=remaining_qty,
            pur_pric=new_pur_pric, cur_prc=new_cur_prc, prft_rt=new_prft_rt,
            max_profit_rate=stored_max, broker_id=broker_id)
    except Exception as e:
        logger.exception(f"❌ [{ticker}] 부분체결 후 DB 갱신 실패: {e}")
    return (filled_qty if filled_qty > 0 else requested_qty), False

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
                    return float(h.get("pur_amt", h.get("pchs_amt", 0.0))) / q
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
            logger.exception(f"❌ 리스크 감시 스레드 오류: {e}")
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

                    # 전략 엔진이 장중에 기록한 글로벌 하드스탑 락아웃을 메모리에 동기화
                    try:
                        if not market_halt and is_hard_stop_lockout(get_repo().get_market_lockout()):
                            market_halt = True
                            logger.warning("⛔ 글로벌 하드스탑 락아웃 감지 (전략 엔진 기록) — 신규 매수를 중단합니다.")
                    except Exception as sync_e:
                        logger.warning(f"⚠️ 락아웃 상태 동기화 실패: {sync_e}")

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
                                # 하드스탑 락아웃이면 set_market_halt(False)가 거부되어 market_halt가 유지됨
                                if not market_halt:
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

                            # 신호 신선도 검사: 종가 베팅(15:00) 신호가 당일 장 마감까지 체결되지 못하고
                            # 다음날 아침 시가에 스테일 가격으로 집행되는 것을 방지
                            try:
                                created_at = datetime.strptime(str(sig['created_at'])[:19], "%Y-%m-%d %H:%M:%S")
                                age_hours = (now.replace(tzinfo=None) - created_at).total_seconds() / 3600.0
                                if age_hours > SIGNAL_MAX_AGE_HOURS:
                                    logger.warning(f"⏭️ [신호 만료] {sig['name']}: 생성 후 {age_hours:.1f}시간 경과 (한도 {SIGNAL_MAX_AGE_HOURS}h) — 매수 취소")
                                    get_repo().cancel_signal(sig['id'], f"신선도 만료 ({age_hours:.1f}h)")
                                    continue
                            except (KeyError, IndexError, ValueError, TypeError):
                                pass  # created_at 파싱 불가 시 기존 동작 유지
                                
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

                                # 학습 기반: 진입 시 팩터 스냅샷 기록 / 청산 시 진입 대비 실현 결과 기록
                                if sig['action'] == 'BUY':
                                    get_repo().record_position_entry(
                                        broker_id=broker_id, stk_cd=sig['ticker'], stk_nm=sig['name'],
                                        entry_price=fill_price, entry_signal_type=parse_entry_signal_type(sig['reason']),
                                        entry_features=sig['features'])
                                else:
                                    exit_reason = "unknown"
                                    try:
                                        if sig['features']:
                                            exit_reason = json.loads(sig['features']).get("exit_reason", exit_reason)
                                    except Exception:
                                        pass
                                    # 오버슈팅 부분 익절(러너 전환)은 전략상 의도된 부분매도라 그대로 두고,
                                    # 그 외(하드스탑/트레일링/교체/글로벌손절)는 전량 청산을 기대하는 주문이므로
                                    # 접수 성공만으로 단정하지 않고 계좌 재조회로 실제 잔여 수량을 확인한다.
                                    if exit_reason == "overshooting_partial":
                                        record_position_outcome(broker_id, sig['ticker'], sig['name'], fill_price,
                                                                exit_reason, qty, position_closed=False)
                                    else:
                                        filled_qty, is_full_close = apply_sell_result(
                                            broker_id, sig['ticker'], sig['name'], qty, api, fill_price, exit_reason)
                                        record_position_outcome(broker_id, sig['ticker'], sig['name'], fill_price,
                                                                exit_reason, filled_qty, position_closed=is_full_close)

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
                                logger.exception(f"❌ DB 업데이트 실패: {db_e}")
                        else:
                            fail_reason = str(res.get('return_msg', '')) if res else 'No response'
                            logger.warning(f"⚠️ 주문 실패: {sig['name']} - {fail_reason}")
                            try:
                                get_repo().cancel_signal(sig['id'], f"실패: {fail_reason[:100]}")
                                logger.info(f"📝 FAILED 상태 업데이트 완료: {sig['name']}")
                            except Exception as db_e:
                                logger.exception(f"❌ FAILED 상태 DB 업데이트 실패: {db_e}")
                            
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
            logger.exception(f"❌ 루프 오류: {e}")
            wait_sec = 10
            
        time.sleep(wait_sec)

if __name__ == "__main__":
    run_trade()
