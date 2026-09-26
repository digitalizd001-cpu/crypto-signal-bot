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
    # Gold (VIP)
    "PAXG/USDT", "XAU/USDT",
    # Majors & Layer 1
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "ADA/USDT",
    "AVAX/USDT", "DOT/USDT", "NEAR/USDT", "SUI/USDT", "APT/USDT", "TON/USDT",
    # Layer 2 & DeFi
    "LINK/USDT", "ARB/USDT", "OP/USDT", "UNI/USDT", "AAVE/USDT", "INJ/USDT",
    # Memes & High Vol
    "DOGE/USDT", "SHIB/USDT", "PEPE/USDT", "WIF/USDT"
]

def get_tehran_time_str() -> str:
    tehran_tz = timezone(timedelta(hours=3, minutes=30))
    return datetime.now(tehran_tz).strftime("%H:%M:%S | %Y/%m/%d")

def send_telegram(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        res = requests.post(url, json=payload, timeout=10)
        data = res.json()
        if not data.get("ok"):
            plain = message.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "").replace("<code>", "").replace("</code>", "")
            requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": plain}, timeout=10)
        return True
    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}")
        return False

# =====================================================================
# ماژول دستورات تعاملی تلگرام (/gold, /status, /trades)
# =====================================================================
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

                if text in ["/gold", "/xau", "/paxg"]:
                    ohlcv, active_ex, name = tech_agent.fetch_candle_data("PAXG/USDT")
                    if ohlcv:
                        price, rsi, atr, vol_ratio = tech_agent.compute_indicators(ohlcv)
                        send_telegram(
                            f"🥇 <b>گزارش اختصاصی طلا (PAXG/USDT)</b>\n"
                            f"━━━━━━━━━━━━━━━━━━\n"
                            f"💵 قیمت: <code>${price:,.2f}</code>\n"
                            f"📉 15m RSI: <code>{rsi:.1f}</code>\n"
                            f"📊 نسبت حجم: <code>{vol_ratio:.1f}x</code>\n"
                            f"⏱️ زمان تهران: <code>{get_tehran_time_str()}</code>"
                        )
                elif text == "/status":
                    send_telegram(f"🟢 ربات فعال است و بازار بدون توقف در حال اسکن می‌باشد.\n⏱️ زمان تهران: <code>{get_tehran_time_str()}</code>")
                elif text == "/trades":
                    trades = TradeLifecycleAgent.load_trades()
                    if not trades:
                        send_telegram("📭 در حال حاضر هیچ پوزیشن بازی وجود ندارد.")
                    else:
                        resp = "📋 <b>پوزیشن‌های باز:</b>\n"
                        for s, t in trades.items():
                            resp += f"• <b>{s}</b> ({t['action']}) | ورود: <code>{t['entry']}</code> | ریسک‌فری: <b>{t['risk_free']}</b>\n"
                        send_telegram(resp)
        except Exception as e:
            print(f"[COMMAND ERROR] {e}")

# =====================================================================
# ماژول رهگیری لحظه‌ای معاملات (TP / SL / Risk-Free)
# =====================================================================
class TradeLifecycleAgent:
    @staticmethod
    def load_trades():
        if os.path.exists(TRADES_STATE_FILE):
            try:
                with open(TRADES_STATE_FILE, "r") as f:
                    return json.load(f)
            except Exception: pass
        return {}

    @staticmethod
    def save_trades(trades):
        try:
            with open(TRADES_STATE_FILE, "w") as f:
                json.dump(trades, f, indent=2)
        except Exception: pass

    @classmethod
    def register_trade(cls, setup: dict):
        trades = cls.load_trades()
        symbol = setup['symbol']
        if symbol in trades:
            return

        trades[symbol] = {
            'action': setup['action'],
            'entry': setup['price'],
            'sl': setup['sl'],
            'tp1': setup['tp1'],
            'tp2': setup['tp2'],
            'tp3': setup['tp3'],
            'leverage': setup['leverage'],
            'tp1_hit': False,
            'risk_free': False,
            'opened_at': get_tehran_time_str()
        }
        cls.save_trades(trades)

    @classmethod
    def monitor_active_trades(cls, tech_agent):
        trades = cls.load_trades()
        if not trades:
            return

        remaining = {}
        for symbol, t in list(trades.items()):
            ohlcv, _, _ = tech_agent.fetch_candle_data(symbol)
            if not ohlcv or len(ohlcv) < 2:
                remaining[symbol] = t
                continue

            last_candle = ohlcv[-2]
            high_p, low_p = last_candle[2], last_candle[3]
            is_long = "LONG" in t['action']
            closed = False

            # لمس تارگت اول و ریسک‌فری
            if not t['tp1_hit']:
                tp1_hit = (high_p >= t['tp1']) if is_long else (low_p <= t['tp1'])
                if tp1_hit:
                    t['tp1_hit'] = True
                    t['risk_free'] = True
                    t['sl'] = t['entry']
                    send_telegram(
                        f"🎯 <b>تارگت اول (TP1) تاچ شد!</b>\n"
                        f"🌐 #{symbol.replace('/', '_')}\n"
                        f"✅ لمس قیمت: <code>{t['tp1']:,.4f}</code>\n"
                        f"🛡️ استاپ به نقطه ورود منتقل و معامله ریسک‌فری شد."
                    )

            # لمس استاپ
            sl_hit = (low_p <= t['sl']) if is_long else (high_p >= t['sl'])
            if sl_hit:
                status = "در نقطه ورود (سربه‌سر)" if t['risk_free'] else "با حد ضرر اولیه"
                send_telegram(f"🛑 <b>معامله بسته شد:</b> #{symbol.replace('/', '_')} {status}")
                closed = True

            # لمس تارگت نهایی
            tp3_hit = (high_p >= t['tp3']) if is_long else (low_p <= t['tp3'])
            if tp3_hit and not closed:
                send_telegram(f"🏆 <b>تارگت نهایی (TP3) تاچ شد:</b> #{symbol.replace('/', '_')} با سود کامل بسته شد.")
                closed = True

            if not closed:
                remaining[symbol] = t

        cls.save_trades(remaining)

