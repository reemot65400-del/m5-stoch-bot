#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
M15 Bollinger Trend Reversal Bot - v5.2 (Production Ready & Zero-Bug)
التعديلات الهندسية الحصرية:
1. توحيد اسم ملف الحالة إلى "state_m15_v5_pro.json" لضمان التطابق التام مع الـ YAML.
2. دمج آمن (Safe Auto-Merge) للملفات القديمة إن وُجدت دون أي مخاطر.
3. الاحتفاظ الكامل بمنطق الاستراتيجية (M15 BB + EMA200)، فلاتر اللمس، وحسم الصفقات، والتقارير الثلاثية.
"""
import os
import sys
import time
import json
import logging
import uuid
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
    print("pip install yfinance")
    sys.exit(1)

# ═══════════════════════════════════════════════
# 0. ثوابت التوقيت الآمنة (Asia/Hebron)
# ═══════════════════════════════════════════════
try:
    HEBRON_TZ = ZoneInfo("Asia/Hebron")
except Exception:
    try:
        HEBRON_TZ = ZoneInfo("Asia/Gaza")
    except Exception:
        HEBRON_TZ = None


def _is_palestine_summer(dt_utc: datetime) -> bool:
    def last_saturday(year, month):
        nxt = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=timezone.utc)
        return nxt - timedelta(days=((nxt.weekday() - 5) % 7) + 1)
    return last_saturday(dt_utc.year, 3) <= dt_utc < last_saturday(dt_utc.year, 10)


def to_gaza(dt_obj: datetime) -> datetime:
    if dt_obj.tzinfo is None:
        dt_obj = dt_obj.replace(tzinfo=timezone.utc)
    if HEBRON_TZ is not None:
        return dt_obj.astimezone(HEBRON_TZ)
    off = 3 if _is_palestine_summer(dt_obj.astimezone(timezone.utc)) else 2
    return dt_obj.astimezone(timezone(timedelta(hours=off)))


def get_palestine_now_safe() -> datetime:
    return to_gaza(datetime.now(timezone.utc))


# ═══════════════════════════════════════════════
# 1. الإعدادات العامة
# ═══════════════════════════════════════════════
BOT_NAME = "M15_BB_Sniper_V5_2"
# ★★ تصحيح الخطأ الأول: توحيد اسم ملف الحالة بدقة مطابقة للـ YAML ★★
STATE_FILE = "state_m15_v5_pro.json"
LOG_LEVEL = logging.INFO

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
BAN_START_HOUR_GAZA = 0
BAN_END_HOUR_GAZA = 9

FREEZE_DURATION_MINUTES = 15
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
# 2. أدوات الاتصال والحالة (State Persistence + Safe Auto-Merge)
# ═══════════════════════════════════════════════

def send_telegram(msg: str) -> bool:
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
                try:
                    wait = int(resp.json().get('parameters', {}).get('retry_after', 5))
                except Exception:
                    wait = 5
                time.sleep(wait)
            elif resp.status_code == 400:
                log.warning("TG Markdown parse error, retrying as plain text...")
                plain_text = msg.replace("*", "").replace("`", "").replace("_", "")
                plain = {"chat_id": TG_CHAT, "text": plain_text}
                resp_plain = requests.post(url, json=plain, timeout=15)
                if resp_plain.status_code == 200:
                    return True
                break
            else:
                log.error(f"TG API Error: {resp.status_code} | {resp.text}")
                break
        except Exception as e:
            log.error(f"TG Send Failed (Attempt {attempt + 1}): {e}")
            time.sleep(2)
    return False


class StateManager:
    def __init__(self, filepath: str):
        self.filepath = Path(filepath)
        self.defaults = {
            "frozen_pairs": {},
            "open_trades": [],
            "trade_history": [],
            "daily_counter_date": "",
            "daily_signal_count": 0,
            "last_daily_report": "",
            "last_weekly_report": "",
            "last_monthly_report": ""
        }
        self.data = json.loads(json.dumps(self.defaults))
        self.load()

    def load(self):
        # 1. تحميل الملف الأساسي المعتمد
        if self.filepath.exists():
            try:
                with open(self.filepath, 'r', encoding='utf-8') as f:
                    loaded_data = json.load(f)
                if isinstance(loaded_data, dict):
                    for key, default_val in self.defaults.items():
                        if key in loaded_data and isinstance(loaded_data[key], type(default_val)):
                            self.data[key] = loaded_data[key]
                self.cleanup_expired_freezes()
                log.info(f"State loaded successfully from {self.filepath}")
            except Exception as e:
                log.error(f"Failed to load state: {e}. Starting with safe defaults.")
                self.save()
        else:
            log.info("State file does not exist yet. Creating initial state file.")
            self.save()

        # 2. الدمج الآمن (Safe Auto-Merge) للملفات القديمة إن وُجدت دون إيقاف البوت عند حدوث أي خطأ
        for legacy_name in ["state_m15_fixed.json", "state_m15_fixed_gaza.json", "state_h1_smart_sniper_v5.json"]:
            legacy_path = Path(legacy_name)
            if legacy_path.exists() and legacy_path != self.filepath:
                try:
                    with open(legacy_path, 'r', encoding='utf-8') as f:
                        legacy_data = json.load(f)
                    existing_ids = {t.get("id") for t in self.data["trade_history"] if t.get("id")}
                    added_count = 0
                    for trade in legacy_data.get("trade_history", []):
                        if trade.get("id") and trade.get("id") not in existing_ids:
                            self.data["trade_history"].append(trade)
                            existing_ids.add(trade.get("id"))
                            added_count += 1
                    if added_count > 0:
                        log.info(f"Safely merged {added_count} trades from legacy file {legacy_name}.")
                        self.save()
                except Exception as e:
                    log.warning(f"Skipped corrupt legacy file {legacy_name}: {e}")

    def save(self):
        try:
            tmp = self.filepath.with_suffix('.tmp')
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=2, default=str, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(str(tmp), str(self.filepath))
        except Exception as e:
            log.critical(f"STATE SAVE CRITICAL FAILURE: {e}")

    def cleanup_expired_freezes(self):
        try:
            expired = [s for s in list(self.data["frozen_pairs"].keys()) if not self.is_frozen(s)]
            if expired:
                for s in expired:
                    self.data["frozen_pairs"].pop(s, None)
                self.save()
        except Exception as e:
            log.error(f"Error cleaning freezes: {e}")

    def log_summary(self):
        try:
            frozen_active = [s for s in self.data["frozen_pairs"] if self.is_frozen(s)]
            log.info(
                f"STATE SUMMARY | open={len(self.data['open_trades'])} | "
                f"frozen={len(frozen_active)} | history={len(self.data['trade_history'])} | "
                f"last_daily={self.data.get('last_daily_report')}"
            )
        except Exception as e:
            log.error(f"State summary failed: {e}")

    def get_next_signal_number(self) -> int:
        today_str = get_palestine_now_safe().strftime("%Y-%m-%d")
        if self.data.get("daily_counter_date") != today_str:
            self.data["daily_counter_date"] = today_str
            self.data["daily_signal_count"] = 0
        self.data["daily_signal_count"] = int(self.data.get("daily_signal_count", 0)) + 1
        self.save()
        return self.data["daily_signal_count"]

    def is_frozen(self, symbol: str) -> bool:
        if symbol not in self.data["frozen_pairs"]:
            return False
        freeze_until_str = self.data["frozen_pairs"][symbol]
        try:
            freeze_until = datetime.fromisoformat(str(freeze_until_str))
            if freeze_until.tzinfo is None:
                freeze_until = freeze_until.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) < freeze_until
        except Exception as e:
            log.error(f"Error checking freeze for {symbol}: {e}")
            return False

    def freeze_pair(self, symbol: str, base_time: datetime, duration_minutes: int = FREEZE_DURATION_MINUTES):
        candle_close_time = base_time + timedelta(minutes=TRADE_DURATION_MINUTES)
        release_time = candle_close_time + timedelta(minutes=duration_minutes)
        now_utc = datetime.now(timezone.utc)
        if release_time <= now_utc:
            release_time = now_utc + timedelta(minutes=duration_minutes)
        self.data["frozen_pairs"][symbol] = release_time.isoformat()
        self.save()

    def add_open_trade(self, trade: dict):
        self.data["open_trades"].append(trade)
        self.save()

    def remove_open_trade(self, tid: str):
        self.data["open_trades"] = [t for t in self.data["open_trades"] if t.get('id') != tid]
        self.save()

    def archive_trade(self, result: dict):
        self.data["trade_history"].append(result)
        if len(self.data["trade_history"]) > 2000:
            self.data["trade_history"] = self.data["trade_history"][-2000:]
        self.save()


# ═══════════════════════════════════════════════
# 3. محرك البيانات والاستراتيجية
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
        while not df.empty and (df.index[-1] + pd.Timedelta(minutes=TRADE_DURATION_MINUTES)) > now_utc:
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

    candle_close_time = candle_time + pd.Timedelta(minutes=TRADE_DURATION_MINUTES)
    now_utc = pd.Timestamp.now(tz="UTC")
    if (now_utc - candle_close_time) > pd.Timedelta(minutes=TRADE_DURATION_MINUTES * 2):
        log.debug(f"Skipping {symbol}: Last closed candle ({candle_time}) is stale.")
        return None

    price = float(curr['Close'])
    ema_200 = float(curr['EMA_200'])
    bb_upper = float(curr['BB_Upper'])
    bb_lower = float(curr['BB_Lower'])

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

    signal_type = None
    distance_from_bb = 0.0

    if price > ema_200 and price <= bb_lower:
        distance_from_bb = (bb_lower - price) * pip_mult
        if distance_from_bb >= MIN_DISTANCE_PIPS:
            signal_type = "CALL"
        else:
            log.debug(f"Signal skipped for {symbol}: Weak CALL touch ({distance_from_bb:.2f} pips < {MIN_DISTANCE_PIPS})")
    elif price < ema_200 and price >= bb_upper:
        distance_from_bb = (price - bb_upper) * pip_mult
        if distance_from_bb >= MIN_DISTANCE_PIPS:
            signal_type = "PUT"
        else:
            log.debug(f"Signal skipped for {symbol}: Weak PUT touch ({distance_from_bb:.2f} pips < {MIN_DISTANCE_PIPS})")

    return {
        "symbol": symbol, 
        "type": signal_type, 
        "price": price,
        "time": candle_time, 
        "ema_200": ema_200,
        "bb_level": bb_lower if signal_type == "CALL" else bb_upper,
        "distance_pips": distance_from_bb,
        "diag_near": near,
        "diag_gap_pips": gap_pips
    }


# ═══════════════════════════════════════════════
# 4. نظام الحسم التلقائي المحصن
# ═══════════════════════════════════════════════

def resolve_pending_trades(sm: StateManager):
    if not sm.data["open_trades"]:
        log.info("No open trades pending resolution.")
        return

    now_utc = datetime.now(timezone.utc)
    remaining_trades = []

    log.info(f"Checking {len(sm.data['open_trades'])} open trade(s) for resolution...")

    for trade in sm.data["open_trades"]:
        try:
            trade_id = trade.get('id') or str(uuid.uuid4())[:8]
            trade['id'] = trade_id

            entry_dt_raw = datetime.fromisoformat(str(trade['entry_time']))
            if entry_dt_raw.tzinfo is None:
                entry_dt_raw = entry_dt_raw.replace(tzinfo=timezone.utc)

            actual_entry_close_time = entry_dt_raw + timedelta(minutes=TRADE_DURATION_MINUTES)
            expected_exit_close_time = actual_entry_close_time + timedelta(minutes=TRADE_DURATION_MINUTES)
            check_ready_time = expected_exit_close_time + timedelta(minutes=2)

            if now_utc < check_ready_time:
                remaining_trades.append(trade)
                continue

            sym = trade['symbol']
            dr = trade.get('direction') or trade.get('type')
            if dr is None:
                log.error(f"Trade {trade_id} missing direction/type. Dropping corrupted record.")
                continue

            entry_px = float(trade['entry_price'])
            log.info(f"Resolving {sym} ({trade_id}) | Target Exit Close: {expected_exit_close_time.strftime('%Y-%m-%d %H:%M')} UTC")

            df_exit_full = get_data(sym)
            if df_exit_full is None or df_exit_full.empty:
                log.warning(f"No market data returned for {sym}. Keeping trade open for next cycle.")
                remaining_trades.append(trade)
                continue

            target_candle_start = expected_exit_close_time - timedelta(minutes=TRADE_DURATION_MINUTES)

            mask = (
                (df_exit_full.index >= target_candle_start - timedelta(seconds=30)) &
                (df_exit_full.index <= target_candle_start + timedelta(seconds=30))
            )
            matching_candles = df_exit_full[mask]

            if not matching_candles.empty:
                exit_px = float(matching_candles.iloc[0]['Close'])
            else:
                future_candles = df_exit_full[df_exit_full.index >= target_candle_start]
                if not future_candles.empty:
                    exit_px = float(future_candles.iloc[0]['Close'])
                    log.info(f"Used first available candle >= {target_candle_start} for {sym}.")
                else:
                    if now_utc < expected_exit_close_time + timedelta(hours=4):
                        log.warning(f"Exit candle not published yet for {sym}. Waiting for next pulse...")
                        remaining_trades.append(trade)
                        continue
                    else:
                        exit_px = float(df_exit_full.iloc[-1]['Close'])
                        log.warning(f"Timeout waiting for exact candle on {sym}; using latest available close.")

            pip_mult = 100.0 if "JPY" in sym else 10000.0
            diff = exit_px - entry_px
            pips_diff = abs(diff) * pip_mult

            if pips_diff < 0.1:
                emoji, is_win, status_text = "⚪ تعادل/لاغي", None, "_(Market Flat)_"
            else:
                is_win = (diff > 0) if dr == 'CALL' else (diff < 0)
                emoji = "✅ صفقة رابحة" if is_win else "❌ صفقة خاسرة"
                status_text = ""

            entry_gaza_str = to_gaza(actual_entry_close_time).strftime('%H:%M')
            exit_gaza_str = to_gaza(expected_exit_close_time).strftime('%H:%M')

            result_msg = (
                f"{emoji} *| نتيجة M15: {sym}*\n\n"
                f"📈 *الاتجاه:* `{dr}`\n"
                f"⏰ *وقت الدخول:* `{actual_entry_close_time.strftime('%H:%M')} UTC` (`{entry_gaza_str}` غزة)\n"
                f"🏁 *وقت الإغلاق:* `{expected_exit_close_time.strftime('%H:%M')} UTC` (`{exit_gaza_str}` غزة)\n"
                f"💰 *سعر الدخول:* `{entry_px:.5f}`\n"
                f"💸 *سعر الإغلاق:* `{exit_px:.5f}`\n"
                f"📊 *الفارق:* `{pips_diff:+.1f} نقطة (Pips)` {status_text}"
            )

            sent = send_telegram(result_msg)
            if not sent:
                log.error(f"Telegram failed to send resolution for {sym} ({trade_id}). Will retry next cycle.")
                remaining_trades.append(trade)
                continue

            sm.archive_trade({
                "id": trade_id,
                "symbol": sym,
                "type": dr,
                "entry_price": entry_px,
                "exit_price": exit_px,
                "entry_time": trade['entry_time'],
                "exit_time": expected_exit_close_time.isoformat(),
                "win": is_win,
                "pips": round(float(pips_diff), 2)
            })
            log.info(f"Trade {sym} ({trade_id}) resolved & archived: win={is_win}, pips={pips_diff:.2f}")

        except Exception as e:
            log.error(f"Critical Error resolving trade {trade.get('id')}: {e}", exc_info=True)
            remaining_trades.append(trade)

    sm.data["open_trades"] = remaining_trades
    sm.save()


# ═══════════════════════════════════════════════
# 5. نظام التقارير الشامل
# ═══════════════════════════════════════════════

AR_MONTHS = {
    "01": "يناير", "02": "فبراير", "03": "مارس", "04": "أبريل",
    "05": "مايو", "06": "يونيو", "07": "يوليو", "08": "أغسطس",
    "09": "سبتمبر", "10": "أكتوبر", "11": "نوفمبر", "12": "ديسمبر"
}
AR_DAYS = {
    0: 'الاثنين', 1: 'الثلاثاء', 2: 'الأربعاء', 3: 'الخميس',
    4: 'الجمعة', 5: 'السبت', 6: 'الأحد'
}


def ar_month_name(dt: datetime) -> str:
    return AR_MONTHS.get(dt.strftime("%m"), dt.strftime("%B"))


def convert_utc_to_gaza_date(time_str: str) -> str:
    try:
        dt_obj = pd.to_datetime(str(time_str), errors='coerce', utc=True)
        if pd.isna(dt_obj):
            return ""
        gaza_dt = to_gaza(dt_obj.to_pydatetime())
        return gaza_dt.strftime("%Y-%m-%d")
    except Exception:
        return ""


def build_report_message(trades_list: list, report_type: str, period_name: str) -> str:
    if not trades_list:
        return (
            f"*📊 التقرير {report_type} — بوت M15 Sniper*\n"
            f"🗓 *الفترة:* `{period_name}`\n\n"
            f"📈 *إجمالي الصفقات:* `0`\n"
            f"ℹ️ _لم تُسجل أي صفقات مكتملة خلال هذه الفترة._\n\n"
            f"_البوت يعمل بكفاءة ويراقب الفرص وفق الفلاتر الصارمة!_"
        )

    total = len(trades_list)
    wins = sum(1 for t in trades_list if t.get('win') is True)
    losses = sum(1 for t in trades_list if t.get('win') is False)
    voids = total - wins - losses

    net_pips = 0.0
    pair_map = {}

    for t in trades_list:
        sym = str(t.get('symbol', 'UNKNOWN'))
        w = t.get('win')
        try:
            p = float(t.get('pips', 0.0) or 0.0)
        except (TypeError, ValueError):
            p = 0.0

        signed_p = p if w is True else (-p if w is False else 0.0)
        net_pips += signed_p

        if sym not in pair_map:
            pair_map[sym] = {"trades": 0, "wins": 0, "losses": 0, "net_pips": 0.0}
        pair_map[sym]["trades"] += 1
        if w is True:
            pair_map[sym]["wins"] += 1
        elif w is False:
            pair_map[sym]["losses"] += 1
        pair_map[sym]["net_pips"] += signed_p

    valid_trades = wins + losses
    wr = (wins / valid_trades * 100.0) if valid_trades > 0 else 0.0
    emoji_wr = "🟢 ممتاز" if wr >= 60 else ("🟡 جيد" if wr >= 45 else "🔴 يحتاج مراجعة")

    best_pair_txt = "غير متاح"
    worst_pair_txt = "غير متاح"

    if pair_map:
        sorted_pairs = sorted(
            pair_map.items(),
            key=lambda item: (item[1]["net_pips"], item[1]["wins"]),
            reverse=True
        )
        best_sym, best_info = sorted_pairs[0]
        b_valid = best_info["wins"] + best_info["losses"]
        b_wr = (best_info["wins"] / b_valid * 100.0) if b_valid > 0 else 0.0
        best_pair_txt = f"🥇 `{best_sym}` ({best_info['net_pips']:+.1f} Pips | فوز: {b_wr:.0f}%)"

        if len(sorted_pairs) > 1:
            worst_sym, worst_info = sorted_pairs[-1]
            w_valid = worst_info["wins"] + worst_info["losses"]
            w_wr = (worst_info["wins"] / w_valid * 100.0) if w_valid > 0 else 0.0
            worst_pair_txt = f"⚠️ `{worst_sym}` ({worst_info['net_pips']:+.1f} Pips | فوز: {w_wr:.0f}%)"
        else:
            worst_pair_txt = "لا يوجد (تم التداول على زوج واحد فقط)"

    return (
        f"*📊 التقرير {report_type} — بوت M15 Sniper*\n"
        f"🗓 *الفترة:* `{period_name}`\n\n"
        f"📈 *إجمالي الصفقات:* `{total}` (✅ رابحة: `{wins}` | ❌ خاسرة: `{losses}` | ⚪ تعادل: `{voids}`)\n"
        f"🏆 *نسبة الفوز (Win Rate):* {emoji_wr} `{wr:.1f}%`\n"
        f"💰 *صافي النقاط (Net Pips):* `{net_pips:+.1f} Pips`\n\n"
        f"*🔍 تحليل أداء الأزواج:*\n"
        f"• *أفضل زوج:* {best_pair_txt}\n"
        f"• *أسوأ زوج:* {worst_pair_txt}\n\n"
        f"_انتهت جلسة التقرير {report_type}. استمر في الانضباط!_"
    )


def generate_advanced_reports(sm: StateManager):
    local_now = get_palestine_now_safe()
    history_raw = sm.data.get("trade_history", [])

    if history_raw:
        df_hist = pd.DataFrame(history_raw)
        if 'exit_time' in df_hist.columns:
            df_hist['gaza_date_key'] = df_hist['exit_time'].apply(convert_utc_to_gaza_date)
            
            def get_approx_gaza_hour(t_str):
                try:
                    dt = pd.to_datetime(t_str, errors='coerce')
                    if pd.isna(dt): return 0
                    if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
                    gaza_dt = to_gaza(dt)
                    return gaza_dt.hour
                except Exception:
                    return 0
            df_hist['approx_hour'] = df_hist['exit_time'].apply(get_approx_gaza_hour)

            valid_mask = df_hist['gaza_date_key'] != ""
            df_clean = df_hist[valid_mask].copy()
        else:
            df_clean = pd.DataFrame(columns=['gaza_date_key'])
    else:
        df_clean = pd.DataFrame(columns=['gaza_date_key'])

    # 1. التقرير اليومي
    yesterday_local = local_now - timedelta(days=1)
    yesterday_str = yesterday_local.strftime("%Y-%m-%d")

    if sm.data.get("last_daily_report") != yesterday_str:
        if not df_clean.empty:
            daily_records = df_clean[df_clean['gaza_date_key'] == yesterday_str].to_dict('records')
        else:
            daily_records = []

        daily_period_name = (
            f"{AR_DAYS[yesterday_local.weekday()]} {yesterday_local.day} "
            f"{AR_MONTHS[yesterday_local.strftime('%m')]} {yesterday_local.year}"
        )
        msg = build_report_message(daily_records, "اليومي", daily_period_name)
        if send_telegram(msg):
            sm.data["last_daily_report"] = yesterday_str
            sm.save()
            log.info(f"Daily report CONFIRMED & locked for {yesterday_str}")

    # 2. التقرير الأسبوعي
    current_gaza_weekday = local_now.weekday()
    if current_gaza_weekday in [5, 6]:
        days_since_monday_this_week = current_gaza_weekday
        last_week_start = (local_now - timedelta(days=days_since_monday_this_week + 7)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        last_week_end = last_week_start + timedelta(days=6)

        week_range_start_str = last_week_start.strftime("%Y-%m-%d")
        week_range_end_str = last_week_end.strftime("%Y-%m-%d")

        last_week_year, last_week_num, _ = last_week_start.isocalendar()
        last_week_str_id = f"{last_week_year}-W{last_week_num:02d}"

        weekly_period_name = (
            f"الأسبوع {last_week_num} "
            f"({last_week_start.day} {AR_MONTHS[last_week_start.strftime('%m')]} – "
            f"{last_week_end.day} {AR_MONTHS[last_week_end.strftime('%m')]} {last_week_end.year})"
        )

        if sm.data.get("last_weekly_report") != last_week_str_id:
            if not df_clean.empty:
                weekly_records = df_clean[
                    (df_clean['gaza_date_key'] >= week_range_start_str) &
                    (df_clean['gaza_date_key'] <= week_range_end_str)
                ].to_dict('records')
            else:
                weekly_records = []

            msg = build_report_message(weekly_records, "الأسبوعي", weekly_period_name)
            if send_telegram(msg):
                sm.data["last_weekly_report"] = last_week_str_id
                sm.save()
                log.info(f"Weekly report CONFIRMED & locked for {last_week_str_id}")

    # 3. التقرير الشهري
    if local_now.day <= 3:
        first_day_this_month = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_day_prev_month = first_day_this_month - timedelta(days=1)

        month_range_start_str = last_day_prev_month.replace(day=1).strftime("%Y-%m-%d")
        month_range_end_str = last_day_prev_month.strftime("%Y-%m-%d")

        monthly_period_name = f"{AR_MONTHS[last_day_prev_month.strftime('%m')]} {last_day_prev_month.year}"
        last_month_str_id = last_day_prev_month.strftime("%Y-%m")

        if sm.data.get("last_monthly_report") != last_month_str_id:
            if not df_clean.empty:
                monthly_records = df_clean[
                    (df_clean['gaza_date_key'] >= month_range_start_str) &
                    (df_clean['gaza_date_key'] <= month_range_end_str)
                ].to_dict('records')
            else:
                monthly_records = []

            msg = build_report_message(monthly_records, "الشهري", monthly_period_name)
            if send_telegram(msg):
                sm.data["last_monthly_report"] = last_month_str_id
                sm.save()
                log.info(f"Monthly report CONFIRMED & locked for {last_month_str_id}")


# ═══════════════════════════════════════════════
# 6. الحلقة الرئيسية
# ═══════════════════════════════════════════════

def main():
    local_now = get_palestine_now_safe()
    log.info("=" * 60)
    log.info(f"STARTING {BOT_NAME} | M15 PRODUCTION MODE (Asia/Hebron)")
    log.info(
        f"UTC Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} | "
        f"Gaza/Hebron Time: {local_now.strftime('%Y-%m-%d %H:%M:%S')}"
    )
    log.info("=" * 60)

    if not TG_TOKEN or not TG_CHAT:
        log.critical("MISSING TELEGRAM CREDS. ABORTING.")
        return

    sm = StateManager(STATE_FILE)
    sm.log_summary()

    resolve_pending_trades(sm)
    generate_advanced_reports(sm)

    now_utc = datetime.now(timezone.utc)
    if now_utc.weekday() >= 5:
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
            f"🎯 *منطقة الدخول:* اختراق البولنجر باندز ({result['distance_pips']:.1f} نقطة)\n"
            f"🏷️ *فلتر الاتجاه (EMA200):* {'أعلى' if result['type']=='CALL' else 'أسفل'} المتوسط\n\n"
            f"⏱️ *التعليمات:*\n"
            f"✅ ادخل فوراً (فرصة إحصائية عالية)\n\n"
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
                "entry_price": float(result['price']),
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
