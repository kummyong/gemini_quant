import os
import sys
import time
import logging
import sqlite3
import pytz
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
from stock_trader.broker.broker_factory import BrokerFactory

# 캐시 및 안전장치 전역 변수
ATR_CACHE = {}
last_circuit_breaker_check = 0.0
market_halt = False
last_holdings_monitoring_time = 0.0

def get_cached_atr_pct(ticker: str) -> float:
    """종목의 14일 ATR% 값을 계산하고 캐시합니다. (일 1회 실시간 조회용)"""
    if ticker in ATR_CACHE:
        return ATR_CACHE[ticker]
    try:
        import FinanceDataReader as fdr
        import pandas as pd
        from datetime import datetime, timedelta
        # 30일치 데이터로 14일 ATR 계산
        df = fdr.DataReader(ticker, start=(datetime.now(KST) - timedelta(days=40)).strftime('%Y-%m-%d'))
        if not df.empty and len(df) >= 15:
            closes = df['Close']
            highs = df['High']
            lows = df['Low']
            tr = pd.concat([highs - lows, (highs - closes.shift(1)).abs(), (lows - closes.shift(1)).abs()], axis=1).max(axis=1)
            atr = tr.rolling(window=14).mean().iloc[-1]
            atr_pct = (atr / closes.iloc[-1]) * 100
            ATR_CACHE[ticker] = atr_pct
            logger.info(f"📋 Cached ATR for {ticker}: {atr_pct:.2f}%")
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
                with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()
                    cursor.execute("SELECT stk_cd, max_profit_rate FROM portfolio_status WHERE broker_id = ?", (b_name,))
                    for row in cursor.fetchall():
                        db_holdings[row["stk_cd"]] = float(row["max_profit_rate"]) if row["max_profit_rate"] is not None else 0.0
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
                    with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
                        conn.execute("""
                            INSERT INTO portfolio_status (broker_id, stk_cd, stk_nm, rmnd_qty, pur_pric, cur_prc, prft_rt, max_profit_rate, last_updated)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
                            ON CONFLICT(broker_id, stk_cd) DO UPDATE SET
                                rmnd_qty = excluded.rmnd_qty,
                                pur_pric = excluded.pur_pric,
                                cur_prc = excluded.cur_prc,
                                prft_rt = excluded.prft_rt,
                                max_profit_rate = excluded.max_profit_rate,
                                last_updated = excluded.last_updated
                        """, (b_name, ticker, name, qty, purchase_price, current_price, profit, new_max))
                        conn.commit()
                except Exception as db_e:
                    logger.error(f"❌ 감시 중 DB 업데이트 에러: {db_e}")
                
                # 2. Hard Stop Loss 판정 (기준: -5%)
                hard_stop_loss_limit = -5.0
                if profit <= hard_stop_loss_limit:
                    logger.warning(f"🚨 [개별 손절] {name} 절대 손절선 도달! (수익률: {profit:.2f}%) 즉시 전량 청산 주문을 집행합니다.")
                    trigger_realtime_sell(b_name, api, ticker, name, qty, f"실시간 Hard Stop Loss 작동 (수익률: {profit:.2f}%)")
                    continue
                
                # 3. Trailing Stop (Chandelier Exit) 판정
                if new_max >= 2.0:
                    atr_pct = get_cached_atr_pct(ticker)
                    # Chandelier Exit Stop 기준: 고점 수익률 대비 ATR의 1.5배수 하락 시 (최소 2%, 최대 5% 제한)
                    stop_threshold = max(2.0, min(5.0, 1.5 * atr_pct))
                    
                    if (new_max - profit) >= stop_threshold:
                        logger.warning(f"🎯 [트레일링스탑] {name} Chandelier Exit 작동! (고점: {new_max:.2f}%, 현재: {profit:.2f}%, 하락폭: {new_max-profit:.2f}%, 기준: {stop_threshold:.2f}%) 즉시 전량 청산합니다.")
                        trigger_realtime_sell(b_name, api, ticker, name, qty, f"실시간 Trailing Stop 작동 (고점: {new_max:.2f}%, 현재: {profit:.2f}%, 하락폭: {new_max-profit:.2f}%p)")
                        
        except Exception as e:
            logger.error(f"❌ 보유 종목 실시간 감시 루프 에러: {e}")

