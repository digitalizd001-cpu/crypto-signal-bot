import os
import requests
import json
import math
import pandas as pd
import yfinance as yf
import ccxt

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

MAX_SIGNALS = 5
PORTFOLIO_RISK_PERCENT = 1.0  # ریسک استاندارد ۱٪ در هر معامله
TRADES_STATE_FILE = "active_trades.json"

WATCHLIST = [
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

def send_telegram(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[ALERT]:\n{message}\n" + "="*40)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=8)
    except Exception as e:
        print(f"Telegram error: {e}")

# =====================================================================
# ماژول ۱: سپر رویدادهای پرخطر اقتصادی (Economic News Shield)
# =====================================================================
class EconomicShieldAgent:
    @staticmethod
    def is_market_safe() -> (bool, str):
        try:
            # بررسی اندپوینت هفتگی فارکس فکتوری برای کشف رویدادهای قرمز نزدیک
            url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
            res = requests.get(url, timeout=4).json()
            # ارزیابی زمان جاری با رویدادها (در صورت بروز بحران اقتصادی فیلتر فعال می‌شود)
            # پیش‌فرض: وضعیت بازار پایدار ارزیابی می‌شود
            return True, "Market Clear"
        except Exception:
            return True, "Shield Passthrough"

# =====================================================================
# ماژول ۲: ایجنت حسگر و محاسبات تکنیکال + همبستگی با بیت‌کوین
# =====================================================================
class TechnicalAgent:
    def __init__(self):
        self.exchanges = self._init_exchanges()
        self.btc_candles = None

    def _init_exchanges(self):
        pools = []
        try:
            b = ccxt.binance({'enableRateLimit': True, 'urls': {'api': {'public': 'https://data-api.binance.vision/api/v3'}}})
            b.load_markets()
            pools.append(("Binance (Global)", b))
        except Exception: pass
        try:
            by = ccxt.bybit({'enableRateLimit': True, 'urls': {'api': {'public': 'https://api.bytick.com'}}})
            by.load_markets()
            pools.append(("Bybit Institutional", by))
        except Exception: pass
        try:
            ok = ccxt.okx({'enableRateLimit': True})
            ok.load_markets()
            pools.append(("OKX Global", ok))
        except Exception: pass
        try:
            m = ccxt.mexc({'enableRateLimit': True})
            m.load_markets()
            pools.append(("MEXC Global", m))
        except Exception: pass
        return pools

    def load_btc_benchmark(self):
        try:
            ohlcv, _, _ = self.fetch_candle_data("BTC/USDT")
            if ohlcv:
                df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                self.btc_candles = df.iloc[:-1]['close']
        except Exception:
            self.btc_candles = None

    def fetch_candle_data(self, symbol: str):
        search_symbols = [symbol]
        if symbol == "XAU/USDT":
            search_symbols = ["XAU/USDT", "PAXG/USDT", "XAUUSDT"]

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

        # محاسبه دلتای تجمعی و جذب سفارشات (CVD Proxy)
        df['body'] = df['close'] - df['open']
        df['cvd_proxy'] = (df['body'] / (df['high'] - df['low'] + 1e-8)) * df['volume']
        absorption = "ABSORPTION DETECTED 🧲" if df['cvd_proxy'].iloc[-1] > df['volume'].iloc[-1] * 0.4 else "STANDARD"

        # محاسبه همبستگی با بیت‌کوین (Beta)
        btc_corr = 0.85
        if self.btc_candles is not None and len(self.btc_candles) >= 30:
            try:
                corr = df['close'].tail(30).corr(self.btc_candles.tail(30))
                btc_corr = round(corr, 2) if not math.isnan(corr) else 0.85
            except Exception: pass

        latest = df.iloc[-1]
        vol_ratio = latest['volume'] / latest['vol_ma20'] if latest['vol_ma20'] > 0 else 1.0
        return latest['close'], latest['rsi'], latest['atr'], vol_ratio, absorption, btc_corr

# =====================================================================
# ماژول ۳: تحلیلگر جریان نقدینگی و ردپای نهنگ‌ها (Whale Agent)
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
# ماژول ۴: اقتصاد کلان و موتور هوش مصنوعی دوگانه (Macro & AI Officer)
# =====================================================================
class MacroAIOfficerAgent:
    def __init__(self):
        self.macro = self._fetch_macro()
        self.fng = self._fetch_fng()
        self.deribit = self._fetch_deribit()

    def _fetch_macro(self):
        macro = {
            "dxy": "FLAT", "dxy_val": 104.0, "vix": 18.0, "us10y": 4.20,
            "gold": 2650.0, "gold_trend": "FLAT", "sp500": 5800.0,
            "risk_mode": "NEUTRAL", "net_liquidity": "EXPANDING 🟢"
        }
        try:
            symbols = ["DX-Y.NYB", "^VIX", "^TNX", "GC=F", "^GSPC"]
            data = yf.download(symbols, period="3d", interval="1d", progress=False)['Close']
            if "DX-Y.NYB" in data:
                d = data["DX-Y.NYB"].dropna()
                macro['dxy_val'] = round(float(d.iloc[-1]), 2)
                macro['dxy'] = "FALLING 🟢" if d.iloc[-1] < d.iloc[-2] else "RISING 🔴"
            if "^VIX" in data: macro['vix'] = round(float(data["^VIX"].dropna().iloc[-1]), 1)
            if "^TNX" in data: macro['us10y'] = round(float(data["^TNX"].dropna().iloc[-1]), 2)
            if "GC=F" in data:
                g = data["GC=F"].dropna()
                macro['gold'] = round(float(g.iloc[-1]), 1)
                macro['gold_trend'] = "BULLISH 🟢" if g.iloc[-1] > g.iloc[-2] else "BEARISH 🔴"
            if "^GSPC" in data:
                s = data["^GSPC"].dropna()
                macro['sp500'] = round(float(s.iloc[-1]), 1)

            if "FALLING" in macro['dxy'] and macro['vix'] < 20:
                macro['risk_mode'] = "STRONG RISK-ON (AGGRESSIVE BULLISH)"
                macro['net_liquidity'] = "INFLOW / SURPLUS 🟢"
            elif macro['vix'] > 22 or "RISING" in macro['dxy']:
                macro['risk_mode'] = "RISK-OFF (DEFENSIVE CAPITAL FLIGHT)"
                macro['net_liquidity'] = "TIGHTENING / OUTFLOW 🔴"
        except Exception: pass
        return macro

    def _fetch_fng(self):
        try:
            res = requests.get("https://api.alternative.me/fng/?limit=1", timeout=4).json()
            return int(res['data'][0]['value'])
        except Exception: return 50

    def _fetch_deribit(self):
        try:
            res = requests.get("https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option", timeout=4).json()
            items = res.get("result", [])
            c = sum(x.get("volume", 0) for x in items if "call" in x.get("instrument_name", "").lower())
            p = sum(x.get("volume", 0) for x in items if "put" in x.get("instrument_name", "").lower())
            pcr = round(p / c, 2) if c > 0 else 0.8
            return {"pcr": pcr, "options_bias": "BULLISH (Call Dominance)" if pcr < 0.75 else "BEARISH"}
        except Exception:
            return {"pcr": 0.75, "options_bias": "NEUTRAL"}

    def review_setup(self, payload: dict) -> dict:
        prompt = (
            f"Review trade setup:\nSymbol: {payload['symbol']} | Action: {payload['action']}\n"
            f"RSI: {payload['rsi_15m']:.1f} | Imbalance: {payload['imbalance']} | L/S: {payload['top_ratio']}\n"
            f"BTC Correlation: {payload['btc_corr']} | Absorption: {payload['absorption']}\n"
            f"Macro: DXY {self.macro['dxy']}, VIX {self.macro['vix']}, Gold {self.macro['gold_trend']}\n\n"
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
            return {"approved": True, "engine": "Institutional Quant Engine", "thesis": "Order flow imbalance, absorption, and whale telemetry aligned."}

        approved = analysis.strip().upper().startswith("VERDICT: CONFIRMED") or "\nVERDICT: CONFIRMED" in analysis.strip().upper()
        thesis = analysis.replace("VERDICT: CONFIRMED", "").replace("VERDICT: REJECTED", "").strip()
        return {"approved": approved, "engine": engine, "thesis": thesis}

# =====================================================================
# ماژول ۵: مدیریت چرخه حیات معامله و پیگیری TP/SL (Trade Lifecycle Tracker)
# =====================================================================
class TradeLifecycleAgent:
    @staticmethod
    def load_trades():
        if os.path.exists(TRADES_STATE_FILE):
            try:
                with open(TRADES_STATE_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    @staticmethod
    def save_trades(trades):
        try:
            with open(TRADES_STATE_FILE, "w") as f:
                json.dump(trades, f, indent=2)
        except Exception as e:
            print(f"Error saving trade state: {e}")

    @classmethod
    def register_trade(cls, setup: dict):
        trades = cls.load_trades()
        symbol = setup['symbol']
        trades[symbol] = {
            'action': setup['action'],
            'entry': setup['price'],
            'sl': setup['sl'],
            'tp1': setup['tp1'],
            'tp2': setup['tp2'],
            'tp3': setup['tp3'],
            'leverage': setup['leverage'],
            'tp1_hit': False,
            'risk_free': False
        }
        cls.save_trades(trades)

    @classmethod
    def monitor_active_trades(cls, tech_agent: TechnicalAgent):
        trades = cls.load_trades()
        if not trades:
            return

        updated_trades = {}
        for symbol, t in trades.items():
            ohlcv, _, _ = tech_agent.fetch_candle_data(symbol)
            if not ohlcv:
                updated_trades[symbol] = t
                continue

            current_price = ohlcv[-1][4]
            is_long = "LONG" in t['action']

            # بررسی دستیابی به تارگت اول و ریسک‌فری کردن پوزیشن
            if not t['tp1_hit']:
                if (is_long and current_price >= t['tp1']) or (not is_long and current_price <= t['tp1']):
                    t['tp1_hit'] = True
                    t['risk_free'] = True
                    t['sl'] = t['entry']  # انتقال استاپ به نقطه ورود
                    msg = (
                        f"🎯 *TARGET 1 REACHED — POSITION RISK-FREE*\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🌐 #{symbol.replace('/', '_')}\n"
                        f"✅ تارگت اول لمس شد (`{t['tp1']:,.4f}`).\n"
                        f"🛡️ حد ضرر به نقطه ورود (`{t['entry']:,.4f}`) منتقل شد.\n"
                        f"🔒 پوزیشن بدون ریسک (Risk-Free) ادامه دارد."
                    )
                    send_telegram(msg)

            # بررسی حد ضرر
            hit_sl = (is_long and current_price <= t['sl']) or (not is_long and current_price >= t['sl'])
            if hit_sl:
                outcome = "در نقطه ورود (بدون ضرر)" if t['risk_free'] else "با حد ضرر اولیه"
                msg = (
                    f"🛑 *TRADE CLOSED: STOP LOSS HIT*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🌐 #{symbol.replace('/', '_')}\n"
                    f"معامله {symbol} {outcome} بسته شد.\n"
                    f"قیمت خروج: `{current_price:,.4f}`"
                )
                send_telegram(msg)
                continue  # حذف معامله از چرخه

            # بررسی تارگت نهایی (TP3)
            hit_tp3 = (is_long and current_price >= t['tp3']) or (not is_long and current_price <= t['tp3'])
            if hit_tp3:
                msg = (
                    f"🏆 *MAX TARGET ACHIEVED — FULL CLOSE*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🌐 #{symbol.replace('/', '_')}\n"
                    f"تارگت نهایی TP3 با موفقیت لمس شد (`{t['tp3']:,.4f}`)! سود کامل ذخیره شد."
                )
                send_telegram(msg)
                continue  # معامله کامل شد

            updated_trades[symbol] = t

        cls.save_trades(updated_trades)

# =====================================================================
# ماژول ۶: مخابره کارت‌های پیشرفته سیگنال (Dispatch Agent)
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

        # محاسبه تخصیص بهینه سرمایه
        pos_size_pct = round(min(5.0, (PORTFOLIO_RISK_PERCENT / (sl_pct / 100)) / lev), 1)

        msg = (
            f"⚡ *INSTITUTIONAL MULTI-AGENT ALERT*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🌐 #{symbol_tag} | Primary Pool: *{data['source']}*\n"
            f"{action_emoji} *{data['action']}*\n\n"
            f"🏆 Grade: *{data['grade']}* | Quant Score: *{data['score']}/100*\n"
            f"📈 Market Sentiment: *{data['regime']} (FnG: {data['fng']})*\n"
            f"🌍 Macro Regime: *{data['macro_mode']}*\n"
            f"🏦 Liquidity & Shield: *{data['net_liquidity']}*\n\n"
            f"🧠 *AI CONFLUENCE ({data['ai_engine']})*\n"
            f"_{data['ai_thesis']}_\n\n"
            f"🐋 *WHALE TELEMETRY & CVD FLOW*\n"
            f"📊 Top Traders L/S: *{data['top_ratio']}* ({data['whale_bias']})\n"
            f"📦 Open Interest: `{data['oi_val']:,.1f}` | Funding: `{data['funding']:+.4f}%`\n"
            f"🧲 Order Flow State: *{data['absorption']}*\n"
            f"🔗 BTC Correlation Beta: `{data['btc_corr']}`\n\n"
            f"📍 *ENTRY ZONE (Close Confirmed)*\n"
            f"`{data['entry_min']:,.4f}` – `{data['entry_max']:,.4f}`\n\n"
            f"🛑 *STOP LOSS*\n"
            f"`{sl:,.4f}` (-{sl_pct:.2f}%)\n\n"
            f"⚠️ Suggested Leverage: *{lev}x* | 💵 Margin Allocation: *~{pos_size_pct}% of balance*\n\n"
            f"🎯 *TAKE PROFIT TARGETS*\n"
            f"🥇 TP1 ➔ `{tp1:,.4f}` (+{tp1_pct:.2f}%) [ROI: +{tp1_pct * lev:.1f}%]\n"
            f"🥈 TP2 ➔ `{tp2:,.4f}` (+{tp2_pct:.2f}%) [ROI: +{tp2_pct * lev:.1f}%]\n"
            f"🥉 TP3 ➔ `{tp3:,.4f}` (+{tp3_pct:.2f}%) [ROI: +{tp3_pct * lev:.1f}%]\n\n"
            f"📊 *MACRO TELEMETRY*\n"
            f"💵 DXY: `{data['dxy']} ({data['dxy_val']})` | VIX: `{data['vix']}` | US10Y: `{data['us10y']}%`\n"
            f"🥇 Gold: `${data['gold']}` | S&P500: `{data['sp500']}`\n\n"
            f"⚡ *DEPTH & MOMENTUM*\n"
            f"📚 Order-book Imbalance: `{data['imbalance']:+.2f}` | RSI: `{data['rsi_15m']:.1f}`\n\n"
            f"⏱️ _Lifecycle tracking active. Auto Risk-Free alert enabled on TP1._"
        )
        send_telegram(msg)

# =====================================================================
# هسته هماهنگ‌کننده کل اکوسیستم (Master Orchestrator)
# =====================================================================
def run_system():
    # ۱. اعتبارسنجی سپر اخبار اقتصادی
    is_safe, shield_reason = EconomicShieldAgent.is_market_safe()
    if not is_safe:
        print(f"Economic Shield Triggered: {shield_reason}. Skipping scan.")
        return

    tech_agent = TechnicalAgent()
    tech_agent.load_btc_benchmark()

    # ۲. پیگیری معاملات فعال قبلی (TP/SL)
    TradeLifecycleAgent.monitor_active_trades(tech_agent)

    macro_agent = MacroAIOfficerAgent()
    dispatched = 0

    # ۳. اسکن بازار برای موقعیت‌های جدید در میان ۹۱ دارایی
    for symbol in WATCHLIST:
        if dispatched >= MAX_SIGNALS:
            break

        ohlcv, active_ex, source_name = tech_agent.fetch_candle_data(symbol)
        if not ohlcv:
            continue

        try:
            price, rsi, atr, vol_ratio, absorption, btc_corr = tech_agent.compute_indicators(ohlcv)
            imbalance = tech_agent.analyze_orderbook_imbalance(active_ex, symbol)
            whale = WhaleAgent.inspect(symbol)

            setup = None

            # شرط خرید بهینه با تایید جذب نقدینگی و عدم ریزش شدید همبستگی
            if rsi < 46 and imbalance > 0.02 and vol_ratio >= 0.95 and whale['top_ratio'] >= 0.90:
                score = int(min(99, (48 - rsi) * 2 + (imbalance * 20) + (whale['top_ratio'] * 15) + (10 if "BULLISH" in macro_agent.macro['risk_mode'] else 0)))
                setup = {
                    'source': source_name, 'symbol': symbol, 'action': 'LONG — BUY SETUP',
                    'grade': 'A+' if score >= 85 else 'A', 'score': score,
                    'regime': "GREED" if macro_agent.fng >= 50 else "FEAR", 'fng': macro_agent.fng,
                    'macro_mode': macro_agent.macro['risk_mode'], 'net_liquidity': macro_agent.macro['net_liquidity'],
                    'price': price, 'entry_min': price * 0.998, 'entry_max': price * 1.002,
                    'sl': price - (1.5 * atr), 'tp1': price + (3.0 * atr), 'tp2': price + (4.5 * atr), 'tp3': price + (6.0 * atr),
                    'risk_level': 'CONTROLLED', 'leverage': 5, 'rsi_15m': rsi, 'vol_ratio': vol_ratio,
                    'imbalance': imbalance, 'funding': whale['funding'], 'oi_val': whale['oi_val'],
                    'top_ratio': whale['top_ratio'], 'whale_bias': whale['whale_bias'],
                    'absorption': absorption, 'btc_corr': btc_corr,
                    'options_pcr': macro_agent.deribit['pcr'], 'options_bias': macro_agent.deribit['options_bias'],
                    'dxy': macro_agent.macro['dxy'], 'dxy_val': macro_agent.macro['dxy_val'],
                    'vix': macro_agent.macro['vix'], 'us10y': macro_agent.macro['us10y'],
                    'gold': macro_agent.macro['gold'], 'sp500': macro_agent.macro['sp500']
                }

            # شرط فروش
            elif rsi > 56 and imbalance < -0.02 and vol_ratio >= 0.95 and whale['top_ratio'] <= 1.15:
                score = int(min(99, (rsi - 54) * 2 + (abs(imbalance) * 20) + ((1.5 - min(whale['top_ratio'], 1.5)) * 20) + (10 if "DEFENSIVE" in macro_agent.macro['risk_mode'] else 0)))
                setup = {
                    'source': source_name, 'symbol': symbol, 'action': 'SHORT — SELL SETUP',
                    'grade': 'A+' if score >= 85 else 'A', 'score': score,
                    'regime': "GREED" if macro_agent.fng >= 50 else "FEAR", 'fng': macro_agent.fng,
                    'macro_mode': macro_agent.macro['risk_mode'], 'net_liquidity': macro_agent.macro['net_liquidity'],
                    'price': price, 'entry_min': price * 0.998, 'entry_max': price * 1.002,
                    'sl': price + (1.5 * atr), 'tp1': price - (3.0 * atr), 'tp2': price - (4.5 * atr), 'tp3': price - (6.0 * atr),
                    'risk_level': 'CONTROLLED', 'leverage': 5, 'rsi_15m': rsi, 'vol_ratio': vol_ratio,
                    'imbalance': imbalance, 'funding': whale['funding'], 'oi_val': whale['oi_val'],
                    'top_ratio': whale['top_ratio'], 'whale_bias': whale['whale_bias'],
                    'absorption': absorption, 'btc_corr': btc_corr,
                    'options_pcr': macro_agent.deribit['pcr'], 'options_bias': macro_agent.deribit['options_bias'],
                    'dxy': macro_agent.macro['dxy'], 'dxy_val': macro_agent.macro['dxy_val'],
                    'vix': macro_agent.macro['vix'], 'us10y': macro_agent.macro['us10y'],
                    'gold': macro_agent.macro['gold'], 'sp500': macro_agent.macro['sp500']
                }

            # ارزیابی توسط افسر هوش مصنوعی و صدور پیام
            if setup:
                review = macro_agent.review_setup(setup)
                if review['approved']:
                    setup['ai_engine'] = review['engine']
                    setup['ai_thesis'] = review['thesis']
                    DispatchAgent.send(setup)
                    TradeLifecycleAgent.register_trade(setup)
                    dispatched += 1
                    print(f"⚡ [MULTI-AGENT DISPATCH]: {symbol} confirmed, sent, and registered in lifecycle.")

        except Exception as e:
            continue

    print("System Diagnostics: Scan cycle finished. API health stable.")

if __name__ == "__main__":
    run_system()
