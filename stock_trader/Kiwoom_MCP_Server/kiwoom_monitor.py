import time
import logging
from kiwoom_mcp import kiwoom_api

# 불필요한 로그 억제
logging.getLogger("urllib3").setLevel(logging.WARNING)

def monitor_stock(stock_code="005930", interval=60):
    print(f"=== [{stock_code}] 주가 모니터링 시작 (간격: {interval}초) ===")
    print("중료하려면 Ctrl+C를 누르세요.\n")
    
    try:
        while True:
            data = kiwoom_api.get_stock_info(stock_code)
            if data and 'cur_prc' in data:
                # 기호(+/-) 제거 및 포맷팅
                price = int(data['cur_prc'].replace('+', '').replace('-', ''))
                change = int(data.get('pred_pre', '0').replace('+', '').replace('-', ''))
                rate = float(data.get('flu_rt', '0'))
                sign = "+" if "+" in data['cur_prc'] else "-" if "-" in data['cur_prc'] else ""
                
                now = time.strftime('%H:%M:%S')
                print(f"[{now}] 현재가: {price:,}원 | 전일대비: {sign}{change:,}원 ({sign}{rate}%)")
            else:
                print(f"데이터를 가져오지 못했습니다: {data}")
            
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n모니터링을 종료합니다.")
    except Exception as e:
        print(f"\n오류 발생: {e}")

if __name__ == "__main__":
    # 삼성전자(005930) 모니터링
    monitor_stock("005930", 60)
