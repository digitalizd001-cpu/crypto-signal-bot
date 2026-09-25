import os
import requests
import json
import math
from datetime import datetime, timezone, timedelta
import pandas as pd
import yfinance as yf
import ccxt

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

MAX_SIGNALS = 3  # کاهش سقف سیگنال‌ها برای تمرکز فقط روی ستاپ‌های استثنایی
PORTFOLIO_RISK_PERCENT = 1.0

TRADES_STATE_FILE = "active_trades.json"
PERFORMANCE_FILE = "performance.json"
HISTORY_FILE = "trade_history.json"
CUSTOM_WATCHLIST_FILE = "custom_watchlist.json"
OFFSET_FILE = "telegram_offset.json"

BASE_WATCHLIST = [
    # Gold & Commodities (VIP Pinned)
    "PAXG/USDT", "XAU/USDT", "XAUT/USDT",
    # Layer 1
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "ADA/USDT",
    "AVAX/USDT", "DOT/USDT", "TRX/USDT", "NEAR/USDT", "SUI/USDT", "APT/USDT",
    "TON/USDT", "KAS/USDT", "SEI/USDT", "HBAR/USDT", "ATOM/USDT", "ALGO/USDT",
    "FTM/USDT", "EGLD/USDT", "FLOW/USDT", "MINA/USDT", "ICP/USDT",
    # Layer 2
    "MATIC/USDT", "ARB/USDT", "OP/USDT", "STRK/USDT", "IMX/USDT", "MANTA/USDT", "METIS/USDT",
    # DeFi
    "LINK/USDT", "UNI/USDT", "AAVE/USDT", "MKR/USDT", "SNX/USDT", "LDO/USDT",
    "CRV/USDT", "RUNE/USDT", "INJ/USDT", "PENDLE/USDT", "ENA/USDT", "DYDX/USDT", "CAKE/USDT",
    # AI & Data
    "FET/USDT", "RENDER/USDT", "TAO/USDT", "AGIX/USDT", "OCEAN/USDT", "GRT/USDT",
    "WLD/USDT", "ARKM/USDT", "THETA/USDT",
    # Memes
    "DOGE/USDT", "SHIB/USDT", "PEPE/USDT", "WIF/USDT", "FLOKI/USDT", "BONK/USDT",
    "BOME/USDT", "MEME/USDT",
    # Legacy
    "LTC/USDT", "BCH/USDT", "ETC/USDT", "XLM/USDT",
    # Gaming
    "GALA/USDT", "SAND/USDT", "MANA/USDT", "AXS/USDT", "BEAM/USDT", "RON/USDT",
    # Storage & Infra
    "FIL/USDT", "AR/USDT", "TIA/USDT"
]

def get_tehran_time_str() -> str:
    tehran_tz = timezone(timedelta(hours=3, minutes=30))
    now = datetime.now(tehran_tz)
    return now.strftime("%H:%M:%S | %Y/%m/%d")

def send_telegram(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TELEGRAM ERROR]: Token or Chat ID not configured.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        res = requests.post(url, json=payload, timeout=10)
        data = res.json()
        if not data.get("ok"):
            plain = message.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "").replace("<code>", "").replace("</code>", "")
            requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": plain}, timeout=10)
    except Exception as e:
        print(f"[TELEGRAM EXCEPTION]: {e}")

# =====================================================================
# ماژول یادگیری تقویتی خودکار (Reinforcement Self-Learning Agent)
# =====================================================================
class SelfLearningAgent:
    @staticmethod
    def load_history():
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r") as f:
                    return json.load(f)
            except Exception: pass
        return []

    @staticmethod
    def log_trade_outcome(trade_data: dict, outcome: str):
        history = SelfLearningAgent.load_history()
        trade_data["outcome"] = outcome
        trade_data["closed_at"] = get_tehran_time_str()
        history.append(trade_data)
        try:
            with open(HISTORY_FILE, "w") as f:
                json.dump(history[-100:], f, indent=2)  # ذخیره ۱۰۰ معامله آخر
        except Exception: pass

    @staticmethod
    def get_dynamic_thresholds():
        """تنظیم خودکار آستانه‌ها بر مبنای سوابق معاملات قبلی"""
        history = SelfLearningAgent.load_history()
        if len(history) < 5:
            # مقادیر اولیه بهینه و سخت‌گیرانه
            return {"rsi_long": 38, "rsi_short": 62, "imbalance_req": 0.08, "whale_min": 1.05}

        recent = history[-20:]
        losses = [h for h in recent if h.get("outcome") == "LOSS"]
        loss_rate = len(losses) / len(recent)

        # اگر نرخ باخت بالا باشد، شرایط ورود به شدت سخت‌تر می‌شود
        if loss_rate > 0.50:
            return {
                "rsi_long": 32,          # نیاز به اشباع فروش عمیق‌تر
                "rsi_short": 68,         # نیاز به اشباع خرید عمیق‌تر
                "imbalance_req": 0.15,   # حجم سفارش سنگین‌تر در اردر بوک
                "whale_min": 1.25        # تأیید قوی‌تر توسط نهنگ‌ها
            }
        else:
            return {"rsi_long": 40, "rsi_short": 60, "imbalance_req": 0.06, "whale_min": 1.00}

