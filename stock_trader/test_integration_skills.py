import json
import sys
import os

# agent_skills.py가 있는 경로 추가
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from agent_skills import skill_router

def run_integration_test():
    print("=== [Gemini-Quant] Agent Skills Integration Test Start ===")
    
    # 1. 계좌 요약 조회 테스트
    print("\n[Step 1] Requesting Account Summary...")
    res1 = skill_router("get_account_summary", {})
    print(f"Result: {json.dumps(res1, indent=2, ensure_ascii=False)}")
    
    # 2. 시장가 매수 주문 테스트 (테스트 종목: 삼성전자 005930, 1주)
    # 실제 주문이 나가지 않도록 MOCK 모드인지 확인이 필요할 수 있으나, 
    # 현재 기본 설정이 MOCK이므로 안전하게 진행합니다.
    print("\n[Step 2] Executing Market Order (BUY 005930 1qty)...")
    order_params = {
        "stock_code": "005930",
        "quantity": 1,
        "price": 0,
        "side": "BUY",
        "ord_tp": "03"  # 시장가
    }
    res2 = skill_router("place_order", order_params)
    print(f"Result: {json.dumps(res2, indent=2, ensure_ascii=False)}")
    
    # 3. 시스템 상태 모니터링 테스트
    print("\n[Step 3] Checking System Status (Metrics)...")
    res3 = skill_router("get_system_status", {})
    print(f"Result: {json.dumps(res3, indent=2, ensure_ascii=False)}")
    
    # 4. 과거 이력 검색 테스트 (비서 기능)
    print("\n[Step 4] Searching Conversation History for '삼성전자'...")
    res4 = skill_router("search_history", {"keyword": "삼성전자"})
    print(f"Result: {json.dumps(res4, indent=2, ensure_ascii=False)}")

    print("\n=== [Gemini-Quant] Integration Test Completed ===")

if __name__ == "__main__":
    run_integration_test()
