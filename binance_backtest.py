import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# ═══════════════════════════════════════════════
# استراتيجية Mean Reversion V5 - Pump/Dump Detection
# فريم: 15 دقيقة | كشف العملات المتحركة بقوة
# ═══════════════════════════════════════════════
ASSET_POOL = [
    "BTC-USD", "ETH-USD", "BNB-USD", "SOL-USD", 
    "XRP-USD", "ADA-USD", "DOGE-USD", "TRX-USD", 
    "DOT-USD", "AVAX-USD", "LINK-USD", "MATIC-USD", 
    "UNI-USD", "LTC-USD", "ATOM-USD", "FIL-USD", 
    "ETC-USD", "XLM-USD", "ALGO-USD", "VET-USD",
    "SHIB-USD", "PEPE-USD", "ARB-USD", "OP-USD",
    "SUI-USD", "SEI-USD", "TIA-USD", "JUP-USD"
]

def detect_pump_dump(df, lookback_period=24):
    """
    كشف العملات التي تشهد بومب أو دب
    lookback_period: عدد الساعات للنظر للخلف (افتراضي 24 ساعة)
    """
    # حساب عدد الشموع (كل شمعة 15 دقيقة = 4 شموع في الساعة)
    candles = lookback_period * 4
    
    if len(df) < candles:
        return False, 0, 0
    
    # أخذ آخر N شمعة
    recent = df.tail(candles)
    
    # حساب نسبة التغير في السعر
    price_change = ((recent['Close'].iloc[-1] - recent['Close'].iloc[0]) / recent['Close'].iloc[0]) * 100
    
    # حساب متوسط الحجم
    avg_volume = recent['Volume'].mean()
    current_volume = recent['Volume'].iloc[-1]
    volume_ratio = current_volume / avg_volume if avg_volume > 0 else 0
    
    # شروط البومب/الدب:
    # 1. تغير سعري أكثر من 5% (يمكن تعديله)
    # 2. حجم تداول أعلى من المتوسط بـ 1.5 مرة على الأقل
    is_pump_dump = (abs(price_change) >= 5) and (volume_ratio >= 1.5)
    
    return is_pump_dump, price_change, volume_ratio

def calculate_indicators(df):
    """حساب جميع المؤشرات: BB, RSI, ADX, MACD"""
    # Bollinger Bands (20, 2)
    df['BB_Middle'] = df['Close'].rolling(window=20).mean()
    bb_std = df['Close'].rolling(window=20).std()
    df['BB_Upper'] = df['BB_Middle'] + (bb_std * 2)
    df['BB_Lower'] = df['BB_Middle'] - (bb_std * 2)
    df['BB_Width'] = (df['BB_Upper'] - df['BB_Lower']) / df['BB_Middle']
    
    # RSI (14)
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    
    # Volume MA (20)
    df['Vol_MA'] = df['Volume'].rolling(window=20).mean()
    
    # ADX (14)
    high = df['High']
    low = df['Low']
    close = df['Close']
    
    plus_dm = high.diff()
    minus_dm = -low.diff()
    
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    atr = tr.rolling(window=14).mean()
    plus_di = 100 * (plus_dm.rolling(window=14).mean() / atr)
    minus_di = 100 * (minus_dm.rolling(window=14).mean() / atr)
    
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di))
    df['ADX'] = dx.rolling(window=14).mean()
    
    # MACD (12, 26, 9)
    ema_12 = df['Close'].ewm(span=12, adjust=False).mean()
    ema_26 = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = ema_12 - ema_26
    df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']
    
    return df

