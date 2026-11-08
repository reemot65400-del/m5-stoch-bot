#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
M15 EMA 21/50 Pullback Rejection Strategy - Backtest
استراتيجية استمرار الاتجاه مع شمعة الرفض والذيل الطويل.
"""
import pandas as pd
import numpy as np
import yfinance as yf

# القائمة الكاملة (22 زوجاً)
TRADING_SYMBOLS = [
    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X", "NZDUSD=X",
    "USDCAD=X", "USDCHF=X", "EURGBP=X", "EURAUD=X", "EURCAD=X", 
    "EURCHF=X", "EURNZD=X", "GBPAUD=X", "GBPCAD=X", "GBPCHF=X", 
    "GBPNZD=X", "AUDCAD=X", "AUDCHF=X", "AUDNZD=X", "CADCHF=X", 
    "CADJPY=X", "CHFJPY=X"
]

TRADE_DURATION_BARS = 1  # 15 دقيقة (شمعة واحدة)

def get_atr(df, period=14):
    high_low = df['High'] - df['Low']
    high_close = np.abs(df['High'] - df['Close'].shift())
    low_close = np.abs(df['Low'] - df['Close'].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = np.max(ranges, axis=1)
    return true_range.rolling(period).mean()

def run_backtest():
    print("=" * 65)
    print("STARTING M15 EMA 21/50 PULLBACK REJECTION BACKTEST...")
    print("=" * 65)

    all_trades = []

    for symbol in TRADING_SYMBOLS:
        try:
            print(f"Fetching historical data for {symbol}...")
            ticker = yf.Ticker(symbol)
            df = ticker.history(period="60d", interval="15m", auto_adjust=False, actions=False)
            
            if df is None or len(df) < 100:
                continue

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [str(c[0]) for c in df.columns]
            df.index = pd.to_datetime(df.index, utc=True)
            df = df[~df.index.duplicated()].sort_index()

            # حساب المؤشرات الجديدة
            df['EMA_21'] = df['Close'].ewm(span=21, adjust=False).mean()
            df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
            df['ATR'] = get_atr(df, 14)
            df = df.dropna()

            pip_mult = 100.0 if "JPY" in symbol else 10000.0

            for i in range(3, len(df) - TRADE_DURATION_BARS):
                curr = df.iloc[i]
                
                price = float(curr['Close'])
                ema_21 = float(curr['EMA_21'])
                ema_50 = float(curr['EMA_50'])
                ema_50_prev = float(df.iloc[i-3]['EMA_50']) # لقياس ميل الخط
                atr = float(curr['ATR'])

                c_open = float(curr['Open'])
                c_close = float(curr['Close'])
                c_high = float(curr['High'])
                c_low = float(curr['Low'])

                # تفصيل الشمعة
                body_size = abs(c_close - c_open)
                total_size = c_high - c_low
                upper_wick = c_high - max(c_open, c_close)
                lower_wick = min(c_open, c_close) - c_low

                # الفلاتر الأساسية
                is_abnormal_candle = total_size > (atr * 2.0) # شمعة كبيرة جداً (أخبار)
                slope_pips = abs(ema_50 - ema_50_prev) * pip_mult
                is_ema_flat = slope_pips < 1.0 # إذا كان الميل شبه معدوم خلال 3 شموع

                if is_abnormal_candle or is_ema_flat or body_size == 0:
                    continue

                signal_type = None

                # ─── شروط الشراء (CALL) ───
                # 1. الاتجاه صاعد (السعر > 50، و 21 > 50، وميل الـ 50 صاعد)
                if price > ema_50 and ema_21 > ema_50 and ema_50 > ema_50_prev:
                    # 2. الارتداد واللمس: اللو يلامس أو يخترق الـ 21 لأسفل
                    if c_low <= ema_21 + (0.5 / pip_mult):
                        # 3. شمعة الرفض: إغلاق فوق الـ 21، وذيل سفلي أطول من الجسم مرة ونصف
                        if c_close > ema_21 and lower_wick >= (body_size * 1.5):
                            signal_type = "CALL"

                # ─── شروط البيع (PUT) ───
                # 1. الاتجاه هابط (السعر < 50، و 21 < 50، وميل الـ 50 هابط)
                elif price < ema_50 and ema_21 < ema_50 and ema_50 < ema_50_prev:
                    # 2. الارتداد واللمس: الهاي يلامس أو يخترق الـ 21 لأعلى
                    if c_high >= ema_21 - (0.5 / pip_mult):
                        # 3. شمعة الرفض: إغلاق تحت الـ 21، وذيل علوي أطول من الجسم مرة ونصف
                        if c_close < ema_21 and upper_wick >= (body_size * 1.5):
                            signal_type = "PUT"

                if signal_type is not None:
                    entry_price = float(curr['Close'])
                    exit_bar = df.iloc[i + TRADE_DURATION_BARS]
                    exit_price = float(exit_bar['Close'])

                    diff = exit_price - entry_price
                    pips = abs(diff) * pip_mult

                    if pips < 0.1:
                        is_win = None
                    else:
                        is_win = (diff > 0) if signal_type == 'CALL' else (diff < 0)

                    all_trades.append({
                        "symbol": symbol,
                        "type": signal_type,
                        "entry_price": entry_price,
                        "exit_price": exit_price,
                        "win": is_win,
                        "pips": round(pips, 2)
                    })

        except Exception as e:
            print(f"Error backtesting {symbol}: {e}")

    # ─── تحليل النتائج ───
    if not all_trades:
        print("No trades matched the strict EMA Pullback criteria.")
        return

    df_res = pd.DataFrame(all_trades)
    total_trades = len(df_res)
    wins = int((df_res['win'] == True).sum())
    losses = int((df_res['win'] == False).sum())
    voids = total_trades - wins - losses
    
    df_res['signed_pips'] = np.where(df_res['win'] == True, df_res['pips'], np.where(df_res['win'] == False, -df_res['pips'], 0.0))
    net_pips = float(df_res['signed_pips'].sum())
    win_rate = (wins / (wins + losses) * 100.0) if (wins + losses) > 0 else 0.0

    print("\n" + "=" * 65)
    print("📊 EMA 21/50 PULLBACK REJECTION - BACKTEST RESULTS")
    print("=" * 65)
    print(f"📈 Total Trades   : {total_trades} (⚪ Flat: {voids})")
    print(f"✅ Winning Trades : {wins}")
    print(f"❌ Losing Trades  : {losses}")
    print(f"🏆 Win Rate       : {win_rate:.2f}%")
    print(f"💰 Net Pips       : {net_pips:+.2f} Pips")
    print("=" * 65)

if __name__ == "__main__":
    run_backtest()
