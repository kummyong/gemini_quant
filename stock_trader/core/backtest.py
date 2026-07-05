import os
import sys
import argparse
import datetime
import pandas as pd
import numpy as np
from typing import List, Dict, Any, Optional

from stock_trader.config import DB_PATH
from stock_trader.data.db_repository import DbRepository
import stock_trader.core.indicators as ind
import stock_trader.core.signals as sig

def parse_args():
    parser = argparse.ArgumentParser(description="ETF Trend Backtester")
    parser.add_argument("--ticker", type=str, default="133690", help="Ticker to backtest (default: 133690 - TIGER US Nasdaq 100)")
    parser.add_argument("--start", type=str, default="2021-07-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--strategy", type=str, default="all", choices=["all", "a", "b", "c", "d", "e"], help="Strategy to run")
    parser.add_argument("--cash", type=float, default=100000000.0, help="Initial cash")
    parser.add_argument("--fee", type=float, default=0.003, help="Round-trip transaction fee + slippage (default: 0.3%)")
    return parser.parse_args()

def calculate_cagr(final_val: float, initial_val: float, years: float) -> float:
    if years <= 0 or initial_val <= 0:
        return 0.0
    return ((final_val / initial_val) ** (1.0 / years) - 1.0) * 100.0

def calculate_mdd(equity_curve: List[float]) -> float:
    arr = np.array(equity_curve)
    if len(arr) == 0:
        return 0.0
    cum_max = np.maximum.accumulate(arr)
    drawdowns = (arr - cum_max) / cum_max * 100.0
    return float(np.min(drawdowns))

def run_strategy_a(df: pd.DataFrame, initial_cash: float) -> Dict[str, Any]:
    """Strategy (a): Buy & Hold"""
    if df.empty:
        return {}
    
    first_row = df.iloc[0]
    last_row = df.iloc[-1]
    
    # Buy at the first open
    buy_price = float(first_row['Open'])
    shares = int(initial_cash / buy_price)
    cash_left = initial_cash - (shares * buy_price)
    
    # Sell at the last close
    sell_price = float(last_row['Close'])
    final_val = cash_left + (shares * sell_price)
    
    equity_curve = [initial_cash]
    for idx, row in df.iterrows():
        equity_curve.append(cash_left + (shares * float(row['Close'])))
        
    return {
        "final_value": final_val,
        "cagr": calculate_cagr(final_val, initial_cash, len(df)/252.0),
        "mdd": calculate_mdd(equity_curve),
        "trade_count": 1,
        "trade_cost": shares * buy_price * 0.0015, # One way buy fee assumed
        "out_of_market_ratio": 0.0,
        "equity_curve": equity_curve
    }

