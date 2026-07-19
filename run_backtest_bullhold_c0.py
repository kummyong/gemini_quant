"""MIN_HOLDING_DAYS_BULL 민감도·워크포워드 — 현행 국면 판정(confirm=0) 기준.

run_backtest_sensitivity.py에서 confirm=3이 민감도 스파이크+워크포워드 불일치로 기각됨에 따라,
현행 판정(c0)에서 BULL 그레이스 연장(14일) 채택이 견고한지 별도 검증한다.
"""
import sys, os
import pandas as pd

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from stock_trader.core.macro_indicators import compute_regime_series
from stock_trader.scripts.backtester import VectorBacktester

UNIVERSE_TOP20 = {
    "005930": "삼성전자", "015760": "한국전력", "005490": "POSCO홀딩스",
    "005380": "현대차", "055550": "신한지주", "017670": "SK텔레콤",
    "066570": "LG전자", "030200": "KT", "012330": "현대모비스",
    "051910": "LG화학", "000270": "기아", "033780": "KT&G",
    "000810": "삼성화재", "096770": "SK이노베이션", "010950": "S-Oil",
    "010130": "고려아연", "004020": "현대제철", "023530": "롯데쇼핑",
    "024110": "기업은행", "009150": "삼성전기"
}
START, END = "2014-04-23", "2026-07-19"
_shared = {"data": None, "vix": None, "kospi": None}


def run_cfg(label, bull_hold, window=None):
    bt = VectorBacktester(
        tickers=UNIVERSE_TOP20, start_date=START, end_date=END,
        initial_capital=100_000_000.0,
        min_hold_days={"BULL": bull_hold, "NEUTRAL": 7, "BEAR": 7, "DEFAULT": 7},
    )
    if _shared["data"] is None:
        bt.prepare_data()
        _shared["data"] = bt.data
        _shared["vix"] = bt.vix_data
        _shared["kospi"] = bt.kospi_data
    bt.data = _shared["data"]
    bt.vix_data = _shared["vix"]
    kospi = _shared["kospi"]
    bt.kospi_data = kospi.loc[window[0]:window[1]] if window else kospi
    bt.run()
    s = bt.get_summary()
    return {"label": label, "return": s.get("Strategy Return (%)"), "mdd": s.get("MDD (%)"),
            "trades": s.get("Total Trades"), "win_rate": s.get("Win Rate (%)")}


def main():
    rows = [run_cfg(f"bull_hold={h}", h) for h in [7, 10, 12, 14, 16, 18]]
    print("\n=== BULL 최소보유 민감도 (confirm=0, 전체 기간) ===")
    print(pd.DataFrame(rows).to_string(index=False))

    W1 = ("2014-04-23", "2020-06-30")
    W2 = ("2020-07-01", "2026-07-19")
    rows = []
    for wname, w in [("전반기", W1), ("후반기", W2)]:
        rows.append(run_cfg(f"{wname} | h7(균일)", 7, w))
        rows.append(run_cfg(f"{wname} | h14(BULL연장)", 14, w))
    print("\n=== 워크포워드: 균일7 vs BULL14 (confirm=0) ===")
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()
