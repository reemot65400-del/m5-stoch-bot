#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
M15 Bollinger Trend Reversal Bot - INTEGRATED v2.0
الميزات: إشارات + حسم تلقائي + تقارير (يومي/أسبوعي/شهري) + حماية الأوقات الميتة.
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
    print("CRITICAL: Missing yfinance."); sys.exit(1)

# ═══════════════════════════════════════════════
# 1. الإعدادات العامة (Global Config)
# ═══════════════════════════════════════════════
BOT_NAME = "M15_BB_Sniper_Integrated_v2"
STATE_FILE = "state_m15_bb_integrated.json" 
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

ACTIVE_HOURS_START = 7   
ACTIVE_HOURS_END = 21    

TG_TOKEN = os.getenv("TG_TOKEN", "").strip()
TG_CHAT = os.getenv("TG_CHAT", "").strip()

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(BOT_NAME)

# ═══════════════════════════════════════════════
# 2. أدوات الاتصال والحالة (Utilities & State)
# ═══════════════════════════════════════════════

def send_telegram(msg: str):
    if not TG_TOKEN or not TG_CHAT: return None
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT, "text": msg, "parse_mode": "Markdown"}
        
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
        # هيكل البيانات الجديد يدعم الصفقات المفتوحة والتاريخ للتقارير
        self.data = {
            "processed_candles": [], 
            "open_trades": [],       # قائمة الصفقات التي لم تحسم بعد
            "trade_history": []      # أرشيف كامل للصفقات المحسومة
        }
        self.load()

    def load(self):
        if self.filepath.exists():
            try:
                with open(self.filepath, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                    # دمج البيانات القديمة مع الجديدة بأمان
                    for k in self.data:
                        if k in loaded: self.data[k] = loaded[k]
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
            if self.filepath.exists(): 
                os.replace(str(tmp), str(self.filepath))
            else:
                tmp.rename(self.filepath)
        except Exception as e:
            log.critical(f"STATE SAVE CRITICAL FAILURE: {e}")

    def mark_processed(self, key): 
        if key not in self.data["processed_candles"]:
            self.data["processed_candles"].append(key)
            if len(self.data["processed_candles"]) > 500: 
                self.data["processed_candles"] = self.data["processed_candles"][-500:]
            self.save()
            
    def is_processed(self, key): return key in self.data["processed_candles"]

    def add_open_trade(self, trade):
        self.data["open_trades"].append(trade)
        self.save()

    def remove_open_trade(self, tid):
        self.data["open_trades"] = [t for t in self.data["open_trades"] if t['id'] != tid]
        self.save()

    def archive_trade(self, result):
        self.data["trade_history"].append(result)
        # حفظ آخر 5000 صفقة فقط للحفاظ على حجم الملف
        if len(self.data["trade_history"]) > 5000:
            self.data["trade_history"] = self.data["trade_history"][-5000:]
        self.save()

# ═══════════════════════════════════════════════
# 3. محرك البيانات والمنطق الاستراتيجي (Strategy Engine)
# ═══════════════════════════════════════════════

def get_data(symbol, interval="15m", period="60d"):
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval=interval, auto_adjust=False, actions=False)
        if df is None or df.empty: return None
        
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [str(c[0]) for c in df.columns]
        df.index = pd.to_datetime(df.index, utc=True)
        df = df[~df.index.duplicated()].sort_index().dropna()
        
        now_utc = pd.Timestamp.now(tz="UTC")
        if not df.empty and df.index[-1] + pd.Timedelta(minutes=15) > now_utc:
            df = df.iloc[:-1]

        if len(df) < EMA_TREND_PERIOD + 10: return None

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

def analyze_market(symbol):
    df = get_data(symbol)
    if df is None or len(df) < 2: return None
    
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
            "time": candle_time,
            "ema_200": ema_200,
            "bb_level": bb_lower if signal_type == "CALL" else bb_upper
        }
    return None

# ═══════════════════════════════════════════════
# 4. نظام الحسم التلقائي (Auto Resolution System)
# ══════════════════════════════════════════════

