import json
import os

import pytest

from stock_trader.data.db_repository import DbRepository


@pytest.fixture
def repo(tmp_path):
    db_path = os.path.join(str(tmp_path), "test_trade_outcomes.db")
    return DbRepository(db_path)


def make_features(score=80.0, rsi=28.0, regime="BULL"):
    return json.dumps({
        "market_regime": regime,
        "kospi_return_90d": 5.0,
        "technical": {"rsi_14": rsi, "atr_pct": 3.0, "relative_momentum": 2.0,
                      "is_aligned": True, "is_under_ma120": False, "is_vcp": False,
                      "momentum_5d": 1.0},
        "fundamental": {"eps_growth": 10.0, "net_buying": 1.0, "dart_revenue_growth": 5.0,
                        "dart_op_growth": 5.0, "dart_debt_ratio": 80.0, "dart_cf_quality": 60.0,
                        "dart_dividend_yield": 2.0},
        "score": score,
    }, ensure_ascii=False)


def test_record_position_entry_preserves_first_entry_on_averaging(repo):
    """추가매수 시 최초 진입가·진입피처가 유지되어야 한다 (신규 진입만 기록)."""
    repo.record_position_entry("KIWOOM", "005930", "삼성전자", entry_price=70000.0,
                                entry_signal_type="역추세", entry_features=make_features(score=80.0))
    entry = repo.get_position_entry("KIWOOM", "005930")
    assert entry["entry_price"] == 70000.0
    assert entry["entry_signal_type"] == "역추세"

    # 같은 종목에 추가매수 발생 (다른 가격/피처) -> 최초 진입 정보가 덮어써지면 안 됨
    repo.record_position_entry("KIWOOM", "005930", "삼성전자", entry_price=75000.0,
                                entry_signal_type="추세추종", entry_features=make_features(score=50.0))
    entry2 = repo.get_position_entry("KIWOOM", "005930")
    assert entry2["entry_price"] == 70000.0
    assert entry2["entry_signal_type"] == "역추세"


def test_get_position_entry_returns_empty_when_absent(repo):
    entry = repo.get_position_entry("KIWOOM", "999999")
    assert entry["entry_date"] is None
    assert entry["entry_price"] is None


def test_partial_exit_preserves_entry_snapshot_full_close_clears_it(repo):
    """오버슈팅 부분 익절(러너 전환)은 진입 스냅샷을 보존해야 하고,
    이후 완전 청산(mark_holding_sold)에서만 스냅샷이 초기화되어야 한다."""
    repo.record_position_entry("KIWOOM", "005930", "삼성전자", entry_price=70000.0,
                                entry_signal_type="역추세", entry_features=make_features())

    # 1차: 오버슈팅 부분 익절 (포지션 유지)
    repo.record_trade_outcome(
        broker_id="KIWOOM", ticker="005930", name="삼성전자",
        entry_date=repo.get_position_entry("KIWOOM", "005930")["entry_date"],
        entry_price=70000.0, entry_signal_type="역추세", entry_features=make_features(),
        exit_price=77000.0, exit_reason="overshooting_partial", quantity=5,
        return_pct=10.0, holding_days=7, position_closed=False,
    )
    # 부분 익절 후에도 진입 스냅샷은 살아있어야 함
    entry_after_partial = repo.get_position_entry("KIWOOM", "005930")
    assert entry_after_partial["entry_price"] == 70000.0

    # 2차: 트레일링 스탑으로 잔여 물량 완전 청산
    repo.record_trade_outcome(
        broker_id="KIWOOM", ticker="005930", name="삼성전자",
        entry_date=entry_after_partial["entry_date"], entry_price=70000.0,
        entry_signal_type="역추세", entry_features=make_features(),
        exit_price=74000.0, exit_reason="trailing_stop", quantity=5,
        return_pct=(74000.0 - 70000.0) / 70000.0 * 100, holding_days=10, position_closed=True,
    )
    repo.mark_holding_sold("KIWOOM", "005930")

    # 완전 청산 후에는 진입 스냅샷이 초기화되어 다음 신규 진입을 받을 준비가 되어야 함
    entry_after_full = repo.get_position_entry("KIWOOM", "005930")
    assert entry_after_full["entry_date"] is None
    assert entry_after_full["entry_price"] is None

    outcomes = repo.get_trade_outcomes()
    assert len(outcomes) == 2
    reasons = {o["exit_reason"] for o in outcomes}
    assert reasons == {"overshooting_partial", "trailing_stop"}
    closed_flags = sorted(o["position_closed"] for o in outcomes)
    assert closed_flags == [0, 1]


def test_new_entry_after_full_close_starts_fresh(repo):
    """완전 청산 후 같은 종목에 재진입하면 새 진입 스냅샷이 기록되어야 한다."""
    repo.record_position_entry("KIWOOM", "005930", "삼성전자", entry_price=70000.0,
                                entry_signal_type="역추세", entry_features=make_features())
    repo.mark_holding_sold("KIWOOM", "005930")
    assert repo.get_position_entry("KIWOOM", "005930")["entry_date"] is None

    repo.record_position_entry("KIWOOM", "005930", "삼성전자", entry_price=90000.0,
                                entry_signal_type="추세추종", entry_features=make_features())
    entry = repo.get_position_entry("KIWOOM", "005930")
    assert entry["entry_price"] == 90000.0
    assert entry["entry_signal_type"] == "추세추종"
