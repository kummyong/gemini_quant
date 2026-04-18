import logging
import os
import sys
import time
import json
import threading
import fcntl
import requests
import re
import sqlite3
from datetime import datetime
import pytz

# 경로 설정
BASE_DIR = "/root/workspace/gemini-quant/stock_trader"
sys.path.append(BASE_DIR)
LOG_DIR = os.path.join(BASE_DIR, "logs")
if not os.path.exists(LOG_DIR): os.makedirs(LOG_DIR)
DB_PATH = os.path.join(LOG_DIR, "system_monitor.db")

from telegram_utils import TOKEN, CHAT_ID
from local_intent_router import get_local_decision, get_top_n_decisions, router
from agent_skills import skill_router, save_training_feedback
from trainer import retrain_model

# 타임존 및 로깅 설정
KST = pytz.timezone('Asia/Seoul')
def kst_converter(*args): return datetime.now(KST).timetuple()
logging.Formatter.converter = kst_converter

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "telegram_listener.log"), encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("GeminiQuant")

# 한글 이름 매핑
INTENT_NAMES = {
    "get_account_summary": "💰 내 계좌 상황 요약 보고",
    "get_balance": "📂 보유 종목 수익률 확인",
    "get_stock_price": "🔍 현재 주식 가격 조회",
    "get_daily_chart": "📈 주식 차트(그래프) 보기",
    "place_order": "📦 주식 사고 팔기(주문)",
    "get_system_status": "📱 휴대폰 및 시스템 상태",
    "search_history": "🎞️ 예전 대화 기록 찾기",
    "list_google_events": "📅 내 일정(캘린더) 확인",
    "switch_ai_model": "🤖 인공지능 모델 변경",
    "CHITCHAT": "💬 일상적인 대화"
}

# 전역 상태 관리
CONTEXT_FILE = os.path.join(BASE_DIR, "context.json")
last_interaction = {
    "text": None, "intent": None, "params": {}, "timestamp": 0, "state": None, 
    "all_candidates": [], "current_options": [], "page": 0
}

def save_context():
    try:
        with open(CONTEXT_FILE, "w", encoding="utf-8") as f:
            json.dump(last_interaction, f, ensure_ascii=False, indent=2)
    except Exception as e: logger.error(f"🚨 Save Context Error: {e}")

def load_context():
    global last_interaction
    try:
        if os.path.exists(CONTEXT_FILE):
            with open(CONTEXT_FILE, "r", encoding="utf-8") as f:
                last_interaction = json.load(f)
            if time.time() - last_interaction.get("timestamp", 0) > 300:
                last_interaction["state"] = None
    except Exception as e: logger.error(f"🚨 Load Context Error: {e}")

def judge_feedback(text):
    text = text.lower().strip()
    if text in ["1", "2", "3"]: return "SELECT", text
    pos_patterns = [r'^[ㅇy]+$', r'^(맞아|응|어|그래|오냐|맞음|네|예|좋아|오케이|ok|정답|그거야)']
    if any(re.search(p, text) for p in pos_patterns): return "POSITIVE", None
    neg_patterns = [r'^[ㄴn]+$', r'^(아니|틀려|아냐|그거아냐|잘못|오답|패스|다음|다른|ㄴㄴ|몰라|아니야|틀렸어)']
    if any(re.search(p, text) for p in neg_patterns): return "NEGATIVE", None
    return "UNKNOWN", None

def get_local_db_best_match(text):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT raw_text, actual_label FROM training_data ORDER BY created_at DESC LIMIT 100")
        rows = cursor.fetchall()
        conn.close()
        best_intent, max_overlap = None, 0
        for raw_text, label in rows:
            overlap = len(set(text) & set(raw_text))
            if overlap > max_overlap: max_overlap, best_intent = overlap, label
        if max_overlap > 2: return best_intent
    except: pass
    return None

def trigger_instant_learning():
    def run():
        logger.info("🧠 [Instant Learning] 즉시 재학습 시작...")
        try:
            retrain_model()
            import joblib
            MODEL_PATH = os.path.join(BASE_DIR, "logs/intent_model.pkl")
            if os.path.exists(MODEL_PATH):
                router.model = joblib.load(MODEL_PATH)
                logger.info("✨ [Instant Learning] 모델 갱신 완료.")
        except Exception as e: logger.error(f"❌ [Instant Learning] 실패: {e}")
    threading.Thread(target=run, daemon=True).start()

