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
# MAIN SCAN
# ============================================================

async def scan_all():

    exchange = ccxt_pro.binance({'options': {'defaultType': 'future'}})

    try:
        await exchange.load_markets()

        print("🔄 Starting Liquidity Radar Scan...")
        send_telegram_message("🔄 <b>Liquidity Radar Scan Started</b>")

        # ===============================
        # GET TOP 50 BY 24H VOLUME
        # ===============================
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

        # ===============================
        # PRELOAD DAILY LEVELS
        # ===============================
        daily_levels = await preload_daily_levels(exchange, symbols)

        alerts = []

        # ===============================
        # SCAN SYMBOLS
        # ===============================
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

                # ===============================
            # Get 15m data for volume + volatility check
            # Skip pairs with missing or zero data
            # ===============================
                try:
                    ohlcv = await exchange.fetch_ohlcv(symbol, '15m', limit=21)
                    if not ohlcv or len(ohlcv) < 2:
                        continue  # skip if no data

                    df = pd.DataFrame(ohlcv, columns=['ts','o','h','l','c','v'])
                    last = df.iloc[-1]

                    avg_volume = df['v'].iloc[:-1].mean()
                    if avg_volume == 0 or last['v'] == 0:
                        continue  # skip zero volume

                    volume_ratio = last['v'] / avg_volume

                    avg_range = (df['h'] - df['l']).iloc[:-1].mean()
                    if avg_range == 0:
                        continue  # skip zero range

                    current_range = last['h'] - last['l']
                    volatility_ratio = current_range / avg_range

                except:
                    continue  # skip on any fetch error

                # ===============================
                # PDH APPROACH
                # ===============================
                if distance_high <= DISTANCE_THRESHOLD:

                    alerts.append(
                        f"{symbol}\n"
                        f"Approaching PDH\n"
                        f"Distance: {distance_high*100:.2f}%\n"
                        f"Volume Ratio: {volume_ratio:.2f}\n"
                        f"Volatility Ratio: {volatility_ratio:.2f}\n"
                    )

                # ===============================
                # PDL APPROACH
                # ===============================
                elif distance_low <= DISTANCE_THRESHOLD:

                    alerts.append(
                        f"{symbol}\n"
                        f"Approaching PDL\n"
                        f"Distance: {distance_low*100:.2f}%\n"
                        f"Volume Ratio: {volume_ratio:.2f}\n"
                        f"Volatility Ratio: {volatility_ratio:.2f}\n"
                    )

            except:
                continue

        # ===============================
        # SEND TELEGRAM ALERT
        # ===============================
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