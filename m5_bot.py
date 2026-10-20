#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
M15 Bollinger Trend Reversal Bot - v3.9 (Advanced Analytics & Zero Spam)
الإصلاحات والإضافات في v3.9:
1. ★★ تقارير يومية، أسبوعية، وشهرية شاملة (مرة واحدة فقط لكل فترة) ★★
2. ★★ تحليل عميق: أفضل/أسوأ زوج، أفضل وقت، أفضل يوم، وأفضل شريحة (JPY vs Majors) ★★
3. ★★ حماية مطلقة من القسمة على صفر، وأخطاء المناطق الزمنية، واعتمادية لغة الخادم ★★
4. باقي إصلاحات v3.8 محفوظة (حساب وقت الشمعة، التجميد، التشخيصات، Retry Loop).
ملاحظة مهمة: منطق الاستراتيجية (analyze_market) لم يُمَس حرفيًا.
"""
import os, sys, time, json, logging, uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
import numpy as np
import pandas as pd
import warnings
warnings.filterwarnings('ignore')

try:
    import yfinance as yf
except ImportError:
    print("CRITICAL: Missing yfinance.")
    sys.exit(1)

# ═══════════════════════════════════════════════
# 1. الإعدادات العامة
# ═══════════════════════════════════════════════
BOT_NAME = "M15_BB_Sniper_v3_9_Analytics"
STATE_FILE = "state_m15_fixed_gaza.json"
LOG_LEVEL = logging.INFO

TRADING_SYMBOLS = [
    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "USDCHF=X",
    "AUDUSD=X", "USDCAD=X", "NZDUSD=X", "EURGBP=X",
    "EURJPY=X", "GBPJPY=X", "AUDJPY=X", "CADJPY=X",
    "CHFJPY=X", "NZDJPY=X"
]

BB_PERIOD = 15
BB_STD_DEV = 2.3
EMA_TREND_PERIOD = 200

BAN_START_HOUR_GAZA = 0   
BAN_END_HOUR_GAZA = 9     

FREEZE_DURATION_MINUTES = 15
TRADE_DURATION_MINUTES = 15

TG_TOKEN = os.getenv("TG_TOKEN", "").strip()
TG_CHAT = os.getenv("TG_CHAT", "").strip()

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(BOT_NAME)

# ═══════════════════════════════════════════════
# 2. أدوات الاتصال والحالة
# ═══════════════════════════════════════════════

def send_telegram(msg: str):
    if not TG_TOKEN or not TG_CHAT:
        log.warning("Telegram credentials missing.")
        return False

    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT, "text": msg, "parse_mode": "Markdown"}

    for attempt in range(3):
        try:
            resp = requests.post(url, json=payload, timeout=15)
            if resp.status_code == 200:
                return True
            elif resp.status_code == 429:
                wait = resp.json().get('parameters', {}).get('retry_after', 5)
                time.sleep(wait)
            else:
                log.error(f"TG API Error: {resp.status_code}")
                break
        except Exception as e:
            log.error(f"TG Send Failed: {e}")
            time.sleep(2)
    return False


class StateManager:
    def __init__(self, filepath: str):
        self.filepath = Path(filepath)
        self.data = {
            "frozen_pairs": {},
            "open_trades": [],
            "trade_history": [],
            "daily_counter_date": "",
            "daily_signal_count": 0,
            "last_daily_report": "",
            "last_weekly_report": "",
            "last_monthly_report": ""
        }
        self.load()

    def load(self):
        if self.filepath.exists():
            try:
                with open(self.filepath, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                    for k in self.data:
                        if k in loaded:
                            self.data[k] = loaded[k]
            except Exception as e:
                log.error(f"State Load Error: {e}. Resetting state.")
                self.save()
        else:
            self.save()

    def save(self):
        try:
            tmp = self.filepath.with_suffix('.tmp')
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=2, default=str)
            os.replace(str(tmp), str(self.filepath))
        except Exception as e:
            log.critical(f"STATE SAVE CRITICAL FAILURE: {e}")

    def log_summary(self):
        try:
            frozen_active = [s for s in self.data["frozen_pairs"] if self.is_frozen(s)]
            log.info(f"STATE SUMMARY | open={len(self.data['open_trades'])} | frozen={len(frozen_active)} | signals_today={self.data.get('daily_signal_count')}")
        except Exception as e:
            log.error(f"State summary failed: {e}")

    def get_next_signal_number(self) -> int:
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.data.get("daily_counter_date") != today_str:
            self.data["daily_counter_date"] = today_str
            self.data["daily_signal_count"] = 0
        self.data["daily_signal_count"] += 1
        self.save()
        return self.data["daily_signal_count"]

    def is_frozen(self, symbol: str) -> bool:
        if symbol not in self.data["frozen_pairs"]:
            return False
        freeze_until_str = self.data["frozen_pairs"][symbol]
        try:
            freeze_until = datetime.fromisoformat(freeze_until_str)
            if freeze_until.tzinfo is None:
                freeze_until = freeze_until.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) < freeze_until
        except Exception as e:
            log.error(f"Error checking freeze for {symbol}: {e}")
            return False

    def freeze_pair(self, symbol: str, base_time: datetime, duration_minutes: int = FREEZE_DURATION_MINUTES):
        release_time = base_time + timedelta(minutes=TRADE_DURATION_MINUTES + duration_minutes)
        now_utc = datetime.now(timezone.utc)
        if release_time <= now_utc:
            release_time = now_utc + timedelta(minutes=FREEZE_DURATION_MINUTES)
        self.data["frozen_pairs"][symbol] = release_time.isoformat()
        self.save()

    def add_open_trade(self, trade):
        self.data["open_trades"].append(trade)
        self.save()

    def remove_open_trade(self, tid):
        self.data["open_trades"] = [t for t in self.data["open_trades"] if t.get('id') != tid]
        self.save()

    def archive_trade(self, result):
        self.data["trade_history"].append(result)
        if len(self.data["trade_history"]) > 2000:
            self.data["trade_history"] = self.data["trade_history"][-2000:]
        self.save()

# ═══════════════════════════════════════════════
# 3. محرك البيانات
# ═══════════════════════════════════════════════

def get_data(symbol: str, interval: str = "15m", period: str = "60d"):
    df = None
    for attempt in range(3):
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period, interval=interval, auto_adjust=False, actions=False)
            if df is not None and not df.empty:
                break
            time.sleep(3)
        except Exception as e:
            time.sleep(3)

    if df is None or df.empty:
        return None

    try:
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [str(c[0]) for c in df.columns]
        df.index = pd.to_datetime(df.index, utc=True)
        df = df[~df.index.duplicated()].sort_index().dropna()

        now_utc = pd.Timestamp.now(tz="UTC")
        if not df.empty and df.index[-1] + pd.Timedelta(minutes=15) > now_utc:
            df = df.iloc[:-1]

        if len(df) < EMA_TREND_PERIOD + 10:
            return None

        c = df['Close']
        sma = c.rolling(window=BB_PERIOD).mean()
        std = c.rolling(window=BB_PERIOD).std(ddof=0)
        df['BB_Upper'] = sma + (BB_STD_DEV * std)
        df['BB_Lower'] = sma - (BB_STD_DEV * std)
        df['EMA_200'] = c.ewm(span=EMA_TREND_PERIOD, adjust=False).mean()

        df.dropna(inplace=True)
        return df
    except Exception as e:
        log.error(f"Data Processing Error [{symbol}]: {e}")
        return None


def analyze_market(symbol: str):
    df = get_data(symbol)
    if df is None or len(df) < 2:
        return None

    curr = df.iloc[-1]
    candle_time = df.index[-1]

    price = float(curr['Close'])
    ema_200 = float(curr['EMA_200'])
    bb_upper = float(curr['BB_Upper'])
    bb_lower = float(curr['BB_Lower'])

    signal_type = None
    if price > ema_200 and price <= bb_lower:
        signal_type = "CALL"
    elif price < ema_200 and price >= bb_upper:
        signal_type = "PUT"

    pip_mult = 100.0 if "JPY" in symbol else 10000.0
    if price > ema_200:
        gap_pips = (price - bb_lower) * pip_mult
        near = "CALL"
    elif price < ema_200:
        gap_pips = (bb_upper - price) * pip_mult
        near = "PUT"
    else:
        gap_pips = None
        near = None

    return {
        "symbol": symbol, "type": signal_type, "price": price,
        "time": candle_time, "ema_200": ema_200,
        "bb_level": bb_lower if signal_type == "CALL" else bb_upper,
        "diag_near": near, "diag_gap_pips": gap_pips
    }

# ═══════════════════════════════════════════════
# 4. نظام الحسم التلقائي
# ═══════════════════════════════════════════════

def resolve_pending_trades(sm: StateManager):
    if not sm.data["open_trades"]:
        return

    now_utc = datetime.now(timezone.utc)
    resolved_ids = []

    for trade in sm.data["open_trades"]:
        try:
            entry_dt = datetime.fromisoformat(trade['entry_time'])
            if entry_dt.tzinfo is None:
                entry_dt = entry_dt.replace(tzinfo=timezone.utc)

            exit_candle_start = entry_dt + timedelta(minutes=TRADE_DURATION_MINUTES)
            check_ready_time = exit_candle_start + timedelta(minutes=TRADE_DURATION_MINUTES) + timedelta(minutes=2)

            if now_utc < check_ready_time:
                continue

            df = get_data(trade['symbol'])
            if df is None or df.empty:
                continue

            target_ts = pd.Timestamp(exit_candle_start)
            future = df[df.index >= target_ts]

            if len(future) > 0:
                exit_price = float(future.iloc[0]['Close'])
            else:
                past = df[df.index < target_ts]
                if past.empty:
                    continue
                exit_price = float(past.iloc[-1]['Close'])

            entry_px = float(trade['entry_price'])
            diff = exit_price - entry_px
            pip_mult = 100.0 if "JPY" in trade['symbol'] else 10000.0
            pips_diff = abs(diff) * pip_mult

            if pips_diff < 0.1:
                emoji, is_win, status_text = "⚪ لاغي", None, "_Market Flat_"
            else:
                is_win = (diff > 0) if trade['type'] == 'CALL' else (diff < 0)
                emoji = "✅ ربح" if is_win else "❌ خسارة"
                status_text = ""

            result_msg = (f"{emoji} *نتيجة M15: {trade['symbol']}*\n\n"
                          f"⏰ *وقت الدخول:* `{entry_dt.strftime('%H:%M')} UTC`\n"
                          f"📈 *الاتجاه:* `{trade['type']}`\n"
                          f"💰 *الدخول:* `{entry_px:.5f}`\n"
                          f"💸 *الخروج:* `{exit_price:.5f}`\n"
                          f"📊 *الفرق:* `{pips_diff:+.1f} نقطة` {status_text}")
            send_telegram(result_msg)

            sm.archive_trade({
                "id": trade['id'], "symbol": trade['symbol'], "type": trade['type'],
                "entry_time": trade['entry_time'], "exit_time": now_utc.isoformat(),
                "win": is_win, "pips": pips_diff
            })
            resolved_ids.append(trade['id'])
        except Exception as e:
            log.error(f"Critical Error resolving trade {trade.get('id')}: {e}", exc_info=True)

    for tid in resolved_ids:
        sm.remove_open_trade(tid)

# ═══════════════════════════════════════════════
# 5. نظام التقارير المتقدم (Zero Spam & Deep Analytics)
# ═══════════════════════════════════════════════

def send_detailed_report(df: pd.DataFrame, report_type: str, period_name: str, sm: StateManager):
    total = len(df)
    wins = len(df[df['win'] == True])
    losses = len(df[df['win'] == False])
    voids = len(df[df['win'].isna()])
    
    valid_trades = wins + losses
    wr = (wins / valid_trades * 100) if valid_trades > 0 else 0.0
    
    # ★★ إصلاح أمان الحسابات العشرية ★★
    net_pips = float(df.loc[df['win'] == True, 'pips'].sum()) - float(df.loc[df['win'] == False, 'pips'].sum())

    # 1. أفضل وأسوأ زوج
    pair_stats = df.groupby('symbol').agg(trades=('id', 'count'), wins=('win', lambda x: (x == True).sum()), pips=('pips', 'sum')).reset_index()
    pair_stats['wr'] = (pair_stats['wins'] / pair_stats['trades']) * 100
    valid_pairs = pair_stats[pair_stats['trades'] >= 2]
    
    if not valid_pairs.empty:
        best_pair = valid_pairs.loc[valid_pairs['pips'].idxmax()]
        worst_pair = valid_pairs.loc[valid_pairs['pips'].idxmin()]
        best_pair_txt = f"🥇 {best_pair['symbol']} ({best_pair['pips']:+.1f} Pips, WR: {best_pair['wr']:.1f}%)"
        worst_pair_txt = f"⚠️ {worst_pair['symbol']} ({worst_pair['pips']:+.1f} Pips, WR: {worst_pair['wr']:.1f}%)"
    else:
        best_pair_txt = "غير متاح (بيانات غير كافية)"
        worst_pair_txt = "غير متاح (بيانات غير كافية)"

    # 2. أفضل وقت (ساعة)
    df['gaza_hour'] = df['exit_dt_gaza'].dt.hour
    time_stats = df.groupby('gaza_hour').agg(wins=('win', lambda x: (x == True).sum()), total=('win', 'count')).reset_index()
    time_stats['wr'] = (time_stats['wins'] / time_stats['total']) * 100
    valid_times = time_stats[time_stats['total'] >= 2]
    best_time_txt = f"🕒 {valid_times.loc[valid_times['wr'].idxmax()]['gaza_hour']:02d}:00 - {(valid_times.loc[valid_times['wr'].idxmax()]['gaza_hour']+1)%24:02d}:00 بتوقيت غزة" if not valid_times.empty else "غير متاح"

    # 3. أفضل يوم (★ إصلاح حاسم: استخدام dayofweek لتجنب مشاكل لغة الخادم ★)
    day_map = {0: 'الاثنين', 1: 'الثلاثاء', 2: 'الأربعاء', 3: 'الخميس', 4: 'الجمعة', 5: 'السبت', 6: 'الأحد'}
    df['gaza_day_ar'] = df['exit_dt_gaza'].dt.dayofweek.map(day_map)
    day_stats = df.groupby('gaza_day_ar').agg(wins=('win', lambda x: (x == True).sum()), total=('win', 'count')).reset_index()
    valid_days = day_stats[day_stats['total'] >= 2]
    best_day_txt = f"📅 {valid_days.loc[valid_days['wr'].idxmax()]['gaza_day_ar']}" if not valid_days.empty else "غير متاح"

    # 4. أفضل شريحة (JPY vs غير JPY)
    df['is_jpy'] = df['symbol'].str.contains('JPY')
    cat_stats = df.groupby('is_jpy').agg(wins=('win', lambda x: (x == True).sum()), total=('win', 'count'), pips=('pips', 'sum')).reset_index()
    cat_stats['name'] = cat_stats['is_jpy'].apply(lambda x: "أزواج الين (JPY)" if x else "الأزواج الرئيسية/الأخرى")
    valid_cats = cat_stats[cat_stats['total'] >= 2]
    best_cat_txt = f"🌍 {valid_cats.loc[valid_cats['pips'].idxmax()]['name']} ({valid_cats.loc[valid_cats['pips'].idxmax()]['pips']:+.1f} Pips)" if not valid_cats.empty else "غير متاح"

    emoji_wr = "🟢 ممتاز" if wr >= 60 else ("🟡 جيد" if wr >= 45 else "🔴 يحتاج مراجعة")

    msg = (
        f"*📊 التقرير {report_type} ({period_name})*\n\n"
        f"📈 *إجمالي الصفقات:* `{total}` (✅ {wins} | ❌ {losses} | ⚪ {voids})\n"
        f"🏆 *نسبة الفوز:* {emoji_wr} `{wr:.1f}%`\n"
        f"💰 *صافي النقاط:* `{net_pips:+.1f} Pips`\n\n"
        f"*🔍 التحليل العميق:*\n"
        f"• *أفضل زوج:* {best_pair_txt}\n"
        f"• *أسوأ زوج (مراجعة):* {worst_pair_txt}\n"
        f"• *أفضل وقت:* {best_time_txt}\n"
        f"• *أفضل يوم:* {best_day_txt}\n"
        f"• *أفضل شريحة:* {best_cat_txt}\n\n"
        f"_انتهت جلسة {report_type}. استمر في الانضباط!_"
    )
    send_telegram(msg)
    log.info(f"Sent {report_type} report for {period_name}")


def generate_advanced_reports(sm: StateManager):
    now_utc = datetime.now(timezone.utc)
    hebron_tz = timezone(timedelta(hours=3))
    local_now = now_utc.astimezone(hebron_tz)

    history = sm.data.get("trade_history", [])
    if not history:
        return

    df_hist = pd.DataFrame(history)
    # ★★ إصلاح حاسم: التعامل الآمن مع المناطق الزمنية المختلطة ★★
    df_hist['exit_dt_gaza'] = pd.to_datetime(df_hist['exit_time'], utc=True).dt.tz_convert(hebron_tz)

    # 1. التقرير اليومي (أمس)
    yesterday_str = (local_now - timedelta(days=1)).strftime("%Y-%m-%d")
    if local_now.hour == 0 and local_now.minute < 15 and sm.data.get("last_daily_report") != yesterday_str:
        daily_df = df_hist[df_hist['exit_dt_gaza'].dt.strftime("%Y-%m-%d") == yesterday_str]
        if not daily_df.empty:
            send_detailed_report(daily_df, "اليومي", yesterday_str, sm)
        sm.data["last_daily_report"] = yesterday_str
        sm.save()

    # 2. التقرير الأسبوعي
    last_week_dt = local_now - timedelta(days=local_now.weekday() + 1)
    last_week_year, last_week_num, _ = last_week_dt.isocalendar()
    last_week_str = f"{last_week_year}-W{last_week_num:02d}"
    
    if local_now.weekday() == 6 and local_now.hour == 0 and local_now.minute < 15 and sm.data.get("last_weekly_report") != last_week_str:
        weekly_df = df_hist[(df_hist['exit_dt_gaza'].dt.isocalendar().year == last_week_year) & 
                            (df_hist['exit_dt_gaza'].dt.isocalendar().week == last_week_num)]
        if not weekly_df.empty:
            send_detailed_report(weekly_df, "الأسبوعي", f"الأسبوع {last_week_num} من {last_week_year}", sm)
        sm.data["last_weekly_report"] = last_week_str
        sm.save()

    # 3. التقرير الشهري
    last_month_dt = local_now.replace(day=1) - timedelta(days=1)
    last_month_str = last_month_dt.strftime("%Y-%m")
    
    if local_now.day == 1 and local_now.hour == 0 and local_now.minute < 15 and sm.data.get("last_monthly_report") != last_month_str:
        monthly_df = df_hist[(df_hist['exit_dt_gaza'].dt.year == last_month_dt.year) & 
                             (df_hist['exit_dt_gaza'].dt.month == last_month_dt.month)]
        if not monthly_df.empty:
            month_names_ar = {"01": "يناير", "02": "فبراير", "03": "مارس", "04": "أبريل", "05": "مايو", "06": "يونيو", 
                              "07": "يوليو", "08": "أغسطس", "09": "سبتمبر", "10": "أكتوبر", "11": "نوفمبر", "12": "ديسمبر"}
            month_name_ar = month_names_ar.get(last_month_dt.strftime("%m"), last_month_dt.strftime("%B"))
            send_detailed_report(monthly_df, "الشهري", f"{month_name_ar} {last_month_dt.year}", sm)
        sm.data["last_monthly_report"] = last_month_str
        sm.save()

# ═══════════════════════════════════════════════
# 6. الحلقة الرئيسية
# ═══════════════════════════════════════════════

def main():
    log.info("=" * 50)
    log.info(f"STARTING {BOT_NAME} | ADVANCED ANALYTICS MODE")
    log.info(f"Current UTC Time: {datetime.now(timezone.utc).strftime('%H:%M:%S')}")
    log.info("=" * 50)

    if not TG_TOKEN or not TG_CHAT:
        log.critical("MISSING TELEGRAM CREDS. ABORTING.")
        return

    sm = StateManager(STATE_FILE)
    sm.log_summary()

    resolve_pending_trades(sm)
    generate_advanced_reports(sm)

    now_utc = datetime.now(timezone.utc)
    hebron_tz = timezone(timedelta(hours=3))
    local_now = now_utc.astimezone(hebron_tz)

    if now_utc.weekday() >= 5:
        log.info("Weekend. Skipping signals.")
        return

    current_gaza_hour = local_now.hour
    if BAN_START_HOUR_GAZA <= current_gaza_hour < BAN_END_HOUR_GAZA:
        log.info(f"Inside Night Ban Window ({current_gaza_hour}:xx Gaza Time). Skipping signals.")
        return

    signals_found = 0
    best_gap, best_sym, best_dir = None, None, None

    for sym in TRADING_SYMBOLS:
        if sm.is_frozen(sym):
            continue

        result = analyze_market(sym)
        if result is None:
            continue

        if result.get("diag_gap_pips") is not None:
            g = result["diag_gap_pips"]
            if g >= 0 and (best_gap is None or g < best_gap):
                best_gap, best_sym, best_dir = g, sym, result["diag_near"]

        if result["type"] is None:
            continue

        signals_found += 1
        signal_num = sm.get_next_signal_number()
        arrival_time_gaza = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=3))).strftime("%H:%M")

        arrow = "🟢 CALL (صعود)" if result['type'] == "CALL" else "🔴 PUT (هبوط)"
        expiry_time = (result['time'] + timedelta(minutes=TRADE_DURATION_MINUTES * 2)).strftime("%H:%M")

        msg = (f"*🎯 إشارة M15 BOLLINGER TREND REVERSAL #{signal_num}*\n\n"
               f"📊 *الزوج:* `{result['symbol']}`\n"
               f"📈 *الاتجاه:* {arrow}\n"
               f"💰 *السعر الحالي:* `{result['price']:.5f}`\n\n"
               f"🎯 *منطقة الدخول:* لمس البولنجر باندز\n"
               f"🏷️ *فلتر الاتجاه (EMA200):* {'أعلى' if result['type']=='CALL' else 'أسفل'} المتوسط\n\n"
               f"⏱️ *التعليمات:*\n"
               f"✅ ادخل فوراُ (فرصة إحصائية عالية)\n\n"
               f"⌛ *انتهاء الصفقة:* {expiry_time} UTC\n"
               f"💰 *شرط الـ payout:* 85% فأعلى فقط\n\n"
               f"🕒 *وقت وصول الإشارة:* {arrival_time_gaza} (غزة)\n"
               f"📝 *ملاحظة:* استراتيجية M15 BB + EMA200 (Z-Score 3.37)")

        mid = send_telegram(msg)
        if mid:
            sm.freeze_pair(sym, base_time=result['time'])
            sm.add_open_trade({
                "id": str(uuid.uuid4())[:8], "symbol": result['symbol'], "type": result['type'],
                "entry_price": result['price'], "entry_time": result['time'].isoformat(), "msg_id": mid
            })
            log.info(f"Signal Sent & Frozen: {sym} {result['type']} (# {signal_num})")
        else:
            log.error(f"Failed to send signal for {sym}")
        time.sleep(1.5)

    if signals_found == 0:
        if best_gap is not None:
            log.info(f"No valid setups. Closest: {best_sym} {best_dir} (gap {best_gap:.1f} pips). Market quiet.")
        else:
            log.info("No valid setups. No pair even near a setup.")
    else:
        log.info(f"Cycle complete. {signals_found} new signals sent.")

    sm.log_summary()

if __name__ == "__main__":
    main()
