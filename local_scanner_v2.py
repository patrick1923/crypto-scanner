import asyncio
import sys
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import pandas as pd
import ccxt.pro as ccxt_pro
import time
from datetime import datetime, timedelta
import pytz
import requests
from database import log_liquidity_context

# ================= TELEGRAM =================

TELEGRAM_TOKEN = "8186631543:AAF8IX7WYCvy-lC78P95aBYzK0wfu0jZlrg"
TELEGRAM_CHAT_ID = "8479800068"

def send_telegram_message(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        requests.post(url, data=payload, timeout=5)
    except:
        print("Telegram send failed.")

# ================= CONFIG =================

KSA_TIMEZONE = pytz.timezone('Asia/Riyadh')
DISTANCE_THRESHOLD = 0.002  # 0.2%

# ============================================================
# DAILY COUNTDOWN
# ============================================================

def get_daily_countdown():
    now = datetime.now(pytz.utc)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    remaining = tomorrow - now

    hours, remainder = divmod(remaining.seconds, 3600)
    minutes, _ = divmod(remainder, 60)

    return f"{hours}h {minutes}m remaining"

# ============================================================
# PRELOAD PREVIOUS DAY LEVELS
# ============================================================

async def preload_daily_levels(exchange, symbols):

    daily_levels = {}

    for symbol in symbols:
        try:
            daily = await exchange.fetch_ohlcv(symbol, timeframe='1d', limit=2)
            if len(daily) < 2:
                continue

            prev_high = daily[-2][2]
            prev_low = daily[-2][3]

            daily_levels[symbol] = {
                "high": prev_high,
                "low": prev_low
            }

        except:
            continue

    return daily_levels

# ============================================================
# MODEL ACTION ENGINE
# ============================================================

def get_model_action(structure, impulse, behavior, volume, volatility):

    if structure == "Bullish":

        if behavior == "Compression" and volume == "Increasing":
            return "Breakout Continuation Likely", "Wait for 1m base then expansion break."

        if behavior == "Expansion" and volume == "Increasing":
            return "Momentum Active", "Wait for pullback before continuation entry."

        if behavior == "Compression" and volume == "Decreasing":
            return "Liquidity Building", "Wait for volume expansion confirmation."

        return "Unclear Bullish Condition", "No trade. Wait for cleaner setup."

    else:

        if behavior == "Compression" and volume == "Increasing":
            return "Breakdown Continuation Likely", "Wait for 1m breakdown with expansion."

        if behavior == "Expansion" and volume == "Increasing":
            return "Bearish Momentum Active", "Wait for pullback before short continuation."

        if behavior == "Compression" and volume == "Decreasing":
            return "Liquidity Building Below", "Wait for expansion before short."

        return "Unclear Bearish Condition", "No trade. Wait for cleaner setup."

# ============================================================
# MAIN SCAN
# ============================================================

async def scan_all():

    exchange = ccxt_pro.binance({'options': {'defaultType': 'future'}})

    try:
        await exchange.load_markets()

        print("🔄 Starting Liquidity Radar Scan...")
        send_telegram_message("🔄 <b>Liquidity Radar Scan Started</b>")

        tickers = await exchange.fetch_tickers()

        usdt_futures = {
            symbol: data for symbol, data in tickers.items()
            if symbol.endswith(':USDT')
            and data.get('quoteVolume') is not None
        }

        sorted_symbols = sorted(
            usdt_futures.items(),
            key=lambda x: x[1]['quoteVolume'],
            reverse=True
        )

        symbols = [s[0] for s in sorted_symbols[:50]]

        print(f"Selected Top {len(symbols)} ultra-liquid pairs.")

        daily_levels = await preload_daily_levels(exchange, symbols)

        alerts = []

        for symbol in symbols:

            if symbol not in daily_levels:
                continue

            try:
                ticker = await exchange.fetch_ticker(symbol)
                current_price = ticker['last']

                prev_day_high = daily_levels[symbol]['high']
                prev_day_low = daily_levels[symbol]['low']

                distance_high = abs(current_price - prev_day_high) / prev_day_high
                distance_low = abs(current_price - prev_day_low) / prev_day_low

                try:
                    ohlcv = await exchange.fetch_ohlcv(symbol, '15m', limit=21)
                    if not ohlcv or len(ohlcv) < 21:
                        continue

                    df = pd.DataFrame(ohlcv, columns=['ts','o','h','l','c','v'])
                    last = df.iloc[-1]

                    avg_volume = df['v'].iloc[:-1].mean()
                    avg_range = (df['h'] - df['l']).iloc[:-1].mean()

                    if avg_volume == 0 or avg_range == 0:
                        continue

                    volume_ratio = last['v'] / avg_volume
                    volatility_ratio = (last['h'] - last['l']) / avg_range

                    # ---- SIMPLE CONTEXT CLASSIFICATION ----
                    structure = "Bullish" if df['c'].iloc[-1] > df['c'].iloc[-5] else "Bearish"

                    impulse_strength = (
                        "Strong Expansion" if volatility_ratio > 1.5
                        else "Moderate" if volatility_ratio > 1.0
                        else "Weak"
                    )

                    behavior = (
                        "Compression" if volatility_ratio < 0.8
                        else "Expansion" if volatility_ratio > 1.2
                        else "Normal"
                    )

                    volume_state = (
                        "Increasing" if volume_ratio > 1.2
                        else "Decreasing" if volume_ratio < 0.8
                        else "Stable"
                    )

                    volatility_state = (
                        "Expanding" if volatility_ratio > 1.2
                        else "Contracting" if volatility_ratio < 0.8
                        else "Stable"
                    )

                except:
                    continue

                # ===============================
                # PDH APPROACH
                # ===============================
                if distance_high <= DISTANCE_THRESHOLD:

                    model_bias, action = get_model_action(
                        structure,
                        impulse_strength,
                        behavior,
                        volume_state,
                        volatility_state
                    )

                    alerts.append(
                        f"{symbol} approaching PDH ({distance_high*100:.2f}%)\n\n"
                        f"Context:\n"
                        f"• 15m Structure: {structure}\n"
                        f"• Impulse: {impulse_strength}\n"
                        f"• Behavior: {behavior}\n"
                        f"• Volume: {volume_state}\n"
                        f"• Volatility: {volatility_state}\n\n"
                        f"Model Bias:\n"
                        f"→ {model_bias}\n"
                        f"Action:\n"
                        f"{action}\n"
                    )

                    log_liquidity_context({
                        "symbol": symbol,
                        "level_type": "PDH",
                        "distance_percent": distance_high * 100,
                        "structure": structure,
                        "impulse_strength": impulse_strength,
                        "behavior": behavior,
                        "volume_state": volume_state,
                        "volatility_state": volatility_state,
                        "model_bias": model_bias
                    })

                # ===============================
                # PDL APPROACH
                # ===============================
                elif distance_low <= DISTANCE_THRESHOLD:

                    model_bias, action = get_model_action(
                        structure,
                        impulse_strength,
                        behavior,
                        volume_state,
                        volatility_state
                    )

                    alerts.append(
                        f"{symbol} approaching PDL ({distance_low*100:.2f}%)\n\n"
                        f"Context:\n"
                        f"• 15m Structure: {structure}\n"
                        f"• Impulse: {impulse_strength}\n"
                        f"• Behavior: {behavior}\n"
                        f"• Volume: {volume_state}\n"
                        f"• Volatility: {volatility_state}\n\n"
                        f"Model Bias:\n"
                        f"→ {model_bias}\n"
                        f"Action:\n"
                        f"{action}\n"
                    )

                    log_liquidity_context({
                        "symbol": symbol,
                        "level_type": "PDL",
                        "distance_percent": distance_low * 100,
                        "structure": structure,
                        "impulse_strength": impulse_strength,
                        "behavior": behavior,
                        "volume_state": volume_state,
                        "volatility_state": volatility_state,
                        "model_bias": model_bias
                    })

            except:
                continue

        if alerts:

            countdown = get_daily_countdown()

            message = (
                f"⚠️ <b>LIQUIDITY RADAR</b>\n\n"
                f"Daily Candle Close In: {countdown}\n\n"
            )

            for alert in alerts:
                message += alert + "\n"

            send_telegram_message(message)
            print("Liquidity alerts sent.")

        else:
            print("No liquidity proximity detected.")

    except Exception as e:
        print(f"Scan error: {e}")

    finally:
        await exchange.close()
        print("Exchange session closed cleanly.")
# ============================================================
# LOOP
# ============================================================

def run_scan():
    asyncio.run(scan_all())

if __name__ == "__main__":

    print("=== Liquidity Radar Started ===")

    while True:
        run_scan()
        print("Waiting 5 minutes...\n")
        time.sleep(300)