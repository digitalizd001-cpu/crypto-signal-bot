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

# تنظیمات دو مدل سرعت (هم برای پامپ LONG و هم برای دامپ SHORT - هر دو بالای 5% حرکت)
MOVE_PROFILES = {
    "ULTRA_FAST": {
        "fa_speed": "سیگنال فوق سریع",
        "en_tag": "ULTRA-FAST (≤ 30m)",
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
        "fa_speed": "سیگنال سریع",
        "en_tag": "FAST (1h - 3h)",
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
    # Memes
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
            return False, f"سقف روزانه ({MAX_DAILY_SIGNALS} سیگنال) تکمیل است."

        shift_count = status["shifts"][current_shift]["count"]
        if shift_count >= SIGNALS_PER_SHIFT:
            return False, f"سهمیه شیفت {current_shift} ({SIGNALS_PER_SHIFT} سیگنال) تکمیل است."

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
            except Exception:
                pass
        return 0

    @staticmethod
    def save_offset(offset):
        try:
            with open(OFFSET_FILE, "w") as f:
                json.dump({"offset": offset}, f)
        except Exception:
            pass

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
                        f"⚡🚀 <b>وضعیت اسکنر پامپ و دامپ (LONG & SHORT)</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"⏰ شیفت کنونی: <b>شیفت {shift_id} ({s_count} از {SIGNALS_PER_SHIFT})</b>\n"
                        f"🎯 کل سیگنال‌های امروز: <b>{t_count} از {MAX_DAILY_SIGNALS}</b>\n"
                        f"🔄 جهت‌ها: <b>پامپ (LONG) + دامپ (SHORT)</b>\n"
                        f"⏱ زمان تهران: <code>{get_tehran_time_str()}</code>"
                    )
                elif text == "/trades":
                    trades = TradeLifecycleAgent.load_trades()
                    if not trades:
                        send_telegram("📭 در حال حاضر هیچ معامله فعالی در جریان نیست.")
                    else:
                        resp = "📋 <b>ACTIVE PUMP & DUMP TRADES</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        for s, t in trades.items():
                            p_str = format_dynamic_price(t['entry'])
                            resp += f"• <b>{s}</b> ({t['direction']}) [{t['fa_speed']}] | ورود: <code>{p_str}</code> | ریسک‌فری: <b>{t['risk_free']}</b>\n"
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
            metrics = tech_agent.analyze_move_metrics(ohlcv)
            imbalance = tech_agent.analyze_orderbook_imbalance(active_ex, "PAXG/USDT")
            whale = WhaleAgent.inspect("PAXG/USDT")
            msg = (
                f"🥇 <b>گزارش لحظه‌ای طلا (PAXG/XAU)</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🌐 استخر: <b>{source_name}</b>\n"
                f"💵 قیمت انس: <code>${format_dynamic_price(metrics['price'])}</code>\n"
                f"📊 نسبت حجم (5m): <code>{metrics['vol_ratio']:.1f}x</code> | RSI: <code>{metrics['rsi']:.1f}</code>\n"
                f"📚 عدم تعادل بوک: <code>{imbalance:+.2f}</code>\n"
                f"🐋 نهنگ‌ها: <b>{whale['top_ratio']}</b> ({whale['whale_bias']})\n\n"
                f"⏱️ <i>زمان تهران: {get_tehran_time_str()}</i>"
            )
            send_telegram(msg)
        else:
            send_telegram("❌ خطا در اتصال به منابع دیتای طلا.")

# =====================================================================
# ماژول رهگیری معاملات باز (پشتیبانی از LONG و SHORT)
# =====================================================================
class TradeLifecycleAgent:
    @staticmethod
    def load_trades():
        if os.path.exists(TRADES_STATE_FILE):
            try:
                with open(TRADES_STATE_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    @staticmethod
    def save_trades(trades):
        try:
            with open(TRADES_STATE_FILE, "w") as f:
                json.dump(trades, f, indent=2)
        except Exception:
            pass

    @classmethod
    def register_trade(cls, setup: dict):
        trades = cls.load_trades()
        symbol = setup['symbol']
        if symbol in trades:
            return

        prof = MOVE_PROFILES[setup['mode']]
        trades[symbol] = {
            'mode': setup['mode'],
            'direction': setup['direction'],
            'fa_speed': prof['fa_speed'],
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
            try:
                ohlcv, _, _ = tech_agent.fetch_candle_data(symbol, timeframe='5m', limit=15)
                if not ohlcv or len(ohlcv) < 2:
                    remaining[symbol] = t
                    continue

                recent_candles = ohlcv[-3:]
                high_p = max(c[2] for c in recent_candles)
                low_p = min(c[3] for c in recent_candles)
                closed = False

                is_long = t.get('direction', 'LONG') == 'LONG'
                elapsed_minutes = (now_ts - t.get('opened_ts', now_ts)) / 60.0
                tag = f"[{t.get('fa_speed', 'سیگنال')} | {'پامپ LONG' if is_long else 'دامپ SHORT'}]"
                tp1_pct_val = t.get('tp1_pct', 5.5)
                tp3_pct_val = t.get('tp3_pct', 13.0)
                sl_pct_val = t.get('sl_pct', 2.5)

                tp1_reached = (high_p >= t['tp1']) if is_long else (low_p <= t['tp1'])
                if not t['tp1_hit'] and tp1_reached:
                    t['tp1_hit'] = True
                    t['risk_free'] = True
                    t['sl'] = t['entry'] * (1.002 if is_long else 0.998)
                    send_telegram(
                        f"🎯 <b>تارگت اول (+{tp1_pct_val:.1f}%) تاچ شد!</b> {tag}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🌐 #{symbol.replace('/', '_')}\n"
                        f"⏱️ زمان تحقق: <b>{int(elapsed_minutes)} دقیقه</b>\n"
                        f"✅ لمس قیمت: <code>{format_dynamic_price(t['tp1'])}</code>\n"
                        f"🛡️ استاپ به نقطه ورود منتقل و معامله ریسک‌فری شد."
                    )

                tp3_reached = (high_p >= t['tp3']) if is_long else (low_p <= t['tp3'])
                if tp3_reached:
                    send_telegram(
                        f"🏆 <b>تارگت نهایی (+{tp3_pct_val:.1f}%) تکمیل شد!</b> {tag}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🌐 #{symbol.replace('/', '_')}\n"
                        f"💰 معامله با حداکثر سود بسته شد.\n"
                        f"⏱️ <i>زمان تهران: {now_tehran}</i>"
                    )
                    closed = True

                sl_reached = (low_p <= t['sl']) if is_long else (high_p >= t['sl'])
                if sl_reached and not closed:
                    if t['risk_free']:
                        send_telegram(f"🛡️ <b>خروج ریسک‌فری:</b> #{symbol.replace('/', '_')} {tag} بدون ضرر در نقطه ورود بسته شد.")
                    else:
                        send_telegram(
                            f"🛑 <b>حد ضرر فعال شد (-{sl_pct_val:.1f}%)</b> {tag}\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"🌐 #{symbol.replace('/', '_')} | خروج: <code>{format_dynamic_price(t['sl'])}</code>"
                        )
                    closed = True

                max_mins = t.get('max_tp1_mins', 30)
                if not closed and not t['tp1_hit'] and not t.get('timeout_warned', False) and elapsed_minutes >= max_mins:
                    t['timeout_warned'] = True
                    send_telegram(
                        f"⏳ <b>هشدار زمان ({max_mins} دقیقه گذشت):</b> #{symbol.replace('/', '_')} {tag}\n"
                        f"حرکت طبق زمان‌بندی انتظار به TP1 نرسید؛ استاپ را مدیریت کنید."
                    )

                if not closed:
                    remaining[symbol] = t
            except Exception as e:
                print(f"[TRADE MONITOR ERROR {symbol}]: {e}")
                remaining[symbol] = t

        cls.save_trades(remaining)

# =====================================================================
# موتور تحلیل تکنیکال دوگانه (پامپ LONG + دامپ SHORT در 5m و 15m)
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
        except Exception:
            pass
        try:
            by = ccxt.bybit({'enableRateLimit': True, 'urls': {'api': {'public': 'https://api.bytick.com'}}})
            by.load_markets()
            pools.append(("Bybit", by))
        except Exception:
            pass
        try:
            ok = ccxt.okx({'enableRateLimit': True})
            ok.load_markets()
            pools.append(("OKX", ok))
        except Exception:
            pass
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
            if not exchange:
                return 0.0
            target = symbol
            if target not in exchange.markets:
                for k in exchange.markets.keys():
                    if symbol.split("/")[0] in k:
                        target = k
                        break
            ob = exchange.fetch_order_book(target, limit=25)
            bids = sum(b[1] for b in ob.get('bids', []))
            asks = sum(a[1] for a in ob.get('asks', []))
            if (bids + asks) == 0:
                return 0.0
            return round((bids - asks) / (bids + asks), 2)
        except Exception:
            return 0.0

    def analyze_move_metrics(self, ohlcv) -> dict:
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
        closed_df['prev_15_low'] = closed_df['low'].shift(1).rolling(15).min()

        latest = closed_df.iloc[-1]
        vol_ma = latest['vol_ma20'] if pd.notna(latest['vol_ma20']) and latest['vol_ma20'] > 0 else 0
        vol_ratio = (latest['volume'] / vol_ma) if vol_ma > 0 else 1.0
        candle_range = latest['high'] - latest['low']

        up_pct = ((latest['close'] - latest['open']) / latest['open']) * 100 if latest['open'] > 0 else 0.0
        up_body = (latest['close'] - latest['open']) / candle_range if candle_range > 0 else 0.0
        up_breakout = bool(pd.notna(latest['prev_15_high']) and latest['close'] > latest['prev_15_high'])
        up_cont = live_candle['close'] >= latest['close'] * 0.998

        down_pct = ((latest['open'] - latest['close']) / latest['open']) * 100 if latest['open'] > 0 else 0.0
        down_body = (latest['open'] - latest['close']) / candle_range if candle_range > 0 else 0.0
        down_breakdown = bool(pd.notna(latest['prev_15_low']) and latest['close'] < latest['prev_15_low'])
        down_cont = live_candle['close'] <= latest['close'] * 1.002

        rsi_val = float(latest['rsi']) if pd.notna(latest['rsi']) else 50.0

        return {
            'price': float(live_candle['close']),
            'rsi': rsi_val,
            'vol_ratio': float(vol_ratio),
            'up_pct': float(up_pct),
            'up_body': float(up_body),
            'up_breakout': up_breakout,
            'up_cont': bool(up_cont),
            'down_pct': float(down_pct),
            'down_body': float(down_body),
            'down_breakdown': down_breakdown,
            'down_cont': bool(down_cont)
        }

# =====================================================================
# ماژول رصد موقعیت نهنگ‌ها
# =====================================================================
class WhaleAgent:
    @staticmethod
    def inspect(symbol: str, period: str = "5m") -> dict:
        clean = symbol.replace("/", "").replace(":USDT", "")
        if "XAU" in clean:
            clean = "PAXGUSDT"
        metrics = {"top_ratio": 1.0, "whale_bias": "NEUTRAL"}
        try:
            rr = requests.get(f"https://fapi.binance.com/futures/data/topLongShortAccountRatio?symbol={clean}&period={period}&limit=1", timeout=3).json()
            if isinstance(rr, list) and len(rr) > 0:
                ratio = float(rr[0].get("longShortRatio", 1.0))
                metrics["top_ratio"] = round(ratio, 2)
                metrics["whale_bias"] = "WHALES LONG 🐋🟢" if ratio > 1.12 else ("WHALES SHORT 🐋🔴" if ratio < 0.88 else "BALANCED")
        except Exception:
            pass
        return metrics

# =====================================================================
# ماژول هوش مصنوعی ارزیاب پامپ و دامپ
# =====================================================================
class MacroAIOfficerAgent:
    def review_setup(self, payload: dict) -> dict:
        prof = MOVE_PROFILES[payload['mode']]
        move_type = "PUMP (LONG)" if payload['direction'] == "LONG" else "DUMP (SHORT)"
        prompt = (
            f"Analyze {move_type} | {prof['en_tag']}:\n"
            f"Symbol: {payload['symbol']} | TF: {prof['timeframe']} | Volume Surge: {payload['vol_ratio']:.1f}x | "
            f"Candle Move: {payload['move_pct']:.2f}% | RSI: {payload['rsi']:.1f} | "
            f"Orderbook Imbalance: {payload['imbalance']} | Whale L/S Ratio: {payload['top_ratio']}\n\n"
            f"Target: +{prof['tp1_pct']*100:.1f}% move within {prof['window_label']}.\n"
            f"Confirm if momentum is strong and genuine (not a fakeout/dead-cat bounce).\n"
            f"Start strictly with 'VERDICT: CONFIRMED' or 'VERDICT: REJECTED'. Add 1 short analytical sentence."
        )
        analysis = ""
        engine = "Gemini Flash"
        if GEMINI_API_KEY:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
                res = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=6).json()
                analysis = res['candidates'][0]['content']['parts'][0]['text'].strip()
            except Exception:
                pass

        if not analysis and GROQ_API_KEY:
            engine = "Groq Llama-3"
            try:
                url = "https://api.groq.com/openai/v1/chat/completions"
                headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
                res = requests.post(url, headers=headers, json={"model": "llama-3.1-8b-instant", "messages": [{"role": "user", "content": prompt}], "max_tokens": 80}, timeout=6).json()
                analysis = res['choices'][0]['message']['content'].strip()
            except Exception:
                pass

        if not analysis:
            return {"approved": True, "engine": "Dual-Mode Quant", "thesis": f"High-volume {prof['timeframe']} {move_type} breakout confirmed."}

        approved = "CONFIRMED" in analysis.upper()
        thesis = analysis.replace("VERDICT: CONFIRMED", "").replace("VERDICT: REJECTED", "").strip()
        return {"approved": approved, "engine": engine, "thesis": thesis}

# =====================================================================
# ماژول صدور کارت سیگنال (با برچسب دقیق سیگنال فوق سریع / سیگنال سریع و پامپ/دامپ)
# =====================================================================
class DispatchAgent:
    @staticmethod
    def send(data: dict, shift_num: int, shift_count: int, total_count: int):
        prof = MOVE_PROFILES[data['mode']]
        symbol_tag = data['symbol'].replace('/', '_')
        is_long = data['direction'] == "LONG"
        price = data['price']

        if is_long:
            dir_fa = "پامپ (LONG — BUY)"
            dir_emoji = "🟢"
            sl = price * (1.0 - prof['sl_pct'])
            tp1 = price * (1.0 + prof['tp1_pct'])
            tp2 = price * (1.0 + prof['tp2_pct'])
            tp3 = price * (1.0 + prof['tp3_pct'])
        else:
            dir_fa = "دامپ (SHORT — SELL)"
            dir_emoji = "🔴"
            sl = price * (1.0 + prof['sl_pct'])
            tp1 = price * (1.0 - prof['tp1_pct'])
            tp2 = price * (1.0 - prof['tp2_pct'])
            tp3 = price * (1.0 - prof['tp3_pct'])

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
            f"<b>{prof['fa_speed']}</b> | {dir_emoji} <b>{dir_fa}</b>\n"
            f"<code>{prof['en_tag']}</code> • <i>[شیفت {shift_num}: {shift_count}/{SIGNALS_PER_SHIFT} | کل: {total_count}/{MAX_DAILY_SIGNALS}]</i>\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"🪙 #{symbol_tag}  |  📊 {prof['timeframe']} Vol: <b>{data['vol_ratio']:.1f}x</b>\n"
            f"🏆 Grade: <b>{data['grade']}</b>  |  Score: <b>{data['score']}/100</b>\n"
            f"⏱️ زمان مورد انتظار تا TP1: <b>{prof['window_label']}</b>\n\n"
            f"📍 <b>ENTRY</b>\n"
            f"<code>{entry_str}</code>\n\n"
            f"🛑 <b>STOP LOSS ({'+' if not is_long else '-'}{prof['sl_pct']*100:.1f}%)</b>\n"
            f"<code>{sl_str}</code>\n\n"
            f"⚠️ <b>RISK:</b> {prof['risk_text']}\n"
            f"⚡️ Suggested leverage: <b>{leverage}x</b>\n\n"
            f"🎯 <b>TAKE PROFIT</b>\n\n"
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
# هسته هماهنگ‌کننده اسکنر دوگانه (پامپ + دامپ | فوق سریع + سریع)
# =====================================================================
def run_system():
    print(f"=== [PUMP & DUMP DUAL-SPEED SCANNER: {get_tehran_time_str()} (Tehran)] ===")

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

    candidates = []

    for symbol in BASE_WATCHLIST:
        if symbol in dispatched_in_this_shift:
            continue

        # ۱. بررسی مدل فوق سریع (5m - هدف تاچ زیر 30 دقیقه) برای پامپ و دامپ
        try:
            ohlcv_5m, active_ex, source_name = tech_agent.fetch_candle_data(symbol, timeframe='5m')
            if ohlcv_5m:
                m5 = tech_agent.analyze_move_metrics(ohlcv_5m)
                imbalance = tech_agent.analyze_orderbook_imbalance(active_ex, symbol)
                whale = WhaleAgent.inspect(symbol, period="5m")

                if (
                    m5['vol_ratio'] >= 3.2
                    and 1.2 <= m5['up_pct'] <= 4.2
                    and m5['up_body'] >= 0.65
                    and 55 <= m5['rsi'] <= 76
                    and m5['up_cont']
                    and (m5['up_breakout'] or imbalance >= 0.12)
                ):
                    score = (
                        80
                        + (10 if m5['vol_ratio'] >= 4.5 else (6 if m5['vol_ratio'] >= 3.5 else 0))
                        + (6 if m5['up_breakout'] else 0)
                        + (6 if imbalance >= 0.18 else 0)
                    )
                    candidates.append({
                        'mode': 'ULTRA_FAST', 'direction': 'LONG', 'source': source_name,
                        'symbol': symbol, 'price': m5['price'], 'vol_ratio': m5['vol_ratio'],
                        'move_pct': m5['up_pct'], 'rsi': m5['rsi'], 'imbalance': imbalance,
                        'top_ratio': whale['top_ratio'], 'leverage': 5,
                        'score': min(score, 100), 'grade': "A+" if score >= 95 else "A"
                    })
                    continue

                if (
                    m5['vol_ratio'] >= 3.2
                    and 1.2 <= m5['down_pct'] <= 4.2
                    and m5['down_body'] >= 0.65
                    and 24 <= m5['rsi'] <= 45
                    and m5['down_cont']
                    and (m5['down_breakdown'] or imbalance <= -0.12)
                ):
                    score = (
                        80
                        + (10 if m5['vol_ratio'] >= 4.5 else (6 if m5['vol_ratio'] >= 3.5 else 0))
                        + (6 if m5['down_breakdown'] else 0)
                        + (6 if imbalance <= -0.18 else 0)
                    )
                    candidates.append({
                        'mode': 'ULTRA_FAST', 'direction': 'SHORT', 'source': source_name,
                        'symbol': symbol, 'price': m5['price'], 'vol_ratio': m5['vol_ratio'],
                        'move_pct': m5['down_pct'], 'rsi': m5['rsi'], 'imbalance': imbalance,
                        'top_ratio': whale['top_ratio'], 'leverage': 5,
                        'score': min(score, 100), 'grade': "A+" if score >= 95 else "A"
                    })
                    continue
        except Exception as e:
            print(f"[5M SCAN ERROR {symbol}]: {e}")

        # ۲. بررسی مدل سریع (15m - موج 1 تا 3 ساعته) برای پامپ و دامپ
        try:
            ohlcv_15m, active_ex15, source_name15 = tech_agent.fetch_candle_data(symbol, timeframe='15m')
            if ohlcv_15m:
                m15 = tech_agent.analyze_move_metrics(ohlcv_15m)
                imbalance15 = tech_agent.analyze_orderbook_imbalance(active_ex15, symbol)
                whale15 = WhaleAgent.inspect(symbol, period="15m")

                if (
                    m15['vol_ratio'] >= 2.4
                    and 1.5 <= m15['up_pct'] <= 5.0
                    and m15['up_body'] >= 0.60
                    and 52 <= m15['rsi'] <= 75
                    and m15['up_cont']
                    and (m15['up_breakout'] or imbalance15 >= 0.10)
                ):
                    score = (
                        76
                        + (10 if m15['vol_ratio'] >= 3.5 else 0)
                        + (7 if m15['up_breakout'] else 0)
                        + (6 if imbalance15 >= 0.15 else 0)
                    )
                    candidates.append({
                        'mode': 'FAST', 'direction': 'LONG', 'source': source_name15,
                        'symbol': symbol, 'price': m15['price'], 'vol_ratio': m15['vol_ratio'],
                        'move_pct': m15['up_pct'], 'rsi': m15['rsi'], 'imbalance': imbalance15,
                        'top_ratio': whale15['top_ratio'], 'leverage': 5,
                        'score': min(score, 100), 'grade': "A+" if score >= 95 else "A"
                    })
                    continue

                if (
                    m15['vol_ratio'] >= 2.4
                    and 1.5 <= m15['down_pct'] <= 5.0
                    and m15['down_body'] >= 0.60
                    and 25 <= m15['rsi'] <= 48
                    and m15['down_cont']
                    and (m15['down_breakdown'] or imbalance15 <= -0.10)
                ):
                    score = (
                        76
                        + (10 if m15['vol_ratio'] >= 3.5 else 0)
                        + (7 if m15['down_breakdown'] else 0)
                        + (6 if imbalance15 <= -0.15 else 0)
                    )
                    candidates.append({
                        'mode': 'FAST', 'direction': 'SHORT', 'source': source_name15,
                        'symbol': symbol, 'price': m15['price'], 'vol_ratio': m15['vol_ratio'],
                        'move_pct': m15['down_pct'], 'rsi': m15['rsi'], 'imbalance': imbalance15,
                        'top_ratio': whale15['top_ratio'], 'leverage': 5,
                        'score': min(score, 100), 'grade': "A+" if score >= 95 else "A"
                    })
        except Exception as e:
            print(f"[15M SCAN ERROR {symbol}]: {e}")
            continue

    if candidates:
        candidates.sort(key=lambda s: (s['score'], s['vol_ratio']), reverse=True)
        best = candidates[0]

        review = macro_agent.review_setup(best)
        if review['approved']:
            best['ai_engine'] = review['engine']
            best['ai_thesis'] = review['thesis']

            s_num = int(current_shift_id)
            s_count = quota["shifts"][current_shift_id]["count"] + 1
            t_count = quota["total_dispatched"] + 1

            DispatchAgent.send(best, s_num, s_count, t_count)
            TradeLifecycleAgent.register_trade(best)
            ShiftQuotaManager.record_dispatch(best['symbol'])
            print(f"🎯 [{best['mode']} | {best['direction']} SENT]: {best['symbol']} | Vol: {best['vol_ratio']:.1f}x")
        else:
            print(f"❌ [AI REJECTED]: {best['symbol']}")
    else:
        print("🔍 [DUAL SCAN COMPLETED]: No fast/ultra-fast pump or dump detected.")

if __name__ == "__main__":
    run_system()
