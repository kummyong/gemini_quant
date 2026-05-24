import sqlite3
import os
from datetime import datetime
from config import DB_PATH
from telegram_utils import send_telegram_message


def get_summary():
    if not os.path.exists(DB_PATH):
        print(f"❌ DB 파일이 존재하지 않습니다: {DB_PATH}")
        return

    try:
        with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
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

    except Exception as e:
        print(f"❌ DB 조회 오류: {e}")

def send_daily_summary_to_telegram():
    """장 마감 후 일일 포트폴리오 요약을 텔레그램으로 전송"""
    if not os.path.exists(DB_PATH):
        print(f"❌ DB 파일이 존재하지 않습니다: {DB_PATH}")
        return
    
    try:
        with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            lines = ["📋 *[일일 마감 보고]*", f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}", "━━━━━━━━━━━━━━"]
            
            # 1. 계좌 요약
            cursor.execute("SELECT * FROM account_summary ORDER BY timestamp DESC LIMIT 1")
            account = cursor.fetchone()
            if account:
                lines.append(f"💰 총자산: {account['total_assets']:,}원")
                lines.append(f"💵 현금: {account['cash']:,}원 ({account['cash_ratio']}%)")
                lines.append("")
            
            # 2. 포트폴리오 현황
            cursor.execute("SELECT * FROM portfolio_status WHERE rmnd_qty > 0 ORDER BY prft_rt DESC")
            portfolio = cursor.fetchall()
            if portfolio:
                lines.append(f"📂 *보유종목 ({len(portfolio)}종목)*")
                for p in portfolio:
                    try:
                        profit = float(p['prft_rt'])
                        emoji = "📈" if profit >= 0 else "📉"
                        lines.append(f"  {emoji} {p['stk_nm']}: {profit:+.2f}%")
                    except (ValueError, TypeError):
                        lines.append(f"  ⚪ {p['stk_nm']}: N/A")
                lines.append("")
            
            # 3. 오늘 매매 이력
            today_date = datetime.now().strftime("%Y-%m-%d")
            cursor.execute(
                "SELECT * FROM trade_history WHERE timestamp LIKE ? ORDER BY timestamp DESC",
                (f"{today_date}%",)
            )
            trades = cursor.fetchall()
            if trades:
                lines.append(f"🔄 *금일 매매 ({len(trades)}건)*")
                for t in trades:
                    side_emoji = "🟢" if t['side'] == 'BUY' else "🔴"
                    side_text = "매수" if t['side'] == 'BUY' else "매도"
                    lines.append(f"  {side_emoji} {side_text} {t['name']} {t['quantity']:,}주")
                lines.append("")
            else:
                lines.append("🔄 금일 매매 없음")
                lines.append("")
            
            # 4. 대기 중인 시그널
            cursor.execute("SELECT * FROM trade_signals WHERE status = 'PENDING'")
            pending = cursor.fetchall()
            if pending:
                lines.append(f"⏳ *미처리 시그널 ({len(pending)}건)*")
                for s in pending:
                    lines.append(f"  · [{s['action']}] {s['name']}: {s['reason'][:50]}")
            
            lines.append("━━━━━━━━━━━━━━")
            
            msg = "\n".join(lines)
            send_telegram_message(msg)
            print("일일 요약 텔레그램 전송 완료")
    except Exception as e:
        print(f"일일 요약 텔레그램 전송 실패: {e}")

if __name__ == "__main__":
    get_summary()

