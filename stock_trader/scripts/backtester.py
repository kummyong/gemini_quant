import pandas as pd
import numpy as np
import FinanceDataReader as fdr
import datetime
import logging
from tqdm import tqdm
import os

logger = logging.getLogger("Backtester")
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

class VectorBacktester:
    def __init__(self, tickers: dict, start_date: str, end_date: str, initial_capital: float = 10000000.0):
        self.tickers = tickers
        self.start_date = start_date
        self.end_date = end_date
        self.initial_capital = initial_capital
        self.data = {}
        self.vix_data = None
        self.kospi_data = None
        
        self.portfolio = [] # list of dicts: {'ticker': '', 'buy_price': 0, 'quantity': 0, 'highest_price': 0, 'atr': 0}
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

    def prepare_data(self):
        logger.info(f"Downloading Macro (VIX) Data...")
        self.vix_data = fdr.DataReader('FRED:VIXCLS', start=self.start_date, end=self.end_date).dropna()
        self.vix_data['VIX_MA20'] = self.vix_data['VIXCLS'].rolling(20).mean()
        self.vix_data['VIX_Disparity'] = ((self.vix_data['VIXCLS'] - self.vix_data['VIX_MA20']) / self.vix_data['VIX_MA20']) * 100
        
        logger.info(f"Downloading Benchmark (KOSPI)...")
        self.kospi_data = fdr.DataReader('KS11', start=self.start_date, end=self.end_date)
        self.kospi_data['Return_20d'] = self.kospi_data['Close'].pct_change(20)

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
                if ticker not in self.data or date not in self.data[ticker].index:
                    continue
                    
                today_data = self.data[ticker].loc[date]
                if pd.isna(today_data['Close']):
                    continue
                
                pos['highest_price'] = max(pos['highest_price'], today_data['High'])
                stop_price = pos['highest_price'] - (3 * pos['atr'])
                
                # Sell condition: Trailing Stop or Hard Stop (10% loss)
                hard_stop_price = pos['buy_price'] * 0.90
                final_stop_price = max(stop_price, hard_stop_price)
                
                if today_data['Low'] <= final_stop_price:
                    sell_price = min(today_data['Open'], final_stop_price)
                    revenue = sell_price * pos['quantity']
                    self.capital += revenue
                    
                    profit_pct = (sell_price - pos['buy_price']) / pos['buy_price'] * 100
                    self.trade_history.append({
                        'sell_date': date_str,
                        'ticker': ticker,
                        'buy_price': pos['buy_price'],
                        'sell_price': sell_price,
                        'profit_pct': profit_pct
                    })
                    self.portfolio.remove(pos)
                    sold_this_turn.append(ticker)

            # 2. Check Macro Regime (VIX)
            if date not in self.vix_data.index:
                vix_disp = 0
            else:
                vix_disp = self.vix_data.loc[date]['VIX_Disparity']
                
            is_panic = vix_disp > 20.0
            
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
                        
                    # Filtering criteria mimicking MultiFactorScorer core
                    is_aligned = row['MA20'] > row['MA60'] > row['MA120']
                    is_under_ma120 = row['Close'] < row['MA120']
                    
                    # Relaxed VCP check (within 10% of lower band, vol < 70% of MA)
                    is_vcp = (row['Close'] < row['BB_lower'] * 1.10) and (row['Volume_Ratio'] < 0.7)
                    
                    if is_under_ma120:
                        continue 
                        
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
                            
                    if score >= 60.0:
                        candidates.append({
                            'ticker': ticker,
                            'score': score,
                            'close': row['Close'],
                            'atr': row['ATR']
                        })
                
                candidates.sort(key=lambda x: x['score'], reverse=True)
                
                # 4. Execute Buys
                slots_available = self.max_positions - len(self.portfolio)
                if slots_available > 0 and len(candidates) > 0:
                    target_alloc = self.capital / slots_available
                    
                    for cand in candidates[:slots_available]:
                        price = cand['close']
                        if price <= 0: continue
                        
                        quantity = int(target_alloc / price)
                        if quantity > 0:
                            cost = quantity * price
                            self.capital -= cost
                            self.portfolio.append({
                                'ticker': cand['ticker'],
                                'buy_price': price,
                                'quantity': quantity,
                                'highest_price': price,
                                'atr': cand['atr']
                            })
            
            # Calculate Equity
            total_equity = self.capital
            for pos in self.portfolio:
                if date in self.data[pos['ticker']].index:
                    total_equity += self.data[pos['ticker']].loc[date]['Close'] * pos['quantity']
                else:
                    total_equity += pos['highest_price'] * pos['quantity'] # Fallback
            
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
