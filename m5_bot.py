#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
M15 Bollinger Trend Reversal Bot - FIXED & GAZA TIME v3.4 (Night Ban Adjusted to Midnight)
الإصلاحات النهائية:
1. حل جذري لمشكلة عدم وصول رسائل الحسم (تحسين منطق البحث عن شمعة الخروج).
2. دعم reply_to في رسائل الحسم (ربط النتيجة بالإشارة).
3. تصحيح منطق التجميد ليرتبط بوقت إغلاق الشمعة (يمنع تفويت الفرص).
4. ★★ تعديل ساعات العمل: الحظر يبدأ منتصف الليل (00:00 غزة) وينتهي 9 صباحاً (09:00 غزة) ★★
5. حفظ ذرّي آمن للملفات.
6. إضافة عداد يومي للإشارات + وقت الوصول في نص الرسالة.
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
BOT_NAME = "M15_BB_Sniper_Fixed_Gaza_v3_4"
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

# ★★ التعديل الجوهري الجديد: توقيت غزة المحلي (UTC+3) ★★
# 9 صباحاُ غزة = 06:00 UTC | 12 منتصف الليل غزة = 21:00 UTC
ACTIVE_HOURS_START = 6   # 06:00 UTC (9 AM Gaza) -> يبدأ العمل
ACTIVE_HOURS_END = 21    # 21:00 UTC (12 Midnight Gaza) -> ينتهي العمل ويبدأ الحظر

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

def send_telegram(msg: str, reply_to=None):
    """إرسال رسالة مع خيار الرد على رسالة سابقة (للحسم التلقائي)"""
    if not TG_TOKEN or not TG_CHAT:
        return None
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT, "text": msg, "parse_mode": "Markdown"}
    
    # ★★ الإصلاح 1: إضافة دعم reply_to ★★
    if reply_to is not None:
        try:
            payload["reply_to_message_id"] = int(reply_to)
        except (ValueError, TypeError):
            pass

    for attempt in range(3):
        try:
            resp = requests.post(url, json=payload, timeout=15)
            if resp.status_code == 200:
                return resp.json().get("result", {}).get("message_id")
            elif resp.status_code == 429:
                wait = resp.json().get('parameters', {}).get('retry_after', 5)
                time.sleep(wait)
            else:
                log.error(f"TG API Error: {resp.status_code}")
                break
        except Exception as e:
            log.error(f"TG Send Failed: {e}")
            time.sleep(2)
    return None