def run_strategy_b(df: pd.DataFrame, initial_cash: float, fee_rate: float) -> Dict[str, Any]:
    """Strategy (b): Legacy Approach (RSI/BB entry, yield-based trailing stop + 2~5% clamp + "+2% trigger", immediate reentry)"""
    if df.empty or len(df) < 120:
        return {}
        
    closes = df['Close']
    rsi_14 = ind.rsi(closes, 14)
    lower_band, _, upper_band = ind.bollinger(closes, 20, 2.0)
    
    df_temp = df.copy()
    df_temp['MA20'] = ind.sma(closes, 20)
    df_temp['MA60'] = ind.sma(closes, 60)
    df_temp['MA120'] = ind.sma(closes, 120)
    
    cash = initial_cash
    shares = 0
    buy_price = 0.0
    peak_profit_rate = 0.0
    trade_count = 0
    trade_cost = 0.0
    out_of_market_days = 0
    equity_curve = []
    
    # We need to start where indicators are fully calculated
    start_idx = 120
    
    for t in range(start_idx, len(df)):
        current_date = df.index[t]
        current_close = float(df['Close'].iloc[t])
        current_open = float(df['Open'].iloc[t])
        
        # Check exit if holding
        if shares > 0:
            current_profit_rate = (current_close - buy_price) / buy_price * 100.0
            peak_profit_rate = max(peak_profit_rate, current_profit_rate)
            
            # Legacy stop exit condition
            # Hard stop loss at -5%
            is_exit = False
            exit_reason = ""
            if current_profit_rate <= -5.0:
                is_exit = True
                exit_reason = "Hard Stop Loss"
            elif peak_profit_rate >= 2.0:
                # Trailing stop: drop 3% from peak profit, clamped to 2% ~ 5%
                clamp_drop = max(2.0, min(5.0, peak_profit_rate - 3.0))
                if current_profit_rate <= clamp_drop:
                    is_exit = True
                    exit_reason = "Trailing Stop Clamped"
                    
            if is_exit:
                # Sell next day open
                if t + 1 < len(df):
                    next_open = float(df['Open'].iloc[t+1])
                    proceeds = shares * next_open * (1.0 - fee_rate)
                    trade_cost += shares * next_open * fee_rate
                    cash = proceeds
                    shares = 0
                    trade_count += 1
                    
        # Check buy if cash
        if shares == 0:
            out_of_market_days += 1
            # Buy condition
            rsi_val = rsi_14.iloc[t]
            bb_low = lower_band.iloc[t]
            ma20 = df_temp['MA20'].iloc[t]
            ma60 = df_temp['MA60'].iloc[t]
            ma120 = df_temp['MA120'].iloc[t]
            
            is_rsi_match = rsi_val <= 30.0
            is_bb_match = current_close <= bb_low
            is_mean_reversion = is_rsi_match or is_bb_match
            
            # Simple trend following flag
            is_trend = ma20 > ma60 > ma120 and 45.0 <= rsi_val <= 65.0
            
            if is_mean_reversion or is_trend:
                # Buy next day open
                if t + 1 < len(df):
                    next_open = float(df['Open'].iloc[t+1])
                    shares_to_buy = int(cash * (1.0 - fee_rate) / next_open)
                    if shares_to_buy > 0:
                        cost = shares_to_buy * next_open
                        trade_cost += cost * fee_rate
                        cash -= cost
                        shares = shares_to_buy
                        buy_price = next_open
                        peak_profit_rate = 0.0
                        trade_count += 1
                        
        port_val = cash + (shares * current_close)
        equity_curve.append(port_val)
        
    final_val = cash + (shares * float(df['Close'].iloc[-1]))
    return {
        "final_value": final_val,
        "cagr": calculate_cagr(final_val, initial_cash, len(equity_curve)/252.0),
        "mdd": calculate_mdd(equity_curve),
        "trade_count": trade_count,
        "trade_cost": trade_cost,
        "out_of_market_ratio": out_of_market_days / len(equity_curve) if len(equity_curve) > 0 else 0.0,
        "equity_curve": equity_curve
    }

