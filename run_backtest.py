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

    # 주의: FinanceDataReader 기본 소스는 KRX 개별 종목 OHLCV를 2014-04-23부터만 제공한다
    # (지수는 그 이전도 제공되어 착시 발생 — 2014년 이전을 넣으면 '매매 0건'이 수익률 0%로 위장됨).
    # 따라서 검증 가능한 최대 기간인 약 12년으로 정직하게 라벨링한다.
    start_date = "2014-04-23"
    end_date = "2026-07-19"

    print(f"=== 국면(BULL/BEAR) + 점수 진입 로직 백테스트 (거래비용 포함) ===")
    print(f"테스트 기간: {start_date} ~ {end_date} (FDR 데이터 한계로 12년)")
    print(f"테스트 유니버스: KOSPI 상위 {len(universe)}개 종목")

    bt = VectorBacktester(
        tickers=universe,
        start_date=start_date,
        end_date=end_date,
        initial_capital=100_000_000.0, # 1억 원
        # 국면별 최소 보유일 (그리드 실험 A안 검증: 균일 7일 대비 수익률 159.89%->174.96%,
        # MDD -30.20%->-31.08%로 위험조정 수익 개선. NEUTRAL/BEAR 단축은 악화되어 7일 유지,
        # BULL만 14일로 연장 — strategy_engine.py의 MIN_HOLDING_DAYS_BULL과 동일한 값)
        min_hold_days={"BULL": 14, "NEUTRAL": 7, "BEAR": 7, "DEFAULT": 7}
    )
    
    bt.run()
    
    print("\n=== [백테스트 결과] ===")
    summary = bt.get_summary()
    print(json.dumps(summary, indent=4, ensure_ascii=False))

    # 연도별 심층 분석용 전체 거래 내역 저장
    trades_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports", "backtest_trades.csv")
    os.makedirs(os.path.dirname(trades_path), exist_ok=True)
    pd.DataFrame(bt.trade_history).to_csv(trades_path, index=False, encoding="utf-8-sig")
    print(f"거래 내역 저장: {trades_path} ({len(bt.trade_history)}건)")

    # 그래프 그리기
    # (주의) 함수 안에서 pandas를 다시 import하면 pd가 지역변수로 취급되어
    # 그보다 앞선 pd 참조가 UnboundLocalError로 깨진다 — 모듈 상단 import만 사용할 것.
    try:
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
            
            # Save Image (프로젝트 소유 reports/ 폴더로 저장)
            save_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports", "backtest_20y_equity_curve.png")
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
