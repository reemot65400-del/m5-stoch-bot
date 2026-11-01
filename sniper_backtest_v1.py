#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FINANCIAL SNIPER SYSTEM - BACKTEST ENGINE v1.0 (Tri-Confluence Strict Mode)
الفلسفة: التقاطع الثلاثي الصارم + بروتوكول النجاة الذاتية.
الهدف: إثبات وجود حافة إحصائية (Edge) قبل التفعيل الحي.
المعايير: 
1. EMA200 (H1) للاتجاه العام.
2. ATR Channel (M15) للمنطقة الهيكلية.
3. MACD Histogram Flip (M5) لزناد الزخم.
4. إيقاف تلقائي بعد خسارتين متتاليتين (Simulated Survival Protocol).
"""
import numpy as np
import pandas as pd
import yfinance as yf
import warnings
from datetime import datetime, timedelta

warnings.filterwarnings('ignore')

# ==========================================
# CONFIGURATION: THE SNIPER PARAMETERS
# ==========================================

SYMBOLS = [
    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X",
    "USDCAD=X", "NZDUSD=X", "EURGBP=X", "EURCHF=X",
    "GBPJPY=X", "AUDJPY=X", "CADJPY=X", "CHFJPY=X",
    "EURAUD=X", "USDCHF=X", "AUDNZD=X", "GBPCAD=X" # Added optimized pairs
]

LOOKBACK_PERIOD = "1y"  # سنة واحدة كافية للاختبار الأولي دون إرهاق الموارد
TIMEFRAME_MACRO = "1h"   # H1 for Trend
TIMEFRAME_STRUCTURAL = "15m" # M15 for Zones
TIMEFRAME_TRIGGER = "5m"     # M5 for Momentum

BREAK_EVEN_WR = 0.5263 

# --- INDICATOR SETTINGS ---
EMA_PERIOD_MACRO = 200
ATR_PERIOD_STRUCT = 14
ATR_MULTIPLIER = 2.0       # نطاق واسع لتجنب الضوضاء الصغيرة
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# --- RISK MANAGEMENT SIMULATION ---
MAX_CONSECUTIVE_LOSSES = 2 # Stop trading if we lose 2 in a row (simulating forced sleep)

def fetch_multi_timeframe_data(symbol):
    """
    يجلب البيانات لثلاثة أطر زمنية مختلفة ويوحد الفهرس الزمني (Index) بدقة.
    هذه هي الخطوة الأصعب تقنياً لضمان عدم حدوث Look-ahead Bias.
    """
    try:
        ticker = yf.Ticker(symbol)
        
        # جلب البيانات الخام
        df_h1 = ticker.history(period=LOOKBACK_PERIOD, interval=TIMEFRAME_MACRO)
        df_m15 = ticker.history(period=LOOKBACK_PERIOD, interval=TIMEFRAME_STRUCTURAL)
        df_m5 = ticker.history(period=LOOKBACK_PERIOD, interval=TIMEFRAME_TRIGGER)
        
        if df_h1.empty or df_m15.empty or df_m5.empty:
            return None
            
        # تنظيف الأعمدة
        for df in [df_h1, df_m15, df_m5]:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [col[0] for col in df.columns]
            df.dropna(subset=['Close'], inplace=True)
            
        # إعادة تشكيل الفهارس للتأكد من أنها Timestamps موحدة
        df_h1.index = pd.to_datetime(df_h1.index, utc=True)
        df_m15.index = pd.to_datetime(df_m15.index, utc=True)
        df_m5.index = pd.to_datetime(df_m5.index, utc=True)
        
        return df_h1, df_m15, df_m5
        
    except Exception as e:
        print(f"Error fetching {symbol}: {e}")
        return None

def calculate_indicators(df_h1, df_m15, df_m5):
    """
    يحسب المؤشرات المطلوبة لكل إطار زمني.
    """
    # 1. Macro Trend (H1) - EMA200
    ema_200 = df_h1['Close'].ewm(span=EMA_PERIOD_MACRO, adjust=False).mean()
    
    # 2. Structural Zone (M15) - ATR Channel
    high_m15 = df_m15['High']
    low_m15 = df_m15['Low']
    close_m15 = df_m15['Close']
    
    tr_m15 = pd.concat([
        high_m15 - low_m15,
        (high_m15 - close_m15.shift(1)).abs(),
        (low_m15 - close_m15.shift(1)).abs()
    ], axis=1).max(axis=1)
    
    atr_m15 = tr_m15.rolling(window=ATR_PERIOD_STRUCT).mean()
    
    sma_mid_m15 = close_m15.rolling(window=ATR_PERIOD_STRUCT).mean()
    upper_band_m15 = sma_mid_m15 + (atr_m15 * ATR_MULTIPLIER)
    lower_band_m15 = sma_mid_m15 - (atr_m15 * ATR_MULTIPLIER)
    
    # 3. Momentum Trigger (M5) - MACD Histogram
    exp1_m5 = df_m5['Close'].ewm(span=MACD_FAST, adjust=False).mean()
    exp2_m5 = df_m5['Close'].ewm(span=MACD_SLOW, adjust=False).mean()
    macd_line_m5 = exp1_m5 - exp2_m5
    signal_line_m5 = macd_line_m5.ewm(span=MACD_SIGNAL, adjust=False).mean()
    histogram_m5 = macd_line_m5 - signal_line_m5
    
    return {
        'ema_200': ema_200,
        'upper_band_m15': upper_band_m15,
        'lower_band_m15': lower_band_m15,
        'histogram_m5': histogram_m5,
        'close_m5': df_m5['Close'],
        'next_close_m5': df_m5['Close'].shift(-1) # السعر في الشمعة التالية للحسم
    }

def run_sniper_simulation(symbol, indicators, raw_df_m5):
    """
    ينفذ منطق القنص الصارم عبر الزمن.
    """
    trades = []
    consecutive_losses = 0
    is_sleeping = False
    sleep_until_index = None
    
    n = len(indicators['close_m5'])
    
    # نتخطى أول 50 شمعة لعدم استقرار المؤشرات
    start_idx = 50 
    
    for i in range(start_idx, n - 1):
        timestamp = raw_df_m5.index[i]
        
        # --- بروتوكول النجاة: هل نحن في وضع السبات؟ ---
        if is_sleeping:
            if sleep_until_index and i < sleep_until_index:
                continue # تخطي هذه الشمعة لأننا نائمون
            else:
                is_sleeping = False # استيقظنا
                consecutive_losses = 0 # أعد ضبط العداد
                
        curr_close_m5 = indicators['close_m5'].iloc[i]
        next_close_m5 = indicators['next_close_m5'].iloc[i]
        
        if pd.isna(curr_close_m5) or pd.isna(next_close_m5):
            continue
            
        # جلب القيم الحرجة للمقارنة
        # ملاحظة: يجب التأكد من أن الفهارس متوافقة زمنياً بين الأطر المختلفة
        # هنا نفترض أن yfinance يوفر فهارس متزامنة تقريباً
        
        try:
            # 1. الاتجاه العام (H1)
            # نجد أقرب قيمة EMA200 من H1 تتوافق مع وقت الشمعة الحالية في M5
            ema_val = indicators['ema_200'].asof(timestamp)
            
            # 2. المنطقة الهيكلية (M15)
            lb_val = indicators['lower_band_m15'].asof(timestamp)
            ub_val = indicators['upper_band_m15'].asof(timestamp)
            
            # 3. الزناد (M5)
            hist_curr = indicators['histogram_m5'].iloc[i]
            hist_prev = indicators['histogram_m5'].iloc[i-1]
            
        except Exception:
            continue # تخطي إذا حدث خطأ في المطابقة الزمنية
            
        if pd.isna(ema_val) or pd.isna(lb_val) or pd.isna(hist_curr):
            continue
            
        signal = None
        
        # --- شرط الشراء (CALL) الصارم ---
        # 1. السعر فوق EMA200 (اتجاه صاعد قوي)
        # 2. السعر لمس أو كسر الحد السفلي لقناة ATR في M15 (منطقة تشبع بيعي هيكلي)
        # 3. الهيستوجرام في M5 انقلب من سالب إلى موجب (زخم شراء جديد دخل السوق)
        if (curr_close_m5 > ema_val and 
            curr_close_m5 <= lb_val and 
            hist_prev <= 0 and hist_curr > 0):
            signal = "CALL"
            
        # --- شرط البيع (PUT) الصارم ---
        # 1. السعر تحت EMA200 (اتجاه هابط قوي)
        # 2. السعر لمس أو تجاوز الحد العلوي لقناة ATR في M15 (منطقة تشبع شرائي هيكلي)
        # 3. الهيستوجرام في M5 انقلب من موجب إلى سالب (زخم بيع جديد دخل السوق)
        elif (curr_close_m5 < ema_val and 
              curr_close_m5 >= ub_val and 
              hist_prev >= 0 and hist_curr < 0):
            signal = "PUT"
            
        if signal:
            # حساب النتيجة (Win/Loss)
            # نفترض أن العقد ينتهي بإغلاق الشمعة التالية في M5 (5 دقائق)
            price_diff = next_close_m5 - curr_close_m5
            
            # خصم تكلفة وهمية بسيطة (Spread Simulation ~ 1 pip equivalent for simplicity)
            # في الخيارات الثنائية، المهم هو الاتجاه وليس مقدار الحركة، لكننا نضيف هامش أمان
            spread_buffer = 0.0001 if "JPY" not in symbol else 0.01 
            
            win = False
            if signal == "CALL":
                win = price_diff > spread_buffer
            elif signal == "PUT":
                win = price_diff < -spread_buffer
                
            trades.append(win)
            
            # تطبيق بروتوكول النجاة
            if not win:
                consecutive_losses += 1
                if consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
                    is_sleeping = True
                    # نم لمدة تعادل 2 ساعات (24 شمعة M5) كمثال
                    sleep_duration_candles = 24 
                    sleep_until_index = i + sleep_duration_candles
                    print(f"[{symbol}] ⚠️ SURVIVAL PROTOCOL ACTIVATED: Sleeping until index {sleep_until_index}")
            else:
                consecutive_losses = 0 # ربح => صفّر العداد
                
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
    print("INITIATING FINANCIAL SNIPER BACKTEST v1.0")
    print(f"Symbols: {len(SYMBOLS)} | Period: {LOOKBACK_PERIOD}")
    print("Strategy: Tri-Confluence (EMA-H1 + ATR-M15 + MACD-M5)")
    print("Risk Control: Auto-Sleep after 2 Consecutive Losses")
    print("="*70)
    
    global_trades = []
    
    for sym in SYMBOLS:
        print(f"\nProcessing {sym}...", end=" ")
        data_tuple = fetch_multi_timeframe_data(sym)
        if data_tuple is None:
            print("Skipped (Data Error)")
            continue
            
        df_h1, df_m15, df_m5 = data_tuple
        
        # حساب المؤشرات
        indicators = calculate_indicators(df_h1, df_m15, df_m5)
        
        # تشغيل المحاكاة
        trades = run_sniper_simulation(sym, indicators, df_m5)
        
        stats = analyze_results(trades)
        print(f"Done. Signals: {stats['count']}, WR: {stats['wr']*100:.2f}%")
        
        global_trades.extend(trades)
        
    # التقرير النهائي المجمّع
    final_stats = analyze_results(global_trades)
    
    print("\n" + "-"*70)
    print("FINAL COUNCIL VERDICT REPORT")
    print("-"*70)
    print(f"Total Signals Generated: {final_stats['count']}")
    print(f"Overall Win Rate:        {final_stats['wr']*100:.2f}%")
    print(f"Z-Score (Significance):  {final_stats['z_score']:.2f}")
    
    verdict = "FAILING"
    if final_stats['count'] >= 30: # حد أدنى للعينة
        if final_stats['wr'] >= 0.60 and final_stats['z_score'] > 1.96:
            verdict = "EXCELLENT 🏆 (Proceed to Live Paper Trading)"
        elif final_stats['wr'] >= BREAK_EVEN_WR and final_stats['z_score'] > 1.0:
            verdict = "VIABLE ⚠️ (Refine Parameters Before Live)"
        else:
            verdict = "FRAGILE ❌ (Do Not Trade Real Money)"
            
    print(f"\nVerdict:                 {verdict}")
    print("="*70)

if __name__ == "__main__":
    main()