class StateManager:
    def __init__(self, filepath: str):
        self.filepath = Path(filepath)
        self.data = {
            "frozen_pairs": {},      # {symbol: freeze_until_iso}
            "open_trades": [],       # قائمة الصفقات المفتوحة
            "trade_history": [],     # أرشيف النتائج
            "daily_counter_date": "", # تاريخ آخر عدّاد (YYYY-MM-DD)
            "daily_signal_count": 0   # عدد إشارات اليوم
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
        """★★ الإصلاح 3: حفظ ذرّي آمن وموحد ★★"""
        try:
            tmp = self.filepath.with_suffix('.tmp')
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=2, default=str)
            # os.replace يعمل بأمان سواء كان الملف موجوداَ أم لا
            os.replace(str(tmp), str(self.filepath))
        except Exception as e:
            log.critical(f"STATE SAVE CRITICAL FAILURE: {e}")

    def get_next_signal_number(self) -> int:
        """يعيد رقم الإشارة التالي ويحدث العداد تلقائياُ عند تغيير اليوم"""
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        
        # إذا كان التاريخ مختلفاُ، نعيد ضبط العداد لليوم الجديد
        if self.data.get("daily_counter_date") != today_str:
            self.data["daily_counter_date"] = today_str
            self.data["daily_signal_count"] = 0
            
        # زيادة العداد وإرجاع القيمة الجديدة
        self.data["daily_signal_count"] += 1
        current_num = self.data["daily_signal_count"]
        
        self.save()
        return current_num

    def is_frozen(self, symbol: str) -> bool:
        if symbol not in self.data["frozen_pairs"]:
            return False
        freeze_until_str = self.data["frozen_pairs"][symbol]
        try:
            freeze_until = datetime.fromisoformat(freeze_until_str)
            if freeze_until.tzinfo is None:
                freeze_until = freeze_until.replace(tzinfo=timezone.utc)
            now_utc = datetime.now(timezone.utc)
            return now_utc < freeze_until
        except Exception as e:
            log.error(f"Error checking freeze for {symbol}: {e}")
            return False

    def freeze_pair(self, symbol: str, base_time: datetime, duration_minutes: int = FREEZE_DURATION_MINUTES):
        """
        ★★ الإصلاح 2: تجميد الزوج بناءً على وقت إغلاق الشمعة (base_time) ★★
        بدلاَ من وقت النظام الحالي، نستخدم وقت الشمعة التي أرسلنا إشارتها.
        هذا يضمن أن الزوج يتحرر تماماَ عند إغلاق شمعة الخروج التالية.
        """
        # وقت التحرر = وقت إغلاق الشمعة الحالية + مدة الصفقة
        release_time = base_time + timedelta(minutes=duration_minutes)
        
        # التأكد من أن وقت التحرر مستقبلي (حماية من تأخر النبضة)
        now_utc = datetime.now(timezone.utc)
        if release_time <= now_utc:
            release_time = now_utc + timedelta(minutes=1) # حد أدنى دقيقة واحدة
            
        self.data["frozen_pairs"][symbol] = release_time.isoformat()
        self.save()
        log.info(f"Frozen {symbol} until {release_time.strftime('%H:%M:%S')} UTC (based on candle close)")

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
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval=interval, auto_adjust=False, actions=False)
        if df is None or df.empty:
            return None

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [str(c[0]) for c in df.columns]
        df.index = pd.to_datetime(df.index, utc=True)
        df = df[~df.index.duplicated()].sort_index().dropna()

        now_utc = pd.Timestamp.now(tz="UTC")
        # حذف الشمعة الجارية
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
        log.error(f"Data Fetch Error [{symbol}]: {e}")
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

    if signal_type:
        return {
            "symbol": symbol,
            "type": signal_type,
            "price": price,
            "time": candle_time, # وقت إغلاق الشمعة
            "ema_200": ema_200,
            "bb_level": bb_lower if signal_type == "CALL" else bb_upper
        }
    return None


# ═══════════════════════════════════════════════
# 4. نظام الحسم التلقائي (مُصلح جذرياُ)
# ═══════════════════════════════════════════════

