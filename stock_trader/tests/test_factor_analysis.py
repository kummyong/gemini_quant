import json
import os

import pytest

from stock_trader.data.db_repository import DbRepository
from stock_trader.core.factor_analysis import load_outcomes_df, compute_ic_table, print_report


@pytest.fixture
def repo(tmp_path):
    db_path = os.path.join(str(tmp_path), "test_factor_analysis.db")
    return DbRepository(db_path)


def _features(rsi, score):
    return json.dumps({
        "market_regime": "BULL",
        "kospi_return_90d": 5.0,
        "technical": {"rsi_14": rsi, "atr_pct": 3.0, "relative_momentum": 2.0,
                      "is_aligned": True, "is_under_ma120": False, "is_vcp": False, "momentum_5d": 1.0},
        "fundamental": {"eps_growth": 10.0, "net_buying": 1.0, "dart_revenue_growth": 5.0,
                        "dart_op_growth": 5.0, "dart_debt_ratio": 80.0, "dart_cf_quality": 60.0,
                        "dart_dividend_yield": 2.0},
        "score": score,
    }, ensure_ascii=False)


def test_empty_outcomes_returns_empty_df(repo):
    df = load_outcomes_df(repo)
    assert df.empty
    # 빈 데이터에 대해 리포트 출력이 예외 없이 동작해야 함
    print_report(df)


def test_ic_detects_strong_monotonic_relationship(repo):
    """score가 낮을수록(더 저평가) 수익률이 높게 나오는 인위적 데이터를 넣으면
    score 팩터의 IC가 강한 음의 상관으로 잡혀야 한다."""
    for i in range(20):
        score = 100.0 - i * 3.0          # 100, 97, ..., 43
        ret = i * 1.5                     # 0, 1.5, ..., 28.5 (score와 역상관)
        repo.record_position_entry("KIWOOM", f"T{i:03d}", f"종목{i}", entry_price=10000.0,
                                    entry_signal_type="역추세", entry_features=_features(rsi=30.0, score=score))
        entry = repo.get_position_entry("KIWOOM", f"T{i:03d}")
        repo.record_trade_outcome(
            broker_id="KIWOOM", ticker=f"T{i:03d}", name=f"종목{i}",
            entry_date=entry["entry_date"], entry_price=10000.0,
            entry_signal_type="역추세", entry_features=_features(rsi=30.0, score=score),
            exit_price=10000.0 * (1 + ret / 100.0), exit_reason="trailing_stop",
            quantity=1, return_pct=ret, holding_days=5, position_closed=True,
        )
        repo.mark_holding_sold("KIWOOM", f"T{i:03d}")

    df = load_outcomes_df(repo)
    assert len(df) == 20

    ic_table = compute_ic_table(df)
    assert not ic_table.empty
    score_row = ic_table[ic_table["factor"] == "score"].iloc[0]
    assert score_row["ic"] < -0.9, f"강한 단조 역상관 데이터인데 IC가 약하게 나옴: {score_row['ic']}"

    # 리포트 출력이 예외 없이 동작해야 함 (min_trades 미달 경고 포함)
    print_report(df, min_trades=30)
