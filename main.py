import os
import requests
import json
import math
from datetime import datetime, timezone, timedelta
import pandas as pd
import ccxt

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

MAX_SIGNALS = 3
TRADES_STATE_FILE = "active_trades.json"
OFFSET_FILE = "telegram_offset.json"

BASE_WATCHLIST = [
    "PAXG/USDT", "XAU/USDT", "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
    "ADA/USDT", "AVAX/USDT", "DOGE/USDT", "DOT/USDT", "NEAR/USDT", "SUI/USDT", "LINK/USDT"
]

def get_tehran_time_str() -> str:
    tehran_tz = timezone(timedelta(hours=3, minutes=30))
    return datetime.now(tehran_tz).strftime("%H:%M:%S | %Y/%m/%d")

def send_telegram(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[CRITICAL] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing!")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        res = requests.post(url, json=payload, timeout=10)
        data = res.json()
        if data.get("ok"):
            print("[TELEGRAM] Message delivered successfully.")
            return True
        else:
            print(f"[TELEGRAM REJECTED] {data.get('description')}")
            # ارسال بدون قالب‌بندی در صورت خطا در کاراکترها
            plain = message.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "").replace("<code>", "").replace("</code>", "")
            fallback = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": plain}, timeout=10).json()
            return fallback.get("ok", False)
    except Exception as e:
        print(f"[TELEGRAM NETWORK ERROR] {e}")
        return False

class TelegramCommandHandler:
    @staticmethod
    def load_offset():
        if os.path.exists(OFFSET_FILE):
            try:
                with open(OFFSET_FILE, "r") as f:
                    return json.load(f).get("offset", 0)
            except Exception: pass
        return 0

    @staticmethod
    def save_offset(offset):
        try:
            with open(OFFSET_FILE, "w") as f:
                json.dump({"offset": offset}, f)
        except Exception: pass

    @classmethod
    def process_pending_commands(cls, tech_agent):
        if not TELEGRAM_BOT_TOKEN:
            return
        offset = cls.load_offset()
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates?offset={offset + 1}&timeout=3"
        try:
            res = requests.get(url, timeout=5).json()
            if not res.get("ok"):
                return
            for update in res.get("result", []):
                update_id = update["update_id"]
                cls.save_offset(update_id)
                msg = update.get("message", {})
                text = msg.get("text", "").strip()
                if not text:
                    continue

                print(f"[USER COMMAND RECEIVED]: {text}")
                if text in ["/gold", "/xau", "/paxg"]:
                    ohlcv, _, name = tech_agent.fetch_candle_data("PAXG/USDT")
                    if ohlcv:
                        price = ohlcv[-1][4]
                        send_telegram(f"🥇 <b>قیمت انس طلا (PAXG):</b> <code>${price:,.2f}</code>\n⏱️ وقت تهران: {get_tehran_time_str()}")
                    else:
                        send_telegram("❌ خطا در اتصال به استخر طلا.")
                elif text == "/status":
                    send_telegram(f"🟢 ربات فعال و اسکنر در حال مانیتور است.\n⏱️ زمان تهران: {get_tehran_time_str()}")
        except Exception as e:
            print(f"[COMMAND ERROR] {e}")

class TechnicalAgent:
    def __init__(self):
        self.exchanges = []
        try:
            b = ccxt.binance({'enableRateLimit': True, 'urls': {'api': {'public': 'https://data-api.binance.vision/api/v3'}}})
            b.load_markets()
            self.exchanges.append(("Binance", b))
        except Exception as e:
            print(f"[BINANCE INIT ERROR] {e}")
        try:
            by = ccxt.bybit({'enableRateLimit': True, 'urls': {'api': {'public': 'https://api.bytick.com'}}})
            by.load_markets()
            self.exchanges.append(("Bybit", by))
        except Exception: pass

    def fetch_candle_data(self, symbol: str):
        search_symbols = [symbol]
        if symbol in ["XAU/USDT", "PAXG/USDT"]:
            search_symbols = ["PAXG/USDT", "PAXGUSDT", "XAUUSDT"]

        for s in search_symbols:
            for name, ex in self.exchanges:
                if s in ex.markets:
                    try:
                        ohlcv = ex.fetch_ohlcv(s, timeframe='15m', limit=50)
                        if ohlcv and len(ohlcv) >= 20:
                            return ohlcv, ex, f"{name} ({s})"
                    except Exception:
                        continue
        return None, None, ""

    def compute_indicators(self, ohlcv):
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / (loss + 1e-8)
        rsi = 100 - (100 / (1 + rs))
        return df['close'].iloc[-1], rsi.iloc[-1]

def run_system():
    print(f"=== [STARTING RUN: {get_tehran_time_str()}] ===")
    
    # تست اتصال اولیه تلگرام
    print("[1/3] Testing Telegram connectivity...")
    send_telegram(f"⚡ <b>اسکنر در مدار قرار گرفت</b>\n⏱️ ساعت: <code>{get_tehran_time_str()}</code>\nدر حال پردازش دستورات و اسکن...")

    tech_agent = TechnicalAgent()
    print(f"[2/3] Connected exchanges: {[name for name, _ in tech_agent.exchanges]}")

    # پردازش دستورات کاربر
    TelegramCommandHandler.process_pending_commands(tech_agent)

    # اسکن نمادها
    print("[3/3] Scanning core assets...")
    for sym in BASE_WATCHLIST:
        ohlcv, _, name = tech_agent.fetch_candle_data(sym)
        if ohlcv:
            price, rsi = tech_agent.compute_indicators(ohlcv)
            print(f" -> {sym} ({name}): Price={price}, RSI={rsi:.1f}")
        else:
            print(f" -> {sym}: No data found.")

    print("=== [CYCLE COMPLETED SUCCESSFULLY] ===")

if __name__ == "__main__":
    run_system()