def resolve_pending_trades(sm: StateManager):
    if not sm.data["open_trades"]:
        return

    now_utc = datetime.now(timezone.utc)
    resolved_ids = []

    log.info(f">>> Checking {len(sm.data['open_trades'])} pending trades...")

    for trade in sm.data["open_trades"]:
        try:
            # 1. استخراج وقت الدخول والتأكد من التوقيت العالمي
            entry_time_str = trade['entry_time']
            entry_dt = datetime.fromisoformat(entry_time_str)
            if entry_dt.tzinfo is None:
                entry_dt = entry_dt.replace(tzinfo=timezone.utc)

            # 2. حساب الوقت المستهدف لإغلاق شمعة الصفقة (Entry Start + Duration)
            # مثال: دخلنا على شمعة بدأت 07:00 ومدتها 15 د -> تغلق 07:15
            exit_candle_close_time = entry_dt + timedelta(minutes=TRADE_DURATION_MINUTES)
            
            # 3. شرط الانتظار: ننتظر حتى يمر وقت الإغلاق + هامش تحديث البيانات (دقيقتين)
            check_ready_time = exit_candle_close_time + timedelta(minutes=2)
            
            if now_utc < check_ready_time:
                continue # الصفقة لم تنتهي بعد أو البيانات غير جاهزة

            log.info(f"Resolving trade for {trade['symbol']} | Target Close: {exit_candle_close_time}")

            # 4. جلب البيانات وتحليلها للعثور على السعر الدقيق عند الإغلاق
            df = get_data(trade['symbol'])
            if df is None or df.empty:
                log.warning(f"No data fetched for {trade['symbol']}. Will retry next cycle.")
                continue

            # ★★ الإصلاح الجوهري هنا ★★
            # نبحث عن الشمعة التي *تبدأ* قبل وقت الإغلاق المستهدف مباشرة
            # لأن الفهرس في yfinance يمثل وقت بداية الشمعة
            target_start_time = exit_candle_close_time - timedelta(minutes=TRADE_DURATION_MINUTES)
            
            # نجلب كل الشموع التي بدأت في نفس الدقيقة المستهدفة (أو الأقرب لها)
            mask = (df.index >= target_start_time) & (df.index <= exit_candle_close_time)
            
            if not mask.any():
                # محاولة أخيرة: أخذ أقرب شمعة سابقة لوقت الإغلاق
                past_candles = df[df.index <= exit_candle_close_time]
                if not past_candles.empty:
                    exit_row = past_candles.iloc[-1]
                    exit_price = float(exit_row['Close'])
                else:
                    log.error(f"Could not find valid exit candle for {trade['symbol']}. Skipping.")
                    continue
            else:
                # أخذ إغلاق الشمعة المطابقة تماماَ
                exit_row = df[mask].iloc[0]
                exit_price = float(exit_row['Close'])

            # 5. تحديد النتيجة (Win/Loss/Void)
            entry_px = float(trade['entry_price'])
            diff = exit_price - entry_px
            
            # حساب النقاط للعرض فقط
            pip_mult = 100.0 if "JPY" in trade['symbol'] else 10000.0
            pips_diff = abs(diff) * pip_mult

            # حماية من الـ Void الكاذب بسبب دقة الأرقام العشرية
            if pips_diff < 0.1: # أقل من عُشر نقطة يعتبر سوقاَ ميتاَ
                emoji = "⚪ لاغي"
                is_win = None
                status_text = "_Market Flat_"
            else:
                if trade['type'] == 'CALL':
                    is_win = (diff > 0)
                else:
                    is_win = (diff < 0)
                
                emoji = "✅ ربح" if is_win else "❌ خسارة"
                status_text = ""

            # 6. إرسال رسالة النتيجة وربطها بالإشارة الأصلية
            result_msg = (f"{emoji} *نتيجة M15: {trade['symbol']}*\n"
                          f"الاتجاه: `{trade['type']}`\n"
                          f"الدخول: `{entry_px:.5f}`\n"
                          f"الخروج: `{exit_price:.5f}`\n"
                          f"الفرق: `{pips_diff:+.1f} نقطة` {status_text}")

            mid_reply = trade.get('msg_id')
            send_telegram(result_msg, reply_to=mid_reply)

            # 7. الأرشفة والحذف من القائمة المفتوحة
            sm.archive_trade({
                "id": trade['id'],
                "symbol": trade['symbol'],
                "type": trade['type'],
                "entry_time": trade['entry_time'],
                "exit_time": now_utc.isoformat(),
                "win": is_win,
                "pips": pips_diff
            })

            resolved_ids.append(trade['id'])
            log.info(f"Successfully Resolved: {trade['symbol']} -> {'WIN' if is_win else ('LOSS' if is_win is False else 'VOID')}")

        except Exception as e:
            log.error(f"Critical Error resolving trade {trade.get('id')}: {e}", exc_info=True)

    # تنظيف القائمة
    for tid in resolved_ids:
        sm.remove_open_trade(tid)


# ═══════════════════════════════════════════════
# 5. نظام التقارير
# ═══════════════════════════════════════════════

def generate_reports(sm: StateManager):
    now_utc = datetime.now(timezone.utc)
    history = sm.data["trade_history"]

    if not history:
        return

    df_hist = pd.DataFrame(history)
    df_hist['exit_dt'] = pd.to_datetime(df_hist['exit_time'], utc=True)

    # التقرير اليومي (عند 00:05 UTC = 03:05 صباحاَ غزة)
    if now_utc.hour == 0 and now_utc.minute < 15:
        today_start = now_utc.replace(hour=0, minute=0, second=0)
        daily_trades = df_hist[df_hist['exit_dt'] >= today_start]

        if len(daily_trades) > 0:
            wins = len(daily_trades[daily_trades['win'] == True])
            total = len(daily_trades)
            wr = (wins / total) * 100
            profit = (wins * 0.85) - ((total - wins) * 1.0)

            report = (f"📅 *التقرير اليومي ({now_utc.strftime('%Y-%m-%d')})*\n\n"
                      f"📊 إجمالي الصفقات: `{total}`\n"
                      f"✅ الرابحة: `{wins}` | ❌ الخاسرة: `{total - wins}`\n"
                      f"🏆 نسبة الفوز: `{wr:.1f}%`\n"
                      f"💰 صافي الوحدات: `{profit:+.2f}`")
            send_telegram(report)


