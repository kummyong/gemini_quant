import os
import requests
import json
from datetime import datetime
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# 경로 설정 및 .env 로드
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(BASE_DIR, ".env")
load_dotenv(env_path)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# MCP 서버 생성
mcp = FastMCP("TelegramManager")

@mcp.tool()
def telegram_send_message(text: str) -> str:
    """텔레그램 사용자에게 메시지를 전송합니다."""
    if not TOKEN or not CHAT_ID:
        return "❌ 오류: 토큰 또는 채팅 ID가 설정되지 않았습니다."
    
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    
    try:
        res = requests.post(url, data=data, timeout=10)
        if res.status_code == 200:
            return f"✅ 메시지 전송 성공: {text[:20]}..."
        else:
            return f"❌ 전송 실패 (Status {res.status_code}): {res.text}"
    except Exception as e:
        return f"❌ 예외 발생: {str(e)}"

@mcp.tool()
def telegram_get_updates(limit: int = 5) -> list:
    """텔레그램 사용자로부터 온 최신 메시지들을 읽어옵니다."""
    if not TOKEN:
        return ["❌ 오류: 토큰이 설정되지 않았습니다."]
    
    url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
    params = {"limit": limit, "offset": -limit} # 최신 메시지만 가져오기
    
    try:
        res = requests.get(url, params=params, timeout=10).json()
        if res.get("ok"):
            messages = []
            for item in res.get("result", []):
                msg = item.get("message", {})
                if str(msg.get("chat", {}).get("id")) == CHAT_ID:
                    messages.append({
                        "time": datetime.fromtimestamp(msg.get("date")).isoformat(),
                        "text": msg.get("text", ""),
                        "from": msg.get("from", {}).get("first_name", "User")
                    })
            return messages
        else:
            return [f"❌ 조회 실패: {res}"]
    except Exception as e:
        return [f"❌ 예외 발생: {str(e)}"]

if __name__ == "__main__":
    mcp.run()
