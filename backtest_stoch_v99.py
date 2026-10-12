import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time

# ═══════════════════════════════════════════════
# 1. إعدادات مختبر Stochastic v9.9
# ═══════════════════════════════════════════════
PAIRS = [
    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X", 
    "USDCAD=X", "NZDUSD=X", "EURCHF=X", "GBPJPY=X"    
]

END_DATE = datetime.now().strftime("%Y-%m-%d")
START_DATE = (datetime.now() - timedelta(days=50)).strftime("%Y-%m-%d")

# معاملات استراتيجية الستوكاستيك (نفس البوت الحي)
K_PERIOD = 9
D_PERIOD = 3
OVERBOUGHT_ZONE = 85
OVERSOLD_ZONE = 15
EMA_PERIOD = 200

TRADE_DURATION_CANDLES = 1 # مدة الصفقة 5 دقائق (شمعة واحدة M5)
SPREAD_PIPS = 1.5 
INITIAL_BALANCE = 10000
BET_SIZE = 100 

RESULT_FILE = "results_stoch_v99.txt"

def log(msg):
    print(msg)
    with open(RESULT_FILE, "a", encoding="utf-8") as f:
        f.write(msg + "\n")

log(f"🧪 Starting Stochastic v9.9 Backtest: {START_DATE} to {END_DATE}")

# ═══════════════════════════════════════════════
# 2. دوال حساب المؤشرات (Pandas Only)
# ═══════════════════════════════════════════════

def calculate_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def calculate_stochastic(df, k_period, d_period):
    low_min = df['Low'].rolling(window=k_period).min()
    high_max = df['High'].rolling(window=k_period).max()
    denom = high_max - low_min
    denom = denom.replace(0, np.nan)
    
    raw_k = 100 * ((df['Close'] - low_min) / denom)
    df['STOCH_K_RAW'] = raw_k.ffill().fillna(50)
    df['STOCH_K'] = df['STOCH_K_RAW'].rolling(window=d_period).mean().ffill()
    df['STOCH_D'] = df['STOCH_K'].rolling(window=d_period).mean().ffill()
    return df

# ═══════════════════════════════════════════════
# 3. محرك الاختبار (Stochastic Logic)
# ═══════════════════════════════════════════════

def run_backtest_for_pair(symbol):
    log(f"\n Testing {symbol} (Stoch v9.9)...")
    
    try:
        df_m5 = yf.download(symbol, start=START_DATE, end=END_DATE, interval="5m", progress=False, auto_adjust=False)
        
        if df_m5.empty:
            log(f"   ⚠️ No data found for {symbol}")
            return []

        if isinstance(df_m5.columns, pd.MultiIndex): df_m5.columns = df_m5.columns.get_level_values(0)

        # حساب المؤشرات
        df_m5['EMA_200'] = calculate_ema(df_m5['Close'], EMA_PERIOD)
        df_m5 = calculate_stochastic(df_m5, K_PERIOD, D_PERIOD)
        
        trades = []
        valid_df = df_m5.dropna()
        
        # نبدأ من الشمعة 250 لضمان جاهزية EMA 200 والستوكاستيك
        start_index = max(250, len(valid_df) - TRADE_DURATION_CANDLES - 1)
        if start_index < 0: start_index = 0
        
        # فحص كل شمعة (بدون step لتجنب تفويت إشارات الستوكاستيك السريعة)
        for i in range(start_index, len(valid_df) - TRADE_DURATION_CANDLES):
            curr = valid_df.iloc[i]
            prev = valid_df.iloc[i-1]
            curr_time = valid_df.index[i]
            
            price = float(curr['Close'])
            ema = float(curr['EMA_200'])
            k_now, d_now = float(curr['STOCH_K']), float(curr['STOCH_D'])
            k_pre, d_pre = float(prev['STOCH_K']), float(prev['STOCH_D'])
            
            signal = None
            
            # ★★ منطق الشراء (CALL) - مطابق للبوت الحي ★★
            if price > ema and k_pre <= d_pre and k_now > d_now and min(k_pre, k_now) < OVERSOLD_ZONE:
                signal = "CALL"
                
            # ★★ منطق البيع (PUT) - مطابق للبوت الحي ★★
            elif price < ema and k_pre >= d_pre and k_now < d_now and max(k_pre, k_now) > OVERBOUGHT_ZONE:
                signal = "PUT"
            
            if signal:
                entry_price = curr['Close']
                exit_candle = valid_df.iloc[i + TRADE_DURATION_CANDLES]
                exit_price = exit_candle['Close']
                
                win = False
                if signal == "CALL" and exit_price > entry_price: win = True
                if signal == "PUT" and exit_price < entry_price: win = True
                
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
# 4. التقرير النهائي
# ═══════════════════════════════════════════════

def main():
    all_trades = []
    
    for pair in PAIRS:
        trades = run_backtest_for_pair(pair)
        all_trades.extend(trades)
        time.sleep(2) 
    
    if not all_trades:
        log("\n❌ NO TRADES GENERATED.")
        return

    df_results = pd.DataFrame(all_trades)
    total_trades = len(df_results)
    wins = df_results[df_results['win'] == True].shape[0]
    losses = total_trades - wins
    win_rate = (wins / total_trades) * 100
    
    total_pips = df_results['pips'].sum()
    
    profit_money = 0
    for _, row in df_results.iterrows():
        if row['win']:
            profit_money += (BET_SIZE * 0.85)
        else:
            profit_money -= BET_SIZE
            
    final_balance = INITIAL_BALANCE + profit_money
    roi = ((final_balance - INITIAL_BALANCE) / INITIAL_BALANCE) * 100

    log("\n" + "="*60)
    log("🏁 STOCHASTIC v9.9 BACKTEST RESULTS (LAST 50 DAYS)")
    log("="*60)
    log(f"📊 Total Trades Found: {total_trades}")
    log(f"✅ Wins: {wins} | ❌ Losses: {losses}")
    log(f"🏆 WIN RATE: {win_rate:.2f}%")
    log(f" Total Net Pips: {total_pips:.2f}")
    log(f"💰 Financial Simulation (Payout 85%):")
    log(f"   Start Balance: ${INITIAL_BALANCE}")
    log(f"   End Balance:   ${final_balance:.2f}")
    log(f"   ROI:           {roi:.2f}%")
    log("="*60)
    
    if win_rate > 56 and roi > 0:
        log("🟢 VERDICT: STRATEGY IS PROFITABLE! Keep using it live.")
    elif win_rate > 50:
        log("🟡 VERDICT: BREAK-EVEN ZONE. Needs minor tweaks.")
    else:
        log("🔴 VERDICT: LOSING. Consider adjusting zones or duration.")

if __name__ == "__main__":
    main()
