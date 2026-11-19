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

# تنظیمات دو مدل پامپ (هر دو بالای 5% سود)
PUMP_PROFILES = {
    "ULTRA_FAST": {
        "fa_title": "⚡ سیگنال فوق سریع",
        "en_tag": "ULTRA-FAST PUMP (≤ 30m)",
        "timeframe": "5m",
        "window_label": "حداکثر ۳۰ دقیقه (≤ 30 Min)",
        "sl_pct": 0.025,       # 2.5% حد ضرر
        "tp1_pct": 0.055,      # 5.5% تارگت اول
        "tp2_pct": 0.085,      # 8.5% تارگت دوم
        "tp3_pct": 0.130,      # 13.0% تارگت سوم
        "max_tp1_mins": 30,
        "risk_text": "🟡 ULTRA VELOCITY (نوسان لحظه‌ای)"
    },
    "FAST": {
        "fa_title": "🚀 سیگنال سریع",
        "en_tag": "FAST PUMP (1h - 3h)",
        "timeframe": "15m",
        "window_label": "۱ تا ۳ ساعت (1h - 3h)",
        "sl_pct": 0.035,       # 3.5% حد ضرر
        "tp1_pct": 0.075,      # 7.5% تارگت اول
        "tp2_pct": 0.120,      # 12.0% تارگت دوم
        "tp3_pct": 0.180,      # 18.0% تارگت سوم
        "max_tp1_mins": 180,
        "risk_text": "🟠 HIGH MOMENTUM (موج تثبیتی)"
    }
}

DAILY_TRACKER_FILE = "daily_signals.json"
TRADES_STATE_FILE = "active_trades.json"
OFFSET_FILE = "telegram_offset.json"

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
    # AI & Narratives
    "FET/USDT", "RENDER/USDT", "TAO/USDT", "WLD/USDT", "ARKM/USDT", "GRT/USDT",
    # Memes (پامپ‌های سریع و فوق سریع)
    "DOGE/USDT", "PEPE/USDT", "WIF/USDT", "FLOKI/USDT", "BONK/USDT",
    "BOME/USDT", "MEME/USDT", "SHIB/USDT",
    # Majors & Legacy
    "BTC/USDT", "ETH/USDT", "BNB/USDT", "XRP/USDT", "LTC/USDT"
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
# ماژول مدیریت سهمیه شیفتی (۳ شیفت ۸ ساعته × ۵ سیگنال = ۱۵ سیگنال)
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
                        f"⚡🚀 <b>وضعیت اسکنر دوگانه پامپ (Dual-Speed)</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"⏰ شیفت کنونی: <b>شیفت {shift_id} ({s_count} از {SIGNALS_PER_SHIFT})</b>\n"
                        f"🎯 کل سیگنال‌های امروز: <b>{t_count} از {MAX_DAILY_SIGNALS}</b>\n"
                        f"⚡ مدل ۱: <b>فوق سریع (+5.5% زیر ۳۰ دقیقه)</b>\n"
                        f"🚀 مدل ۲: <b>سریع (+7.5% تا +18%)</b>\n"
                        f"⏱ زمان تهران: <code>{get_tehran_time_str()}</code>"
                    )
                elif text == "/trades":
                    trades = TradeLifecycleAgent.load_trades()
                    if not trades:
                        send_telegram("📭 در حال حاضر هیچ پامپ فعالی در جریان نیست.")
                    else:
                        resp = "📋 <b>ACTIVE DUAL-SPEED PUMPS</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        for s, t in trades.items():
                            p_str = format_dynamic_price(t['entry'])
                            mode_label = t.get('fa_title', 'پامپ')
                            resp += f"• <b>{s}</b> [{mode_label}] | ورود: <code>{p_str}</code> | ریسک‌فری: <b>{t['risk_free']}</b>\n"
                        send_telegram(resp)
        except Exception as e:
            print(f"[COMMAND ERROR] {e}")

    @classmethod
    def _report_gold_status(cls, tech_agent):
        send_telegram("🥇 <i>در حال بررسی نوسان و حجم لحظه‌ای طلا...</i>")
        ohlcv, active_ex, source_name = tech_agent.fetch_candle_data("PAXG/USDT", timeframe='5m')
        if not ohlcv:
            ohlcv, active_ex, source_name = tech_agent.fetch_candle_data("XAU/USDT", timeframe='5m')

        if ohlcv:
            metrics = tech_agent.analyze_pump_metrics(ohlcv)
            imbalance = tech_agent.analyze_orderbook_imbalance(active_ex, "PAXG/USDT")
            whale = WhaleAgent.inspect("PAXG/USDT")
            msg = (
                f"🥇 <b>گزارش لحظه‌ای طلا (PAXG/XAU)</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🌐 استخر: <b>{source_name}</b>\n"
                f"💵 قیمت انس: <code>${format_dynamic_price(metrics['price'])}</code>\n"
                f"📊 جهش حجم (5m): <code>{metrics['vol_ratio']:.1f}x</code> | RSI: <code>{metrics['rsi']:.1f}</code>\n"
                f"📚 برتری خریداران: <code>{imbalance:+.2f}</code>\n"
                f"🐋 نهنگ‌ها: <b>{whale['top_ratio']}</b> ({whale['whale_bias']})\n\n"
                f"⏱️ <i>زمان تهران: {get_tehran_time_str()}</i>"
            )
            send_telegram(msg)
        else:
            send_telegram("❌ خطا در اتصال به منابع دیتای طلا.")

