"""국면 감지기 변형 실험 — 휩쏘 축소가 실제 성과로 이어지는지 검증.

진단(2026-07-19): 구형 판정(버퍼 0, 확인 0)은 BULL 스트릭의 56%가 5일 이하 휩쏘였고,
전략의 BULL 진입이 그레이스 해제 직후 손절되는 패턴(6~9일 청산 평균 -2.27%)의 원인.

1단계: KOSPI 지수만으로 각 변형의 국면 품질(전방수익률 분리도, 스트릭 길이, 휩쏘율) 평가
2단계: 유망 변형을 실제 전략 백테스트(Top20, 국면별 최소보유+러너)로 검증
데이터는 변형 간 공유해 다운로드를 1회로 줄인다.
"""
import sys, os, json
import pandas as pd
import numpy as np
import FinanceDataReader as fdr

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
MIN_HOLD = {"BULL": 14, "NEUTRAL": 7, "BEAR": 7, "DEFAULT": 7}

VARIANTS = {
    "V0_baseline":        dict(buffer_pct=0.00, slope_days=1, confirm_days=0),
    "V1_confirm3":        dict(buffer_pct=0.00, slope_days=1, confirm_days=3),
    "V2_confirm5":        dict(buffer_pct=0.00, slope_days=1, confirm_days=5),
    "V3_buf2_slope5":     dict(buffer_pct=0.02, slope_days=5, confirm_days=0),
    "V4_buf2_slope5_c3":  dict(buffer_pct=0.02, slope_days=5, confirm_days=3),
    "V5_buf1_slope3_c3":  dict(buffer_pct=0.01, slope_days=3, confirm_days=3),
}


def regime_quality(kospi: pd.DataFrame):
    rows = []
    for label, kw in VARIANTS.items():
        df = kospi.copy()
        df['Regime'] = compute_regime_series(df['Close'], **kw)
        for h in [20, 60]:
            df[f'Fwd_{h}'] = df['Close'].shift(-h) / df['Close'] - 1.0

        df['chg'] = df['Regime'] != df['Regime'].shift(1)
        df['sid'] = df['chg'].cumsum()
        streaks = df.groupby(['sid', 'Regime']).size().reset_index(name='days')

        row = {"variant": label}
        for regime in ["BULL", "NEUTRAL", "BEAR"]:
            sub = df[df['Regime'] == regime]
            s = streaks[streaks['Regime'] == regime]['days']
            row[f"{regime}_days%"] = round(len(sub) / len(df) * 100, 1)
            row[f"{regime}_fwd60%"] = round(sub['Fwd_60'].mean() * 100, 2)
            row[f"{regime}_whipsaw%"] = round((s <= 5).mean() * 100, 1) if len(s) else None
            row[f"{regime}_avgstreak"] = round(s.mean(), 1) if len(s) else None
        row["spread_fwd60"] = round(row["BULL_fwd60%"] - row["BEAR_fwd60%"], 2)
        rows.append(row)
    return pd.DataFrame(rows)


def run_strategy(label, kw, shared):
    bt = VectorBacktester(
        tickers=UNIVERSE_TOP20, start_date=START, end_date=END,
        initial_capital=100_000_000.0, min_hold_days=MIN_HOLD, regime_kwargs=kw,
    )
    if shared["data"] is None:
        bt.prepare_data()
        shared["data"] = bt.data
        shared["vix"] = bt.vix_data
        shared["kospi_raw"] = bt.kospi_data[['Close', 'Open', 'High', 'Low', 'Volume', 'Return_20d']].copy() \
            if 'Open' in bt.kospi_data.columns else bt.kospi_data[['Close', 'Return_20d']].copy()
    else:
        bt.data = shared["data"]
        bt.vix_data = shared["vix"]
        kospi = shared["kospi_raw"].copy()
        kospi['Regime'] = compute_regime_series(kospi['Close'], **kw)
        bt.kospi_data = kospi
    bt.run()
    s = bt.get_summary()

    trades = pd.DataFrame(bt.trade_history)
    bull = trades[trades['regime_at_buy'] == 'BULL'] if not trades.empty else pd.DataFrame()
    return {
        "variant": label,
        "return": s.get("Strategy Return (%)"),
        "mdd": s.get("MDD (%)"),
        "trades": s.get("Total Trades"),
        "win_rate": s.get("Win Rate (%)"),
        "bull_n": len(bull),
        "bull_mean_%": round(bull['profit_pct'].mean(), 3) if len(bull) else None,
        "bull_win_%": round((bull['profit_pct'] > 0).mean() * 100, 1) if len(bull) else None,
    }


def main():
    print("=== 1단계: KOSPI 국면 품질 비교 ===")
    kospi = fdr.DataReader('KS11', start=START, end=END)
    qdf = regime_quality(kospi)
    print(qdf.to_string(index=False))
    qdf.to_csv("reports/regime_variants_quality.csv", index=False)

    print("\n=== 2단계: 전략 백테스트 비교 (전체 변형) ===")
    shared = {"data": None, "vix": None, "kospi_raw": None}
    results = []
    for label, kw in VARIANTS.items():
        print(f"\n--- {label} {kw} ---")
        r = run_strategy(label, kw, shared)
        print(json.dumps(r, ensure_ascii=False))
        results.append(r)

    rdf = pd.DataFrame(results)
    print(f"\n{'='*100}")
    print(rdf.to_string(index=False))
    rdf.to_csv("reports/regime_variants_strategy.csv", index=False)


if __name__ == "__main__":
    main()