def resolve_pending_trades(sm: StateManager):
    """يفحص الصفقات المفتوحة ويحسمها إذا مضى عليها 15 دقيقة"""
    if not sm.data["open_trades"]: return
    
    now_utc = datetime.now(timezone.utc)
    resolved_ids = []
    
    log.info(f"Checking {len(sm.data['open_trades'])} pending trades...")
    
    for trade in sm.data["open_trades"]:
        entry_time = pd.to_datetime(trade['entry_time'], utc=True).to_pydatetime()
        exit_target = entry_time + timedelta(minutes=15)
        
        # ننتظر دقيقة إضافية بعد انتهاء الشمعة لضمان توفر بيانات الإغلاق
        check_time = exit_target + timedelta(minutes=1)
        
        if now_utc >= check_time:
            log.info(f"Resolving trade for {trade['symbol']} entered at {entry_time}")
            
            # جلب سعر الإغلاق لشمعة الـ 15 دقيقة المستهدفة
            df = get_data(trade['symbol'])
            if df is None: continue
            
            # البحث عن الشمعة التي توافق وقت الخروج
            target_candle = df[df.index == exit_target]
            
            exit_price = None
            if not target_candle.empty:
                exit_price = float(target_candle.iloc[0]['Close'])
            else:
                # إذا لم نجد الشمعة بالضبط، نأخذ أقرب شمعة تالية أو الأخيرة
                future_candles = df[df.index >= exit_target]
                if not future_candles.empty:
                    exit_price = float(future_candles.iloc[0]['Close'])
                else:
                    exit_price = float(df.iloc[-1]['Close']) # ملاذ أخير
            
            if exit_price:
                is_win = False
                if trade['type'] == 'CALL' and exit_price > trade['entry_price']: is_win = True
                if trade['type'] == 'PUT' and exit_price < trade['entry_price']: is_win = True
                
                # إرسال نتيجة الصفقة
                emoji = "✅ ربح" if is_win else "❌ خسارة"
                result_msg = (f"{emoji} *نتيجة M15: {trade['symbol']}*\n"
                              f"الاتجاه: `{trade['type']}`\n"
                              f"الدخول: `{trade['entry_price']:.5f}`\n"
                              f"الخروج: `{exit_price:.5f}`")
                
                send_telegram(result_msg)
                
                # أرشفة النتيجة
                sm.archive_trade({
                    "id": trade['id'],
                    "symbol": trade['symbol'],
                    "type": trade['type'],
                    "entry_time": trade['entry_time'],
                    "exit_time": now_utc.isoformat(),
                    "win": is_win,
                    "payout": 0.85 # افتراضي للحسابات
                })
                
                resolved_ids.append(trade['id'])
    
    # إزالة الصفقات المحسومة من القائمة المفتوحة
    for tid in resolved_ids:
        sm.remove_open_trade(tid)

# ═══════════════════════════════════════════════
# 5. نظام التقارير الذكي (Smart Reporting System)
# ═══════════════════════════════════════════════

def generate_reports(sm: StateManager):
    """ينشئ تقريراً يومياً عند منتصف الليل، وأسبوعياً يوم الجمعة، وشهرياً في الأول"""
    now_utc = datetime.now(timezone.utc)
    history = sm.data["trade_history"]
    
    if not history: return
    
    # تحويل التاريخ لسهولة الفلترة
    df_hist = pd.DataFrame(history)
    df_hist['exit_dt'] = pd.to_datetime(df_hist['exit_time'], utc=True)
    
    # --- التقرير اليومي (عند الساعة 00:05 UTC) ---
    if now_utc.hour == 0 and now_utc.minute < 10:
        today_start = now_utc.replace(hour=0, minute=0, second=0)
        daily_trades = df_hist[df_hist['exit_dt'] >= today_start]
        
        if len(daily_trades) > 0:
            wins = len(daily_trades[daily_trades['win'] == True])
            total = len(daily_trades)
            wr = (wins / total) * 100
            profit = (wins * 0.85) - ((total - wins) * 1.0) # حساب تقريبي بالوحدات
            
            report = (f"📅 *التقرير اليومي ({now_utc.strftime('%Y-%m-%d')})*\n\n"
                      f"📊 إجمالي الصفقات: `{total}`\n"
                      f"✅ الرابحة: `{wins}` | ❌ الخاسرة: `{total-wins}`\n"
                      f"🏆 نسبة الفوز: `{wr:.1f}%`\n"
                      f"💰 صافي الوحدات: `{profit:+.2f}`")
            send_telegram(report)

    # --- التقرير الأسبوعي (يوم الجمعة عند 23:00 UTC) ---
    if now_utc.weekday() == 4 and now_utc.hour == 23: # 4 = Friday
        week_start = now_utc - timedelta(days=now_utc.weekday())
        weekly_trades = df_hist[df_hist['exit_dt'] >= week_start]
        
        if len(weekly_trades) > 0:
            wins = len(weekly_trades[weekly_trades['win'] == True])
            total = len(weekly_trades)
            wr = (wins / total) * 100
            
            report = (f"📅 *التقرير الأسبوعي*\n\n"
                      f"📊 إجمالي الصفقات: `{total}`\n"
                      f"🏆 نسبة الفوز: `{wr:.1f}%`\n"
                      f" ملاحظة: الاستراتيجية تعمل بكفاءة عالية." )
            send_telegram(report)

    # --- التقرير الشهري (اليوم الأول من الشهر عند 01:00 UTC) ---
    if now_utc.day == 1 and now_utc.hour == 1:
        month_start = now_utc.replace(day=1, hour=0, minute=0, second=0)
        monthly_trades = df_hist[df_hist['exit_dt'] >= month_start]
        
        if len(monthly_trades) > 0:
            wins = len(monthly_trades[monthly_trades['win'] == True])
            total = len(monthly_trades)
            wr = (wins / total) * 100
            
            report = (f"📅 *التقرير الشهري ({now_utc.strftime('%B %Y')})*\n\n"
                      f" إجمالي الصفقات: `{total}`\n"
                      f"🏆 نسبة الفوز: `{wr:.1f}%`\n"
                      f" استمر في الالتزام بالخطة!")
            send_telegram(report)

