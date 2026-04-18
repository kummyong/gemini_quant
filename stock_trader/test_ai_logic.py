import os, sys, json
from datetime import datetime
# 경로 설정
BASE_DIR = "/root/workspace/gemini-quant/stock_trader"
sys.path.append(BASE_DIR)

try:
    from telegram_listener import get_intent_robust, SYSTEM_TOOLS
    print("✅ Module Import Success")
except ImportError as e:
    print(f"❌ Import Error: {e}")
    sys.exit(1)

test_cases = [
    "내 주식 잔고랑 수익률 좀 보여줘",
    "삼성전자 30% 매수해줘",
    "내일(2026-04-17) 아침 9시 30분에 하이닉스 전량(100%) 매도 예약해줘",
    "삼성전자 관련 예약 다 취소해줘"
]

print(f"\n--- [AI Logic & Tool Calling Test Start] ---")
for i, text in enumerate(test_cases, 1):
    print(f"\n[Test #{i}] User: {text}")
    try:
        intent, params, _ = get_intent_robust(text)
        print(f"🤖 Result -> Intent: {intent}")
        print(f"📦 Params: {json.dumps(params, ensure_ascii=False, indent=2)}")
        
        # 기본 검증 로직
        if i == 1 and intent == "get_account_status": print("✅ PASSED: get_account_status detected")
        elif i == 2 and intent == "execute_trade_immediate" and params.get('action') == "BUY": print("✅ PASSED: execute_trade_immediate (BUY) detected")
        elif i == 3 and intent == "reserve_trade_schedule": print("✅ PASSED: reserve_trade_schedule detected")
        elif i == 4 and intent == "cancel_task": print("✅ PASSED: cancel_task detected")
        else: print("⚠️ Review needed for this case.")
    except Exception as e:
        print(f"❌ Test Failed with Error: {e}")

print(f"\n--- [Test Completed] ---")