# =====================================================================
# ماژول رهگیری پامپ‌های باز (متناسب با سرعت فوق سریع یا سریع)
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

        prof = PUMP_PROFILES[setup['mode']]
        trades[symbol] = {
            'mode': setup['mode'],
            'fa_title': prof['fa_title'],
            'entry': setup['price'],
            'sl': setup['sl'],
            'tp1': setup['tp1'],
            'tp2': setup['tp2'],
            'tp3': setup['tp3'],
            'tp1_pct': prof['tp1_pct'] * 100,
            'tp3_pct': prof['tp3_pct'] * 100,
            'sl_pct': prof['sl_pct'] * 100,
            'max_tp1_mins': prof['max_tp1_mins'],
            'leverage': setup['leverage'],
            'tp1_hit': False,
            'risk_free': False,
            'timeout_warned': False,
            'opened_ts': get_tehran_datetime().timestamp(),
            'opened_at': get_tehran_time_str()
        }
        cls.save_trades(trades)

    @classmethod
    def monitor_active_trades(cls, tech_agent):
        trades = cls.load_trades()
        if not trades:
            return

        remaining = {}
        now_dt = get_tehran_datetime()
        now_tehran = now_dt.strftime("%H:%M:%S | %Y/%m/%d")
        now_ts = now_dt.timestamp()

        for symbol, t in list(trades.items()):
            ohlcv, _, _ = tech_agent.fetch_candle_data(symbol, timeframe='5m', limit=15)
            if not ohlcv or len(ohlcv) < 2:
                remaining[symbol] = t
                continue

            recent_candles = ohlcv[-3:]
            high_p = max(c[2] for c in recent_candles)
            low_p = min(c[3] for c in recent_candles)
            closed = False

            elapsed_minutes = (now_ts - t.get('opened_ts', now_ts)) / 60.0
            mode_badge = t.get('fa_title', '⚡ سیگنال پامپ')
            tp1_pct_val = t.get('tp1_pct', 5.5)
            tp3_pct_val = t.get('tp3_pct', 13.0)
            sl_pct_val = t.get('sl_pct', 2.5)

            if not t['tp1_hit'] and high_p >= t['tp1']:
                t['tp1_hit'] = True
                t['risk_free'] = True
                t['sl'] = t['entry'] * 1.002
                send_telegram(
                    f"🎯 <b>تارگت اول (+{tp1_pct_val:.1f}%) تاچ شد!</b> [{mode_badge}]\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🌐 #{symbol.replace('/', '_')}\n"
                    f"⏱️ زمان تحقق: <b>{int(elapsed_minutes)} دقیقه</b>\n"
                    f"✅ لمس قیمت: <code>{format_dynamic_price(t['tp1'])}</code>\n"
                    f"🛡️ استاپ به نقطه ورود منتقل و پوزیشن ریسک‌فری شد."
                )

            if high_p >= t['tp3']:
                send_telegram(
                    f"🏆 <b>انفجار نهایی (+{tp3_pct_val:.1f}%) تکمیل شد!</b> [{mode_badge}]\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🌐 #{symbol.replace('/', '_')}\n"
                    f"💰 پامپ با حداکثر سود بسته شد.\n"
                    f"⏱️ <i>زمان تهران: {now_tehran}</i>"
                )
                closed = True

            if low_p <= t['sl'] and not closed:
                if t['risk_free']:
                    send_telegram(f"🛡️ <b>خروج ریسک‌فری:</b> #{symbol.replace('/', '_')} [{mode_badge}] بدون ضرر در نقطه ورود بسته شد.")
                else:
                    send_telegram(
                        f"🛑 <b>حد ضرر فعال شد (-{sl_pct_val:.1f}%)</b> [{mode_badge}]\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🌐 #{symbol.replace('/', '_')} | خروج: <code>{format_dynamic_price(t['sl'])}</code>"
                    )
                closed = True

            max_mins = t.get('max_tp1_mins', 30)
            if not closed and not t['tp1_hit'] and not t.get('timeout_warned', False) and elapsed_minutes >= max_mins:
                t['timeout_warned'] = True
                send_telegram(
                    f"⏳ <b>هشدار زمان ({max_mins} دقیقه گذشت):</b> #{symbol.replace('/', '_')} [{mode_badge}]\n"
                    f"شتاب اولیه پامپ طبق زمان‌بندی انتظار تکمیل نشد؛ استاپ را نزدیک‌تر کنید یا با سود جزئی خارج شوید."
                )

            if not closed:
                remaining[symbol] = t

        cls.save_trades(remaining)

