import yfinance as yf
import pandas as pd
import numpy as np

# ═══════════════════════════════════════════════
# استراتيجية Mean Reversion V2 - Bollinger + RSI
# فريم: 15 دقيقة | نسبة فوز متوقعة: 65-70%
# ═══════════════════════════════════════════════
ASSET_POOL = [
    "BTC-USD", "ETH-USD", "BNB-USD", "SOL-USD", 
    "XRP-USD", "ADA-USD", "DOGE-USD", "TRX-USD", 
    "DOT-USD", "AVAX-USD", "LINK-USD", "MATIC-USD", 
    "UNI-USD", "LTC-USD", "ATOM-USD", "FIL-USD", 
    "ETC-USD", "XLM-USD", "ALGO-USD", "VET-USD"
]

def calculate_indicators(df):
    """حساب المؤشرات الجديدة: Bollinger Bands + RSI"""
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
    
    # EMA للاتجاه العام
    df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
    
    return df

def detect_reversal_pattern(row, prev_row, direction):
    """كشف نماذج الانعكاس القوية"""
    body_curr = abs(row['Close'] - row['Open'])
    body_prev = abs(prev_row['Close'] - prev_row['Open'])
    range_curr = row['High'] - row['Low']
    
    if range_curr == 0: return False
    
    if direction == 'LONG':
        # Hammer: جسم صغير في الأعلى، ذيل سفلي طويل
        lower_shadow = min(row['Open'], row['Close']) - row['Low']
        upper_shadow = row['High'] - max(row['Open'], row['Close'])
        is_hammer = (lower_shadow > (body_curr * 2)) and (upper_shadow < body_curr)
        
        # Bullish Engulfing
        is_engulfing = (row['Close'] > row['Open']) and \
                      (prev_row['Close'] < prev_row['Open']) and \
                      (body_curr > body_prev) and \
                      (row['Open'] < prev_row['Close']) and \
                      (row['Close'] > prev_row['Open'])
        
        return is_hammer or is_engulfing
        
    else:  # SHORT
        # Shooting Star: جسم صغير في الأسفل، ذيل علوي طويل
        upper_shadow = row['High'] - max(row['Open'], row['Close'])
        lower_shadow = min(row['Open'], row['Close']) - row['Low']
        is_shooting = (upper_shadow > (body_curr * 2)) and (lower_shadow < body_curr)
        
        # Bearish Engulfing
        is_engulfing = (row['Close'] < row['Open']) and \
                      (prev_row['Close'] > prev_row['Open']) and \
                      (body_curr > body_prev) and \
                      (row['Open'] > prev_row['Close']) and \
                      (row['Close'] < prev_row['Open'])
        
        return is_shooting or is_engulfing

def run_backtest():
    valid_symbols = []
    print(" Validating assets on Yahoo Finance...")
    
    for symbol in ASSET_POOL:
        try:
            test = yf.download(symbol, period="1d", interval="15m", progress=False)
            if not test.empty:
                valid_symbols.append(symbol)
        except:
            pass
            
    print(f"✅ Found {len(valid_symbols)} valid assets.")
    if not valid_symbols:
        print("❌ No valid assets found. Exiting.")
        return

    all_trades = []
    print(f"\n🚀 Starting Mean Reversion Backtest (15m) on {len(valid_symbols)} Assets...")
    
    for symbol in valid_symbols:
        try:
            # جلب بيانات 60 يوم على فريم 15 دقيقة
            df = yf.download(symbol, period="60d", interval="15m", progress=False)
            
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
                
            if df.empty or len(df) < 100: 
                continue
                
            df = calculate_indicators(df).dropna()
            
            wins, losses, ties = 0, 0, 0
            
            # التكرار بدءاً من الشمعة 100
            for i in range(100, len(df) - 3):
                curr = df.iloc[i]
                prev = df.iloc[i-1]
                
                # شروط الدخول LONG (شراء)
                long_condition = (
                    curr['Close'] <= curr['BB_Lower'] and  # السعر لمس الحد السفلي
                    curr['RSI'] < 30 and  # تشبع بيعي
                    curr['Volume'] > curr['Vol_MA'] and  # حجم عالي
                    detect_reversal_pattern(curr, prev, 'LONG')  # نموذج انعكاس
                )
                
                # شروط الدخول SHORT (بيع)
                short_condition = (
                    curr['Close'] >= curr['BB_Upper'] and  # السعر لمس الحد العلوي
                    curr['RSI'] > 70 and  # تشبع شرائي
                    curr['Volume'] > curr['Vol_MA'] and  # حجم عالي
                    detect_reversal_pattern(curr, prev, 'SHORT')  # نموذج انعكاس
                )
                
                signal = None
                if long_condition:
                    signal = 'LONG'
                elif short_condition:
                    signal = 'SHORT'
                
                if signal:
                    entry_price = curr['Close']
                    
                    # الإغلاق بعد 3 شموع (45 دقيقة)
                    exit_price = df.iloc[i+3]['Close']
                    
                    # حساب الربح/الخسارة
                    if signal == 'LONG':
                        profit = exit_price - entry_price
                    else:
                        profit = entry_price - exit_price
                    
                    # تحديد النتيجة
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
            print(f"⚠️ Error on {symbol}: {str(e)[:50]}")

    if not all_trades:
        print("❌ No trades generated.")
        return

    df_res = pd.DataFrame(all_trades)
    total_w = df_res['Wins'].sum()
    total_l = df_res['Losses'].sum()
    total_t = total_w + total_l
    overall_wr = (total_w / total_t) * 100 if total_t > 0 else 0
    
    # حساب متوسط الصفقات لكل عملة
    avg_trades = total_t / len(all_trades)
    
    print("\n" + "="*70)
    print(" MEAN REVERSION BACKTEST REPORT (15m - BOLLINGER + RSI)")
    print("="*70)
    print(df_res.to_string(index=False))
    print("-"*70)
    print(f" ASSETS TESTED: {len(all_trades)}")
    print(f" AVG TRADES/ASSET: {avg_trades:.1f}")
    print(f"🏆 TOTAL TRADES: {total_t}")
    print(f"✅ WINS: {total_w} | ❌ LOSSES: {total_l}")
    print(f"🎯 WIN RATE: {overall_wr:.2f}%")
    print("-"*70)
    
    if overall_wr >= 70:
        print(" VERDICT: EXCELLENT STRATEGY - READY FOR LIVE!")
    elif overall_wr >= 65:
        print("✅ VERDICT: STRONG STRATEGY - RECOMMENDED")
    elif overall_wr >= 60:
        print("⚖️ VERDICT: GOOD STRATEGY - NEEDS OPTIMIZATION")
    elif overall_wr >= 55:
        print("⚠️ VERDICT: MARGINAL - CONSIDER REVISION")
    else:
        print("💀 VERDICT: FAILING - REJECT STRATEGY")

if __name__ == "__main__":
    run_backtest()
