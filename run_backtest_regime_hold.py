"""국면별 동적 최소 보유 기간(Plan Gemini ⑤) 그리드 실험.

진단 결과(41ac181/2f0e494 이후 재분석):
- BULL 진입 거래는 6~9일 근처(그레이스 종료 직후)에 청산되면 평균 -2.27%,
  9일 초과 보유하면 평균 +1.69%(승률 51.4%) — 짧은 BULL 휩쏘 국면(평균 12.7일, 중앙값 4일)의
  꼬리에서 진입한 뒤 그레이스가 풀리자마자 하드스탑에 잘리는 패턴으로 추정됨.
  => BULL 그레이스를 오히려 '늘리는' 방향을 테스트한다 (제미나이 제안과 반대 가설도 포함).
- BEAR 진입 거래는 평균 수익률이 양수(+0.23%)라 제미나이의 "BEAR는 짧게 3일" 제안이
  실제로 이득인지 불확실 — 함께 그리드에 포함해 데이터로 검증한다.
"""
import sys, os, json
import pandas as pd

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
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

start_date = "2014-04-23"
end_date = "2026-07-19"

CONFIGS = {
    "baseline_uniform7": 7,
    "A_bull_extend14": {"BULL": 14, "NEUTRAL": 7, "BEAR": 7, "DEFAULT": 7},
    "B_bear_short3": {"BULL": 7, "NEUTRAL": 7, "BEAR": 3, "DEFAULT": 7},
    "C_bull14_bear3": {"BULL": 14, "NEUTRAL": 7, "BEAR": 3, "DEFAULT": 7},
    "D_uniform10": 10,
}


def run_one(label, min_hold_days):
    bt = VectorBacktester(
        tickers=UNIVERSE_TOP20,
        start_date=start_date,
        end_date=end_date,
        initial_capital=100_000_000.0,
        min_hold_days=min_hold_days,
    )
    bt.run()
    summary = bt.get_summary()

    trades = pd.DataFrame(bt.trade_history)
    if not trades.empty:
        trades['buy_date'] = pd.to_datetime(trades['buy_date'])
        trades['sell_date'] = pd.to_datetime(trades['sell_date'])
        trades['hold_days'] = (trades['sell_date'] - trades['buy_date']).dt.days
        bull = trades[trades['regime_at_buy'] == 'BULL']
        bull_mean = bull['profit_pct'].mean() if len(bull) else float('nan')
        bull_win = (bull['profit_pct'] > 0).mean() * 100 if len(bull) else float('nan')
        bear = trades[trades['regime_at_buy'] == 'BEAR']
        bear_mean = bear['profit_pct'].mean() if len(bear) else float('nan')
        bear_win = (bear['profit_pct'] > 0).mean() * 100 if len(bear) else float('nan')
    else:
        bull_mean = bull_win = bear_mean = bear_win = float('nan')

    return {
        "label": label,
        "return": summary.get("Strategy Return (%)"),
        "mdd": summary.get("MDD (%)"),
        "trades": summary.get("Total Trades"),
        "win_rate": summary.get("Win Rate (%)"),
        "bull_mean_%": round(bull_mean, 3) if bull_mean == bull_mean else None,
        "bull_win_%": round(bull_win, 1) if bull_win == bull_win else None,
        "bear_mean_%": round(bear_mean, 3) if bear_mean == bear_mean else None,
        "bear_win_%": round(bear_win, 1) if bear_win == bear_win else None,
    }


def main():
    results = []
    for label, cfg in CONFIGS.items():
        print(f"\n=== {label} (min_hold_days={cfg}) ===")
        r = run_one(label, cfg)
        print(json.dumps(r, indent=2, ensure_ascii=False))
        results.append(r)

    print(f"\n{'='*100}")
    print("  [비교 요약]")
    print(f"{'='*100}")
    df = pd.DataFrame(results)
    print(df.to_string(index=False))
    df.to_csv("reports/regime_hold_grid_results.csv", index=False)


if __name__ == "__main__":
    main()