# =====================================================================
# ماژول رهگیری معاملات (Trade Lifecycle) با ثبت فیدبک
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
            'rsi_at_entry': setup.get('rsi_15m', 50),
            'imbalance_at_entry': setup.get('imbalance', 0),
            'opened_at': get_tehran_time_str()
        }
        cls.save_trades(trades)

    @classmethod
    def monitor_active_trades(cls, tech_agent):
        trades = cls.load_trades()
        if not trades:
            return

        remaining_trades = {}
        tehran_now = get_tehran_time_str()

        for symbol, t in list(trades.items()):
            ohlcv, _, _ = tech_agent.fetch_candle_data(symbol)
            if not ohlcv or len(ohlcv) < 2:
                remaining_trades[symbol] = t
                continue

            last_candle = ohlcv[-2]
            high_price = last_candle[2]
            low_price = last_candle[3]

            is_long = "LONG" in t['action']
            closed = False

            # ۱. لمس TP1 و انتقال فوری استاپ به نقطه ورود + کارمزد
            if not t['tp1_hit']:
                tp1_reached = (high_price >= t['tp1']) if is_long else (low_price <= t['tp1'])
                if tp1_reached:
                    t['tp1_hit'] = True
                    t['risk_free'] = True
                    t['sl'] = t['entry'] * (1.001 if is_long else 0.999)  # ریسک‌فری واقعی با پوشش کارمزد
                    msg = (
                        f"🎯 <b>تارگت اول (TP1) محقق شد!</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🌐 جفت‌ارز: <b>#{symbol.replace('/', '_')}</b>\n"
                        f"✅ لمس قیمت: <code>{t['tp1']:,.4f}</code>\n"
                        f"🛡️ استاپ به نقطه ورود منتقل شد (معامله ۱۰۰٪ بدون ریسک شد).\n"
                        f"⏱️ <i>زمان: {tehran_now}</i>"
                    )
                    send_telegram(msg)

            # ۲. لمس TP3 (پیروزی کامل)
            tp3_reached = (high_price >= t['tp3']) if is_long else (low_price <= t['tp3'])
            if tp3_reached:
                msg = (
                    f"🏆 <b>تارگت نهایی (TP3) کامل شد!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🌐 جفت‌ارز: <b>#{symbol.replace('/', '_')}</b>\n"
                    f"💰 معامله با حداکثر سود بسته شد."
                )
                send_telegram(msg)
                SelfLearningAgent.log_trade_outcome(t, "WIN")
                closed = True

            # ۳. بررسی حد ضرر
            sl_reached = (low_price <= t['sl']) if is_long else (high_price >= t['sl'])
            if sl_reached and not closed:
                if t['risk_free']:
                    msg = (
                        f"🛡️ <b>خروج در نقطه ورود (Risk-Free)</b>\n"
                        f"🌐 #{symbol.replace('/', '_')} | سود سیو شد و بدون ضرر بسته شد."
                    )
                    SelfLearningAgent.log_trade_outcome(t, "BE")  # Break-Even
                else:
                    msg = (
                        f"🛑 <b>حد ضرر (Stop Loss) لمس شد</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🌐 #{symbol.replace('/', '_')} | قیمت خروج: <code>{t['sl']:,.4f}</code>\n"
                        f"🧠 <i>الگوی شکست برای ارتقای فیلترها در دیتابیس یادگیری ثبت شد.</i>"
                    )
                    SelfLearningAgent.log_trade_outcome(t, "LOSS")
                send_telegram(msg)
                closed = True

            if not closed:
                remaining_trades[symbol] = t

        cls.save_trades(remaining_trades)

