import os
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from unittest.mock import patch

from stock_trader.data.db_repository import DbRepository
from stock_trader.broker.broker_factory import BrokerFactory
from stock_trader.broker.adapters.mock_adapters import KoreaInvestmentAdapter
from stock_trader.core import auto_trader
from stock_trader.core.auto_trader import process_pending_signals


@pytest.fixture
def test_pipeline_env(tmp_path):
    """임시 DB와 Mock 증권사 어댑터로 격리된 자동매매 파이프라인 환경 구성"""
    db_file = tmp_path / "test_pipeline.db"
    repo = DbRepository(str(db_file))

    # auto_trader의 전역 REPO를 테스트용 repo로 오버라이드
    auto_trader._REPO = repo

    # Mock어댑터 생성 및 BrokerFactory에 등록
    mock_adapter = KoreaInvestmentAdapter(initial_balance=10000000.0, use_mock=True)
    # mock_adapter의 get_current_price 메서드가 유효한 가격을 반환하도록 보장
    if not hasattr(mock_adapter, "get_current_price"):
        mock_adapter.get_current_price = lambda ticker: 70000.0
    else:
        original_get_price = mock_adapter.get_current_price
        mock_adapter.get_current_price = lambda ticker: 70000.0 if not original_get_price(ticker) else original_get_price(ticker)

    BrokerFactory._brokers["KIWOOM"] = mock_adapter

    # 텔레그램 메시지 모킹
    with patch("stock_trader.core.auto_trader.send_telegram_message") as mock_tg:
        yield repo, mock_adapter, mock_tg

    # Cleanup
    auto_trader._REPO = None
    if "KIWOOM" in BrokerFactory._brokers:
        del BrokerFactory._brokers["KIWOOM"]


def test_buy_signal_pipeline_success(test_pipeline_env):
    repo, adapter, mock_tg = test_pipeline_env

    # 1. strategy_engine이 생성한 매수 신호 시뮬레이션
    sig_id = repo.save_trade_signal(
        ticker="005930",
        name="삼성전자",
        action="BUY",
        quantity=10,
        reason="BULL_FACTOR_VCP",
        status="PENDING",
        broker_id="KIWOOM"
    )

    assert len(repo.get_pending_signals()) == 1

    # 2. auto_trader 신호 처리 및 주문 집행
    process_pending_signals()

    # 3. 검증: PENDING 신호가 처리되어 DONE 상태로 전환되었는지 확인
    pending_after = repo.get_pending_signals()
    assert len(pending_after) == 0

    # 4. 검증: trade_history에 정상 기록이 추가되었는지 확인
    history = repo.get_trades_on_date("")  # 전체 이력 조회
    assert len(history) >= 1
    buy_trade = history[0]
    assert buy_trade["ticker"] == "005930"
    assert buy_trade["side"] == "BUY"
    assert buy_trade["quantity"] == 10
    assert buy_trade["reason"] == "BULL_FACTOR_VCP"


def test_sell_signal_pipeline_success(test_pipeline_env):
    repo, adapter, mock_tg = test_pipeline_env

    # 0. 포트폴리오에 기존 보유 종목 등록
    repo.update_portfolio_holding(
        stk_cd="005930",
        stk_nm="삼성전자",
        rmnd_qty=10,
        pur_pric=70000.0,
        cur_prc=75000.0,
        prft_rt=7.14,
        max_profit_rate=7.14,
        broker_id="KIWOOM"
    )

    # 1. 매도 신호 시뮬레이션
    sig_id = repo.save_trade_signal(
        ticker="005930",
        name="삼성전자",
        action="SELL",
        quantity=10,
        reason="TAKE_PROFIT",
        status="PENDING",
        broker_id="KIWOOM"
    )

    assert len(repo.get_pending_signals()) == 1

    # 2. auto_trader 신호 처리
    process_pending_signals()

    # 3. 검증: PENDING 신호가 제거되었는지 확인
    assert len(repo.get_pending_signals()) == 0

    # 4. 검증: trade_history에 SELL 기록 생성 확인
    history = repo.get_trades_on_date("")
    sell_trades = [t for t in history if t["side"] == "SELL" and t["ticker"] == "005930"]
    assert len(sell_trades) == 1
    assert sell_trades[0]["quantity"] == 10
    assert sell_trades[0]["reason"] == "TAKE_PROFIT"


