import sys
import os
import io

# 윈도우 콘솔 한글/유니코드 출력 지원 강제 설정
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_trader"))

import logging
logging.basicConfig(level=logging.INFO)

try:
    from kiwoom_api_core import KiwoomApiCore
    
    print("[INFO] Kiwoom 모의 계좌 API 호출 중...")
    api = KiwoomApiCore(mode="MOCK")
    res = api.get_account_summary()
    
    if res:
        print("========================================")
        print(" [모의 계좌 잔고 및 보유 종목 현황] ")
        print("========================================")
        
        output_info = res.get("output", {})
        if not output_info and isinstance(res, dict):
            output_info = res
            
        for k, v in output_info.items():
            if isinstance(v, (str, int, float)) and k not in ["acnt_evlt_remn_indv_tot"]:
                print(f"- {k}: {v}")
                
        holdings = res.get("acnt_evlt_remn_indv_tot", [])
        print("\n [보유 종목 리스트]")
        if holdings:
            for idx, item in enumerate(holdings, 1):
                stk_cd = item.get("stk_cd", "")
                stk_nm = item.get("stk_nm", "")
                qty = item.get("rmnd_qty", "0")
                pchs = item.get("pchs_amt", "0")
                evlt = item.get("evlt_amt", "0")
                prft = item.get("prft_rt", "0")
                print(f" {idx}. {stk_nm} ({stk_cd}) | 잔량: {qty}주 | 매입: {pchs}원 | 평가: {evlt}원 | 수익률: {prft}%")
        else:
            print(" (보유 중인 종목이 없습니다.)")
        print("========================================")
    else:
        print("[-] 계좌 정보를 불러오는 데 실패했습니다. 응답이 비어 있습니다.")
except Exception as e:
    print(f"[-] 에러 발생: {e}")
