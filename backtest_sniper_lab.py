import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time

# ═══════════════════════════════════════════════
# 1. إعدادات المختبر (Lab Configuration)
# ═══════════════════════════════════════════════
PAIRS = [
    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "USDCHF=X", 
    "AUDUSD=X", "USDCAD=X", "NZDUSD=X", "EURGBP=X", "EURJPY=X"
]

END_DATE = datetime.now().strftime("%Y-%m-%d")
# ★★ التعديل الجوهري: تغيير الفترة من 90 إلى 50 يوم لتجنب خطأ ياهو فاينانس ★★
START_DATE = (datetime.now() - timedelta(days=50)).strftime("%Y-%m-%d")

EMA_TREND_LEN = 200
EMA_ENTRY_LEN = 50
RSI_LEN = 14
TOUCH_THRESHOLD = 0.0015 

TRADE_DURATION_CANDLES = 3 # 15 minutes (3 candles of 5m)
SPREAD_PIPS = 2.0 
INITIAL_BALANCE = 10000
BET_SIZE = 100 

RESULT_FILE = "results.txt"

def log(msg):
    print(msg)
    with open(RESULT_FILE, "a", encoding="utf-8") as f:
        f.write(msg + "\n")

log(f"🧪 Starting Backtest Lab (Pure Pandas Mode): {START_DATE} to {END_DATE}")
log(f"⚠️ Note: Period limited to 50 days due to Yahoo Finance 5m data restriction.")

# ═══════════════════════════════════════════════
# 2. دوال حساب المؤشرات يدوياً (بدون pandas_ta)
# ═══════════════════════════════════════════════

def calculate_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def calculate_rsi(series, period):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

# ═══════════════════════════════════════════════
# 3. دوال المنطق (Logic Functions)
# ═══════════════════════════════════════════════

def check_bullish_engulfing(curr, prev):
    if prev['Close'] < prev['Open'] and curr['Close'] > curr['Open']:
        if curr['Close'] > prev['Open'] and curr['Open'] < prev['Close']: return True
    return False

def check_bearish_engulfing(curr, prev):
    if prev['Close'] > prev['Open'] and curr['Close'] < curr['Open']:
        if curr['Close'] < prev['Open'] and curr['Open'] > prev['Close']: return True
    return False

def check_pin_bar(candle, direction):
    body = abs(candle['Close'] - candle['Open'])
    if body == 0: body = 0.00001
    upper_wick = candle['High'] - max(candle['Open'], candle['Close'])
    lower_wick = min(candle['Open'], candle['Close']) - candle['Low']
    
    if direction == 'UP' and lower_wick > (body * 2) and upper_wick < body: return True
    if direction == 'DOWN' and upper_wick > (body * 2) and lower_wick < body: return True
    return False

# ═══════════════════════════════════════════════
# 4. محرك الاختبار (Backtest Engine)
# ═══════════════════════════════════════════════

