import os
import sys
import time
import logging
import sqlite3
import pytz
from datetime import datetime

# 1. 경로 및 시간 설정
BASE_DIR = "/root/workspace/gemini-quant/stock_trader"
DB_PATH = os.path.join(BASE_DIR, "logs/system_monitor.db")
KST = pytz.timezone('Asia/Seoul')

# 2. 로깅 (KST 시간대 적용)
def kst_converter(*args):
    return datetime.now(KST).timetuple()

logging.Formatter.converter = kst_converter
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("AutoTrader")

# 3. 경량 API 코어 임포트
sys.path.append(BASE_DIR)
from kiwoom_api_core import KiwoomApiCore

def run_trade():
    logger.info("🚀 [KST 정밀 매매 엔진] 가동 시작")
    api = KiwoomApiCore(mode="MOCK")
    
    while True:
        try:
            now = datetime.now(KST)
            # 장중 시간 (09:00 ~ 15:30)
            if now.weekday() < 5 and (9 <= now.hour < 15 or (now.hour == 15 and now.minute < 30)):
                # 1. 매매 신호 로드
                with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()
                    cursor.execute("SELECT ticker, name, action, quantity, reason FROM trade_signals WHERE status = 'PENDING'")
                    signals = cursor.fetchall()
                
                if signals:
                    logger.info(f"🧠 {len(signals)}개의 매매 신호 처리 시작")
                    for sig in signals:
                        qty = int(sig['quantity']) if sig.get('quantity') and int(sig['quantity']) > 0 else 1
                        logger.info(f"🛒 주문 요청: {sig['name']} ({sig['ticker']}) - 수량: {qty}주")
                        res = api.place_order(sig['ticker'], qty, 0, side=sig['action'])
                        
                        logger.info(f"📡 API 응답: {res}")
                        
                        # 응답 성공 조건 완화 및 명시적 체크
                        is_success = False
                        if res:
                            # 다양한 응답 형식 대응 (MOCK 서버 특성 고려)
                            if res.get("return_code") == 0 or res.get("status") == "success" or res.get("rt_cd") == "0":
                                is_success = True
                        
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
                            except Exception as db_e:
                                logger.error(f"❌ DB 업데이트 실패: {db_e}")
                        else:
                            logger.warning(f"⚠️ 주문 실패 또는 응답 형식 불일치: {sig['name']}")
                        
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
