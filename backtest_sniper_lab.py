import yfinance as yf
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timedelta
import time

# ═══════════════════════════════════════════════
# 1. إعدادات المختبر (Lab Configuration)
# ═══════════════════════════════════════════════
PAIRS = [
    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "USDCHF=X", 
    "AUDUSD=X", "USDCAD=X", "NZDUSD=X", "EURGBP=X", "EURJPY=X"
]

START_DATE = "2023-10-01"
END_DATE = "2024-10-01"

# معاملات الاستراتيجية (يجب أن تطابق البوت الحي)
EMA_TREND_LEN = 200
EMA_ENTRY_LEN = 50
RSI_LEN = 14
TOUCH_THRESHOLD = 0.0015 # 0.15%

# إعدادات المحاكاة
TRADE_DURATION_CANDLES = 3 # 3 شموع M5 = 15 دقيقة
SPREAD_PIPS = 2.0 # تكلفة السبريد الافتراضية لكل صفقة
INITIAL_BALANCE = 10000
BET_SIZE = 100 # مبلغ ثابت لكل صفقة

print(f"🧪 Starting Backtest Lab: {START_DATE} to {END_DATE}")
print(f"💰 Initial Balance: ${INITIAL_BALANCE} | Bet Size: ${BET_SIZE}")

# ═══════════════════════════════════════════════
# 2. دوال المنطق (Logic Functions - Same as Bot)
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
# 3. محرك الاختبار (Backtest Engine)
# ═══════════════════════════════════════════════

def run_backtest_for_pair(symbol):
    print(f"\n📉 Testing {symbol}...")
    
    # جلب البيانات (نحتاج بيانات H1 و M5)
    # ملاحظة: yfinance قد يكون بطيئاً للسنة الكاملة ببيانات M5، لذا سنقسمها أو نستخدم فترة أقصر إذا لزم الأمر
    try:
        # لجلب بيانات كافية للـ EMA 200 على H1، نحتاج فترة طويلة
        df_h1 = yf.download(symbol, start=START_DATE, end=END_DATE, interval="1h", progress=False, auto_adjust=False)
        df_m5 = yf.download(symbol, start=START_DATE, end=END_DATE, interval="5m", progress=False, auto_adjust=False)
        
        if df_h1.empty or df_m5.empty:
            print(f"   ⚠️ No data found for {symbol}")
            return []

        # تنظيف الأعمدة (MultiIndex fix)
        if isinstance(df_h1.columns, pd.MultiIndex): df_h1.columns = df_h1.columns.get_level_values(0)
        if isinstance(df_m5.columns, pd.MultiIndex): df_m5.columns = df_m5.columns.get_level_values(0)

        # حساب المؤشرات
        df_h1['EMA_200'] = ta.ema(df_h1['Close'], length=EMA_TREND_LEN)
        df_m5['EMA_50'] = ta.ema(df_m5['Close'], length=EMA_ENTRY_LEN)
        df_m5['RSI'] = ta.rsi(df_m5['Close'], length=RSI_LEN)
        
        trades = []
        
        # نحن نمر على شموع M5، ونبحث عن对应的 شمعة H1
        # لتبسيط الكود وتسريعه، سنقوم بعمل merge تقريبي أو فحص عند كل شمعة M5
        
        valid_m5 = df_m5.dropna()
        
        # حلقة المحاكة (Simulation Loop)
        # نبدأ من الشمعة 200 لضمان جاهزية المؤشرات
        for i in range(200, len(valid_m5) - TRADE_DURATION_CANDLES):
            curr_m5 = valid_m5.iloc[i]
            prev_m5 = valid_m5.iloc[i-1]
            curr_time = valid_m5.index[i]
            
            # 1. تحديد اتجاه H1 في هذه اللحظة
            # نجد أقرب شمعة H1 سابقة أو حالية
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
                # 3. محاكاة النتيجة (بعد 3 شموع)
                entry_price = curr_m5['Close']
                exit_candle = valid_m5.iloc[i + TRADE_DURATION_CANDLES]
                exit_price = exit_candle['Close']
                
                win = False
                if signal == "CALL" and exit_price > entry_price: win = True
                if signal == "PUT" and exit_price < entry_price: win = True
                
                # حساب النقاط (Pips) تقريبياً
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
        print(f"   ❌ Error processing {symbol}: {e}")
        return []

# ═══════════════════════════════════════════════
# 4. التقرير النهائي (Final Report)
# ═══════════════════════════════════════════════

def main():
    all_trades = []
    
    for pair in PAIRS:
        trades = run_backtest_for_pair(pair)
        all_trades.extend(trades)
        time.sleep(2) # راحة للياهو فاينانس
    
    if not all_trades:
        print("\n❌ NO TRADES GENERATED IN THE ENTIRE YEAR.")
        return

    df_results = pd.DataFrame(all_trades)
    total_trades = len(df_results)
    wins = df_results[df_results['win'] == True].shape[0]
    losses = total_trades - wins
    win_rate = (wins / total_trades) * 100
    
    total_pips = df_results['pips'].sum()
    
    # حساب الربح المالي التقديري
    # نفترض payout 85% للفوز، و خسارة 100% للخسارة
    profit_money = 0
    for _, row in df_results.iterrows():
        if row['win']:
            profit_money += (BET_SIZE * 0.85)
        else:
            profit_money -= BET_SIZE
            
    final_balance = INITIAL_BALANCE + profit_money
    roi = ((final_balance - INITIAL_BALANCE) / INITIAL_BALANCE) * 100

    print("\n" + "="*60)
    print("🏁 BACKTEST RESULTS (1 YEAR SIMULATION)")
    print("="*60)
    print(f"📅 Period: {START_DATE} to {END_DATE}")
    print(f"📊 Total Trades Found: {total_trades}")
    print(f"✅ Wins: {wins} | ❌ Losses: {losses}")
    print(f"🏆 WIN RATE: {win_rate:.2f}%")
    print(f"📈 Total Net Pips: {total_pips:.2f}")
    print(f"💰 Financial Simulation (Payout 85%):")
    print(f"   Start Balance: ${INITIAL_BALANCE}")
    print(f"   End Balance:   ${final_balance:.2f}")
    print(f"   ROI:           {roi:.2f}%")
    print("="*60)
    
    if win_rate > 60 and roi > 0:
        print("🟢 VERDICT: STRATEGY IS PROFITABLE. Ready for Live Demo.")
    else:
        print("🔴 VERDICT: STRATEGY NEEDS OPTIMIZATION. Do not trade live yet.")

if __name__ == "__main__":
    main()