def run_strategy_c(df: pd.DataFrame, initial_cash: float, fee_rate: float, params: sig.StrategyParams) -> Dict[str, Any]:
    """Strategy (c): Chandelier Stop + 3-way Reentry Rules"""
    if df.empty or len(df) < params.trend_sma_period:
        return {}
        
    cash = initial_cash
    shares = 0
    buy_price = 0.0
    peak_close = 0.0
    trade_count = 0
    trade_cost = 0.0
    out_of_market_days = 0
    equity_curve = []
    
    last_stop_record = None
    
    start_idx = params.trend_sma_period
    
    for t in range(start_idx, len(df)):
        current_date = df.index[t]
        current_date_str = current_date.strftime('%Y-%m-%d')
        sub_df = df.iloc[:t+1]
        current_close = float(df['Close'].iloc[t])
        
        # Check exit if holding
        if shares > 0:
            peak_close = max(peak_close, current_close)
            pos_dict = {
                "pur_pric": buy_price,
                "peak_close": peak_close
            }
            exit_sig = sig.check_exit(sub_df, pos_dict, params)
            if exit_sig:
                # Sell next day open
                if t + 1 < len(df):
                    next_open = float(df['Open'].iloc[t+1])
                    proceeds = shares * next_open * (1.0 - fee_rate)
                    trade_cost += shares * next_open * fee_rate
                    cash = proceeds
                    shares = 0
                    trade_count += 1
                    last_stop_record = {
                        "stop_date": df.index[t+1].strftime('%Y-%m-%d'),
                        "stop_price": next_open
                    }
                    
        # Check buy if cash
        if shares == 0:
            out_of_market_days += 1
            
            is_buy_eligible = False
            if last_stop_record:
                is_buy_eligible = sig.can_reenter(sub_df, last_stop_record, params, current_date_str)
            else:
                is_buy_eligible = sig.trend_ok(sub_df, params)
                
            if is_buy_eligible:
                # Buy next day open
                if t + 1 < len(df):
                    next_open = float(df['Open'].iloc[t+1])
                    shares_to_buy = int(cash * (1.0 - fee_rate) / next_open)
                    if shares_to_buy > 0:
                        cost = shares_to_buy * next_open
                        trade_cost += cost * fee_rate
                        cash -= cost
                        shares = shares_to_buy
                        buy_price = next_open
                        peak_close = next_open
                        trade_count += 1
                        
        port_val = cash + (shares * current_close)
        equity_curve.append(port_val)
        
    final_val = cash + (shares * float(df['Close'].iloc[-1]))
    return {
        "final_value": final_val,
        "cagr": calculate_cagr(final_val, initial_cash, len(equity_curve)/252.0),
        "mdd": calculate_mdd(equity_curve),
        "trade_count": trade_count,
        "trade_cost": trade_cost,
        "out_of_market_ratio": out_of_market_days / len(equity_curve) if len(equity_curve) > 0 else 0.0,
        "equity_curve": equity_curve
    }

def run_strategy_d(df: pd.DataFrame, initial_cash: float, fee_rate: float, params: sig.StrategyParams) -> Dict[str, Any]:
    """Strategy (d): SMA200 filter only"""
    if df.empty or len(df) < params.trend_sma_period:
        return {}
        
    cash = initial_cash
    shares = 0
    trade_count = 0
    trade_cost = 0.0
    out_of_market_days = 0
    equity_curve = []
    
    start_idx = params.trend_sma_period
    
    for t in range(start_idx, len(df)):
        sub_df = df.iloc[:t+1]
        current_close = float(df['Close'].iloc[t])
        
        # exit if close <= SMA200
        if shares > 0:
            if not sig.trend_ok(sub_df, params):
                # Sell next day open
                if t + 1 < len(df):
                    next_open = float(df['Open'].iloc[t+1])
                    proceeds = shares * next_open * (1.0 - fee_rate)
                    trade_cost += shares * next_open * fee_rate
                    cash = proceeds
                    shares = 0
                    trade_count += 1
                    
        # buy if close > SMA200
        if shares == 0:
            out_of_market_days += 1
            if sig.trend_ok(sub_df, params):
                # Buy next day open
                if t + 1 < len(df):
                    next_open = float(df['Open'].iloc[t+1])
                    shares_to_buy = int(cash * (1.0 - fee_rate) / next_open)
                    if shares_to_buy > 0:
                        cost = shares_to_buy * next_open
                        trade_cost += cost * fee_rate
                        cash -= cost
                        shares = shares_to_buy
                        trade_count += 1
                        
        port_val = cash + (shares * current_close)
        equity_curve.append(port_val)
        
    final_val = cash + (shares * float(df['Close'].iloc[-1]))
    return {
        "final_value": final_val,
        "cagr": calculate_cagr(final_val, initial_cash, len(equity_curve)/252.0),
        "mdd": calculate_mdd(equity_curve),
        "trade_count": trade_count,
        "trade_cost": trade_cost,
        "out_of_market_ratio": out_of_market_days / len(equity_curve) if len(equity_curve) > 0 else 0.0,
        "equity_curve": equity_curve
    }

