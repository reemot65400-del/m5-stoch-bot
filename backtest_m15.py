#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
M15 Bollinger Trend Reversal - Backtest Engine
يقوم باختبار الاستراتيجية تاريخياً على الأزواج الـ 22 وحساب نسبة الفوز وصافي النقاط.
"""
import os
import sys
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timezone

TRADING_SYMBOLS = [
    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X", "NZDUSD=X",
    "USDCAD=X", "USDCHF=X", "EURGBP=X", "EURAUD=X", "EURCAD=X", 
    "EURCHF=X", "EURNZD=X", "GBPAUD=X", "GBPCAD=X", "GBPCHF=X", 
    "GBPNZD=X", "AUDCAD=X", "AUDCHF=X", "AUDNZD=X", "CADCHF=X", 
    "CADJPY=X", "CHFJPY=X"
]

BB_PERIOD = 15
BB_STD_DEV = 2.3
EMA_TREND_PERIOD = 200
MIN_DISTANCE_PIPS = 1.0
TRADE_DURATION_BARS = 1  # شمعة واحدة (15 دقيقة)

def run_backtest():
    print("=" * 60)
    print("STARTING M15 BOLLINGER TREND REVERSAL BACKTEST...")
    print("=" * 60)

    all_trades = []

    for symbol in TRADING_SYMBOLS:
        try:
            print(f"Fetching historical data for {symbol}...")
            ticker = yf.Ticker(symbol)
            df = ticker.history(period="60d", interval="15m", auto_adjust=False, actions=False)
            
            if df is None or len(df) < EMA_TREND_PERIOD + 20:
                print(f"Skipping {symbol}: Insufficient data.")
                continue

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [str(c[0]) for c in df.columns]
            df.index = pd.to_datetime(df.index, utc=True)
            df = df[~df.index.duplicated()].sort_index().dropna()

            # حساب مؤشرات البولينجر و EMA200
            c = df['Close']
            sma = c.rolling(window=BB_PERIOD).mean()
            std = c.rolling(window=BB_PERIOD).std(ddof=0)
            df['BB_Upper'] = sma + (BB_STD_DEV * std)
            df['BB_Lower'] = sma - (BB_STD_DEV * std)
            df['EMA_200'] = c.ewm(span=EMA_TREND_PERIOD, adjust=False).mean()
            df.dropna(inplace=True)

            pip_mult = 100.0 if "JPY" in symbol else 10000.0

            # محاكاة الصفقات شمعة بشمعة
            for i in range(1, len(df) - TRADE_DURATION_BARS):
                prev = df.iloc[i - 1]
                curr = df.iloc[i]

                price = float(curr['Close'])
                ema_200 = float(curr['EMA_200'])
                bb_upper = float(curr['BB_Upper'])
                bb_lower = float(curr['BB_Lower'])

                signal_type = None
                distance_pips = 0.0

                # فحص شروط الدخول مع فلتر قوة اللمس
                if price > ema_200 and price <= bb_lower:
                    distance_pips = (bb_lower - price) * pip_mult
                    if distance_pips >= MIN_DISTANCE_PIPS:
                        signal_type = "CALL"
                elif price < ema_200 and price >= bb_upper:
                    distance_pips = (price - bb_upper) * pip_mult
                    if distance_pips >= MIN_DISTANCE_PIPS:
                        signal_type = "PUT"

                if signal_type is not None:
                    entry_price = float(curr['Close'])
                    # الخروج عند إغلاق الشمعة التالية
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
                        "entry_time": str(curr.name),
                        "entry_price": entry_price,
                        "exit_price": exit_price,
                        "win": is_win,
                        "pips": round(pips, 2)
                    })

        except Exception as e:
            print(f"Error backtesting {symbol}: {e}")

    # تحليل النتائج الإجمالية
    if not all_trades:
        print("No trades generated during backtest period.")
        return

    df_res = pd.DataFrame(all_trades)
    total_trades = len(df_res)
    wins = int((df_res['win'] == True).sum())
    losses = int((df_res['win'] == False).sum())
    
    df_res['signed_pips'] = np.where(df_res['win'] == True, df_res['pips'], np.where(df_res['win'] == False, -df_res['pips'], 0.0))
    net_pips = float(df_res['signed_pips'].sum())
    win_rate = (wins / (wins + losses) * 100.0) if (wins + losses) > 0 else 0.0

    print("\n" + "=" * 60)
    print("📊 BACKTEST RESULTS SUMMARY")
    print("=" * 60)
    print(f"📈 Total Trades: {total_trades}")
    print(f"✅ Winning Trades: {wins}")
    print(f"❌ Losing Trades: {losses}")
    print(f"🏆 Win Rate: {win_rate:.2f}%")
    print(f"💰 Net Pips: {net_pips:+.2f} Pips")
    print("=" * 60)

if __name__ == "__main__":
    run_backtest()