def trigger_realtime_sell(broker_id: str, api, ticker: str, name: str, qty: int, reason: str):
    """실시간 청산 매도 주문 전송 및 알림"""
    try:
        logger.info(f"🛒 [{broker_id}] 실시간 매도 주문 실행: {name} - {qty}주")
        res = api.place_order(ticker, qty, 0, side="SELL")
        logger.info(f"📡 실시간 매도 응답: {res}")
        
        is_success = False
        if res:
            if res.get("return_code") == 0 or res.get("status") == "success" or res.get("rt_cd") == "0":
                is_success = True
                
        if is_success:
            # DB 기록
            with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
                # portfolio_status 수량 0으로 업데이트
                conn.execute("UPDATE portfolio_status SET rmnd_qty = 0, last_updated = datetime('now', 'localtime') WHERE broker_id = ? AND stk_cd = ?", (broker_id, ticker))
                # history 기록
                conn.execute("INSERT INTO trade_history (ticker, name, side, quantity, price, amt, reason) VALUES (?, ?, ?, ?, ?, ?, ?)",
                             (ticker, name, "SELL", qty, 0, 0, f"[실시간감시] {reason}"))
                conn.commit()
                
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

def run_trade():
    global last_circuit_breaker_check, market_halt
    logger.info("🚀 [KST 정밀 매매 엔진] 가동 시작 (다중 증권사 지원)")
    
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
                                market_halt = True
                                halt_msg = f"🚨 *[시장 급락 서킷 브레이커 발동]*\nKOSPI: {kospi_chg:+.2f}%, KOSDAQ: {kosdaq_chg:+.2f}%\n모든 신규 매수를 중단합니다!"
                                logger.critical(halt_msg)
                                try:
                                    send_telegram_message(halt_msg)
                                except Exception as tg_e:
                                    logger.warning(f"텔레그램 알림 실패: {tg_e}")
                        else:
                            if market_halt:
                                market_halt = False
                                resume_msg = f"✅ *[시장 지수 회복 - 매매 해제]*\nKOSPI: {kospi_chg:+.2f}%, KOSDAQ: {kosdaq_chg:+.2f}%\n신규 매수제한을 해제합니다."
                                logger.info(resume_msg)
                                try:
                                    send_telegram_message(resume_msg)
                                except Exception as tg_e:
                                    logger.warning(f"텔레그램 알림 실패: {tg_e}")
                
                # 2. 실시간 보유 종목 Trailing Stop / Hard Stop 감시
                monitor_holdings_and_stops()
                
                # 3. 매매 신호 로드
                with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()
                    cursor.execute("SELECT id, ticker, name, action, quantity, reason, broker_id FROM trade_signals WHERE status = 'PENDING'")
                    signals = cursor.fetchall()
                
                if signals:
                    logger.info(f"🧠 {len(signals)}개의 매매 신호 분석 중...")
                    
                    broker_apis = {}
                    broker_cash = {}
                    
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
                        available_cash = broker_cash[broker_id]
                        
                        if not api:
                            logger.warning(f"⚠️ [{broker_id}] API를 불러올 수 없어 {sig['name']} 주문 스킵")
                            continue

                        qty = int(sig['quantity']) if sig['quantity'] is not None and int(sig['quantity']) > 0 else 1
                        
                        # BUY 주문 시 추가 필터링 및 스마트 주문 집행
                        if sig['action'] == 'BUY':
                            if market_halt:
                                logger.warning(f"⏭️ [매수 보류] {sig['name']}: 시장 서킷 브레이커 작동 중으로 매수 스킵")
                                continue
                                
                            # 스마트 분할 매수 / 눌림목 대기 로직 적용
                            try:
                                current_price = api.get_current_price(sig['ticker'])
                                if current_price <= 0:
                                    logger.warning(f"⚠️ [{sig['name']}] 현재가 조회 실패로 일단 주문 진행")
                                    current_price = 0.0
                                    
                                target_price = None
                                if "TARGET_PRICE:" in sig['reason']:
                                    try:
                                        target_price = float(sig['reason'].split("TARGET_PRICE:")[1].split()[0])
                                    except:
                                        pass
                                
                                # 스마트 매수 집행 판단:
                                # 1. 타겟가(볼밴 하단) 이하로 내려오는 눌림목일 때 매수진입
                                # 2. 단, 장 마감 임박(14시 30분 이후)에는 타겟 가격에 도달하지 못했더라도 금일 포트폴리오 구성을 완료하기 위해 즉시 매수 집행
                                if target_price and current_price > 0:
                                    is_target_reached = current_price <= target_price * 1.005 # 0.5% 안전마진 이내
                                    is_late_afternoon = now.hour > 14 or (now.hour == 14 and now.minute >= 30)
                                    
                                    if not is_target_reached and not is_late_afternoon:
                                        logger.info(f"⏳ [눌림목 대기] {sig['name']}: 현재가 {current_price:,.0f}원 > 목표가 {target_price:,.0f}원 (14시 30분 이후 자동체결 대기)")
                                        continue
                                    elif is_late_afternoon and not is_target_reached:
                                        logger.info(f"⏰ [시간 만료 즉시 집행] {sig['name']}: 14:30 경과로 현재가 {current_price:,.0f}원에 매수 집행 (목표가 {target_price:,.0f}원)")
                                    else:
                                        logger.info(f"🎯 [목표가 터치 진입] {sig['name']}: 현재가 {current_price:,.0f}원 <= 목표가 {target_price:,.0f}원 만족")
                                
                                # 예수금 최종 검증
                                if current_price > 0:
                                    estimated_cost = qty * current_price
                                    if estimated_cost > available_cash:
                                        old_qty = qty
                                        qty = int(available_cash * 0.95 / current_price)  # 95% 안전마진
                                        if qty <= 0:
                                            logger.warning(f"⚠️ [{broker_id}] 예수금 부족으로 매수 스킵: {sig['name']} (필요: {estimated_cost:,.0f}원, 예수금: {available_cash:,.0f}원)")
                                            with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
                                                conn.execute("UPDATE trade_signals SET status = 'CANCELLED', reason = reason || ' [예수금 부족]' WHERE id = ?", (sig['id'],))
                                                conn.commit()
                                            continue
                                        else:
                                            logger.info(f"📉 [{broker_id}] 수량 조정: {sig['name']} {old_qty}주 → {qty}주 (예수금 제한)")
                                            
                            except Exception as price_e:
                                logger.warning(f"⚠️ 스마트 매수 조건 검증 실패 ({sig['ticker']}): {price_e}")
                        
                        logger.info(f"🛒 [{broker_id}] 주문 요청: {sig['name']} ({sig['ticker']}) - {sig['action']} {qty}주")
                        res = api.place_order(sig['ticker'], qty, 0, side=sig['action'])
                        
                        logger.info(f"📡 API 응답: {res}")
                        
                        is_success = False
                        if res:
                            if res.get("return_code") == 0 or res.get("status") == "success" or res.get("rt_cd") == "0":
                                is_success = True
                        
                        if is_success:
                            logger.info(f"✅ 주문 성공 판정: {sig['name']}")
                            try:
                                with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
                                    conn.execute("UPDATE trade_signals SET status = 'DONE' WHERE id = ?", (sig['id'],))
                                    conn.execute("INSERT INTO trade_history (ticker, name, side, quantity, price, amt, reason) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                                 (sig['ticker'], sig['name'], sig['action'], qty, 0, 0, sig['reason']))
                                    conn.commit()
                                logger.info(f"✅ DB 기록 완료: {sig['name']}")
                                
                                # 텔레그램 매매 알림 전송
                                action_emoji = "🟢" if sig['action'] == "BUY" else "🔴"
                                action_text = "매수" if sig['action'] == "BUY" else "매도"
                                trade_msg = (
                                    f"{action_emoji} *[{action_text} 체결]*\n"
                                    f"━━━━━━━━━━━━━━\n"
                                    f"📌 *종목:* {sig['name']} ({sig['ticker']})\n"
                                    f"🔢 *수량:* {qty:,}주\n"
                                    f"💵 *단가:* 시장가 주문 (체결가 DB확인 필요)\n"
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
                                with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
                                    conn.execute("UPDATE trade_signals SET status = 'CANCELLED', reason = reason || ' [실패: ' || ? || ']' WHERE id = ?",
                                                 (fail_reason[:100], sig['id']))
                                    conn.commit()
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
