#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
M15 Bollinger Trend Reversal Bot - LIVE v1.0
الاستراتيجية: M15 BB(15, 2.3) + EMA(200) Trend Filter.
الميزات: فحص 14 زوجاً، إدارة حالة لمنع التكرار، رسائل تليجرام عربية احترافية.
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
BOT_NAME = "M15_BB_Sniper_Live_v1"
STATE_FILE = "state_m15_bb_live.json" 
LOG_LEVEL = logging.INFO

# الأزواج الـ 14 الرئيسية
TRADING_SYMBOLS = [
    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "USDCHF=X", 
    "AUDUSD=X", "USDCAD=X", "NZDUSD=X", "EURGBP=X",
    "EURJPY=X", "GBPJPY=X", "AUDJPY=X", "CADJPY=X",
    "CHFJPY=X", "NZDJPY=X"
]

# معاملات الاستراتيجية (نفس الاختبار الناجح)
BB_PERIOD = 15
BB_STD_DEV = 2.3
EMA_TREND_PERIOD = 200

# قواعد الإدارة الزمنية
ACTIVE_HOURS_START = 7   # 07:00 UTC
ACTIVE_HOURS_END = 21    # 21:00 UTC

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
        self.data = {"processed_candles": []}
        self.load()

    def load(self):
        if self.filepath.exists():
            try:
                with open(self.filepath, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                    if "processed_candles" in loaded:
                        self.data["processed_candles"] = loaded["processed_candles"]
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

# ═══════════════════════════════════════════════
# 3. محرك البيانات والمنطق الاستراتيجي (Strategy Engine)
# ═══════════════════════════════════════════════

def get_data(symbol, interval="15m", period="60d"):
    """جلب بيانات M15 وحساب المؤشرات"""
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval=interval, auto_adjust=False, actions=False)
        if df is None or df.empty: return None
        
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [str(c[0]) for c in df.columns]
        df.index = pd.to_datetime(df.index, utc=True)
        df = df[~df.index.duplicated()].sort_index().dropna()
        
        # حذف الشمعة الحالية غير المكتملة لضمان دقة الإشارة
        now_utc = pd.Timestamp.now(tz="UTC")
        if not df.empty and df.index[-1] + pd.Timedelta(minutes=15) > now_utc:
            df = df.iloc[:-1]

        if len(df) < EMA_TREND_PERIOD + 10: return None

        # حساب المؤشرات (نفس منطق الاختبار)
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
    """تحليل الزوج وإرجاع الإشارة إذا تحققت الشروط"""
    df = get_data(symbol)
    if df is None or len(df) < 2: return None
    
    curr = df.iloc[-1]
    candle_time = df.index[-1]
    
    price = float(curr['Close'])
    ema_200 = float(curr['EMA_200'])
    bb_upper = float(curr['BB_Upper'])
    bb_lower = float(curr['BB_Lower'])
    
    signal_type = None
    
    # --- BUY RULE (CALL) ---
    if price > ema_200 and price <= bb_lower:
        signal_type = "CALL"
        
    # --- SELL RULE (PUT) ---
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

# ══════════════════════════════════════════════
# 4. الحلقة الرئيسية (Main Execution)
# ═══════════════════════════════════════════════

def main():
    log.info("="*50)
    log.info(f"STARTING {BOT_NAME} | LIVE SCANNING 14 PAIRS")
    log.info("="*50)
    
    if not TG_TOKEN or not TG_CHAT:
        log.critical("MISSING TELEGRAM CREDS. ABORTING.")
        return

    sm = StateManager(STATE_FILE)
    
    # فلتر الوقت
    now_utc = datetime.now(timezone.utc)
    if now_utc.weekday() >= 5:
        log.info("Weekend detected. Skipping scan.")
        return
    if now_utc.hour < ACTIVE_HOURS_START or now_utc.hour >= ACTIVE_HOURS_END:
        log.info(f"Outside active hours ({now_utc.hour}:00 UTC). Skipping.")
        return

    signals_found = 0

    for sym in TRADING_SYMBOLS:
        result = analyze_market(sym)
        
        if result:
            unique_key = f"{sym}_{result['time'].strftime('%Y-%m-%dT%H:%M')}"
            if not sm.is_processed(unique_key):
                signals_found += 1
                
                # صياغة الرسالة العربية
                arrow = "🟢 CALL (صعود)" if result['type'] == "CALL" else " PUT (هبوط)"
                expiry_time = (result['time'] + timedelta(minutes=15)).strftime("%H:%M")
                
                msg = (f"*🎯 إشارة M15 BOLLINGER TREND REVERSAL*\n\n"
                       f"📊 *الزوج:* `{result['symbol']}`\n"
                       f"📈 *الاتجاه:* {arrow}\n"
                       f"💰 *السعر الحالي:* `{result['price']:.5f}`\n\n"
                       f"🎯 *منطقة الدخول:* لمس البولنجر باندز\n"
                       f"🏷️ *فلتر الاتجاه (EMA200):* {'أعلى' if result['type']=='CALL' else 'أسفل'} المتوسط\n\n"
                       f"⏱️ *التعليمات:*\n"
                       f"✅ ادخل فوراً (فرصة إحصائية عالية)\n\n"
                       f"⌛ *انتهاء الشمعة:* {expiry_time} UTC\n"
                       f"💰 *شرط الـ payout:* 85% فأعلى فقط\n\n"
                       f"📝 *ملاحظة:* استراتيجية M15 BB + EMA200 (Z-Score 3.37)")
                       
                mid = send_telegram(msg)
                if mid:
                    sm.mark_processed(unique_key)
                    log.info(f"Signal Sent: {sym} {result['type']}")
                else:
                    log.error(f"Failed to send signal for {sym}")
        
        time.sleep(1.5) # تأخير لتجنب حظر Yahoo Finance

    if signals_found == 0:
        log.info("No valid setups found in this cycle.")
    else:
        log.info(f"Cycle complete. {signals_found} signals sent.")

if __name__ == "__main__":
    main()