@pytest.mark.parametrize("age_hours", [9.0, 12.0])
def test_stale_signal_is_cancelled_not_rejuvenated(test_pipeline_env, age_hours):
    """9~13시간 경과한 스테일 신호가 취소되는지 검증 (회귀 방지).

    과거 auto_trader에는 'age_hours >= 8.5면 9시간 차감'하는 UTC 보정 휴리스틱이 있었는데,
    created_at이 KST로 기록되도록 근본 수정된 뒤에는 이 보정이 진짜 오래된 신호를
    0~4시간으로 '회춘'시켜 SIGNAL_MAX_AGE_HOURS(4h) 가드를 무력화했다.
    이 테스트는 그 보정이 다시 들어오면 실패한다.
    """
    repo, adapter, mock_tg = test_pipeline_env

    sig_id = repo.save_trade_signal(
        ticker="005930", name="삼성전자", action="BUY", quantity=10,
        reason="STALE_TEST", status="PENDING", broker_id="KIWOOM",
    )

    # created_at을 KST 기준 age_hours 시간 전으로 조작
    stale_kst = datetime.now(timezone(timedelta(hours=9))) - timedelta(hours=age_hours)
    with sqlite3.connect(repo.db_path) as conn:
        conn.execute(
            "UPDATE trade_signals SET created_at = ? WHERE id = ?",
            (stale_kst.strftime("%Y-%m-%d %H:%M:%S"), sig_id),
        )
        conn.commit()

    initial_history_len = len(repo.get_trades_on_date(""))

    process_pending_signals()

    # 스테일 신호는 체결되지 않아야 한다
    assert len(repo.get_trades_on_date("")) == initial_history_len
    assert not any(s["id"] == sig_id for s in repo.get_pending_signals())


def test_fresh_signal_is_not_cancelled(test_pipeline_env):
    """신선한 신호(1시간 경과)는 만료 처리되지 않고 정상 체결되어야 한다."""
    repo, adapter, mock_tg = test_pipeline_env

    sig_id = repo.save_trade_signal(
        ticker="005930", name="삼성전자", action="BUY", quantity=10,
        reason="FRESH_TEST", status="PENDING", broker_id="KIWOOM",
    )

    fresh_kst = datetime.now(timezone(timedelta(hours=9))) - timedelta(hours=1)
    with sqlite3.connect(repo.db_path) as conn:
        conn.execute(
            "UPDATE trade_signals SET created_at = ? WHERE id = ?",
            (fresh_kst.strftime("%Y-%m-%d %H:%M:%S"), sig_id),
        )
        conn.commit()

    process_pending_signals()

    history = repo.get_trades_on_date("")
    assert any(t["ticker"] == "005930" and t["reason"] == "FRESH_TEST" for t in history)


def test_order_failure_does_not_create_trade_history(test_pipeline_env):
    repo, adapter, mock_tg = test_pipeline_env

    # 주문 실패 응답을 반환하도록 mock_adapter 설정
    adapter.place_order = lambda stock_code, quantity, price, side: {
        "return_code": 1,
        "return_msg": "잔고 부족으로 인한 주문 접수 실패"
    }

    # 매수 신호 저장
    sig_id = repo.save_trade_signal(
        ticker="035720",
        name="카카오",
        action="BUY",
        quantity=10,
        reason="MOMENTUM_TEST",
        status="PENDING",
        broker_id="KIWOOM"
    )

    initial_history_len = len(repo.get_trades_on_date(""))

    # auto_trader 신호 처리 실행
    process_pending_signals()

    # 검증 1: 주문 실패 시 trade_history에 트랜잭션 기록이 추가되지 않아야 함
    current_history = repo.get_trades_on_date("")
    assert len(current_history) == initial_history_len

    # 검증 2: 주문 실패 처리되어 CANCELLED로 변경되었거나 PENDING이 유지됨 (잘못된 DONE이 아님)
    pending_signals = repo.get_pending_signals()
    assert not any(s["id"] == sig_id and s["status"] == "DONE" for s in pending_signals)