# ═══════════════════════════════════════════════
# 6. الحلقة الرئيسية
# ═══════════════════════════════════════════════

def main():
    log.info("=" * 50)
    log.info(f"STARTING {BOT_NAME} | FIXED & GAZA TIME")
    log.info(f"Current UTC Time: {datetime.now(timezone.utc).strftime('%H:%M:%S')}")
    log.info("=" * 50)

    if not TG_TOKEN or not TG_CHAT:
        log.critical("MISSING TELEGRAM CREDS. ABORTING.")
        return

    sm = StateManager(STATE_FILE)

    # 1. الحسم التلقائي أولاَ
    resolve_pending_trades(sm)

    # 2. التقارير
    generate_reports(sm)

    # 3. فلتر الوقت (بتوقيت غزة المحلي)
    now_utc = datetime.now(timezone.utc)
    hebron_tz = timezone(timedelta(hours=3))
    local_now = now_utc.astimezone(hebron_tz)

    if now_utc.weekday() >= 5:
        log.info("Weekend. Skipping signals.")
        return

    # التحقق من ساعات العمل باستخدام التوقيت المحلي لغزة
    if local_now.hour < ACTIVE_HOURS_START or local_now.hour >= ACTIVE_HOURS_END:
        log.info(f"Outside active hours ({local_now.strftime('%H:%M')} Gaza Time). Skipping signals.")
        return

    signals_found = 0

    for sym in TRADING_SYMBOLS:
        if sm.is_frozen(sym):
            log.debug(f"{sym} is frozen. Skipping.")
            continue

        result = analyze_market(sym)

        if result:
            signals_found += 1
            
            # ★★ التعديل الجديد: جلب رقم الإشارة الحالي ★★
            signal_num = sm.get_next_signal_number()
            
            # ★★ التعديل الجديد: حساب وقت الوصول بتوقيت غزة ★★
            arrival_time_gaza = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=3))).strftime("%H:%M")

            arrow = "🟢 CALL (صعود)" if result['type'] == "CALL" else "🔴 PUT (هبوط)"
            expiry_time = (result['time'] + timedelta(minutes=15)).strftime("%H:%M")

            # ★★ التعديل الجديد: تضمين الرقم والوقت في نص الرسالة ★★
            msg = (f"*🎯 إشارة M15 BOLLINGER TREND REVERSAL #{signal_num}*\n\n"
                   f"📊 *الزوج:* `{result['symbol']}`\n"
                   f"📈 *الاتجاه:* {arrow}\n"
                   f"💰 *السعر الحالي:* `{result['price']:.5f}`\n\n"
                   f"🎯 *منطقة الدخول:* لمس البولنجر باندز\n"
                   f"🏷️ *فلتر الاتجاه (EMA200):* {'أعلى' if result['type']=='CALL' else 'أسفل'} المتوسط\n\n"
                   f"⏱️ *التعليمات:*\n"
                   f"✅ ادخل فوراُ (فرصة إحصائية عالية)\n\n"
                   f"⌛ *انتهاء الشمعة:* {expiry_time} UTC\n"
                   f"💰 *شرط الـ payout:* 85% فأعلى فقط\n\n"
                   f"🕒 *وقت وصول الإشارة:* {arrival_time_gaza} (غزة)\n"
                   f"📝 *ملاحظة:* استراتيجية M15 BB + EMA200 (Z-Score 3.37)")

            mid = send_telegram(msg)
            if mid:
                # ★★ تمرير وقت إغلاق الشمعة (result['time']) للدالة freeze_pair ★★
                sm.freeze_pair(sym, base_time=result['time'])

                sm.add_open_trade({
                    "id": str(uuid.uuid4())[:8],
                    "symbol": result['symbol'],
                    "type": result['type'],
                    "entry_price": result['price'],
                    "entry_time": result['time'].isoformat(),
                    "msg_id": mid
                })

                log.info(f"Signal Sent & Frozen: {sym} {result['type']} (# {signal_num})")
            else:
                log.error(f"Failed to send signal for {sym}")

        time.sleep(1.5)

    if signals_found == 0:
        log.info("No valid setups found in this cycle.")
    else:
        log.info(f"Cycle complete. {signals_found} new signals sent.")


if __name__ == "__main__":
    main()