def run_backtest_for_pair(symbol):
    log(f"\n📉 Testing {symbol}...")
    
    try:
        # جلب البيانات (الآن لفترة 50 يوم فقط)
        df_h1 = yf.download(symbol, start=START_DATE, end=END_DATE, interval="1h", progress=False, auto_adjust=False)
        df_m5 = yf.download(symbol, start=START_DATE, end=END_DATE, interval="5m", progress=False, auto_adjust=False)
        
        if df_h1.empty or df_m5.empty:
            log(f"   ⚠️ No data found for {symbol}")
            return []

        # تنظيف الأعمدة (MultiIndex fix)
        if isinstance(df_h1.columns, pd.MultiIndex): df_h1.columns = df_h1.columns.get_level_values(0)
        if isinstance(df_m5.columns, pd.MultiIndex): df_m5.columns = df_m5.columns.get_level_values(0)

        # حساب المؤشرات يدوياً
        df_h1['EMA_200'] = calculate_ema(df_h1['Close'], EMA_TREND_LEN)
        df_m5['EMA_50'] = calculate_ema(df_m5['Close'], EMA_ENTRY_LEN)
        df_m5['RSI'] = calculate_rsi(df_m5['Close'], RSI_LEN)
        
        trades = []
        valid_m5 = df_m5.dropna()
        
        # حلقة المحاكاة
        # نبدأ من الشمعة 200 لضمان جاهزية المؤشرات (EMA 200 يحتاج بيانات كافية)
        # ملاحظة: بما أننا نستخدم 50 يوم فقط، قد تكون البيانات محدودة لـ EMA 200 على H1
        # لكننا سنحاول البدء من أبعد نقطة ممكنة
        start_index = min(200, len(valid_m5) - TRADE_DURATION_CANDLES - 1)
        if start_index < 0: start_index = 0
        
        step = 5 # فحص كل 5 شموع لتسريع العملية
        
        for i in range(start_index, len(valid_m5) - TRADE_DURATION_CANDLES, step):
            curr_m5 = valid_m5.iloc[i]
            prev_m5 = valid_m5.iloc[i-1]
            curr_time = valid_m5.index[i]
            
            # 1. تحديد اتجاه H1 في هذه اللحظة
            h1_mask = df_h1.index <= curr_time
            if not h1_mask.any(): continue
            latest_h1 = df_h1[h1_mask].iloc[-1]
            
            if pd.isna(latest_h1['EMA_200']): continue
            
            trend = None
            if latest_h1['Close'] > latest_h1['EMA_200']: trend = "BULLISH"
            elif latest_h1['Close'] < latest_h1['EMA_200']: trend = "BEARISH"
            else: continue
            
            # 2. فحص شروط M5
            ema_50_val = curr_m5['EMA_50']
            rsi_val = curr_m5['RSI']
            
            # شرط اللمسة
            dist = abs(curr_m5['Low'] - ema_50_val) if trend == "BULLISH" else abs(curr_m5['High'] - ema_50_val)
            if (dist / ema_50_val) > TOUCH_THRESHOLD: continue
            
            signal = None
            
            # منطق الدخول
            if trend == "BULLISH" and rsi_val > 50:
                if check_bullish_engulfing(curr_m5, prev_m5) or check_pin_bar(curr_m5, 'UP'):
                    signal = "CALL"
            elif trend == "BEARISH" and rsi_val < 50:
                if check_bearish_engulfing(curr_m5, prev_m5) or check_pin_bar(curr_m5, 'DOWN'):
                    signal = "PUT"
            
            if signal:
                # 3. محاكاة النتيجة (بعد 3 شموع = 15 دقيقة)
                entry_price = curr_m5['Close']
                exit_candle = valid_m5.iloc[i + TRADE_DURATION_CANDLES]
                exit_price = exit_candle['Close']
                
                win = False
                if signal == "CALL" and exit_price > entry_price: win = True
                if signal == "PUT" and exit_price < entry_price: win = True
                
                # حساب النقاط (Pips)
                pip_mult = 100 if "JPY" in symbol else 10000
                pips_gained = abs(exit_price - entry_price) * pip_mult
                net_pips = pips_gained - SPREAD_PIPS if win else -pips_gained - SPREAD_PIPS
                
                trades.append({
                    "time": curr_time,
                    "signal": signal,
                    "win": win,
                    "pips": net_pips
                })
                
        return trades

    except Exception as e:
        log(f"   ❌ Error processing {symbol}: {e}")
        return []

# ═══════════════════════════════════════════════
# 5. التقرير النهائي (Final Report)
# ═══════════════════════════════════════════════

def main():
    all_trades = []
    
    for pair in PAIRS:
        trades = run_backtest_for_pair(pair)
        all_trades.extend(trades)
        time.sleep(2) # راحة للياهو فاينانس
    
    if not all_trades:
        log("\n❌ NO TRADES GENERATED IN THE PERIOD.")
        log("   Possible reasons: Market was sideways, or strict criteria were not met.")
        return

    df_results = pd.DataFrame(all_trades)
    total_trades = len(df_results)
    wins = df_results[df_results['win'] == True].shape[0]
    losses = total_trades - wins
    win_rate = (wins / total_trades) * 100
    
    total_pips = df_results['pips'].sum()
    
    # حساب الربح المالي التقديري (Payout 85%)
    profit_money = 0
    for _, row in df_results.iterrows():
        if row['win']:
            profit_money += (BET_SIZE * 0.85)
        else:
            profit_money -= BET_SIZE
            
    final_balance = INITIAL_BALANCE + profit_money
    roi = ((final_balance - INITIAL_BALANCE) / INITIAL_BALANCE) * 100

    log("\n" + "="*60)
    log("🏁 BACKTEST RESULTS (LAST 50 DAYS SIMULATION)")
    log("="*60)
    log(f"📅 Period: {START_DATE} to {END_DATE}")
    log(f"📊 Total Trades Found: {total_trades}")
    log(f"✅ Wins: {wins} | ❌ Losses: {losses}")
    log(f"🏆 WIN RATE: {win_rate:.2f}%")
    log(f"📈 Total Net Pips: {total_pips:.2f}")
    log(f"💰 Financial Simulation (Payout 85%):")
    log(f"   Start Balance: ${INITIAL_BALANCE}")
    log(f"   End Balance:   ${final_balance:.2f}")
    log(f"   ROI:           {roi:.2f}%")
    log("="*60)
    
    if win_rate > 60 and roi > 0:
        log("🟢 VERDICT: STRATEGY IS PROFITABLE. Ready for Live Demo.")
    else:
        log("🔴 VERDICT: STRATEGY NEEDS OPTIMIZATION. Do not trade live yet.")

if __name__ == "__main__":
    main()
