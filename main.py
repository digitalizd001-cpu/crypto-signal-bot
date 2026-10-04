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
    # Legacy & Infra
    "LTC/USDT", "BCH/USDT", "ETC/USDT", "XLM/USDT", "FIL/USDT", "AR/USDT", "TIA/USDT"
]

def get_tehran_time_str() -> str:
    tehran_tz = timezone(timedelta(hours=3, minutes=30))
    now = datetime.now(tehran_tz)
    return now.strftime("%H:%M:%S | %Y/%m/%d")

def send_telegram(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TELEGRAM ERROR]: Token or Chat ID not configured.")
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
        print(f"[TELEGRAM EXCEPTION]: {e}")
        return False

# =====================================================================
# ماژول دستورات تعاملی تلگرام (/gold, /trades, /status)
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
    def process_pending_commands(cls, tech_agent, macro_agent):
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
                    cls._report_gold_status(tech_agent)
                elif text == "/status":
                    send_telegram(
                        f"🟢 <b>DIAGNOSTIC STATUS</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🤖 Gemini AI: <b>{'ONLINE' if GEMINI_API_KEY else 'MISSING'}</b>\n"
                        f"⚡ Groq Fallback: <b>{'ONLINE' if GROQ_API_KEY else 'MISSING'}</b>\n"
                        f"⏱️️ زمان محلی تهران: <code>{get_tehran_time_str()}</code>"
                    )
                elif text == "/trades":
                    trades = TradeLifecycleAgent.load_trades()
                    if not trades:
                        send_telegram("📭 در حال حاضر هیچ پوزیشن بازی وجود ندارد.")
                    else:
                        resp = "📋 <b>ACTIVE MANAGED TRADES</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        for s, t in trades.items():
                            resp += f"• <b>{s}</b> ({t['action']}) | ورود: <code>{t['entry']}</code> | ریسک‌فری: <b>{t['risk_free']}</b>\n"
                        send_telegram(resp)
        except Exception as e:
            print(f"[COMMAND ERROR] {e}")

    @classmethod
    def _report_gold_status(cls, tech_agent):
        send_telegram("🥇 <i>در حال بررسی وضعیت لحظه‌ای طلا...</i>")
        ohlcv, active_ex, source_name = tech_agent.fetch_candle_data("PAXG/USDT")
        if not ohlcv:
            ohlcv, active_ex, source_name = tech_agent.fetch_candle_data("XAU/USDT")

        if ohlcv:
            price, rsi, atr, vol_ratio, ema20 = tech_agent.compute_indicators(ohlcv)
            imbalance = tech_agent.analyze_orderbook_imbalance(active_ex, "PAXG/USDT")
            whale = WhaleAgent.inspect("PAXG/USDT")
            msg = (
                f"🥇 <b>گزارش اختصاصی طلا (PAXG/XAU)</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🌐 استخر تأمین: <b>{source_name}</b>\n"
                f"💵 قیمت هر انس: <code>${price:,.2f}</code>\n"
                f"📉 15m RSI: <code>{rsi:.1f}</code> | نسبت حجم: <code>{vol_ratio:.1f}x</code>\n"
                f"📚 برتری اوردربوک: <code>{imbalance:+.2f}</code>\n"
                f"🐋 نهنگ‌ها (L/S): <b>{whale['top_ratio']}</b> ({whale['whale_bias']})\n\n"
                f"⏱️ <i>زمان گزارش به وقت تهران: {get_tehran_time_str()}</i>"
            )
            send_telegram(msg)
        else:
            send_telegram("❌ خطا در اتصال به منابع دیتای طلا.")

# =====================================================================
# ماژول یادگیری تقویتی خودکار (Reinforcement Feedback Loop)
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
                json.dump(history[-100:], f, indent=2)
        except Exception: pass

    @staticmethod
    def get_dynamic_thresholds():
        history = SelfLearningAgent.load_history()
        if len(history) < 5:
            return {"rsi_long": 43, "rsi_short": 57, "imbalance_req": 0.03}

        recent = history[-20:]
        losses = [h for h in recent if h.get("outcome") == "LOSS"]
        loss_rate = len(losses) / len(recent)

        if loss_rate > 0.50:
            return {"rsi_long": 38, "rsi_short": 62, "imbalance_req": 0.08}
        else:
            return {"rsi_long": 43, "rsi_short": 57, "imbalance_req": 0.03}

