import yfinance as yf
import pandas as pd
import numpy as np
import sys

# ═══════════════════════════════════════════════
# إعدادات الاستراتيجية والاختبار
# ═══════════════════════════════════════════════
SYMBOLS = [
    "BTC-USD", "ETH-USD", "BNB-USD", "SOL-USD", 
    "ADA-USD", "XRP-USD", "DOT-USD", "LINK-USD", 
    "AVAX-USD", "LTC-USD", "UNI-USD", "ATOM-USD", 
    "FIL-USD", "MATIC-USD"
]

def calculate_indicators(df):
    """حساب المؤشرات الفنية باستخدام Wilder's Smoothing للـ RSI"""
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
    """كشف نماذج الشموع (Engulfing/Hammer logic)"""
    body_curr = abs(row['Close'] - row['Open'])
    range_curr = row['High'] - row['Low']
    if range_curr == 0: return False
    
    # شرط حجم الجسم بالنسبة للمدى
    strong_body = body_curr > (range_curr * 0.6)
    
    if direction == 'CALL':
        return (row['Close'] > row['Open']) and strong_body
    else:
        return (row['Close'] < row['Open']) and strong_body

def run_backtest():
    all_trades = []
    
    print(f"🚀 Starting M5 Sniper Backtest on {len(SYMBOLS)} Assets...")
    
    for symbol in SYMBOLS:
        try:
            # جلب بيانات 60 يوم (أقصى حد لـ yfinance للفريم الصغير)
            df = yf.download(symbol, period="60d", interval="5m", progress=False)
            if df.empty or len(df) < 250: continue
            
            # تنظيف الأعمدة في حال كانت MultiIndex
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
                
            df = calculate_indicators(df).dropna()
            
            wins, losses, ties = 0, 0, 0
            
            for i in range(250, len(df) - 5):
                curr = df.iloc[i]
                prev = df.iloc[i-1]
                
                # فلاتر الاتجاه والزخم والحجم
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
                    exit_p = df.iloc[i+5]['Close'] # إغلاق بعد 25 دقيقة
                    
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
                    'WR': round((wins/total)*100, 2)
                })
                
        except Exception as e:
            print(f"⚠️ Error on {symbol}: {e}")

    # طباعة التقرير النهائي
    if not all_trades:
        print("❌ No trades generated. Strategy too strict or data issue.")
        return

    df_res = pd.DataFrame(all_trades)
    total_w = df_res['Wins'].sum()
    total_t = df_res['Trades'].sum()
    overall_wr = (total_w / total_t) * 100
    
    print("\n" + "="*60)
    print("📊 M5 SNIPER BACKTEST REPORT (60 Days Intensive)")
    print("="*60)
    print(df_res.to_string(index=False))
    print("-"*60)
    print(f"🏆 TOTAL TRADES: {total_t}")
    print(f"✅ OVERALL WIN RATE: {overall_wr:.2f}%")
    
    if overall_wr >= 77:
        print("🔥 VERDICT: EXCELLENT (Target Achieved!)")
    elif overall_wr >= 55:
        print("⚖️ VERDICT: MARGINAL (Needs Optimization)")
    else:
        print("💀 VERDICT: FAILING (Strategy Rejected)")

if __name__ == "__main__":
    run_backtest()
