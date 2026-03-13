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


BINANCE_SQUARE_KEY = os.getenv("BINANCE_SQUARE_KEY")  # use env variable

def send_binance_square(text: str):
    """
    Sends a plain text post to Binance Square using the Square Skill API.
    """
    url = "https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add"
    
    headers = {
        "X-Square-OpenAPI-Key": BINANCE_SQUARE_KEY,
        "Content-Type": "application/json",
        "clienttype": "binanceSkill"
    }
    
    payload = {
        "bodyTextOnly": text
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        result = response.json()
        
        if response.status_code == 200 and result.get("success"):
            post_id = result.get("data", {}).get("id")
            print(f"Posted to Square! Link: https://www.binance.com/square/post/{post_id}")
        else:
            print(f"Binance Square post failed: {result.get('message', 'Unknown error')}")
            
    except Exception as e:
        print(f"Error connecting to Binance Square: {e}")

# ================= TELEGRAM =================
scanner_memory = {}
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

        EXCLUDED_PAIRS = ["XAU/USDT:USDT", "XAG/USDT:USDT","TSLA/USDT:USDT"]

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

                    previous_funding = funding.get("previousFundingRate")

                    funding_delta = 0
                    funding_trend = ""

                    if previous_funding is not None:
                        funding_delta = funding_rate - previous_funding
                        
                        if funding_delta > 0:
                            funding_trend = f"+{funding_delta*100:.4f}%"
                        elif funding_delta < 0:
                            funding_trend = f"{funding_delta*100:.4f}%"
                        else:
                            funding_trend = "0%"
                except:
                    funding_rate = None

                funding_text = f"{funding_rate * 100:.4f}%" if funding_rate else "N/A"

                prev_day_high = daily_levels[symbol]['high']
                prev_day_low = daily_levels[symbol]['low']
                
                # ===============================
                # DISTANCE FROM DAILY LIQUIDITY
                # ===============================

                distance_from_pdh = abs(current_price - prev_day_high) / prev_day_high
                distance_from_pdl = abs(current_price - prev_day_low) / prev_day_low

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
                # LIQUIDITY STACK ANALYSIS
                # ===============================

                lookback = 10
                candles = df.iloc[-lookback:]

                above_pdh = sum(c['h'] > prev_day_high for _, c in candles.iterrows())
                below_pdl = sum(c['l'] < prev_day_low for _, c in candles.iterrows())

                if above_pdh > below_pdl and above_pdh >= 3:
                    liquidity_bias = "Liquidity Stacked Above PDH 🔼"
                elif below_pdl > above_pdh and below_pdl >= 3:
                    liquidity_bias = "Liquidity Stacked Below PDL 🔽"
                else:
                    liquidity_bias = "Balanced Liquidity ⚖️"

                

                # ===============================
                # VOLUME + VOLATILITY BASELINES
                # ===============================

                avg_volume = df['v'].iloc[:-1].mean()
                avg_range = (df['h'] - df['l']).iloc[:-1].mean()

                if avg_volume == 0 or avg_range == 0:
                    continue

                volume_ratio = last['v'] / avg_volume
                volatility_ratio = (last['h'] - last['l']) / avg_range

                market_dead = (avg_range < current_price * 0.0015)
                if market_dead:
                    continue
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
                # ===============================
                # BREAKOUT ACCEPTANCE
                # ===============================

                body_size_last = abs(last['c'] - last['o'])
                range_last = last['h'] - last['l']

                body_strength = body_size_last / range_last if range_last > 0 else 0

                strong_acceptance = (
                    body_strength > 0.6
                    and volume_ratio > 1.3
                )

                bullish_acceptance = (
                    prev['c'] > prev_day_high
                    and last['c'] > prev_day_high
                    and strong_acceptance
                )

                bearish_acceptance = (
                    prev['c'] < prev_day_low
                    and last['c'] < prev_day_low
                    and strong_acceptance
                )
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

                structure = "Bullish" if df['c'].iloc[-1] > df['c'].iloc[-2] else "Bearish"

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
                # BREAKOUT PRESSURE DETECTION
                # ===============================

                bullish_break_pressure = (
                    structure == "Bullish"
                    and volume_ratio > 1.2
                    and volatility_ratio > 1.2
                    and behavior == "Expansion"
                )

                bearish_break_pressure = (
                    structure == "Bearish"
                    and volume_ratio > 1.2
                    and volatility_ratio > 1.2
                    and behavior == "Expansion"
                )
                # ===============================
                # PRE-EXPLOSION DETECTION
                # ===============================

                range_compression = (last['h'] - last['l']) < avg_range * 0.7

                pre_explosion = (
                    behavior == "Compression"
                    and volume_state == "Increasing"
                    and volatility_state == "Contracting"
                    and range_compression
                )
                # ===============================
                # HIGH PROBABILITY CONTINUATION
                # ===============================

                high_prob_continuation = (
                    (
                        bullish_break_pressure and bullish_acceptance
                        or
                        bearish_break_pressure and bearish_acceptance
                    )
                    and impulse_strength == "Strong Expansion"
                    and volume_ratio > 1.3
                    and volatility_ratio > 1.2
                )
                # ===============================
                # APPROACHING LIQUIDITY
                # ===============================

                approaching = ""

                if distance_from_pdh < 0.003:
                    approaching = "Approaching PDH 🔼"

                elif distance_from_pdl < 0.003:
                    approaching = "Approaching PDL 🔽"

                # ===============================
                # GLOBAL DISTANCE FILTER
                # ===============================

                max_signal_distance = 0.015  # 1.5%

                far_from_liquidity = (
                    distance_from_pdh > max_signal_distance
                    and distance_from_pdl > max_signal_distance
                )

                too_far_from_breakout = False
                if structure == "Bullish" and current_price > prev_day_high:
                    if distance_from_pdh > 0.01:
                        too_far_from_breakout = True

                if structure == "Bearish" and current_price < prev_day_low:
                    if distance_from_pdl > 0.01:
                        too_far_from_breakout = True
                
                
                # ===============================
                # TRAP DETECTION (SMART MONEY)
                # ===============================

                bullish_trap = (
                    pdl_sweep
                    and funding_rate is not None
                    and funding_rate < -0.005
                )

                bearish_trap = (
                    pdh_sweep
                    and funding_rate is not None
                    and funding_rate > 0.005
                )
                # ===============================
                # HIGH PROBABILITY REVERSAL LOGIC
                # ===============================

                high_prob_bullish_reversal = (
                    pdl_sweep
                    and last['c'] > prev_day_low
                    and sweep_strength >= 6
                    and volume_ratio > 1.2
                    and volatility_ratio > 1.1
                )

                high_prob_bearish_reversal = (
                    pdh_sweep
                    and last['c'] < prev_day_high   # rejection
                    and sweep_strength >= 6
                    and volume_ratio > 1.2
                    and volatility_ratio > 1.1
                )

                # ===============================
                # MODEL ACTION ENGINE
                # ===============================

                model_action = "Wait"
                model_instruction = "Observe market behavior"

                if behavior == "Compression":
                    model_action = "Breakout Pending"
                    model_instruction = "Watch for volatility expansion."

                elif impulse_strength == "Strong Expansion" and structure == "Bullish":
                    model_action = "Bullish Continuation Likely"
                    model_instruction = "Look for pullback long."

                elif impulse_strength == "Strong Expansion" and structure == "Bearish":
                    model_action = "Bearish Continuation Likely"
                    model_instruction = "Look for pullback short."

                elif high_prob_bullish_reversal:
                    model_action = "Bullish Reversal Setup"
                    model_instruction = "Wait for confirmation candle."

                elif high_prob_bearish_reversal:
                    model_action = "Bearish Reversal Setup"
                    model_instruction = "Watch rejection confirmation."
                # ===============================
                # ANTI SPAM MEMORY
                # ===============================

                signal_type = None

                if high_prob_bullish_reversal:
                    signal_type = "bullish_reversal"

                elif high_prob_bearish_reversal:
                    signal_type = "bearish_reversal"

                elif high_prob_continuation:
                    signal_type = "continuation"

                elif bullish_break_pressure:
                    signal_type = "bullish_pressure"

                elif bearish_break_pressure:
                    signal_type = "bearish_pressure"

                elif pre_explosion:
                    signal_type = "compression"

                now = time.time()

                signal_key = f"{symbol}_{signal_type}"

                previous = scanner_memory.get(symbol)

                if previous:
                    prev_signal, prev_time = previous
                    
                    # same signal within 30 minutes = ignore
                    if prev_signal == signal_key and (now - prev_time) < 1800:
                        continue

                scanner_memory[symbol] = (signal_key, now)
                
                # ===============================
                # LIQUIDATION CASCADE DETECTION
                # ===============================

                short_squeeze = (
                    funding_rate < -0.01
                    and volume_ratio > 1.8
                    and impulse_strength == "Strong Expansion"
                )

                long_squeeze = (
                    funding_rate > 0.01
                    and volume_ratio > 1.8
                    and impulse_strength == "Strong Expansion"
                )

                if short_squeeze:

                    alerts.append(
                        f"💥Watch ${symbol}\n"
                        f"Short Squeeze Detected\n\n"
                        f"Strong Bullish Expansion\n"
                        f"Short Positions Under Pressure\n\n"
                        f"Price: {current_price}\n"
                        f"Funding Rate: {funding_text} ({funding_trend})\n"
                        f"Volume Spike: {volume_ratio:.2f}x\n\n"
                        f"PDH: {prev_day_high}\n"
                        f"PDL: {prev_day_low}"
                    )

                    alerts.append(separator)


                elif long_squeeze:

                    alerts.append(
                        f"💥 Watch ${symbol}\n"
                        f"Long Squeeze Detected\n\n"
                        f"Strong Bearish Expansion\n"
                        f"Long Positions Under Pressure\n\n"
                        f"Price: {current_price}\n"
                        f"Funding Rate: {funding_text} ({funding_trend})\n"
                        f"Volume Spike: {volume_ratio:.2f}x\n\n"
                        f"PDH: {prev_day_high}\n"
                        f"PDL: {prev_day_low}"
                    )

                    alerts.append(separator)

                # ===============================
                # SIGNAL PRIORITY + EMOJI STACK
                # ===============================

                signal_score = 0

                if high_prob_continuation and not too_far_from_breakout:
                    signal_score += 3

                if high_prob_bullish_reversal or high_prob_bearish_reversal:
                    signal_score += 3

                if bullish_break_pressure or bearish_break_pressure:
                    signal_score += 2

                if short_squeeze or long_squeeze:
                    signal_score += 2

                if pre_explosion:
                    signal_score += 1


                if signal_score >= 4:
                    stars = "⭐⭐⭐"
                elif signal_score >= 2:
                    stars = "⭐⭐"
                else:
                    stars = "⭐"

                # Ignore strong signals if too far from liquidity
                if stars in ["⭐⭐", "⭐⭐⭐"] and far_from_liquidity:
                    continue


                

                # ===============================
                # SIGNAL PRIORITY SYSTEM
                # ===============================
                emoji_stack = ""
                # 1️⃣ REVERSALS (highest priority)
                if high_prob_bullish_reversal:
                    emoji_stack = "🔄⬆️"

                elif high_prob_bearish_reversal:
                    emoji_stack = "🔄⬇️"

                # 2️⃣ CONTINUATION
                elif high_prob_continuation and not too_far_from_breakout:
                    emoji_stack = "🧨🚀"

                # 3️⃣ BREAK PRESSURE
                elif bullish_break_pressure:
                    emoji_stack = "🧨⬆️"

                elif bearish_break_pressure:
                    emoji_stack = "🧨⬇️"

                # 4️⃣ LIQUIDATION CASCADE
                elif short_squeeze:
                    emoji_stack = "💥⬆️"

                elif long_squeeze:
                    emoji_stack = "💥⬇️"

                # 5️⃣ PRE-EXPLOSION
                elif pre_explosion:
                    emoji_stack = "⚡"

                # fallback
                else:
                    emoji_stack = "📊"

                
                # ===============================
                # HIGH PROBABILITY ALERTS
                # ===============================

                if high_prob_bullish_reversal or high_prob_bearish_reversal or (high_prob_continuation and not too_far_from_breakout):

                    # Compose message with $ symbol
                    formatted_symbol = f"${symbol}"

                    if high_prob_bullish_reversal:
                        trap_tag = "🐻 SHORT TRAP" if bullish_trap else ""
                        alert_text = (
                            f"{stars} {emoji_stack} {formatted_symbol}\n"
                            f"Potential Bullish Reversal {trap_tag}\n"
                            f"Sweep Strength: {sweep_strength}/10\n"
                            f"Funding Rate: {funding_text} ({funding_trend})\n\n"
                            f"Liquidity Grab Below PDL\n"
                            f"PDH: {prev_day_high}\n"
                            f"PDL: {prev_day_low}\n\n"
                            f"{approaching}\n"
                            f"{liquidity_bias}\n"
                            f"Volume Expansion: {volume_ratio:.2f}x\n"
                            f"Volatility Expansion: {volatility_ratio:.2f}x\n\n"
                        )

                    elif high_prob_bearish_reversal:
                        trap_tag = "🐂 LONG TRAP" if bearish_trap else ""
                        alert_text = (
                            f"{stars} {emoji_stack} {formatted_symbol}\n"
                            f"Potential Bearish Reversal {trap_tag}\n"
                            f"Sweep Strength: {sweep_strength}/10\n"
                            f"Funding Rate: {funding_text} ({funding_trend})\n\n"
                            f"Liquidity Grab Above PDH\n"
                            f"PDH: {prev_day_high}\n"
                            f"PDL: {prev_day_low}\n\n"
                            f"{approaching}\n"
                            f"{liquidity_bias}\n"
                            f"Volume Expansion: {volume_ratio:.2f}x\n"
                            f"Volatility Expansion: {volatility_ratio:.2f}x\n\n"
                        )

                    elif high_prob_continuation and not too_far_from_breakout:
                        direction = "Bullish Continuation" if structure == "Bullish" else "Bearish Continuation"
                        alert_text = (
                            f"{stars} {emoji_stack} {formatted_symbol}\n"
                            f"High Probability {direction}\n\n"
                            f"Price: {current_price}\n"
                            f"Funding Rate: {funding_text} ({funding_trend})\n\n"
                            f"Context:\n"
                            f"• Structure: {structure}\n"
                            f"• Impulse: {impulse_strength}\n"
                            f"• Volume: {volume_state}\n"
                            f"• Volatility: {volatility_state}\n\n"
                            f"Model Action: {model_action}\n"
                            f"Instruction: {model_instruction}\n\n"
                            f"PDH: {prev_day_high}\n"
                            f"PDL: {prev_day_low}\n\n"
                            f"{liquidity_bias}\n"
                        )

                    # append to alerts for Telegram
                    alerts.append(alert_text)
                    alerts.append(separator)

                    # also send to Binance Square
                    send_binance_square(alert_text)
                    continue
                # ===============================
                # PRE-EXPLOSION ALERT
                # ===============================

                if pre_explosion:

                    alerts.append(
                        f"{stars} {emoji_stack} ${symbol}\n"
                        f"Market Compression Detected\n"
                        f"Possible Explosive Move Incoming\n\n"
                        f"Price: {current_price}\n"
                        f"Funding Rate: {funding_text} ({funding_trend})\n\n"
                        f"Context:\n"
                        f"• Structure: {structure}\n"
                        f"• Impulse: {impulse_strength}\n"
                        f"• Volume: {volume_state}\n"
                        f"• Volatility: {volatility_state}\n\n"
                        f"Model Action: {model_action}\n"
                        f"Instruction: {model_instruction}\n\n"
                        f"PDH: {prev_day_high}\n"
                        f"PDL: {prev_day_low}\n\n"
                        f"{liquidity_bias}\n"
                    )

                    alerts.append(separator)
                    continue
                # ===============================
                # BREAKOUT PRESSURE ALERT
                # ===============================

                if bullish_break_pressure or bearish_break_pressure:

                    direction = "Bullish Breakout Pressure" if bullish_break_pressure else "Bearish Breakout Pressure"

                    alerts.append(
                        f"{stars} {emoji_stack} ${symbol}\n"
                        f"{direction}\n\n"
                        f"Price: {current_price}\n"
                        f"Funding Rate: {funding_text} ({funding_trend})\n\n"
                        f"Context:\n"
                        f"• Structure: {structure}\n"
                        f"• Behavior: {behavior}\n"
                        f"• Volume: {volume_state}\n"
                        f"• Volatility: {volatility_state}\n\n"
                        f"PDH: {prev_day_high}\n"
                        f"PDL: {prev_day_low}\n\n"
                        f"{liquidity_bias}\n"
                    )

                    alerts.append(separator)
                    continue
                

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

            print("No high probability setups detected.")

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

while True:

    wait_until_next_5min()

    print("\n🔄 Running synchronized scan...\n")
    print(f"\nSCAN TIME UTC: {datetime.utcnow().strftime('%H:%M:%S')}")

    run_scan()