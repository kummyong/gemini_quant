"""Top50 유니버스 정직 버전 백테스트.

기존 Top50 실험(run_backtest_d/experiments.py)은 상폐 종목(예: 쌍용C&E 003410, 2024 상폐)이
역대 최고가로 영구 평가되는 백테스터 버그 때문에 수익률이 낙관 왜곡되어 있었다.
이 스크립트는 상폐 강제청산(exit_reason='delisted')이 반영된 백테스터로 재실행하고,
010140 종목명 오기(한솔제지→삼성중공업)도 수정된 유니버스를 쓴다.
구성: 현행 채택 파라미터(국면 confirm=0, 국면별 최소보유 BULL14/기타7, 러너 0.5).
"""
import sys, os, json
import pandas as pd

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from stock_trader.scripts.backtester import VectorBacktester
from run_backtest_d import UNIVERSE_TOP50

START, END = "2014-04-23", "2026-07-19"


def main():
    bt = VectorBacktester(
        tickers=UNIVERSE_TOP50,
        start_date=START, end_date=END,
        initial_capital=100_000_000.0,
        min_hold_days={"BULL": 14, "NEUTRAL": 7, "BEAR": 7, "DEFAULT": 7},
    )
    bt.run()
    print("\n=== [Top50 정직 버전 결과] ===")
    print(json.dumps(bt.get_summary(), indent=4, ensure_ascii=False))

    trades = pd.DataFrame(bt.trade_history)
    if not trades.empty and 'exit_reason' in trades.columns:
        delisted = trades[trades['exit_reason'] == 'delisted']
        print(f"\n상폐 강제청산 거래: {len(delisted)}건")
        if len(delisted):
            print(delisted[['buy_date', 'sell_date', 'ticker', 'profit_pct']].to_string(index=False))
    trades.to_csv("reports/backtest_trades_top50.csv", index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
