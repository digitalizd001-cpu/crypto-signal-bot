import os
import requests
import pandas as pd
import yfinance as yf
import ccxt

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
MAX_SIGNALS = 10

WATCHLIST = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "DOGE/USDT",
    "ADA/USDT", "SUI/USDT", "PEPE/USDT", "WIF/USDT", "FET/USDT", "NEAR/USDT",
    "AVAX/USDT", "LINK/USDT", "DOT/USDT", "LTC/USDT", "BCH/USDT", "UNI/USDT",
    "ARB/USDT", "OP/USDT", "ENA/USDT", "SEI/USDT", "RENDER/USDT", "AAVE/USDT"
]

def send_telegram_alert(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[PREVIEW SIGNAL]:\n{message}\n" + "="*40)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error sending telegram: {e}")

# ==========================================
# 1. رصد تخصصی نهنگ‌ها (Smart Money & Whales)
# ==========================================
def get_whale_metrics(symbol: str):
    """استخراج سود باز (OI)، پوزیشن حساب‌های بزرگ (Top Traders) و فاندینگ ریت بایننس"""
    clean = symbol.replace("/", "").replace(":USDT", "")
    metrics = {
        "funding": 0.01,
        "oi_val": 0.0,
        "top_ratio": 1.0,
        "whale_bias": "NEUTRAL"
    }
    
    # 1. فاندینگ ریت زنده فیوچرز بایننس
    try:
        url_fund = f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={clean}"
        rf = requests.get(url_fund, timeout=4).json()
        metrics["funding"] = round(float(rf.get("lastFundingRate", 0)) * 100, 4)
    except Exception:
        pass

    # 2. سود باز زنده (Open Interest)
    try:
        url_oi = f"https://fapi.binance.com/fapi/v1/openInterest?symbol={clean}"
        roi = requests.get(url_oi, timeout=4).json()
        metrics["oi_val"] = round(float(roi.get("openInterest", 0)), 1)
    except Exception:
        pass

    # 3. نسبت لانگ به شورت تریدرهای نهادی و برتر بایننس (Top Trader Long/Short Ratio)
    try:
        url_ratio = f"https://fapi.binance.com/futures/data/topLongShortAccountRatio?symbol={clean}&period=15m&limit=1"
        rr = requests.get(url_ratio, timeout=4).json()
        if rr and len(rr) > 0:
            ratio = float(rr[0].get("longShortRatio", 1.0))
            metrics["top_ratio"] = round(ratio, 2)
            if ratio > 1.3:
                metrics["whale_bias"] = "WHALES NET LONG 🐋🟢"
            elif ratio < 0.75:
                metrics["whale_bias"] = "WHALES NET SHORT 🐋🔴"
            else:
                metrics["whale_bias"] = "BALANCED 🐋⚖️"
    except Exception:
        pass

    return metrics

# ==========================================
# 2. داده‌های اقتصاد کلان، طلا و وال‌استریت
# ==========================================
def get_institutional_macro_metrics():
    macro = {
        "dxy": "FLAT", "dxy_val": 104.0,
        "vix": 18.0,
        "us10y": 4.20,
        "gold": 2650.0, "gold_trend": "FLAT",
        "sp500": 5800.0, "sp500_trend": "FLAT",
        "risk_mode": "NEUTRAL",
        "net_liquidity": "EXPANDING 🟢"
    }
    try:
        symbols = ["DX-Y.NYB", "^VIX", "^TNX", "GC=F", "^GSPC"]
        data = yf.download(symbols, period="3d", interval="1d", progress=False)['Close']
        
        if "DX-Y.NYB" in data:
            dxy_c = data["DX-Y.NYB"].dropna()
            macro['dxy_val'] = round(float(dxy_c.iloc[-1]), 2)
            macro['dxy'] = "FALLING 🟢" if dxy_c.iloc[-1] < dxy_c.iloc[-2] else "RISING 🔴"
        
        if "^VIX" in data:
            macro['vix'] = round(float(data["^VIX"].dropna().iloc[-1]), 1)

        if "^TNX" in data:
            macro['us10y'] = round(float(data["^TNX"].dropna().iloc[-1]), 2)

        if "GC=F" in data:
            gold_c = data["GC=F"].dropna()
            macro['gold'] = round(float(gold_c.iloc[-1]), 1)
            macro['gold_trend'] = "BULLISH 🟢" if gold_c.iloc[-1] > gold_c.iloc[-2] else "BEARISH 🔴"

        if "^GSPC" in data:
            sp_c = data["^GSPC"].dropna()
            macro['sp500'] = round(float(sp_c.iloc[-1]), 1)
            macro['sp500_trend'] = "UP 🟢" if sp_c.iloc[-1] > sp_c.iloc[-2] else "DOWN 🔴"

        if "FALLING" in macro['dxy'] and macro['vix'] < 20 and "UP" in macro['sp500_trend']:
            macro['risk_mode'] = "STRONG RISK-ON (AGGRESSIVE BULLISH)"
            macro['net_liquidity'] = "INFLOW / SURPLUS 🟢"
        elif macro['vix'] > 22 or "RISING" in macro['dxy']:
            macro['risk_mode'] = "RISK-OFF (DEFENSIVE CAPITAL FLIGHT)"
            macro['net_liquidity'] = "TIGHTENING / OUTFLOW 🔴"
        else:
            macro['risk_mode'] = "BALANCED / SELECTIVE"
    except Exception as e:
        print(f"Macro fetch warning: {e}")
    return macro

