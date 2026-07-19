import sqlite3
import os
from datetime import datetime
from stock_trader.config import DB_PATH
from stock_trader.communication.telegram_utils import send_telegram_message


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

def _fetch_live_account_data():
    """활성 브로커 전체의 실계좌 데이터를 조회하고, 그 시점에 DB 포지션도 대사합니다.
    반환: {"total_assets", "cash", "holdings": [...]} — 전 브로커 조회 실패 시 None"""
    from stock_trader.broker.broker_factory import BrokerFactory
    from stock_trader.core import reconciler
    from stock_trader.data.db_repository import DbRepository

    repo = None
    try:
        repo = DbRepository(DB_PATH)
    except Exception as e:
        print(f"⚠️ DB 레포지토리 초기화 실패 (대사/스냅샷 생략): {e}")

    total_assets = 0.0
    total_cash = 0.0
    holdings = []
    any_success = False

    for b_name in BrokerFactory.get_active_brokers():
        try:
            broker = BrokerFactory.get_broker(b_name)
            summary = broker.get_account_summary()
            if not summary:
                print(f"⚠️ [{b_name}] 계좌 조회 응답 없음")
                continue

            evlt = float(summary.get("tot_evlt_amt", 0) or 0)
            cash = float(summary.get("prsm_dpst_aset_amt", 0) or 0)
            parsed = reconciler._parse_broker_holdings(summary)

            # 전부 0이면 어댑터의 API 실패 폴백 응답으로 간주 (진짜 빈 계좌도 예수금은 있음)
            if evlt <= 0 and cash <= 0 and not parsed:
                print(f"⚠️ [{b_name}] 계좌 응답이 전부 0 — API 실패로 간주하고 스킵")
                continue

            total_assets += evlt + cash
            total_cash += cash
            for ticker, h in parsed.items():
                holdings.append({"broker": b_name, "ticker": ticker, **h})
            any_success = True

            # 보고 시점에 DB 포지션을 실계좌 기준으로 대사 (텔레그램 중복 알림은 생략)
            if repo is not None:
                try:
                    reconciler.reconcile(b_name, broker, repo, notify=False)
                except Exception as rec_e:
                    print(f"⚠️ [{b_name}] 포지션 대사 실패: {rec_e}")
        except Exception as e:
            print(f"⚠️ [{b_name}] 실계좌 조회 실패: {e}")

    if not any_success:
        return None

    # 계좌 스냅샷 기록 (전략 엔진의 DB 폴백 등에서 최신값으로 활용됨)
    if repo is not None:
        try:
            ratio = round(total_cash / total_assets * 100, 1) if total_assets > 0 else 0.0
            repo.save_account_snapshot(int(total_assets), int(total_cash), ratio)
        except Exception as e:
            print(f"⚠️ 계좌 스냅샷 저장 실패: {e}")

    holdings.sort(key=lambda h: h["profit_rate"], reverse=True)
    return {"total_assets": total_assets, "cash": total_cash, "holdings": holdings}


def send_daily_summary_to_telegram():
    """장 마감 후 일일 포트폴리오 요약을 텔레그램으로 전송.
    계좌·보유종목은 실계좌(API)가 진실이므로 API를 우선 조회하고,
    실패한 경우에만 DB 사본을 사용하며 그 사실을 보고에 명시합니다."""
    if not os.path.exists(DB_PATH):
        print(f"❌ DB 파일이 존재하지 않습니다: {DB_PATH}")
        return

    try:
        lines = ["📋 *[일일 마감 보고]*", f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}", "━━━━━━━━━━━━━━"]

        live = None
        try:
            live = _fetch_live_account_data()
        except Exception as e:
            print(f"⚠️ 실계좌 데이터 수집 실패 — DB 사본으로 대체: {e}")

        if live:
            # 1·2. 계좌 요약 + 보유종목 (실계좌 기준)
            ratio = (live['cash'] / live['total_assets'] * 100) if live['total_assets'] > 0 else 0.0
            lines.append(f"💰 총자산: {live['total_assets']:,.0f}원")
            lines.append(f"💵 현금: {live['cash']:,.0f}원 ({ratio:.1f}%)")
            lines.append("")
            if live["holdings"]:
                lines.append(f"📂 *보유종목 ({len(live['holdings'])}종목)*")
                for h in live["holdings"]:
                    emoji = "📈" if h["profit_rate"] >= 0 else "📉"
                    lines.append(f"  {emoji} {h['name']}: {h['profit_rate']:+.2f}% ({h['quantity']:,}주)")
            else:
                lines.append("📂 보유종목 없음")
            lines.append("")
        else:
            # 폴백: DB 사본 (스테일 가능성을 보고에 명시)
            lines.append("⚠️ _실계좌 조회 실패 — 아래는 DB 기록 기준(실계좌와 다를 수 있음)_")
            with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM account_summary ORDER BY timestamp DESC LIMIT 1")
                account = cursor.fetchone()
                if account:
                    lines.append(f"💰 총자산: {account['total_assets']:,}원 (기록시각: {account['timestamp']})")
                    lines.append(f"💵 현금: {account['cash']:,}원 ({account['cash_ratio']}%)")
                lines.append("")
                cursor.execute("SELECT * FROM portfolio_status WHERE rmnd_qty > 0 ORDER BY prft_rt DESC")
                portfolio = cursor.fetchall()
                if portfolio:
                    lines.append(f"📂 *보유종목 ({len(portfolio)}종목, DB 기준)*")
                    for p in portfolio:
                        try:
                            profit = float(p['prft_rt'])
                            emoji = "📈" if profit >= 0 else "📉"
                            lines.append(f"  {emoji} {p['stk_nm']}: {profit:+.2f}%")
                        except (ValueError, TypeError):
                            lines.append(f"  ⚪ {p['stk_nm']}: N/A")
                lines.append("")

        # 3·4. 매매 이력·대기 신호 (시스템 기록이 원본이므로 DB에서 조회)
        with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

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
                    price_str = f" @ {t['price']:,}원" if t['price'] else ""
                    lines.append(f"  {side_emoji} {side_text} {t['name']} {t['quantity']:,}주{price_str}")
                lines.append("")
            else:
                lines.append("🔄 금일 매매 없음")
                lines.append("")

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

