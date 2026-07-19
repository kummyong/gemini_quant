"""오버슈팅 부분 익절+러너 전환 로직(제미나이 제안 ①)이 백테스트 결과에 미치는 영향 검증.

라이브 strategy_engine.py는 이미 이 로직을 갖고 있지만(O1 실험: 트레일링 청산 평균
+0.33%->+8.25%), 백테스터에는 반영되어 있지 않아 라이브-백테스트 간 괴리가 있었다.
overshoot_exit_fraction=1.0은 러너 없이 오버슈팅 시 전량 청산(구 동작과 유사한 대조군),
0.5는 라이브 기본값(절반 익절 후 러너 전환)이다.
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
MIN_HOLD = {"BULL": 14, "NEUTRAL": 7, "BEAR": 7, "DEFAULT": 7}  # 그리드 실험 A안


def run_one(label, overshoot_fraction):
    bt = VectorBacktester(
        tickers=UNIVERSE_TOP20,
        start_date=start_date,
        end_date=end_date,
        initial_capital=100_000_000.0,
        min_hold_days=MIN_HOLD,
        overshoot_exit_fraction=overshoot_fraction,
    )
    bt.run()
    summary = bt.get_summary()

    trades = pd.DataFrame(bt.trade_history)
    overshoot_stats = {}
    if not trades.empty and 'exit_reason' in trades.columns:
        for reason in trades['exit_reason'].dropna().unique():
            sub = trades[trades['exit_reason'] == reason]
            overshoot_stats[reason] = {
                "count": len(sub),
                "mean_%": round(sub['profit_pct'].mean(), 3),
            }

    return {
        "label": label,
        "return": summary.get("Strategy Return (%)"),
        "mdd": summary.get("MDD (%)"),
        "trades": summary.get("Total Trades"),
        "win_rate": summary.get("Win Rate (%)"),
        "by_exit_reason": overshoot_stats,
    }


def main():
    results = []
    for label, frac in [("no_runner_full_exit(1.0)", 1.0), ("live_default_runner(0.5)", 0.5)]:
        print(f"\n=== {label} ===")
        r = run_one(label, frac)
        print(json.dumps(r, indent=2, ensure_ascii=False))
        results.append(r)

    print(f"\n{'='*80}")
    print("[요약]")
    for r in results:
        print(f"{r['label']:<30} return={r['return']:<10} mdd={r['mdd']:<10} trades={r['trades']:<6} win={r['win_rate']}")


if __name__ == "__main__":
    main()
