import yfinance as yf
import pandas as pd
import ta
import warnings
import time
import requests
warnings.filterwarnings('ignore')

# إعداد session مع headers يحاكي المتصفح (يحل مشكلة JSONDecodeError)
session = requests.Session()
session.headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
yf.set_tz_cache_location('/tmp/yf_cache')

class YahooForexLab:
    def __init__(self, symbols, interval='15m', period='60d'):
        self.symbols = symbols
        self.interval = interval
        self.period = period
        self.all_results = []

    def fetch_and_prepare(self, symbol, max_retries=3):
        for attempt in range(max_retries):
            try:
                ticker = yf.Ticker(symbol, session=session)
                df = ticker.history(interval=self.interval, period=self.period)
                
                if df is not None and not df.empty and len(df) >= 60:
                    df = df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
                    print(f"  ✓ {symbol}: {len(df)} شمعة")
                    return df
                    
            except Exception as e:
                print(f"  ✗ محاولة {attempt+1} لـ {symbol}: {e}")
                time.sleep(3)
        
        # Fallback: تجربة فريم 1h إذا فشل 15m
        print(f"  ⚠ تجربة فريم 1h لـ {symbol}")
        try:
            ticker = yf.Ticker(symbol, session=session)
            df = ticker.history(interval='1h', period=self.period)
            if df is not None and not df.empty and len(df) >= 60:
                df = df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
                print(f"  ✓ {symbol} (1h): {len(df)} شمعة")
                return df
        except:
            pass
        
        return None

    def apply_indicators(self, df):
        df['EMA_50'] = ta.trend.ema_indicator(df['Close'], window=50)
        bb = ta.volatility.BollingerBands(df['Close'], window=20, window_dev=2)
        df['BB_Upper'] = bb.bollinger_hband()
        df['BB_Lower'] = bb.bollinger_lband()
        df['RSI'] = ta.momentum.rsi(df['Close'], window=14)
        return df.dropna()

    def backtest_strategy(self, df):
        trades = []
        for i in range(1, len(df) - 1):
            current = df.iloc[i]
            next_candle = df.iloc[i + 1]
            if (current['Close'] > current['EMA_50'] and 
                current['Low'] <= current['BB_Lower'] and 
                current['RSI'] < 30):
                win = 1 if next_candle['Close'] > current['Close'] else 0
                trades.append({'Type': 'CALL', 'Win': win})
            elif (current['Close'] < current['EMA_50'] and 
                  current['High'] >= current['BB_Upper'] and 
                  current['RSI'] > 70):
                win = 1 if next_candle['Close'] < current['Close'] else 0
                trades.append({'Type': 'PUT', 'Win': win})
        return pd.DataFrame(trades)

    def run_lab(self):
        print(f"=== مختبر الاستراتيجيات - فريم {self.interval} ===\n")
        for symbol in self.symbols:
            print(f"جاري معالجة {symbol}...")
            df = self.fetch_and_prepare(symbol)
            if df is None:
                print(f"  ⚠ تخطي {symbol}\n")
                continue
            df = self.apply_indicators(df)
            trades_df = self.backtest_strategy(df)
            if trades_df.empty:
                print(f"  لا صفقات لـ {symbol}\n")
                continue
            total = len(trades_df)
            wins = int(trades_df['Win'].sum())
            win_rate = (wins / total * 100)
            self.all_results.append({
                'Symbol': symbol, 'Total Trades': total,
                'Wins': wins, 'Losses': total - wins,
                'Win Rate (%)': round(win_rate, 2)
            })
            print(f"  → {total} صفقة، نسبة نجاح: {round(win_rate,2)}%\n")
            time.sleep(2)
        return pd.DataFrame(self.all_results)

if __name__ == "__main__":
    pairs = [
        "EURUSD=X", "GBPUSD=X", "USDJPY=X", "USDCHF=X", 
        "AUDUSD=X", "USDCAD=X", "NZDUSD=X", "EURJPY=X", "GBPJPY=X"
    ]
    lab = YahooForexLab(symbols=pairs, interval='15m', period='60d')
    results = lab.run_lab()
    if not results.empty:
        results = results.sort_values('Win Rate (%)', ascending=False)
        print("\n=== النتائج النهائية ===")
        print(results.to_string(index=False))
        print(f"\nمتوسط نسبة النجاح: {round(results['Win Rate (%)'].mean(), 2)}%")
    else:
        print("\n⚠ لا نتائج. تأكد من اتصال GitHub Actions بالإنترنت.")
