"""실거래 학습 기반 분석: trade_outcomes에 쌓인 실현 청산 결과와 진입 시점 팩터 스냅샷을
연결해, 어떤 팩터가 실제로 수익률을 예측하는지(Information Coefficient) 살펴본다.

데이터 출처: auto_trader가 매수 체결 시 portfolio_status.entry_* 컬럼에 진입 가격·전략유형·
팩터 스냅샷(strategy_engine._build_feature_dict)을 저장해두고, 매도 체결 시(전량 청산 또는
오버슈팅 부분 익절) 그 진입 스냅샷과 실현 손익을 묶어 trade_outcomes 테이블에 한 행씩 남긴다.

이 스크립트는 그 데이터를 읽기만 한다 — 실거래 로직이나 스코어 가중치를 자동으로 바꾸지 않는다.
표본이 충분히 쌓인 뒤 사람이 결과를 보고 strategy_hyperparams나 scorer.py의 가중치를
판단해 조정하는 것을 전제로 한다.
"""
import argparse
import json
import logging
from typing import Dict, List, Optional

import pandas as pd

from stock_trader.config import DB_PATH
from stock_trader.data.db_repository import DbRepository

logger = logging.getLogger("FactorAnalysis")

# entry_features JSON에서 IC를 계산할 수치형 팩터 경로 (점 표기 -> 컬럼명)
FACTOR_PATHS = {
    "score": "score",
    "kospi_return_90d": "kospi_return_90d",
    "technical.rsi_14": "rsi_14",
    "technical.atr_pct": "atr_pct",
    "technical.momentum_5d": "momentum_5d",
    "technical.relative_momentum": "relative_momentum",
    "technical.is_aligned": "is_aligned",
    "technical.is_under_ma120": "is_under_ma120",
    "technical.is_vcp": "is_vcp",
    "technical.is_obv_rising": "is_obv_rising",
    "fundamental.eps_growth": "eps_growth",
    "fundamental.net_buying": "net_buying",
    "fundamental.consecutive_buy_days": "consecutive_buy_days",
    "fundamental.has_insider_buying": "has_insider_buying",
    "fundamental.dart_revenue_growth": "dart_revenue_growth",
    "fundamental.dart_op_growth": "dart_op_growth",
    "fundamental.dart_debt_ratio": "dart_debt_ratio",
    "fundamental.dart_cf_quality": "dart_cf_quality",
    "fundamental.dart_dividend_yield": "dart_dividend_yield",
}


def _get_path(d: dict, path: str):
    cur = d
    for part in path.split('.'):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def load_outcomes_df(repo: Optional[DbRepository] = None) -> pd.DataFrame:
    """trade_outcomes를 로드하고 entry_features JSON을 평탄화한 DataFrame으로 변환합니다."""
    repo = repo or DbRepository(DB_PATH)
    rows = repo.get_trade_outcomes()
    if not rows:
        return pd.DataFrame()

    records = []
    for r in rows:
        rec = {
            "id": r["id"], "broker_id": r["broker_id"], "ticker": r["ticker"], "name": r["name"],
            "entry_date": r["entry_date"], "entry_price": r["entry_price"],
            "entry_signal_type": r["entry_signal_type"],
            "exit_date": r["exit_date"], "exit_price": r["exit_price"], "exit_reason": r["exit_reason"],
            "quantity": r["quantity"], "return_pct": r["return_pct"], "holding_days": r["holding_days"],
            "position_closed": bool(r["position_closed"]),
        }
        try:
            feat = json.loads(r["entry_features"]) if r["entry_features"] else {}
        except Exception:
            feat = {}
        rec["market_regime"] = feat.get("market_regime")
        for path, col in FACTOR_PATHS.items():
            val = _get_path(feat, path)
            if isinstance(val, bool):
                val = int(val)
            rec[col] = val
        records.append(rec)

    return pd.DataFrame(records)


