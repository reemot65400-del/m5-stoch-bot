#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FINANCIAL SNIPER SYSTEM - BACKTEST ENGINE v1.3 (Relaxed Confluence Mode)
الإصلاحات الجوهرية لحل مشكلة "صفر إشارات":
1. ★★ تخفيف شروط الدخول (Entry Relaxation):
   - تم تغيير شرط "اللمس الدقيق" للحدود إلى شرط "الاقتراب الشديد" (Proximity Zone).
   - تم استبدال شرط "انقلاب الهيستوجرام الحاد" بشرط "الميل الإيجابي/السلبي الواضح".
2. ★★ الحفاظ على هيكل البيانات الناجح:
   - نستخدم نفس دالة الدمج (Merge AsOf) لأنها أثبتت نجاحها في النسخة السابقة.
"""
import numpy as np
import pandas as pd
import yfinance as yf
import warnings
import time 

warnings.filterwarnings('ignore')

# ==========================================
# CONFIGURATION: THE RELAXED SNIPER PARAMETERS
# ==========================================

SYMBOLS = [
    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X",
    "USDCAD=X", "NZDUSD=X", "EURGBP=X", "EURCHF=X",
    "GBPJPY=X", "AUDJPY=X", "CADJPY=X", "CHFJPY=X",
    "EURAUD=X", "USDCHF=X", "AUDNZD=X", "GBPCAD=X"
]

LOOKBACK_PERIOD = "60d"  # مقيد بسبب بيانات M5 المجانية
TIMEFRAME_MACRO = "1h"   
TIMEFRAME_STRUCTURAL = "15m" 
TIMEFRAME_TRIGGER = "5m"     

BREAK_EVEN_WR = 0.5263 

# --- INDICATOR SETTINGS ---
EMA_PERIOD_MACRO = 200
ATR_PERIOD_STRUCT = 14
ATR_MULTIPLIER = 2.0       
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# ★★ معاملات التخفيف الجديدة (Relaxation Factors) ★★
PROXIMITY_THRESHOLD = 0.0015 # السماح بالدخول إذا كان السعر قريباً من الحد بنسبة 0.15%
MOMENTUM_SLOPE_LOOKBACK = 3  # عدد الشموع للنظر في ميل الهيستوجرام (بدلا من الانقلاب اللحظي)

# --- RISK MANAGEMENT SIMULATION ---
MAX_CONSECUTIVE_LOSSES = 2 

def fetch_and_merge_data(symbol):
    """نفس دالة الجلب الناجحة من v1.2"""
    try:
        ticker = yf.Ticker(symbol)
        
        print(f"   Fetching H1...", end="", flush=True)
        df_h1 = ticker.history(period=LOOKBACK_PERIOD, interval=TIMEFRAME_MACRO)
        time.sleep(0.5) 
        
        print(f" M15...", end="", flush=True)
        df_m15 = ticker.history(period=LOOKBACK_PERIOD, interval=TIMEFRAME_STRUCTURAL)
        time.sleep(0.5)
        
        print(f" M5...")
        df_m5 = ticker.history(period=LOOKBACK_PERIOD, interval=TIMEFRAME_TRIGGER)
        time.sleep(0.5) 
        
        if df_h1.empty or df_m15.empty or df_m5.empty:
            raise ValueError(f"One of the dataframes is empty for {symbol}")
            
        def clean_df(df):
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [col[0] for col in df.columns]
            df.index = pd.to_datetime(df.index, utc=True)
            return df[['Open', 'High', 'Low', 'Close']]

        df_h1 = clean_df(df_h1)
        df_m15 = clean_df(df_m15)
        df_m5 = clean_df(df_m5)
        
        # الدمج الذكي
        merged = pd.merge_asof(
            df_m5.sort_index(), 
            df_h1.sort_index()[['Close']].rename(columns={'Close': 'H1_Close'}), 
            left_index=True, right_index=True, direction='backward'
        )
        
        final_df = pd.merge_asof(
            merged.sort_index(), 
            df_m15.sort_index()[['Close', 'High', 'Low']].rename(columns={
                'Close': 'M15_Close', 
                'High': 'M15_High', 
                'Low': 'M15_Low'
            }), 
            left_index=True, right_index=True, direction='backward'
        )
        
        final_df.dropna(inplace=True)
        
        if len(final_df) < 100:
             raise ValueError(f"Not enough aligned data after merge for {symbol}.")
             
        return final_df
        
    except Exception as e:
        print(f"\n   ❌ ERROR fetching/merging {symbol}: {e}")
        return None

def calculate_indicators_on_merged(df):
    """حساب المؤشرات مع إضافة حقول مساعدة للتخفيف"""
    c_m5 = df['Close']
    
    # 1. Macro Trend
    ema_200 = df['H1_Close'].ewm(span=EMA_PERIOD_MACRO, adjust=False).mean()
    
    # 2. Structural Zone (ATR Bands)
    high_m15 = df['M15_High']
    low_m15 = df['M15_Low']
    close_m15 = df['M15_Close']
    
    tr_m15 = pd.concat([
        high_m15 - low_m15,
        (high_m15 - close_m15.shift(1)).abs(),
        (low_m15 - close_m15.shift(1)).abs()
    ], axis=1).max(axis=1)
    
    atr_m15 = tr_m15.rolling(window=ATR_PERIOD_STRUCT).mean()
    sma_mid_m15 = close_m15.rolling(window=ATR_PERIOD_STRUCT).mean()
    upper_band_m15 = sma_mid_m15 + (atr_m15 * ATR_MULTIPLIER)
    lower_band_m15 = sma_mid_m15 - (atr_m15 * ATR_MULTIPLIER)
    
    # حساب نطاق التقارب (Proximity Range)
    proximity_range_lower = lower_band_m15 * (1 + PROXIMITY_THRESHOLD)
    proximity_range_upper = upper_band_m15 * (1 - PROXIMITY_THRESHOLD)
    
    # 3. Momentum Trigger (MACD Slope instead of Flip)
    exp1_m5 = c_m5.ewm(span=MACD_FAST, adjust=False).mean()
    exp2_m5 = c_m5.ewm(span=MACD_SLOW, adjust=False).mean()
    macd_line_m5 = exp1_m5 - exp2_m5
    signal_line_m5 = macd_line_m5.ewm(span=MACD_SIGNAL, adjust=False).mean()
    histogram_m5 = macd_line_m5 - signal_line_m5
    
    # حساب ميل الهيستوجرام (هل يتحسن الاتجاه؟)
    hist_slope = histogram_m5.diff(MOMENTUM_SLOPE_LOOKBACK)
    
    return df.assign(
        EMA_200=ema_200,
        UPPER_BAND_M15=upper_band_m15,
        LOWER_BAND_M15=lower_band_m15,
        PROX_LOWER=proximity_range_lower, # حد مرخص للشراء
        PROX_UPPER=proximity_range_upper, # حد مرخص للبيع
        HISTOGRAM_M5=histogram_m5,
        HIST_SLOPE=hist_slope,            # ميل الزخم
        NEXT_CLOSE_M5=c_m5.shift(-1) 
    ).dropna(subset=['NEXT_CLOSE_M5'])

def run_sniper_simulation(symbol, df_processed):
    """منطق القنص المخفف"""
    trades = []
    consecutive_losses = 0
    is_sleeping = False
    sleep_until_index = None
    
    n = len(df_processed)
    start_idx = 50 
    
    closes = df_processed['Close'].values
    next_closes = df_processed['NEXT_CLOSE_M5'].values
    emas = df_processed['EMA_200'].values
    lbs = df_processed['LOWER_BAND_M15'].values
    ubs = df_processed['UPPER_BAND_M15'].values
    prox_lbs = df_processed['PROX_LOWER'].values # استخدام الحدود المرخصة
    prox_ubs = df_processed['PROX_UPPER'].values # استخدام الحدود المرخصة
    slopes = df_processed['HIST_SLOPE'].values   # استخدام الميل بدل الانقلاب
    
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
        lb_val = lbs[i]
        ub_val = ubs[i]
        plb_val = prox_lbs[i]
        pub_val = prox_ubs[i]
        slope_val = slopes[i]
        
        signal = None
        
        # ★★ شرط الشراء المخفف (RELAXED CALL) ★★
        # 1. اتجاه عام صاعد (Price > EMA200)
        # 2. السعر داخل منطقة التشبع البيعي المرخصة (Price <= Proximal Lower Band)
        #    ملاحظة: استخدمنا plb_val الذي يسمح بالبقاء قليلاً فوق الحد الفعلي
        # 3. الزخم يتحسن (Positive Slope) وليس بالضرورة انقلاب حاد
        if (curr_close > ema_val and 
            curr_close <= plb_val and 
            slope_val > 0): 
            signal = "CALL"
            
        # ★★ شرط البيع المخفف (RELAXED PUT) ★★
        # 1. اتجاه عام هابط (Price < EMA200)
        # 2. السعر داخل منطقة التشبع الشرائي المرخصة (Price >= Proximal Upper Band)
        # 3. الزخم يضعف (Negative Slope)
        elif (curr_close < ema_val and 
              curr_close >= pub_val and 
              slope_val < 0):
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
                    sleep_duration_candles = 24 
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
    print("INITIATING FINANCIAL SNIPER BACKTEST v1.3 (RELAXED MODE)")
    print(f"Symbols: {len(SYMBOLS)} | Period: {LOOKBACK_PERIOD}")
    print("Logic: Relaxed Confluence (Proximity Zones + Momentum Slopes)")
    print("="*70)
    
    global_trades = []
    success_count = 0
    
    for sym in SYMBOLS:
        print(f"\n>>> Processing {sym}:", end=" ")
        
        merged_df = fetch_and_merge_data(sym)
        
        if merged_df is None:
            print("SKIPPED due to data issues.")
            continue
            
        processed_df = calculate_indicators_on_merged(merged_df)
        
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
    if final_stats['count'] >= 30: 
        if final_stats['wr'] >= 0.60 and final_stats['z_score'] > 1.96:
            verdict = "EXCELLENT 🏆 (Proceed to Live Paper Trading)"
        elif final_stats['wr'] >= BREAK_EVEN_WR and final_stats['z_score'] > 1.0:
            verdict = "VIABLE ⚠️ (Refine Parameters Before Live)"
        else:
            verdict = "FRAGILE ❌ (Do Not Trade Real Money)"
    else:
        verdict = "INSUFFICIENT SAMPLE SIZE ❌ (Even relaxed mode found too few signals)"
            
    print(f"\nVerdict:                      {verdict}")
    print("="*70)

if __name__ == "__main__":
    main()
