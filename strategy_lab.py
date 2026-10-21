import yfinance as yf
import pandas as pd
import ta
import warnings
import time
warnings.filterwarnings('ignore')

class YahooForexLab:
    def __init__(self, symbols, interval='15m', period='60d'):
        self.symbols = symbols
        self.interval = interval
        self.period = period
        self.all_results = []

    def fetch_and_prepare(self, symbol):
        # تجربة صيغ متعددة للرمز
        ticker_formats = [
            symbol,
            symbol.replace('=X', ''),
            symbol.replace('=X', '=X')
        ]
        
        for ticker in ticker_formats:
            try:
                print(f"جاري تحميل {ticker}...")
                df = yf.download(
                    ticker, 
                    interval=self.interval, 
                    period=self.period, 
                    progress=False,
                    timeout=10
                )
                
                if df is not None and not df.empty and len(df) >= 60:
                    # تسطيح الأعمدة متعددة المؤشرات
                    if isinstance(df.columns, pd.MultiIndex):
                        df.columns = df.columns.get_level_values(0)
                    
                    df = df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
                    print(f"✓ تم تحميل {ticker} بنجاح - {len(df)} شمعة")
                    return df
                    
            except Exception as e:
                print(f"✗ فشل {ticker}: {e}")
                time.sleep(2)  # انتظار قبل المحاولة التالية
        
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
            
            # شروط CALL
            if (current['Close'] > current['EMA_50'] and 
                current['Low'] <= current['BB_Lower'] and 
                current['RSI'] < 30):
                
                win = 1 if next_candle['Close'] > current['Close'] else 0
                trades.append({'Type': 'CALL', 'Win': win})
                
            # شروط PUT
            elif (current['Close'] < current['EMA_50'] and 
                  current['High'] >= current['BB_Upper'] and 
                  current['RSI'] > 70):
                  
                win = 1 if next_candle['Close'] < current['Close'] else 0
                trades.append({'Type': 'PUT', 'Win': win})
                
        return pd.DataFrame(trades)

    def run_lab(self):
        print(f"بدء اختبار الاستراتيجيات على فريم {self.interval}...\n")
        
        for symbol in self.symbols:
            df = self.fetch_and_prepare(symbol)
            if df is None:
                print(f"⚠ تخطي {symbol} - لا توجد بيانات")
                continue
                
            df = self.apply_indicators(df)
            trades_df = self.backtest_strategy(df)
            
            if trades_df.empty:
                continue
                
            total = len(trades_df)
            wins = trades_df['Win'].sum()
            win_rate = (wins / total * 100) if total > 0 else 0
            
            self.all_results.append({
                'Symbol': symbol,
                'Total Trades': total,
                'Wins': int(wins),
                'Losses': int(total - wins),
                'Win Rate (%)': round(win_rate, 2)
            })
            
        return pd.DataFrame(self.all_results)

if __name__ == "__main__":
    # أزواج العملات بصيغ بديلة
    forex_pairs = [
        "EURUSD=X", "GBPUSD=X", "USDJPY=X", "USDCHF=X", 
        "AUDUSD=X", "USDCAD=X", "NZDUSD=X", "EURJPY=X", "GBPJPY=X"
    ]
    
    lab = YahooForexLab(symbols=forex_pairs, interval='15m', period='60d')
    results_df = lab.run_lab()
    
    if not results_df.empty:
        results_df = results_df.sort_values(by='Win Rate (%)', ascending=False)
        print("\n=== نتائج مختبر الاستراتيجيات ===")
        print(results_df.to_string(index=False))
        
        avg_win_rate = results_df['Win Rate (%)'].mean()
        print(f"\nمتوسط نسبة النجاح: {round(avg_win_rate, 2)}%")
    else:
        print("\n⚠ لم يتم العثور على بيانات صالحة. جرب فريم 1h أو period=30d")