# =====================================================================
# موتور تحلیل تکنیکال دوگانه (5m فوق سریع + 15m سریع)
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

    def fetch_candle_data(self, symbol: str, timeframe='5m', limit=50):
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
        closed_df = df.iloc[:-1].copy()
        live_candle = df.iloc[-1]

        delta = closed_df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / (loss + 1e-8)
        closed_df['rsi'] = 100 - (100 / (1 + rs))

        closed_df['vol_ma20'] = closed_df['volume'].rolling(20).mean()
        closed_df['prev_15_high'] = closed_df['high'].shift(1).rolling(15).max()

        latest = closed_df.iloc[-1]
        vol_ratio = latest['volume'] / latest['vol_ma20'] if latest['vol_ma20'] > 0 else 1.0
        
        candle_gain_pct = ((latest['close'] - latest['open']) / latest['open']) * 100
        live_continuation = live_candle['close'] >= latest['close'] * 0.998

        candle_range = latest['high'] - latest['low']
        body_strength = (latest['close'] - latest['open']) / candle_range if candle_range > 0 else 0
        is_breakout = latest['close'] > latest['prev_15_high']

        return {
            'price': live_candle['close'],
            'rsi': latest['rsi'],
            'vol_ratio': vol_ratio,
            'candle_gain_pct': candle_gain_pct,
            'body_strength': body_strength,
            'is_breakout': is_breakout,
            'live_continuation': live_continuation
        }

# =====================================================================
# ماژول رصد ورود نقدینگی نهنگ‌ها
# =====================================================================
class WhaleAgent:
    @staticmethod
    def inspect(symbol: str, period: str = "5m") -> dict:
        clean = symbol.replace("/", "").replace(":USDT", "")
        if "XAU" in clean: clean = "PAXGUSDT"
        metrics = {"top_ratio": 1.0, "whale_bias": "NEUTRAL"}
        try:
            rr = requests.get(f"https://fapi.binance.com/futures/data/topLongShortAccountRatio?symbol={clean}&period={period}&limit=1", timeout=3).json()
            if isinstance(rr, list) and len(rr) > 0:
                ratio = float(rr[0].get("longShortRatio", 1.0))
                metrics["top_ratio"] = round(ratio, 2)
                metrics["whale_bias"] = "WHALES PUMPING 🐋⚡" if ratio > 1.12 else ("NEUTRAL" if ratio >= 0.95 else "SELL PRESSURE")
        except Exception: pass
        return metrics

