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
import os
from database import log_liquidity_context
from dotenv import load_dotenv
load_dotenv()

# ================= TELEGRAM =================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

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
    
################################################

def wait_until_next_5min():

    now = datetime.utcnow()

    next_minute = (now.minute // 5 + 1) * 5
    next_time = now.replace(second=0, microsecond=0)

    if next_minute == 60:
        next_time = next_time.replace(minute=0) + timedelta(hours=1)
    else:
        next_time = next_time.replace(minute=next_minute)

    wait_seconds = (next_time - now).total_seconds()

    print(f"Next scan aligned in {int(wait_seconds)} seconds.")

    time.sleep(wait_seconds)

# ============================================================
# MAIN SCAN
# ============================================================

async def scan_all():

    exchange = ccxt_pro.binance({'options': {'defaultType': 'future'}})

    try:
        await exchange.load_markets()

        print("🔄 Starting Liquidity Radar Scan.")

        separator = "\n━━━━━━━━━━━━━━━━━━━━\n"

        donation_message = (
            "\n💙If this tool helps your trading,\n"
            "you can support development:\n\n"
            "USDT BSC BEP20\n"
            "0x7070f252c95df9a42a9c4df536b4166927a5e670\n"
        )

        tickers = await exchange.fetch_tickers()

        EXCLUDED_PAIRS = ["XAU/USDT:USDT", "XAG/USDT:USDT"]

        usdt_futures = {
            symbol: data for symbol, data in tickers.items()
            if symbol.endswith(':USDT')
            and data.get('quoteVolume') is not None
            and symbol not in EXCLUDED_PAIRS
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

                funding_rate = None
                try:
                    funding = await exchange.fetch_funding_rate(symbol)
                    funding_rate = funding['fundingRate']
                except:
                    funding_rate = None

                # FORMAT FUNDING RATE FOR TELEGRAM
                if funding_rate is not None:
                    funding_text = f"{funding_rate * 100:.4f}%"
                else:
                    funding_text = "N/A"

                prev_day_high = daily_levels[symbol]['high']
                prev_day_low = daily_levels[symbol]['low']

                distance_high = abs(current_price - prev_day_high) / prev_day_high
                distance_low = abs(current_price - prev_day_low) / prev_day_low

                # ===============================
                # FETCH 15m DATA
                # ===============================

                ohlcv = await exchange.fetch_ohlcv(symbol, '15m', limit=21)

                if not ohlcv or len(ohlcv) < 21:
                    continue

                df = pd.DataFrame(ohlcv, columns=['ts','o','h','l','c','v'])

                last = df.iloc[-1]
                prev = df.iloc[-2]

                # ===============================
                # VOLUME + VOLATILITY BASELINES
                # ===============================

                avg_volume = df['v'].iloc[:-1].mean()
                avg_range = (df['h'] - df['l']).iloc[:-1].mean()

                if avg_volume == 0 or avg_range == 0:
                    continue

                volume_ratio = last['v'] / avg_volume
                volatility_ratio = (last['h'] - last['l']) / avg_range

                # ===============================
                # LIQUIDITY SWEEP DETECTION
                # ===============================

                pdl_sweep = (
                    prev['l'] < prev_day_low and
                    prev['c'] > prev_day_low
                )

                pdh_sweep = (
                    prev['h'] > prev_day_high and
                    prev['c'] < prev_day_high
                )

                reversal_volume = volume_ratio > 1.2
                reversal_volatility = volatility_ratio > 1.1

                # ===============================
                # SWEEP STRENGTH SCORE
                # ===============================

                wick_size = abs(prev['h'] - prev['l'])
                body_size = abs(prev['c'] - prev['o'])

                wick_ratio = wick_size / body_size if body_size > 0 else 0

                score = 0

                if wick_ratio > 2:
                    score += 3

                if volume_ratio > 1.3:
                    score += 3

                if volatility_ratio > 1.2:
                    score += 2

                if pdl_sweep or pdh_sweep:
                    score += 2

                sweep_strength = min(score, 10)

                # ===============================
                # CONTEXT CLASSIFICATION
                # ===============================

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

                # ===============================
                # REVERSAL DETECTION
                # ===============================

                if pdl_sweep and sweep_strength >= 6:

                    alerts.append(
                        f"🔥 {symbol}\n"
                        f"Potential Bullish Reversal\n"
                        f"Sweep Strength: {sweep_strength}/10\n"
                        f"Funding Rate: {funding_text}\n"
                        f"Liquidity Grab Below PDL\n"
                    )
                    alerts.append(separator)

                elif pdh_sweep and sweep_strength >= 6:

                    alerts.append(
                        f"🔥 {symbol}\n"
                        f"Potential Bearish Reversal\n"
                        f"Sweep Strength: {sweep_strength}/10\n"
                        f"Funding Rate: {funding_text}\n"
                        f"Liquidity Grab Above PDH\n"
                    )
                    alerts.append(separator)

                # ===============================
                # ORIGINAL RADAR (UNCHANGED)
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
                        f"{symbol}\n"
                        f"Price: {current_price}\n"
                        f"PDH: {prev_day_high}\n"
                        f"PDL: {prev_day_low}\n\n"
                        f"Approaching PDH ({distance_high*100:.2f}%)\n\n"
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

                    alerts.append(separator)

                elif distance_low <= DISTANCE_THRESHOLD:

                    model_bias, action = get_model_action(
                        structure,
                        impulse_strength,
                        behavior,
                        volume_state,
                        volatility_state
                    )

                    alerts.append(
                        f"{symbol}\n"
                        f"Price: {current_price}\n"
                        f"PDH: {prev_day_high}\n"
                        f"PDL: {prev_day_low}\n\n"
                        f"Approaching PDL ({distance_low*100:.2f}%)\n\n"
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

                    alerts.append(separator)

            except:
                continue

        # ===============================
        # TELEGRAM SEND
        # ===============================

        if alerts:

            countdown = get_daily_countdown()

            message = (
                f"⚠️ <b>RADAR</b>\n\n"
                f"Daily Candle Close In: {countdown}\n\n"
            )

            for alert in alerts:
                message += alert + "\n"

            message += separator
            message += donation_message

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

        wait_until_next_5min()

        print("\n🔄 Running synchronized scan...\n")
        print(f"\nSCAN TIME UTC: {datetime.utcnow().strftime('%H:%M:%S')}")

        run_scan()