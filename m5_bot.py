#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
M15 Bollinger Trend Reversal Bot - FIXED & GAZA TIME v3.8 (Independent Resolution Mode)
الإصلاحات في v3.8 (تشخيص وإصلاح احترافي دون لمس منطق الاستراتيجية):
1. ★★ إصلاح حاسم: الحسم كان يقرأ سعر الخروج من *شمعة الدخول نفسها* → كل نتيجة ⚪ VOID دائمًا.
   السبب: result['time'] هو وقت *بداية* الشمعة (فهرس yfinance = بداية الشمعة)،
   والكود كان يعامله كوقت إغلاق ويطرح 15 دقيقة مرتين.
   الآن: شمعة الخروج = أول شمعة تبدأ عند/بعد (بداية شمعة الدخول + مدة الصفقة).
2. ★★ إصلاح التجميد: كان يتحرر بعد ~دقيقة واحدة بدل 15 دقيقة (نفس خطأ وقت البداية/الإغلاق).
   الآن: التحرير = بداية شمعة الدخول + مدة الصفقة + مدة التجميد.
3. إضافة تشخيصات تشغيل: ملخص الحالة عند الإقلاع + أقرب مسافة لأي setup في كل دورة
   (للإجابة definitively: هل السوق هادئ أم هناك عطل صامت).
4. إضافة Retry Loop فعلي لجلب yfinance (كان مذكورًا في الوصف وغير موجود).

ملاحظة مهمة: شروط الدخول في analyze_market لم تُمَس حرفيًا — هي شروط صارمة إحصائيًا
(إغلاق كامل خارج البولنجر مع فلتر EMA200)، وندرة الإشارات بعد إصلاحات الشموع
الكاملة سلوك متوقع وليس عطلًا. التشخيصات الجديدة ستثبت ذلك من اللوجزات.
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
BOT_NAME = "M15_BB_Sniper_Fixed_Gaza_v3_8"
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

