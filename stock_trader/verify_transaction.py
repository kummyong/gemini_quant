import sqlite3
import os
import sys
import logging
from datetime import datetime

# 기존 경로 설정 유지
BASE_DIR = "/root/workspace/gemini-quant/stock_trader"
DB_PATH = os.path.join(BASE_DIR, "logs/system_monitor.db")

# 테스트를 위해 AutoTrader 클래스의 execute_order 로직만 추출하여 테스트 함수 구성
def test_execute_order_logic(simulate_error=False):
    ticker = "999999"
    stock_name = "테스트종목"
    side = "BUY"
    quantity = 10
    price = 5000
    reason = "트랜잭션 테스트"
    
    print(f"\n--- [{'장애' if simulate_error else '정상'}] 테스트 시작 ---")
    
    # 0. 초기 상태 설정 (테스트용 신호 삽입)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM trade_signals WHERE ticker = ?", (ticker,))
    cursor.execute("INSERT INTO trade_signals (ticker, name, action, status) VALUES (?, ?, ?, 'PENDING')", (ticker, stock_name, side))
    cursor.execute("DELETE FROM trade_history WHERE ticker = ?", (ticker,))
    conn.commit()
    
    if simulate_error:
        # 강제로 에러를 유발하기 위해 테이블 이름을 변경하거나 삭제 (테스트 후 복구)
        cursor.execute("ALTER TABLE trade_history RENAME TO trade_history_bak")
        conn.commit()
        print("⚠️  테스트를 위해 trade_history 테이블을 일시적으로 변경했습니다. (에러 유도)")

    # 1. 주문 성공 가정 (API 호출 부분 생략 및 DB 로직만 실행)
    print(f"🛒 주문 체결 완료 (가정). DB 기록 및 상태 전이 시작...")
    
    # [Step 3 로직 복사본]
    db_conn = sqlite3.connect(DB_PATH)
    success = False
    try:
        db_conn.execute("BEGIN TRANSACTION")
        db_cursor = db_conn.cursor()
        
        # INSERT 시도 (에러 발생 지점)
        db_cursor.execute("""
            INSERT INTO trade_history (ticker, name, side, quantity, price, amt, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (ticker, stock_name, side, quantity, price, quantity * price, reason))
        
        db_cursor.execute("UPDATE trade_signals SET status = 'DONE' WHERE ticker = ?", (ticker,))
        
        db_conn.commit()
        print("✅ DB 업데이트 성공 (Committed)")
        success = True
    except Exception as e:
        db_conn.rollback()
        print(f"❌ DB 업데이트 실패 및 롤백 완료 (Rollbacked). 에러: {e}")
        success = False
    finally:
        db_conn.close()

    # 2. 결과 검증
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    if simulate_error:
        # 테이블 복구
        cursor.execute("ALTER TABLE trade_history_bak RENAME TO trade_history")
        conn.commit()
        
    cursor.execute("SELECT status FROM trade_signals WHERE ticker = ?", (ticker,))
    status = cursor.fetchone()[0]
    
    if simulate_error:
        if status == 'PENDING' and not success:
            print("✨ 검증 결과: [성공] 에러 발생 시 데이터가 롤백되어 PENDING 상태를 유지합니다.")
        else:
            print(f"🚨 검증 결과: [실패] 롤백이 제대로 되지 않았습니다. (상태: {status})")
    else:
        if status == 'DONE' and success:
            print("✨ 검증 결과: [성공] 정상 상황에서 데이터가 성공적으로 커밋되었습니다.")
        else:
            print(f"🚨 검증 결과: [실패] 커밋이 되지 않았습니다. (상태: {status})")
    
    conn.close()

if __name__ == "__main__":
    # 1. 정상 케이스 테스트
    test_execute_order_logic(simulate_error=False)
    # 2. 롤백 케이스 테스트
    test_execute_order_logic(simulate_error=True)