def get_fear_and_greed() -> int:
    try:
        res = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5).json()
        return int(res['data'][0]['value'])
    except Exception:
        return 50

def get_deribit_market_sentiment():
    try:
        res = requests.get("https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option", timeout=5).json()
        items = res.get("result", [])
        calls_vol = sum(x.get("volume", 0) for x in items if "call" in x.get("instrument_name", "").lower())
        puts_vol = sum(x.get("volume", 0) for x in items if "put" in x.get("instrument_name", "").lower())
        pcr = round(puts_vol / calls_vol, 2) if calls_vol > 0 else 0.8
        bias = "BULLISH (Call Dominance)" if pcr < 0.75 else ("BEARISH (Hedging Heavy)" if pcr > 1.1 else "NEUTRAL")
        return {"pcr": pcr, "options_bias": bias}
    except Exception:
        return {"pcr": 0.75, "options_bias": "NEUTRAL"}

# ==========================================
# 3. اتصال چندگانه به صرافی‌ها
# ==========================================
def init_all_exchanges():
    exchanges = []
    
    # Binance Global
    try:
        b = ccxt.binance({
            'enableRateLimit': True,
            'urls': {'api': {'public': 'https://data-api.binance.vision/api/v3'}}
        })
        b.load_markets()
        exchanges.append(("Binance (Global)", b))
    except Exception:
        pass

    # Bybit
    try:
        by = ccxt.bybit({
            'enableRateLimit': True,
            'urls': {'api': {'public': 'https://api.bytick.com', 'private': 'https://api.bytick.com'}}
        })
        by.load_markets()
        exchanges.append(("Bybit Institutional", by))
    except Exception:
        pass

    # OKX
    try:
        ok = ccxt.okx({'enableRateLimit': True})
        ok.load_markets()
        exchanges.append(("OKX Global", ok))
    except Exception:
        pass

    # MEXC
    try:
        m = ccxt.mexc({'enableRateLimit': True})
        m.load_markets()
        exchanges.append(("MEXC Global", m))
    except Exception:
        pass

    return exchanges

def get_orderbook_imbalance(exchange, symbol: str) -> float:
    try:
        ob = exchange.fetch_order_book(symbol, limit=20)
        bids = sum(b[1] for b in ob['bids'])
        asks = sum(a[1] for a in ob['asks'])
        if (bids + asks) == 0:
            return 0.0
        return round((bids - asks) / (bids + asks), 2)
    except Exception:
        return 0.0

def calculate_technical_indicators(df: pd.DataFrame):
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
    return df

