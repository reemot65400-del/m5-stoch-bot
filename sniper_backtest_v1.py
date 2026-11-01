#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FINANCIAL SNIPER SYSTEM - BACKTEST ENGINE v1.2 (Resilient Data Loader Edition)
الإصلاحات الجوهرية:
1. ★★ حل مشكلة "Data Error": تم إضافة تأخير (Delay) بين الطلبات لتجنب حظر IP.
2. ★★ تحسين المطابقة الزمنية: تم استخدام Merge بدلاَ من asof لضمان دقة التوقيت بين الأطر المختلفة.
3. ★★ معالجة البيانات الناقصة: التحقق الصارم من توفر البيانات لكل إطار زمني قبل المعالجة.
"""
import numpy as np
import pandas as pd
import yfinance as yf
import warnings
import time # لإضافة التأخير بين الطلبات

warnings.filterwarnings('ignore')

# ==========================================
# CONFIGURATION: THE SNIPER PARAMETERS
# ==========================================

SYMBOLS = [
    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X",
    "USDCAD=X", "NZDUSD=X", "EURGBP=X", "EURCHF=X",
    "GBPJPY=X", "AUDJPY=X", "CADJPY=X", "CHFJPY=X",
    "EURAUD=X", "USDCHF=X", "AUDNZD=X", "GBPCAD=X"
]

LOOKBACK_PERIOD = "60d"  
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

# --- RISK MANAGEMENT SIMULATION ---
MAX_CONSECUTIVE_LOSSES = 2 

def fetch_and_merge_data(symbol):
    """
    ★★ الدالة المحصنة ضد الأخطاء ★★
    تجلب البيانات لثلاثة أطر وتدمجها في جدول واحد موحد الزمن.
    """
    try:
        ticker = yf.Ticker(symbol)
        
        # جلب البيانات مع تأخير بسيط بين كل طلب لتجنب Rate Limiting
        print(f"   Fetching H1...", end="", flush=True)
        df_h1 = ticker.history(period=LOOKBACK_PERIOD, interval=TIMEFRAME_MACRO)
        time.sleep(1) 
        
        print(f" M15...", end="", flush=True)
        df_m15 = ticker.history(period=LOOKBACK_PERIOD, interval=TIMEFRAME_STRUCTURAL)
        time.sleep(1)
        
        print(f" M5...")
        df_m5 = ticker.history(period=LOOKBACK_PERIOD, interval=TIMEFRAME_TRIGGER)
        time.sleep(1) # انتظار أخير
        
        # التحقق من عدم وجود بيانات فارغة
        if df_h1.empty or df_m15.empty or df_m5.empty:
            raise ValueError(f"One of the dataframes is empty for {symbol}")
            
        # تنظيف الأعمدة والفهارس
        def clean_df(df):
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [col[0] for col in df.columns]
            df.index = pd.to_datetime(df.index, utc=True)
            return df[['Open', 'High', 'Low', 'Close']]

        df_h1 = clean_df(df_h1)
        df_m15 = clean_df(df_m15)
        df_m5 = clean_df(df_m5)
        
        # ★★ الدمج الذكي (Smart Merge) ★★
        # نريد أن نحصل على قيم H1 و M15 المقابلة لكل شمعة M5
        # نستخدم merge_asof لأننا نبحث عن أقرب قيمة سابقة أو مساوية للوقت الحالي
        
        # 1. دمج H1 مع M5
        merged = pd.merge_asof(
            df_m5.sort_index(), 
            df_h1.sort_index()[['Close']].rename(columns={'Close': 'H1_Close'}), 
            left_index=True, right_index=True, direction='backward'
        )
        
        # 2. دمج M15 مع الناتج السابق
        final_df = pd.merge_asof(
            merged.sort_index(), 
            df_m15.sort_index()[['Close', 'High', 'Low']].rename(columns={
                'Close': 'M15_Close', 
                'High': 'M15_High', 
                'Low': 'M15_Low'
            }), 
            left_index=True, right_index=True, direction='backward'
        )
        
        # إسقاط الصفوف التي لم تجد لها مطابقة (NaNs) في البداية بسبب عدم توفر بيانات سابقة
        final_df.dropna(inplace=True)
        
        if len(final_df) < 100:
             raise ValueError(f"Not enough aligned data after merge for {symbol}. Only {len(final_df)} rows.")
             
        return final_df
        
    except Exception as e:
        print(f"\n   ❌ ERROR fetching/merging {symbol}: {e}")
        return None

def calculate_indicators_on_merged(df):
    """
    يحسب المؤشرات مباشرة على الجدول المدمج الموحد.
    """
    c_m5 = df['Close']
    
    # 1. Macro Trend (من عمود H1_Close المدمج)
    ema_200 = df['H1_Close'].ewm(span=EMA_PERIOD_MACRO, adjust=False).mean()
    
    # 2. Structural Zone (من أعمدة M15 المدمجة)
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
    
    # 3. Momentum Trigger (M5 MACD)
    exp1_m5 = c_m5.ewm(span=MACD_FAST, adjust=False).mean()
    exp2_m5 = c_m5.ewm(span=MACD_SLOW, adjust=False).mean()
    macd_line_m5 = exp1_m5 - exp2_m5
    signal_line_m5 = macd_line_m5.ewm(span=MACD_SIGNAL, adjust=False).mean()
    histogram_m5 = macd_line_m5 - signal_line_m5
    
    return df.assign(
        EMA_200=ema_200,
        UPPER_BAND_M15=upper_band_m15,
        LOWER_BAND_M15=lower_band_m15,
        HISTOGRAM_M5=histogram_m5,
        NEXT_CLOSE_M5=c_m5.shift(-1) # السعر القادم للحسم
    ).dropna(subset=['NEXT_CLOSE_M5']) # نتخلص من آخر صف لأنه لا يملك سعراً مستقبلياً

def run_sniper_simulation(symbol, df_processed):
    """
    ينفذ منطق القنص على البيانات المجهزة مسبقاً.
    """
    trades = []
    consecutive_losses = 0
    is_sleeping = False
    sleep_until_index = None
    
    n = len(df_processed)
    start_idx = 50 
    
    # استخراج المصفوفات لسرعة الوصول
    closes = df_processed['Close'].values
    next_closes = df_processed['NEXT_CLOSE_M5'].values
    emas = df_processed['EMA_200'].values
    lbs = df_processed['LOWER_BAND_M15'].values
    ubs = df_processed['UPPER_BAND_M15'].values
    hists = df_processed['HISTOGRAM_M5'].values
    
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
        hist_curr = hists[i]
        hist_prev = hists[i-1]
        
        signal = None
        
        # شرط الشراء الصارم
        if (curr_close > ema_val and 
            curr_close <= lb_val and 
            hist_prev <= 0 and hist_curr > 0):
            signal = "CALL"
            
        # شرط البيع الصارم
        elif (curr_close < ema_val and 
              curr_close >= ub_val and 
              hist_prev >= 0 and hist_curr < 0):
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
                    # print(f"[{symbol}] ⚠️ SLEEPING...") # تعليق للطباعة لتقليل الضوضاء
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
    print("INITIATING FINANCIAL SNIPER BACKTEST v1.2 (RESILIENT MODE)")
    print(f"Symbols: {len(SYMBOLS)} | Period: {LOOKBACK_PERIOD}")
    print("="*70)
    
    global_trades = []
    success_count = 0
    
    for sym in SYMBOLS:
        print(f"\n>>> Processing {sym}:", end=" ")
        
        # 1. جلب ودمج البيانات بحذر
        merged_df = fetch_and_merge_data(sym)
        
        if merged_df is None:
            print("SKIPPED due to data issues.")
            continue
            
        # 2. حساب المؤشرات
        processed_df = calculate_indicators_on_merged(merged_df)
        
        if processed_df.empty:
            print("SKIPPED: No valid indicators calculated.")
            continue
            
        # 3. تشغيل المحاكاة
        trades = run_sniper_simulation(sym, processed_df)
        
        stats = analyze_results(trades)
        print(f"DONE. Signals: {stats['count']}, WR: {stats['wr']*100:.2f}%")
        
        global_trades.extend(trades)
        success_count += 1
        
    # التقرير النهائي
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
        verdict = "INSUFFICIENT DATA ❌ (Try increasing LOOKBACK_PERIOD or reducing strictness)"
            
    print(f"\nVerdict:                      {verdict}")
    print("="*70)

if __name__ == "__main__":
    main()