def detect_reversal_pattern(row, prev_row, direction):
    """كشف نماذج الانعكاس"""
    body_curr = abs(row['Close'] - row['Open'])
    body_prev = abs(prev_row['Close'] - prev_row['Open'])
    range_curr = row['High'] - row['Low']
    
    if range_curr == 0: return False
    
    if direction == 'LONG':
        lower_shadow = min(row['Open'], row['Close']) - row['Low']
        upper_shadow = row['High'] - max(row['Open'], row['Close'])
        is_hammer = (lower_shadow > (body_curr * 2)) and (upper_shadow < body_curr)
        
        is_engulfing = (row['Close'] > row['Open']) and \
                      (prev_row['Close'] < prev_row['Open']) and \
                      (body_curr > body_prev) and \
                      (row['Open'] < prev_row['Close']) and \
                      (row['Close'] > prev_row['Open'])
        
        return is_hammer or is_engulfing
    else:
        upper_shadow = row['High'] - max(row['Open'], row['Close'])
        lower_shadow = min(row['Open'], row['Close']) - row['Low']
        is_shooting = (upper_shadow > (body_curr * 2)) and (lower_shadow < body_curr)
        
        is_engulfing = (row['Close'] < row['Open']) and \
                      (prev_row['Close'] > prev_row['Open']) and \
                      (body_curr > body_prev) and \
                      (row['Open'] > prev_row['Close']) and \
                      (row['Close'] < prev_row['Open'])
        
        return is_shooting or is_engulfing