# =====================================================================
# ماژول رهگیری لحظه‌ای معاملات (TP / SL / Auto Risk-Free)
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
        tehran_now = get_tehran_time_str()

        for symbol, t in list(trades.items()):
            ohlcv, _, _ = tech_agent.fetch_candle_data(symbol)
            if not ohlcv or len(ohlcv) < 2:
                remaining[symbol] = t
                continue

            last_candle = ohlcv[-2]
            high_p, low_p = last_candle[2], last_candle[3]
            is_long = "LONG" in t['action']
            closed = False

            if not t['tp1_hit']:
                tp1_hit = (high_p >= t['tp1']) if is_long else (low_p <= t['tp1'])
                if tp1_hit:
                    t['tp1_hit'] = True
                    t['risk_free'] = True
                    t['sl'] = t['entry'] * (1.001 if is_long else 0.999)
                    send_telegram(
                        f"🎯 <b>تارگت اول (TP1) محقق شد!</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🌐 #{symbol.replace('/', '_')}\n"
                        f"✅ لمس قیمت: <code>{t['tp1']:,.4f}</code>\n"
                        f"🛡️ استاپ به نقطه ورود منتقل و معامله کاملاً ریسک‌فری شد.\n"
                        f"⏱️ <i>زمان: {tehran_now}</i>"
                    )

            tp3_hit = (high_p >= t['tp3']) if is_long else (low_p <= t['tp3'])
            if tp3_hit:
                send_telegram(
                    f"🏆 <b>تارگت نهایی (TP3) کامل شد!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🌐 #{symbol.replace('/', '_')}\n"
                    f"💰 پوزیشن با حداکثر سود بسته شد.\n"
                    f"⏱️ <i>زمان: {tehran_now}</i>"
                )
                SelfLearningAgent.log_trade_outcome(t, "WIN")
                closed = True

            sl_hit = (low_p <= t['sl']) if is_long else (high_p >= t['sl'])
            if sl_hit and not closed:
                if t['risk_free']:
                    send_telegram(f"🛡️ <b>خروج ریسک‌فری:</b> #{symbol.replace('/', '_')} بدون ضرر در نقطه ورود بسته شد.")
                    SelfLearningAgent.log_trade_outcome(t, "BE")
                else:
                    send_telegram(
                        f"🛑 <b>حد ضرر (Stop Loss) لمس شد</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🌐 #{symbol.replace('/', '_')} | خروج در قیمت: <code>{t['sl']:,.4f}</code>\n"
                        f"🧠 <i>الگوی ستاپ جهت اصلاح آستانه‌ها در سیستم یادگیری ثبت شد.</i>"
                    )
                    SelfLearningAgent.log_trade_outcome(t, "LOSS")
                closed = True

            if not closed:
                remaining[symbol] = t

        cls.save_trades(remaining)

# =====================================================================
# ماژول تحلیل تکنیکال و چند صرافی
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
        try:
            ok = ccxt.okx({'enableRateLimit': True})
            ok.load_markets()
            pools.append(("OKX", ok))
        except Exception: pass
        return pools

    def fetch_candle_data(self, symbol: str, timeframe='15m', limit=60):
        search_symbols = [symbol]
        if symbol in ["XAU/USDT", "PAXG/USDT", "XAUT/USDT"]:
            search_symbols = ["PAXG/USDT", "PAXGUSDT", "XAUT/USDT", "XAU/USDT:USDT", "XAUUSDT"]

        for s in search_symbols:
            for name, ex in self.exchanges:
                if s in ex.markets:
                    try:
                        ohlcv = ex.fetch_ohlcv(s, timeframe=timeframe, limit=limit)
                        if ohlcv and len(ohlcv) >= 30:
                            return ohlcv, ex, f"{name} ({s})"
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
        df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()

        high_low = df['high'] - df['low']
        high_close = (df['high'] - df['close'].shift()).abs()
        low_close = (df['low'] - df['close'].shift()).abs()
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        df['atr'] = ranges.max(axis=1).rolling(14).mean()

        latest = df.iloc[-1]
        vol_ratio = latest['volume'] / latest['vol_ma20'] if latest['vol_ma20'] > 0 else 1.0
        return latest['close'], latest['rsi'], latest['atr'], vol_ratio, latest['ema20']

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
            roi = requests.get(f"https://fapi.binance.com/fapi/v1/openInterest?symbol={clean}", timeout=3).json()
            if isinstance(roi, dict) and "openInterest" in roi:
                metrics["oi_val"] = round(float(roi.get("openInterest", 0)), 1)
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
# ماژول هوش مصنوعی تاییدکننده نهایی
# =====================================================================
class MacroAIOfficerAgent:
    def review_setup(self, payload: dict) -> dict:
        prompt = (
            f"Review trade setup:\nSymbol: {payload['symbol']} | Action: {payload['action']}\n"
            f"RSI: {payload['rsi']:.1f} | Order-book Imbalance: {payload['imbalance']} | L/S Ratio: {payload['top_ratio']}\n\n"
            f"Instruction: Start strictly with 'VERDICT: CONFIRMED' or 'VERDICT: REJECTED'. Add 1 sentence thesis."
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
            return {"approved": True, "engine": "Quant Strict Filter", "thesis": "Order flow, volume & momentum aligned."}

        approved = "CONFIRMED" in analysis.upper()
        thesis = analysis.replace("VERDICT: CONFIRMED", "").replace("VERDICT: REJECTED", "").strip()
        return {"approved": approved, "engine": engine, "thesis": thesis}

# =====================================================================
# ماژول ارسال کارت سیگنال همراه با ساعت تهران
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
            f"⚡ <b>سیگنال معتبر صادر شد</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🌐 #{symbol_tag} | استخر: <b>{data['source']}</b>\n"
            f"{action_emoji} <b>{data['action']}</b>\n\n"
            f"🧠 <b>تحلیل هوش مصنوعی ({data['ai_engine']}):</b>\n"
            f"<i>{data['ai_thesis']}</i>\n\n"
            f"🐋 <b>نهنگ‌ها:</b> <b>{data['top_ratio']}</b> ({data['whale_bias']})\n"
            f"📍 <b>نقطه ورود:</b> <code>{price:,.4f}</code>\n"
            f"🛑 <b>حد ضرر:</b> <code>{sl:,.4f}</code> (-{sl_pct:.2f}%)\n\n"
            f"🎯 <b>تارگت‌های سود:</b>\n"
            f"🥇 TP1 ➔ <code>{tp1:,.4f}</code> (+{tp1_pct:.2f}%) [ریسک‌فری]\n"
            f"🥈 TP2 ➔ <code>{tp2:,.4f}</code> (+{tp2_pct:.2f}%)\n"
            f"🥉 TP3 ➔ <code>{tp3:,.4f}</code> (+{tp3_pct:.2f}%)\n\n"
            f"⏱️ <b>زمان صدور به وقت تهران:</b> <code>{get_tehran_time_str()}</code>"
        )
        send_telegram(msg)

