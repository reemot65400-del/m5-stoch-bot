import yfinance as yf
import pandas as pd
import numpy as np
import time

# ═══════════════════════════════════════════════
# إعدادات استراتيجية M5 Sniper - YAHOO STABLE FIX
# ═══════════════════════════════════════════════
# قائمة شاملة بأقوى العملات مع بدائلها الاحتياطية
ASSET_POOL = [
    ("BTC-USD", "Bitcoin"), ("ETH-USD", "Ethereum"), ("BNB-USD", "Binance Coin"),
    ("SOL-USD", "Solana"), ("XRP-USD", "Ripple"), ("ADA-USD", "Cardano"),
    ("DOGE-USD", "Dogecoin"), ("TRX-USD", "Tron"), ("DOT-USD", "Polkadot"),
    ("AVAX-USD", "Avalanche"), ("LINK-USD", "Chainlink"), ("MATIC-USD", "Polygon"),
    ("UNI-USD", "Uniswap"), ("LTC-USD", "Litecoin"), ("ATOM-USD", "Cosmos"),
    ("FIL-USD", "Filecoin"), ("ETC-USD", "Ethereum Classic"), ("XLM-USD", "Stellar"),
    ("ALGO-USD", "Algorand"), ("VET-USD", "VeChain")
]

def validate_and_get_symbols():
    """التحقق من توفر الرموز واستبدال المفقود منها"""
    valid_symbols = []
    print("🔍 Validating asset availability on Yahoo Finance...")
    
    for symbol, name in ASSET_POOL:
        try:
            # محاولة جلب شمعة واحدة للتحقق من وجود الرمز
            test_data = yf.download(symbol, period="1d", interval="1m", progress=False)
            if not test_data.empty:
                valid_symbols.append(symbol)
                print(f"  ✅ {symbol} ({name}) - Available")
            else:
                print(f"  ⚠️ {symbol} ({name}) - Not found, skipping")
        except Exception:
            print(f"  ❌ {symbol} ({name}) - Error, skipping")
            
    print(f"\n✅ Successfully validated {len(valid_symbols)} assets.")
    return valid_symbols[:20]  # أخذ أقوى 20 عملة متاحة فقط

def calculate_indicators(df):
    df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
    df['EMA_200'] = df['Close'].ewm(span=200, adjust=False).mean()
    
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    
    df['Vol_MA'] = df['Volume'].rolling(window=10).mean()
    return df

def detect_pattern(row, prev_row, direction):
    body_curr = abs(row['Close'] - row['Open'])
    range_curr = row['High'] - row['Low']
    if range_curr == 0: return False
    
    strong_body = body_curr > (range_curr * 0.6)
    
    if direction == 'CALL':
        return (row['Close'] > row['Open']) and strong_body
    else:
        return (row['Close'] < row['Open']) and strong_body

def run_backtest():
    symbols = validate_and_get_symbols()
    if not symbols:
        print("❌ No valid symbols found. Exiting.")
        return

    all_trades = []
    print(f"\n🚀 Starting Stable Backtest on {len(symbols)} Assets...")
    
    for symbol in symbols:
        try:
            # جلب بيانات 60 يوم (أقصى حد لـ Yahoo للفريم الصغير)
            df = yf.download(symbol, period="60d", interval="5m", progress=False)
            
            # تنظيف الأعمدة
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
                
            if df.empty or len(df) < 250: 
                print(f"⚠️ Insufficient data for {symbol}")
                continue
                
            df = calculate_indicators(df).dropna()
            
            wins, losses, ties = 0, 0, 0
            
            for i in range(250, len(df) - 5):
                curr = df.iloc[i]
                prev = df.iloc[i-1]
                
                trend_up = curr['Close'] > curr['EMA_50'] > curr['EMA_200']
                trend_down = curr['Close'] < curr['EMA_50'] < curr['EMA_200']
                vol_ok = curr['Volume'] > curr['Vol_MA']
                
                signal = None
                
                if trend_up and curr['RSI'] < 30 and prev['RSI'] >= 30 and vol_ok:
                    if detect_pattern(curr, prev, 'CALL'): signal = 'CALL'
                    
                elif trend_down and curr['RSI'] > 70 and prev['RSI'] <= 70 and vol_ok:
                    if detect_pattern(curr, prev, 'PUT'): signal = 'PUT'
                
                if signal:
                    entry = curr['Close']
                    exit_p = df.iloc[i+5]['Close']
                    
                    if signal == 'CALL': win = exit_p > entry
                    else: win = exit_p < entry
                    
                    if abs(exit_p - entry) < 0.00001: ties += 1
                    elif win: wins += 1
                    else: losses += 1
                    
            total = wins + losses
            if total > 0:
                all_trades.append({
                    'Symbol': symbol, 
                    'Trades': total, 
                    'Wins': wins, 
                    'WR %': round((wins/total)*100, 2)
                })
                
        except Exception as e:
            print(f"⚠️ Error on {symbol}: {str(e)[:60]}")

    if not all_trades:
        print("❌ No trades generated. Strategy too strict.")
        return

    df_res = pd.DataFrame(all_trades)
    total_w = df_res['Wins'].sum()
    total_t = df_res['Trades'].sum()
    overall_wr = (total_w / total_t) * 100 if total_t > 0 else 0
    
    print("\n" + "="*60)
    print(" FINAL STABLE BACKTEST REPORT")
    print("="*60)
    print(df_res.to_string(index=False))
    print("-"*60)
    print(f"🏆 TOTAL TRADES: {total_t}")
    print(f"✅ WIN RATE: {overall_wr:.2f}%")
    
    if overall_wr >= 77: print("🔥 VERDICT: EXCELLENT STRATEGY")
    elif overall_wr >= 55: print("️ VERDICT: MARGINAL - NEEDS TUNING")
    else: print("💀 VERDICT: FAILING - REJECT STRATEGY")

if __name__ == "__main__":
    run_backtest()
