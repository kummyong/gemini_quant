import os
import pytest
from unittest.mock import MagicMock

from stock_trader.data.db_repository import DbRepository
from stock_trader.core.reconciler import reconcile


@pytest.fixture
def repo(tmp_path):
    db_path = os.path.join(str(tmp_path), "test_reconciler.db")
    return DbRepository(db_path)


def make_broker(holdings):
    broker = MagicMock()
    broker.get_account_summary.return_value = {
        "acnt_evlt_remn_indv_tot": holdings
    }
    return broker


def test_ghost_position_cleanup(repo):
    """시나리오 a) DB에는 있으나 계좌에는 없는 유령 포지션은 DB에서 정리되어야 한다."""
    repo.update_portfolio_holding(
        stk_cd="005930", stk_nm="삼성전자", rmnd_qty=10,
        pur_pric=70000, cur_prc=72000, prft_rt=2.85, max_profit_rate=5.0,
        broker_id="KIWOOM",
    )
    broker = make_broker([])  # 종목 1개뿐이라 계좌 리셋 임계치 미만 -> 유령 포지션으로 처리
    result = reconcile("KIWOOM", broker, repo, notify=False)

    assert result["success"] is True
    assert result["account_reset_detected"] is False
    assert result["ghost_positions"] == ["005930"]
    assert repo.get_portfolio_holdings() == []


def test_unrecorded_position_registered(repo):
    """시나리오 b) 계좌에는 있으나 DB에 없는 포지션은 신규 등록되고
    peak_close(=max_profit_rate)는 현재 평균단가를 기준으로 0.0 초기화된다."""
    broker = make_broker([
        {"stk_cd": "A000660", "stk_nm": "SK하이닉스", "rmnd_qty": 5,
         "pchs_amt": 1000000, "evlt_amt": 1050000, "prft_rt": "5.0"}
    ])
    result = reconcile("KIWOOM", broker, repo, notify=False)

    assert result["success"] is True
    assert result["unrecorded_positions"] == ["000660"]
    rows = repo.get_portfolio_holdings()
    assert len(rows) == 1
    assert rows[0]["stk_cd"] == "000660"
    assert rows[0]["rmnd_qty"] == 5
    assert rows[0]["max_profit_rate"] == 0.0


def test_quantity_price_mismatch_updates_from_broker_but_preserves_peak(repo):
    """시나리오 c) 수량/단가 불일치 시 브로커 값으로 갱신하되,
    브로커가 알 수 없는 전략 상태(max_profit_rate)는 절대 덮어쓰지 않는다."""
    repo.update_portfolio_holding(
        stk_cd="005930", stk_nm="삼성전자", rmnd_qty=10,
        pur_pric=70000, cur_prc=72000, prft_rt=2.85, max_profit_rate=8.5,
        broker_id="KIWOOM",
    )
    # 계좌 실측: 수량 7주로 상이 (부분 체결 등으로 DB가 못 따라간 상황을 가정)
    broker = make_broker([
        {"stk_cd": "A005930", "stk_nm": "삼성전자", "rmnd_qty": 7,
         "pchs_amt": 490000, "evlt_amt": 504000, "prft_rt": "2.85"}
    ])
    result = reconcile("KIWOOM", broker, repo, notify=False)

    assert result["success"] is True
    assert result["mismatched_positions"] == ["005930"]
    rows = repo.get_portfolio_holdings()
    assert len(rows) == 1
    assert rows[0]["rmnd_qty"] == 7
    assert rows[0]["pur_pric"] == 70000  # 490000 / 7
    # 전략 상태(peak_close 기반 max_profit_rate)는 절대 덮어쓰지 않음
    assert rows[0]["max_profit_rate"] == 8.5


def test_account_reset_detected_and_stopped_positions_preserved(repo):
    """시나리오 d) 계좌가 완전히 비어있는데 DB에 다수 포지션이 남아있으면
    모의계좌 리셋으로 간주해 포지션을 일괄 정리하되, stopped_positions(쿨다운 기록)는 보존한다."""
    repo.update_portfolio_holding(
        stk_cd="005930", stk_nm="삼성전자", rmnd_qty=10,
        pur_pric=70000, cur_prc=72000, prft_rt=2.85, max_profit_rate=5.0,
        broker_id="KIWOOM",
    )
    repo.update_portfolio_holding(
        stk_cd="000660", stk_nm="SK하이닉스", rmnd_qty=5,
        pur_pric=100000, cur_prc=105000, prft_rt=5.0, max_profit_rate=6.0,
        broker_id="KIWOOM",
    )
    repo.save_stopped_position("133690", "2026-06-01", 25000.0, "ETF_TREND")

    broker = make_broker([])
    result = reconcile("KIWOOM", broker, repo, notify=False)

    assert result["success"] is True
    assert result["account_reset_detected"] is True
    assert set(result["ghost_positions"]) == {"005930", "000660"}
    assert repo.get_portfolio_holdings() == []
    # 전략 상태인 쿨다운 기록은 계좌 리셋과 무관하게 보존되어야 함
    cooldowns = repo.get_stopped_positions("ETF_TREND")
    assert len(cooldowns) == 1
    assert cooldowns[0]["ticker"] == "133690"


def test_broker_api_failure_returns_not_success(repo):
    """브로커 API 조회 실패 시 success=False를 반환하여 어긋난 원장 위에서
    상위(전략 엔진)가 신호 생성을 하지 않고 중단할 수 있어야 한다."""
    broker = MagicMock()
    broker.get_account_summary.side_effect = Exception("API 연결 끊김")
    result = reconcile("KIWOOM", broker, repo, notify=False)

    assert result["success"] is False
    assert result["error"] is not None


def test_all_zero_account_response_aborts_and_preserves_positions(repo):
    """어댑터가 API 실패 시 반환하는 전부 0 폴백 응답(총자산·예수금 필드는 존재)을
    계좌 리셋으로 오판해 정상 DB 포지션을 전량 삭제하면 안 된다."""
    repo.update_portfolio_holding(
        stk_cd="005930", stk_nm="삼성전자", rmnd_qty=10,
        pur_pric=70000, cur_prc=72000, prft_rt=2.85, max_profit_rate=5.0,
        broker_id="KIWOOM",
    )
    repo.update_portfolio_holding(
        stk_cd="000660", stk_nm="SK하이닉스", rmnd_qty=5,
        pur_pric=100000, cur_prc=105000, prft_rt=5.0, max_profit_rate=6.0,
        broker_id="KIWOOM",
    )

    broker = MagicMock()
    broker.get_account_summary.return_value = {
        "tot_pur_amt": "0",
        "tot_evlt_amt": "0",
        "tot_prft_rt": "0.0",
        "prsm_dpst_aset_amt": "0",
        "acnt_evlt_remn_indv_tot": [],
    }
    result = reconcile("KIWOOM", broker, repo, notify=False)

    assert result["success"] is False
    assert result["account_reset_detected"] is False
    assert result["error"] is not None
    # 포지션이 보존되어야 함
    assert len(repo.get_portfolio_holdings()) == 2