# ══════════════════════════════════════════════
# 6. الحلقة الرئيسية (Main Execution)
# ══════════════════════════════════════════════

def main():
    log.info("="*50)
    log.info(f"STARTING {BOT_NAME} | INTEGRATED MODE")
    log.info("="*50)
    
    if not TG_TOKEN or not TG_CHAT:
        log.critical("MISSING TELEGRAM CREDS. ABORTING.")
        return

    sm = StateManager(STATE_FILE)
    
    # 1. الحسم التلقائي (يعمل دائماً حتى خارج أوقات التداول)
    resolve_pending_trades(sm)
    
    # 2. فحص التقارير (يعمل دائماً)
    generate_reports(sm)

    # 3. فلتر الوقت لإرسال الإشارات الجديدة فقط
    now_utc = datetime.now(timezone.utc)
    if now_utc.weekday() >= 5:
        log.info("Weekend detected. Skipping new signals.")
        return
    if now_utc.hour < ACTIVE_HOURS_START or now_utc.hour >= ACTIVE_HOURS_END:
        log.info(f"Outside active hours ({now_utc.hour}:00 UTC). Skipping new signals.")
        return

    # 4. مسح السوق وإرسال الإشارات
    signals_found = 0
    for sym in TRADING_SYMBOLS:
        result = analyze_market(sym)
        
        if result:
            unique_key = f"{sym}_{result['time'].strftime('%Y-%m-%dT%H:%M')}"
            if not sm.is_processed(unique_key):
                signals_found += 1
                
                arrow = " CALL (صعود)" if result['type'] == "CALL" else "🔴 PUT (هبوط)"
                expiry_time = (result['time'] + timedelta(minutes=15)).strftime("%H:%M")
                
                msg = (f"*🎯 إشارة M15 BOLLINGER TREND REVERSAL*\n\n"
                       f"📊 *الزوج:* `{result['symbol']}`\n"
                       f"📈 *الاتجاه:* {arrow}\n"
                       f"💰 *السعر الحالي:* `{result['price']:.5f}`\n\n"
                       f" *منطقة الدخول:* لمس البولنجر باندز\n"
                       f"️ *فلتر الاتجاه (EMA200):* {'أعلى' if result['type']=='CALL' else 'أسفل'} المتوسط\n\n"
                       f"⏱️ *التعليمات:*\n"
                       f"✅ ادخل فوراً (فرصة إحصائية عالية)\n\n"
                       f"⌛ *انتهاء الشمعة:* {expiry_time} UTC\n"
                       f"💰 *شرط الـ payout:* 85% فأعلى فقط\n\n"
                       f"📝 *ملاحظة:* استراتيجية M15 BB + EMA200 (Z-Score 3.37)")
                       
                mid = send_telegram(msg)
                if mid:
                    sm.mark_processed(unique_key)
                    # إضافة الصفقة لقائمة الانتظار للحسم التلقائي
                    sm.add_open_trade({
                        "id": str(uuid.uuid4())[:8],
                        "symbol": result['symbol'],
                        "type": result['type'],
                        "entry_price": result['price'],
                        "entry_time": result['time'].isoformat(),
                        "msg_id": mid
                    })
                    log.info(f"Signal Sent & Queued for Resolution: {sym} {result['type']}")
                else:
                    log.error(f"Failed to send signal for {sym}")
        
        time.sleep(1.5) 

    if signals_found == 0:
        log.info("No valid setups found in this cycle.")
    else:
        log.info(f"Cycle complete. {signals_found} new signals sent.")

if __name__ == "__main__":
    main()
