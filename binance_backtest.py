import ccxt
import pandas as pd
import numpy as np
import time

# ═══════════════════════════════════════════════
# إعدادات استراتيجية M5 Sniper - Bybit Stable Fix
# ═══════════════════════════════════════════════
TOP_N_ASSETS = 20  # عدد آمن لتجنب الحظر المؤقت

def get_top_assets(exchange, n=20):
    """جلب أقوى العملات من Bybit (مفتوحة عالمياً)"""
    print(f"📡 Fetching top {n} assets from Bybit Global...")
    try:
        tickers = exchange.fetch_tickers()
        
        # تصفية أزواج USDT فقط
        usdt_pairs = {
            k: v for k, v in tickers.items() 
            if k.endswith('/USDT') and 'USDC' not in k
        }
        
        sorted_pairs = sorted(
            usdt_pairs.items(), 
            key=lambda x: x[1].get('quoteVolume', 0), 
            reverse=True
        )
        
        symbols = [pair[0] for pair in sorted_pairs[:n]]
        print(f"✅ Selected Top {len(symbols)} Assets: {symbols[:3]}...")
        return symbols
        
    except Exception as e:
        print(f"❌ Fetch failed: {e}")
        return []

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
    # استخدام Bybit كمصدر بيانات مستقر وعالمي
    exchange = ccxt.bybit({
        'enableRateLimit': True,
        'options': {'defaultType': 'spot'}
    })
    
    symbols = get_top_assets(exchange, TOP_N_ASSETS)
    if not symbols:
        print(" No symbols found. Exiting.")
        return

    all_trades = []
    print(f"\n🚀 Starting Backtest on {len(symbols)} Assets...")
    
    for symbol in symbols:
        try:
            # جلب بيانات 5 دقائق (آخر 500 شمعة)
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe='5m', limit=500)
            if not ohlcv or len(ohlcv) < 250: 
                continue
            
            df = pd.DataFrame(ohlcv, columns=['Timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
            df['Timestamp'] = pd.to_datetime(df['Timestamp'], unit='ms')
            df.set_index('Timestamp', inplace=True)
                
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
                
            time.sleep(0.1)
                
        except Exception as e:
            print(f"⚠️ Skip {symbol}: {str(e)[:60]}")

    if not all_trades:
        print(" No trades generated.")
        return

    df_res = pd.DataFrame(all_trades)
    total_w = df_res['Wins'].sum()
    total_t = df_res['Trades'].sum()
    overall_wr = (total_w / total_t) * 100 if total_t > 0 else 0
    
    print("\n" + "="*60)
    print("📊 BYBIT BACKTEST REPORT (STABLE GLOBAL DATA)")
    print("="*60)
    print(df_res.to_string(index=False))
    print("-"*60)
    print(f"🏆 TOTAL TRADES: {total_t}")
    print(f"✅ WIN RATE: {overall_wr:.2f}%")
    
    if overall_wr >= 77: print(" VERDICT: EXCELLENT STRATEGY")
    elif overall_wr >= 55: print("⚖️ VERDICT: MARGINAL")
    else: print("💀 VERDICT: FAILING")

if __name__ == "__main__":
    run_backtest()