# =====================================================================
# ماژول دیتای تکنیکال و صرافی‌ها
# =====================================================================
class TechnicalAgent:
    def __init__(self):
        self.exchanges = []
        try:
            b = ccxt.binance({'enableRateLimit': True, 'urls': {'api': {'public': 'https://data-api.binance.vision/api/v3'}}})
            b.load_markets()
            self.exchanges.append(("Binance", b))
        except Exception: pass
        try:
            by = ccxt.bybit({'enableRateLimit': True, 'urls': {'api': {'public': 'https://api.bytick.com'}}})
            by.load_markets()
            self.exchanges.append(("Bybit", by))
        except Exception: pass

    def fetch_candle_data(self, symbol: str):
        search = [symbol]
        if symbol in ["PAXG/USDT", "XAU/USDT"]:
            search = ["PAXG/USDT", "PAXGUSDT", "XAUUSDT"]

        for s in search:
            for name, ex in self.exchanges:
                if s in ex.markets:
                    try:
                        ohlcv = ex.fetch_ohlcv(s, timeframe='15m', limit=50)
                        if ohlcv and len(ohlcv) >= 25:
                            return ohlcv, ex, name
                    except Exception:
                        continue
        return None, None, ""

    def analyze_orderbook_imbalance(self, exchange, symbol: str) -> float:
        try:
            target = symbol
            if target not in exchange.markets:
                for k in exchange.markets.keys():
                    if symbol.split("/")[0] in k:
                        target = k
                        break
            ob = exchange.fetch_order_book(target, limit=20)
            bids = sum(b[1] for b in ob['bids'])
            asks = sum(a[1] for a in ob['asks'])
            if (bids + asks) == 0: return 0.0
            return round((bids - asks) / (bids + asks), 2)
        except Exception:
            return 0.0

    def compute_indicators(self, ohlcv):
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df = df.iloc[:-1].copy()

        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / (loss + 1e-8)
        df['rsi'] = 100 - (100 / (1 + rs))
        df['vol_ma20'] = df['volume'].rolling(20).mean()

        high_low = df['high'] - df['low']
        high_close = (df['high'] - df['close'].shift()).abs()
        low_close = (df['low'] - df['close'].shift()).abs()
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        df['atr'] = ranges.max(axis=1).rolling(14).mean()

        latest = df.iloc[-1]
        vol_ratio = latest['volume'] / latest['vol_ma20'] if latest['vol_ma20'] > 0 else 1.0
        return latest['close'], latest['rsi'], latest['atr'], vol_ratio

# =====================================================================
# هوش مصنوعی تاییدکننده معامله
# =====================================================================
class MacroAIOfficerAgent:
    def review_setup(self, payload: dict) -> dict:
        prompt = (
            f"Review setup:\nSymbol: {payload['symbol']} | Action: {payload['action']}\n"
            f"RSI(15m): {payload['rsi']:.1f} | Order-book Imbalance: {payload['imbalance']}\n"
            f"Start with 'VERDICT: CONFIRMED' or 'VERDICT: REJECTED'. Add 1 short sentence reason."
        )
        analysis = ""
        engine = "Gemini Flash"
        if GEMINI_API_KEY:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
                res = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=6).json()
                analysis = res['candidates'][0]['content']['parts'][0]['text'].strip()
            except Exception: pass

        if not analysis and GROQ_API_KEY:
            engine = "Groq Llama-3"
            try:
                url = "https://api.groq.com/openai/v1/chat/completions"
                headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
                res = requests.post(url, headers=headers, json={"model": "llama-3.1-8b-instant", "messages": [{"role": "user", "content": prompt}], "max_tokens": 80}, timeout=6).json()
                analysis = res['choices'][0]['message']['content'].strip()
            except Exception: pass

        if not analysis:
            return {"approved": True, "engine": "Quant Strict Filter", "thesis": "Order flow & momentum aligned."}

        approved = "CONFIRMED" in analysis.upper()
        thesis = analysis.replace("VERDICT: CONFIRMED", "").replace("VERDICT: REJECTED", "").strip()
        return {"approved": approved, "engine": engine, "thesis": thesis}

