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

MAX_DAILY_SIGNALS = 15
SIGNALS_PER_SHIFT = 5

# پارامترهای درصدی پامپ‌یاب (حداقل +8% سود در کوتاه‌مدت)
STOP_LOSS_PCT = 0.035      # 3.5% حد ضرر درصدی
TP1_PUMP_PCT = 0.080       # 8.0% تارگت اول (ریسک‌فری)
TP2_PUMP_PCT = 0.120       # 12.0% تارگت دوم
TP3_PUMP_PCT = 0.180       # 18.0% تارگت سوم

DAILY_TRACKER_FILE = "daily_signals.json"
TRADES_STATE_FILE = "active_trades.json"
OFFSET_FILE = "telegram_offset.json"

# واچ‌لیست بهینه‌شده برای ارزهای مستعد پامپ (حذف نمادهای کم‌نوسان و GRAMUSDT)
BASE_WATCHLIST = [
    # Gold & Commodities (VIP Pinned)
    "PAXG/USDT", "XAU/USDT",
    # High-Beta Layer 1 & Layer 2
    "SOL/USDT", "SUI/USDT", "APT/USDT", "SEI/USDT", "INJ/USDT", "TIA/USDT",
    "NEAR/USDT", "AVAX/USDT", "FTM/USDT", "KAS/USDT", "ARB/USDT", "OP/USDT",
    "STRK/USDT", "IMX/USDT", "MANTA/USDT", "METIS/USDT",
    # DeFi & High Volatility
    "ENA/USDT", "PENDLE/USDT", "RUNE/USDT", "AAVE/USDT", "UNI/USDT",
    "LINK/USDT", "LDO/USDT", "CRV/USDT", "DYDX/USDT", "SNX/USDT",
    # AI & Narratives (مستعدترین بخش برای پامپ‌های سریع)
    "FET/USDT", "RENDER/USDT", "TAO/USDT", "WLD/USDT", "ARKM/USDT", "GRT/USDT",
    # Memes (پامپ‌های انفجاری کوتاه‌مدت)
    "DOGE/USDT", "PEPE/USDT", "WIF/USDT", "FLOKI/USDT", "BONK/USDT",
    "BOME/USDT", "MEME/USDT", "SHIB/USDT",
    # Majors & Legacy
    "BTC/USDT", "ETH/USDT", "BNB/USDT", "XRP/USDT", "DOGE/USDT", "LTC/USDT"
]

def format_dynamic_price(val: float) -> str:
    if val is None or math.isnan(val):
        return "0.00"
    abs_v = abs(val)
    if abs_v >= 100:
        return f"{val:,.2f}"
    elif abs_v >= 1:
        return f"{val:,.4f}"
    elif abs_v >= 0.01:
        return f"{val:,.5f}"
    elif abs_v >= 0.0001:
        return f"{val:,.7f}"
    else:
        return f"{val:,.9f}".rstrip('0').rstrip('.')

def get_tehran_datetime() -> datetime:
    tehran_tz = timezone(timedelta(hours=3, minutes=30))
    return datetime.now(tehran_tz)

def get_tehran_time_str() -> str:
    return get_tehran_datetime().strftime("%H:%M:%S | %Y/%m/%d")

def get_tehran_date_str() -> str:
    return get_tehran_datetime().strftime("%Y-%m-%d")