# =====================================================================
# ماژول تحلیل تکنیکال و فیلتر روند کلان (Multi-Timeframe Trend)
# =====================================================================
class TechnicalAgent:
    def __init__(self):
        self.exchanges = self._init_exchanges()

    def _init_exchanges(self):
        pools = []
        try:
            b = ccxt.binance({'enableRateLimit': True, 'urls': {'api': {'public': 'https://data-api.binance.vision/api/v3'}}})
            b.load_markets()
            pools.append(("Binance", b))
        except Exception: pass
        try:
            by = ccxt.bybit({'enableRateLimit': True, 'urls': {'api': {'public': 'https://api.bytick.com'}}})
            by.load_markets()
            pools.append(("Bybit", by))
        except Exception: pass
        return pools

    def fetch_candle_data(self, symbol: str, timeframe='15m', limit=70):
        search_symbols = [symbol]
        if symbol in ["XAU/USDT", "PAXG/USDT", "XAUT/USDT"]:
            search_symbols = ["PAXG/USDT", "PAXGUSDT", "XAUT/USDT", "XAU/USDT:USDT"]

        for s in search_symbols:
            for name, ex in self.exchanges:
                if s in ex.markets:
                    try:
                        ohlcv = ex.fetch_ohlcv(s, timeframe=timeframe, limit=limit)
                        if ohlcv and len(ohlcv) >= 40:
                            return ohlcv, ex, f"{name} ({s})"
                    except Exception:
                        continue
        return None, None, ""

    def check_macro_trend(self, symbol: str) -> str:
        """بررسی روند کلان ۱ ساعته با EMA 50 برای جلوگیری از معامله خلاف جهت"""
        ohlcv_1h, _, _ = self.fetch_candle_data(symbol, timeframe='1h', limit=60)
        if not ohlcv_1h:
            return "NEUTRAL"
        df = pd.DataFrame(ohlcv_1h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        ema50 = df['close'].ewm(span=50, adjust=False).mean().iloc[-1]
        last_close = df['close'].iloc[-1]
        return "BULLISH" if last_close > ema50 else "BEARISH"

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
# ماژول رصد نهنگ‌ها
# =====================================================================
class WhaleAgent:
    @staticmethod
    def inspect(symbol: str) -> dict:
        clean = symbol.replace("/", "").replace(":USDT", "")
        if "XAU" in clean: clean = "PAXGUSDT"
        metrics = {"funding": 0.01, "oi_val": 0.0, "top_ratio": 1.0, "whale_bias": "NEUTRAL"}
        try:
            rf = requests.get(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={clean}", timeout=3).json()
            if isinstance(rf, dict) and "lastFundingRate" in rf:
                metrics["funding"] = round(float(rf.get("lastFundingRate", 0)) * 100, 4)
        except Exception: pass
        try:
            rr = requests.get(f"https://fapi.binance.com/futures/data/topLongShortAccountRatio?symbol={clean}&period=15m&limit=1", timeout=3).json()
            if isinstance(rr, list) and len(rr) > 0:
                ratio = float(rr[0].get("longShortRatio", 1.0))
                metrics["top_ratio"] = round(ratio, 2)
                metrics["whale_bias"] = "WHALES NET LONG 🐋🟢" if ratio > 1.10 else ("WHALES NET SHORT 🐋🔴" if ratio < 0.90 else "BALANCED")
        except Exception: pass
        return metrics

# =====================================================================
# ماژول هوش مصنوعی
# =====================================================================
class MacroAIOfficerAgent:
    def review_setup(self, payload: dict) -> dict:
        prompt = (
            f"Review institutional setup:\nSymbol: {payload['symbol']} | Action: {payload['action']}\n"
            f"RSI: {payload['rsi_15m']:.1f} | Macro 1h Trend: {payload['macro_trend']} | L/S Ratio: {payload['top_ratio']}\n\n"
            f"CRITICAL: If trade is against Macro 1h Trend, strictly REJECT.\n"
            f"Start with 'VERDICT: CONFIRMED' or 'VERDICT: REJECTED'. Add 2 sentences reasoning."
        )
        analysis = ""
        engine = "Gemini Flash"
        if GEMINI_API_KEY:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
                res = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=8).json()
                analysis = res['candidates'][0]['content']['parts'][0]['text'].strip()
            except Exception: pass

        if not analysis and GROQ_API_KEY:
            engine = "Groq Llama-3"
            try:
                url = "https://api.groq.com/openai/v1/chat/completions"
                headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
                res = requests.post(url, headers=headers, json={"model": "llama-3.1-8b-instant", "messages": [{"role": "user", "content": prompt}], "max_tokens": 120}, timeout=8).json()
                analysis = res['choices'][0]['message']['content'].strip()
            except Exception: pass

        if not analysis:
            return {"approved": True, "engine": "Quant Strict Filter", "thesis": "Trend and order flow volume aligned."}

        approved = analysis.strip().upper().startswith("VERDICT: CONFIRMED") or "\nVERDICT: CONFIRMED" in analysis.strip().upper()
        thesis = analysis.replace("VERDICT: CONFIRMED", "").replace("VERDICT: REJECTED", "").strip()
        return {"approved": approved, "engine": engine, "thesis": thesis}

# =====================================================================
# ماژول ارسال پیام به تلگرام
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
        tp2_pct = abs((tp2 - price) / price) * 100
        tp3_pct = abs((tp3 - price) / price) * 100

        msg = (
            f"⚡ <b>HIGH-PROBABILITY SIGNAL (OPTIMIZED)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🌐 #{symbol_tag} | Pool: <b>{data['source']}</b>\n"
            f"{action_emoji} <b>{data['action']}</b>\n\n"
            f"🧭 روند کلان ۱ ساعته: <b>{data['macro_trend']}</b>\n"
            f"🧠 <b>تأیید هوش مصنوعی ({data['ai_engine']})</b>\n"
            f"<i>{data['ai_thesis']}</i>\n\n"
            f"📍 <b>نقطه ورود:</b> <code>{price:,.4f}</code>\n"
            f"🛑 <b>حد ضرر عریض (ضد هانت):</b> <code>{sl:,.4f}</code> (-{sl_pct:.2f}%)\n\n"
            f"🎯 <b>تارگت‌های سود:</b>\n"
            f"🥇 TP1 ➔ <code>{tp1:,.4f}</code> (+{tp1_pct:.2f}%) [انتقال به ریسک‌فری]\n"
            f"🥈 TP2 ➔ <code>{tp2:,.4f}</code> (+{tp2_pct:.2f}%)\n"
            f"🥉 TP3 ➔ <code>{tp3:,.4f}</code> (+{tp3_pct:.2f}%)\n\n"
            f"⏱️ <i>زمان صدور به وقت تهران: {get_tehran_time_str()}</i>"
        )
        send_telegram(msg)

# =====================================================================
# هسته هماهنگ‌کننده برنامه
# =====================================================================
def run_system():
    tech_agent = TechnicalAgent()
    macro_agent = MacroAIOfficerAgent()

    # ۱. رصد پوزیشن‌های باز قبلی و ثبت فیدبک پیروزی/شکست
    TradeLifecycleAgent.monitor_active_trades(tech_agent)

    # ۲. دریافت آستانه‌های دینامیک بر اساس نتایج قبلی
    thresholds = SelfLearningAgent.get_dynamic_thresholds()

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
            whale = WhaleAgent.inspect(symbol)
            macro_trend = tech_agent.check_macro_trend(symbol)

            setup = None

            # شرط خرید فوق بهینه: همبستگی با روند ۱ ساعته + حد ضرر ۲.۲ برابری ATR جهت جلوگیری از هانت
            if (rsi < thresholds['rsi_long'] and 
                imbalance > thresholds['imbalance_req'] and 
                whale['top_ratio'] >= thresholds['whale_min'] and 
                macro_trend == "BULLISH"):

                sl_price = price - (2.2 * atr)  # استاپ عریض ضد شکار شدوها
                tp1_price = price + (1.8 * atr) # تارگت اول در دسترس‌تر برای خروج سریع ریسک
                tp2_price = price + (3.5 * atr)
                tp3_price = price + (5.5 * atr)

                setup = {
                    'source': source_name, 'symbol': symbol, 'action': 'LONG — BUY SETUP',
                    'price': price, 'sl': sl_price, 'tp1': tp1_price, 'tp2': tp2_price, 'tp3': tp3_price,
                    'leverage': 3, 'rsi_15m': rsi, 'imbalance': imbalance, 'macro_trend': macro_trend,
                    'top_ratio': whale['top_ratio']
                }

            # شرط فروش فوق بهینه: روند کلان نزولی + فیلترهای تطبیقی
            elif (rsi > thresholds['rsi_short'] and 
                  imbalance < -thresholds['imbalance_req'] and 
                  whale['top_ratio'] <= (2.0 - thresholds['whale_min']) and 
                  macro_trend == "BEARISH"):

                sl_price = price + (2.2 * atr)
                tp1_price = price - (1.8 * atr)
                tp2_price = price - (3.5 * atr)
                tp3_price = price - (5.5 * atr)

                setup = {
                    'source': source_name, 'symbol': symbol, 'action': 'SHORT — SELL SETUP',
                    'price': price, 'sl': sl_price, 'tp1': tp1_price, 'tp2': tp2_price, 'tp3': tp3_price,
                    'leverage': 3, 'rsi_15m': rsi, 'imbalance': imbalance, 'macro_trend': macro_trend,
                    'top_ratio': whale['top_ratio']
                }

            if setup:
                review = macro_agent.review_setup(setup)
                if review['approved']:
                    setup['ai_engine'] = review['engine']
                    setup['ai_thesis'] = review['thesis']
                    DispatchAgent.send(setup)
                    TradeLifecycleAgent.register_trade(setup)
                    dispatched += 1
                    print(f"⚡ [OPTIMIZED DISPATCH]: {symbol} confirmed with {macro_trend} trend alignment.")

        except Exception:
            continue

if __name__ == "__main__":
    run_system()