def run_strategy_e(df: pd.DataFrame, index_df: pd.DataFrame, initial_cash: float, fee_rate: float, params: sig.StrategyParams) -> Dict[str, Any]:
    """Strategy (e): Chandelier Stop + Reentry + Lockout (Triggered on Index KODEX 200 SMA50 break or KODEX 200 daily return <= -3%)"""
    if df.empty or len(df) < params.trend_sma_period or index_df.empty:
        return {}
        
    cash = initial_cash
    shares = 0
    buy_price = 0.0
    peak_close = 0.0
    trade_count = 0
    trade_cost = 0.0
    out_of_market_days = 0
    equity_curve = []
    
    last_stop_record = None
    lockout_state = {"active": False, "since": None, "reason": None}
    
    start_idx = params.trend_sma_period
    
    # Align dates between index and asset
    for t in range(start_idx, len(df)):
        current_date = df.index[t]
        current_date_str = current_date.strftime('%Y-%m-%d')
        sub_df = df.iloc[:t+1]
        current_close = float(df['Close'].iloc[t])
        
        # Sub index dataframe up to current date
        sub_index = index_df[index_df.index <= current_date]
        
        # Check lockout state updates
        if not sub_index.empty:
            last_idx_row = sub_index.iloc[-1]
            last_idx_close = float(last_idx_row['Close'])
            # Check KODEX 200 daily return <= -3% for lockout trigger
            if len(sub_index) >= 2:
                prev_idx_close = float(sub_index['Close'].iloc[-2])
                idx_ret = (last_idx_close - prev_idx_close) / prev_idx_close * 100.0
                if idx_ret <= -3.0 and not lockout_state["active"]:
                    lockout_state = {"active": True, "since": current_date_str, "reason": f"KODEX 200 급락 ({idx_ret:.2f}%)"}
            
            # Check lockout release
            if lockout_state["active"]:
                lockout_active = sig.market_lockout(sub_index, lockout_state, params)
                if not lockout_active:
                    lockout_state = {"active": False, "since": None, "reason": None}
                    
        # Check exit if holding
        if shares > 0:
            peak_close = max(peak_close, current_close)
            pos_dict = {
                "pur_pric": buy_price,
                "peak_close": peak_close
            }
            exit_sig = sig.check_exit(sub_df, pos_dict, params)
            if exit_sig:
                # Sell next day open
                if t + 1 < len(df):
                    next_open = float(df['Open'].iloc[t+1])
                    proceeds = shares * next_open * (1.0 - fee_rate)
                    trade_cost += shares * next_open * fee_rate
                    cash = proceeds
                    shares = 0
                    trade_count += 1
                    last_stop_record = {
                        "stop_date": df.index[t+1].strftime('%Y-%m-%d'),
                        "stop_price": next_open
                    }
                    
        # Check buy if cash
        if shares == 0:
            out_of_market_days += 1
            
            if not lockout_state["active"]:
                is_buy_eligible = False
                if last_stop_record:
                    is_buy_eligible = sig.can_reenter(sub_df, last_stop_record, params, current_date_str)
                else:
                    is_buy_eligible = sig.trend_ok(sub_df, params)
                    
                if is_buy_eligible:
                    # Buy next day open
                    if t + 1 < len(df):
                        next_open = float(df['Open'].iloc[t+1])
                        shares_to_buy = int(cash * (1.0 - fee_rate) / next_open)
                        if shares_to_buy > 0:
                            cost = shares_to_buy * next_open
                            trade_cost += cost * fee_rate
                            cash -= cost
                            shares = shares_to_buy
                            buy_price = next_open
                            peak_close = next_open
                            trade_count += 1
                            
        port_val = cash + (shares * current_close)
        equity_curve.append(port_val)
        
    final_val = cash + (shares * float(df['Close'].iloc[-1]))
    return {
        "final_value": final_val,
        "cagr": calculate_cagr(final_val, initial_cash, len(equity_curve)/252.0),
        "mdd": calculate_mdd(equity_curve),
        "trade_count": trade_count,
        "trade_cost": trade_cost,
        "out_of_market_ratio": out_of_market_days / len(equity_curve) if len(equity_curve) > 0 else 0.0,
        "equity_curve": equity_curve
    }

