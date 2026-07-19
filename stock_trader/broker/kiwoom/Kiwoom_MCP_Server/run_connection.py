import os
import sys
import logging

# Kiwoom_MCP_Server 디렉토리를 경로에 추가하여 모듈을 불러올 수 있게 합니다.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from kiwoom_mcp import KiwoomConfig, KiwoomApiManager

# 로그 끄기 (결과만 보기 위해)
logging.getLogger().setLevel(logging.ERROR)

def test_connection():
    try:
        # 1. 설정 로드 (MOCK 모드)
        config = KiwoomConfig(mode="MOCK")
        api = KiwoomApiManager(config)
        
        print(f"📡 [연결 테스트] 계좌번호: {config.account_no} (모드: {config.mode})")
        
        # 2. 계좌 요약 정보 조회 (kt00018)
        summary = api.get_account_summary()
        
        if not summary or "error" in str(summary).lower():
            print(f"❌ 조회 실패: {summary}")
            return

        print("\n💰 [계좌 요약 현황]")
        # API 응답 구조에 따라 데이터를 파싱합니다.
        output = summary.get("output", {})
        if isinstance(output, list) and len(output) > 0:
            data = output[0]
            print(f"- 총 평가금액: {data.get('tot_evl_amt', '0')}원")
            print(f"- 총 손익금액: {data.get('tot_pft_loss_amt', '0')}원")
            print(f"- 수익률: {data.get('tot_pft_rt', '0')}%")
            print(f"- 추정 예수금: {data.get('estm_dps', '0')}원")
        else:
            print(f"데이터 형식이 예상과 다릅니다: {summary}")

    except Exception as e:
        print(f"❌ 실행 중 오류 발생: {e}")

if __name__ == "__main__":
    test_connection()
