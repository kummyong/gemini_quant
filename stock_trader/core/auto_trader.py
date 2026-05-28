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

def run_trade():
    logger.info("🚀 [KST 정밀 매매 엔진] 가동 시작 (다중 증권사 지원)")
    
    while True:
        try:
            now = datetime.now(KST)
            from stock_trader.core.korean_market_calendar import is_market_holiday
            
            # 장중 시간 (09:00 ~ 15:30) 이고 휴일이 아닌 경우
            is_trading_time = (9 <= now.hour < 15 or (now.hour == 15 and now.minute < 30))
            if not is_market_holiday(now) and is_trading_time:
                # 1. 매매 신호 로드
                with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()
                    cursor.execute("SELECT ticker, name, action, quantity, reason, broker_id FROM trade_signals WHERE status = 'PENDING'")
                    signals = cursor.fetchall()
                
                if signals:
                    logger.info(f"🧠 {len(signals)}개의 매매 신호 처리 시작")
                    
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
                        
                        # BUY 주문 시 예수금 검증
                        if sig['action'] == 'BUY' and available_cash > 0:
                            try:
                                import FinanceDataReader as fdr
                                price_df = fdr.DataReader(sig['ticker'])
                                if not price_df.empty:
                                    current_price = float(price_df['Close'].iloc[-1])
                                    estimated_cost = qty * current_price
                                    if estimated_cost > available_cash:
                                        old_qty = qty
                                        qty = int(available_cash * 0.95 / current_price)  # 95% 안전마진
                                        if qty <= 0:
                                            logger.warning(f"⚠️ [{broker_id}] 예수금 부족으로 매수 스킵: {sig['name']} (필요: {estimated_cost:,.0f}원, 예수금: {available_cash:,.0f}원)")
                                            # CANCELLED 처리
                                            with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
                                                conn.execute("UPDATE trade_signals SET status = 'CANCELLED', reason = reason || ' [예수금 부족]' WHERE ticker = ? AND status = 'PENDING'", (sig['ticker'],))
                                                conn.commit()
                                            continue
                                        else:
                                            logger.info(f"📉 [{broker_id}] 수량 조정: {sig['name']} {old_qty}주 → {qty}주 (예수금 제한)")
                            except Exception as price_e:
                                logger.warning(f"⚠️ 현재가 조회 실패 ({sig['ticker']}): {price_e}")
                        
                        logger.info(f"🛒 [{broker_id}] 주문 요청: {sig['name']} ({sig['ticker']}) - {sig['action']} {qty}주")
                        res = api.place_order(sig['ticker'], qty, 0, side=sig['action'])
                        
                        logger.info(f"📡 API 응답: {res}")
                        
                        # 응답 성공 조건 완화 및 명시적 체크
                        is_success = False
                        if res:
                            # 다양한 응답 형식 대응 (MOCK 서버 특성 고려)
                            if res.get("return_code") == 0 or res.get("status") == "success" or res.get("rt_cd") == "0":
                                is_success = True
                        
                        # 1.1 텔레그램 임포트 로컬 수행 또는 상단 수행 (여기서는 상단에 수행)
                        
                        if is_success:
                            logger.info(f"✅ 주문 성공 판정: {sig['name']}")
                            # DB 기록
                            try:
                                with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
                                    conn.execute("UPDATE trade_signals SET status = 'DONE' WHERE ticker = ?", (sig['ticker'],))
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
                            # 실패한 신호를 CANCELLED로 마킹하여 무한 재시도 방지
                            try:
                                with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
                                    conn.execute("UPDATE trade_signals SET status = 'CANCELLED', reason = reason || ' [실패: ' || ? || ']' WHERE ticker = ? AND status = 'PENDING'",
                                                 (fail_reason[:100], sig['ticker']))
                                    conn.commit()
                                logger.info(f"📝 FAILED 상태 업데이트 완료: {sig['name']}")
                            except Exception as db_e:
                                logger.error(f"❌ FAILED 상태 DB 업데이트 실패: {db_e}")
                            # 주문 실패 텔레그램 알림
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
                
                # 2. 계좌 요약 (주문 후 갱신)
                api.get_account_summary()
                wait_sec = 60
            else:
                logger.info(f"💤 장외 대기 중... ({now.strftime('%H:%M:%S')})")
                wait_sec = 600
        except Exception as e:
            logger.error(f"❌ 루프 오류: {e}")
            wait_sec = 10
            
        time.sleep(wait_sec)

if __name__ == "__main__":
    run_trade()