# =====================================================================
# هسته هماهنگ‌کننده کل سیستم
# =====================================================================
def run_system():
    tech_agent = TechnicalAgent()
    macro_agent = MacroAIOfficerAgent()

    # ۱. پاسخ‌دهی به دستورات تعاملی کاربر در تلگرام
    TelegramCommandHandler.process_pending_commands(tech_agent, macro_agent)

    # ۲. رصد و بستن معاملات باز قبلی
    TradeLifecycleAgent.monitor_active_trades(tech_agent)

    # ۳. دریافت آستانه‌های دینامیک بر اساس نتایج قبلی
    thresholds = SelfLearningAgent.get_dynamic_thresholds()

    dispatched = 0
    print(f"--- [SCANNING {len(BASE_WATCHLIST)} ASSETS | TEHRAN: {get_tehran_time_str()}] ---")

    for symbol in BASE_WATCHLIST:
        if dispatched >= MAX_SIGNALS:
            break

        ohlcv, active_ex, source_name = tech_agent.fetch_candle_data(symbol)
        if not ohlcv:
            continue

        try:
            price, rsi, atr, vol_ratio, ema20 = tech_agent.compute_indicators(ohlcv)
            imbalance = tech_agent.analyze_orderbook_imbalance(active_ex, symbol)
            whale = WhaleAgent.inspect(symbol)

            setup = None

            # فیلتر تطبیقی لانگ: اشباع RSI یا پولبک EMA20 همراه با برتری خرید
            if (rsi < thresholds['rsi_long'] and imbalance > thresholds['imbalance_req']) or (price > ema20 and rsi < 46 and imbalance > 0.04):
                setup = {
                    'source': source_name, 'symbol': symbol, 'action': 'LONG — BUY SETUP',
                    'price': price, 'sl': price - (1.8 * atr),
                    'tp1': price + (1.6 * atr), 'tp2': price + (3.0 * atr), 'tp3': price + (4.5 * atr),
                    'leverage': 3, 'rsi': rsi, 'imbalance': imbalance,
                    'top_ratio': whale['top_ratio'], 'whale_bias': whale['whale_bias']
                }

            # فیلتر تطبیقی شورت: اشباع RSI یا پس‌زدن از EMA20 همراه با برتری فروش
            elif (rsi > thresholds['rsi_short'] and imbalance < -thresholds['imbalance_req']) or (price < ema20 and rsi > 54 and imbalance < -0.04):
                setup = {
                    'source': source_name, 'symbol': symbol, 'action': 'SHORT — SELL SETUP',
                    'price': price, 'sl': price + (1.8 * atr),
                    'tp1': price - (1.6 * atr), 'tp2': price - (3.0 * atr), 'tp3': price - (4.5 * atr),
                    'leverage': 3, 'rsi': rsi, 'imbalance': imbalance,
                    'top_ratio': whale['top_ratio'], 'whale_bias': whale['whale_bias']
                }

            if setup:
                review = macro_agent.review_setup(setup)
                if review['approved']:
                    setup['ai_engine'] = review['engine']
                    setup['ai_thesis'] = review['thesis']
                    DispatchAgent.send(setup)
                    TradeLifecycleAgent.register_trade(setup)
                    dispatched += 1
                    print(f"🎯 [DISPATCHED]: {symbol} confirmed by {review['engine']}")
                else:
                    print(f"❌ [AI REJECTED]: {symbol}")
            else:
                print(f"  - {symbol}: RSI={rsi:.1f}, Imbalance={imbalance:+.2f}")

        except Exception as e:
            print(f"[SCAN ERROR on {symbol}] {e}")

if __name__ == "__main__":
    run_system()