def run_backtest():
    valid_symbols = []
    pump_dump_symbols = []
    
    print("🔍 المرحلة 1: التحقق من توفر الأصول على Yahoo Finance...")
    
    # التحقق من توفر الرموز
    for symbol in ASSET_POOL:
        try:
            test = yf.download(symbol, period="1d", interval="15m", progress=False)
            if not test.empty:
                valid_symbols.append(symbol)
        except:
            pass
            
    print(f"✅ تم العثور على {len(valid_symbols)} أصلاً صالحاً.")
    
    if not valid_symbols:
        print("❌ لم يتم العثور على أي أصول صالحة. إنهاء.")
        return
    
    print("\n🚀 المرحلة 2: كشف العملات التي تشهد بومب/دب (آخر 24 ساعة)...")
    
    # فحص كل عملة لمعرفة إذا كانت تشهد بومب أو دب
    for symbol in valid_symbols:
        try:
            df = yf.download(symbol, period="5d", interval="15m", progress=False)
            
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            
            if df.empty or len(df) < 96:  # 96 شمعة = 24 ساعة
                continue
            
            is_pump_dump, price_change, volume_ratio = detect_pump_dump(df, lookback_period=24)
            
            if is_pump_dump:
                pump_dump_symbols.append({
                    'symbol': symbol,
                    'price_change': round(price_change, 2),
                    'volume_ratio': round(volume_ratio, 2),
                    'type': 'PUMP' if price_change > 0 else 'DUMP'
                })
                print(f"  🔥 {symbol}: {price_change:+.2f}% | Volume: {volume_ratio:.1f}x | Type: {'PUMP' if price_change > 0 else 'DUMP'}")
                
        except Exception as e:
            print(f"  ⚠️ خطأ في {symbol}: {str(e)[:50]}")
    
    print(f"\n✅ تم اكتشاف {len(pump_dump_symbols)} عملة تشهد بومب/دب")
    
    if not pump_dump_symbols:
        print("⚠️ لم يتم العثور على عملات تشهد بومب/دب حالياً. استخدام جميع العملات الصالحة.")
        test_symbols = valid_symbols[:10]  # استخدام أول 10 عملات
    else:
        test_symbols = [item['symbol'] for item in pump_dump_symbols]
    
    print(f"\n🎯 المرحلة 3: بدء الاختبار الخلفي على {len(test_symbols)} عملة متحركة...")
    
    all_trades = []
    
    for symbol in test_symbols:
        try:
            # جلب بيانات 60 يوم على فريم 15 دقيقة
            df = yf.download(symbol, period="60d", interval="15m", progress=False)
            
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
                
            if df.empty or len(df) < 100: 
                continue
                
            df = calculate_indicators(df).dropna()
            
            wins, losses, ties = 0, 0, 0
            
            for i in range(100, len(df) - 5):
                curr = df.iloc[i]
                prev = df.iloc[i-1]
                
                # الفلاتر
                adx_ok = curr['ADX'] > 20
                bb_width_ok = curr['BB_Width'] > 0.01
                
                macd_long = curr['MACD_Hist'] > prev['MACD_Hist']
                macd_short = curr['MACD_Hist'] < prev['MACD_Hist']
                
                if not (adx_ok and bb_width_ok):
                    continue
                
                # شروط LONG
                long_condition = (
                    curr['Close'] <= curr['BB_Lower'] and
                    curr['RSI'] < 35 and
                    curr['Volume'] > curr['Vol_MA'] * 0.8 and
                    macd_long and
                    detect_reversal_pattern(curr, prev, 'LONG')
                )
                
                # شروط SHORT
                short_condition = (
                    curr['Close'] >= curr['BB_Upper'] and
                    curr['RSI'] > 65 and
                    curr['Volume'] > curr['Vol_MA'] * 0.8 and
                    macd_short and
                    detect_reversal_pattern(curr, prev, 'SHORT')
                )
                
                signal = None
                if long_condition:
                    signal = 'LONG'
                elif short_condition:
                    signal = 'SHORT'
                
                if signal:
                    entry_price = curr['Close']
                    exit_price = df.iloc[i+5]['Close']
                    
                    if signal == 'LONG':
                        profit = exit_price - entry_price
                    else:
                        profit = entry_price - exit_price
                    
                    if profit > 0:
                        wins += 1
                    elif profit < 0:
                        losses += 1
                    else:
                        ties += 1
                    
            total = wins + losses
            if total > 0:
                all_trades.append({
                    'Symbol': symbol, 
                    'Trades': total, 
                    'Wins': wins, 
                    'Losses': losses,
                    'WR %': round((wins/total)*100, 2)
                })
                
        except Exception as e:
            print(f"⚠️ خطأ في {symbol}: {str(e)[:50]}")

    if not all_trades:
        print("❌ لم يتم توليد أي صفقات.")
        return

    df_res = pd.DataFrame(all_trades)
    total_w = df_res['Wins'].sum()
    total_l = df_res['Losses'].sum()
    total_t = total_w + total_l
    overall_wr = (total_w / total_t) * 100 if total_t > 0 else 0
    
    avg_trades = total_t / len(all_trades)
    assets_above_60 = len(df_res[df_res['WR %'] >= 60])
    assets_above_70 = len(df_res[df_res['WR %'] >= 70])
    assets_below_50 = len(df_res[df_res['WR %'] < 50])
    
    print("\n" + "="*70)
    print("📊 MEAN REVERSION V5 BACKTEST (PUMP/DUMP DETECTION)")
    print("="*70)
    print(df_res.to_string(index=False))
    print("-"*70)
    print(f"🔥 PUMP/DUMP ASSETS TESTED: {len(all_trades)}")
    print(f"🌟 ASSETS WITH WR ≥ 70%: {assets_above_70}")
    print(f"✅ ASSETS WITH WR ≥ 60%: {assets_above_60}")
    print(f"📉 ASSETS WITH WR < 50%: {assets_below_50}")
    print(f"📊 AVG TRADES/ASSET: {avg_trades:.1f}")
    print(f"🏆 TOTAL TRADES: {total_t}")
    print(f"✅ WINS: {total_w} | ❌ LOSSES: {total_l}")
    print(f"🎯 WIN RATE: {overall_wr:.2f}%")
    print("-"*70)
    
    if overall_wr >= 70:
        print("🔥 VERDICT: EXCELLENT - READY FOR LIVE TRADING!")
    elif overall_wr >= 65:
        print("✅ VERDICT: STRONG - RECOMMENDED WITH CONFIDENCE")
    elif overall_wr >= 60:
        print("️ VERDICT: GOOD - NEEDS MINOR TUNING")
    elif overall_wr >= 55:
        print("⚠️ VERDICT: MARGINAL - CONSIDER REVISION")
    else:
        print("💀 VERDICT: FAILING - REJECT STRATEGY")

if __name__ == "__main__":
    run_backtest()
