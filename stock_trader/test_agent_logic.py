import os
import sys
import google.generativeai as genai
from dotenv import load_dotenv

# 경로 설정 및 도구 임포트
BASE_DIR = "/root/workspace/gemini-quant/stock_trader"
sys.path.append(BASE_DIR)
from agent_skills import get_account_status, execute_market_order, update_profit_cut

load_dotenv(os.path.join(BASE_DIR, ".env"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

def simulate_test():
    print("=== [에이전트 지능 및 도구 호출 시뮬레이션 테스트] ===")
    genai.configure(api_key=GEMINI_API_KEY)
    
    # 도구 등록
    tools = [get_account_status, execute_market_order, update_profit_cut]
    # 가용성이 확인된 최신 모델로 변경
    model = genai.GenerativeModel(model_name="gemini-2.0-flash", tools=tools)
    chat = model.start_chat(enable_automatic_function_calling=True)
    
    # 테스트 1: 계좌 상태 물어보기
    print("\n[Test 1] 사용자: '내 계좌 현황 좀 알려줘'")
    res1 = chat.send_message("내 계좌 현황 좀 알려줘")
    print(f"AI 응답: {res1.text}")
    
    # 테스트 2: 매수 주문 지시
    print("\n[Test 2] 사용자: '삼성전자 10%만 사줘'")
    res2 = chat.send_message("삼성전자 10%만 사줘")
    print(f"AI 응답: {res2.text}")

if __name__ == "__main__":
    simulate_test()
