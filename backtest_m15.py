#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
M15 Bollinger Trend Reversal - RAW OLD BOT BACKTEST (BEFORE FIXES)
يختبر الكود القديم كما كان تماماً قبل أي تعديل:
- 14 زوجاً فقط (بدلاً من 22).
- بدون فلتر قوة اللمس (أي لمس أو اختراق ولو 0.0 نقطة يدخل صفقة).
- مدة التجميد 45 دقيقة + 15 دقيقة مدة الصفقة (60 دقيقة إجمالاً).
- تطبيق الحظر الليلي (00:00 - 09:00 بتوقيت غزة) وعطلة نهاية الأسبوع كما في الكود الخام.
"""
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import timedelta, timezone

# القائمة القديمة الناقصة (14 زوجاً فقط كما كانت في الكود الخام)
TRADING_SYMBOLS = [
    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X",
    "USDCAD=X", "NZDUSD=X", "EURGBP=X", "EURCHF=X",
    "GBPJPY=X", "AUDJPY=X", "CADJPY=X", "CHFJPY=X",
    "EURAUD=X", "USDCHF=X"
]

BB_PERIOD = 15
BB_STD_DEV = 2.3
EMA_TREND_PERIOD = 200

BAN_START_HOUR_GAZA = 0
BAN_END_HOUR_GAZA = 9
PALESTINE_UTC_OFFSET_HOURS = 3

FREEZE_DURATION_MINUTES = 45
TRADE_DURATION_MINUTES = 15
TRADE_DURATION_BARS = 1  # شمعة واحدة (15 دقيقة)


def run_raw_old_backtest():
    print("=" * 65)
    print("STARTING BACKTEST FOR OLD UNFIXED BOT (14 PAIRS | NO FILTER | 45m FREEZE)")
    print("=" * 65)

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

            # نفس حسابات الكود الخام القديم حرفياً
            c = df['Close']
            sma = c.rolling(window=BB_PERIOD).mean()
            std = c.rolling(window=BB_PERIOD).std(ddof=0)
            df['BB_Upper'] = sma + (BB_STD_DEV * std)
            df['BB_Lower'] = sma - (BB_STD_DEV * std)
            df['EMA_200'] = c.ewm(span=EMA_TREND_PERIOD, adjust=False).mean()
            df = df.dropna()

            pip_mult = 100.0 if "JPY" in symbol else 10000.0
            freeze_until = None

            for i in range(1, len(df) - TRADE_DURATION_BARS):
                curr = df.iloc[i]
                candle_time = df.index[i]

                # 1. فحص عطلة نهاية الأسبوع كما في الكود الخام (now_utc.weekday() >= 5)
                if candle_time.weekday() >= 5:
                    continue

                # 2. فحص نافذة الحظر الليلي كما في الكود الخام (+3 ساعات إزاحة ثابتة)
                gaza_time = candle_time + timedelta(hours=PALESTINE_UTC_OFFSET_HOURS)
                if BAN_START_HOUR_GAZA <= gaza_time.hour < BAN_END_HOUR_GAZA:
                    continue

                # 3. فحص تجميد الزوج (Freeze) كما في الكود الخام
                if freeze_until is not None and candle_time < freeze_until:
                    continue

                price = float(curr['Close'])
                ema_200 = float(curr['EMA_200'])
                bb_upper = float(curr['BB_Upper'])
                bb_lower = float(curr['BB_Lower'])

                # 4. شروط الدخول الخام (بدون أي فلتر لقوة اللمس!)
                signal_type = None
                if price > ema_200 and price <= bb_lower:
                    signal_type = "CALL"
                elif price < ema_200 and price >= bb_upper:
                    signal_type = "PUT"

                if signal_type is not None:
                    entry_price = price
                    exit_bar = df.iloc[i + TRADE_DURATION_BARS]
                    exit_price = float(exit_bar['Close'])

                    diff = exit_price - entry_price
                    pips_diff = abs(diff) * pip_mult

                    if pips_diff < 0.1:
                        is_win = None
                    else:
                        is_win = (diff > 0) if signal_type == "CALL" else (diff < 0)

                    all_trades.append({
                        "symbol": symbol,
                        "type": signal_type,
                        "entry_time": str(candle_time),
                        "entry_price": entry_price,
                        "exit_price": exit_price,
                        "win": is_win,
                        "pips": round(pips_diff, 2)
                    })

                    # تجميد الزوج بنفس معادلة الكود القديم: base_time + 15 + 45 = 60 دقيقة
                    freeze_until = candle_time + timedelta(
                        minutes=TRADE_DURATION_MINUTES + FREEZE_DURATION_MINUTES
                    )

        except Exception as e:
            print(f"Error backtesting {symbol}: {e}")

    if not all_trades:
        print("No trades generated during backtest period.")
        return

    df_res = pd.DataFrame(all_trades)
    total_trades = len(df_res)
    wins = int((df_res['win'] == True).sum())
    losses = int((df_res['win'] == False).sum())
    voids = int(df_res['win'].isna().sum())

    df_res['signed_pips'] = np.where(
        df_res['win'] == True, df_res['pips'],
        np.where(df_res['win'] == False, -df_res['pips'], 0.0)
    )
    net_pips = float(df_res['signed_pips'].sum())
    win_rate = (wins / (wins + losses) * 100.0) if (wins + losses) > 0 else 0.0

    print("\n" + "=" * 65)
    print("📊 BACKTEST RESULTS SUMMARY (OLD UNFIXED BOT)")
    print("=" * 65)
    print(f"📈 Total Trades   : {total_trades} (⚪ Flat/Void: {voids})")
    print(f"✅ Winning Trades : {wins}")
    print(f"❌ Losing Trades  : {losses}")
    print(f"🏆 Win Rate       : {win_rate:.2f}%")
    print(f"💰 Net Pips       : {net_pips:+.2f} Pips")
    print("=" * 65)


if __name__ == "__main__":
    run_raw_old_backtest()