def execute_and_report(func_name, params):
    logger.info(f"⚡ [Execute] {func_name}({params})")
    try:
        result = skill_router(func_name, params)
    except Exception as ex:
        logger.error(f"🚨 Skill Router Error: {ex}")
        result = f"오류 발생: {ex}"
    return f"🤖 [처리 보고]\n\n🔹 기능: {INTENT_NAMES.get(func_name, func_name)}\n🔹 결과: {result}"

def merge_params(old_params, new_params):
    merged = old_params.copy()
    for k, v in new_params.items():
        if v: merged[k] = v
    return merged

def get_ai_teacher_decision(text):
    from agent_skills import SYSTEM_TOOLS, AVAILABLE_MODELS
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    if not GEMINI_API_KEY: return None, {}, 0
    
    # 설정된 모델명 가져오기 (기본값: gemini-2.0-flash)
    model_name = os.getenv("GEMINI_MODEL_NAME")
    if not model_name:
        model_name = AVAILABLE_MODELS[0] if AVAILABLE_MODELS else "models/gemini-2.0-flash"
    
    # URL에서 사용하기 위해 models/ 접두사 제거 (API endpoint 형식에 맞게)
    clean_model_name = model_name.replace("models/", "")
    
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model_name}:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{"parts": [{"text": f"Msg: \"{text}\"\nIntent/Params extraction."}]}],
            "tools": [{"function_declarations": SYSTEM_TOOLS}],
            "tool_config": {"function_calling_config": {"mode": "ANY"}}
        }
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code == 429: return "LIMIT_REACHED", {}, 0
        res_json = res.json()
        candidates = res_json.get('candidates', [])
        if candidates:
            part = candidates[0].get('content', {}).get('parts', [{}])[0]
            if "functionCall" in part:
                fn = part["functionCall"]
                return fn["name"], fn.get("args", {}), 1.0
    except: pass
    return None, {}, 0