def main():
    args = parse_args()
    print(f"[Backtest] Start: ticker {args.ticker} | start {args.start}")
    
    # Load Data from Database
    repo = DbRepository(DB_PATH)
    # Get 5 years of historical data to ensure we have enough data before the start date for indicators
    df_asset = repo.get_recent_ohlcv(args.ticker, limit=1825)
    df_kodex = repo.get_recent_ohlcv("069500", limit=1825)
    
    if df_asset.empty:
        print(f"[Error] No data found in DB for ticker [{args.ticker}]. Run backfill first.")
        return
        
    df_asset.sort_index(inplace=True)
    df_kodex.sort_index(inplace=True)
    
    # Filter by start date for evaluation, but keep leading history for indicators
    start_dt = pd.to_datetime(args.start)
    eval_df = df_asset[df_asset.index >= start_dt]
    if eval_df.empty:
        print(f"[Error] No data found after start date {args.start}.")
        return
        
    params = sig.StrategyParams()
    results = {}
    
    # Run Strategies
    if args.strategy in ("all", "a"):
        results["Buy & Hold"] = run_strategy_a(eval_df, args.cash)
        
    if args.strategy in ("all", "b"):
        # Strategy B needs MA120, so keep 120 days of history before start
        history_df = df_asset[df_asset.index < start_dt].tail(120)
        full_df = pd.concat([history_df, eval_df])
        results["Legacy (Clamp + Immediate Reentry)"] = run_strategy_b(full_df, args.cash, args.fee)
        
    if args.strategy in ("all", "c"):
        # Strategy C needs SMA200
        history_df = df_asset[df_asset.index < start_dt].tail(params.trend_sma_period)
        full_df = pd.concat([history_df, eval_df])
        results["Chandelier + Reentry 3-Rules"] = run_strategy_c(full_df, args.cash, args.fee, params)
        
    if args.strategy in ("all", "d"):
        # Strategy D needs SMA200
        history_df = df_asset[df_asset.index < start_dt].tail(params.trend_sma_period)
        full_df = pd.concat([history_df, eval_df])
        results["SMA200 Filter Only"] = run_strategy_d(full_df, args.cash, args.fee, params)
        
    if args.strategy in ("all", "e"):
        # Strategy E needs SMA200
        history_df = df_asset[df_asset.index < start_dt].tail(params.trend_sma_period)
        full_df = pd.concat([history_df, eval_df])
        results["Chandelier + Lockout"] = run_strategy_e(full_df, df_kodex, args.cash, args.fee, params)
        
    # Print Console Report
    print("\n" + "="*80)
    print(f"[Backtest Report] Ticker: {args.ticker} | Period: {args.start} ~ {df_asset.index[-1].strftime('%Y-%m-%d')}")
    print("="*80)
    print(f"{'Strategy':<38} | {'Final Value':<15} | {'CAGR (%)':<10} | {'MDD (%)':<10} | {'Trades':<8} | {'Cost':<12} | {'Out Ratio':<10}")
    print("-"*120)
    
    rows = []
    for name, res in results.items():
        if not res:
            continue
        print(f"{name:<38} | {res['final_value']:<15,.0f} | {res['cagr']:<10.2f} | {res['mdd']:<10.2f} | {res['trade_count']:<8} | {res['trade_cost']:<12,.0f} | {res['out_of_market_ratio']:<10.2%}")
        rows.append({
            "Strategy": name,
            "Final Value": round(res["final_value"], 0),
            "CAGR (%)": round(res["cagr"], 2),
            "MDD (%)": round(res["mdd"], 2),
            "Trades": res["trade_count"],
            "Trade Cost": round(res["trade_cost"], 0),
            "Out Ratio": round(res["out_of_market_ratio"], 4)
        })
    print("="*80)
    
    # Save Report to CSV
    try:
        report_df = pd.DataFrame(rows)
        csv_filename = f"backtest_report_{args.ticker}.csv"
        report_df.to_csv(csv_filename, index=False, encoding='utf-8-sig')
        print(f"[Success] CSV report saved: {csv_filename}")
    except Exception as save_e:
        print(f"[Warning] CSV save error: {save_e}")

if __name__ == "__main__":
    main()
