"""STOCK_MULTIFACTOR 전략 백테스터.

strategy_engine의 주식 멀티팩터 전략을 일봉 기준으로 점진(walk-forward) 시뮬레이션한다.
프로덕션 코드 경로(market_features.compute_technical_features, scorer.MultiFactorScorer,
indicators.adx/sma)를 그대로 재사용하여 라이브 신호 로직과의 괴리를 최소화한다.

한계 (반드시 인지할 것):
- 네이버 EPS 성장률/수급, DART 재무 팩터는 과거 시점(point-in-time) 데이터를 구할 수 없어
  중립값으로 고정된다. 따라서 이 백테스트가 검증하는 것은 전략의 '기술적 코어'다:
  국면 판독(50MA/ADX), RSI/BB 역추세 진입, 추세추종 진입, 상대모멘텀/정배열/VCP 보너스,
  동적 손절/트레일링/오버슈팅/교체 청산, 글로벌 하드스탑 + 락아웃, ATR 기반 자금관리.
- 장중 1분 감시(auto_trader) 대신 일봉 종가로 청산을 판정한다 (실거래 대비 보수적).
- 매수는 신호 다음 날, 눌림목 대기 로직(목표가 터치 0.5% 마진 / 추격 상한)을 일봉으로 근사한다.

실행 예 (프로젝트 루트에서):
    py -m stock_trader.core.backtest_multifactor --start 2024-01-01 --end 2025-12-31
    py -m stock_trader.core.backtest_multifactor --start 2024-01-01 --tickers 005930,000660,035420
"""
import argparse
import logging
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple

import pandas as pd

import stock_trader.core.indicators as ind
from stock_trader.core.market_features import compute_technical_features
from stock_trader.core.scorer import MultiFactorScorer, apply_sector_momentum

logger = logging.getLogger("MultiFactorBacktest")


@dataclass
class BacktestConfig:
    start: str = "2024-01-01"
    end: str = "2026-12-31"
    initial_cash: float = 100_000_000.0

    # 포트폴리오/자금 관리 (strategy_engine 기본값과 동일)
    top_n: int = 5
    target_weight: float = 0.10
    max_single_order_ratio: float = 0.20

    # 국면별 진입 파라미터 (_load_hyperparams / _determine_market_regime 기본값)
    bull_rsi: float = 30.0
    bull_bb: float = 2.0
    bear_rsi: float = 25.0
    bear_bb: float = 2.2
    sideway_rsi: float = 45.0
    sideway_bb: float = 1.8
    rsi_sell_thres: float = 70.0
    bear_position_factor: float = 0.5

    # 리스크 관리
    hard_stop_loss: float = -5.0          # 글로벌 하드스탑 %
    hard_stop_basis: str = "equity"       # "equity": 계좌 전체(현금 포함) 대비(라이브 현행) | "holdings": 보유분 매입금 대비(구 버전)
    hard_stop_cooldown_days: int = 5      # 락아웃 최소 유지 일수 (해제엔 BULL 국면 필요)
    circuit_breaker_drop: float = -3.0    # KOSPI 당일 등락률 서킷 브레이커 %

    # 추세추종 진입 문턱 (_generate_stock_buy_signals 하드코딩 값의 실험용 노출)
    tf_rsi_min: float = 45.0
    tf_rsi_max: float = 65.0
    tf_rel_momentum_min: float = 3.0      # 상대강도(vs KOSPI 90일) 최소 %

    # 교체 매도 규율 (리스크 청산에는 미적용, replacement에만 적용)
    min_holding_days: int = 5             # 교체 매도 전 최소 보유 달력일 (라이브 현행: MIN_HOLDING_DAYS=5)
    replacement_grace_days: int = 0       # 순위(top_n) 연속 이탈 유예 거래일 (0 = 라이브 현행: 미사용)

    # 러너 관리: 오버슈팅 익절 시 매도 비율 (1.0 = 전량 익절, 라이브 현행)
    # 1.0 미만이면 해당 비율만 익절하고 잔여 물량은 러너로 전환되어
    # 트레일링/하드스탑으로만 청산된다 (오버슈팅 재발동 없음).
    overshoot_exit_fraction: float = 1.0

    # 체결 모델 (auto_trader 근사)
    target_touch_margin: float = 0.005    # 목표가 터치 인정 마진 (0.5%)
    max_chase_pct: float = 2.0            # 마감 임박 추격 허용 폭 %
    fee_buy: float = 0.00015              # 매수 수수료
    fee_sell: float = 0.00215             # 매도 수수료 + 거래세(0.20%)
    slippage: float = 0.0005              # 시장가 슬리피지 (편도)

    # 유니버스 1차 필터 (fetch_market_data와 동일)
    min_price: float = 1000.0
    min_avg_value_5d: float = 500_000_000.0

    # 데이터 윈도우 (라이브 동작 복제: get_recent_ohlcv(limit=150), 국면 판독 약 180일)
    feature_window: int = 150
    regime_window: int = 130

    quiet: bool = True  # scorer/feature 모듈의 종목별 INFO 로그 억제


