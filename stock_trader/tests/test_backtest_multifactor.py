"""backtest_multifactor 백테스터의 핵심 메커니즘 검증 (합성 데이터, 네트워크 불필요).

검증 항목:
- look-ahead 부재: 모든 체결은 신호일 다음 거래일 이후에 발생
- 역추세 진입 → 손절/글로벌 하드스탑 청산 사이클 동작
- 글로벌 하드스탑 발동 시 락아웃 및 쿨다운 기간 매수 차단
- 현금/자산 회계 일관성 및 결정성(재실행 시 동일 결과)
"""
import numpy as np
import pandas as pd
import pytest

from stock_trader.core.backtest_multifactor import (
    BacktestConfig, MultiFactorBacktester, run_backtest
)

DATES = pd.bdate_range("2024-01-02", periods=260)


def make_ohlcv(closes, dates=DATES, volume=200_000):
    closes = pd.Series(list(closes), index=dates[:len(list(closes))], dtype=float)
    return pd.DataFrame({
        "Open": closes.shift(1).fillna(closes.iloc[0]),
        "High": pd.concat([closes, closes.shift(1).fillna(closes.iloc[0])], axis=1).max(axis=1) * 1.005,
        "Low": pd.concat([closes, closes.shift(1).fillna(closes.iloc[0])], axis=1).min(axis=1) * 0.995,
        "Close": closes,
        "Volume": [volume] * len(closes)
    }, index=closes.index)


def make_kospi_bull(n=len(DATES)):
    """완만한 상승 지수 (BULL 국면 유지, 일일 변동 -3% 미만)"""
    closes = 2400.0 * (1.001 ** np.arange(n))
    return make_ohlcv(closes)


def make_crash_stock(n=len(DATES), flat_days=180, drop_rate=0.05, base=10000.0):
    """장기 횡보(미세 상승) 후 급락하는 종목 — 역추세 진입 후 손절 시나리오 유발"""
    closes = []
    price = base
    for i in range(n):
        if i < flat_days:
            price *= 1.0002
        else:
            price *= (1 - drop_rate)
        closes.append(max(price, 100.0))
    return make_ohlcv(closes)


def default_config():
    # 워밍업 150봉 이후부터 시뮬레이션 시작
    return BacktestConfig(
        start=str(DATES[160].date()),
        end=str(DATES[-1].date()),
        initial_cash=100_000_000.0,
        quiet=True
    )


def test_no_lookahead_and_trade_cycle():
    """매수/매도 체결은 반드시 신호일보다 뒤여야 하고, 급락 종목에서 진입→청산 사이클이 발생해야 한다."""
    kospi = make_kospi_bull()
    data = {"AAAAAA": make_crash_stock(drop_rate=0.015)}
    results = run_backtest(default_config(), data, kospi, {"AAAAAA": "테스트A"})

    trades = results["trades"]
    assert len(trades) >= 1, "급락 구간에서 역추세 진입/청산 사이클이 발생해야 함"
    for tr in trades:
        # 체결일은 각 신호일의 다음 거래일 이후 (look-ahead 부재)
        assert tr["entry_date"] > tr["entry_signal_date"], f"진입 look-ahead 발생: {tr}"
        assert tr["exit_date"] > tr["exit_signal_date"], f"청산 look-ahead 발생: {tr}"
        assert tr["exit_date"] > tr["entry_date"]
        assert tr["exit_reason"] in {"hard_stop", "trailing_stop", "overshooting", "replacement", "global_hard_stop"}

    # 자산 회계: 자산 곡선이 존재하고 최종 자산 > 0
    eq = results["equity_curve"]
    assert len(eq) > 0
    assert eq.iloc[-1] > 0
    assert results["metrics"]["n_trades"] == len(trades)


