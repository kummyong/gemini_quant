import os
import time, sys
import json
import requests
import sqlite3
import fcntl
import logging
from datetime import datetime
from dotenv import load_dotenv

# 경로 설정
BASE_DIR = "/root/workspace/gemini-quant/stock_trader"
LOG_DIR = os.path.join(BASE_DIR, 'logs')
if not os.path.exists(LOG_DIR): os.makedirs(LOG_DIR)
LOG_FILE = os.path.join(LOG_DIR, "telegram_listener.log")

# 전역 설정 로드
env_path = os.path.join(BASE_DIR, ".env")
load_dotenv(env_path)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = str(os.getenv("TELEGRAM_CHAT_ID")).strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DB_PATH = os.path.join(LOG_DIR, "system_monitor.db")

# agent_skills 모듈 로드
sys.path.append(BASE_DIR)
from agent_skills import SYSTEM_TOOLS, skill_router

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler(LOG_FILE, encoding='utf-8'), logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("TelegramAgent")

# 중복 실행 방지 락
LOCK_FILE = os.path.join(LOG_DIR, "telegram_listener.lock")
fp = open(LOCK_FILE, 'w')
try:
    fcntl.lockf(fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
except IOError:
    logger.error("⚠️ Telegram Listener is already running.")
    sys.exit(0)

# 가용 모델 및 API 버전 조합 (우선순위 순)
AI_CONFIGS = [
    {"version": "v1beta", "model": "models/gemini-flash-latest"},
    {"version": "v1", "model": "models/gemini-flash-latest"},
    {"version": "v1beta", "model": "models/gemini-pro-latest"},
    {"version": "v1", "model": "models/gemini-pro-latest"}
]

def get_ai_decision(text):
    """Gemini API를 사용하여 의도 분석 및 도구 호출 결정 (v1/v1beta 규격 준수)"""
    logger.info(f"📥 User Input: {text}")
    
    if not GEMINI_API_KEY:
        logger.error("❌ GEMINI_API_KEY not found in .env")
        return None, {}, "에러: AI API 키가 설정되지 않았습니다."

    last_error = "의도를 파악할 수 없습니다."
    
    for config in AI_CONFIGS:
        ver = config["version"]
        model = config["model"]
        try:
            url = f"https://generativelanguage.googleapis.com/{ver}/{model}:generateContent?key={GEMINI_API_KEY}"
            
            # 최신 API 규격 페이로드 (tools 필드 포함)
            payload = {
                "contents": [
                    {
                        "role": "user",
                        "parts": [{"text": f"현재 시간: {datetime.now().isoformat()}\n사용자 메시지: \"{text}\"\n\n위 메시지에 대해 적절한 시스템 도구를 호출하고 실행해줘."}]
                    }
                ],
                "tools": [
                    {
                        "function_declarations": SYSTEM_TOOLS
                    }
                ],
                "tool_config": {
                    "function_calling_config": {
                        "mode": "AUTO"
                    }
                }
            }
            
            logger.info(f"🔍 Trying {ver} with {model}...")
            res = requests.post(url, json=payload, timeout=20)
            res_json = res.json()
            
            if res.status_code == 200:
                candidates = res_json.get('candidates', [])
                if not candidates:
                    logger.warning(f"⚠️ No candidates in response from {model}")
                    continue
                
                part = candidates[0].get('content', {}).get('parts', [{}])[0]
                
                if "functionCall" in part:
                    fn_call = part["functionCall"]
                    func_name = fn_call["name"]
                    args = fn_call.get("args", {})
                    logger.info(f"🤖 AI Decision ({model}): Function Call -> {func_name}({args})")
                    return func_name, args, None
                
                elif "text" in part:
                    ai_reply = part["text"].strip()
                    logger.info(f"🤖 AI Decision ({model}): Text Reply -> {ai_reply}")
                    return "CHITCHAT", {"reply": ai_reply}, None
            
            else:
                err_msg = res_json.get('error', {}).get('message', 'Unknown Error')
                logger.error(f"AI API Error ({res.status_code}) for {model}: {err_msg}")
                last_error = err_msg
                continue

        except Exception as e:
            logger.error(f"🚨 Exception with {model}: {e}")
            last_error = str(e)
            continue

    return None, {}, f"모든 AI 모델 호출에 실패했습니다. (마지막 에러: {last_error})"

def send_telegram(message):
    """텔레그램 메시지 전송 유틸리티"""
    if not message: return
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
        requests.post(url, data=payload, timeout=10)
    except Exception as e:
        logger.error(f"Telegram Send Error: {e}")

def execute_and_reply(func_name, args):
    """도구를 실행하고 결과를 사용자에게 요약하여 보고"""
    if func_name == "CHITCHAT":
        send_telegram(args.get("reply", "안녕하세요! 무엇을 도와드릴까요?"))
        return

    # 1. 실제 도구 실행 (skill_router 활용)
    logger.info(f"⚡ Executing Skill: {func_name}")
    raw_result = skill_router(func_name, args)
    
    # 2. 실행 결과를 AI에게 다시 보내서 '친절한 요약' 생성
    try:
        summary_prompt = f"사용자의 요청: {func_name}({args})\n실행 결과: {json.dumps(raw_result, ensure_ascii=False)}\n\n위 결과를 바탕으로 사용자에게 친절하고 전문적인 말투로 실행 완료 보고를 해줘."
        # 검증된 최신 모델로 요약 요청
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={GEMINI_API_KEY}"
        payload = {"contents": [{"parts": [{"text": summary_prompt}]}]}
        res = requests.post(url, json=payload, timeout=10)
        summary = res.json()['candidates'][0]['content']['parts'][0]['text'].strip()
    except:
        summary = f"✅ 실행 완료: {func_name}\n결과: {raw_result}"

    send_telegram(f"🤖 *[Gemini-Quant 실행 보고서]*\n\n{summary}")

def main():
    logger.info("🚀 차세대 지능형 텔레그램 에이전트 가동 (Tool-Calling Edition)")
    offset = None
    
    # DB 초기화 (필요시)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS scheduled_tasks (id INTEGER PRIMARY KEY, scheduled_at TEXT, content TEXT, intent TEXT, params TEXT, status TEXT, result_msg TEXT)")

    while True:
        try:
            # 텔레그램 업데이트 확인
            url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
            r = requests.get(url, params={"offset": offset, "timeout": 20}).json()
            
            if r.get("ok"):
                for item in r.get("result", []):
                    offset = item["update_id"] + 1
                    msg = item.get("message", {})
                    text = msg.get("text")
                    
                    if text:
                        # AI에게 판단 위임
                        func_name, args, error = get_ai_decision(text)
                        
                        if error:
                            send_telegram(f"❌ {error}")
                        else:
                            # 즉시 실행
                            execute_and_reply(func_name, args)
                            
            time.sleep(1)
        except Exception as e:
            logger.error(f"Main Loop Error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
