import os
import requests
import pandas as pd
import yfinance as yf
import ccxt

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
MAX_SIGNALS = 10

WATCHLIST = [
    "PEPE/USDT", "WIF/USDT", "SEI/USDT", "DOGE/USDT", "SUI/USDT", "FET/USDT",
    "BONK/USDT", "SHIB/USDT", "SOL/USDT", "XRP/USDT", "ARB/USDT", "XLM/USDT", 
    "BTC/USDT", "ADA/USDT", "RENDER/USDT", "AAVE/USDT", "HBAR/USDT", "LTC/USDT", 
    "BCH/USDT", "JUP/USDT", "ETH/USDT", "BNB/USDT", "LINK/USDT", "DOT/USDT", 
    "FIL/USDT", "ATOM/USDT", "TIA/USDT", "TRX/USDT", "INJ/USDT", "UNI/USDT", 
    "NEAR/USDT", "OP/USDT", "ENA/USDT", "FTM/USDT", "POPCAT/USDT", "NEIRO/USDT"
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

def get_global_macro_metrics():
    macro = {"dxy": "FLAT", "vix": 18.0, "risk_mode": "NEUTRAL", "us10y": 4.0}
    try:
        tickers = yf.download(["DX-Y.NYB", "^VIX", "^TNX"], period="2d", interval="1d", progress=False)
        close = tickers['Close']
        dxy_prev, dxy_curr = close['DX-Y.NYB'].iloc[0], close['DX-Y.NYB'].iloc[-1]
        macro['dxy'] = "FALLING 🟢" if dxy_curr < dxy_prev else "RISING 🔴"
        macro['vix'] = round(float(close['^VIX'].iloc[-1]), 1)
        macro['us10y'] = round(float(close['^TNX'].iloc[-1]), 2)
        if "FALLING" in macro['dxy'] and macro['vix'] < 20:
            macro['risk_mode'] = "RISK-ON (BULLISH)"
        elif macro['vix'] > 22 or "RISING" in macro['dxy']:
            macro['risk_mode'] = "RISK-OFF (DEFENSIVE)"
    except Exception:
        pass
    return macro

def get_fear_and_greed() -> int:
    try:
        res = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5).json()
        return int(res['data'][0]['value'])
    except Exception:
        return 50

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

    roi_tp1 = tp1_pct * leverage
    roi_tp2 = tp2_pct * leverage
    roi_tp3 = tp3_pct * leverage

    msg = (
        f"🚨 *FUTURES SIGNAL*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🌐 #{symbol_tag}\n"
        f"{action_emoji} *{data['action']}*\n\n"
        f"🏆 Grade: *{data['grade']}* | Score: *{data['score']}/100*\n"
        f"📈 Market regime: *{data['regime']}*\n"
        f"🌍 Macro Regime: *{data['macro_mode']}*\n\n"
        f"📍 *ENTRY ZONE*\n"
        f"`{data['entry_min']:,.4f}` – `{data['entry_max']:,.4f}`\n\n"
        f"🛑 *STOP LOSS*\n"
        f"`{sl:,.4f}` (-{sl_pct:.2f}%)\n\n"
        f"⚠️ RISK: *{data['risk_level']}*\n"
        f"⚡ Suggested leverage: *{leverage}x*\n\n"
        f"🎯 *TAKE PROFIT*\n"
        f"🥇 TP1 ➔ `{tp1:,.4f}` (+{tp1_pct:.2f}%)\n"
        f"🥈 TP2 ➔ `{tp2:,.4f}` (+{tp2_pct:.2f}%)\n"
        f"🥉 TP3 ➔ `{tp3:,.4f}` (+{tp3_pct:.2f}%)\n\n"
        f"📊 *APPROX. ROI @ {leverage}x*\n"
        f"TP1 ➔ +{roi_tp1:.2f}%\n"
        f"TP2 ➔ +{roi_tp2:.2f}%\n"
        f"TP3 ➔ +{roi_tp3:.2f}%\n\n"
        f"⚖️ *RISK / REWARD*\n"
        f"TP1 ➔ 1:2\n"
        f"TP2 ➔ 1:3\n"
        f"TP3 ➔ 1:4\n\n"
        f"📉 RSI (15m): `{data['rsi_15m']:.1f}`\n"
        f"📚 Order-book imbalance: `{data['imbalance']:+.2f}`\n"
        f"💵 DXY: `{data['dxy']}` | VIX: `{data['vix']}` | US10Y: `{data['us10y']}%`\n"
        f"🔎 Confirmations: *{data['confirmations']}*\n\n"
        f"⚠️ _Does not include fees, funding, or slippage._\n"
        f"_Please re-check Entry and SL on the trading platform before entering._"
    )
    return msg