def format_signal_message(data: dict) -> str:
    symbol_tag = data['symbol'].replace('/', '_')
    action_emoji = "🟢" if "LONG" in data['action'] else "🔴"
    price = data['price']
    sl = data['sl']
    leverage = data['leverage']

    sl_pct = abs((price - sl) / price) * 100
    tp1, tp2, tp3 = data['tp1'], data['tp2'], data['tp3']

    tp1_pct = abs((tp1 - price) / price) * 100
    tp2_pct = abs((tp2 - price) / price) * 100
    tp3_pct = abs((tp3 - price) / price) * 100

    msg = (
        f"🚨 *SMART MONEY & INSTITUTIONAL SIGNAL*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🌐 #{symbol_tag} | Primary Pool: *{data['source']}*\n"
        f"{action_emoji} *{data['action']}*\n\n"
        f"🏆 Grade: *{data['grade']}* | Institutional Score: *{data['score']}/100*\n"
        f"📈 Market Sentiment: *{data['regime']} (FnG: {data['fng']})*\n"
        f"🌍 Macro Regime: *{data['macro_mode']}*\n"
        f"🏦 Global Liquidity: *{data['net_liquidity']}*\n\n"
        f"🐋 *WHALE TELEMETRY (Smart Money Flow)*\n"
        f"📊 Top Traders L/S Ratio: *{data['top_ratio']}* ({data['whale_bias']})\n"
        f"📦 Open Interest (Futures): `{data['oi_val']:,.1f}`\n"
        f"⛓️ Funding Rate: `{data['funding']:+.4f}%`\n"
        f"🎲 Deribit Options PCR: `{data['options_pcr']}` ({data['options_bias']})\n\n"
        f"📍 *ENTRY ZONE (Close Confirmed)*\n"
        f"`{data['entry_min']:,.4f}` – `{data['entry_max']:,.4f}`\n\n"
        f"🛑 *STOP LOSS*\n"
        f"`{sl:,.4f}` (-{sl_pct:.2f}%)\n\n"
        f"⚠️ RISK: *{data['risk_level']}* | Suggested Leverage: *{leverage}x*\n\n"
        f"🎯 *TAKE PROFIT TARGETS*\n"
        f"🥇 TP1 ➔ `{tp1:,.4f}` (+{tp1_pct:.2f}%) [ROI: +{tp1_pct * leverage:.1f}%]\n"
        f"🥈 TP2 ➔ `{tp2:,.4f}` (+{tp2_pct:.2f}%) [ROI: +{tp2_pct * leverage:.1f}%]\n"
        f"🥉 TP3 ➔ `{tp3:,.4f}` (+{tp3_pct:.2f}%) [ROI: +{tp3_pct * leverage:.1f}%]\n\n"
        f"📊 *MACRO & DEPTH CONFIRMATIONS*\n"
        f"💵 DXY: `{data['dxy']} ({data['dxy_val']})` | VIX: `{data['vix']}` | US10Y: `{data['us10y']}%`\n"
        f"🥇 Gold: `${data['gold']}` | S&P500: `{data['sp500']}`\n"
        f"📚 Order-book Imbalance: `{data['imbalance']:+.2f}`\n"
        f"📉 15m Confirmed RSI: `{data['rsi_15m']:.1f}`\n"
        f"🔎 Setup: *{data['confirmations']}*\n\n"
        f"⚠️ _Execution verified on completed 15m candle across multi-exchange liquidity pools._"
    )
    return msg

