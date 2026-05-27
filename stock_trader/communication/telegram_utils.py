import os
import requests
from dotenv import load_dotenv

# .env 파일 로드 (부모 디렉토리 또는 현재 디렉토리)
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
load_dotenv(env_path)

TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

def send_telegram_message(message):
    """텔레그램으로 메시지를 전송합니다."""
    if not TOKEN or not CHAT_ID:
        print("❌ [Telegram] 토큰 또는 채팅 ID가 설정되지 않았습니다.")
        return False
        
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    
    try:
        res = requests.post(url, data=data, timeout=10)
        if res.status_code == 200:
            return True
        else:
            print(f"❌ [Telegram] 전송 실패: {res.text}")
            return False
    except Exception as e:
        print(f"❌ [Telegram] 오류 발생: {e}")
        return False

def send_telegram_photo(photo_path, caption=None):
    """텔레그램으로 사진을 전송합니다."""
    if not TOKEN or not CHAT_ID:
        print("❌ [Telegram] 토큰 또는 채팅 ID가 설정되지 않았습니다.")
        return False
        
    url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
    data = {"chat_id": CHAT_ID}
    if caption:
        data["caption"] = caption
        data["parse_mode"] = "Markdown"
        
    try:
        with open(photo_path, 'rb') as photo:
            files = {'photo': photo}
            res = requests.post(url, data=data, files=files, timeout=20)
            if res.status_code == 200:
                return True
            else:
                print(f"❌ [Telegram] 사진 전송 실패: {res.text}")
                return False
    except Exception as e:
        print(f"❌ [Telegram] 사진 전송 오류 발생: {e}")
        return False

if __name__ == "__main__":
    # 테스트 실행
    send_telegram_message("🚀 [System] 텔레그램 모듈이 정상적으로 로드되었습니다.")
