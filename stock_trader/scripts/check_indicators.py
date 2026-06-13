import os
import sys
import json
import pandas as pd

# 프로젝트 루트를 path에 추가
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, "..", ".."))
sys.path.append(project_root)

from stock_trader.data.db_repository import DbRepository
from stock_trader.core.strategy_engine import StrategyEngine
from stock_trader.config import DB_PATH

def main():
    repo = DbRepository(DB_PATH)
    # ipc_publisher는 굳이 안 써도 되므로 None 처리
    engine = StrategyEngine(db_repository=repo, ipc_publisher=None)
    
    # 시장 국면 및 파라미터 로드
    engine._determine_market_regime()
    print("=" * 60)
    print(f"현재 시장 국면: {engine.current_regime}")
    print(f"적용 매수 RSI 기준선: {engine.RSI_BUY_THRES}")
    print(f"적용 볼린저 밴드 하단배수: {engine.BB_STD}")
    print("=" * 60)
    
    # 시장 데이터 수집
    market_data = engine.fetch_market_data()
    scored_stocks = engine.calculate_scores(market_data)
    
    print("\n[상위 10개 종목 상세 지표 현황]")
    print(f"{'종목명':<10} | {'현재가':>8} | {'RSI':>5} | {'RSI매수선':>8} | {'볼밴하단':>8} | {'이평선하회':>8} | {'하락세멈춤':>8}")
    print("-" * 80)
    
    for s in scored_stocks[:10]:
        rsi_val = s.get("rsi", 50.0)
        lower_band = s.get("lower_band", 0.0)
        price = s["price"]
        is_rsi_match = rsi_val <= engine.RSI_BUY_THRES
        is_bb_match = (lower_band > 0) and (price <= lower_band)
        is_bottoming = s.get("is_bottoming", True)
        is_under_ma120 = s.get("is_under_ma120", False)
        
        rsi_status = "OK" if is_rsi_match else "FAIL"
        bb_status = "OK" if is_bb_match else "FAIL"
        bottom_status = "OK" if is_bottoming else "FAIL"
        
        print(f"{s['name']:<10} | {price:>8,.0f} | {rsi_val:>5.1f} ({rsi_status}) | {engine.RSI_BUY_THRES:>8.1f} | {lower_band:>8,.0f} ({bb_status}) | {'Yes' if is_under_ma120 else 'No':^8} | {bottom_status:^8}")

    print("\n[전체 모니터링 종목 중 기술적 조건 분석]")
    matched_rsi = 0
    matched_bb = 0
    matched_both = 0
    matched_all = 0
    
    for s in scored_stocks:
        rsi_val = s.get("rsi", 50.0)
        lower_band = s.get("lower_band", 0.0)
        price = s["price"]
        is_rsi_match = rsi_val <= engine.RSI_BUY_THRES
        is_bb_match = (lower_band > 0) and (price <= lower_band)
        is_bottoming = s.get("is_bottoming", True)
        
        if is_rsi_match:
            matched_rsi += 1
        if is_bb_match:
            matched_bb += 1
        if is_rsi_match or is_bb_match:
            matched_both += 1
            if is_bottoming:
                matched_all += 1
                
    print(f"- 전체 종목 수: {len(scored_stocks)}개")
    print(f"- RSI 조건({engine.RSI_BUY_THRES} 이하) 만족: {matched_rsi}개")
    print(f"- BB 조건(하단배수 {engine.BB_STD}) 만족: {matched_bb}개")
    print(f"- RSI 또는 BB 중 하나 이상 만족: {matched_both}개")
    print(f"- 진입 조건(RSI/BB) & 하락세 멈춤(최종 매수 대상) 만족: {matched_all}개")
    print("=" * 60)

if __name__ == "__main__":
    main()
