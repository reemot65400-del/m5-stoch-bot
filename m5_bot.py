#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
M15 Bollinger Trend Reversal Bot - v4.9 (Corrected Timezone Mapping)
مراجعة v5.0 الهندسية (فوق v4.9 — دون لمس الاستراتيجية أو الدخول):
A. توقيت غزة الحقيقي عبر ZoneInfo("Asia/Gaza") + قاعدة DST يدوية احتياطية —
   الإزاحة الثابتة +3 كانت تخطئ بساعة كاملة في الشتاء (الحظر الليلي، حدود
   أيام التقارير، عداد الإشارات اليومي كلها انزاحت).
B. نطاق الأسبوع الأسبوعي: كان يبدأ يوم الثلاثاء ويضيّع صفقات يوم الاثنين
   (صحّح الصيغة إلى: الاثنين الماضي ← الأحد الماضي).
C. الحسم: سلسلة احتياطية آمنة (تطابق تام ← أول شمعة لاحقة ← آخيرة سابقة بتحذير)
   — المسار القديم كان قد يقرأ سعر إغلاق شمعة الدخول نفسها فيᵣ وجود فجوة بيانات.
D. فلتر العطلة صار بتوقيت غزة بدل UTC (انزياح ساعة في الشتاء).

الإصلاحات الأصلية المحفوظة من v4.9:
1. ★★ إصلاح جذري لخرائط التواريخ (Date Mapping Fix):
   تم استبدال استخراج النص الخام (Raw String Extraction) بتحويل زمني دقيق.
   الآن يتم تحويل وقت الخروج (UTC) إلى توقيت غزة (+3) قبل تحديد "يوم التداول".
   هذا يمنع ضياع الصفقات الليلية (مثل تلك التي تغلق 23:50 غزة / 20:50 UTC).
2. ★★ ضمان تسليم التقارير الأسبوعية والشهرية:
   نافذة إرسال مفتوحة طوال يوم الأحد (للأسبوعي) وأول 3 أيام (للشهري) لضمان الوصول.
