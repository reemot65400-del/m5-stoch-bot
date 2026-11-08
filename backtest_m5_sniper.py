#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
M15 Combined Backtest Engine (backtest_m15.py)
يستخرج منطق الكود الحي v5.2 ويختبر إضافة مؤشري EMA 50 و EMA 21 مع شروط شمعة الرفض.
"""
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import timedelta
from zoneinfo import ZoneInfo

try:
    HEBRON_TZ = ZoneInfo("Asia/Hebron")
except Exception:
    HEBRON_TZ = ZoneInfo("Asia/Gaza")

TRADING_SYMBOLS = [
    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X", "NZDUSD=X",
    "USDCAD=X", "USDCHF=X", "EURGBP=X", "EURAUD=X", "EURCAD=X",
    "EURCHF=X", "EURNZD=X", "GBPAUD=X", "GBPCAD=X", "GBPCHF=X",
    "GBPNZD=X", "AUDCAD=X", "AUDCHF=X", "AUDNZD=X", "CADCHF=X",
    "CADJPY=X", "CHFJPY=X"
]

# إعدادات الاستراتيجية الأصلية (v5.2)
BB_PERIOD = 15
BB_STD_DEV = 2.3
EMA_TREND_PERIOD = 200
MIN_DISTANCE_PIPS = 1.0

# إعدادات المؤشرين المضافين
EMA_FAST_PERIOD = 21
EMA_SLOW_PERIOD = 50
ATR_PERIOD = 14

BAN_START_HOUR_GAZA = 0
BAN_END_HOUR_GAZA = 9
FREEZE_DURATION_MINUTES = 15
TRADE_DURATION_MINUTES = 15


def prepare_data(symbol: str):
    ticker = yf.Ticker(symbol)
    df = ticker.history(period="60d", interval="15m", auto_adjust=False, actions=False)
    if df is None or len(df) < EMA_TREND_PERIOD + 20:
        return None

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [str(c[0]) for c in df.columns]
    df.index = pd.to_datetime(df.index, utc=True)
    df = df[~df.index.duplicated()].sort_index().dropna()

    c = df['Close']
    h = df['High']
    l = df['Low']

    # 1. مؤشرات الاستراتيجية الأصلية
    sma = c.rolling(window=BB_PERIOD).mean()
    std = c.rolling(window=BB_PERIOD).std(ddof=0)
    df['BB_Upper'] = sma + (BB_STD_DEV * std)
    df['BB_Lower'] = sma - (BB_STD_DEV * std)
    df['EMA_200'] = c.ewm(span=EMA_TREND_PERIOD, adjust=False).mean()

    # 2. المؤشران الجديدان المضافان (EMA 50 & EMA 21) + ATR لقياس حجم الشمعة الطبيعي
    df['EMA_21'] = c.ewm(span=EMA_FAST_PERIOD, adjust=False).mean()
    df['EMA_50'] = c.ewm(span=EMA_SLOW_PERIOD, adjust=False).mean()

    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    df['ATR_14'] = tr.rolling(window=ATR_PERIOD).mean()

    df = df.dropna()
    return df


def check_bb_signal(curr, pip_mult):
    """منطق الاستراتيجية الأصلية (BB + EMA200 + فلتر 1.0 نقطة)"""
    price = float(curr['Close'])
    ema_200 = float(curr['EMA_200'])
    bb_upper = float(curr['BB_Upper'])
    bb_lower = float(curr['BB_Lower'])

    if price > ema_200 and price <= bb_lower:
        if (bb_lower - price) * pip_mult >= MIN_DISTANCE_PIPS:
            return "CALL"
    elif price < ema_200 and price >= bb_upper:
        if (price - bb_upper) * pip_mult >= MIN_DISTANCE_PIPS:
            return "PUT"
    return None


def check_ema21_50_signal(df, i, pip_mult):
    """منطق المؤشرين المضافين (EMA 50 + EMA 21 + شمعة الرفض + فلاتر الأمان)"""
    curr = df.iloc[i]
    price = float(curr['Close'])
    open_px = float(curr['Open'])
    high_px = float(curr['High'])
    low_px = float(curr['Low'])

    ema_21 = float(curr['EMA_21'])
    ema_50 = float(curr['EMA_50'])
    ema_50_prev = float(df.iloc[i - 3]['EMA_50'])
    atr = float(curr['ATR_14'])

    candle_range = high_px - low_px
    if candle_range <= 0 or pd.isna(atr) or atr <= 0:
        return None

    # فلتر الأمان 1: تجنب الشمعة الكبيرة جداً بشكل غير طبيعي
    if candle_range > (atr * 2.2):
        return None

    # فلتر الأمان 2: تجنب السوق العرضي إذا كان EMA 50 مسطحاً (Flat)
    slope_pips = (ema_50 - ema_50_prev) * pip_mult
    if abs(slope_pips) < 0.8:
        return None

    body_size = abs(price - open_px)
    lower_wick = min(open_px, price) - low_px
    upper_wick = high_px - max(open_px, price)

    # مسافة الاقتراب أو اللمس من الخط الأصفر EMA 21 (بحد أقصى 1.5 نقطة)
    touch_tolerance = 1.5 / pip_mult

    # شروط الشراء (CALL):
    # السعر فوق EMA50، و EMA21 فوق EMA50، و EMA50 متجه للأعلى + لمس/اقتراب من EMA21 + ذيل سفلي طويل وإغلاق فوق EMA21
    if (price > ema_50 and ema_21 > ema_50 and slope_pips >= 0.8):
        touched_ema21 = (low_px <= ema_21 + touch_tolerance)
        long_lower_wick = (lower_wick >= body_size * 1.2) and (lower_wick >= candle_range * 0.35)
        closed_above_ema21 = (price > ema_21)
        if touched_ema21 and long_lower_wick and closed_above_ema21:
            return "CALL"

    # شروط البيع (PUT):
    # السعر تحت EMA50، و EMA21 تحت EMA50، و EMA50 متجه للأسفل + لمس/اقتراب من EMA21 + ذيل علوي طويل وإغلاق تحت EMA21
    if (price < ema_50 and ema_21 < ema_50 and slope_pips <= -0.8):
        touched_ema21 = (high_px >= ema_21 - touch_tolerance)
        long_upper_wick = (upper_wick >= body_size * 1.2) and (upper_wick >= candle_range * 0.35)
        closed_below_ema21 = (price < ema_21)
        if touched_ema21 and long_upper_wick and closed_below_ema21:
            return "PUT"

    return None


def simulate_mode(data_map, mode: str, title: str):
    all_trades = []

    for sym in TRADING_SYMBOLS:
        df = data_map.get(sym)
        if df is None or df.empty:
            continue

        pip_mult = 100.0 if "JPY" in sym else 10000.0
        freeze_until = None

        for i in range(3, len(df) - 1):
            curr = df.iloc[i]
            candle_time = df.index[i]

            # تطبيق أوقات عمل البوت الحي (استبعاد العطلة والحظر الليلي 00:00 - 09:00 غزة)
            if candle_time.weekday() >= 5:
                continue
            gaza_dt = candle_time.astimezone(HEBRON_TZ)
            if BAN_START_HOUR_GAZA <= gaza_dt.hour < BAN_END_HOUR_GAZA:
                continue

            # تطبيق نظام التجميد (15 دقيقة صفقة + 15 دقيقة تجميد)
            if freeze_until is not None and candle_time < freeze_until:
                continue

            sig_bb = check_bb_signal(curr, pip_mult)
            sig_ema = check_ema21_50_signal(df, i, pip_mult)

            signal_type = None
            if mode == "ORIGINAL_BB_ONLY":
                signal_type = sig_bb
            elif mode == "STRICT_AND":
                # يشترط اتفاق الاستراتيجيتين أو فلترة البولينجر عبر اتجاه EMA 21/50
                price = float(curr['Close'])
                ema_21 = float(curr['EMA_21'])
                ema_50 = float(curr['EMA_50'])
                ema_50_prev = float(df.iloc[i - 3]['EMA_50'])
                slope_pips = (ema_50 - ema_50_prev) * pip_mult
                if sig_bb == "CALL" and (ema_21 > ema_50 and slope_pips >= 0.8):
                    signal_type = "CALL"
                elif sig_bb == "PUT" and (ema_21 < ema_50 and slope_pips <= -0.8):
                    signal_type = "PUT"
            elif mode == "COMBINED_OR":
                # يعمل بكلا المحركين معاً (البولينجر الأصلي + ارتداد EMA 21/50)
                signal_type = sig_bb if sig_bb is not None else sig_ema
            elif mode == "NEW_EMA_ONLY":
                signal_type = sig_ema

            if signal_type is not None:
                entry_price = float(curr['Close'])
                exit_price = float(df.iloc[i + 1]['Close'])
                diff = exit_price - entry_price
                pips = abs(diff) * pip_mult

                if pips < 0.1:
                    is_win = None
                else:
                    is_win = (diff > 0) if signal_type == "CALL" else (diff < 0)

                all_trades.append({
                    "symbol": sym,
                    "type": signal_type,
                    "win": is_win,
                    "pips": round(pips, 2)
                })

                freeze_until = candle_time + timedelta(
                    minutes=TRADE_DURATION_MINUTES + FREEZE_DURATION_MINUTES
                )

    print("\n" + "=" * 68)
    print(f"📊 {title}")
    print("=" * 68)

    if not all_trades:
        print("📈 Total Trades   : 0 (No trades matched)")
        print("=" * 68)
        return

    df_res = pd.DataFrame(all_trades)
    total = len(df_res)
    wins = int((df_res['win'] == True).sum())
    losses = int((df_res['win'] == False).sum())
    voids = total - wins - losses
    wr = (wins / (wins + losses) * 100.0) if (wins + losses) > 0 else 0.0

    df_res['signed_pips'] = np.where(
        df_res['win'] == True, df_res['pips'],
        np.where(df_res['win'] == False, -df_res['pips'], 0.0)
    )
    net_pips = float(df_res['signed_pips'].sum())

    print(f"📈 Total Trades   : {total} (⚪ Flat: {voids})")
    print(f"✅ Winning Trades : {wins}")
    print(f"❌ Losing Trades  : {losses}")
    print(f"🏆 Win Rate       : {wr:.2f}%")
    print(f"💰 Net Pips       : {net_pips:+.2f} Pips")
    print("=" * 68)


def main():
    print("=" * 68)
    print("FETCHING 60D M15 DATA FOR 22 PAIRS (TESTING ADDED EMA 50 & EMA 21)...")
    print("=" * 68)

    data_map = {}
    for sym in TRADING_SYMBOLS:
        try:
            print(f"Loading {sym}...")
            df = prepare_data(sym)
            if df is not None:
                data_map[sym] = df
        except Exception as e:
            print(f"Error loading {sym}: {e}")

    # 1. الاستراتيجية الأصلية للبوت الشغال (للمقارنة المرجعية مع الحظر والتجميد)
    simulate_mode(
        data_map,
        mode="ORIGINAL_BB_ONLY",
        title="1) CURRENT BOT v5.2 (BB + EMA200 Only - Baseline)"
    )

    # 2. إضافة EMA 50 & EMA 21 كفلتر اتجاه إضافي فوق شروط البولينجر (AND)
    simulate_mode(
        data_map,
        mode="STRICT_AND",
        title="2) BB + EMA200 FILTERED BY (EMA 21 > EMA 50 + Slope)"
    )

    # 3. تشغيل المؤشرين الجديدين إلى جانب المؤشرات السابقة في نفس البوت (OR)
    simulate_mode(
        data_map,
        mode="COMBINED_OR",
        title="3) COMBINED SIGNALS: (BB + EMA200) + (EMA 21/50 Rejection)"
    )

    # 4. المؤشران الجديدان (EMA 21 + EMA 50 + شمعة الرفض) بمفردهما
    simulate_mode(
        data_map,
        mode="NEW_EMA_ONLY",
        title="4) NEW INDICATORS ONLY (EMA 50 + EMA 21 + Rejection Wick)"
    )


if __name__ == "__main__":
    main()
