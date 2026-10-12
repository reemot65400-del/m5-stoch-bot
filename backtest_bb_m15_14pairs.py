import numpy as np
import pandas as pd
import yfinance as yf
import warnings
import time
warnings.filterwarnings('ignore')

# ==========================================
# CONFIGURATION: M15 BOLLINGER TREND REVERSAL (14 PAIRS)
# ==========================================
SYMBOLS = [
    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "USDCHF=X", 
    "AUDUSD=X", "USDCAD=X", "NZDUSD=X", "EURGBP=X",
    "EURJPY=X", "GBPJPY=X", "AUDJPY=X", "CADJPY=X",
    "CHFJPY=X", "NZDJPY=X"
]
TIMEFRAME = "15m"
PERIOD_LIMIT = "60d" 
BREAK_EVEN_WR = 0.5263 

# Strategy Parameters
BB_PERIOD = 15
BB_STD_DEV = 2.3
EMA_TREND_PERIOD = 200

RESULT_FILE = "results_bb_m15_14pairs.txt"

def log(msg):
    print(msg)
    with open(RESULT_FILE, "a", encoding="utf-8") as f:
        f.write(msg + "\n")

def flatten_columns(df):
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] for col in df.columns]
    return df

def get_data(symbol, period=PERIOD_LIMIT, interval=TIMEFRAME):
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval=interval)
        if df.empty: return None
        df = flatten_columns(df)
        required_cols = ['Open', 'High', 'Low', 'Close']
        if not all(col in df.columns for col in required_cols): return None
        df = df.dropna(subset=['Close'])
        df.sort_index(inplace=True)
        df = df[~df.index.duplicated(keep='first')]
        return df[['Open', 'High', 'Low', 'Close']]
    except Exception as e:
        log(f"Error fetching {symbol}: {e}")
        return None

def calculate_indicators(df):
    """Calculates BB(15, 2.3) and EMA(200) with Population Std Dev."""
    c = df['Close']
    
    # 1. Bollinger Bands (Population Std Dev ddof=0 is critical)
    sma = c.rolling(window=BB_PERIOD).mean()
    std = c.rolling(window=BB_PERIOD).std(ddof=0) 
    upper_band = sma + (BB_STD_DEV * std)
    lower_band = sma - (BB_STD_DEV * std)
    
    # 2. Exponential Moving Average (Trend Filter)
    ema_200 = c.ewm(span=EMA_TREND_PERIOD, adjust=False).mean()
    
    return df.assign(
        BB_Upper=upper_band,
        BB_Lower=lower_band,
        EMA_200=ema_200
    )

def run_backtest(df):
    """Executes the strict entry rules and calculates Win Rate/Z-Score."""
    trades = []
    n = len(df)
    
    # Start after indicators are stable
    start_idx = max(BB_PERIOD, EMA_TREND_PERIOD) + 1
    
    for i in range(start_idx, n - 1):
        curr_close = df['Close'].iloc[i]
        next_close = df['Close'].iloc[i+1]
        
        # Skip ties
        if curr_close == next_close:
            continue
            
        bb_upper = df['BB_Upper'].iloc[i]
        bb_lower = df['BB_Lower'].iloc[i]
        ema_200 = df['EMA_200'].iloc[i]
        
        direction = 0 # 0=None, 1=CALL, -1=PUT
        
        # --- BUY RULE (CALL) ---
        if curr_close > ema_200 and curr_close <= bb_lower:
            direction = 1
            
        # --- SELL RULE (PUT) ---
        elif curr_close < ema_200 and curr_close >= bb_upper:
            direction = -1
            
        if direction != 0:
            win = False
            if direction == 1: # CALL
                win = next_close > curr_close
            else: # PUT
                win = next_close < curr_close
                
            trades.append(win)
            
    return trades

def analyze_results(all_trades):
    """Calculates statistical metrics."""
    total = len(all_trades)
    if total == 0:
        return {'count': 0, 'wr': 0, 'z_score': 0}
        
    wins = sum(all_trades)
    wr = wins / total
    
    # Z-Score Calculation against Break-even Probability
    p_null = BREAK_EVEN_WR
    std_dev = np.sqrt(total * p_null * (1 - p_null))
    z_score = (wins - (total * p_null)) / std_dev if std_dev > 0 else 0
    
    return {'count': total, 'wr': wr, 'z_score': z_score}

def main():
    log("="*60)
    log("VALIDATING: M15 BOLLINGER TREND REVERSAL (14 PAIRS)")
    log(f"Period: Last {PERIOD_LIMIT} | TF: {TIMEFRAME}")
    log("="*60)
    
    global_trades = []
    
    for sym in SYMBOLS:
        log(f"Processing {sym}...") 
        
        df = get_data(sym)
        if df is not None and len(df) > 250:
            enriched_df = calculate_indicators(df)
            trades = run_backtest(enriched_df)
            global_trades.extend(trades)
            log(f"   -> Done ({len(trades)} signals)")
        else:
            log("   -> Skipped (Insufficient Data)")
        time.sleep(1.5) # زيادة التأخير قليلاً لتجنب الحظر بسبب كثرة الطلبات
            
    stats = analyze_results(global_trades)
    
    log("\n" + "-"*40)
    log("FINAL STATISTICAL REPORT")
    log("-"*40)
    log(f"Total Signals Generated: {stats['count']}")
    log(f"Overall Win Rate:        {stats['wr']*100:.2f}%")
    log(f"Z-Score (Significance):  {stats['z_score']:.2f}")
    
    verdict = "FAILING"
    if stats['count'] >= 100: 
        if stats['wr'] >= 0.58 and stats['z_score'] > 2.0:
            verdict = "EXCELLENT 🏆"
        elif stats['wr'] >= BREAK_EVEN_WR and stats['z_score'] > 1.0:
            verdict = "VIABLE ⚠️"
        else:
            verdict = "FRAGILE ❌"
    else:
        verdict = "INSUFFICIENT DATA (<100 trades)"
            
    log(f"Verdict:                 {verdict}")
    log("="*60)

if __name__ == "__main__":
    main()
