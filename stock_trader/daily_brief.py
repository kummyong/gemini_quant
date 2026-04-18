import os
import sys
import logging
from datetime import datetime
from telegram_utils import send_telegram_message

def send_daily_brief(summary):
    """아침 뉴스 브리핑을 전송합니다."""
    today = datetime.now().strftime("%Y-%m-%d")
    brief_msg = f"☀️ *[오늘의 증시 브리핑 - {today}]*\n\n"
    brief_msg += summary
    brief_msg += "\n\n🚀 *오늘도 성투하세요! Gemini-Quant 드림.*"
    
    return send_telegram_message(brief_msg)

if __name__ == "__main__":
    # 이 스크립트는 Gemini가 뉴스를 요약한 내용을 인자로 받아 실행될 예정입니다.
    if len(sys.argv) > 1:
        content = sys.argv[1]
        send_daily_brief(content)
    else:
        print("❌ 브리핑 내용이 없습니다.")