def test_global_hard_stop_triggers_lockout_and_cooldown():
    """급락으로 글로벌 하드스탑이 발동하면 락아웃되고, 쿨다운 기간 동안 신규 진입이 차단되어야 한다."""
    kospi = make_kospi_bull()
    # -6%/일 급락 → 보유분 평가손실이 -5%를 빠르게 하회 → global_hard_stop
    data = {"AAAAAA": make_crash_stock(drop_rate=0.06)}
    cfg = default_config()
    # 락아웃 메커니즘 자체를 검증하는 테스트이므로 발동이 민감한 보유분 기준으로 고정
    cfg.hard_stop_basis = "holdings"
    results = run_backtest(cfg, data, kospi, {"AAAAAA": "테스트A"})

    reasons = {tr["exit_reason"] for tr in results["trades"]}
    assert "global_hard_stop" in reasons, f"글로벌 하드스탑이 발동해야 함 (발생 사유: {reasons})"

    # 락아웃 시작일 확인
    lockout_days = [row["date"] for row in results["daily_log"] if row["lockout"]]
    assert lockout_days, "락아웃 상태가 daily_log에 기록되어야 함"
    lockout_start = min(lockout_days)

    # 쿨다운(달력일) 내 신규 진입 없음
    cooldown_end = lockout_start + pd.Timedelta(days=cfg.hard_stop_cooldown_days)
    entries_in_cooldown = [
        tr for tr in results["trades"]
        if lockout_start < tr["entry_date"] <= cooldown_end
    ]
    # 락아웃 발동 직후 체결되는 청산 주문(다음날 시가)은 허용, '진입'만 검사
    assert entries_in_cooldown == [], f"쿨다운 기간 내 신규 진입 발생: {entries_in_cooldown}"


def test_equity_basis_hard_stop_less_sensitive():
    """hard_stop_basis='equity'는 현금 비중이 높을 때 글로벌 하드스탑 과민 발동을 막아야 한다.
    (포지션 비중 ~10%에서 개별 -6% 손실은 계좌 전체로는 -0.6%라 글로벌 스탑 미발동,
    개별 hard_stop만 발동해야 함)"""
    kospi = make_kospi_bull()
    data = {"AAAAAA": make_crash_stock(drop_rate=0.06)}

    cfg = default_config()
    cfg.hard_stop_basis = "equity"
    results = run_backtest(cfg, data, kospi, {"AAAAAA": "테스트A"})

    reasons = {tr["exit_reason"] for tr in results["trades"]}
    assert "global_hard_stop" not in reasons, f"equity 기준에서는 글로벌 스탑이 발동하면 안 됨: {reasons}"
    assert "hard_stop" in reasons, "개별 하드스탑은 여전히 동작해야 함"


def test_bear_regime_uses_stricter_threshold_and_reduced_size():
    """BEAR 국면(지수 < 50MA)에서는 신규 진입 자체가 더 엄격해야 한다(RSI 25, 비중 축소)."""
    n = len(DATES)
    # 하락 지수 → BEAR
    kospi_closes = 2400.0 * (0.999 ** np.arange(n))
    kospi = make_ohlcv(kospi_closes)
    data = {"AAAAAA": make_crash_stock(drop_rate=0.015)}

    cfg = default_config()
    bt = MultiFactorBacktester(cfg, data, kospi, {"AAAAAA": "테스트A"})
    regime, rsi_thres, bb_std, _ = bt._determine_regime(kospi.tail(cfg.regime_window))
    assert regime == "BEAR"
    assert rsi_thres == cfg.bear_rsi
    assert bb_std == cfg.bear_bb

    # BULL 지수 검증 (반대 케이스)
    bull = make_kospi_bull()
    regime_b, rsi_b, bb_b, _ = bt._determine_regime(bull.tail(cfg.regime_window))
    assert regime_b == "BULL"
    assert rsi_b == cfg.bull_rsi


def test_replacement_discipline_gates():
    """min_holding_days / replacement_grace_days가 교체 매도를 게이트해야 한다.
    리스크 청산이 아닌 replacement에만 적용된다."""
    from stock_trader.core.backtest_multifactor import Position

    kospi = make_kospi_bull()
    stock = make_ohlcv([10000.0] * 260)
    cfg = default_config()
    cfg.min_holding_days = 5
    cfg.replacement_grace_days = 3
    bt = MultiFactorBacktester(cfg, {"AAAAAA": stock}, kospi, {"AAAAAA": "테스트A"})
    t = DATES[200]
    feats = {"AAAAAA": {"rsi": 50.0, "upper_band": 999_999_999.0, "atr_pct": 3.0}}

    # 보유 2일차 + 순위 이탈 1일째 → 두 조건 모두 미충족, 교체 금지
    bt.positions["AAAAAA"] = Position(
        ticker="AAAAAA", name="테스트A", quantity=10, avg_price=10000.0,
        entry_date=t - pd.Timedelta(days=2), signal_date=t - pd.Timedelta(days=3),
        signal_type="역추세")
    bt.out_of_top_streak["AAAAAA"] = 0
    exits = bt._generate_exits(t, feats, top_tickers=[])
    assert exits == [], "최소 보유일/유예 미충족 시 교체 매도가 발생하면 안 됨"

    # 보유 10일차 + 이탈 streak 3 충족 → 교체 발동
    bt.positions["AAAAAA"].entry_date = t - pd.Timedelta(days=10)
    bt.out_of_top_streak["AAAAAA"] = 2  # _generate_exits에서 +1 → 3
    exits = bt._generate_exits(t, feats, top_tickers=[])
    assert len(exits) == 1 and exits[0].reason == "replacement"

    # 순위 복귀 시 streak 리셋 확인
    bt.out_of_top_streak["AAAAAA"] = 2
    bt._generate_exits(t, feats, top_tickers=["AAAAAA"])
    assert bt.out_of_top_streak["AAAAAA"] == 0


