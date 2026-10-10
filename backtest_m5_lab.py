#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
M5 Stochastic Extreme Reversal - BACKTESTING LAB v1.0
Author: Quantitative Analyst AI
Purpose: Validate strategy profitability over historical data with spread simulation.
"""

import os
import sys
import time
import logging
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
import yfinance as yf
import matplotlib.pyplot as plt

# ═══════════════════════════════════════════════
# 1. CONFIGURATION (Adjust these parameters)
# ═══════════════════════════════════════════════

SYMBOLS = [
    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X", 
    "USDCAD=X", "NZDUSD=X", "EURCHF=X", "GBPJPY=X"    
]

# Strategy Parameters (Must match live bot exactly)
K_PERIOD = 9
D_PERIOD = 3
OVERBOUGHT_ZONE = 85
OVERSOLD_ZONE = 15
EMA_PERIOD = 200

# Backtest Settings
START_DATE = "2023-10-01" # Start from Oct 2023 for ~1 year of data
END_DATE = None           # None means 'Today'
INTERVAL = "5m"           # 5-minute candles
SPREAD_PIPS_MAJORS = 1.5  # Average spread for EURUSD, GBPUSD, etc.
SPREAD_PIPS_JPY = 2.5     # Average spread for USDJPY, GBPJPY, etc.
LOT_SIZE = 0.01           # Standard lot size for P/L calculation in currency

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
log = logging.getLogger("BACKTEST_LAB")

# ═══════════════════════════════════════════════
# 2. DATA FETCHING & CLEANING ENGINE
# ═══════════════════════════════════════════════

def fetch_data(symbol: str):
    """Fetches clean OHLCV data using yfinance."""
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(start=START_DATE, end=END_DATE, interval=INTERVAL, auto_adjust=False, actions=False)
        
        if df is None or df.empty:
            log.warning(f"No data returned for {symbol}")
            return None
            
        # Handle MultiIndex columns if present
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [str(c[0]) for c in df.columns]
            
        required_cols = ["Open", "High", "Low", "Close"]
        if not all(col in df.columns for col in required_cols):
            log.error(f"Missing columns for {symbol}: {required_cols}")
            return None
            
        df = df[required_cols].copy()
        df.index = pd.to_datetime(df.index, utc=True)
        df = df[~df.index.duplicated()].sort_index().dropna()
        
        # Remove incomplete last candle if it exists (simulate real-time closure)
        now_utc = pd.Timestamp.now(tz="UTC")
        if not df.empty and df.index[-1] + pd.Timedelta(minutes=5) > now_utc:
             df = df.iloc[:-1]
             
        return df
    except Exception as e:
        log.error(f"Error fetching data for {symbol}: {e}")
        return None

# ═══════════════════════════════════════════════
# 3. INDICATOR CALCULATION ENGINE (Vectorized)
# ═══════════════════════════════════════════════

def calculate_indicators(df: pd.DataFrame):
    """Calculates EMA and Stochastic Oscillator efficiently."""
    if df is None or len(df) < EMA_PERIOD + K_PERIOD:
        return None
        
    close_prices = df["Close"]
    
    # EMA 200
    df["EMA_200"] = close_prices.ewm(span=EMA_PERIOD, adjust=False).mean()
    
    # Stochastic %K (Raw)
    lowest_low = df["Low"].rolling(window=K_PERIOD).min()
    highest_high = df["High"].rolling(window=K_PERIOD).max()
    denom = highest_high - lowest_low
    denom.replace(0, np.nan, inplace=True) 
    
    raw_k = 100 * ((close_prices - lowest_low) / denom)
    df["STOCH_K_RAW"] = raw_k.ffill().fillna(50)
    
    # Stochastic %K (Smoothed - Simple MA of Raw K)
    # Note: Many platforms use SMA(K, 3). We replicate that here.
    df["STOCH_K"] = df["STOCH_K_RAW"].rolling(window=D_PERIOD).mean().ffill()
    
    # Stochastic %D (Simple MA of Smoothed K)
    df["STOCH_D"] = df["STOCH_K"].rolling(window=D_PERIOD).mean().ffill()
    
    return df

# ═══════════════════════════════════════════════
# 4. STRATEGY LOGIC SIMULATOR
# ═══════════════════════════════════════════════

def simulate_trades(df: pd.DataFrame, symbol: str):
    """Generates trade signals based on exact live bot logic."""
    trades = []
    
    # Drop rows where indicators are NaN (warm-up period)
    valid_df = df.dropna(subset=["EMA_200", "STOCH_K", "STOCH_D"]).copy()
    
    if len(valid_df) < 2:
        return trades

    # Iterate through candles to find crossovers
    # Using shift(-1) allows us to look at the NEXT candle for exit price
    for i in range(len(valid_df) - 1):
        curr = valid_df.iloc[i]
        next_candle = valid_df.iloc[i+1]
        
        k_curr = float(curr['STOCH_K'])
        d_curr = float(curr['STOCH_D'])
        ema_curr = float(curr['EMA_200'])
        close_curr = float(curr['Close'])
        
        # Get previous values for crossover check
        prev_idx = max(0, i-1)
        prev = valid_df.iloc[prev_idx]
        k_prev = float(prev['STOCH_K'])
        d_prev = float(prev['STOCH_D'])
        
        signal_type = None
        
        # CALL Condition
        if close_curr > ema_curr and k_prev <= d_prev and k_curr > d_curr and min(k_prev, k_curr) < OVERSOLD_ZONE:
            signal_type = "CALL"
            
        # PUT Condition
        elif close_curr < ema_curr and k_prev >= d_prev and k_curr < d_curr and max(k_prev, k_curr) > OVERBOUGHT_ZONE:
            signal_type = "PUT"
            
        if signal_type:
            entry_price = close_curr
            exit_price = float(next_candle['Close']) # Exit at next candle close (5 mins later)
            
            # Calculate Pips
            pip_mult = 100 if "JPY" in symbol.upper() else 10000
            gross_pips = abs(exit_price - entry_price) * pip_mult
            
            # Apply Spread Deduction
            spread_cost = SPREAD_PIPS_JPY if "JPY" in symbol.upper() else SPREAD_PIPS_MAJORS
            net_pips = gross_pips - spread_cost
            
            # Determine Win/Loss
            is_win = False
            if signal_type == "CALL":
                is_win = (exit_price > entry_price) and (net_pips > 0)
            elif signal_type == "PUT":
                is_win = (exit_price < entry_price) and (net_pips > 0)
                
            trades.append({
                "timestamp": curr.name,
                "symbol": symbol,
                "direction": signal_type,
                "entry": entry_price,
                "exit": exit_price,
                "gross_pips": gross_pips,
                "spread": spread_cost,
                "net_pips": net_pips,
                "win": is_win
            })
            
    return trades

# ═══════════════════════════════════════════════
# 5. REPORTING & VISUALIZATION ENGINE
# ═══════════════════════════════════════════════

def generate_report(all_trades):
    if not all_trades:
        print("\n❌ NO TRADES FOUND IN THE SPECIFIED PERIOD.")
        return

    df_trades = pd.DataFrame(all_trades)
    
    total_trades = len(df_trades)
    wins = df_trades[df_trades['win'] == True].shape[0]
    losses = total_trades - wins
    win_rate = (wins / total_trades) * 100 if total_trades > 0 else 0
    
    net_profit_pips = df_trades['net_pips'].sum()
    avg_win = df_trades[df_trades['win'] == True]['net_pips'].mean() if wins > 0 else 0
    avg_loss = df_trades[df_trades['win'] == False]['net_pips'].mean() if losses > 0 else 0
    
    profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else 0
    
    # Per Symbol Analysis
    symbol_stats = {}
    for sym in SYMBOLS:
        sub_df = df_trades[df_trades['symbol'] == sym]
        if not sub_df.empty:
            s_wins = sub_df[sub_df['win'] == True].shape[0]
            s_total = len(sub_df)
            s_wr = (s_wins / s_total) * 100
            s_net = sub_df['net_pips'].sum()
            symbol_stats[sym] = {"trades": s_total, "wr": s_wr, "net_pips": s_net}

    # Print Console Report
    print("\n" + "="*60)
    print("📊 M5 STOCHASTIC EXTREME REVERSAL - BACKTEST RESULTS")
    print("="*60)
    print(f"Period: {START_DATE} to {datetime.now().strftime('%Y-%m-%d')}")
    print(f"Total Trades: {total_trades}")
    print(f"Win Rate: {win_rate:.2f}% ({wins} Wins / {losses} Losses)")
    print(f"Net Profit (Pips): {net_profit_pips:.2f}")
    print(f"Avg Win: {avg_win:.2f} pips | Avg Loss: {abs(avg_loss):.2f} pips")
    print(f"Profit Factor: {profit_factor:.2f}")
    print("-"*60)
    print("PERFORMANCE BY PAIR:")
    for sym, stats in sorted(symbol_stats.items(), key=lambda x: x[1]['net_pips'], reverse=True):
        emoji = "🟢" if stats['net_pips'] > 0 else "🔴"
        print(f"{emoji} {sym}: {stats['trades']} trades | WR: {stats['wr']:.1f}% | Net: {stats['net_pips']:.2f} pips")
    print("="*60)

    # Equity Curve Plot
    df_trades_sorted = df_trades.sort_values('timestamp')
    equity_curve = df_trades_sorted['net_pips'].cumsum()
    
    plt.figure(figsize=(12, 6))
    plt.plot(equity_curve.index, equity_curve.values, label='Cumulative Net Pips', color='blue', linewidth=2)
    plt.title('Equity Curve: M5 Stochastic Strategy (With Spread)', fontsize=14)
    plt.xlabel('Date')
    plt.ylabel('Net Pips')
    plt.grid(True, linestyle='--', alpha=0.7)
    plt.legend(loc='upper left')
    plt.tight_layout()
    
    filename = f"equity_curve_{int(time.time())}.png"
    plt.savefig(filename)
    print(f"\n💾 Chart saved as: {filename}")
    plt.show()

# ═══════════════════════════════════════════════
# 6. MAIN EXECUTION LOOP
# ═══════════════════════════════════════════════

def main():
    log.info("Starting Backtest Engine...")
    all_trades = []
    
    for symbol in SYMBOLS:
        log.info(f"Processing {symbol}...")
        df_raw = fetch_data(symbol)
        if df_raw is None: continue
        
        df_ind = calculate_indicators(df_raw)
        if df_ind is None: continue
        
        trades = simulate_trades(df_ind, symbol)
        all_trades.extend(trades)
        
        # Polite delay to avoid rate limiting from Yahoo Finance
        time.sleep(1) 
        
    generate_report(all_trades)

if __name__ == "__main__":
    main()
