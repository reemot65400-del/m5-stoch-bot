#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FINANCIAL SNIPER SYSTEM - BACKTEST ENGINE v2.0 (Volume Confluence Mode)
الفلسفة الجديدة: الجودة القصوى على حساب الكمية.
الاستراتيجية: H1 Trend Filter + M15 Pullback Zone + Volume Spike Confirmation.
القاعدة الذهبية: لا إشارة إلا إذا كان حجم التداول الحالي أعلى بـ 2 ضعف من المتوسط.
"""
import numpy as np
import pandas as pd
import yfinance as yf
import warnings
import time 

warnings.filterwarnings('ignore')

# ==========================================
# CONFIGURATION: THE VOLUME SNIPER PARAMETERS
# ==========================================

SYMBOLS = [
    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X",
    "USDCAD=X", "NZDUSD=X", "EURGBP=X", "GBPJPY=X", 
    "AUDJPY=X", "CADJPY=X", "CHFJPY=X", "EURAUD=X", 
    "USDCHF=X", "AUDNZD=X", "GBPCAD=X", "NZDJPY=X" # Added NZDJPY for volatility
]

LOOKBACK_PERIOD = "60d"  
TIMEFRAME_MACRO = "1h"   
TIMEFRAME_TRIGGER = "15m" # Changed from M5 to M15 for stability and less noise

BREAK_EVEN_WR = 0.5263 

# --- INDICATOR SETTINGS ---
EMA_PERIOD_MACRO = 50      # Faster EMA for H1 trend detection
VOL_MA_PERIOD = 20         # Period for Volume Moving Average
VOL_SPIKE_MULTIPLIER = 2.0 # Volume must be > 2x average to trigger signal
PULLBACK_LEVEL = 0.5       # Price must retrace 50% of the recent impulse leg

def fetch_and_merge_data(symbol):
    """
    يجلب بيانات H1 و M15 ويدمجها.
    """
    try:
        ticker = yf.Ticker(symbol)
        
        print(f"   Fetching H1...", end="", flush=True)
        df_h1 = ticker.history(period=LOOKBACK_PERIOD, interval=TIMEFRAME_MACRO)
        time.sleep(0.5) 
        
        print(f" M15...")
        df_m15 = ticker.history(period=LOOKBACK_PERIOD, interval=TIMEFRAME_TRIGGER)
        time.sleep(0.5) 
        
        if df_h1.empty or df_m15.empty:
            raise ValueError(f"Data empty for {symbol}")
            
        def clean_df(df):
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [col[0] for col in df.columns]
            df.index = pd.to_datetime(df.index, utc=True)
            return df[['Open', 'High', 'Low', 'Close', 'Volume']]

        df_h1 = clean_df(df_h1)
        df_m15 = clean_df(df_m15)
        
        # Merge H1 Close into M15 dataframe
        merged = pd.merge_asof(
            df_m15.sort_index(), 
            df_h1.sort_index()[['Close']].rename(columns={'Close': 'H1_Close'}), 
            left_index=True, right_index=True, direction='backward'
        )
        
        merged.dropna(inplace=True)
        
        if len(merged) < 100:
             raise ValueError(f"Not enough data for {symbol}.")
             
        return merged
        
    except Exception as e:
        print(f"\n   ❌ ERROR fetching/merging {symbol}: {e}")
        return None

def calculate_volume_confluence(df):
    """
    يحسب مؤشرات الاستراتيجية الجديدة.
    """
    c_m15 = df['Close']
    vol_m15 = df['Volume']
    
    # 1. Macro Trend (H1 EMA50)
    ema_50_h1 = df['H1_Close'].ewm(span=EMA_PERIOD_MACRO, adjust=False).mean()
    
    # 2. Volume Analysis (M15)
    avg_vol = vol_m15.rolling(window=VOL_MA_PERIOD).mean()
    is_volume_spike = vol_m15 > (avg_vol * VOL_SPIKE_MULTIPLIER)
    
    # 3. Impulse & Pullback Logic (M15)
    # Find local highs/lows over last 10 candles to define an "Impulse Leg"
    lookback_impulse = 10
    high_impulse = c_m15.rolling(window=lookback_impulse).max()
    low_impulse = c_m15.rolling(window=lookback_impulse).min()
    
    # Calculate retracement level from the most recent extreme
    # For CALL: We expect price to drop from High_Impulse towards Mid-point
    # For PUT: We expect price to rise from Low_Impulse towards Mid-point
    
    mid_point_high = high_impulse - ((high_impulse - low_impulse) * PULLBACK_LEVEL)
    mid_point_low = low_impulse + ((high_impulse - low_impulse) * PULLBACK_LEVEL)
    
    # Check if current price is near the pullback zone
    is_in_pullback_zone_call = (c_m15 <= mid_point_high) & (c_m15 >= low_impulse)
    is_in_pullback_zone_put = (c_m15 >= mid_point_low) & (c_m15 <= high_impulse)
    
    return df.assign(
        EMA_50_H1=ema_50_h1,
        IS_VOL_SPIKE=is_volume_spike,
        IN_PULLBACK_CALL=is_in_pullback_zone_call,
        IN_PULLBACK_PUT=is_in_pullback_zone_put,
        NEXT_CLOSE_M15=c_m15.shift(-1)
    ).dropna(subset=['NEXT_CLOSE_M15'])

def run_sniper_simulation(symbol, df_processed):
    """
    منطق القنص الصارم القائم على الحجم.
    """
    trades = []
    consecutive_losses = 0
    is_sleeping = False
    sleep_until_index = None
    
    n = len(df_processed)
    start_idx = 50 
    
    closes = df_processed['Close'].values
    next_closes = df_processed['NEXT_CLOSE_M15'].values
    emas = df_processed['EMA_50_H1'].values
    spikes = df_processed['IS_VOL_SPIKE'].values
    pb_calls = df_processed['IN_PULLBACK_CALL'].values
    pb_puts = df_processed['IN_PULLBACK_PUT'].values
    
    for i in range(start_idx, n):
        if is_sleeping:
            if sleep_until_index and i < sleep_until_index:
                continue 
            else:
                is_sleeping = False 
                consecutive_losses = 0 
        
        curr_close = closes[i]
        next_close = next_closes[i]
        ema_val = emas[i]
        has_spike = spikes[i]
        in_pb_call = pb_calls[i]
        in_pb_put = pb_puts[i]
        
        signal = None
        
        # ★★ شرط الشراء (CALL) الصارم ★★
        # 1. الاتجاه العام صاعد (Price > EMA50 on H1)
        # 2. السعر في منطقة تصحيح هابط (Pullback) على M15
        # 3. انفجار حجمي مفاجئ يؤكد انتهاء التصحيح وبدء الموجة الدافعة
        if (curr_close > ema_val and 
            in_pb_call and 
            has_spike):
            signal = "CALL"
            
        # ★★ شرط البيع (PUT) الصارم ★★
        # 1. الاتجاه العام هابط (Price < EMA50 on H1)
        # 2. السعر في منطقة تصحيح صاعد (Pullback) على M15
        # 3. انفجار حجمي مفاجئ يؤكد انتهاء التصحيح وبدء الموجة الهابطة
        elif (curr_close < ema_val and 
              in_pb_put and 
              has_spike):
            signal = "PUT"
            
        if signal:
            price_diff = next_close - curr_close
            spread_buffer = 0.0001 if "JPY" not in symbol else 0.01 
            
            win = False
            if signal == "CALL":
                win = price_diff > spread_buffer
            elif signal == "PUT":
                win = price_diff < -spread_buffer
                
            trades.append(win)
            
            if not win:
                consecutive_losses += 1
                if consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
                    is_sleeping = True
                    sleep_duration_candles = 4 # Sleep for 1 hour (4 x 15min candles)
                    sleep_until_index = i + sleep_duration_candles
            else:
                consecutive_losses = 0 
                
    return trades

def analyze_results(all_trades):
    total = len(all_trades)
    if total == 0:
        return {'count': 0, 'wr': 0, 'z_score': 0}
        
    wins = sum(all_trades)
    wr = wins / total
    
    p_null = BREAK_EVEN_WR
    std_dev = np.sqrt(total * p_null * (1 - p_null))
    z_score = (wins - (total * p_null)) / std_dev if std_dev > 0 else 0
    
    return {'count': total, 'wr': wr, 'z_score': z_score}

def main():
    print("="*70)
    print("INITIATING FINANCIAL SNIPER BACKTEST v2.0 (VOLUME CONFLUENCE)")
    print(f"Symbols: {len(SYMBOLS)} | Period: {LOOKBACK_PERIOD}")
    print("Logic: H1 Trend + M15 Pullback + Volume Spike (>2x Avg)")
    print("="*70)
    
    global_trades = []
    success_count = 0
    
    for sym in SYMBOLS:
        print(f"\n>>> Processing {sym}:", end=" ")
        
        merged_df = fetch_and_merge_data(sym)
        
        if merged_df is None:
            print("SKIPPED due to data issues.")
            continue
            
        processed_df = calculate_volume_confluence(merged_df)
        
        if processed_df.empty:
            print("SKIPPED: No valid indicators calculated.")
            continue
            
        trades = run_sniper_simulation(sym, processed_df)
        
        stats = analyze_results(trades)
        print(f"DONE. Signals: {stats['count']}, WR: {stats['wr']*100:.2f}%")
        
        global_trades.extend(trades)
        success_count += 1
        
    final_stats = analyze_results(global_trades)
    
    print("\n" + "-"*70)
    print("FINAL COUNCIL VERDICT REPORT")
    print("-"*70)
    print(f"Symbols Successfully Processed: {success_count}/{len(SYMBOLS)}")
    print(f"Total Signals Generated:        {final_stats['count']}")
    print(f"Overall Win Rate:               {final_stats['wr']*100:.2f}%")
    print(f"Z-Score (Significance):         {final_stats['z_score']:.2f}")
    
    verdict = "FAILING"
    # Criteria for Success in this strict mode:
    # 1. At least 20 signals to have statistical meaning.
    # 2. Win rate > 55% (since we are sniping rare setups).
    # 3. Z-score > 1.0 (positive correlation with profit).
    
    if final_stats['count'] >= 20: 
        if final_stats['wr'] >= 0.55 and final_stats['z_score'] > 1.0:
            verdict = "EXCELLENT 🏆 (Proceed to Live Paper Trading)"
        elif final_stats['wr'] >= BREAK_EVEN_WR and final_stats['z_score'] > 0.5:
            verdict = "VIABLE ⚠️ (Refine Parameters Before Live)"
        else:
            verdict = "FRAGILE ❌ (Do Not Trade Real Money)"
    else:
        verdict = "INSUFFICIENT SAMPLE SIZE ❌ (Too few signals generated)"
            
    print(f"\nVerdict:                      {verdict}")
    print("="*70)

if __name__ == "__main__":
    main()