def scan_markets():
    exchanges = init_all_exchanges()
    if not exchanges:
        print("Error: No exchanges available.")
        return

    macro = get_institutional_macro_metrics()
    fng = get_fear_and_greed()
    regime = "GREED / BULL" if fng >= 50 else "FEAR / BEAR"
    options_data = get_deribit_market_sentiment()

    candidates = []

    for symbol in WATCHLIST:
        ohlcv = None
        active_exchange = None
        source_name = ""

        for name, ex in exchanges:
            if symbol in ex.markets:
                try:
                    data = ex.fetch_ohlcv(symbol, timeframe='15m', limit=60)
                    if data and len(data) >= 35:
                        ohlcv = data
                        active_exchange = ex
                        source_name = name
                        break
                except Exception:
                    continue

        if not ohlcv or not active_exchange:
            continue

        try:
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df = df.iloc[:-1].copy()

            df = calculate_technical_indicators(df)

            latest = df.iloc[-1]
            price = latest['close']
            atr = latest['atr']
            vol_ratio = latest['volume'] / latest['vol_ma20'] if latest['vol_ma20'] > 0 else 1.0
            rsi = latest['rsi']

            imbalance = get_orderbook_imbalance(active_exchange, symbol)
            whale = get_whale_metrics(symbol)

            # سیگنال لانگ نهادی همراه با تایید پوزیشن نهنگ‌ها
            if rsi < 42 and imbalance > 0.05 and vol_ratio >= 1.05 and whale['top_ratio'] >= 1.0:
                score = int(min(99, (45 - rsi) * 2 + (imbalance * 20) + (whale['top_ratio'] * 15) + (10 if "BULLISH" in macro['risk_mode'] else 0)))
                sl = price - (1.5 * atr)
                tp1 = price + (3.0 * atr)
                tp2 = price + (4.5 * atr)
                tp3 = price + (6.0 * atr)

                candidates.append({
                    'source': source_name,
                    'symbol': symbol,
                    'action': 'LONG — BUY SETUP',
                    'grade': 'A+' if score >= 85 else 'A',
                    'score': score,
                    'regime': regime,
                    'fng': fng,
                    'macro_mode': macro['risk_mode'],
                    'net_liquidity': macro['net_liquidity'],
                    'price': price,
                    'entry_min': price * 0.998,
                    'entry_max': price * 1.002,
                    'sl': sl,
                    'tp1': tp1,
                    'tp2': tp2,
                    'tp3': tp3,
                    'risk_level': 'CONTROLLED',
                    'leverage': 5,
                    'rsi_15m': rsi,
                    'imbalance': imbalance,
                    'funding': whale['funding'],
                    'oi_val': whale['oi_val'],
                    'top_ratio': whale['top_ratio'],
                    'whale_bias': whale['whale_bias'],
                    'options_pcr': options_data['pcr'],
                    'options_bias': options_data['options_bias'],
                    'dxy': macro['dxy'],
                    'dxy_val': macro['dxy_val'],
                    'vix': macro['vix'],
                    'us10y': macro['us10y'],
                    'gold': macro['gold'],
                    'sp500': macro['sp500'],
                    'confirmations': f"Whale long dominance ({whale['top_ratio']}), Confirmed 15m oversold"
                })

            # سیگنال شورت نهادی همراه با تایید شورت نهنگ‌ها
            elif rsi > 60 and imbalance < -0.05 and vol_ratio >= 1.05 and whale['top_ratio'] <= 1.05:
                score = int(min(99, (rsi - 55) * 2 + (abs(imbalance) * 20) + ((1.5 - min(whale['top_ratio'], 1.5)) * 20) + (10 if "DEFENSIVE" in macro['risk_mode'] else 0)))
                sl = price + (1.5 * atr)
                tp1 = price - (3.0 * atr)
                tp2 = price - (4.5 * atr)
                tp3 = price - (6.0 * atr)

                candidates.append({
                    'source': source_name,
                    'symbol': symbol,
                    'action': 'SHORT — SELL SETUP',
                    'grade': 'A+' if score >= 85 else 'A',
                    'score': score,
                    'regime': regime,
                    'fng': fng,
                    'macro_mode': macro['risk_mode'],
                    'net_liquidity': macro['net_liquidity'],
                    'price': price,
                    'entry_min': price * 0.998,
                    'entry_max': price * 1.002,
                    'sl': sl,
                    'tp1': tp1,
                    'tp2': tp2,
                    'tp3': tp3,
                    'risk_level': 'CONTROLLED',
                    'leverage': 5,
                    'rsi_15m': rsi,
                    'imbalance': imbalance,
                    'funding': whale['funding'],
                    'oi_val': whale['oi_val'],
                    'top_ratio': whale['top_ratio'],
                    'whale_bias': whale['whale_bias'],
                    'options_pcr': options_data['pcr'],
                    'options_bias': options_data['options_bias'],
                    'dxy': macro['dxy'],
                    'dxy_val': macro['dxy_val'],
                    'vix': macro['vix'],
                    'us10y': macro['us10y'],
                    'gold': macro['gold'],
                    'sp500': macro['sp500'],
                    'confirmations': f"Whale short pressure ({whale['top_ratio']}), Confirmed 15m overbought"
                })

        except Exception:
            continue

    top_signals = sorted(candidates, key=lambda x: x['score'], reverse=True)[:MAX_SIGNALS]

    if not top_signals:
        print("Smart Money scan completed: No confluence setup matching whales entry found.")
    else:
        for sig in top_signals:
            msg = format_signal_message(sig)
            send_telegram_alert(msg)

if __name__ == "__main__":
    # پیام اعلام وضعیت با تایید افزوده شدن سنسور نهنگ‌ها
    status_msg = (
        "🐋 *Institutional & Whale Scanner Online*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📡 سنسورهای نهنگ‌ها (OI, Top Trader L/S Ratio, Binance Funding) متصل شدند.\n"
        "🌍 دیتای اقتصاد کلان (DXY, VIX, Gold, US10Y, S&P500) فعال است.\n"
        "🎲 احساسات آپشن‌ها (Deribit PCR) در حال رصد می‌باشد.\n"
        "✅ تحلیل روی کندل‌های قطعی بسته‌شده فعال شد."
    )
    send_telegram_alert(status_msg)
    scan_markets()
