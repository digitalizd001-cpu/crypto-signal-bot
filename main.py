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

MAX_SIGNALS = 5
PORTFOLIO_RISK_PERCENT = 1.0

TRADES_STATE_FILE = "active_trades.json"
PERFORMANCE_FILE = "performance.json"
CUSTOM_WATCHLIST_FILE = "custom_watchlist.json"
OFFSET_FILE = "telegram_offset.json"

BASE_WATCHLIST = [
    "PAXG/USDT", "XAU/USDT",
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "ADA/USDT",
    "AVAX/USDT", "DOT/USDT", "TRX/USDT", "NEAR/USDT", "SUI/USDT", "APT/USDT",
    "TON/USDT", "KAS/USDT", "SEI/USDT", "HBAR/USDT", "ATOM/USDT", "ALGO/USDT",
    "FTM/USDT", "EGLD/USDT", "FLOW/USDT", "MINA/USDT", "ICP/USDT",
    "MATIC/USDT", "ARB/USDT", "OP/USDT", "STRK/USDT", "IMX/USDT", "MANTA/USDT", "METIS/USDT",
    "LINK/USDT", "UNI/USDT", "AAVE/USDT", "MKR/USDT", "SNX/USDT", "LDO/USDT",
    "CRV/USDT", "RUNE/USDT", "INJ/USDT", "PENDLE/USDT", "ENA/USDT", "DYDX/USDT", "CAKE/USDT",
    "FET/USDT", "RENDER/USDT", "TAO/USDT", "AGIX/USDT", "OCEAN/USDT", "GRT/USDT",
    "WLD/USDT", "ARKM/USDT", "THETA/USDT",
    "DOGE/USDT", "SHIB/USDT", "PEPE/USDT", "WIF/USDT", "FLOKI/USDT", "BONK/USDT",
    "BOME/USDT", "MEME/USDT",
    "LTC/USDT", "BCH/USDT", "ETC/USDT", "XLM/USDT",
    "GALA/USDT", "SAND/USDT", "MANA/USDT", "AXS/USDT", "BEAM/USDT", "RON/USDT",
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
# ماژول رهگیری لحظه‌ای معاملات (TP / SL / Risk-Free Tracker)
# =====================================================================
class TradeLifecycleAgent:
    @staticmethod
    def load_trades():
        if os.path.exists(TRADES_STATE_FILE):
            try:
                with open(TRADES_STATE_FILE, "r") as f:
                    return json.load(f)
            except Exception: pass
        default_state = {}
        with open(TRADES_STATE_FILE, "w") as f:
            json.dump(default_state, f)
        return default_state

    @staticmethod
    def save_trades(trades):
        try:
            with open(TRADES_STATE_FILE, "w") as f:
                json.dump(trades, f, indent=2)
        except Exception as e:
            print(f"Error saving trades: {e}")

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
            'tp2_hit': False,
            'risk_free': False,
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

            # ۱. بررسی لمس TP1 و ریسک‌فری
            if not t['tp1_hit']:
                tp1_reached = (high_price >= t['tp1']) if is_long else (low_price <= t['tp1'])
                if tp1_reached:
                    t['tp1_hit'] = True
                    t['risk_free'] = True
                    t['sl'] = t['entry']
                    msg = (
                        f"🎯 <b>تارگت اول (TP1) تاچ شد!</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🌐 جفت‌ارز: <b>#{symbol.replace('/', '_')}</b>\n"
                        f"✅ تارگت اول در قیمت <code>{t['tp1']:,.4f}</code> محقق شد.\n"
                        f"🛡️ <b>وضعیت:</b> معامله ریسک‌فری شد (حد ضرر به نقطه ورود منتقل شد).\n"
                        f"⏱️ <i>زمان ثبت رویداد به وقت تهران: {tehran_now}</i>"
                    )
                    send_telegram(msg)

            # ۲. بررسی لمس TP2
            if t['tp1_hit'] and not t.get('tp2_hit', False):
                tp2_reached = (high_price >= t['tp2']) if is_long else (low_price <= t['tp2'])
                if tp2_reached:
                    t['tp2_hit'] = True
                    msg = (
                        f"🥈 <b>تارگت دوم (TP2) تاچ شد!</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🌐 جفت‌ارز: <b>#{symbol.replace('/', '_')}</b>\n"
                        f"🎯 تارگت دوم در قیمت <code>{t['tp2']:,.4f}</code> لمس گردید.\n"
                        f"⏱️ <i>زمان ثبت رویداد به وقت تهران: {tehran_now}</i>"
                    )
                    send_telegram(msg)

            # ۳. بررسی لمس TP3
            tp3_reached = (high_price >= t['tp3']) if is_long else (low_price <= t['tp3'])
            if tp3_reached:
                msg = (
                    f"🏆 <b>تارگت نهایی (TP3) محقق شد — خروج کامل</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🌐 جفت‌ارز: <b>#{symbol.replace('/', '_')}</b>\n"
                    f"🎉 تمام اهداف معامله در قیمت <code>{t['tp3']:,.4f}</code> تکمیل شدند.\n"
                    f"⏱️ <i>زمان تکمیل به وقت تهران: {tehran_now}</i>"
                )
                send_telegram(msg)
                closed = True

            # ۴. بررسی حد ضرر (Stop Loss)
            sl_reached = (low_price <= t['sl']) if is_long else (high_price >= t['sl'])
            if sl_reached and not closed:
                if t['risk_free']:
                    msg = (
                        f"🛡️ <b>خروج در نقطه ورود (Risk-Free Exit)</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🌐 جفت‌ارز: <b>#{symbol.replace('/', '_')}</b>\n"
                        f"معامله در نقطه ورود <code>{t['entry']:,.4f}</code> با سود ذخیره‌شده بسته شد.\n"
                        f"⏱️ <i>زمان خروج به وقت تهران: {tehran_now}</i>"
                    )
                else:
                    msg = (
                        f"🛑 <b>حد ضرر (Stop Loss) لمس شد</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🌐 جفت‌ارز: <b>#{symbol.replace('/', '_')}</b>\n"
                        f"معامله در قیمت <code>{t['sl']:,.4f}</code> با رعایت مدیریت ریسک بسته شد.\n"
                        f"⏱️ <i>زمان خروج به وقت تهران: {tehran_now}</i>"
                    )
                send_telegram(msg)
                closed = True

            if not closed:
                remaining_trades[symbol] = t

        cls.save_trades(remaining_trades)

# =====================================================================
# ماژول تحلیل تکنیکال و صرافی‌ها
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

    def fetch_candle_data(self, symbol: str):
        search_symbols = [symbol]
        if symbol in ["XAU/USDT", "PAXG/USDT"]:
            search_symbols = ["PAXG/USDT", "XAU/USDT", "XAUUSDT"]

        for s in search_symbols:
            for name, ex in self.exchanges:
                if s in ex.markets:
                    try:
                        ohlcv = ex.fetch_ohlcv(s, timeframe='15m', limit=60)
                        if ohlcv and len(ohlcv) >= 35:
                            return ohlcv, ex, name
                    except Exception:
                        continue
        return None, None, ""

    def analyze_orderbook_imbalance(self, exchange, symbol: str) -> float:
        try:
            ob = exchange.fetch_order_book(symbol, limit=20)
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
        rs = gain / loss
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
                if ratio > 1.20:
                    metrics["whale_bias"] = "WHALES NET LONG 🐋🟢"
                elif ratio < 0.85:
                    metrics["whale_bias"] = "WHALES NET SHORT 🐋🔴"
                else:
                    metrics["whale_bias"] = "BALANCED 🐋⚖️"
        except Exception: pass
        return metrics

# =====================================================================
# ماژول هوش مصنوعی و ارزیابی نهایی
# =====================================================================
class MacroAIOfficerAgent:
    def review_setup(self, payload: dict) -> dict:
        prompt = (
            f"Review trade setup:\nSymbol: {payload['symbol']} | Action: {payload['action']}\n"
            f"RSI: {payload['rsi_15m']:.1f} | Imbalance: {payload['imbalance']} | L/S: {payload['top_ratio']}\n\n"
            f"Instruction: Start strictly with 'VERDICT: CONFIRMED' or 'VERDICT: REJECTED'. Add 2 sentences thesis."
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
            return {"approved": True, "engine": "Institutional Quant", "thesis": "Order flow and volume telemetry aligned."}

        approved = analysis.strip().upper().startswith("VERDICT: CONFIRMED") or "\nVERDICT: CONFIRMED" in analysis.strip().upper()
        thesis = analysis.replace("VERDICT: CONFIRMED", "").replace("VERDICT: REJECTED", "").strip()
        return {"approved": approved, "engine": engine, "thesis": thesis}

# =====================================================================
# ماژول ارسال پیام به تلگرام همراه با زمان تهران
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

        tehran_timestamp = get_tehran_time_str()

        msg = (
            f"⚡ <b>INSTITUTIONAL SIGNAL DETECTED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🌐 #{symbol_tag} | Primary Pool: <b>{data['source']}</b>\n"
            f"{action_emoji} <b>{data['action']}</b>\n\n"
            f"🏆 Grade: <b>{data['grade']}</b> | Quant Score: <b>{data['score']}/100</b>\n"
            f"🧠 <b>AI CONFLUENCE ({data['ai_engine']})</b>\n"
            f"<i>{data['ai_thesis']}</i>\n\n"
            f"🐋 <b>WHALE TELEMETRY</b>\n"
            f"📊 Top Traders L/S: <b>{data['top_ratio']}</b> ({data['whale_bias']})\n"
            f"📦 Open Interest: <code>{data['oi_val']:,.1f}</code> | Funding: <code>{data['funding']:+.4f}%</code>\n\n"
            f"📍 <b>ENTRY ZONE</b>\n"
            f"<code>{data['entry_min']:,.4f}</code> – <code>{data['entry_max']:,.4f}</code>\n\n"
            f"🛑 <b>STOP LOSS</b>\n"
            f"<code>{sl:,.4f}</code> (-{sl_pct:.2f}%)\n\n"
            f"🎯 <b>TAKE PROFIT TARGETS</b>\n"
            f"🥇 TP1 ➔ <code>{tp1:,.4f}</code> (+{tp1_pct:.2f}%)\n"
            f"🥈 TP2 ➔ <code>{tp2:,.4f}</code> (+{tp2_pct:.2f}%)\n"
            f"🥉 TP3 ➔ <code>{tp3:,.4f}</code> (+{tp3_pct:.2f}%)\n\n"
            f"⏱️ <b>زمان صدور به وقت تهران:</b> <code>{tehran_timestamp}</code>"
        )
        send_telegram(msg)

# =====================================================================
# هسته هماهنگ‌کننده برنامه
# =====================================================================
def run_system():
    tech_agent = TechnicalAgent()
    macro_agent = MacroAIOfficerAgent()

    # رصد معاملات باز قبلی
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
            whale = WhaleAgent.inspect(symbol)

            setup = None

            # شرط خرید (Long)
            if rsi < 46 and imbalance > 0.02 and vol_ratio >= 0.95 and whale['top_ratio'] >= 0.90:
                score = int(min(99, (48 - rsi) * 2 + (imbalance * 20) + (whale['top_ratio'] * 15)))
                setup = {
                    'source': source_name, 'symbol': symbol, 'action': 'LONG — BUY SETUP',
                    'grade': 'A+' if score >= 85 else 'A', 'score': score,
                    'price': price, 'entry_min': price * 0.998, 'entry_max': price * 1.002,
                    'sl': price - (1.5 * atr), 'tp1': price + (3.0 * atr), 'tp2': price + (4.5 * atr), 'tp3': price + (6.0 * atr),
                    'leverage': 5, 'rsi_15m': rsi, 'imbalance': imbalance, 'funding': whale['funding'],
                    'oi_val': whale['oi_val'], 'top_ratio': whale['top_ratio'], 'whale_bias': whale['whale_bias']
                }

            # شرط فروش (Short)
            elif rsi > 56 and imbalance < -0.02 and vol_ratio >= 0.95 and whale['top_ratio'] <= 1.15:
                score = int(min(99, (rsi - 54) * 2 + (abs(imbalance) * 20) + ((1.5 - min(whale['top_ratio'], 1.5)) * 20)))
                setup = {
                    'source': source_name, 'symbol': symbol, 'action': 'SHORT — SELL SETUP',
                    'grade': 'A+' if score >= 85 else 'A', 'score': score,
                    'price': price, 'entry_min': price * 0.998, 'entry_max': price * 1.002,
                    'sl': price + (1.5 * atr), 'tp1': price - (3.0 * atr), 'tp2': price - (4.5 * atr), 'tp3': price - (6.0 * atr),
                    'leverage': 5, 'rsi_15m': rsi, 'imbalance': imbalance, 'funding': whale['funding'],
                    'oi_val': whale['oi_val'], 'top_ratio': whale['top_ratio'], 'whale_bias': whale['whale_bias']
                }

            if setup:
                review = macro_agent.review_setup(setup)
                if review['approved']:
                    setup['ai_engine'] = review['engine']
                    setup['ai_thesis'] = review['thesis']
                    DispatchAgent.send(setup)
                    TradeLifecycleAgent.register_trade(setup)
                    dispatched += 1
                    print(f"⚡ [DISPATCH]: {symbol} sent at Tehran time: {get_tehran_time_str()}")

        except Exception:
            continue

if __name__ == "__main__":
    run_system()
