#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
M5 Stochastic Extreme Reversal Bot - GITHUB ACTIONS EDITION v9.5 (Ghost Candle Proof)
الإصلاح الجذري: استبدال الفلترة الزمنية الصارمة بدلاَ من الاعتماد على الفهارس الثابتة.
هذا يمنع قراءة الشموع الجارية أو الناقصة نهائياَ.
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
BOT_NAME = "M5_Stoch_GhostProof_V9"
STATE_FILE = "state_m5_omega.json" 
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
            if self.filepath.exists(): self.filepath.unlink()
            tmp.replace(self.filepath)
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
# 3. محرك البيانات المحصن ضد الأشباح (Ghost-Proof Data Engine)
# ═══════════════════════════════════════════════

def fetch_clean_m5_data(symbol: str):
    """
    يجلب البيانات وينظفها ثم يحذف الشموع غير المكتملة زمنياَ.
    ★★ الإصلاح الجذري هنا ★★
    """
    try:
        ticker = yf.Ticker(symbol)
        # جلب بيانات كافية لحساب EMA200 بدقة على فريم M5
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
        
        # ★★★ الخطوة الحاسمة: حذف الشموع التي لم تنتهِ بعد ★★★
        # نحدد الوقت الحالي
        now_utc = pd.Timestamp.now(tz="UTC")
        
        # نحسب متى يجب أن تنتهي آخر شمعة موجودة في القائمة
        # إذا كان وقت انتهاء الشمعة أكبر من الآن، فهي "جارية" وغير موثوقة
        last_candle_end_time = df.index[-1] + pd.Timedelta(minutes=5)
        
        if last_candle_end_time > now_utc:
            # حذف آخر صف لأنه غير مكتمل
            df = df.iloc[:-1]
            log.debug(f"Dropped incomplete candle for {symbol}. Last valid index: {df.index[-1]}")
            
        # تحقق إضافي: هل بقي لدينا بيانات كافية؟
        if len(df) < EMA_PERIOD + 10: return None
            
        return df
    except Exception as e:
        log.error(f"Data Fetch Error [{symbol}]: {e}")
        return None

def analyze_market(df: pd.DataFrame):
    """
    يحسب المؤشرات ويرجع الإشارة.
    بما أن البيانات نظيفة ومكتملة من المصدر، يمكننا استخدام iloc[-1] بأمان.
    """
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
    
    # ★★ الآن نستخدم آخر شمعة لأنها مضمونة بأنها مغلقة وكاملة ★★
    curr = df.iloc[-1] 
    prev = df.iloc[-2] 
    
    if pd.isna(curr['STOCH_K']) or pd.isna(prev['STOCH_K']): return None, None
    
    sig = None
    price = float(curr['Close'])
    ema = float(curr['EMA_200'])
    k_now, d_now = float(curr['STOCH_K']), float(curr['STOCH_D'])
    k_pre, d_pre = float(prev['STOCH_K']), float(prev['STOCH_D'])
    
    # شروط الدخول الصارمة
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
            
            # مدة الصفقة 5 دقائق
            target_exit_time = entry_dt + timedelta(minutes=5)
            
            # ننتظر 30 ثانية إضافية بعد إغلاق الشمعة لضمان توفر البيانات
            check_time = target_exit_time + timedelta(seconds=30)
            
            if now < check_time: 
                continue
                
            # استخدام الدالة النظيفة الجديدة
            df = fetch_clean_m5_data(t['symbol'])
            time.sleep(0.5) 
            
            if df is None or df.empty: continue
            
            # البحث عن الشمعة التي تمثل وقت الانتهاء المستهدف
            mask = df.index >= target_exit_time
            exit_px = None
            
            if mask.any():
                candidate_df = df[mask]
                if len(candidate_df) > 0:
                    exit_px = float(candidate_df.iloc[0]['Close'])
            
            if exit_px is None:
                exit_px = float(df.iloc[-1]['Close'])
                
            diff = exit_px - float(t['entry_price'])
            win = (diff > 0) if t['direction'] == 'CALL' else (diff < 0)
            
            # حساب النقاط
            pip_mult = 100 if "JPY" in t['symbol'].upper() else 10000
            pips = abs(diff) * pip_mult
            
            emoji = "✅ WIN" if win else "❌ LOSS"
            msg = (f"{emoji} *M5 Result: {t['symbol']}*\n"
                   f"Entry: `{float(t['entry_price']):.5f}`\n"
                   f"Exit: `{exit_px:.5f}`\n"
                   f"P/L: `{pips:.1f} pips`")
                   
            mid_val = t.get('telegram_message_id')
            reply_id = int(mid_val) if mid_val and str(mid_val).isdigit() else None
            send_telegram(msg, reply_to=reply_id)
            
            sm.archive_trade({
                "id": t['id'], "symbol": t['symbol'], "win": bool(win),
                "resolved_at": now.isoformat()
            })
            to_remove.append(t['id'])
            
        except Exception as e:
            log.error(f"Resolve Error for trade {t.get('id')}: {e}")
            
    for tid in to_remove: sm.remove_pending_trade(tid)

def scan_signals(sm: StateManager):
    open_syms = {t['symbol'] for t in sm.get_pending()}
    
    for sym in TRADING_SYMBOLS:
        if sym in open_syms: continue
        
        # استخدام الدالة النظيفة الجديدة
        df = fetch_clean_m5_data(sym)
        time.sleep(0.5) 
        
        sig, processed_df = analyze_market(df)
        
        if sig and processed_df is not None:
            # بما أن آخر شمعة في processed_df هي الموثوقة، نستخدمها مباشرة
            candle_time = processed_df.index[-1] 
            key = f"{sym}_{candle_time.strftime('%Y-%m-%dT%H:%M')}"
            
            if sm.is_processed(key): continue
            
            price = float(processed_df.iloc[-1]['Close'])
            arrow = "🟢 CALL" if sig == "CALL" else "🔴 PUT"
            
            msg = (f"*🔥 NEW M5 SIGNAL DETECTED*\n\n"
                   f"Pair: `{sym}`\n"
                   f"Direction: {arrow}\n"
                   f"Price: `{price:.5f}`\n"
                   f"Duration: 5 Minutes\n\n"
                   f"_Stochastic Extreme Reversal Confirmed_")
                   
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

# ═══════════════════════════════════════════════
# 5. الحلقة الرئيسية (Single Shot Execution)
# ═══════════════════════════════════════════════

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
        send_telegram(f"⚠️ *Bot Warning*\nCycle crashed.\n`{str(e)[:100]}`")
        sys.exit(1) 

if __name__ == "__main__":
    main()