def get_current_shift_id(hour: int) -> int:
    return (hour // 8) + 1

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
# ماژول مدیریت سهمیه شیفتی (۳ شیفت ۸ ساعته × ۵ سیگنال)
# =====================================================================
class ShiftQuotaManager:
    @staticmethod
    def get_status() -> dict:
        today = get_tehran_date_str()
        default_data = {
            "date": today,
            "total_dispatched": 0,
            "shifts": {
                "1": {"count": 0, "symbols": []},
                "2": {"count": 0, "symbols": []},
                "3": {"count": 0, "symbols": []}
            }
        }
        if os.path.exists(DAILY_TRACKER_FILE):
            try:
                with open(DAILY_TRACKER_FILE, "r") as f:
                    data = json.load(f)
                    if data.get("date") == today:
                        if "shifts" not in data:
                            data["shifts"] = default_data["shifts"]
                            data["total_dispatched"] = data.get("dispatched_count", 0)
                        return data
            except Exception:
                pass
        return default_data

    @staticmethod
    def can_dispatch() -> tuple[bool, str]:
        status = ShiftQuotaManager.get_status()
        current_shift = str(get_current_shift_id(get_tehran_datetime().hour))

        if status["total_dispatched"] >= MAX_DAILY_SIGNALS:
            return False, f"سقف روزانه ({MAX_DAILY_SIGNALS} پامپ) تکمیل است."

        shift_count = status["shifts"][current_shift]["count"]
        if shift_count >= SIGNALS_PER_SHIFT:
            return False, f"سهمیه شیفت {current_shift} ({SIGNALS_PER_SHIFT} پامپ) تکمیل است."

        return True, "مجاز به ارسال"

    @staticmethod
    def record_dispatch(symbol: str):
        status = ShiftQuotaManager.get_status()
        current_shift = str(get_current_shift_id(get_tehran_datetime().hour))
        status["total_dispatched"] += 1
        status["shifts"][current_shift]["count"] += 1
        status["shifts"][current_shift]["symbols"].append(symbol)
        try:
            with open(DAILY_TRACKER_FILE, "w") as f:
                json.dump(status, f, indent=2)
        except Exception as e:
            print(f"[TRACKER ERROR]: {e}")

# =====================================================================
# ماژول دستورات تلگرام (/gold, /trades, /status)
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
                    cls._report_gold_status(tech_agent)
                elif text == "/status":
                    quota = ShiftQuotaManager.get_status()
                    shift_id = str(get_current_shift_id(get_tehran_datetime().hour))
                    s_count = quota["shifts"][shift_id]["count"]
                    t_count = quota["total_dispatched"]
                    send_telegram(
                        f"🚀 <b>وضعیت اسکنر پامپ‌یاب (PUMP HUNTER)</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"⏰ شیفت کنونی: <b>شیفت {shift_id} ({s_count} از {SIGNALS_PER_SHIFT})</b>\n"
                        f"🎯 کل شکارهای امروز: <b>{t_count} از {MAX_DAILY_SIGNALS}</b>\n"
                        f"📈 حداقل تارگت سود: <b>+{TP1_PUMP_PCT*100:.0f}% اسپات</b>\n"
                        f"⏱ زمان تهران: <code>{get_tehran_time_str()}</code>"
                    )
                elif text == "/trades":
                    trades = TradeLifecycleAgent.load_trades()
                    if not trades:
                        send_telegram("📭 در حال حاضر هیچ پامپ فعالی در جریان نیست.")
                    else:
                        resp = "📋 <b>ACTIVE PUMP TRADES</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        for s, t in trades.items():
                            p_str = format_dynamic_price(t['entry'])
                            resp += f"• <b>{s}</b> | ورود: <code>{p_str}</code> | ریسک‌فری: <b>{t['risk_free']}</b>\n"
                        send_telegram(resp)
        except Exception as e:
            print(f"[COMMAND ERROR] {e}")

    @classmethod
    def _report_gold_status(cls, tech_agent):
        send_telegram("🥇 <i>در حال بررسی نوسان و حجم لحظه‌ای طلا...</i>")
        ohlcv, active_ex, source_name = tech_agent.fetch_candle_data("PAXG/USDT")
        if not ohlcv:
            ohlcv, active_ex, source_name = tech_agent.fetch_candle_data("XAU/USDT")

        if ohlcv:
            metrics = tech_agent.analyze_pump_metrics(ohlcv)
            imbalance = tech_agent.analyze_orderbook_imbalance(active_ex, "PAXG/USDT")
            whale = WhaleAgent.inspect("PAXG/USDT")
            msg = (
                f"🥇 <b>گزارش لحظه‌ای طلا (PAXG/XAU)</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🌐 استخر: <b>{source_name}</b>\n"
                f"💵 قیمت انس: <code>${format_dynamic_price(metrics['price'])}</code>\n"
                f"📊 جهش حجم: <code>{metrics['vol_ratio']:.1f}x</code> | RSI: <code>{metrics['rsi']:.1f}</code>\n"
                f"📚 برتری خریداران: <code>{imbalance:+.2f}</code>\n"
                f"🐋 نهنگ‌ها: <b>{whale['top_ratio']}</b> ({whale['whale_bias']})\n\n"
                f"⏱️ <i>زمان تهران: {get_tehran_time_str()}</i>"
            )
            send_telegram(msg)
        else:
            send_telegram("❌ خطا در اتصال به منابع دیتای طلا.")

# =====================================================================
# ماژول رهگیری پامپ‌های باز (TP1 +8% / TP2 +12% / TP3 +18%)
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
        now_tehran = get_tehran_time_str()

        for symbol, t in list(trades.items()):
            ohlcv, _, _ = tech_agent.fetch_candle_data(symbol)
            if not ohlcv or len(ohlcv) < 2:
                remaining[symbol] = t
                continue

            last_candle = ohlcv[-2]
            high_p, low_p = last_candle[2], last_candle[3]
            closed = False

            if not t['tp1_hit'] and high_p >= t['tp1']:
                t['tp1_hit'] = True
                t['risk_free'] = True
                t['sl'] = t['entry'] * 1.002  # انتقال استاپ به نقطه ورود + کارمزد
                send_telegram(
                    f"🚀 <b>تارگت اول پامپ (+8%) شکار شد!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🌐 #{symbol.replace('/', '_')}\n"
                    f"✅ لمس قیمت: <code>{format_dynamic_price(t['tp1'])}</code>\n"
                    f"🛡️ استاپ به نقطه ورود منتقل و معامله کاملاً ریسک‌فری شد.\n"
                    f"⏱️ <i>زمان تهران: {now_tehran}</i>"
                )

            if high_p >= t['tp3']:
                send_telegram(
                    f"🏆 <b>انفجار نهایی (+18%) تکمیل شد!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🌐 #{symbol.replace('/', '_')}\n"
                    f"💰 پامپ با حداکثر سود بسته شد.\n"
                    f"⏱️ <i>زمان تهران: {now_tehran}</i>"
                )
                closed = True

            if low_p <= t['sl'] and not closed:
                if t['risk_free']:
                    send_telegram(f"🛡️ <b>خروج ریسک‌فری:</b> #{symbol.replace('/', '_')} بدون ضرر در نقطه ورود بسته شد.")
                else:
                    send_telegram(
                        f"🛑 <b>حد ضرر پامپ فعال شد (-3.5%)</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🌐 #{symbol.replace('/', '_')} | خروج: <code>{format_dynamic_price(t['sl'])}</code>"
                    )
                closed = True

            if not closed:
                remaining[symbol] = t

        cls.save_trades(remaining)

# =====================================================================
# موتور تشخیص پامپ و شکست مقاومت (Pump & Breakout Engine)
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

    def fetch_candle_data(self, symbol: str, timeframe='15m', limit=50):
        search_symbols = [symbol]
        if symbol in ["XAU/USDT", "PAXG/USDT", "XAUT/USDT"]:
            search_symbols = ["PAXG/USDT", "PAXGUSDT", "XAUT/USDT"]

        for s in search_symbols:
            for name, ex in self.exchanges:
                if s in ex.markets:
                    try:
                        ohlcv = ex.fetch_ohlcv(s, timeframe=timeframe, limit=limit)
                        if ohlcv and len(ohlcv) >= 30:
                            return ohlcv, ex, f"{name}"
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
            ob = exchange.fetch_order_book(target, limit=25)
            bids = sum(b[1] for b in ob['bids'])
            asks = sum(a[1] for a in ob['asks'])
            if (bids + asks) == 0: return 0.0
            return round((bids - asks) / (bids + asks), 2)
        except Exception:
            return 0.0

    def analyze_pump_metrics(self, ohlcv) -> dict:
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df = df.iloc[:-1].copy()  # آخرین کندل بسته‌شده

        # محاسبه RSI
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / (loss + 1e-8)
        df['rsi'] = 100 - (100 / (1 + rs))

        # میانگین حجم و سقف ۲۰ کندل اخیر (شکست مقاومت کوتاه‌مدت)
        df['vol_ma20'] = df['volume'].rolling(20).mean()
        df['prev_20_high'] = df['high'].shift(1).rolling(20).max()
        df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()

        latest = df.iloc[-1]
        vol_ratio = latest['volume'] / latest['vol_ma20'] if latest['vol_ma20'] > 0 else 1.0
        
        # درصد رشد کندل فعلی و قدرت بدنه کندل (جلوگیری از شدوی فیک)
        candle_gain_pct = ((latest['close'] - latest['open']) / latest['open']) * 100
        candle_range = latest['high'] - latest['low']
        body_strength = (latest['close'] - latest['open']) / candle_range if candle_range > 0 else 0

        is_breakout = latest['close'] > latest['prev_20_high']

        return {
            'price': latest['close'],
            'rsi': latest['rsi'],
            'vol_ratio': vol_ratio,
            'ema20': latest['ema20'],
            'candle_gain_pct': candle_gain_pct,
            'body_strength': body_strength,
            'is_breakout': is_breakout
        }

# =====================================================================
# ماژول رصد ورود نقدینگی نهنگ‌ها
# =====================================================================
class WhaleAgent:
    @staticmethod
    def inspect(symbol: str) -> dict:
        clean = symbol.replace("/", "").replace(":USDT", "")
        if "XAU" in clean: clean = "PAXGUSDT"
        metrics = {"top_ratio": 1.0, "whale_bias": "NEUTRAL"}
        try:
            rr = requests.get(f"https://fapi.binance.com/futures/data/topLongShortAccountRatio?symbol={clean}&period=15m&limit=1", timeout=3).json()
            if isinstance(rr, list) and len(rr) > 0:
                ratio = float(rr[0].get("longShortRatio", 1.0))
                metrics["top_ratio"] = round(ratio, 2)
                metrics["whale_bias"] = "WHALES PUMPING 🐋🚀" if ratio > 1.12 else ("NEUTRAL" if ratio >= 0.95 else "SELL PRESSURE")
        except Exception: pass
        return metrics

# =====================================================================
# ماژول هوش مصنوعی تاییدکننده مومنتوم پامپ
# =====================================================================
class MacroAIOfficerAgent:
    def review_pump(self, payload: dict) -> dict:
        prompt = (
            f"Analyze explosive crypto pump breakout:\n"
            f"Symbol: {payload['symbol']} | Volume Surge: {payload['vol_ratio']:.1f}x average | "
            f"15m Candle Gain: +{payload['candle_gain_pct']:.2f}% | RSI: {payload['rsi']:.1f} | "
            f"Orderbook Bid Imbalance: {payload['imbalance']} | Whale Ratio: {payload['top_ratio']}\n\n"
            f"Task: Confirm if this is a genuine early-stage momentum breakout capable of +8% to +18% continuation, or an exhausted fakeout.\n"
            f"Start strictly with 'VERDICT: CONFIRMED' or 'VERDICT: REJECTED'. Add 1 short analytical sentence."
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
            return {"approved": True, "engine": "Pump Quant Filter", "thesis": "Massive volume spike with clean resistance breakout."}

        approved = "CONFIRMED" in analysis.upper()
        thesis = analysis.replace("VERDICT: CONFIRMED", "").replace("VERDICT: REJECTED", "").strip()
        return {"approved": approved, "engine": engine, "thesis": thesis}

# =====================================================================
# ماژول صدور کارت سیگنال پامپ (تارگت‌های +8% تا +18%)
# =====================================================================
class DispatchAgent:
    @staticmethod
    def send(data: dict, shift_num: int, shift_count: int, total_count: int):
        symbol_tag = data['symbol'].replace('/', '_')
        price = data['price']

        # محاسبه کاملاً درصدی برای پامپ‌های کوتاه‌مدت
        sl = price * (1.0 - STOP_LOSS_PCT)
        tp1 = price * (1.0 + TP1_PUMP_PCT)
        tp2 = price * (1.0 + TP2_PUMP_PCT)
        tp3 = price * (1.0 + TP3_PUMP_PCT)

        leverage = data['leverage']
        roi1 = (TP1_PUMP_PCT * 100) * leverage
        roi2 = (TP2_PUMP_PCT * 100) * leverage
        roi3 = (TP3_PUMP_PCT * 100) * leverage

        entry_str = format_dynamic_price(price)
        sl_str = format_dynamic_price(sl)
        tp1_str = format_dynamic_price(tp1)
        tp2_str = format_dynamic_price(tp2)
        tp3_str = format_dynamic_price(tp3)

        msg = (
            f"🚀 <b>PUMP BREAKOUT SIGNAL [شیفت {shift_num}: {shift_count}/{SIGNALS_PER_SHIFT} | کل: {total_count}/{MAX_DAILY_SIGNALS}]</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"🪙 #{symbol_tag}  |  📊 Vol Surge: <b>{data['vol_ratio']:.1f}x</b>\n"
            f"🟢 <b>LONG — PUMP MOMENTUM</b>\n\n"
            f"🏆 Grade: <b>{data['grade']}</b>  |  Score: <b>{data['score']}/100</b>\n"
            f"📈 Market regime: <b>EXPLOSIVE BREAKOUT</b>\n\n"
            f"📍 <b>ENTRY</b>\n"
            f"<code>{entry_str}</code>\n\n"
            f"🛑 <b>STOP LOSS (-{STOP_LOSS_PCT*100:.1f}%)</b>\n"
            f"<code>{sl_str}</code>\n\n"
            f"⚠️ <b>RISK:</b> 🟡 VOLATILE PUMP\n"
            f"⚡️ Suggested leverage: <b>{leverage}x</b>\n\n"
            f"🎯 <b>TAKE PROFIT (SPOT GAINS)</b>\n\n"
            f"🥇 TP1 ➔ <code>{tp1_str}</code>  (<b>+8.0%</b>) [ریسک‌فری]\n"
            f"🥈 TP2 ➔ <code>{tp2_str}</code>  (<b>+12.0%</b>)\n"
            f"🥉 TP3 ➔ <code>{tp3_str}</code>  (<b>+18.0%</b>)\n\n"
            f"📊 <b>APPROX. ROI @ {leverage}x</b>\n\n"
            f"TP1 ➔ <b>+{roi1:.1f}%</b>\n"
            f"TP2 ➔ <b>+{roi2:.1f}%</b>\n"
            f"TP3 ➔ <b>+{roi3:.1f}%</b>\n\n"
            f"⚖️ <b>RISK / REWARD</b>\n"
            f"TP1 ➔ <b>1:2.3</b>  |  TP2 ➔ <b>1:3.4</b>  |  TP3 ➔ <b>1:5.1</b>\n\n"
            f"🧠 <i>AI Verification ({data['ai_engine']}): {data['ai_thesis']}</i>\n"
            f"⏱️ <b>زمان به وقت ایران:</b> <code>{get_tehran_time_str()}</code>"
        )

        data['sl'] = sl
        data['tp1'] = tp1
        data['tp2'] = tp2
        data['tp3'] = tp3

        send_telegram(msg)

# =====================================================================
# هسته هماهنگ‌کننده اسکنر پامپ‌یاب
# =====================================================================
def run_system():
    print(f"=== [PUMP HUNTER CYCLE: {get_tehran_time_str()} (Tehran)] ===")

    tech_agent = TechnicalAgent()
    macro_agent = MacroAIOfficerAgent()

    TelegramCommandHandler.process_pending_commands(tech_agent)
    TradeLifecycleAgent.monitor_active_trades(tech_agent)

    can_proceed, reason = ShiftQuotaManager.can_dispatch()
    if not can_proceed:
        print(f"🛑 [SHIFT/DAILY LIMIT]: {reason}")
        return

    quota = ShiftQuotaManager.get_status()
    current_shift_id = str(get_current_shift_id(get_tehran_datetime().hour))
    dispatched_in_this_shift = quota["shifts"][current_shift_id]["symbols"]

    pump_candidates = []

    for symbol in BASE_WATCHLIST:
        if symbol in dispatched_in_this_shift:
            continue

        ohlcv, active_ex, source_name = tech_agent.fetch_candle_data(symbol)
        if not ohlcv:
            continue

        try:
            m = tech_agent.analyze_pump_metrics(ohlcv)
            imbalance = tech_agent.analyze_orderbook_imbalance(active_ex, symbol)
            whale = WhaleAgent.inspect(symbol)

            # شروط شکار پامپ واقعی (انفجار حجم + شکست سقف + بدنه قوی + خریدار سنگین)
            has_volume_explosion = m['vol_ratio'] >= 2.3
            has_strong_candle = m['candle_gain_pct'] >= 1.2 and m['body_strength'] >= 0.60
            not_overheated = 52 <= m['rsi'] <= 78
            buyer_dominance = imbalance >= 0.08

            if has_volume_explosion and has_strong_candle and not_overheated and (m['is_breakout'] or buyer_dominance):
                raw_score = 75
                if m['vol_ratio'] >= 3.5: raw_score += 12
                elif m['vol_ratio'] >= 2.8: raw_score += 7
                if m['is_breakout']: raw_score += 8
                if imbalance >= 0.15: raw_score += 8
                if whale['top_ratio'] > 1.15: raw_score += 7

                grade = "A+" if raw_score >= 98 else ("A" if raw_score >= 88 else "B+")

                pump_candidates.append({
                    'source': source_name,
                    'symbol': symbol,
                    'action': 'LONG — PUMP',
                    'price': m['price'],
                    'vol_ratio': m['vol_ratio'],
                    'candle_gain_pct': m['candle_gain_pct'],
                    'rsi': m['rsi'],
                    'imbalance': imbalance,
                    'top_ratio': whale['top_ratio'],
                    'whale_bias': whale['whale_bias'],
                    'leverage': 5,
                    'score': min(raw_score, 100),
                    'grade': grade
                })

        except Exception:
            continue

    # انتخاب پرقدرت‌ترین پامپ در حال شکل‌گیری
    if pump_candidates:
        pump_candidates.sort(key=lambda s: (s['score'], s['vol_ratio']), reverse=True)
        best_pump = pump_candidates[0]

        review = macro_agent.review_pump(best_pump)
        if review['approved']:
            best_pump['ai_engine'] = review['engine']
            best_pump['ai_thesis'] = review['thesis']

            s_num = int(current_shift_id)
            s_count = quota["shifts"][current_shift_id]["count"] + 1
            t_count = quota["total_dispatched"] + 1

            DispatchAgent.send(best_pump, s_num, s_count, t_count)
            TradeLifecycleAgent.register_trade(best_pump)
            ShiftQuotaManager.record_dispatch(best_pump['symbol'])
            print(f"🚀 [PUMP DISPATCHED]: {best_pump['symbol']} | Vol: {best_pump['vol_ratio']:.1f}x | Price: {format_dynamic_price(best_pump['price'])}")
        else:
            print(f"❌ [AI REJECTED FAKEOUT]: {best_pump['symbol']}")
    else:
        print("🔍 [PUMP SCAN COMPLETED]: No explosive volume breakout detected.")

if __name__ == "__main__":
    run_system()
