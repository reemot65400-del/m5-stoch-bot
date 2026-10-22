#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
M15 Bollinger Trend Reversal Bot - v4.3 (Fixed Resolution Timing)
الإصلاحات الهندسية في v4.3:
1. ★★ إصلاح جذري لمشكلة عدم وصول النتائج (Resolution Failure):
   كان يتم تسجيل entry_time كوقت "فتح" الشمعة، مما يسبب انزياحاً زمنياً في حساب وقت الخروج
   ويجعل البوت يفشل في إيجاد الشمعة الصحيحة للحسم أحياناً.
   الآن: يتم تسجيل entry_time كوقت "إغلاق" شمعة الإشارة (الدقيقة 15)، وهو المرجع الحقيقي
   لدورة الصفقة (30 دقيقة من لحظة الإغلاق).
2. تحسين دقة الحسم: الاعتماد على وقت الإغلاق الفعلي للشموع بدلاً من التقدير النظري.
"""
import os, sys, time, json, logging, uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
import requests
import numpy as np
import pandas as pd
import warnings
warnings.filterwarnings('ignore')

try:
    import yfinance as yf
except ImportError:
    print("pip install yfinance"); sys.exit(1)

try:
    GAZA_TZ = ZoneInfo("Asia/Gaza")
except Exception:
    GAZA_TZ = timezone(timedelta(hours=3))

def get_gaza_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(GAZA_TZ)

# ═══════════════════════════════════════════════
# 1. الإعدادات العامة
# ═══════════════════════════════════════════════
BOT_NAME = "M15_BB_Sniper_v4_3_Pro" # ← تحديث الرقم للإشارة للإصلاح
STATE_FILE = "state_m15_fixed_gaza.json"
LOG_LEVEL = logging.INFO

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

FREEZE_DURATION_MINUTES = 45 
TRADE_DURATION_MINUTES = 15 

TG_TOKEN = os.getenv("TG_TOKEN", "").strip()
TG_CHAT = os.getenv("TG_CHAT", "").strip()

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(BOT_NAME)

# ═══════════════════════════════════════════════
# 2. أدوات الاتصال والحالة (نفسها بدون تغيير)
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
            elif resp.status_code == 400:
                log.warning("TG Markdown parse error, retrying as plain text...")
                plain = {"chat_id": TG_CHAT, "text": msg.replace("*", "").replace("`", "").replace("_", "")}
                try:
                    resp_plain = requests.post(url, json=plain, timeout=15)
                    if resp_plain.status_code == 200:
                        return True
                except Exception:
                    pass
                break
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
                    for k in self.data.keys():
                        if k in loaded:
                            self.data[k] = loaded[k]
                log.info("State loaded successfully.")
            except Exception as e:
                log.error(f"Failed to load state: {e}. Starting fresh.")
                self.save()
        else:
            self.save()

    def save(self):
        try:
            tmp = self.filepath.with_suffix('.tmp')
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=2, default=str, ensure_ascii=False)
            os.replace(str(tmp), str(self.filepath))
        except Exception as e:
            log.critical(f"STATE SAVE CRITICAL FAILURE: {e}")

    def log_summary(self):
        try:
            frozen_active = [s for s in self.data["frozen_pairs"] if self.is_frozen(s)]
            log.info(f"STATE SUMMARY | open={len(self.data['open_trades'])} | frozen={len(frozen_active)} | history={len(self.data['trade_history'])}")
        except Exception as e:
            log.error(f"State summary failed: {e}")

    def get_next_signal_number(self) -> int:
        today_str = get_gaza_now().strftime("%Y-%m-%d")
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
# 3. محرك البيانات والاستراتيجية (لم يُمس حرفياَ)
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
        except Exception:
            time.sleep(3)

    if df is None or df.empty:
        return None

    try:
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [str(c[0]) for c in df.columns]
        df.index = pd.to_datetime(df.index, utc=True)
        df = df[~df.index.duplicated()].sort_index().dropna()

        now_utc = pd.Timestamp.now(tz="UTC")
        # حذف الشمعة الجارية فقط: 15 دقيقة إغلاق + دقيقتان هامش بيانات
        if not df.empty and df.index[-1] + pd.Timedelta(minutes=17) > now_utc: 
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
    candle_time = df.index[-1] # هذا وقت فتح الشمعة

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
# 4. نظام الحسم التلقائي (★★ مُعدل جذرياَ لحل مشكلة عدم الوصول ★★)
# ═══════════════════════════════════════════════

def resolve_pending_trades(sm: StateManager):
    if not sm.data["open_trades"]:
        return

    now_utc = datetime.now(timezone.utc)
    resolved_ids = []

    for trade in sm.data["open_trades"]:
        try:
            # قراءة وقت الدخول المسجل
            entry_dt_raw = datetime.fromisoformat(trade['entry_time'])
            if entry_dt_raw.tzinfo is None:
                entry_dt_raw = entry_dt_raw.replace(tzinfo=timezone.utc)
            
            # ★★ الإصلاح هنا: تحويل وقت الفتح إلى وقت الإغلاق الفعلي للشمعة ★★
            # الشمعة تستمر 15 دقيقة. إذن وقت الإغلاق = وقت الفتح + 15 دقيقة.
            # هذا هو اللحظة التي "يبدأ" فيها العد التنازلي للصفقة الحقيقية.
            actual_entry_close_time = entry_dt_raw + timedelta(minutes=TRADE_DURATION_MINUTES)

            # تحديد موعد انتهاء الصفقة (شمعة خروج واحدة = 15 دقيقة أخرى)
            expected_exit_close_time = actual_entry_close_time + timedelta(minutes=TRADE_DURATION_MINUTES)
            
            # ننتظر إغلاق شمعة الخروج + هامش تحديث البيانات (دقيقتان)
            check_ready_time = expected_exit_close_time + timedelta(minutes=2)
            
            if now_utc < check_ready_time:
                continue
            
            sym = trade['symbol']
            dr = trade['direction']
            entry_px = float(trade['entry_price'])
            
            log.info(f"Resolving {sym} | Target Exit Close: {expected_exit_close_time}")
            
            df_exit_full = get_data(sym)
            if df_exit_full is None or df_exit_full.empty:
                log.warning(f"No exit data for {sym}. Retry next cycle.")
                continue
                
            # البحث عن الشمعة التي تمثل "وقت الخروج"
            # نحن نريد إغلاق الشمعة التي تنتهي عند expected_exit_close_time
            # في yfinance، الفهرس هو وقت البدء. إذن الشمعة المطلوبة تبدأ عند:
            target_candle_start = expected_exit_close_time - timedelta(minutes=TRADE_DURATION_MINUTES)
            
            # نجدها بمطابقة تقريبية (لأن الفهارس قد تكون بالثواني الجزئية)
            mask = (df_exit_full.index >= target_candle_start - timedelta(seconds=30)) & \
                   (df_exit_full.index <= target_candle_start + timedelta(seconds=30))
                   
            matching_candles = df_exit_full[mask]
            
            if not matching_candles.empty:
                exit_px = float(matching_candles.iloc[0]['Close'])
            else:
                # احتياطي صارم: نأخذ أقرب شمعة مغلقة قبل الموعد المحدد
                past_mask = df_exit_full.index < target_candle_start
                if past_mask.any():
                    exit_px = float(df_exit_full[past_mask].iloc[-1]['Close'])
                    log.warning(f"Exact exit candle not found for {sym}, using last close before target.")
                else:
                    log.error(f"No candles available for {sym} resolution. Skipping.")
                    continue

            pip_mult = 100.0 if "JPY" in sym else 10000.0
            diff = exit_px - entry_px
            pips_diff = abs(diff) * pip_mult
            
            log.info(f"Success! Entry={entry_px} Exit={exit_px} Diff={diff} Pips={pips_diff:.2f}")
            
            # سوق ساكن حقاَ
            if pips_diff < 0.1:
                emoji, is_win, status_text = "⚪ لاغي", None, "_Market Flat_"
            else:
                is_win = (diff > 0) if dr == 'CALL' else (diff < 0)
                emoji = "✅ ربح" if is_win else "❌ خسارة"
                status_text = ""

            result_msg = (f"{emoji} *نتيجة M15: {sym}*\n\n"
                          f"⏰ *وقت الدخول:* `{actual_entry_close_time.strftime('%H:%M')} UTC`\n"
                          f"📈 *الاتجاه:* `{dr}`\n"
                          f"💰 *الدخول:* `{entry_px:.5f}`\n"
                          f"💸 *الخروج:* `{exit_px:.5f}`\n"
                          f"📊 *الفرق:* `{pips_diff:+.1f} نقطة` {status_text}")
            send_telegram(result_msg)

            sm.archive_trade({
                "id": trade['id'], "symbol": sym, "type": dr,
                "entry_time": trade['entry_time'], # نحفظ الأصلي للأرشيف
                "exit_time": expected_exit_close_time.isoformat(), # وقت الإغلاق الفعلي للخروج
                "win": is_win, "pips": float(pips_diff)
            })
            resolved_ids.append(trade['id'])
            
        except Exception as e:
            log.error(f"Critical Error resolving trade {trade.get('id')}: {e}", exc_info=True)

    for tid in resolved_ids:
        sm.remove_open_trade(tid)

# ═══════════════════════════════════════════════
# 5. نظام التقارير المتقدم (نفسه بدون تغيير)
# ═══════════════════════════════════════════════

AR_MONTHS = {
    "01": "يناير", "02": "فبراير", "03": "مارس", "04": "أبريل",
    "05": "مايو", "06": "يونيو", "07": "يوليو", "08": "أغسطس",
    "09": "سبتمبر", "10": "أكتوبر", "11": "نوفمبر", "12": "ديسمبر"
}
AR_DAYS = {0: 'الاثنين', 1: 'الثلاثاء', 2: 'الأربعاء', 3: 'الخميس',
           4: 'الجمعة', 5: 'السبت', 6: 'الأحد'}


def ar_month_name(dt: datetime) -> str:
    return AR_MONTHS.get(dt.strftime("%m"), dt.strftime("%B"))


def send_detailed_report(df: pd.DataFrame, report_type: str, period_name: str) -> bool:
    if df is None or df.empty:
        msg = (
            f"*📊 التقرير {report_type} ({period_name})*\n\n"
            f"📈 *إجمالي الصفقات:* `0`\n"
            f"ℹ️ _لم تُسجل صفقات خلال هذه الفترة._\n\n"
            f"_البوت يعمل بكفاءة ويراقب السوق بانتظام!_"
        )
        sent = send_telegram(msg)
        if sent:
            log.info(f"Sent empty-period {report_type} report for {period_name}")
        return sent

    df = df.copy()

    # ★★ v4.1 Zero-Crash: حماية من الأعمدة الناقصة في أرشيف قديم ★★
    if 'id' not in df.columns:
        df['id'] = [f"row{i}" for i in range(len(df))]
    if 'symbol' not in df.columns:
        df['symbol'] = 'UNKNOWN'
    if 'win' not in df.columns:
        df['win'] = None
    if 'pips' not in df.columns:
        df['pips'] = 0.0
    if 'exit_dt_gaza' not in df.columns:
        df['exit_dt_gaza'] = pd.NaT

    df['pips'] = pd.to_numeric(df['pips'], errors='coerce').fillna(0.0)

    # النقاط الموجّهة: موجب ربح، سالب خسارة، صفر لاغي
    df['signed_pips'] = np.where(
        df['win'] == True, df['pips'],
        np.where(df['win'] == False, -df['pips'], 0.0)
    )

    total = len(df)
    wins = int((df['win'] == True).sum())
    losses = int((df['win'] == False).sum())
    voids = int(df['win'].isna().sum())

    valid_trades = wins + losses
    wr = (wins / valid_trades * 100.0) if valid_trades > 0 else 0.0
    net_pips = float(df['signed_pips'].sum())

    # 1. أفضل/أسوأ زوج (محصن ضد القسمة على صفر)
    pair_stats = df.groupby('symbol', as_index=False).agg(
        trades=('id', 'count'),
        wins=('win', lambda x: int((x == True).sum())),
        losses=('win', lambda x: int((x == False).sum())),
        pips=('signed_pips', 'sum')
    )
    pair_stats['valid'] = pair_stats['wins'] + pair_stats['losses']
    pair_stats['wr'] = np.where(pair_stats['valid'] > 0,
                                (pair_stats['wins'] / pair_stats['valid']) * 100.0, 0.0)

    valid_pairs = pair_stats[pair_stats['trades'] >= 2]
    if valid_pairs.empty:
        valid_pairs = pair_stats[pair_stats['trades'] >= 1]

    if not valid_pairs.empty:
        best_pair = valid_pairs.loc[valid_pairs['pips'].idxmax()]
        worst_pair = valid_pairs.loc[valid_pairs['pips'].idxmin()]
        best_pair_txt = f"🥇 {best_pair['symbol']} ({best_pair['pips']:+.1f} Pips, WR: {best_pair['wr']:.1f}%)"
        
        # ★★ إصلاح: التحقق من وجود أزواج أخرى مختلفة عن الأفضل ★★
        other_pairs = valid_pairs[valid_pairs['symbol'] != best_pair['symbol']]
        if not other_pairs.empty:
            worst_pair_txt = f"⚠️ {worst_pair['symbol']} ({worst_pair['pips']:+.1f} Pips, WR: {worst_pair['wr']:.1f}%)"
        else:
            worst_pair_txt = "لا يوجد (زوج واحد فقط تم التداول عليه)"
    else:
        best_pair_txt = "غير متاح"
        worst_pair_txt = "غير متاح"

    # 2. أفضل وقت (ساعة) بتوقيت غزة — v4.1: حماية من NaN
    hour_src = df['exit_dt_gaza'].dt.hour
    time_stats = df.assign(gaza_hour=hour_src).dropna(subset=['gaza_hour']).groupby('gaza_hour', as_index=False).agg(
        wins=('win', lambda x: int((x == True).sum())),
        losses=('win', lambda x: int((x == False).sum())),
        total=('id', 'count')
    )
    time_stats['valid'] = time_stats['wins'] + time_stats['losses']
    time_stats['wr'] = np.where(time_stats['valid'] > 0,
                                (time_stats['wins'] / time_stats['valid']) * 100.0, 0.0)
    valid_times = time_stats[time_stats['total'] >= 2]
    if valid_times.empty:
        valid_times = time_stats[time_stats['total'] >= 1]

    best_time_txt = "غير متاح"
    if not valid_times.empty:
        best_hour = valid_times.loc[valid_times['wr'].idxmax()]['gaza_hour']
        if pd.notna(best_hour):
            best_hour = int(best_hour)
            best_time_txt = f"🕒 {best_hour:02d}:00 - {(best_hour + 1) % 24:02d}:00 بتوقيت غزة"

    # 3. أفضل يوم (إصلاح KeyError: 'wr' محفوظ من v4.0)
    day_src = df['exit_dt_gaza'].dt.dayofweek
    day_stats = df.assign(gaza_day_ar=day_src.dropna().map(lambda i: AR_DAYS.get(int(i), 'غير معروف')))\
        .dropna(subset=['gaza_day_ar']).groupby('gaza_day_ar', as_index=False).agg(
        wins=('win', lambda x: int((x == True).sum())),
        losses=('win', lambda x: int((x == False).sum())),
        total=('id', 'count')
    )
    day_stats['valid'] = day_stats['wins'] + day_stats['losses']
    day_stats['wr'] = np.where(day_stats['valid'] > 0,
                               (day_stats['wins'] / day_stats['valid']) * 100.0, 0.0)
    valid_days = day_stats[day_stats['total'] >= 2]
    if valid_days.empty:
        valid_days = day_stats[day_stats['total'] >= 1]

    best_day_txt = "غير متاح"
    if not valid_days.empty:
        best_day_txt = f"📅 {valid_days.loc[valid_days['wr'].idxmax()]['gaza_day_ar']}"

    # 4. أفضل شريحة (JPY vs غير JPY)
    df['is_jpy'] = df['symbol'].astype(str).str.contains('JPY')
    cat_stats = df.groupby('is_jpy', as_index=False).agg(
        wins=('win', lambda x: int((x == True).sum())),
        total=('id', 'count'),
        pips=('signed_pips', 'sum')
    )
    cat_stats['name'] = cat_stats['is_jpy'].apply(lambda x: "أزواج الين (JPY)" if x else "الأزواج الرئيسية/الأخرى")
    valid_cats = cat_stats[cat_stats['total'] >= 2]
    if valid_cats.empty:
        valid_cats = cat_stats[cat_stats['total'] >= 1]

    if not valid_cats.empty:
        best_cat_row = valid_cats.loc[valid_cats['pips'].idxmax()]
        best_cat_txt = f"🌍 {best_cat_row['name']} ({best_cat_row['pips']:+.1f} Pips)"
    else:
        best_cat_txt = "غير متاح"

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
        f"_انتهت جلسة التقرير {report_type}. استمر في الانضباط!_"
    )
    sent = send_telegram(msg)
    if sent:
        log.info(f"Sent {report_type} report for {period_name}")
    return sent


def generate_advanced_reports(sm: StateManager):
    local_now = get_gaza_now()
    history = sm.data.get("trade_history", [])

    if history:
        df_hist = pd.DataFrame(history)
        if 'exit_time' in df_hist.columns:
            df_hist['exit_dt_gaza'] = pd.to_datetime(
                df_hist['exit_time'], utc=True, errors='coerce').dt.tz_convert(GAZA_TZ)
            df_hist = df_hist.dropna(subset=['exit_dt_gaza'])
        else:
            df_hist = pd.DataFrame()
    else:
        df_hist = pd.DataFrame()

    # ─── 1. التقرير اليومي: أي نبضة بعد منتصف الليل حتى ينجح الإرسال ───
    yesterday = local_now - timedelta(days=1)
    yesterday_str = yesterday.strftime("%Y-%m-%d")
    daily_period_name = f"{AR_DAYS[yesterday.weekday()]} {yesterday.day} {ar_month_name(yesterday)} {yesterday.year}"

    if sm.data.get("last_daily_report") != yesterday_str:
        log.info(f"Daily report due for {yesterday_str} — attempting send...")
        if not df_hist.empty:
            daily_df = df_hist[df_hist['exit_dt_gaza'].dt.strftime("%Y-%m-%d") == yesterday_str]
        else:
            daily_df = pd.DataFrame()

        if send_detailed_report(daily_df, "اليومي", daily_period_name):
            sm.data["last_daily_report"] = yesterday_str
            sm.save()
            log.info(f"Daily report CONFIRMED for {yesterday_str}")
        else:
            log.warning("Daily report send failed — will retry on next pulse (guaranteed delivery).")

    # ─── 2. التقرير الأسبوعي: كل نبضات يوم الأحد حتى ينجح ───
    last_week_dt = local_now - timedelta(days=local_now.weekday() + 1)
    last_week_year, last_week_num, _ = last_week_dt.isocalendar()
    last_week_str = f"{last_week_year}-W{last_week_num:02d}"
    week_start = last_week_dt - timedelta(days=6)
    weekly_period_name = (f"الأسبوع {last_week_num} "
                          f"({week_start.day} {ar_month_name(week_start)} – "
                          f"{last_week_dt.day} {ar_month_name(last_week_dt)} {last_week_dt.year})")

    if local_now.weekday() == 6 and sm.data.get("last_weekly_report") != last_week_str:
        log.info(f"Weekly report due for {last_week_str} — attempting send...")
        if not df_hist.empty:
            iso_cal = df_hist['exit_dt_gaza'].dt.isocalendar()
            weekly_df = df_hist[(iso_cal.year == last_week_year) & (iso_cal.week == last_week_num)]
        else:
            weekly_df = pd.DataFrame()

        if send_detailed_report(weekly_df, "الأسبوعي", weekly_period_name):
            sm.data["last_weekly_report"] = last_week_str
            sm.save()
            log.info(f"Weekly report CONFIRMED for {last_week_str}")
        else:
            log.warning("Weekly report send failed — will retry on next pulse.")

    # ─── 3. التقرير الشهري: كل نبضات يوم 1 حتى ينجح ───
    last_month_dt = local_now.replace(day=1) - timedelta(days=1)
    last_month_str = last_month_dt.strftime("%Y-%m")
    monthly_period_name = f"{ar_month_name(last_month_dt)} {last_month_dt.year}"

    if local_now.day == 1 and sm.data.get("last_monthly_report") != last_month_str:
        log.info(f"Monthly report due for {last_month_str} — attempting send...")
        if not df_hist.empty:
            monthly_df = df_hist[
                (df_hist['exit_dt_gaza'].dt.year == last_month_dt.year) &
                (df_hist['exit_dt_gaza'].dt.month == last_month_dt.month)
            ]
        else:
            monthly_df = pd.DataFrame()

        if send_detailed_report(monthly_df, "الشهري", monthly_period_name):
            sm.data["last_monthly_report"] = last_month_str
            sm.save()
            log.info(f"Monthly report CONFIRMED for {last_month_str}")
        else:
            log.warning("Monthly report send failed — will retry on next pulse.")


# ═══════════════════════════════════════════════
# 6. الحلقة الرئيسية
# ═══════════════════════════════════════════════

def main():
    local_now = get_gaza_now()
    log.info("=" * 55)
    log.info(f"STARTING {BOT_NAME} | ZERO-BUG PRODUCTION MODE")
    log.info(f"UTC Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} | Gaza Time: {local_now.strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 55)

    if not TG_TOKEN or not TG_CHAT:
        log.critical("MISSING TELEGRAM CREDS. ABORTING.")
        return

    sm = StateManager(STATE_FILE)
    sm.log_summary()

    # 1. حسم الصفقات المفتوحة أولاَ لتدخل في التقرير إذا حان وقته
    resolve_pending_trades(sm)

    # 2. فحص وإرسال التقارير (اليومية / الأسبوعية / الشهرية)
    generate_advanced_reports(sm)

    # 3. فلاتر أوقات التوقف (عطلة نهاية الأسبوع + الحظر الليلي)
    now_utc = datetime.now(timezone.utc)
    if now_utc.weekday() >= 5:
        log.info("Weekend. Skipping new signals.")
        return

    current_gaza_hour = local_now.hour
    if BAN_START_HOUR_GAZA <= current_gaza_hour < BAN_END_HOUR_GAZA:
        log.info(f"Inside Night Ban Window ({current_gaza_hour:02d}:xx Gaza Time). Skipping new signals.")
        return

    # 4. فحص السوق وإرسال الإشارات
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
        arrival_time_gaza = get_gaza_now().strftime("%H:%M")

        arrow = "🟢 CALL (صعود)" if result['type'] == "CALL" else "🔴 PUT (هبوط)"
        expiry_time = (result['time'] + timedelta(minutes=TRADE_DURATION_MINUTES * 2)).strftime("%H:%M")

        msg = (
            f"*🎯 إشارة M15 BOLLINGER TREND REVERSAL #{signal_num}*\n\n"
            f"📊 *الزوج:* `{result['symbol']}`\n"
            f"📈 *الاتجاه:* {arrow}\n"
            f"💰 *السعر الحالي:* `{result['price']:.5f}`\n\n"
            f"🎯 *منطقة الدخول:* لمس البولنجر باندز\n"
            f"🏷️ *فلتر الاتجاه (EMA200):* {'أعلى' if result['type']=='CALL' else 'أسفل'} المتوسط\n\n"
            f"⏱️ *التعليمات:*\n"
            f"✅ ادخل فوراَ (فرصة إحصائية عالية)\n\n"
            f"⌛ *انتهاء الصفقة:* {expiry_time} UTC\n"
            f"💰 *شرط الـ payout:* 85% فأعلى فقط\n\n"
            f"🕒 *وقت وصول الإشارة:* {arrival_time_gaza} (غزة)\n"
            f"📝 *ملاحظة:* استراتيجية M15 BB + EMA200 (Z-Score 3.37)"
        )

        mid = send_telegram(msg)
        if mid:
            sm.freeze_pair(sym, base_time=result['time'])
            sm.add_open_trade({
                "id": str(uuid.uuid4())[:8],
                "symbol": result['symbol'],
                "type": result['type'],
                "entry_price": result['price'],
                "entry_time": result['time'].isoformat(), # ← نحتفظ بوقت الفتح كما هو
                "msg_id": mid
            })
            log.info(f"Signal Sent & Frozen: {sym} {result['type']} (#{signal_num})")
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