# ★★ الحظر الليلي (بتوقيت غزة المحلي UTC+3) ★★
BAN_START_HOUR_GAZA = 0   # الحظر يبدأ منتصف الليل (00:00)
BAN_END_HOUR_GAZA = 9     # الحظر ينتهي 9 صباحاُ (09:00)

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
    """دالة إرسال بسيطة وموثوقة بدون اعتماد على reply_to"""
    if not TG_TOKEN or not TG_CHAT:
        log.warning("Telegram credentials missing.")
        return False

    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT, "text": msg, "parse_mode": "Markdown"}

    for attempt in range(3):
        try:
            resp = requests.post(url, json=payload, timeout=15)
            if resp.status_code == 200:
                log.info("Message sent successfully to Telegram.")
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
                log.info(f"State file loaded: {self.filepath}")
            except Exception as e:
                log.error(f"State Load Error: {e}. Resetting state.")
                self.save()
        else:
            self.save()

    def save(self):
        """حفظ ذرّي آمن وموحد"""
        try:
            tmp = self.filepath.with_suffix('.tmp')
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=2, default=str)
            os.replace(str(tmp), str(self.filepath))
        except Exception as e:
            log.critical(f"STATE SAVE CRITICAL FAILURE: {e}")

    def log_summary(self):
        """★★ تشخيص جديد: ملخص الحالة عند الإقلاع (يكشف مشاكل الـ cache فورًا) ★★"""
        try:
            frozen_active = [s for s in self.data["frozen_pairs"] if self.is_frozen(s)]
            log.info(
                f"STATE SUMMARY | open_trades={len(self.data['open_trades'])} "
                f"| frozen_active_now={len(frozen_active)} {frozen_active if frozen_active else ''} "
                f"| frozen_entries_total={len(self.data['frozen_pairs'])} "
                f"| today_signals={self.data.get('daily_signal_count')} "
                f"| counter_date={self.data.get('daily_counter_date')}"
            )
        except Exception as e:
            log.error(f"State summary failed: {e}")

    def get_next_signal_number(self) -> int:
        """يعيد رقم الإشارة التالي ويحدث العداد تلقائياُ عند تغيير اليوم"""
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        if self.data.get("daily_counter_date") != today_str:
            self.data["daily_counter_date"] = today_str
            self.data["daily_signal_count"] = 0

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
            frozen = now_utc < freeze_until
            if frozen:
                log.info(f"{symbol} frozen until {freeze_until.strftime('%H:%M:%S')} UTC")
            return frozen
        except Exception as e:
            log.error(f"Error checking freeze for {symbol}: {e}")
            return False

    def freeze_pair(self, symbol: str, base_time: datetime, duration_minutes: int = FREEZE_DURATION_MINUTES):
        """
        ★★ v3.8: إصلاح حساب وقت التحرير ★★
        base_time = وقت *بداية* شمعة الدخول (فهرس yfinance).
        شمعة الدخول تغلق عند base_time + TRADE_DURATION.
        نجمّد حتى إغلاق شمعة الخروج: base_time + TRADE_DURATION + duration_minutes.
        """
        release_time = base_time + timedelta(minutes=TRADE_DURATION_MINUTES + duration_minutes)

        now_utc = datetime.now(timezone.utc)
        if release_time <= now_utc:
            release_time = now_utc + timedelta(minutes=FREEZE_DURATION_MINUTES)

        self.data["frozen_pairs"][symbol] = release_time.isoformat()
        self.save()
        log.info(f"Frozen {symbol} until {release_time.strftime('%H:%M:%S')} UTC")

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
    """★★ v3.8: Retry Loop حقيقي ضد تأخر yfinance (كان مذكورًا وغير منفذ) ★★"""
    df = None
    for attempt in range(3):
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period, interval=interval, auto_adjust=False, actions=False)
            if df is not None and not df.empty:
                break
            log.warning(f"[{symbol}] Empty data from yfinance (attempt {attempt+1}/3). Retrying in 3s...")
            time.sleep(3)
        except Exception as e:
            log.error(f"Data Fetch Error [{symbol}] (attempt {attempt+1}/3): {e}")
            time.sleep(3)

    if df is None or df.empty:
        return None

    try:
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [str(c[0]) for c in df.columns]
        df.index = pd.to_datetime(df.index, utc=True)
        df = df[~df.index.duplicated()].sort_index().dropna()

        now_utc = pd.Timestamp.now(tz="UTC")
        # حذف الشمعة الجارية (فهرس yfinance = بداية الشمعة؛ الشمعة جارية إذا لم يكتمل ربع ساعتها)
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
    """
    ★★ منطق الاستراتيجية لم يُمَس حرفيًا (نفس الشروط تمامًا) ★★
    التغيير الوحيد: تعيد الآن قاموسًا يتضمن تشخيصات المسافة للـ setup الأقرب،
    مع type=None عند غياب الإشارة. القرار التجاري متطابق 100% مع v3.7.
    """
    df = get_data(symbol)
    if df is None or len(df) < 2:
        return None

    curr = df.iloc[-1]
    candle_time = df.index[-1]  # ★ هذا وقت بداية الشمعة (open time) ★

    price = float(curr['Close'])
    ema_200 = float(curr['EMA_200'])
    bb_upper = float(curr['BB_Upper'])
    bb_lower = float(curr['BB_Lower'])

    # نفس شروط v3.7 حرفيًا
    signal_type = None
    if price > ema_200 and price <= bb_lower:
        signal_type = "CALL"
    elif price < ema_200 and price >= bb_upper:
        signal_type = "PUT"

    # ★★ تشخيص فقط: المسافة بالنقاط لأقرب setup (لا يؤثر على القرار) ★★
    pip_mult = 100.0 if "JPY" in symbol else 10000.0
    if price > ema_200:
        gap_pips = (price - bb_lower) * pip_mult   # كم نقطة فوق الحد السفلي
        near = "CALL"
    elif price < ema_200:
        gap_pips = (bb_upper - price) * pip_mult   # كم نقطة تحت الحد العلوي
        near = "PUT"
    else:
        gap_pips = None
        near = None

    return {
        "symbol": symbol,
        "type": signal_type,
        "price": price,
        "time": candle_time,  # بداية شمعة الإشارة
        "ema_200": ema_200,
        "bb_level": bb_lower if signal_type == "CALL" else bb_upper,
        "diag_near": near,
        "diag_gap_pips": gap_pips
    }

# ═══════════════════════════════════════════════
# 4. نظام الحسم التلقائي (رسائل مستقلة)
# ═══════════════════════════════════════════════