def scan_markets():
    exchange = ccxt.bybit({'enableRateLimit': True})
    markets = exchange.load_markets()
    
    macro = get_global_macro_metrics()
    fng = get_fear_and_greed()
    regime = "BULL" if fng >= 50 else "BEAR"

    candidates = []

    for symbol in WATCHLIST:
        if symbol not in markets:
            continue

        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe='15m', limit=50)
            if not ohlcv or len(ohlcv) < 30:
                continue

            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df = calculate_technical_indicators(df)

            latest = df.iloc[-1]
            price = latest['close']
            atr = latest['atr']
            vol_ratio = latest['volume'] / latest['vol_ma20'] if latest['vol_ma20'] > 0 else 1.0
            rsi = latest['rsi']

            imbalance = get_orderbook_imbalance(exchange, symbol)

            if rsi < 38 and imbalance > 0.10 and vol_ratio >= 1.1:
                score = int(min(99, (40 - rsi) * 2 + (imbalance * 30) + (vol_ratio * 10)))
                sl = price - (1.5 * atr)
                tp1 = price + (3.0 * atr)
                tp2 = price + (4.5 * atr)
                tp3 = price + (6.0 * atr)

                candidates.append({
                    'symbol': symbol,
                    'action': 'LONG — BUY',
                    'grade': 'A+' if score >= 85 else 'A',
                    'score': score,
                    'regime': regime,
                    'macro_mode': macro['risk_mode'],
                    'price': price,
                    'entry_min': price * 0.998,
                    'entry_max': price * 1.002,
                    'sl': sl,
                    'tp1': tp1,
                    'tp2': tp2,
                    'tp3': tp3,
                    'risk_level': 'MEDIUM',
                    'leverage': 4,
                    'rsi_15m': rsi,
                    'imbalance': imbalance,
                    'dxy': macro['dxy'],
                    'vix': macro['vix'],
                    'us10y': macro['us10y'],
                    'confirmations': '15m RSI oversold, Order-book buyer dominance'
                })

            elif rsi > 65 and imbalance < -0.10 and vol_ratio >= 1.1:
                score = int(min(99, (rsi - 60) * 2 + (abs(imbalance) * 30) + (vol_ratio * 10)))
                sl = price + (1.5 * atr)
                tp1 = price - (3.0 * atr)
                tp2 = price - (4.5 * atr)
                tp3 = price - (6.0 * atr)

                candidates.append({
                    'symbol': symbol,
                    'action': 'SHORT — SELL',
                    'grade': 'A+' if score >= 85 else 'A',
                    'score': score,
                    'regime': regime,
                    'macro_mode': macro['risk_mode'],
                    'price': price,
                    'entry_min': price * 0.998,
                    'entry_max': price * 1.002,
                    'sl': sl,
                    'tp1': tp1,
                    'tp2': tp2,
                    'tp3': tp3,
                    'risk_level': 'MEDIUM',
                    'leverage': 4,
                    'rsi_15m': rsi,
                    'imbalance': imbalance,
                    'dxy': macro['dxy'],
                    'vix': macro['vix'],
                    'us10y': macro['us10y'],
                    'confirmations': '15m RSI overbought, Order-book seller dominance'
                })

        except Exception:
            continue

    top_signals = sorted(candidates, key=lambda x: x['score'], reverse=True)[:MAX_SIGNALS]

    for sig in top_signals:
        msg = format_signal_message(sig)
        send_telegram_alert(msg)

if __name__ == "__main__":
    scan_markets()
