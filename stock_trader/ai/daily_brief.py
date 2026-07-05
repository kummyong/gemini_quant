import os
import sys
import logging
from datetime import datetime
from stock_trader.communication.telegram_utils import send_telegram_message

def send_daily_brief(summary):
    """아침 뉴스 브리핑을 전송합니다."""
    today = datetime.now().strftime("%Y-%m-%d")
    brief_msg = f"☀️ *[오늘의 증시 브리핑 - {today}]*\n\n"
    brief_msg += summary
    
    try:
        from stock_trader.config import ACTIVE_STRATEGY_PROFILE, ETF_TREND, DB_PATH
        if ACTIVE_STRATEGY_PROFILE == ETF_TREND:
            from stock_trader.data.db_repository import DbRepository
            import stock_trader.core.signals as sig
            repo = DbRepository(DB_PATH)
            lockout = repo.get_market_lockout()
            status_str = "활성" if lockout.get("active") else "비활성"
            brief_msg += f"\n\n🔒 *시장 락아웃 상태*: {status_str}"
            if lockout.get("active"):
                brief_msg += f" (사유: {lockout.get('reason')})"
            
            # Check Cooldown status
            cooldowns = repo.get_stopped_positions(ETF_TREND)
            active_cooldowns = []
            sig_params = sig.StrategyParams()
            for c in cooldowns:
                ticker = c["ticker"]
                df_hist = repo.get_recent_ohlcv(ticker, limit=150)
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
                brief_msg += f"\n⏳ *쿨다운 제한 중인 종목*: {', '.join(active_cooldowns)}"
            else:
                brief_msg += f"\n⏳ *쿨다운 제한 중인 종목*: 없음"
    except Exception as e:
        logging.error(f"데일리 브리프에 ETF 상태 정보 취합 실패: {e}")
        
    brief_msg += "\n\n🚀 *오늘도 성투하세요! Gemini-Quant 드림.*"
    return send_telegram_message(brief_msg)

if __name__ == "__main__":
    # 이 스크립트는 Gemini가 뉴스를 요약한 내용을 인자로 받아 실행될 예정입니다.
    if len(sys.argv) > 1:
        content = sys.argv[1]
        send_daily_brief(content)
    else:
        print("❌ 브리핑 내용이 없습니다.")