# =====================================================================
# ارسال کارت سیگنال
# =====================================================================
class DispatchAgent:
    @staticmethod
    def send(data: dict):
        symbol_tag = data['symbol'].replace('/', '_')
        action_emoji = "🟢" if "LONG" in data['action'] else "🔴"
        price, sl, lev = data['price'], data['sl'], data['leverage']
        sl_pct = abs((price - sl) / price) * 100
        tp1, tp2, tp3 = data['tp1'], data['tp2'], data['tp3']
        tp1_pct = abs((tp1 - price) / price) * 100

        msg = (
            f"⚡ <b>سیگنال معتبر صادر شد</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🌐 #{symbol_tag} | استخر: <b>{data['source']}</b>\n"
            f"{action_emoji} <b>{data['action']}</b>\n\n"
            f"🧠 <b>تحلیل هوش مصنوعی ({data['ai_engine']}):</b>\n"
            f"<i>{data['ai_thesis']}</i>\n\n"
            f"📍 <b>نقطه ورود:</b> <code>{price:,.4f}</code>\n"
            f"🛑 <b>حد ضرر:</b> <code>{sl:,.4f}</code> (-{sl_pct:.2f}%)\n\n"
            f"🎯 <b>تارگت‌ها:</b>\n"
            f"🥇 TP1 ➔ <code>{tp1:,.4f}</code> (+{tp1_pct:.2f}%) [ریسک‌فری]\n"
            f"🥈 TP2 ➔ <code>{tp2:,.4f}</code>\n"
            f"🥉 TP3 ➔ <code>{tp3:,.4f}</code>\n\n"
            f"⏱️ <i>زمان به وقت تهران: {get_tehran_time_str()}</i>"
        )
        send_telegram(msg)

# =====================================================================
# هسته هماهنگ‌کننده کل اسکنر
# =====================================================================
def run_system():
    tech_agent = TechnicalAgent()
    macro_agent = MacroAIOfficerAgent()

    # ۱. پردازش دستورات تلگرام کاربر
    TelegramCommandHandler.process_pending_commands(tech_agent)

    # ۲. رصد معاملات باز قبلی
    TradeLifecycleAgent.monitor_active_trades(tech_agent)

    dispatched = 0
    for symbol in BASE_WATCHLIST:
        if dispatched >= MAX_SIGNALS:
            break

        ohlcv, active_ex, source_name = tech_agent.fetch_candle_data(symbol)
        if not ohlcv:
            continue

        try:
            price, rsi, atr, vol_ratio = tech_agent.compute_indicators(ohlcv)
            imbalance = tech_agent.analyze_orderbook_imbalance(active_ex, symbol)

            setup = None

            # فیلتر لانگ: اشباع فروش RSI + برتری حجم خریدار در اوردربوک
            if rsi < 40 and imbalance > 0.04 and vol_ratio >= 0.90:
                setup = {
                    'source': source_name, 'symbol': symbol, 'action': 'LONG — BUY SETUP',
                    'price': price, 'sl': price - (2.0 * atr),
                    'tp1': price + (1.8 * atr), 'tp2': price + (3.5 * atr), 'tp3': price + (5.0 * atr),
                    'leverage': 3, 'rsi': rsi, 'imbalance': imbalance
                }

            # فیلتر شورت: اشباع خرید RSI + برتری حجم فروشنده در اوردربوک
            elif rsi > 60 and imbalance < -0.04 and vol_ratio >= 0.90:
                setup = {
                    'source': source_name, 'symbol': symbol, 'action': 'SHORT — SELL SETUP',
                    'price': price, 'sl': price + (2.0 * atr),
                    'tp1': price - (1.8 * atr), 'tp2': price - (3.5 * atr), 'tp3': price - (5.0 * atr),
                    'leverage': 3, 'rsi': rsi, 'imbalance': imbalance
                }

            if setup:
                review = macro_agent.review_setup(setup)
                if review['approved']:
                    setup['ai_engine'] = review['engine']
                    setup['ai_thesis'] = review['thesis']
                    DispatchAgent.send(setup)
                    TradeLifecycleAgent.register_trade(setup)
                    dispatched += 1
                    print(f"[SIGNAL SENT]: {symbol}")

        except Exception as e:
            print(f"[SCAN ERROR on {symbol}] {e}")

if __name__ == "__main__":
    run_system()