@dataclass
class Position:
    ticker: str
    name: str
    quantity: int
    avg_price: float
    entry_date: pd.Timestamp
    signal_date: pd.Timestamp
    signal_type: str
    max_profit_rate: float = 0.0
    runner: bool = False   # 부분 익절 후 잔여 물량 (오버슈팅 재발동 제외)


@dataclass
class Order:
    side: str                 # "BUY" | "SELL"
    ticker: str
    name: str
    quantity: int
    signal_date: pd.Timestamp
    signal_type: str = ""
    target_price: Optional[float] = None   # BUY 전용 (눌림목 목표가)
    planned_cost: float = 0.0              # BUY 예약 금액 (신호일 종가 기준)
    reason: str = ""


class MultiFactorBacktester:
    """price_data: {ticker: OHLCV DataFrame(DatetimeIndex)}, kospi: KS11 OHLCV DataFrame"""

    def __init__(self, config: BacktestConfig, price_data: Dict[str, pd.DataFrame],
                 kospi: pd.DataFrame, names: Optional[Dict[str, str]] = None):
        self.cfg = config
        self.data = price_data
        self.kospi = kospi.sort_index()
        self.names = names or {t: t for t in price_data}
        self.scorer = MultiFactorScorer()

        if config.quiet:
            logging.getLogger("MultiFactorScorer").setLevel(logging.WARNING)
            logging.getLogger("MarketFeatures").setLevel(logging.WARNING)

        self.cash = config.initial_cash
        self.reserved_cash = 0.0            # 미체결 매수 주문 예약분
        self.positions: Dict[str, Position] = {}
        self.out_of_top_streak: Dict[str, int] = {}  # 보유 종목의 top_n 연속 이탈 거래일 수
        self.pending_orders: List[Order] = []
        self.lockout_since: Optional[pd.Timestamp] = None
        self.trades: List[dict] = []
        self.equity_curve: List[Tuple[pd.Timestamp, float]] = []
        self.daily_log: List[dict] = []

    # ── 국면 판독 (_determine_market_regime 복제) ──────────────────────────
    def _determine_regime(self, kospi_slice: pd.DataFrame) -> Tuple[str, float, float, float]:
        cfg = self.cfg
        closes = kospi_slice['Close'] if not kospi_slice.empty else pd.Series(dtype=float)
        if len(closes) < 50:
            return "BEAR", cfg.bear_rsi, cfg.bear_bb, 0.0

        latest_close = float(closes.iloc[-1])
        latest_ma = float(ind.sma(closes, 50).iloc[-1])
        try:
            adx_series = ind.adx(kospi_slice, 14)
            adx_val = float(adx_series.iloc[-1]) if not pd.isna(adx_series.iloc[-1]) else 25.0
        except Exception:
            adx_val = 25.0

        if len(closes) >= 60:
            ret_90d = ((latest_close - float(closes.iloc[-60])) / float(closes.iloc[-60])) * 100
        else:
            ret_90d = 0.0

        ma_gap_pct = ((latest_close - latest_ma) / latest_ma) * 100 if latest_ma > 0 else 0.0
        if adx_val < 20.0 and ma_gap_pct < 8.0:
            return "SIDEWAY", cfg.sideway_rsi, cfg.sideway_bb, ret_90d
        if latest_close > latest_ma:
            return "BULL", cfg.bull_rsi, cfg.bull_bb, ret_90d
        return "BEAR", cfg.bear_rsi, cfg.bear_bb, ret_90d

    # ── 체결 처리 ──────────────────────────────────────────────────────────
    def _execute_pending(self, t: pd.Timestamp):
        cfg = self.cfg
        still_pending: List[Order] = []
        for order in self.pending_orders:
            df = self.data.get(order.ticker)
            bar = df.loc[t] if (df is not None and t in df.index) else None
            if bar is None:
                # 당일 거래 없음(정지 등) → 신호는 하루살이(만료)
                if order.side == "BUY":
                    self.reserved_cash -= order.planned_cost
                continue

            if order.side == "SELL":
                pos = self.positions.get(order.ticker)
                if pos is None:
                    continue
                sell_qty = min(order.quantity, pos.quantity)
                fill_price = float(bar['Open']) * (1 - cfg.slippage)
                proceeds = sell_qty * fill_price * (1 - cfg.fee_sell)
                self.cash += proceeds
                ret_pct = (fill_price - pos.avg_price) / pos.avg_price * 100
                self.trades.append({
                    "ticker": order.ticker, "name": order.name,
                    "entry_date": pos.entry_date, "exit_date": t,
                    "entry_signal_date": pos.signal_date,
                    "exit_signal_date": order.signal_date,
                    "entry_price": pos.avg_price, "exit_price": fill_price,
                    "quantity": sell_qty,
                    "return_pct": round(ret_pct, 3),
                    "pnl": round(proceeds - sell_qty * pos.avg_price, 0),
                    "holding_days": int((t - pos.entry_date).days),
                    "signal_type": pos.signal_type,
                    "exit_reason": order.reason
                })
                if sell_qty >= pos.quantity:
                    del self.positions[order.ticker]
                    self.out_of_top_streak.pop(order.ticker, None)
                else:
                    pos.quantity -= sell_qty  # 부분 익절 → 잔여 물량 유지 (러너)
                continue

            # BUY: 눌림목 목표가 터치 / 추격 상한 근사 (auto_trader 스마트 집행)
            self.reserved_cash -= order.planned_cost
            target = order.target_price or float(bar['Open'])
            limit = target * (1 + cfg.target_touch_margin)
            open_p, low_p, close_p = float(bar['Open']), float(bar['Low']), float(bar['Close'])

            if open_p <= limit:
                fill_price = open_p
            elif low_p <= limit:
                fill_price = limit
            else:
                chase_pct = (close_p - target) / target * 100.0 if target > 0 else 999.0
                if chase_pct <= cfg.max_chase_pct:
                    fill_price = close_p
                else:
                    continue  # 목표가 미도달 + 추격 상한 초과 → 만료

            fill_price *= (1 + cfg.slippage)
            qty = order.quantity
            cost = qty * fill_price * (1 + cfg.fee_buy)
            if cost > self.cash:
                qty = int(self.cash / (fill_price * (1 + cfg.fee_buy)))
                cost = qty * fill_price * (1 + cfg.fee_buy)
            if qty <= 0:
                continue

            self.cash -= cost
            self.positions[order.ticker] = Position(
                ticker=order.ticker, name=order.name, quantity=qty,
                avg_price=fill_price * (1 + cfg.fee_buy),  # 수수료 포함 평균단가
                entry_date=t, signal_date=order.signal_date,
                signal_type=order.signal_type
            )
            self.out_of_top_streak[order.ticker] = 0
        self.pending_orders = still_pending

    # ── 청산 신호 (generate_management_signals 주식 경로 복제) ─────────────
    def _generate_exits(self, t: pd.Timestamp, features: Dict[str, dict],
                        top_tickers: List[str]) -> List[Order]:
        cfg = self.cfg
        exits: List[Order] = []
        if not self.positions:
            return exits

        # 1) 글로벌 하드스탑 (보유분 매입가 대비 전체 평가 수익률)
        total_purchase = sum(p.avg_price * p.quantity for p in self.positions.values())
        total_eval = 0.0
        for tic, pos in self.positions.items():
            df = self.data.get(tic)
            close = float(df.loc[t, 'Close']) if (df is not None and t in df.index) else pos.avg_price
            total_eval += close * pos.quantity
        if total_purchase > 0:
            unrealized = total_eval - total_purchase
            if cfg.hard_stop_basis == "equity":
                # 라이브 현행: 미실현 손실을 계좌 전체(현금 포함) 대비 %로 평가 — 현금 비중이 높을 때 과민 발동 방지
                account_equity = self.cash + total_eval
                portfolio_rate = (unrealized / account_equity * 100) if account_equity > 0 else 0.0
            else:
                # 구 버전(보유분 매입금 대비) — 비교 실험용으로 유지
                portfolio_rate = unrealized / total_purchase * 100
            if portfolio_rate <= cfg.hard_stop_loss:
                self.lockout_since = t
                for tic, pos in self.positions.items():
                    exits.append(Order(side="SELL", ticker=tic, name=pos.name,
                                       quantity=pos.quantity, signal_date=t,
                                       reason="global_hard_stop"))
                return exits

        # 2) 개별 청산
        for tic in self.positions:
            # 교체 규율용: top_n 연속 이탈 거래일 추적
            if tic not in top_tickers:
                self.out_of_top_streak[tic] = self.out_of_top_streak.get(tic, 0) + 1
            else:
                self.out_of_top_streak[tic] = 0

        for tic, pos in self.positions.items():
            df = self.data.get(tic)
            if df is None or t not in df.index:
                continue
            close = float(df.loc[t, 'Close'])
            profit = (close - pos.avg_price) / pos.avg_price * 100
            pos.max_profit_rate = max(pos.max_profit_rate, profit)

            feats = features.get(tic, {})
            rsi_val = feats.get("rsi", 50.0)
            upper_band = feats.get("upper_band", 0.0) or 999_999_999.0
            atr_pct = feats.get("atr_pct", 3.0)
            dynamic_hard_stop = -max(3.0, min(8.0, 1.5 * atr_pct))
            dynamic_trailing_stop = max(2.0, min(5.0, 1.5 * atr_pct))

            reason = None
            if profit <= dynamic_hard_stop:
                reason = "hard_stop"
            elif pos.max_profit_rate >= 2.0 and (pos.max_profit_rate - profit) >= dynamic_trailing_stop:
                reason = "trailing_stop"
            elif (rsi_val >= cfg.rsi_sell_thres or close >= upper_band) and not pos.runner:
                reason = "overshooting"
            elif tic not in top_tickers and profit < 2.0:
                # 교체 규율: 최소 보유일 경과 + 순위 연속 이탈 유예 충족 시에만 교체
                holding_days = (t - pos.entry_date).days
                if (holding_days >= cfg.min_holding_days
                        and self.out_of_top_streak.get(tic, 0) >= cfg.replacement_grace_days):
                    reason = "replacement"

            if reason:
                sell_qty = pos.quantity
                if reason == "overshooting" and cfg.overshoot_exit_fraction < 1.0:
                    part_qty = int(pos.quantity * cfg.overshoot_exit_fraction)
                    if 0 < part_qty < pos.quantity:
                        # 부분 익절 후 잔여 물량은 러너로 전환 (트레일링/하드스탑으로만 청산)
                        pos.runner = True
                        sell_qty = part_qty
                        reason = "overshooting_partial"
                exits.append(Order(side="SELL", ticker=tic, name=pos.name,
                                   quantity=sell_qty, signal_date=t, reason=reason))
        return exits

    # ── 매수 신호 (_generate_stock_buy_signals 복제) ───────────────────────
    def _generate_buys(self, t: pd.Timestamp, regime: str, rsi_buy_thres: float,
                       scored: List[dict], exits: List[Order],
                       circuit_locked: bool) -> List[Order]:
        cfg = self.cfg
        buys: List[Order] = []

        # 글로벌 하드스탑 락아웃 게이트 (쿨다운 경과 + BULL 국면에서만 해제)
        if self.lockout_since is not None:
            days_elapsed = (t - self.lockout_since).days
            if days_elapsed >= cfg.hard_stop_cooldown_days and regime == "BULL":
                self.lockout_since = None
            else:
                return buys
        if circuit_locked:
            return buys

        top = scored[:cfg.top_n]
        if not top:
            return buys
        total_score_sum = sum(s["total_score"] for s in top) or 1.0
        selling = {o.ticker for o in exits}

        equity = self._current_equity(t)
        available_cash = self.cash - self.reserved_cash
        remaining_cash = available_cash

        for s in top:
            tic = s["ticker"]
            if tic in self.positions or tic in selling:
                continue

            rsi_val = s.get("rsi", 50.0)
            lower_band = s.get("lower_band", 0.0)
            price = s["price"]
            score = s["total_score"]
            atr_pct = s.get("atr_pct", 3.0)

            is_rsi_match = rsi_val <= rsi_buy_thres
            is_bb_match = (lower_band > 0) and (price <= lower_band)
            is_mean_reversion = is_rsi_match or is_bb_match
            is_trend_following = (
                regime in ("BULL", "SIDEWAY") and
                s.get("is_aligned", False) and
                cfg.tf_rsi_min <= rsi_val <= cfg.tf_rsi_max and
                s.get("relative_momentum", 0.0) > cfg.tf_rel_momentum_min and
                s.get("is_bottoming", True)
            )

            if is_mean_reversion:
                signal_type = "역추세"
                weight_multiplier = 1.0
                # 라이브 동작: TARGET_PRICE=lower_band. 0이면 auto_trader가 대기 없이 시장가 집행 → None(시가 체결)
                target_price = lower_band if lower_band > 0 else None
                if is_rsi_match and not is_bb_match and not s.get("is_bottoming", True):
                    continue
            elif is_trend_following:
                weight_multiplier = 0.5 if regime == "SIDEWAY" else 0.7
                signal_type = "추세추종"
                target_price = price
            else:
                continue

            if regime.startswith("BEAR"):
                if cfg.bear_position_factor <= 0.0:
                    continue
                weight_multiplier *= cfg.bear_position_factor

            if price <= 0 or remaining_cash <= 0:
                continue

            score_ratio = (score / total_score_sum) * len(top)
            size_factor = max(0.5, min(1.5, 3.0 / atr_pct)) * weight_multiplier
            adjusted_weight = cfg.target_weight * score_ratio * size_factor
            target_amt = min(
                equity * adjusted_weight,
                available_cash * cfg.max_single_order_ratio,
                remaining_cash
            )
            quantity = int(target_amt / price)
            if quantity <= 0:
                continue
            order_cost = quantity * price
            if order_cost > remaining_cash:
                quantity = int(remaining_cash / price)
                order_cost = quantity * price
            if quantity <= 0:
                continue

            remaining_cash -= order_cost
            buys.append(Order(side="BUY", ticker=tic, name=s["name"], quantity=quantity,
                              signal_date=t, signal_type=signal_type,
                              target_price=target_price, planned_cost=order_cost))
        return buys

    # ── 헬퍼 ───────────────────────────────────────────────────────────────
    def _current_equity(self, t: pd.Timestamp) -> float:
        value = 0.0
        for tic, pos in self.positions.items():
            df = self.data.get(tic)
            if df is not None and t in df.index:
                value += float(df.loc[t, 'Close']) * pos.quantity
            else:
                sliced = df.loc[:t] if df is not None else pd.DataFrame()
                value += (float(sliced['Close'].iloc[-1]) if not sliced.empty else pos.avg_price) * pos.quantity
        return self.cash + value

    def _passes_universe_filter(self, window: pd.DataFrame) -> bool:
        """fetch_market_data의 1차 필터 (동전주/거래대금) 복제"""
        if window.empty or len(window) < 5:
            return False
        latest_close = float(window['Close'].iloc[-1])
        avg_value_5d = latest_close * float(window['Volume'].tail(5).mean())
        return latest_close >= self.cfg.min_price and avg_value_5d >= self.cfg.min_avg_value_5d

    # ── 메인 루프 ──────────────────────────────────────────────────────────
    def run(self) -> dict:
        cfg = self.cfg
        start_ts, end_ts = pd.Timestamp(cfg.start), pd.Timestamp(cfg.end)
        trading_days = self.kospi.index[(self.kospi.index >= start_ts) & (self.kospi.index <= end_ts)]

        for t in trading_days:
            # 1) 전일 신호 체결 (당일 시가/저가/종가 사용 — look-ahead 없음)
            self._execute_pending(t)

            # 2) 국면 판독 + 서킷 브레이커 (당일 종가 기준: 신호 생성은 장 마감 후)
            kospi_slice = self.kospi.loc[:t].tail(cfg.regime_window)
            regime, rsi_buy_thres, bb_std, kospi_ret_90d = self._determine_regime(kospi_slice)
            circuit_locked = False
            if len(kospi_slice) >= 2:
                prev_c, cur_c = float(kospi_slice['Close'].iloc[-2]), float(kospi_slice['Close'].iloc[-1])
                circuit_locked = ((cur_c - prev_c) / prev_c * 100) <= cfg.circuit_breaker_drop

            # 3) 종목별 피처 계산 (프로덕션 함수 재사용, 라이브와 동일한 150봉 윈도우)
            features: Dict[str, dict] = {}
            stocks: List[dict] = []
            for tic, df in self.data.items():
                window = df.loc[:t].tail(cfg.feature_window)
                if window.empty or t not in df.index:
                    continue
                close = float(window['Close'].iloc[-1])
                feats = compute_technical_features(
                    window, current_price=close, bb_std=bb_std,
                    kospi_return_90d=kospi_ret_90d, name=self.names.get(tic, tic)
                )
                features[tic] = feats
                if not self._passes_universe_filter(window):
                    continue
                stocks.append({
                    "ticker": tic, "name": self.names.get(tic, tic),
                    # 펀더멘털 팩터는 point-in-time 데이터 부재로 중립 고정 (모듈 docstring 참고)
                    "eps_growth": 0.0, "industry_name": "기타", "net_buying": 0.0,
                    "price": close,
                    "dart_revenue_growth": 0.0, "dart_op_growth": 0.0,
                    "dart_debt_ratio": 100.0, "dart_cf_quality": 50.0,
                    "dart_dividend_yield": 0.0, "dart_major_shareholder_bonus": 0.0,
                    "dart_available": False,
                    **feats,
                    "industry_score": 50.0
                })

            # 4) 스코어링 (프로덕션 스코어러 재사용)
            apply_sector_momentum(stocks)
            scored = self.scorer.calculate_scores(stocks)
            top_tickers = [s["ticker"] for s in scored[:cfg.top_n]]

            # 5) 청산 → 매수 신호 (라이브 run()과 동일한 순서)
            exits = self._generate_exits(t, features, top_tickers)
            buys = self._generate_buys(t, regime, rsi_buy_thres, scored, exits, circuit_locked)

            for order in buys:
                self.reserved_cash += order.planned_cost
            self.pending_orders.extend(exits + buys)

            # 6) 기록
            equity = self._current_equity(t)
            self.equity_curve.append((t, equity))
            self.daily_log.append({
                "date": t, "regime": regime, "equity": equity,
                "exposure_pct": round((equity - self.cash) / equity * 100, 2) if equity > 0 else 0.0,
                "n_positions": len(self.positions),
                "lockout": self.lockout_since is not None,
                "circuit": circuit_locked
            })

        return self._build_results()

    # ── 결과/지표 ──────────────────────────────────────────────────────────
    def _build_results(self) -> dict:
        eq = pd.Series({d: v for d, v in self.equity_curve}).sort_index()
        metrics: Dict[str, float] = {}
        if len(eq) >= 2:
            total_return = eq.iloc[-1] / eq.iloc[0] - 1
            years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
            daily_ret = eq.pct_change().dropna()
            dd = ind.drawdown_from_peak(eq)
            metrics = {
                "total_return_pct": round(total_return * 100, 2),
                "cagr_pct": round(((eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1) * 100, 2),
                "mdd_pct": round(float(dd.min()), 2),
                "sharpe": round(float(daily_ret.mean() / (daily_ret.std() + 1e-12) * (252 ** 0.5)), 2),
                "final_equity": round(float(eq.iloc[-1]), 0),
            }
        wins = [tr for tr in self.trades if tr["pnl"] > 0]
        losses = [tr for tr in self.trades if tr["pnl"] <= 0]
        gross_win = sum(tr["pnl"] for tr in wins)
        gross_loss = abs(sum(tr["pnl"] for tr in losses))
        metrics.update({
            "n_trades": len(self.trades),
            "win_rate_pct": round(len(wins) / len(self.trades) * 100, 1) if self.trades else 0.0,
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else float('inf') if gross_win > 0 else 0.0,
            "avg_holding_days": round(sum(tr["holding_days"] for tr in self.trades) / len(self.trades), 1) if self.trades else 0.0,
            "avg_exposure_pct": round(sum(r["exposure_pct"] for r in self.daily_log) / len(self.daily_log), 1) if self.daily_log else 0.0,
        })
        # KOSPI 벤치마크 (동일 기간 buy & hold)
        if len(eq) >= 2:
            k = self.kospi['Close']
            k_slice = k[(k.index >= eq.index[0]) & (k.index <= eq.index[-1])]
            if len(k_slice) >= 2:
                metrics["benchmark_kospi_pct"] = round((float(k_slice.iloc[-1]) / float(k_slice.iloc[0]) - 1) * 100, 2)
        exit_reasons: Dict[str, int] = {}
        for tr in self.trades:
            exit_reasons[tr["exit_reason"]] = exit_reasons.get(tr["exit_reason"], 0) + 1
        return {
            "metrics": metrics,
            "exit_reasons": exit_reasons,
            "equity_curve": eq,
            "trades": self.trades,
            "daily_log": self.daily_log,
            "config": asdict(self.cfg),
        }


def run_backtest(config: BacktestConfig, price_data: Dict[str, pd.DataFrame],
                 kospi: pd.DataFrame, names: Optional[Dict[str, str]] = None) -> dict:
    return MultiFactorBacktester(config, price_data, kospi, names).run()


# ── 데이터 로더 / CLI ────────────────────────────────────────────────────────
def load_data_fdr(tickers: List[Tuple[str, str]], start: str, end: str,
                  warmup_days: int = 400) -> Tuple[Dict[str, pd.DataFrame], pd.DataFrame, Dict[str, str]]:
    """FinanceDataReader로 유니버스 + KOSPI 데이터를 로드 (피처 워밍업 기간 포함)"""
    import FinanceDataReader as fdr
    fetch_start = (pd.Timestamp(start) - pd.Timedelta(days=warmup_days)).strftime('%Y-%m-%d')
    kospi = fdr.DataReader('KS11', fetch_start, end)
    data, names = {}, {}
    for tic, name in tickers:
        try:
            df = fdr.DataReader(tic, fetch_start, end)
            if df.empty or len(df) < 60:
                logger.warning(f"[{name}({tic})] 데이터 부족으로 유니버스에서 제외 ({len(df)}봉)")
                continue
            data[tic] = df
            names[tic] = name
        except Exception as e:
            logger.warning(f"[{name}({tic})] 데이터 로드 실패로 제외: {e}")
    return data, kospi, names


def print_report(results: dict):
    m = results["metrics"]
    print("=" * 62)
    print("STOCK_MULTIFACTOR 백테스트 결과 (기술적 코어 검증)")
    print("=" * 62)
    print(f"  총수익률      : {m.get('total_return_pct', 0):+.2f}%   (KOSPI: {m.get('benchmark_kospi_pct', 0):+.2f}%)")
    print(f"  CAGR          : {m.get('cagr_pct', 0):+.2f}%")
    print(f"  MDD           : {m.get('mdd_pct', 0):.2f}%")
    print(f"  Sharpe        : {m.get('sharpe', 0):.2f}")
    print(f"  거래 수       : {m.get('n_trades', 0)}건 (승률 {m.get('win_rate_pct', 0):.1f}%, PF {m.get('profit_factor', 0)})")
    print(f"  평균 노출도   : {m.get('avg_exposure_pct', 0):.1f}%")
    print(f"  평균 보유일   : {m.get('avg_holding_days', 0):.1f}일")
    print(f"  최종 자산     : {m.get('final_equity', 0):,.0f}원")
    if results["exit_reasons"]:
        print("  청산 사유 분포:")
        for reason, cnt in sorted(results["exit_reasons"].items(), key=lambda x: -x[1]):
            reason_trades = [tr for tr in results["trades"] if tr["exit_reason"] == reason]
            avg_ret = sum(tr["return_pct"] for tr in reason_trades) / len(reason_trades)
            print(f"    - {reason:<18}: {cnt:>3}건 (평균 {avg_ret:+.2f}%)")
    print("=" * 62)


def main():
    parser = argparse.ArgumentParser(description="STOCK_MULTIFACTOR Backtester")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default=pd.Timestamp.today().strftime('%Y-%m-%d'))
    parser.add_argument("--cash", type=float, default=100_000_000.0)
    parser.add_argument("--tickers", default=None, help="쉼표 구분 티커 (기본: SAMPLE_TICKERS 전체)")
    parser.add_argument("--export", default=None, help="결과 CSV 저장 디렉토리")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

    from stock_trader.core.stock_universe import SAMPLE_TICKERS
    if args.tickers:
        wanted = {t.strip() for t in args.tickers.split(',')}
        universe = [(t, n) for t, n in SAMPLE_TICKERS if t in wanted]
        universe += [(t, t) for t in wanted if t not in {u[0] for u in universe}]
    else:
        universe = SAMPLE_TICKERS

    logger.info(f"데이터 로드 중... (유니버스 {len(universe)}종목, {args.start} ~ {args.end})")
    data, kospi, names = load_data_fdr(universe, args.start, args.end)
    logger.info(f"로드 완료: {len(data)}종목. 백테스트 실행...")

    config = BacktestConfig(start=args.start, end=args.end, initial_cash=args.cash)
    results = run_backtest(config, data, kospi, names)
    print_report(results)

    if args.export:
        import os
        os.makedirs(args.export, exist_ok=True)
        results["equity_curve"].to_csv(os.path.join(args.export, "equity_curve.csv"), header=["equity"])
        pd.DataFrame(results["trades"]).to_csv(os.path.join(args.export, "trades.csv"), index=False)
        pd.DataFrame(results["daily_log"]).to_csv(os.path.join(args.export, "daily_log.csv"), index=False)
        logger.info(f"결과 CSV 저장 완료: {args.export}")


if __name__ == "__main__":
    main()
