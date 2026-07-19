"""BEAR 국면 인버스 ETF 헷지(제미나이 제안 ⑦) 검증.

진단 결과: 현재 BEAR 판정 구간의 KOSPI 전방수익률(60일 평균 +0.86%)과 전략 자체의
BEAR 진입 평균 수익률(+0.23~0.52%, 그리드 실험 기준)이 모두 양수라, "BEAR = 하락장"이라는
전제로 인버스를 사면 실제로는 완만한 상승/횡보 구간에서 인버스를 들고 있다가 손실을 볼
위험이 크다. 이 스크립트는 그 우려를 실제 자본배분으로 검증한다.

방법: 총 자본 1억원을 8천만(전략)+2천만(헷지 슬리브)으로 분리한다.
- 전략 슬리브: 기존 VectorBacktester(국면별 최소보유+러너 로직 포함, 8천만원)
- 헷지 슬리브: BEAR 국면일 때만 KODEX 인버스(114800)를 전액 매수, 그 외 국면엔 전액 현금
두 슬리브의 일별 평가액을 합산해 결합 수익률/MDD를 계산하고, 헷지 없이 1억 전액을
전략에 투입한 기존 결과(194.32%/-31.59%, 2f0e494+러너 반영분)와 비교한다.
"""
import sys, os, json
import pandas as pd
import FinanceDataReader as fdr

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
MIN_HOLD = {"BULL": 14, "NEUTRAL": 7, "BEAR": 7, "DEFAULT": 7}
COMMISSION_RATE = 0.00015
SELL_TAX_RATE = 0.0015

STRATEGY_CAPITAL = 80_000_000.0
HEDGE_CAPITAL = 20_000_000.0
BASELINE_RETURN_PCT = 194.32  # 헷지 없이 1억 전액 투입한 기존 결과 (run_backtest.py)
BASELINE_MDD_PCT = -31.59


def run_strategy_sleeve():
    bt = VectorBacktester(
        tickers=UNIVERSE_TOP20,
        start_date=start_date,
        end_date=end_date,
        initial_capital=STRATEGY_CAPITAL,
        min_hold_days=MIN_HOLD,
    )
    bt.run()
    return bt


def run_hedge_sleeve(kospi_regime: pd.Series):
    inv = fdr.DataReader("114800", start=start_date, end=end_date)
    hedge_cash = HEDGE_CAPITAL
    hedge_shares = 0
    equity = []

    for date in kospi_regime.index:
        if date not in inv.index or pd.isna(inv.loc[date, 'Close']):
            equity.append({'Date': date, 'HedgeEquity': hedge_cash + hedge_shares * (inv.loc[:date, 'Close'].iloc[-1] if not inv.loc[:date].empty else 0)})
            continue

        close = inv.loc[date, 'Close']
        regime = kospi_regime.loc[date]

        if regime == "BEAR" and hedge_shares == 0 and hedge_cash > 0:
            hedge_shares = int(hedge_cash / (close * (1.0 + COMMISSION_RATE)))
            cost = hedge_shares * close * (1.0 + COMMISSION_RATE)
            hedge_cash -= cost
        elif regime != "BEAR" and hedge_shares > 0:
            proceeds = hedge_shares * close * (1.0 - COMMISSION_RATE - SELL_TAX_RATE)
            hedge_cash += proceeds
            hedge_shares = 0

        equity.append({'Date': date, 'HedgeEquity': hedge_cash + hedge_shares * close})

    return pd.DataFrame(equity).set_index('Date')


def summarize(equity_series: pd.Series, label: str, initial: float):
    total_return = (equity_series.iloc[-1] / initial - 1.0) * 100
    dd = (equity_series / equity_series.cummax() - 1.0) * 100
    mdd = dd.min()
    print(f"{label:<20} return={total_return:+.2f}%  MDD={mdd:.2f}%  final={equity_series.iloc[-1]:,.0f}")
    return total_return, mdd


def main():
    print("전략 슬리브(8천만원) 백테스트 실행...")
    bt = run_strategy_sleeve()
    strat_equity = pd.DataFrame(bt.equity_curve)
    strat_equity['Date'] = pd.to_datetime(strat_equity['Date'])
    strat_equity.set_index('Date', inplace=True)

    print("헷지 슬리브(2천만원, KODEX 인버스 114800) 시뮬레이션...")
    hedge_equity = run_hedge_sleeve(bt.kospi_data['Regime'])

    combined = strat_equity.join(hedge_equity, how='left')
    combined['HedgeEquity'] = combined['HedgeEquity'].ffill().fillna(HEDGE_CAPITAL)
    combined['Combined'] = combined['Equity'] + combined['HedgeEquity']

    print(f"\n{'='*70}")
    print("[비교 결과]")
    print(f"{'='*70}")
    print(f"{'베이스라인(1억 전액 전략, 헷지 없음)':<35} return=+{BASELINE_RETURN_PCT:.2f}%  MDD={BASELINE_MDD_PCT:.2f}%")
    summarize(strat_equity['Equity'], "전략 슬리브 단독(8천만)", STRATEGY_CAPITAL)
    hedge_ret, hedge_mdd = summarize(combined['HedgeEquity'], "헷지 슬리브 단독(2천만)", HEDGE_CAPITAL)
    combined_ret, combined_mdd = summarize(combined['Combined'], "결합(전략+헷지, 1억)", STRATEGY_CAPITAL + HEDGE_CAPITAL)

    print(f"\n순효과: 결합 수익률 {combined_ret:+.2f}%p vs 베이스라인 {BASELINE_RETURN_PCT:+.2f}%p "
          f"(차이 {combined_ret - BASELINE_RETURN_PCT:+.2f}%p), "
          f"MDD {combined_mdd:.2f}% vs {BASELINE_MDD_PCT:.2f}% (차이 {combined_mdd - BASELINE_MDD_PCT:+.2f}%p)")

    bear_days = (bt.kospi_data['Regime'] == 'BEAR').sum()
    total_days = len(bt.kospi_data)
    print(f"\nBEAR 국면 비율: {bear_days}/{total_days}일 ({bear_days/total_days*100:.1f}%)")


if __name__ == "__main__":
    main()