def process_and_reply(text: str):
    global last_interaction
    now = time.time()
    text_raw = text.strip()
    
    # [0] 슬래시 명령어(/) 직접 처리
    if text_raw.startswith("/"):
        parts = text_raw.split(maxsplit=1)
        cmd = parts[0][1:].lower() # '/' 제외한 명령어만 추출
        params_str = parts[1] if len(parts) > 1 else ""
        
        # 특수 명령어 처리
        if cmd == "help":
            return skill_router("get_help", {})
        elif cmd == "switch_ai_model":
            from agent_skills import switch_ai_model
            return switch_ai_model(params_str)
            
        # 그 외 모든 슬래시 명령어는 skill_router로 직접 전달
        return execute_and_report(cmd, {})

    
    # [1] 피드백 및 상태 확인
    feedback_type, choice_val = judge_feedback(text_raw)
    state = last_interaction.get("state")
    
    # [2] 이전 컨텍스트 로드 및 병합 준비
    # 5분 이내라면 아까 그 종목/수량 등을 유지함
    is_recent = (now - last_interaction.get("timestamp", 0)) < 300
    prev_params = last_interaction.get("params", {}) if is_recent else {}
    
    # [Case A] 확답 대기 상태 처리 ("응/아니")
    if state == "WAITING_CONFIRMATION":
        if feedback_type == "POSITIVE":
            chosen_intent = last_interaction["intent"]
            # 긍정 답변 시 파라미터는 아까 추출했던 것 그대로 사용
            save_training_feedback(last_interaction["text"], chosen_intent, chosen_intent, 1.0)
            trigger_instant_learning()
            params = last_interaction.get("params", {})
            last_interaction.update({"state": None, "timestamp": now})
            save_context()
            return f"✅ 확인했습니다! '{INTENT_NAMES.get(chosen_intent)}'을 실행합니다.\n\n" + execute_and_report(chosen_intent, params)
        
        elif feedback_type == "NEGATIVE":
            last_interaction.update({"state": None, "timestamp": now})
            save_context()
            return "😥 죄송합니다. 다시 한 번 정확하게 말씀해 주시겠어요?"

    # [Case B] 번호 선택 상태 처리
    if state == "WAITING_SELECTION" and feedback_type == "SELECT":
        choice_idx = int(choice_val) - 1
        candidates = last_interaction.get("current_options", [])
        if choice_idx < len(candidates):
            chosen_intent = candidates[choice_idx][0]
            save_training_feedback(last_interaction["text"], "UNKNOWN", chosen_intent, 1.0)
            trigger_instant_learning()
            params = last_interaction.get("params", {})
            last_interaction.update({"intent": chosen_intent, "state": None, "timestamp": now})
            save_context()
            return f"💡 '{INTENT_NAMES.get(chosen_intent)}' 기능을 학습했습니다.\n\n" + execute_and_report(chosen_intent, params)

    # [Case C] 새로운 명령 분석
    _, current_params, confidence = get_local_decision(text_raw)
    accumulated_params = merge_params(prev_params, current_params)
    
    # 1) 로컬 신뢰도 높음 (즉시 실행)
    local_intent, _, local_conf = get_local_decision(text_raw)
    if local_conf >= 0.9: # 임계값을 높여 더 확실할 때만 로컬 엔진 사용
        last_interaction.update({"text": text_raw, "intent": local_intent, "params": accumulated_params, "timestamp": now, "state": None})
        save_context()
        return execute_and_report(local_intent, accumulated_params)
    
    # 2) 신뢰도 낮음 -> AI 스승(Gemini 2.0) 개입 (가장 정확한 방법)
    logger.info(f"🤔 확신도 낮음({local_conf:.2f}). AI 스승(Gemini)에게 물어봅니다...")
    ai_intent, ai_params, _ = get_ai_teacher_decision(text_raw)
    
    if ai_intent and ai_intent not in ["LIMIT_REACHED", "UNKNOWN"]:
        save_training_feedback(text_raw, local_intent, ai_intent, 1.0)
        trigger_instant_learning()
        final_params = merge_params(accumulated_params, ai_params)
        last_interaction.update({"text": text_raw, "intent": ai_intent, "params": final_params, "timestamp": now, "state": None})
        save_context()
        return f"🤖 [AI 판단] '{INTENT_NAMES.get(ai_intent, ai_intent)}' 기능을 실행합니다.\n\n" + execute_and_report(ai_intent, final_params)
    
    # 3) AI도 모르거나 에러난 경우 최선의 로컬 매칭 시도
    db_intent = get_local_db_best_match(text_raw)
    if db_intent:
        last_interaction.update({"text": text_raw, "intent": db_intent, "params": accumulated_params, "timestamp": now, "state": None})
        save_context()
        return f"📁 (DB기반) '{INTENT_NAMES.get(db_intent)}' 실행합니다.\n\n" + execute_and_report(db_intent, accumulated_params)

    # 4) 애매한 경우 -> 확인 요청 (WAITING_CONFIRMATION 진입)
    last_interaction.update({
        "text": text_raw, 
        "intent": local_intent, 
        "params": accumulated_params, 
        "timestamp": now, 
        "state": "WAITING_CONFIRMATION"
    })
    save_context()
    return f"❓ 혹시 **'{INTENT_NAMES.get(local_intent, local_intent)}'** 기능을 원하시는 건가요? (맞으면 응 / 틀리면 아니)"

def send_telegram(message: str):
    if not message: return
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": message}, timeout=10)
    except Exception as e: logger.error(f"❌ Telegram Send Error: {e}")

def main():
    load_context()
    logger.info("🚀 하이브리드 리스너 가동")
    offset = None
    while True:
        try:
            url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
            r = requests.get(url, params={"offset": offset, "timeout": 20}).json()
            if r.get("ok"):
                for item in r.get("result", []):
                    offset = item["update_id"] + 1
                    msg = item.get("message", {})
                    text = msg.get("text")
                    if text:
                        reply = process_and_reply(text)
                        send_telegram(reply)
            time.sleep(1)
        except Exception as e:
            logger.error(f"Main Loop Error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
