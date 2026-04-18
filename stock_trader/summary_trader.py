import sqlite3
import os
from datetime import datetime

BASE_DIR = "/root/workspace/gemini-quant/stock_trader"
DB_PATH = os.path.join(BASE_DIR, "logs/system_monitor.db")

def get_summary():
    if not os.path.exists(DB_PATH):
        print(f"❌ DB 파일이 존재하지 않습니다: {DB_PATH}")
        return

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        print(f"--- [Gemini-Quant Real-time Status] ---")
        print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        # 1. 최신 계좌 요약 (account_summary)
        cursor.execute("SELECT * FROM account_summary ORDER BY timestamp DESC LIMIT 1")
        account = cursor.fetchone()
        if account:
            print(f"Total Assets: {account['total_assets']:,} | Cash: {account['cash']:,} ({account['cash_ratio']}%)")
        else:
            print("⚠️ 계좌 요약 정보가 없습니다.")

        # 2. 포트폴리오 현황 (portfolio_status)
        cursor.execute("SELECT * FROM portfolio_status ORDER BY prft_rt DESC")
        portfolio = cursor.fetchall()
        print(f"Portfolio: {len(portfolio)} stocks")
        for p in portfolio:
            # prft_rt가 문자열 형태로 저장되어 있을 수 있으므로 float 변환 시도
            try:
                profit = float(p['prft_rt'])
                print(f"  - {p['stk_nm']}({p['stk_cd']}): {profit:+.2f}% | Qty: {p['rmnd_qty']:,}")
            except (ValueError, TypeError):
                print(f"  - {p['stk_nm']}({p['stk_cd']}): {p['prft_rt']}% | Qty: {p['rmnd_qty']:,}")

        # 3. 오늘 매매 이력 (trade_history)
        print(f"\nToday's Activity:")
        today_date = datetime.now().strftime("%Y-%m-%d")
        cursor.execute("""
            SELECT * FROM trade_history 
            WHERE timestamp LIKE ? 
            ORDER BY timestamp DESC
        """, (f"{today_date}%",))
        trades = cursor.fetchall()
        
        if trades:
            for t in trades:
                print(f"  ✅ [{t['timestamp']}] {t['side']} {t['name']}({t['ticker']}) {t['quantity']}주 @ {t['price']:,}원 ({t['reason']})")
        else:
            print("  No trades executed today yet.")

        # 4. 대기 중인 신호 (trade_signals)
        cursor.execute("SELECT * FROM trade_signals WHERE status = 'PENDING'")
        signals = cursor.fetchall()
        if signals:
            print(f"\nPending Signals:")
            for s in signals:
                print(f"  [{s['action']}] {s['name']}({s['ticker']}): {s['reason']}")

        conn.close()
    except Exception as e:
        print(f"❌ DB 조회 오류: {e}")

if __name__ == "__main__":
    get_summary()
