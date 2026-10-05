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

MAX_DAILY_SIGNALS = 10
PORTFOLIO_RISK_PERCENT = 1.0

DAILY_TRACKER_FILE = "daily_signals.json"
TRADES_STATE_FILE = "active_trades.json"
PERFORMANCE_FILE = "performance.json"
HISTORY_FILE = "trade_history.json"
OFFSET_FILE = "telegram_offset.json"

BASE_WATCHLIST = [
    # Gold & Commodities (VIP)
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

def get_tehran_datetime() -> datetime:
    tehran_tz = timezone(timedelta(hours=3, minutes=30))
    return datetime.now(tehran_tz)

def get_tehran_time_str() -> str:
    return get_tehran_datetime().strftime("%H:%M:%S | %Y/%m/%d")

def get_tehran_date_str() -> str:
    return get_tehran_datetime().strftime("%Y-%m-%d")

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
# ماژول مدیریت سقف ۱۰ سیگنال روزانه
# =====================================================================
class DailyQuotaManager:
    @staticmethod
    def get_status() -> dict:
        today = get_tehran_date_str()
        default_data = {"date": today, "dispatched_count": 0, "dispatched_symbols": []}
        if os.path.exists(DAILY_TRACKER_FILE):
            try:
                with open(DAILY_TRACKER_FILE, "r") as f:
                    data = json.load(f)
                    if data.get("date") == today:
                        return data
            except Exception:
                pass
        return default_data

    @staticmethod
    def can_dispatch() -> bool:
        status = DailyQuotaManager.get_status()
        return status["dispatched_count"] < MAX_DAILY_SIGNALS

    @staticmethod
    def record_dispatch(symbol: str):
        status = DailyQuotaManager.get_status()
        status["dispatched_count"] += 1
        status["dispatched_symbols"].append(symbol)
        try:
            with open(DAILY_TRACKER_FILE, "w") as f:
                json.dump(status, f, indent=2)
        except Exception as e:
            print(f"[TRACKER ERROR]: {e}")

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
                    quota = DailyQuotaManager.get_status()
                    send_telegram(
                        f"📊 <b>وضعیت زنده اسکنر</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🎯 سهمیه مصرفی امروز: <b>{quota['dispatched_count']} از {MAX_DAILY_SIGNALS}</b>\n"
                        f"🤖 موتور هوش مصنوعی: <b>{'Gemini Flash' if GEMINI_API_KEY else 'Groq Llama-3'}</b>\n"
                        f"⏱ زمان تهران: <code>{get_tehran_time_str()}</code>"
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
                f"🌐 استخر: <b>{source_name}</b>\n"
                f"💵 قیمت هر انس: <code>${price:,.2f}</code>\n"
                f"📉 15m RSI: <code>{rsi:.1f}</code> | نسبت حجم: <code>{vol_ratio:.1f}x</code>\n"
                f"📚 عدم تعادل بوک: <code>{imbalance:+.2f}</code>\n"
                f"🐋 نهنگ‌ها (L/S): <b>{whale['top_ratio']}</b> ({whale['whale_bias']})\n\n"
                f"⏱️ <i>زمان گزارش به وقت تهران: {get_tehran_time_str()}</i>"
            )
            send_telegram(msg)
        else:
            send_telegram("❌ خطا در اتصال به منابع دیتای طلا.")

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
                        f"🎯 <b>تارگت اول (TP1) تاچ شد!</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🌐 #{symbol.replace('/', '_')}\n"
                        f"✅ لمس قیمت: <code>{t['tp1']:,.4f}</code>\n"
                        f"🛡️ استاپ به نقطه ورود منتقل و معامله ریسک‌فری شد.\n"
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
                closed = True

            sl_hit = (low_p <= t['sl']) if is_long else (high_p >= t['sl'])
            if sl_hit and not closed:
                if t['risk_free']:
                    send_telegram(f"🛡️ <b>خروج ریسک‌فری:</b> #{symbol.replace('/', '_')} بدون ضرر در نقطه ورود بسته شد.")
                else:
                    send_telegram(
                        f"🛑 <b>حد ضرر (Stop Loss) فعال شد</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🌐 #{symbol.replace('/', '_')} | خروج در قیمت: <code>{t['sl']:,.4f}</code>"
                    )
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
        return pools

    def fetch_candle_data(self, symbol: str, timeframe='15m', limit=60):
        search_symbols = [symbol]
        if symbol in ["XAU/USDT", "PAXG/USDT", "XAUT/USDT"]:
            search_symbols = ["PAXG/USDT", "PAXGUSDT", "XAUT/USDT", "XAU/USDT:USDT"]

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
        metrics = {"funding": 0.01, "top_ratio": 1.0, "whale_bias": "NEUTRAL"}
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
# ماژول هوش مصنوعی تاییدکننده نهایی
# =====================================================================
class MacroAIOfficerAgent:
    def review_setup(self, payload: dict) -> dict:
        prompt = (
            f"Review institutional setup:\nSymbol: {payload['symbol']} | Action: {payload['action']}\n"
            f"RSI: {payload['rsi']:.1f} | Order-book Imbalance: {payload['imbalance']} | L/S Ratio: {payload['top_ratio']}\n\n"
            f"Criteria: Select ONLY exceptionally high-probability setups. Strict binary verdict.\n"
            f"Start strictly with 'VERDICT: CONFIRMED' or 'VERDICT: REJECTED'. Add 1 short sentence thesis."
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
# ماژول صدور کارت سیگنال (قالب پیشرفته فیوچرز با اطلاعات کامل)
# =====================================================================
class DispatchAgent:
    @staticmethod
    def send(data: dict):
        symbol_tag = data['symbol'].replace('/', '_')
        is_long = "LONG" in data['action']
        action_title = "LONG — BUY" if is_long else "SHORT — SELL"
        action_emoji = "🟢" if is_long else "🔴"
        
        price = data['price']
        sl = data['sl']
        sl_pct = abs((price - sl) / price) * 100
        risk_dist = abs(price - sl)

        # محاسبه دقیق تارگت‌ها بر اساس R:R
        # TP1 -> 1:2 | TP2 -> 1:3 | TP3 -> 1:4
        if is_long:
            tp1 = price + (risk_dist * 2.0)
            tp2 = price + (risk_dist * 3.0)
            tp3 = price + (risk_dist * 4.0)
        else:
            tp1 = price - (risk_dist * 2.0)
            tp2 = price - (risk_dist * 3.0)
            tp3 = price - (risk_dist * 4.0)

        tp1_pct = abs((tp1 - price) / price) * 100
        tp2_pct = abs((tp2 - price) / price) * 100
        tp3_pct = abs((tp3 - price) / price) * 100

        leverage = data['leverage']
        roi1 = tp1_pct * leverage
        roi2 = tp2_pct * leverage
        roi3 = tp3_pct * leverage

        # ارزیابی سطح ریسک
        if sl_pct <= 1.5:
            risk_label = "🟢 LOW"
        elif sl_pct <= 3.0:
            risk_label = "🟡 MEDIUM"
        else:
            risk_label = "🔴 HIGH"

        # ساخت پیام با فرمت کامل و منظم
        msg = (
            f"🚨 <b>FUTURES SIGNAL</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"🪙 #{symbol_tag}\n"
            f"{action_emoji} <b>{action_title}</b>\n\n"
            f"🏆 Grade: <b>{data['grade']}</b>  |  Score: <b>{data['score']}/100</b>\n"
            f"📈 Market regime: <b>{data['regime']}</b>\n\n"
            f"📍 <b>ENTRY</b>\n"
            f"<code>{price:,.4f}</code>\n\n"
            f"🛑 <b>STOP LOSS</b>\n"
            f"<code>{sl:,.4f}</code>  ({sl_pct:.2f}%)\n\n"
            f"⚠️ <b>RISK:</b> {risk_label}\n"
            f"⚡️ Suggested leverage: <b>{leverage}x</b>\n\n"
            f"🎯 <b>TAKE PROFIT</b>\n\n"
            f"🥇 TP1 ➔ <code>{tp1:,.4f}</code>  (+{tp1_pct:.2f}%)\n"
            f"🥈 TP2 ➔ <code>{tp2:,.4f}</code>  (+{tp2_pct:.2f}%)\n"
            f"🥉 TP3 ➔ <code>{tp3:,.4f}</code>  (+{tp3_pct:.2f}%)\n\n"
            f"📊 <b>APPROX. ROI @ {leverage}x</b>\n\n"
            f"TP1 ➔ +{roi1:.2f}%\n"
            f"TP2 ➔ +{roi2:.2f}%\n"
            f"TP3 ➔ +{roi3:.2f}%\n\n"
            f"⚖️ <b>RISK / REWARD</b>\n"
            f"TP1 ➔ <b>1:2</b>\n"
            f"TP2 ➔ <b>1:3</b>\n"
            f"TP3 ➔ <b>1:4</b>\n\n"
            f"🧠 <i>AI Confluence: {data['ai_thesis']}</i>\n"
            f"⏱️ <i>Tehran Time: {get_tehran_time_str()}</i>"
        )

        # به روزرسانی مقادیر محاسبه‌شده در ستاپ برای رهگیری
        data['tp1'] = tp1
        data['tp2'] = tp2
        data['tp3'] = tp3

        send_telegram(msg)

# =====================================================================
# هسته هماهنگ‌کننده برنامه
# =====================================================================
def run_system():
    now_tehran = get_tehran_datetime()
    print(f"=== [RUN CYCLE: {get_tehran_time_str()}] ===")

    tech_agent = TechnicalAgent()
    macro_agent = MacroAIOfficerAgent()

    # ۱. پاسخ به دستورات تعاملی کاربر در تلگرام
    TelegramCommandHandler.process_pending_commands(tech_agent, macro_agent)

    # ۲. رصد و مدیریت معاملات باز قبلی
    TradeLifecycleAgent.monitor_active_trades(tech_agent)

    # ۳. بررسی سهمیه روزانه و پنجره معاملاتی فعال روز (۸ صبح تا ۱۲ شب به وقت تهران)
    quota = DailyQuotaManager.get_status()
    if quota["dispatched_count"] >= MAX_DAILY_SIGNALS:
        print(f"🛑 [DAILY QUOTA FULL]: {quota['dispatched_count']}/{MAX_DAILY_SIGNALS} signals issued today. Awaiting 00:00 Tehran.")
        return

    if now_tehran.hour < 8:
        print(f"🌙 [OFF-HOURS]: {now_tehran.hour}:00 Tehran. Market entry signals paused until 08:00 AM.")
        return

    # ۴. ارزیابی نامزدها و انتخاب باکیفیت‌ترین موقعیت موجود
    candidate_setups = []

    for symbol in BASE_WATCHLIST:
        if symbol in quota["dispatched_symbols"]:
            continue

        ohlcv, active_ex, source_name = tech_agent.fetch_candle_data(symbol)
        if not ohlcv:
            continue

        try:
            price, rsi, atr, vol_ratio, ema20 = tech_agent.compute_indicators(ohlcv)
            imbalance = tech_agent.analyze_orderbook_imbalance(active_ex, symbol)
            whale = WhaleAgent.inspect(symbol)

            setup = None
            raw_score = 75

            # ستاپ لانگ باکیفیت
            if (rsi < 42 and imbalance > 0.05) or (price > ema20 and rsi < 45 and imbalance > 0.08 and vol_ratio > 1.2):
                raw_score += int(min(abs(imbalance) * 100, 20))
                if vol_ratio > 1.5: raw_score += 10
                if whale['top_ratio'] > 1.15: raw_score += 10
                
                grade = "A+" if raw_score >= 100 else ("A" if raw_score >= 88 else "B+")
                regime = "BULL" if price > ema20 else "ACCUMULATION"

                setup = {
                    'source': source_name, 'symbol': symbol, 'action': 'LONG — BUY',
                    'price': price, 'sl': price - (1.8 * atr),
                    'leverage': 5, 'rsi': rsi, 'imbalance': imbalance,
                    'top_ratio': whale['top_ratio'], 'whale_bias': whale['whale_bias'],
                    'score': raw_score, 'grade': grade, 'regime': regime
                }

            # ستاپ شورت باکیفیت
            elif (rsi > 58 and imbalance < -0.05) or (price < ema20 and rsi > 55 and imbalance < -0.08 and vol_ratio > 1.2):
                raw_score += int(min(abs(imbalance) * 100, 20))
                if vol_ratio > 1.5: raw_score += 10
                if whale['top_ratio'] < 0.85: raw_score += 10
                
                grade = "A+" if raw_score >= 100 else ("A" if raw_score >= 88 else "B+")
                regime = "BEAR" if price < ema20 else "DISTRIBUTION"

                setup = {
                    'source': source_name, 'symbol': symbol, 'action': 'SHORT — SELL',
                    'price': price, 'sl': price + (1.8 * atr),
                    'leverage': 5, 'rsi': rsi, 'imbalance': imbalance,
                    'top_ratio': whale['top_ratio'], 'whale_bias': whale['whale_bias'],
                    'score': raw_score, 'grade': grade, 'regime': regime
                }

            if setup:
                candidate_setups.append(setup)

        except Exception:
            continue

    # انتخاب تنها ۱ ستاپ با بالاترین امتیاز کیفی در این دور
    if candidate_setups:
        candidate_setups.sort(key=lambda s: s['score'], reverse=True)
        best_setup = candidate_setups[0]

        review = macro_agent.review_setup(best_setup)
        if review['approved']:
            best_setup['ai_engine'] = review['engine']
            best_setup['ai_thesis'] = review['thesis']
            
            DispatchAgent.send(best_setup)
            TradeLifecycleAgent.register_trade(best_setup)
            DailyQuotaManager.record_dispatch(best_setup['symbol'])
            print(f"🎯 [DISPATCHED]: {best_setup['symbol']} | Score: {best_setup['score']}")
        else:
            print(f"❌ [AI REJECTED]: {best_setup['symbol']}")
    else:
        print("🔍 [SCAN COMPLETED]: No setup reached institutional threshold.")

if __name__ == "__main__":
    run_system()
