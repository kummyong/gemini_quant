import sys
import os
import json

# Ensure project root is in PYTHONPATH
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stock_trader.scripts.backtester import VectorBacktester

def main():
    # 시가총액 상위 종목 중심 유니버스 (20개)
    universe = {
        "005930": "삼성전자",
        "000660": "SK하이닉스",
        "373220": "LG에너지솔루션",
        "207940": "삼성바이오로직스",
        "005380": "현대차",
        "000270": "기아",
        "068270": "셀트리온",
        "005490": "POSCO홀딩스",
        "035420": "NAVER",
        "051910": "LG화학",
        "028260": "삼성물산",
        "035720": "카카오",
        "105560": "KB금융",
        "055550": "신한지주",
        "032830": "삼성생명",
        "012330": "현대모비스",
        "066570": "LG전자",
        "033780": "KT&G",
        "323410": "카카오뱅크",
        "015760": "한국전력"
    }

    # 최근 3년간 테스트 (현재 시점: 2026-07-19 기준 과거 3년)
    start_date = "2023-07-19"
    end_date = "2026-07-19"

    print(f"=== 스마트 머니(Core) 과거 데이터 백테스트 ===")
    print(f"테스트 기간: {start_date} ~ {end_date}")
    print(f"테스트 유니버스: KOSPI 상위 {len(universe)}개 종목")

    bt = VectorBacktester(
        tickers=universe,
        start_date=start_date,
        end_date=end_date,
        initial_capital=100_000_000.0 # 1억 원
    )
    
    bt.run()
    
    print("\n=== [백테스트 결과] ===")
    summary = bt.get_summary()
    print(json.dumps(summary, indent=4, ensure_ascii=False))

if __name__ == "__main__":
    main()
