"""파라미터 민감도 + 워크포워드 검증 (과적합 방지 규율).

검증 대상:
1. 국면 확인일(confirm_days) {0,2,3,4} — V1(3일) 채택이 스파이크가 아닌지
2. MIN_HOLDING_DAYS_BULL {10,12,14,16,18} (confirm=3 고정) — 14일 채택이 스파이크가 아닌지
3. 워크포워드: 전반기(2014-04~2020-06) / 후반기(2020-07~2026-07) 분할 —
   채택 구성(confirm3+BULL14)이 두 구간 모두에서 구형 구성 대비 성립하는지

데이터는 전 구간 1회 다운로드 후 공유. 워크포워드는 전체 기간으로 지표/국면을 계산한 뒤
거래 구간만 슬라이스해 워밍업 손실 없이 공정 비교한다.
"""
import sys, os, json
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

_shared = {"data": None, "vix": None, "kospi_raw": None}


def run_cfg(label, confirm_days, bull_hold, window=None):
    bt = VectorBacktester(
        tickers=UNIVERSE_TOP20, start_date=START, end_date=END,
        initial_capital=100_000_000.0,
        min_hold_days={"BULL": bull_hold, "NEUTRAL": 7, "BEAR": 7, "DEFAULT": 7},
        regime_kwargs=dict(confirm_days=confirm_days),
    )
    if _shared["data"] is None:
        bt.prepare_data()
        _shared["data"] = bt.data
        _shared["vix"] = bt.vix_data
        _shared["kospi_raw"] = bt.kospi_data.drop(columns=['Regime'])
    bt.data = _shared["data"]
    bt.vix_data = _shared["vix"]
    kospi = _shared["kospi_raw"].copy()
    kospi['Regime'] = compute_regime_series(kospi['Close'], confirm_days=confirm_days)
    if window:
        kospi = kospi.loc[window[0]:window[1]]
    bt.kospi_data = kospi
    bt.run()
    s = bt.get_summary()
    return {
        "label": label,
        "return": s.get("Strategy Return (%)"),
        "kospi": s.get("Benchmark (KOSPI) Return (%)"),
        "mdd": s.get("MDD (%)"),
        "trades": s.get("Total Trades"),
        "win_rate": s.get("Win Rate (%)"),
    }


def show(title, rows):
    print(f"\n{'='*90}\n  {title}\n{'='*90}")
    df = pd.DataFrame(rows)
    print(df.to_string(index=False))
    return df


def main():
    # 1. 국면 확인일 민감도 (BULL hold 14 고정)
    rows = [run_cfg(f"confirm={c}", c, 14) for c in [0, 2, 3, 4]]
    df1 = show("1. 국면 확인일(confirm_days) 민감도 [BULL_hold=14]", rows)

    # 2. BULL 최소보유 민감도 (confirm 3 고정)
    rows = [run_cfg(f"bull_hold={h}", 3, h) for h in [10, 12, 14, 16, 18]]
    df2 = show("2. MIN_HOLDING_DAYS_BULL 민감도 [confirm=3]", rows)

    # 3. 워크포워드 (전반/후반)
    W1 = ("2014-04-23", "2020-06-30")
    W2 = ("2020-07-01", "2026-07-19")
    rows = []
    for wname, w in [("전반기 2014-2020H1", W1), ("후반기 2020H2-2026", W2)]:
        rows.append(run_cfg(f"{wname} | 구형(c0,h14)", 0, 14, w))
        rows.append(run_cfg(f"{wname} | 채택(c3,h14)", 3, 14, w))
    df3 = show("3. 워크포워드: 구형(confirm0) vs 채택(confirm3)", rows)

    for name, df in [("sensitivity_confirm", df1), ("sensitivity_bullhold", df2), ("walkforward", df3)]:
        df.to_csv(f"reports/{name}_results.csv", index=False)


if __name__ == "__main__":
    main()
