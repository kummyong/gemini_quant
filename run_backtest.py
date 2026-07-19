import sys
import os
import json
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# Ensure project root is in PYTHONPATH
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stock_trader.scripts.backtester import VectorBacktester

def main():
    # 2006년 당시 시가총액 상위권 위주의 '전통 우량주' 유니버스 (생존자 편향 최소화)
    universe = {
        "005930": "삼성전자",
        "015760": "한국전력",
        "005490": "POSCO홀딩스",
        "005380": "현대차",
        "055550": "신한지주",
        "017670": "SK텔레콤",
        "066570": "LG전자",
        "030200": "KT",
        "012330": "현대모비스",
        "051910": "LG화학",
        "000270": "기아",
        "033780": "KT&G",
        "000810": "삼성화재",
        "096770": "SK이노베이션",
        "010950": "S-Oil",
        "010130": "고려아연",
        "004020": "현대제철",
        "023530": "롯데쇼핑",
        "024110": "기업은행",
        "009150": "삼성전기"
    }

    # 최근 20년간 테스트 (현재 시점: 2026-07-19 기준 과거 20년)
    start_date = "2006-07-19"
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

    # 그래프 그리기
    try:
        import pandas as pd
        
        equity_df = pd.DataFrame(bt.equity_curve)
        equity_df['Date'] = pd.to_datetime(equity_df['Date'])
        equity_df.set_index('Date', inplace=True)
        
        # Benchmark Normalize
        kospi = bt.kospi_data.copy()
        if not kospi.empty and not equity_df.empty:
            start_kospi = kospi['Close'].iloc[0]
            kospi['Normalized'] = (kospi['Close'] / start_kospi) * bt.initial_capital
            
            plt.figure(figsize=(14, 7))
            
            # 주식 수익률 (봇)
            plt.plot(equity_df.index, equity_df['Equity'], label='Strategy (Quant Bot)', color='red', linewidth=2)
            
            # 벤치마크 (KOSPI)
            plt.plot(kospi.index, kospi['Normalized'], label='Benchmark (KOSPI)', color='gray', linestyle='--', linewidth=1.5)
            
            plt.title('20-Year Backtest Equity Curve (2006-2026)', fontsize=16, fontweight='bold')
            plt.xlabel('Date', fontsize=12)
            plt.ylabel('Equity (KRW)', fontsize=12)
            plt.grid(True, linestyle=':', alpha=0.6)
            plt.legend(fontsize=12, loc='upper left')
            plt.gca().yaxis.set_major_formatter(plt.matplotlib.ticker.StrMethodFormatter('{x:,.0f}'))
            
            # Save Image
            save_path = r"C:\Users\SDS\.gemini\antigravity\brain\0d82e71e-ba72-484b-8a50-a77b0f509954\scratch\equity_curve.png"
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            plt.close()
            print(f"Graph saved to {save_path}")
            
            # 연도별 분석 (Yearly Analysis)
            print("\n=== [연도별 성과 분석 (Yearly Analysis)] ===")
            equity_df['Year'] = equity_df.index.year
            kospi['Year'] = kospi.index.year
            
            years = equity_df['Year'].unique()
            print(f"{'Year':<6} | {'Strategy Ret':<12} | {'KOSPI Ret':<12} | {'Strategy MDD':<12} | {'KOSPI MDD'}")
            print("-" * 65)
            
            for y in years:
                y_eq = equity_df[equity_df['Year'] == y]
                y_kp = kospi[kospi['Year'] == y]
                
                if y_eq.empty or y_kp.empty: continue
                
                s_start, s_end = y_eq['Equity'].iloc[0], y_eq['Equity'].iloc[-1]
                s_ret = (s_end / s_start - 1) * 100
                s_mdd = ((y_eq['Equity'] / y_eq['Equity'].cummax()) - 1).min() * 100
                
                k_start, k_end = y_kp['Close'].iloc[0], y_kp['Close'].iloc[-1]
                k_ret = (k_end / k_start - 1) * 100
                k_mdd = ((y_kp['Close'] / y_kp['Close'].cummax()) - 1).min() * 100
                
                print(f"{y:<6} | {s_ret:>11.2f}% | {k_ret:>11.2f}% | {s_mdd:>11.2f}% | {k_mdd:>8.2f}%")
            
    except Exception as e:
        print(f"Failed to draw graph or analyze: {e}")

if __name__ == "__main__":
    main()