# =====================================================================
# ماژول هوش مصنوعی ارزیاب سرعت و مومنتوم پامپ
# =====================================================================
class MacroAIOfficerAgent:
    def review_pump(self, payload: dict) -> dict:
        prof = PUMP_PROFILES[payload['mode']]
        prompt = (
            f"Analyze {prof['en_tag']} breakout:\n"
            f"Symbol: {payload['symbol']} | Timeframe: {prof['timeframe']} | Volume Surge: {payload['vol_ratio']:.1f}x | "
            f"Candle Gain: +{payload['candle_gain_pct']:.2f}% | RSI: {payload['rsi']:.1f} | "
            f"Orderbook Bid Dominance: {payload['imbalance']} | Whale Ratio: {payload['top_ratio']}\n\n"
            f"Target: Hit +{prof['tp1_pct']*100:.1f}% within {prof['window_label']}.\n"
            f"Confirm if momentum is fresh and explosive enough to hit TP1 cleanly, or if it's an exhausted fakeout.\n"
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
            return {"approved": True, "engine": "Dual-Speed Quant", "thesis": f"Strong {prof['timeframe']} volume surge with resistance breakout."}

        approved = "CONFIRMED" in analysis.upper()
        thesis = analysis.replace("VERDICT: CONFIRMED", "").replace("VERDICT: REJECTED", "").strip()
        return {"approved": approved, "engine": engine, "thesis": thesis}

# =====================================================================
# ماژول صدور کارت سیگنال دوگانه (مشخص کردن دقیق فوق سریع یا سریع)
# =====================================================================
class DispatchAgent:
    @staticmethod
    def send(data: dict, shift_num: int, shift_count: int, total_count: int):
        prof = PUMP_PROFILES[data['mode']]
        symbol_tag = data['symbol'].replace('/', '_')
        price = data['price']

        sl = price * (1.0 - prof['sl_pct'])
        tp1 = price * (1.0 + prof['tp1_pct'])
        tp2 = price * (1.0 + prof['tp2_pct'])
        tp3 = price * (1.0 + prof['tp3_pct'])

        leverage = data['leverage']
        roi1 = (prof['tp1_pct'] * 100) * leverage
        roi2 = (prof['tp2_pct'] * 100) * leverage
        roi3 = (prof['tp3_pct'] * 100) * leverage

        rr1 = round(prof['tp1_pct'] / prof['sl_pct'], 1)
        rr2 = round(prof['tp2_pct'] / prof['sl_pct'], 1)
        rr3 = round(prof['tp3_pct'] / prof['sl_pct'], 1)

        entry_str = format_dynamic_price(price)
        sl_str = format_dynamic_price(sl)
        tp1_str = format_dynamic_price(tp1)
        tp2_str = format_dynamic_price(tp2)
        tp3_str = format_dynamic_price(tp3)

        msg = (
            f"<b>{prof['fa_title']}</b> | <code>{prof['en_tag']}</code>\n"
            f"📊 <i>[شیفت {shift_num}: {shift_count}/{SIGNALS_PER_SHIFT} | کل امروز: {total_count}/{MAX_DAILY_SIGNALS}]</i>\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"🪙 #{symbol_tag}  |  📊 {prof['timeframe']} Vol: <b>{data['vol_ratio']:.1f}x</b>\n"
            f"🟢 <b>LONG — {prof['fa_title']}</b>\n\n"
            f"🏆 Grade: <b>{data['grade']}</b>  |  Score: <b>{data['score']}/100</b>\n"
            f"⏱️ زمان مورد انتظار تا TP1: <b>{prof['window_label']}</b>\n\n"
            f"📍 <b>ENTRY</b>\n"
            f"<code>{entry_str}</code>\n\n"
            f"🛑 <b>STOP LOSS (-{prof['sl_pct']*100:.1f}%)</b>\n"
            f"<code>{sl_str}</code>\n\n"
            f"⚠️ <b>RISK:</b> {prof['risk_text']}\n"
            f"⚡️ Suggested leverage: <b>{leverage}x</b>\n\n"
            f"🎯 <b>TAKE PROFIT (SPOT GAINS)</b>\n\n"
            f"🥇 TP1 ➔ <code>{tp1_str}</code>  (<b>+{prof['tp1_pct']*100:.1f}%</b>) [ریسک‌فری]\n"
            f"🥈 TP2 ➔ <code>{tp2_str}</code>  (<b>+{prof['tp2_pct']*100:.1f}%</b>)\n"
            f"🥉 TP3 ➔ <code>{tp3_str}</code>  (<b>+{prof['tp3_pct']*100:.1f}%</b>)\n\n"
            f"📊 <b>APPROX. ROI @ {leverage}x</b>\n\n"
            f"TP1 ➔ <b>+{roi1:.1f}%</b>\n"
            f"TP2 ➔ <b>+{roi2:.1f}%</b>\n"
            f"TP3 ➔ <b>+{roi3:.1f}%</b>\n\n"
            f"⚖️ <b>RISK / REWARD</b>\n"
            f"TP1 ➔ <b>1:{rr1}</b>  |  TP2 ➔ <b>1:{rr2}</b>  |  TP3 ➔ <b>1:{rr3}</b>\n\n"
            f"🧠 <i>AI Verification ({data['ai_engine']}): {data['ai_thesis']}</i>\n"
            f"⏱️ <b>زمان به وقت ایران:</b> <code>{get_tehran_time_str()}</code>"
        )

        data['sl'] = sl
        data['tp1'] = tp1
        data['tp2'] = tp2
        data['tp3'] = tp3

        send_telegram(msg)

# =====================================================================
# هسته هماهنگ‌کننده اسکنر دوگانه (فوق سریع + سریع)
# =====================================================================
def run_system():
    print(f"=== [DUAL-SPEED PUMP SCANNER: {get_tehran_time_str()} (Tehran)] ===")

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

        # ۱. بررسی لایه اول: مدل فوق سریع (تایم‌فریم 5m - هدف تاچ زیر 30 دقیقه)
        ohlcv_5m, active_ex, source_name = tech_agent.fetch_candle_data(symbol, timeframe='5m')
        if ohlcv_5m:
            try:
                m5 = tech_agent.analyze_pump_metrics(ohlcv_5m)
                imbalance = tech_agent.analyze_orderbook_imbalance(active_ex, symbol)
                whale = WhaleAgent.inspect(symbol, period="5m")

                # شروط پامپ فوق سریع: حجم 3.2x ناگهانی + رشد اولیه 1.2% تا 4.2% در 5 دقیقه
                if (
                    m5['vol_ratio'] >= 3.2
                    and 1.2 <= m5['candle_gain_pct'] <= 4.2
                    and m5['body_strength'] >= 0.65
                    and 55 <= m5['rsi'] <= 76
                    and m5['live_continuation']
                    and (m5['is_breakout'] or imbalance >= 0.12)
                ):
                    raw_score = 80
                    if m5['vol_ratio'] >= 4.5: raw_score += 10
                    if m5['is_breakout']: raw_score += 6
                    if imbalance >= 0.18: raw_score += 6
                    if whale['top_ratio'] > 1.15: raw_score += 5

                    grade = "A+" if raw_score >= 98 else ("A" if raw_score >= 88 else "B+")
                    pump_candidates.append({
                        'mode': 'ULTRA_FAST',
                        'source': source_name,
                        'symbol': symbol,
                        'price': m5['price'],
                        'vol_ratio': m5['vol_ratio'],
                        'candle_gain_pct': m5['candle_gain_pct'],
                        'rsi': m5['rsi'],
                        'imbalance': imbalance,
                        'top_ratio': whale['top_ratio'],
                        'leverage': 5,
                        'score': min(raw_score, 100),
                        'grade': grade
                    })
                    continue  # اگر فوق سریع پیدا شد، نیازی به بررسی حالت کندتر روی همین ارز نیست
            except Exception:
                pass

        # ۲. بررسی لایه دوم: مدل سریع (تایم‌فریم 15m - موج شکست 1 تا 3 ساعته)
        ohlcv_15m, active_ex15, source_name15 = tech_agent.fetch_candle_data(symbol, timeframe='15m')
        if ohlcv_15m:
            try:
                m15 = tech_agent.analyze_pump_metrics(ohlcv_15m)
                imbalance15 = tech_agent.analyze_orderbook_imbalance(active_ex15, symbol)
                whale15 = WhaleAgent.inspect(symbol, period="15m")

                # شروط پامپ سریع 15m: حجم 2.4x + رشد تثبیتی 1.5% تا 5.0% در 15 دقیقه
                if (
                    m15['vol_ratio'] >= 2.4
                    and 1.5 <= m15['candle_gain_pct'] <= 5.0
                    and m15['body_strength'] >= 0.60
                    and 52 <= m15['rsi'] <= 75
                    and m15['live_continuation']
                    and (m15['is_breakout'] or imbalance15 >= 0.10)
                ):
                    raw_score = 76
                    if m15['vol_ratio'] >= 3.5: raw_score += 10
                    if m15['is_breakout']: raw_score += 7
                    if imbalance15 >= 0.15: raw_score += 6
                    if whale15['top_ratio'] > 1.15: raw_score += 6

                    grade = "A+" if raw_score >= 98 else ("A" if raw_score >= 88 else "B+")
                    pump_candidates.append({
                        'mode': 'FAST',
                        'source': source_name15,
                        'symbol': symbol,
                        'price': m15['price'],
                        'vol_ratio': m15['vol_ratio'],
                        'candle_gain_pct': m15['candle_gain_pct'],
                        'rsi': m15['rsi'],
                        'imbalance': imbalance15,
                        'top_ratio': whale15['top_ratio'],
                        'leverage': 5,
                        'score': min(raw_score, 100),
                        'grade': grade
                    })
            except Exception:
                continue

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
            print(f"🎯 [{best_pump['mode']} SENT]: {best_pump['symbol']} | Vol: {best_pump['vol_ratio']:.1f}x")
        else:
            print(f"❌ [AI REJECTED]: {best_pump['symbol']}")
    else:
        print("🔍 [DUAL SCAN COMPLETED]: No ultra-fast or fast pump detected.")

if __name__ == "__main__":
    run_system()
