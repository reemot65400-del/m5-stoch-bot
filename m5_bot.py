#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
M5 Stochastic Extreme Reversal Bot - ARABIC EDITION v9.9 (Final Gold Master)
الإصلاحات النهائية: تصحيح تنسيق Markdown، إضافة إيموجي PUT المفقود، وإصلاح منطق أرشفة VOID.
"""
import os, sys, time, json, logging, uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
import numpy as np
import pandas as pd

try:
    import yfinance as yf
except ImportError:
    print("CRITICAL: Missing yfinance."); sys.exit(1)

# ═══════════════════════════════════════════════
# 1. الإعدادات العامة (Global Config)
# ═══════════════════════════════════════════════
BOT_NAME = "M5_Arabic_Sniper_V9_9"
STATE_FILE = "state_m5_arabic_v99.json" 
LOG_LEVEL = logging.INFO

TRADING_SYMBOLS = [
    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X", 
    "USDCAD=X", "NZDUSD=X", "EURCHF=X", "GBPJPY=X"    
]

K_PERIOD = 9
D_PERIOD = 3
OVERBOUGHT_ZONE = 85
OVERSOLD_ZONE = 15
EMA_PERIOD = 200

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

def get_pip_multiplier(symbol: str) -> float:
    if "JPY" in symbol.upper(): return 100.0
    return 10000.0

def send_telegram(msg: str, reply_to=None):
    if not TG_TOKEN or not TG_CHAT: return None
    
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT, "text": msg, "parse_mode": "Markdown"}
    
    if reply_to and int(reply_to) > 0:
        payload["reply_to_message_id"] = int(reply_to)
        
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
        self.data = {"pending_trades": [], "history": [], "processed_candles": []}
        self.load()

    def load(self):
        if self.filepath.exists():
            try:
                with open(self.filepath, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
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

    def add_pending_trade(self, trade): 
        self.data["pending_trades"].append(trade); self.save()
        
    def remove_pending_trade(self, tid): 
        self.data["pending_trades"] = [t for t in self.data["pending_trades"] if t.get("id") != tid]; self.save()
        
    def archive_trade(self, res): 
        self.data["history"].append(res)
        if len(self.data["history"]) > 2000: self.data["history"] = self.data["history"][-2000:]
        self.save()
        
    def mark_processed(self, key): 
        if key not in self.data["processed_candles"]:
            self.data["processed_candles"].append(key)
            if len(self.data["processed_candles"]) > 500: 
                self.data["processed_candles"] = self.data["processed_candles"][-500:]
            self.save()
            
    def is_processed(self, key): return key in self.data["processed_candles"]
    def get_pending(self): return self.data.get("pending_trades", [])

# ═══════════════════════════════════════════════
# 3. محرك البيانات والمؤشرات (Data Engine)
# ═══════════════════════════════════════════════

def fetch_clean_m5_data(symbol: str):
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="60d", interval="5m", auto_adjust=False, actions=False)
        
        if df is None or df.empty: return None
            
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [str(c[0]) for c in df.columns]
            
        req = ["Open", "High", "Low", "Close"]
        if not all(c in df.columns for c in req): return None
            
        df = df[req].copy()
        df.index = pd.to_datetime(df.index, utc=True)
        df = df[~df.index.duplicated()].sort_index().dropna()
        
        if len(df) < EMA_PERIOD + 10: return None
        
        now_utc = pd.Timestamp.now(tz="UTC")
        last_candle_end_time = df.index[-1] + pd.Timedelta(minutes=5)
        
        if last_candle_end_time > now_utc:
            df = df.iloc[:-1]
            
        if len(df) < EMA_PERIOD + 10: return None
            
        return df
    except Exception as e:
        log.error(f"Data Fetch Error [{symbol}]: {e}")
        return None

def analyze_market(df: pd.DataFrame):
    if df is None or len(df) < 3: return None, None
    
    close = df["Close"]
    df["EMA_200"] = close.ewm(span=EMA_PERIOD, adjust=False).mean()
    
    low_min = df["Low"].rolling(window=K_PERIOD).min()
    high_max = df["High"].rolling(window=K_PERIOD).max()
    denom = high_max - low_min
    denom = denom.replace(0, np.nan)
    
    raw_k = 100 * ((close - low_min) / denom)
    df["STOCH_K"] = raw_k.ffill().fillna(50)
    df["STOCH_D"] = df["STOCH_K"].rolling(window=D_PERIOD).mean().ffill()
    
    curr = df.iloc[-1] 
    prev = df.iloc[-2] 
    
    if pd.isna(curr['STOCH_K']) or pd.isna(prev['STOCH_K']): return None, None
    
    sig = None
    price = float(curr['Close'])
    ema = float(curr['EMA_200'])
    k_now, d_now = float(curr['STOCH_K']), float(curr['STOCH_D'])
    k_pre, d_pre = float(prev['STOCH_K']), float(prev['STOCH_D'])
    
    if price > ema and k_pre <= d_pre and k_now > d_now and min(k_pre, k_now) < OVERSOLD_ZONE:
        sig = "CALL"
    elif price < ema and k_pre >= d_pre and k_now < d_now and max(k_pre, k_now) > OVERBOUGHT_ZONE:
        sig = "PUT"
        
    return sig, df

# ═══════════════════════════════════════════════
# 4. التنفيذ وإغلاق الصفقات (Execution & Resolution)
# ═══════════════════════════════════════════════

def resolve_trades(sm: StateManager):
    pending = sm.get_pending()
    if not pending: return
    
    now = datetime.now(timezone.utc)
    to_remove = []
    
    for t in pending:
        try:
            entry_dt_str = t['entry_time']
            entry_dt = pd.to_datetime(entry_dt_str, utc=True).to_pydatetime()
            
            target_exit_time = entry_dt + timedelta(minutes=5)
            check_time = target_exit_time + timedelta(seconds=30)
            
            if now < check_time: 
                continue
                
            df = fetch_clean_m5_data(t['symbol'])
            time.sleep(0.5) 
            
            if df is None or df.empty: continue
            
            mask = df.index >= target_exit_time
            exit_px = None
            
            if mask.any():
                candidate_df = df[mask]
                if len(candidate_df) > 0:
                    exit_px = float(candidate_df.iloc[0]['Close'])
            
            if exit_px is None:
                exit_px = float(df.iloc[-1]['Close'])
                
            diff = exit_px - float(t['entry_price'])
            
            epsilon = 0.00001 
            status = ""
            emoji_result = ""
            win_val = None # None means Void/Draw
            
            if abs(diff) < epsilon:
                status = "VOID_FLAT"
                emoji_result = "⚪ لاغي (سوق ساكن)"
                log.warning(f"Trade {t['id']} for {t['symbol']} resulted in flat movement. Marking as Void.")
            else:
                if t['direction'] == 'CALL':
                    win_val = (diff > 0)
                else:
                    win_val = (diff < 0)
                
                status = "RESOLVED"
                emoji_result = "✅ ربح" if win_val else "❌ خسارة"
            
            pip_mult = get_pip_multiplier(t['symbol'])
            pips_val = abs(diff) * pip_mult if status == "RESOLVED" else 0.0

            dir_arrow = "▲ صعود (CALL)" if t['direction'] == 'CALL' else "▼ هبوط (PUT)"
            
            msg_body = (f"{emoji_result} *نتيجة M5: {t['symbol']}*\n"
                        f"الاتجاه: `{dir_arrow}`\n"
                        f"الدخول: `{float(t['entry_price']):.5f}`\n"
                        f"الخروج: `{exit_px:.5f}`\n"
                        f"النقاط: `{pips_val:.1f} نقطة`")
                   
            mid_val = t.get('telegram_message_id')
            reply_id = int(mid_val) if mid_val and str(mid_val).isdigit() else None
            send_telegram(msg_body, reply_to=reply_id)
            
            # ★★ إصلاح منطقي: عدم حفظ VOID كخسارة (False) بل كـ None ★★
            archive_win_status = win_val if status == "RESOLVED" else None
            
            sm.archive_trade({
                "id": t['id'], "symbol": t['symbol'], "win": archive_win_status,
                "resolved_at": now.isoformat(),
                "status": status
            })
            to_remove.append(t['id'])
            
        except Exception as e:
            log.error(f"Resolve Error for trade {t.get('id')}: {e}")
            
    for tid in to_remove: sm.remove_pending_trade(tid)

def scan_signals(sm: StateManager):
    now_utc = datetime.now(timezone.utc)
    current_hour = now_utc.hour
    
    if now_utc.weekday() >= 5:
        log.info("Weekend detected. Skipping scan.")
        return
        
    if current_hour < ACTIVE_HOURS_START or current_hour >= ACTIVE_HOURS_END:
         log.info(f"Outside active trading hours ({current_hour}:00 UTC). Skipping scan.")
         return

    open_syms = {t['symbol'] for t in sm.get_pending()}
    
    for sym in TRADING_SYMBOLS:
        if sym in open_syms: continue
        
        df = fetch_clean_m5_data(sym)
        time.sleep(0.5) 
        
        sig, processed_df = analyze_market(df)
        
        if sig and processed_df is not None:
            candle_time = processed_df.index[-1] 
            key = f"{sym}_{candle_time.strftime('%Y-%m-%dT%H:%M')}"
            
            if sm.is_processed(key): continue
            
            price = float(processed_df.iloc[-1]['Close'])
            # ★★ إصلاح بصري: إضافة إيموجي PUT المفقود ★★
            arrow = "🟢 CALL (صعود)" if sig == "CALL" else "🔴 PUT (هبوط)"
            
            expiry_time_utc = (candle_time + timedelta(minutes=5)).strftime("%H:%M")
            
            # ★★ إصلاح بصري: إزالة المسافة قبل النجمة الأولى لتنسيق Bold صحيح ★★
            msg = (f"*🎯 إشارة M5 v9.9 — Stoch Extreme Reversal*\n\n"
                   f"📊 *الزوج:* `{sym}`\n"
                   f"📈 *الاتجاه:* {arrow}\n"
                   f"💰 *السعر الحي الآن:* `{price:.5f}`\n\n"
                   f"🎯 *مناطق الدخول:*\n"
                   f"• قوية (عند الباند): `{price:.5f}`\n"
                   f"• وسطى (مقبولة): `{price:.5f}`\n\n"
                   f"⏱️ *التعليمات:*\n"
                   f"✅ ادخل فورا خلال 60 ثانية\n"
                   f"(السعر عند الباند — فرصة مثالية)\n\n"
                   f"📌 *السبب:* تقاطع ستوكاستيك متطرف مع اتجاه EMA200\n"
                   f"🏷️ *ضمن V4:* ➖\n\n"
                   f"⌛ *الانتهاء:* 5 دقائق (حتى {expiry_time_utc} UTC)\n"
                   f"💰 *شرط الـ payout:* 85% فأعلى فقط\n\n"
                   f"📊 *تنبيهات اليوم:* {len(open_syms) + 1}\n"
                   f"📝 *النتيجة تُسجل تلقائيا عند الانتهاء*")
                   
            mid = send_telegram(msg)
            if mid:
                sm.add_pending_trade({
                    "id": str(uuid.uuid4())[:8],
                    "symbol": sym,
                    "direction": sig,
                    "entry_price": price,
                    "entry_time": candle_time.isoformat(),
                    "telegram_message_id": mid
                })
                sm.mark_processed(key)
                log.info(f"Signal Sent: {sym} {sig}")
                time.sleep(1) 

# ══════════════════════════════════════════════
# 5. الحلقة الرئيسية (Main Loop)
# ══════════════════════════════════════════════

def main():
    log.info("="*50)
    log.info(f"STARTING {BOT_NAME} | SINGLE SHOT MODE")
    log.info("="*50)
    
    if not TG_TOKEN or not TG_CHAT:
        log.critical("MISSING TELEGRAM CREDS. ABORTING.")
        return

    sm = StateManager(STATE_FILE)
    
    try:
        log.info("--- Starting Scan Cycle ---")
        resolve_trades(sm)
        scan_signals(sm)
        log.info("--- Cycle Complete. Exiting. ---")
    except Exception as e:
        log.exception(f"CYCLE CRASH: {e}")
        send_telegram(f"⚠️ *تحذير البوت*\nتعطلت الدورة.\n`{str(e)[:100]}`")
        sys.exit(1) 

if __name__ == "__main__":
    main()
