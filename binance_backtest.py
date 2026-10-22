import yfinance as yf
import pandas as pd
import numpy as np

# ═══════════════════════════════════════════════
# استراتيجية Mean Reversion V3 - مع فلتر ADX
# فريم: 15 دقيقة | نسبة فوز مستهدفة: 65%+
# ══════════════════════════════════════════════
ASSET_POOL = [
    "BTC-USD", "ETH-USD", "BNB-USD", "SOL-USD", 
    "XRP-USD", "ADA-USD", "DOGE-USD", "TRX-USD", 
    "DOT-USD", "AVAX-USD", "LINK-USD", "MATIC-USD", 
    "UNI-USD", "LTC-USD", "ATOM-USD", "FIL-USD", 
    "ETC-USD", "XLM-USD", "ALGO-USD", "VET-USD"
]

def calculate_indicators(df):
    """حساب جميع المؤشرات بما فيها ADX"""
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
    
    # ADX (14) - مؤشر قوة الاتجاه
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
    print("🔍 Validating assets on Yahoo Finance...")
    
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
    print(f"\n Starting Mean Reversion V3 Backtest (15m) on {len(valid_symbols)} Assets...")
    
    for symbol in valid_symbols:
        try:
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
                
                # ⭐ فلتر ADX الجديد: نتجنب الأسواق الجانبية
                adx_ok = curr['ADX'] > 25
                
                # ⭐ فلتر BB Width: نتجنب الأسواق المضغوطة جداً
                bb_width_ok = curr['BB_Width'] > 0.02
                
                if not (adx_ok and bb_width_ok):
                    continue
                
                # شروط LONG
                long_condition = (
                    curr['Close'] <= curr['BB_Lower'] and
                    curr['RSI'] < 30 and
                    curr['Volume'] > curr['Vol_MA'] and
                    detect_reversal_pattern(curr, prev, 'LONG')
                )
                
                # شروط SHORT
                short_condition = (
                    curr['Close'] >= curr['BB_Upper'] and
                    curr['RSI'] > 70 and
                    curr['Volume'] > curr['Vol_MA'] and
                    detect_reversal_pattern(curr, prev, 'SHORT')
                )
                
                signal = None
                if long_condition:
                    signal = 'LONG'
                elif short_condition:
                    signal = 'SHORT'
                
                if signal:
                    entry_price = curr['Close']
                    
                    # ⭐ تمديد المدة إلى 5 شموع (75 دقيقة)
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
            print(f"⚠️ Error on {symbol}: {str(e)[:50]}")

    if not all_trades:
        print("❌ No trades generated. Try relaxing ADX filter.")
        return

    df_res = pd.DataFrame(all_trades)
    total_w = df_res['Wins'].sum()
    total_l = df_res['Losses'].sum()
    total_t = total_w + total_l
    overall_wr = (total_w / total_t) * 100 if total_t > 0 else 0
    
    avg_trades = total_t / len(all_trades)
    
    # إحصائيات إضافية
    assets_above_60 = len(df_res[df_res['WR %'] >= 60])
    assets_below_50 = len(df_res[df_res['WR %'] < 50])
    
    print("\n" + "="*70)
    print("📊 MEAN REVERSION V3 BACKTEST (ADX FILTER + 75min HOLD)")
    print("="*70)
    print(df_res.to_string(index=False))
    print("-"*70)
    print(f"🔢 ASSETS TESTED: {len(all_trades)}")
    print(f" ASSETS WITH WR ≥ 60%: {assets_above_60}")
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
        print("⚖️ VERDICT: GOOD - NEEDS MINOR TUNING")
    elif overall_wr >= 55:
        print("⚠️ VERDICT: MARGINAL - CONSIDER REVISION")
    else:
        print(" VERDICT: FAILING - REJECT STRATEGY")

if __name__ == "__main__":
    run_backtest()