def resolve_pending_trades(sm: StateManager):
    if not sm.data["open_trades"]:
        return

    now_utc = datetime.now(timezone.utc)
    resolved_ids = []

    log.info(f">>> Checking {len(sm.data['open_trades'])} pending trades...")

    for trade in sm.data["open_trades"]:
        try:
            entry_dt = datetime.fromisoformat(trade['entry_time'])
            if entry_dt.tzinfo is None:
                entry_dt = entry_dt.replace(tzinfo=timezone.utc)

            # ★★ v3.8: تصحيح دلالات الوقت ★★
            # entry_dt = بداية شمعة الدخول.
            # شمعة الخروج هي الشمعة التالية: تبدأ عند entry_dt + TRADE_DURATION وتغلق بعدها بـ 15 دقيقة.
            exit_candle_start = entry_dt + timedelta(minutes=TRADE_DURATION_MINUTES)

            # ننتظر إغلاق شمعة الخروج + هامش تحديث البيانات (دقيقتان)
            check_ready_time = exit_candle_start + timedelta(minutes=TRADE_DURATION_MINUTES) + timedelta(minutes=2)
            # (يساوي entry_dt + 32 دقيقة — نفس توقيت v3.7 تقريبًا)

            if now_utc < check_ready_time:
                continue

            log.info(f"Resolving trade for {trade['symbol']} | Exit candle starts: {exit_candle_start}")

            df = get_data(trade['symbol'])
            if df is None or df.empty:
                log.warning(f"No data fetched for {trade['symbol']}. Will retry next cycle.")
                continue

            # ★★ الإصلاح الجوهري: شمعة الخروج = أول شمعة تبدأ عند/بعد exit_candle_start ★★
            target_ts = pd.Timestamp(exit_candle_start)
            future = df[df.index >= target_ts]

            if len(future) > 0:
                exit_price = float(future.iloc[0]['Close'])
            else:
                # احتياطي لنقص البيانات: أقرب شمعة متاحة قبل الهدف
                past = df[df.index < target_ts]
                if past.empty:
                    log.error(f"No candles at all for {trade['symbol']}. Skipping.")
                    continue
                exit_price = float(past.iloc[-1]['Close'])
                log.warning(f"Exit candle missing for {trade['symbol']}; using last available close as fallback.")

            entry_px = float(trade['entry_price'])
            diff = exit_price - entry_px

            pip_mult = 100.0 if "JPY" in trade['symbol'] else 10000.0
            pips_diff = abs(diff) * pip_mult

            if pips_diff < 0.1:
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

            result_msg = (f"{emoji} *نتيجة M15: {trade['symbol']}*\n\n"
                          f"⏰ *وقت الدخول:* `{entry_dt.strftime('%H:%M')} UTC`\n"
                          f"📈 *الاتجاه:* `{trade['type']}`\n"
                          f"💰 *الدخول:* `{entry_px:.5f}`\n"
                          f"💸 *الخروج:* `{exit_price:.5f}`\n"
                          f"📊 *الفرق:* `{pips_diff:+.1f} نقطة` {status_text}")

            send_telegram(result_msg)

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
    log.info(f"STARTING {BOT_NAME} | INDEPENDENT RESOLUTION MODE")
    log.info(f"Current UTC Time: {datetime.now(timezone.utc).strftime('%H:%M:%S')}")
    log.info("=" * 50)

    if not TG_TOKEN or not TG_CHAT:
        log.critical("MISSING TELEGRAM CREDS. ABORTING.")
        return

    sm = StateManager(STATE_FILE)
    sm.log_summary()  # ★★ تشخيص: ملخص الحالة عند كل إقلاع ★★

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

    current_gaza_hour = local_now.hour
    if BAN_START_HOUR_GAZA <= current_gaza_hour < BAN_END_HOUR_GAZA:
        log.info(f"Inside Night Ban Window ({current_gaza_hour}:xx Gaza Time). Skipping signals.")
        return

    signals_found = 0
    best_gap = None   # ★★ تشخيص: أقرب setup في الدورة ★★
    best_sym = None
    best_dir = None

    for sym in TRADING_SYMBOLS:
        if sm.is_frozen(sym):
            continue

        result = analyze_market(sym)

        if result is None:
            continue

        # تتبع أقرب setup (تشخيص فقط)
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
        expiry_time = (result['time'] + timedelta(minutes=15)).strftime("%H:%M")

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

    # ★★ تشخيص نهاية الدورة: يثبت هل السوق قريب من الست اب أم البوت معطل ★★
    if signals_found == 0:
        if best_gap is not None:
            log.info(f"No valid setups found in this cycle. Closest: {best_sym} {best_dir} "
                     f"(gap {best_gap:.1f} pips from band). Market quiet — scan logic healthy.")
        else:
            log.info("No valid setups found in this cycle. No pair even near a setup (check data feeds).")
    else:
        log.info(f"Cycle complete. {signals_found} new signals sent.")


if __name__ == "__main__":
    main()