def compute_ic_table(df: pd.DataFrame) -> pd.DataFrame:
    """각 팩터와 실현수익률(return_pct)의 스피어만 상관(IC)을 계산합니다."""
    factor_cols = [c for c in FACTOR_PATHS.values() if c in df.columns]
    rows = []
    for col in factor_cols:
        sub = df[[col, "return_pct"]].dropna()
        if len(sub) < 5 or sub[col].nunique() < 2:
            continue
        ic = sub[col].corr(sub["return_pct"], method="spearman")
        if pd.isna(ic):
            continue
        rows.append({"factor": col, "ic": round(float(ic), 3), "n": len(sub)})
    return pd.DataFrame(rows).sort_values("ic", key=lambda s: s.abs(), ascending=False).reset_index(drop=True)


def print_report(df: pd.DataFrame, min_trades: int = 30):
    if df.empty:
        print("아직 trade_outcomes 데이터가 없습니다. 실거래가 몇 건 이상 청산된 뒤 다시 실행하세요.")
        return

    n = len(df)
    print("=" * 62)
    print("실거래 학습 기반 팩터 분석 (trade_outcomes)")
    print("=" * 62)
    print(f"  표본 수         : {n}건 (완전청산 {int(df['position_closed'].sum())}건, "
          f"부분익절 {int((~df['position_closed']).sum())}건)")

    if n < min_trades:
        print(f"  ⚠️  표본이 {min_trades}건 미만입니다 — 아래 IC/통계는 참고용이며 신뢰하기엔 이릅니다.")

    win_rate = (df["return_pct"] > 0).mean() * 100
    print(f"  승률            : {win_rate:.1f}%")
    print(f"  평균 수익률     : {df['return_pct'].mean():+.2f}%")
    print(f"  평균 보유일     : {df['holding_days'].mean():.1f}일")

    print("\n  청산 사유별 평균 수익률:")
    by_reason = df.groupby("exit_reason")["return_pct"].agg(["count", "mean"]).sort_values("count", ascending=False)
    for reason, row in by_reason.iterrows():
        print(f"    - {reason:<22}: {int(row['count']):>4}건, 평균 {row['mean']:+.2f}%")

    if df["entry_signal_type"].notna().any():
        print("\n  진입 유형별 평균 수익률:")
        by_type = df.groupby("entry_signal_type")["return_pct"].agg(["count", "mean"]).sort_values("count", ascending=False)
        for sig_type, row in by_type.iterrows():
            print(f"    - {str(sig_type):<22}: {int(row['count']):>4}건, 평균 {row['mean']:+.2f}%")

    if df["market_regime"].notna().any():
        print("\n  진입 시 시장국면별 평균 수익률:")
        by_regime = df.groupby("market_regime")["return_pct"].agg(["count", "mean"]).sort_values("count", ascending=False)
        for regime, row in by_regime.iterrows():
            print(f"    - {str(regime):<22}: {int(row['count']):>4}건, 평균 {row['mean']:+.2f}%")

    ic_table = compute_ic_table(df)
    if not ic_table.empty:
        print("\n  팩터별 Information Coefficient (스피어만 상관, |IC| 내림차순):")
        print("    ※ |IC| > 0.1이면 약한 예측력, 표본이 적으면 우연일 가능성이 큽니다.")
        for _, row in ic_table.iterrows():
            print(f"    - {row['factor']:<22}: IC {row['ic']:+.3f}  (n={int(row['n'])})")
    else:
        print("\n  IC 계산 가능한 팩터가 없습니다 (표본 부족 또는 팩터 값이 전부 동일).")
    print("=" * 62)


def main():
    parser = argparse.ArgumentParser(description="실거래 trade_outcomes 팩터 분석")
    parser.add_argument("--min-trades", type=int, default=30, help="신뢰 가능하다고 간주할 최소 표본 수")
    parser.add_argument("--export", default=None, help="평탄화된 원본 데이터를 저장할 CSV 경로")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

    df = load_outcomes_df()
    print_report(df, min_trades=args.min_trades)

    if args.export and not df.empty:
        df.to_csv(args.export, index=False, encoding="utf-8-sig")
        logger.info(f"원본 데이터 CSV 저장 완료: {args.export}")


if __name__ == "__main__":
    main()