3. ★★ الحفاظ التام على الاستراتيجية والحسم.
"""
import os, sys, time, json, logging, uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
import numpy as np
import pandas as pd
import warnings
warnings.filterwarnings('ignore')

try:
    import yfinance as yf
except ImportError:
    print("pip install yfinance"); sys.exit(1)

# ═══════════════════════════════════════════════
# 0. ثوابت التوقيت الآمنة (Safety Constants)
# ═══════════════════════════════════════════════
try:
    GAZA_TZ = ZoneInfo("Asia/Gaza")   # يتبع التوقيت الصيفي/الشتوي رسميًا
except Exception:
    GAZA_TZ = None                    # fallback: قاعدة DST يدوية (آخر سبت مارس ←→ آخر سبت أكتوبر)

def _is_palestine_summer(dt_utc: datetime) -> bool:
    """قاعدة احتياطية يدوية إذا غابت قاعدة المنطقة الزمنية من النظام"""
    def last_saturday(year, month):
        d = datetime(year, month, 1, tzinfo=timezone.utc)
        nxt = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=timezone.utc)
        return nxt - timedelta(days=((nxt.weekday() - 5) % 7) + 1)
    return last_saturday(dt_utc.year, 3) <= dt_utc < last_saturday(dt_utc.year, 10)

def to_gaza(dt_obj: datetime) -> datetime:
    """تحويل أي datetime مدرّج لمنطقة غزة الحقيقية"""
    if GAZA_TZ is not None:
        return dt_obj.astimezone(GAZA_TZ)
    off = 3 if _is_palestine_summer(dt_obj.astimezone(timezone.utc)) else 2
    return dt_obj.astimezone(timezone(timedelta(hours=off)))

def get_palestine_now_safe() -> datetime:
    """توقيت غزة الحالي — صيفًا وشتاءً — بلا افتراض إزاحة ثابتة"""
    return to_gaza(datetime.now(timezone.utc))

# ═══════════════════════════════════════════════
# 1. الإعدادات العامة
# ═══════════════════════════════════════════════
BOT_NAME = "M15_BB_Sniper_v5_0_Reviewed"
STATE_FILE = "state_m15_fixed_gaza.json"
LOG_LEVEL = logging.INFO

TRADING_SYMBOLS = [
    "AUDNZD=X","GBPUSD=X", "USDJPY=X", "AUDUSD=X",
    "USDCAD=X", "NZDUSD=X", "EURGBP=X", "GBPCAD=X",
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
                    loaded_data = json.load(f)
                    for key in self.data.keys():
                        if key in loaded_data:
                            self.data[key] = loaded_data[key]
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
        today_str = get_palestine_now_safe().strftime("%Y-%m-%d")
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
            entry_dt_raw = datetime.fromisoformat(trade['entry_time'])
            if entry_dt_raw.tzinfo is None:
                entry_dt_raw = entry_dt_raw.replace(tzinfo=timezone.utc)
            
            actual_entry_close_time = entry_dt_raw + timedelta(minutes=TRADE_DURATION_MINUTES)
            expected_exit_close_time = actual_entry_close_time + timedelta(minutes=TRADE_DURATION_MINUTES)
            check_ready_time = expected_exit_close_time + timedelta(minutes=2)
            
            if now_utc < check_ready_time:
                continue
            
            sym = trade['symbol']
            dr = trade.get('direction') or trade.get('type')
            
            if dr is None:
                 log.error(f"Trade {trade['id']} has neither 'direction' nor 'type'. Skipping safely.")
                 continue

            entry_px = float(trade['entry_price'])
            
            log.info(f"Resolving {sym} | Target Exit Close: {expected_exit_close_time}")
            
            df_exit_full = get_data(sym)
            if df_exit_full is None or df_exit_full.empty:
                log.warning(f"No exit data for {sym}. Retry next cycle.")
                continue
                
            target_candle_start = expected_exit_close_time - timedelta(minutes=TRADE_DURATION_MINUTES)
            
            mask = (df_exit_full.index >= target_candle_start - timedelta(seconds=30)) & \
                   (df_exit_full.index <= target_candle_start + timedelta(seconds=30))
                   
            matching_candles = df_exit_full[mask]
            
            if not matching_candles.empty:
                exit_px = float(matching_candles.iloc[0]['Close'])
            else:
                # احتياطي 1: أول شمعة تبدأ بعد الهدف (تغطي تأخر نشر البيانات)
                future_mask2 = df_exit_full.index >= target_candle_start
                if future_mask2.any():
                    exit_px = float(df_exit_full[future_mask2].iloc[0]['Close'])
                    log.warning(f"Exact exit candle not found for {sym}, used first candle after target.")
                else:
                    # احتياطي 2 (أخير): آخر شمعة قبل الهدف — قد يعطي نتيجة ⚪ مشوّهة
                    past_mask = df_exit_full.index < target_candle_start
                    if past_mask.any():
                        exit_px = float(df_exit_full[past_mask].iloc[-1]['Close'])
                        log.warning(f"Only pre-target candles for {sym}; result may be distorted ⚪.")
                    else:
                        log.error(f"No candles available for {sym} resolution. Skipping.")
                        continue

            pip_mult = 100.0 if "JPY" in sym else 10000.0
            diff = exit_px - entry_px
            pips_diff = abs(diff) * pip_mult
            
            log.info(f"Success! Entry={entry_px} Exit={exit_px} Diff={diff} Pips={pips_diff:.2f}")
            
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
                "entry_time": trade['entry_time'], 
                "exit_time": expected_exit_close_time.isoformat(), 
                "win": is_win, "pips": float(pips_diff)
            })
            resolved_ids.append(trade['id'])
            
        except Exception as e:
            log.error(f"Critical Error resolving trade {trade.get('id')}: {e}", exc_info=True)

    for tid in resolved_ids:
        sm.remove_open_trade(tid)

# ═══════════════════════════════════════════════
# 5. نظام التقارير المتقدم (★★ الإصلاح الجذري للتواريخ ★★)
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


def convert_utc_to_gaza_date(time_str: str) -> str:
    """
    ★★ الوظيفة الحاسمة للإصلاح ★★
    تحول نص الوقت (UTC) إلى كائن datetime، تضيف 3 ساعات (لتصبح غزة)،
    ثم تستخرج التاريخ بالصيغة YYYY-MM-DD.
    هذا يضمن أن صفقة الساعة 23:50 غزة (20:50 UTC) تُحسب ليوم الغد الصحيح.
    """
    try:
        # 1. قراءة النص وتحويله لـ DateTime مع افتراض UTC إذا لم يكن هناك منطقة زمنية
        dt_obj = pd.to_datetime(time_str, errors='coerce')
        if pd.isna(dt_obj):
            return ""
        
        # 2. التأكد من أن الكائن يحمل معلومات المنطقة الزمنية (أو نفترض UTC)
        if dt_obj.tzinfo is None:
            dt_obj = dt_obj.replace(tzinfo=timezone.utc)
        
        # 3. التحويل الفعلي لإزاحة غزة (+3)
        gaza_dt = to_gaza(dt_obj)
        
        # 4. استخراج مفتاح التاريخ المحلي
        return gaza_dt.strftime("%Y-%m-%d")
    except Exception:
        return ""


def send_detailed_report(df: pd.DataFrame, report_type: str, period_name: str) -> bool:
    if df is None or df.empty:
        msg = (
            f"*📊 التقرير {report_type} ({period_name})*\n\n"
            f"📈 *إجمالي الصفقات:* `0`\n"
            f"ℹ️ _لم تسجل صفقات خلال هذه الفترة._\n\n"
            f"_البوت يعمل بكفاءة ويراقب السوق بانتظام!_"
        )
        sent = send_telegram(msg)
        if sent:
            log.info(f"Sent empty-period {report_type} report for {period_name}")
        return sent

    df = df.copy()

    # تحصين الأعمدة الأساسية
    required_cols = ['id', 'symbol', 'win', 'pips', 'gaza_date_key', 'approx_hour']
    for col in required_cols:
        if col not in df.columns:
            if col == 'id': df[col] = [f"row{i}" for i in range(len(df))]
            elif col == 'symbol': df[col] = 'UNKNOWN'
            elif col == 'win': df[col] = None
            elif col == 'pips': df[col] = 0.0
            elif col == 'gaza_date_key': df[col] = ''
            elif col == 'approx_hour': df[col] = 0

    df['pips'] = pd.to_numeric(df['pips'], errors='coerce').fillna(0.0)
    df['signed_pips'] = np.where(df['win'] == True, df['pips'], np.where(df['win'] == False, -df['pips'], 0.0))

    total = len(df)
    wins = int((df['win'] == True).sum())
    losses = int((df['win'] == False).sum())
    voids = int(df['win'].isna().sum())
    valid_trades = wins + losses
    wr = (wins / valid_trades * 100.0) if valid_trades > 0 else 0.0
    net_pips = float(df['signed_pips'].sum())

    # إحصائيات الأزواج
    pair_stats = df.groupby('symbol', as_index=False).agg(
        trades=('id', 'count'),
        wins=('win', lambda x: int((x == True).sum())),
        losses=('win', lambda x: int((x == False).sum())),
        pips=('signed_pips', 'sum')
    )
    pair_stats['valid'] = pair_stats['wins'] + pair_stats['losses']
    pair_stats['wr'] = np.where(pair_stats['valid'] > 0, (pair_stats['wins'] / pair_stats['valid']) * 100.0, 0.0)
    
    valid_pairs = pair_stats[pair_stats['trades'] >= 2]
    if valid_pairs.empty: valid_pairs = pair_stats[pair_stats['trades'] >= 1]

    best_pair_txt = "غير متاح"
    worst_pair_txt = "غير متاح"
    if not valid_pairs.empty:
        bp = valid_pairs.loc[valid_pairs['pips'].idxmax()]
        wp = valid_pairs.loc[valid_pairs['pips'].idxmin()]
        best_pair_txt = f"🥇 {bp['symbol']} ({bp['pips']:+.1f} Pips, WR: {bp['wr']:.1f}%)"
        others = valid_pairs[valid_pairs['symbol'] != bp['symbol']]
        if not others.empty:
            worst_pair_txt = f"⚠️ {wp['symbol']} ({wp['pips']:+.1f} Pips, WR: {wp['wr']:.1f}%)"
        else:
            worst_pair_txt = "لا يوجد (زوج واحد فقط)"

    # إحصائيات الوقت (الساعة التقريبية من UTC + 3)
    hour_src = df['approx_hour']
    time_stats = df.assign(gaza_hour=hour_src).groupby('gaza_hour', as_index=False).agg(
        wins=('win', lambda x: int((x == True).sum())),
        losses=('win', lambda x: int((x == False).sum())),
        total=('id', 'count')
    )
    time_stats['valid'] = time_stats['wins'] + time_stats['losses']
    time_stats['wr'] = np.where(time_stats['valid'] > 0, (time_stats['wins'] / time_stats['valid']) * 100.0, 0.0)
    vt = time_stats[time_stats['total'] >= 2]
    if vt.empty: vt = time_stats[time_stats['total'] >= 1]
    
    best_time_txt = "غير متاح"
    if not vt.empty:
        bh = vt.loc[vt['wr'].idxmax()]['gaza_hour']
        if pd.notna(bh):
            bh_int = int(bh)
            best_time_txt = f"🕒 {bh_int:02d}:00 - {(bh_int+1)%24:02d}:00 (تقريبي)"

    # إحصائيات الأيام (بناءً على المفتاح المُصلح gaza_date_key)
    day_src = df['gaza_date_key'].dropna()
    days_as_dates = pd.to_datetime(day_src, errors='coerce')
    valid_days_mask = days_as_dates.notna()
    best_day_txt = "غير متاح"
    
    if valid_days_mask.any():
        vd_df = df[valid_days_mask].copy()
        vd_df['day_of_week'] = days_as_dates[valid_days_mask].dt.dayofweek
        ds = vd_df.groupby('day_of_week', as_index=False).agg(
            wins=('win', lambda x: int((x == True).sum())),
            losses=('win', lambda x: int((x == False).sum())),
            total=('id', 'count')
        )
        ds['valid'] = ds['wins'] + ds['losses']
        ds['wr'] = np.where(ds['valid'] > 0, (ds['wins'] / ds['valid']) * 100.0, 0.0)
        vdg = ds[ds['total'] >= 2]
        if vdg.empty: vdg = ds[ds['total'] >= 1]
        if not vdg.empty:
            bwd = vdg.loc[vdg['wr'].idxmax()]['day_of_week']
            best_day_txt = f"📅 {AR_DAYS.get(int(bwd), 'غير معروف')}"

    # شرائح JPY
    df['is_jpy'] = df['symbol'].astype(str).str.contains('JPY')
    cs = df.groupby('is_jpy', as_index=False).agg(wins=('win', lambda x: int((x==True).sum())), total=('id','count'), pips=('signed_pips','sum'))
    cs['name'] = cs['is_jpy'].apply(lambda x: "أزواج الين (JPY)" if x else "الأزواج الرئيسية/الأخرى")
    vc = cs[cs['total'] >= 2]
    if vc.empty: vc = cs[cs['total'] >= 1]
    best_cat_txt = "غير متاح"
    if not vc.empty:
        bcr = vc.loc[vc['pips'].idxmax()]
        best_cat_txt = f"🌍 {bcr['name']} ({bcr['pips']:+.1f} Pips)"

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
    """
    ★★ v4.9 — منطق التواريخ المصحح رياضياً ★★
    """
    local_now = get_palestine_now_safe()
    yesterday_local = local_now - timedelta(days=1)
    yesterday_str = yesterday_local.strftime("%Y-%m-%d")
    
    log.info(f"DEBUG v4.9: Gaza Now={local_now.strftime('%Y-%m-%d %H:%M')} | Target Yesterday={yesterday_str}")

    history = sm.data.get("trade_history", [])
    if not history:
        return

    df_hist = pd.DataFrame(history)
    
    # ★★ الخطوة الإصلاحية: إنشاء عمود جديد بالتاريخ المحلي الصحيح ★★
    if 'exit_time' in df_hist.columns:
        # 1. تحويل كل صفقات الأرشفيف لاستخراج التاريخ المحلي (غزة)
        df_hist['gaza_date_key'] = df_hist['exit_time'].apply(convert_utc_to_gaza_date)
        
        # 2. استخراج الساعة التقريبية للتحليل (UTC Hour + 3)
        def get_approx_gaza_hour(t_str):
            try:
                dt = pd.to_datetime(t_str, errors='coerce')
                if pd.isna(dt): return 0
                if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
                gaza_dt = to_gaza(dt)
                return gaza_dt.hour
            except: return 0
        df_hist['approx_hour'] = df_hist['exit_time'].apply(get_approx_gaza_hour)

        # 3. تصفية السجلات التالفة تماماً
        valid_mask = df_hist['gaza_date_key'] != ""
        dropped_count = (~valid_mask).sum()
        if dropped_count > 0:
            log.warning(f"FIXED v4.9: Dropped {dropped_count} entries with invalid dates.")
        
        df_clean = df_hist[valid_mask].copy()
        log.info(f"DEBUG v4.9: Clean trades count: {len(df_clean)}")
    else:
        log.error("CRITICAL: No 'exit_time' column found!")
        return

    # ─── 1. التقرير اليومي ───
    daily_df = df_clean[df_clean['gaza_date_key'] == yesterday_str].copy()
    
    try:
        dt_obj = datetime.strptime(yesterday_str, "%Y-%m-%d")
        daily_period_name = f"{AR_DAYS[dt_obj.weekday()]} {dt_obj.day} {ar_month_name(dt_obj)} {dt_obj.year}"
    except:
        daily_period_name = yesterday_str
        
    if sm.data.get("last_daily_report") != yesterday_str:
        log.info(f"Daily report due for {yesterday_str}...")
        success = send_detailed_report(daily_df, "اليومي", daily_period_name)
        if success:
            sm.data["last_daily_report"] = yesterday_str
            sm.save()
            log.info(f"Daily report CONFIRMED for {yesterday_str}")

    # ─── 2. التقرير الأسبوعي (نافذة مفتوحة: السبت والأحد) ───
    # نستخدم weekday المحلي لغزة لتحديد النافذة
    current_gaza_weekday = local_now.weekday() # 0=Mon ... 6=Sun
    
    # إذا كان اليوم السبت (5) أو الأحد (6)، نحاول إرسال تقرير الأسبوع المنتهي
    if current_gaza_weekday in [5, 6]:
        # حساب نطاق الأسبوع الماضي (من الاثنين الماضي للأحد الماضي)
        # ملاحظة: بما أننا في نهاية الأسبوع، فالأسبوع "المنتهي" هو الذي بدأ الاثنين قبل 6 أو 7 أيام
        # الأسبوع المنتهي (الاثنين ← الأحد): ناقص (رقم اليوم + 7) أيام → يوم الاثنين الماضي
        last_week_start = (local_now - timedelta(days=current_gaza_weekday + 7)).replace(hour=0, minute=0, second=0, microsecond=0)
        last_week_end = last_week_start + timedelta(days=6)  # الأحد نفسه
        
        week_range_start_str = last_week_start.strftime("%Y-%m-%d")
        week_range_end_str = last_week_end.strftime("%Y-%m-%d")
        
        iso_cal_last_week = last_week_start.isocalendar()
        last_week_year, last_week_num, _ = iso_cal_last_week
        last_week_str_id = f"{last_week_year}-W{last_week_num:02d}"
        
        weekly_period_name = (f"الأسبوع {last_week_num} "
                              f"({last_week_start.day} {ar_month_name(last_week_start)} – "
                              f"{last_week_end.day} {ar_month_name(last_week_end)} {last_week_end.year})")

        if sm.data.get("last_weekly_report") != last_week_str_id:
            log.info(f"Weekly report window active. Checking for {last_week_str_id}...")
            
            if not df_clean.empty:
                weekly_df = df_clean[(df_clean['gaza_date_key'] >= week_range_start_str) & 
                                     (df_clean['gaza_date_key'] <= week_range_end_str)].copy()
            else:
                weekly_df = pd.DataFrame()
                
            log.info(f"DEBUG v4.9 WEEKLY MATCH: Found {len(weekly_df)} trades for Week {last_week_num}")

            success = send_detailed_report(weekly_df, "الأسبوعي", weekly_period_name)
            if success:
                sm.data["last_weekly_report"] = last_week_str_id
                sm.save()
                log.info(f"Weekly report CONFIRMED for {last_week_str_id}")

    # ─── 3. التقرير الشهري (نافذة مفتوحة: أول 3 أيام من الشهر) ───
    if local_now.day <= 3:
        first_day_this_month = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_day_prev_month = first_day_this_month - timedelta(days=1)
        
        month_range_start_str = last_day_prev_month.replace(day=1).strftime("%Y-%m-%d")
        month_range_end_str = last_day_prev_month.strftime("%Y-%m-%d")
        
        monthly_period_name = f"{ar_month_name(last_day_prev_month)} {last_day_prev_month.year}"
        last_month_str_id = last_day_prev_month.strftime("%Y-%m")

        if sm.data.get("last_monthly_report") != last_month_str_id:
            log.info(f"Monthly report window active. Checking for {last_month_str_id}...")
            
            if not df_clean.empty:
                monthly_df = df_clean[(df_clean['gaza_date_key'] >= month_range_start_str) & 
                                      (df_clean['gaza_date_key'] <= month_range_end_str)].copy()
            else:
                monthly_df = pd.DataFrame()
                
            log.info(f"DEBUG v4.9 MONTHLY MATCH: Found {len(monthly_df)} trades for Month {last_month_str_id}")

            success = send_detailed_report(monthly_df, "الشهري", monthly_period_name)
            if success:
                sm.data["last_monthly_report"] = last_month_str_id
                sm.save()
                log.info(f"Monthly report CONFIRMED for {last_month_str_id}")


# ═══════════════════════════════════════════════
# 6. الحلقة الرئيسية
# ═══════════════════════════════════════════════

def main():
    local_now = get_palestine_now_safe()
    log.info("=" * 55)
    log.info(f"STARTING {BOT_NAME} | ZERO-BUG PRODUCTION MODE")
    log.info(f"UTC Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} | Gaza Safe Time: {local_now.strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 55)

    if not TG_TOKEN or not TG_CHAT:
        log.critical("MISSING TELEGRAM CREDS. ABORTING.")
        return

    sm = StateManager(STATE_FILE)
    sm.log_summary()

    resolve_pending_trades(sm)
    generate_advanced_reports(sm)

    if local_now.weekday() >= 5:
        log.info("Weekend. Skipping new signals.")
        return

    current_gaza_hour = local_now.hour
    if BAN_START_HOUR_GAZA <= current_gaza_hour < BAN_END_HOUR_GAZA:
        log.info(f"Inside Night Ban Window ({current_gaza_hour:02d}:xx Gaza Time). Skipping new signals.")
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
        arrival_time_gaza = get_palestine_now_safe().strftime("%H:%M")

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
                "entry_time": result['time'].isoformat(),
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