def test_overshoot_partial_exit_creates_runner():
    """overshoot_exit_fraction < 1.0이면 오버슈팅 시 부분 익절 후 잔여 물량이
    러너로 전환되고, 러너에는 오버슈팅이 재발동하지 않아야 한다."""
    from stock_trader.core.backtest_multifactor import Position

    kospi = make_kospi_bull()
    stock = make_ohlcv([12000.0] * 260)
    cfg = default_config()
    cfg.overshoot_exit_fraction = 0.5
    bt = MultiFactorBacktester(cfg, {"AAAAAA": stock}, kospi, {"AAAAAA": "테스트A"})
    t = DATES[200]
    bt.positions["AAAAAA"] = Position(
        ticker="AAAAAA", name="테스트A", quantity=10, avg_price=10000.0,
        entry_date=t - pd.Timedelta(days=10), signal_date=t - pd.Timedelta(days=11),
        signal_type="역추세")
    feats = {"AAAAAA": {"rsi": 75.0, "upper_band": 0.0, "atr_pct": 3.0}}

    # 오버슈팅(RSI 75) → 절반(5주)만 익절 주문, 러너 마킹
    exits = bt._generate_exits(t, feats, top_tickers=["AAAAAA"])
    assert len(exits) == 1
    assert exits[0].quantity == 5
    assert exits[0].reason == "overshooting_partial"
    assert bt.positions["AAAAAA"].runner is True

    # 체결 후 잔여 5주 유지 + 트레이드 기록은 부분 물량
    bt.pending_orders = exits
    t2 = DATES[201]
    bt._execute_pending(t2)
    assert bt.positions["AAAAAA"].quantity == 5
    assert bt.trades[-1]["quantity"] == 5
    assert bt.trades[-1]["exit_reason"] == "overshooting_partial"

    # 러너에는 오버슈팅 재발동 없음 (트레일링/하드스탑만 적용)
    exits = bt._generate_exits(t2, feats, top_tickers=["AAAAAA"])
    assert exits == []


def test_deterministic_rerun():
    """동일 입력으로 두 번 실행하면 결과가 완전히 같아야 한다 (상태 누수 방지)."""
    kospi = make_kospi_bull()
    data = {"AAAAAA": make_crash_stock(drop_rate=0.02)}
    r1 = run_backtest(default_config(), data, kospi, {"AAAAAA": "테스트A"})
    r2 = run_backtest(default_config(), data, kospi, {"AAAAAA": "테스트A"})
    assert r1["metrics"] == r2["metrics"]
    assert len(r1["trades"]) == len(r2["trades"])
    pd.testing.assert_series_equal(r1["equity_curve"], r2["equity_curve"])


def test_universe_filter_blocks_penny_and_illiquid():
    """동전주/거래대금 미달 종목은 진입 대상에서 제외되어야 한다."""
    kospi = make_kospi_bull()
    # 500원짜리 동전주 (급락 패턴이어도 매수 금지)
    penny = make_crash_stock(drop_rate=0.015, base=500.0)
    results = run_backtest(default_config(), {"AAAAAA": penny}, kospi, {"AAAAAA": "동전주"})
    assert results["trades"] == [], "동전주는 1차 필터에서 걸러져 거래가 없어야 함"

    # 거래대금 부족 (가격은 정상, 거래량 극소)
    illiquid = make_crash_stock(drop_rate=0.015, base=10000.0)
    illiquid["Volume"] = 100  # 10,000원 × 100주 = 100만 원 << 5억
    results = run_backtest(default_config(), {"BBBBBB": illiquid}, kospi, {"BBBBBB": "저유동"})
    assert results["trades"] == [], "거래대금 미달 종목은 거래가 없어야 함"
