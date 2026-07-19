import pandas as pd
import numpy as np
import FinanceDataReader as fdr
import datetime
import logging
from tqdm import tqdm
import os

from stock_trader.core.macro_indicators import compute_regime_series

logger = logging.getLogger("Backtester")
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

class VectorBacktester:
    # 거래비용 (편도 수수료 0.015% + 매도 시 증권거래세 0.15%)
    COMMISSION_RATE = 0.00015
    SELL_TAX_RATE = 0.0015

    def __init__(self, tickers: dict, start_date: str, end_date: str, initial_capital: float = 10000000.0, min_hold_days=0, entry_threshold_base: float = 60.0,
                 overshoot_exit_fraction: float = 0.5, overshoot_rsi_thres: float = 70.0,
                 regime_kwargs: dict = None):
        """min_hold_days: 정수(전 국면 공통) 또는 {'BULL': x, 'NEUTRAL': y, 'BEAR': z} 형태의 국면별 딕셔너리.
        overshoot_exit_fraction: 오버슈팅(RSI 과열/BB상단 돌파) 시 부분 익절 비율. 1.0이면 전량 익절(러너 비활성),
        0.5면 절반 익절 후 잔여 물량을 트레일링/하드스탑 전용 '러너'로 전환한다 (라이브 strategy_engine과 동일 로직)."""
        self.tickers = tickers
        self.start_date = start_date
        self.end_date = end_date
        self.initial_capital = initial_capital
        self.min_hold_days = min_hold_days
        self.entry_threshold_base = entry_threshold_base
        self.overshoot_exit_fraction = overshoot_exit_fraction
        self.overshoot_rsi_thres = overshoot_rsi_thres
        # 국면 판정 파라미터 오버라이드 (None이면 core/macro_indicators의 기본값 = 라이브와 동일)
        self.regime_kwargs = regime_kwargs or {}
        self.data = {}
        self.vix_data = None
        self.kospi_data = None

        self.portfolio = [] # list of dicts: {'ticker': '', 'buy_price': 0, 'quantity': 0, 'highest_price': 0, 'atr_pct': 0}
        self.capital = initial_capital
        self.equity_curve = []
        self.trade_history = []
        self.max_positions = 5

    def _calculate_indicators(self, df):
        df['MA20'] = df['Close'].rolling(20).mean()
        df['MA60'] = df['Close'].rolling(60).mean()
        df['MA120'] = df['Close'].rolling(120).mean()
        
        # RSI
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))
        
        # Bollinger Bands
        df['BB_std'] = df['Close'].rolling(20).std()
        df['BB_upper'] = df['MA20'] + (df['BB_std'] * 2)
        df['BB_lower'] = df['MA20'] - (df['BB_std'] * 2)
        
        # ATR
        high_low = df['High'] - df['Low']
        high_close = np.abs(df['High'] - df['Close'].shift())
        low_close = np.abs(df['Low'] - df['Close'].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        df['ATR'] = true_range.rolling(14).mean()
        
        # VCP / Volume contraction
        df['Vol_MA20'] = df['Volume'].rolling(20).mean()
        df['Volume_Ratio'] = df['Volume'] / df['Vol_MA20']
        
        return df

    def _get_min_hold_days(self, regime_at_buy: str) -> float:
        if isinstance(self.min_hold_days, dict):
            return self.min_hold_days.get(regime_at_buy, self.min_hold_days.get('DEFAULT', 0))
        return self.min_hold_days

    def prepare_data(self):
        logger.info(f"Downloading Macro (VIX) Data...")
        self.vix_data = fdr.DataReader('FRED:VIXCLS', start=self.start_date, end=self.end_date).dropna()
        self.vix_data['VIX_MA20'] = self.vix_data['VIXCLS'].rolling(20).mean()
        self.vix_data['VIX_Disparity'] = ((self.vix_data['VIXCLS'] - self.vix_data['VIX_MA20']) / self.vix_data['VIX_MA20']) * 100
        
        logger.info(f"Downloading Benchmark (KOSPI) & Calculating Market Regime...")
        self.kospi_data = fdr.DataReader('KS11', start=self.start_date, end=self.end_date)
        self.kospi_data['Return_20d'] = self.kospi_data['Close'].pct_change(20)
        # Determine Market Regime — 라이브(fetch_market_regime)와 동일한 공유 함수 사용
        self.kospi_data['Regime'] = compute_regime_series(self.kospi_data['Close'], **self.regime_kwargs)

        for ticker, name in self.tickers.items():
            logger.info(f"Downloading {name} ({ticker})...")
            df = fdr.DataReader(ticker, start=self.start_date, end=self.end_date)
            if not df.empty:
                df = self._calculate_indicators(df)
                df['Return_20d'] = df['Close'].pct_change(20)
                self.data[ticker] = df

    def run(self):
        if not self.data:
            self.prepare_data()
            
        dates = self.kospi_data.index
        
        for date in tqdm(dates, desc="Simulating Trading Days"):
            date_str = date.strftime("%Y-%m-%d")
            
            # 1. Update Portfolio (Trailing Stops)
            current_holdings = [p['ticker'] for p in self.portfolio]
            sold_this_turn = []
            
            for pos in list(self.portfolio):
                ticker = pos['ticker']
                if ticker not in self.data:
                    continue
                tdf = self.data[ticker]
                if date not in tdf.index:
                    # 데이터가 종료된 종목(상장폐지 등)은 마지막 종가로 강제 청산한다.
                    # (기존에는 highest_price로 평가액이 동결되어 상폐 종목이 역대 최고가로
                    # 영구 평가되는 낙관 편향이 있었음 — Top50 등 상폐 포함 유니버스에서 치명적)
                    if len(tdf.index) > 0 and date > tdf.index[-1]:
                        last_close = tdf['Close'].iloc[-1]
                        revenue = last_close * pos['quantity'] * (1.0 - self.COMMISSION_RATE - self.SELL_TAX_RATE)
                        self.capital += revenue
                        profit_pct = (last_close - pos['buy_price']) / pos['buy_price'] * 100
                        self.trade_history.append({
                            'buy_date': pos.get('buy_date', ''),
                            'sell_date': date_str,
                            'ticker': ticker,
                            'regime_at_buy': pos.get('regime_at_buy', ''),
                            'buy_price': pos['buy_price'],
                            'sell_price': last_close,
                            'profit_pct': profit_pct,
                            'exit_reason': 'delisted'
                        })
                        self.portfolio.remove(pos)
                        sold_this_turn.append(ticker)
                    continue

                today_data = tdf.loc[date]
                if pd.isna(today_data['Close']):
                    continue

                # Market Regime based Trailing Stop
                regime = "NEUTRAL"
                if date in self.kospi_data.index:
                    regime = self.kospi_data.loc[date]['Regime']

                if regime == "BULL":
                    mult, max_drop = 5.0, 25.0
                elif regime == "BEAR":
                    mult, max_drop = 1.0, 5.0
                else:
                    mult, max_drop = 2.5, 10.0

                trailing_pct = max(3.0, min(max_drop, mult * pos['atr_pct']))
                
                # 선견 편향 방지: 스탑 라인은 '전일까지의 고점' 기준으로 먼저 판정하고,
                # 당일 고가 반영은 판정 후에 한다 (당일 고가로 라인을 올려놓고 같은 봉에서
                # 체결시키면 고가 근처 매도라는 불가능한 체결이 된다).
                stop_price = pos['highest_price'] * (1.0 - trailing_pct / 100.0)
                dynamic_hard_pct = max(3.0, min(6.0, 1.5 * pos['atr_pct']))
                hard_stop_price = pos['buy_price'] * (1.0 - dynamic_hard_pct / 100.0)
                final_stop_price = max(stop_price, hard_stop_price)

                # 최소 보유 기간 중에는 트레일링 스탑 라인 자체를 무효화한다 (체결가에도 반영해야
                # 트레일링 라인의 더 유리한 가격으로 체결되는 낙관 편향이 생기지 않는다).
                in_grace = False
                mh = self._get_min_hold_days(pos.get('regime_at_buy', 'NEUTRAL'))
                if mh > 0:
                    buy_dt = pd.Timestamp(pos.get('buy_date', date_str))
                    days_held = (date - buy_dt).days
                    in_grace = days_held < mh

                effective_stop_price = hard_stop_price if in_grace else final_stop_price

                if today_data['Low'] <= effective_stop_price:
                    # 갭하락 시 시가 체결, 아니면 스탑 라인 체결
                    sell_price = min(today_data['Open'], effective_stop_price)
                    revenue = sell_price * pos['quantity']
                    revenue *= (1.0 - self.COMMISSION_RATE - self.SELL_TAX_RATE)
                    self.capital += revenue

                    profit_pct = (sell_price - pos['buy_price']) / pos['buy_price'] * 100
                    self.trade_history.append({
                        'buy_date': pos.get('buy_date', ''),
                        'sell_date': date_str,
                        'ticker': ticker,
                        'regime_at_buy': pos.get('regime_at_buy', ''),
                        'buy_price': pos['buy_price'],
                        'sell_price': sell_price,
                        'profit_pct': profit_pct,
                        'exit_reason': 'hard_stop' if effective_stop_price <= hard_stop_price else 'trailing_stop'
                    })
                    self.portfolio.remove(pos)
                    sold_this_turn.append(ticker)
                else:
                    pos['highest_price'] = max(pos['highest_price'], today_data['High'])

                    # 오버슈팅(RSI 과열/BB상단 돌파) 부분 익절 + 러너 전환 (라이브 strategy_engine
                    # generate_management_signals의 오버슈팅 분기와 동일 로직: BULL 국면 제외,
                    # 러너 전환된 포지션은 이후 트레일링/하드스탑으로만 관리).
                    rsi_val = today_data.get('RSI', float('nan'))
                    bb_upper = today_data.get('BB_upper', float('nan'))
                    if (regime != "BULL" and not pos.get('is_runner', False)
                            and not pd.isna(rsi_val) and not pd.isna(bb_upper)
                            and (rsi_val >= self.overshoot_rsi_thres or today_data['Close'] >= bb_upper)):
                        fraction = self.overshoot_exit_fraction
                        sell_qty = pos['quantity'] if fraction >= 1.0 else int(pos['quantity'] * fraction)
                        if 0 < sell_qty <= pos['quantity']:
                            exit_price = today_data['Close']
                            revenue = exit_price * sell_qty * (1.0 - self.COMMISSION_RATE - self.SELL_TAX_RATE)
                            self.capital += revenue

                            profit_pct = (exit_price - pos['buy_price']) / pos['buy_price'] * 100
                            self.trade_history.append({
                                'buy_date': pos.get('buy_date', ''),
                                'sell_date': date_str,
                                'ticker': ticker,
                                'regime_at_buy': pos.get('regime_at_buy', ''),
                                'buy_price': pos['buy_price'],
                                'sell_price': exit_price,
                                'profit_pct': profit_pct,
                                'exit_reason': 'overshoot_runner' if sell_qty < pos['quantity'] else 'overshoot_full'
                            })

                            if sell_qty >= pos['quantity']:
                                self.portfolio.remove(pos)
                                sold_this_turn.append(ticker)
                            else:
                                pos['quantity'] -= sell_qty
                                pos['is_runner'] = True

            # 2. Check Macro Regime (VIX)
            if date not in self.vix_data.index:
                vix_disp = 0
                vix_val = 0
            else:
                vix_disp = self.vix_data.loc[date]['VIX_Disparity']
                vix_val = self.vix_data.loc[date]['VIXCLS']
                
            is_panic = (vix_disp > 15.0) or (vix_val > 30.0)
            
            # 3. Screen for new candidates
            candidates = []
            if not is_panic and len(self.portfolio) < self.max_positions:
                for ticker, df in self.data.items():
                    if ticker in current_holdings or ticker in sold_this_turn:
                        continue
                    if date not in df.index:
                        continue
                        
                    row = df.loc[date]
                    if pd.isna(row['MA120']) or pd.isna(row['RSI']):
                        continue
                        
                    # Corporate action anomaly check (±45% jump indicates split/merger anomaly)
                    prev_date_idx = df.index.get_loc(date) - 1
                    if prev_date_idx >= 0:
                        prev_close = df['Close'].iloc[prev_date_idx]
                        if prev_close > 0 and abs(row['Close'] - prev_close) / prev_close > 0.45:
                            continue
                        
                    # Filtering criteria mimicking MultiFactorScorer core
                    is_aligned = row['MA20'] > row['MA60'] > row['MA120']
                    is_under_ma120 = row['Close'] < row['MA120']
                    
                    # 스코어 기반 심사 (Gating 조건 제거)
                    # 역배열이라도 과매도, VCP, 모멘텀 등이 좋으면 점수가 올라감
                    is_vcp = (row['Close'] < row['BB_lower'] * 1.10) and (row['Volume_Ratio'] < 0.7)
                        
                    score = 50.0
                    if is_aligned: score += 15.0
                    if is_vcp: score += 10.0
                    if row['RSI'] < 40: score += 5.0
                    
                    if date in self.kospi_data.index:
                        kospi_ret = self.kospi_data.loc[date]['Return_20d']
                        if not pd.isna(kospi_ret) and not pd.isna(row['Return_20d']):
                            rel_mom = (row['Return_20d'] - kospi_ret) * 100
                            if rel_mom > 0:
                                score += min(10.0, rel_mom * 0.1)
                                
                    regime_for_entry = "NEUTRAL"
                    if date in self.kospi_data.index:
                        regime_for_entry = self.kospi_data.loc[date]['Regime']
                        
                    entry_threshold = 70.0 if regime_for_entry == "BEAR" else self.entry_threshold_base
                            
                    if score >= entry_threshold:
                        atr_pct = (row['ATR'] / row['Close'] * 100.0) if (not pd.isna(row['ATR']) and row['Close'] > 0) else 3.0
                        candidates.append({
                            'ticker': ticker,
                            'score': score,
                            'close': row['Close'],
                            'atr_pct': atr_pct
                        })
                
                candidates.sort(key=lambda x: x['score'], reverse=True)
                
                # 4. Execute Buys
                slots_available = self.max_positions - len(self.portfolio)
                if slots_available > 0 and len(candidates) > 0:
                    target_alloc = self.capital / slots_available
                    
                    regime = "NEUTRAL"
                    if date in self.kospi_data.index:
                        regime = self.kospi_data.loc[date]['Regime']
                    
                    # Size factor application based on regime
                    size_factor = 1.0
                    if regime == "BULL":
                        size_factor = 1.0 # 1.5배 오버웨이트 제거 (과접합 방지)
                    elif regime == "BEAR":
                        size_factor = 0.2
                        
                    for cand in candidates[:slots_available]:
                        price = cand['close']
                        if price <= 0: continue
                        
                        alloc = target_alloc * size_factor
                        # Ensure we don't exceed remaining capital
                        alloc = min(alloc, self.capital)
                        
                        quantity = int(alloc / price)
                        if quantity > 0:
                            cost = quantity * price * (1.0 + self.COMMISSION_RATE)
                            if cost > self.capital:
                                quantity = int(self.capital / (price * (1.0 + self.COMMISSION_RATE)))
                                cost = quantity * price * (1.0 + self.COMMISSION_RATE)
                            if quantity <= 0:
                                continue
                            self.capital -= cost
                            self.portfolio.append({
                                'ticker': cand['ticker'],
                                'buy_date': date_str,
                                'regime_at_buy': regime,
                                'buy_price': price,
                                'quantity': quantity,
                                'highest_price': price,
                                'atr_pct': cand['atr_pct'],
                                'is_runner': False
                            })
            
            # Calculate Equity
            total_equity = self.capital
            for pos in self.portfolio:
                tdf = self.data[pos['ticker']]
                if date in tdf.index:
                    total_equity += tdf.loc[date]['Close'] * pos['quantity']
                else:
                    # 일시 거래정지 등으로 당일 데이터가 없으면 직전 종가로 평가한다
                    # (구 fallback인 highest_price는 역대 최고가라 낙관 편향)
                    px = tdf['Close'].loc[:date]
                    total_equity += (px.iloc[-1] if len(px) else pos['buy_price']) * pos['quantity']
            
            self.equity_curve.append({
                'Date': date_str,
                'Equity': total_equity
            })

    def get_summary(self):
        equity_df = pd.DataFrame(self.equity_curve).set_index('Date')
        if equity_df.empty:
            return {}
            
        equity_df['Drawdown'] = (equity_df['Equity'] / equity_df['Equity'].cummax()) - 1.0
        mdd = equity_df['Drawdown'].min() * 100
        
        final_equity = equity_df['Equity'].iloc[-1]
        total_return = ((final_equity / self.initial_capital) - 1.0) * 100
        
        win_trades = [t for t in self.trade_history if t['profit_pct'] > 0]
        win_rate = (len(win_trades) / len(self.trade_history) * 100) if self.trade_history else 0
        
        # Benchmark return
        bench_return = 0.0
        if not self.kospi_data.empty:
            bench_start = self.kospi_data.iloc[0]['Close']
            bench_end = self.kospi_data.iloc[-1]['Close']
            bench_return = ((bench_end / bench_start) - 1.0) * 100
        
        return {
            'Initial Capital': f"{self.initial_capital:,.0f} 원",
            'Final Equity': f"{final_equity:,.0f} 원",
            'Strategy Return (%)': f"{total_return:.2f}%",
            'Benchmark (KOSPI) Return (%)': f"{bench_return:.2f}%",
            'MDD (%)': f"{mdd:.2f}%",
            'Total Trades': len(self.trade_history),
            'Win Rate (%)': f"{win_rate:.2f}%"
        }
