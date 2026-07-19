import sys
import os
import json
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stock_trader.scripts.backtester import VectorBacktester

# === 유니버스 정의 ===
UNIVERSE_TOP20 = {
    "005930": "삼성전자", "015760": "한국전력", "005490": "POSCO홀딩스",
    "005380": "현대차", "055550": "신한지주", "017670": "SK텔레콤",
    "066570": "LG전자", "030200": "KT", "012330": "현대모비스",
    "051910": "LG화학", "000270": "기아", "033780": "KT&G",
    "000810": "삼성화재", "096770": "SK이노베이션", "010950": "S-Oil",
    "010130": "고려아연", "004020": "현대제철", "023530": "롯데쇼핑",
    "024110": "기업은행", "009150": "삼성전기"
}

# Top 50 (Top 20 + 30개 대형주 추가 — 2014년 이전부터 존재하는 종목 위주로 생존자 편향 최소화)
UNIVERSE_TOP50 = {
    **UNIVERSE_TOP20,
    "000660": "SK하이닉스", "035420": "NAVER", "068270": "셀트리온",
    "105560": "KB금융", "055550": "신한지주", "086790": "하나금융",
    "003550": "LG", "028260": "삼성물산", "034730": "SK",
    "018260": "삼성에스디에스", "032830": "삼성생명", "003490": "대한항공",
    "011170": "롯데케미칼", "010140": "한솔제지", "006400": "삼성SDI",
    "036570": "엔씨소프트", "009540": "한국조선해양", "161390": "한국타이어",
    "004170": "신세계", "001040": "CJ", "000100": "유한양행",
    "011200": "HMM", "002790": "아모레G", "071050": "한국금융지주",
    "016360": "삼성증권", "138040": "메리츠금융", "003410": "쌍용C&E",
    "007070": "GS리테일", "006800": "미래에셋증권", "034020": "두산에너빌리티"
}


def run_experiment(universe, min_hold_days, label):
    start_date = "2014-04-23"
    end_date = "2026-07-19"

    print(f"\n{'='*60}")
    print(f"  실험: {label}")
    print(f"  유니버스: {len(universe)}종목 / 최소 보유일: {min_hold_days}일")
    print(f"{'='*60}")

    bt = VectorBacktester(
        tickers=universe,
        start_date=start_date,
        end_date=end_date,
        initial_capital=100_000_000.0,
        min_hold_days=min_hold_days
    )

    bt.run()

    summary = bt.get_summary()
    print(json.dumps(summary, indent=4, ensure_ascii=False))

    # Save graph
    try:
        equity_df = pd.DataFrame(bt.equity_curve)
        equity_df['Date'] = pd.to_datetime(equity_df['Date'])
        equity_df.set_index('Date', inplace=True)

        kospi = bt.kospi_data.copy()
        if not kospi.empty and not equity_df.empty:
            start_kospi = kospi['Close'].iloc[0]
            kospi['Normalized'] = (kospi['Close'] / start_kospi) * bt.initial_capital

            plt.figure(figsize=(14, 7))
            plt.plot(equity_df.index, equity_df['Equity'], label='Strategy', color='red', linewidth=2)
            plt.plot(kospi.index, kospi['Normalized'], label='KOSPI', color='gray', linestyle='--', linewidth=1.5)
            plt.title(f'{label}', fontsize=16, fontweight='bold')
            plt.xlabel('Date', fontsize=12)
            plt.ylabel('Equity (KRW)', fontsize=12)
            plt.grid(True, linestyle=':', alpha=0.6)
            plt.legend(fontsize=12, loc='upper left')
            plt.gca().yaxis.set_major_formatter(plt.matplotlib.ticker.StrMethodFormatter('{x:,.0f}'))

            safe_label = label.replace(" ", "_").replace("/", "_").replace("+", "and")
            save_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports", f"backtest_{safe_label}.png")
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            plt.close()
            print(f"Graph saved: {save_path}")

            # Yearly
            print(f"\n--- 연도별 성과 ---")
            equity_df['Year'] = equity_df.index.year
            kospi['Year'] = kospi.index.year
            years = equity_df['Year'].unique()
            print(f"{'Year':<6} | {'Strategy Ret':<12} | {'KOSPI Ret':<12} | {'Strategy MDD':<12} | {'KOSPI MDD'}")
            print("-" * 65)
            for y in years:
                y_eq = equity_df[equity_df['Year'] == y]
                y_kp = kospi[kospi['Year'] == y]
                if y_eq.empty or y_kp.empty: continue
                s_ret = (y_eq['Equity'].iloc[-1] / y_eq['Equity'].iloc[0] - 1) * 100
                s_mdd = ((y_eq['Equity'] / y_eq['Equity'].cummax()) - 1).min() * 100
                k_ret = (y_kp['Close'].iloc[-1] / y_kp['Close'].iloc[0] - 1) * 100
                k_mdd = ((y_kp['Close'] / y_kp['Close'].cummax()) - 1).min() * 100
                print(f"{y:<6} | {s_ret:>11.2f}% | {k_ret:>11.2f}% | {s_mdd:>11.2f}% | {k_mdd:>8.2f}%")

    except Exception as e:
        print(f"Graph error: {e}")

    return summary


def main():
    results = {}

    # 실험 A: 유니버스 확장 (Top 50, 최소 보유 0일)
    results['A'] = run_experiment(UNIVERSE_TOP50, min_hold_days=0, label="A_Top50")

    # 실험 B: 최소 보유 기간 (Top 20, 최소 보유 7일)
    results['B'] = run_experiment(UNIVERSE_TOP20, min_hold_days=7, label="B_MinHold7")

    # 실험 C: 혼합 (Top 50 + 최소 보유 7일)
    results['C'] = run_experiment(UNIVERSE_TOP50, min_hold_days=7, label="C_Top50_MinHold7")

    # 비교 요약
    print(f"\n{'='*70}")
    print("  [최종 비교 요약]")
    print(f"{'='*70}")
    print(f"{'실험':<22} | {'수익률':<12} | {'MDD':<10} | {'거래수':<8} | {'승률'}")
    print("-" * 70)
    labels = {'A': 'A. Top50', 'B': 'B. MinHold7', 'C': 'C. Top50+MinHold7'}
    for key, s in results.items():
        print(f"{labels[key]:<22} | {s['Strategy Return (%)']:<12} | {s['MDD (%)']:<10} | {s['Total Trades']:<8} | {s['Win Rate (%)']}")


if __name__ == "__main__":
    main()
