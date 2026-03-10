import asyncio
import sys
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import pandas as pd
import ccxt
import time
from datetime import datetime, timedelta
import pytz
import requests
import os
from database import log_liquidity_context
from dotenv import load_dotenv
load_dotenv()

# ================= TELEGRAM =================
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_SECRET = os.getenv("BINANCE_SECRET")

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

def format_square_symbol(symbol):

    base = symbol.split('/')[0]   # BTC/USDT:USDT → BTC
    return f"${base}"

# ================= CONFIG =================
DISTANCE_THRESHOLD = 0.002
TRADE_RISK = 0.05
ACTIVE_TRADES = set()
BREAKEVEN_MOVED = set()
KSA_TIMEZONE = pytz.timezone('Asia/Riyadh')

# ============================================================
# BINANCE TRADING CLIENT
# ============================================================

trade_exchange = ccxt.binance({
    "apiKey": BINANCE_API_KEY,
    "secret": BINANCE_SECRET,
    "options": {"defaultType": "future"}
})

# ============================================================
# HELPER FUNCTIONS
# ============================================================

def get_position(symbol):

    try:
        positions = trade_exchange.fetch_positions([symbol])

        for p in positions:

            contracts = float(p.get("contracts", 0))

            if contracts != 0:
                return p

        return None

    except Exception as e:
        print("Position check failed:", e)
        return None

async def execute_trade(symbol, side, price):

    try:

        # 1️⃣ Prevent duplicate trades in this bot session
        if symbol in ACTIVE_TRADES:
            print(f"Skipping {symbol} — already traded in this session")
            return

        # 2️⃣ Check Binance if position already exists
        existing = get_position(symbol)

        if existing:
            print(f"Skipping {symbol} — position already open")
            return

        balance = trade_exchange.fetch_balance()
        usdt_balance = balance['USDT']['free']

        trade_size = usdt_balance * TRADE_RISK

        
        # ===============================
        # DETECT LEVERAGE FROM BINANCE
        # ===============================

        # Read leverage only (do NOT modify Binance setting)
        leverage = 1

        try:
            positions = trade_exchange.fetch_positions([symbol])
            if positions:
                leverage = float(positions[0].get("leverage", 1))
        except:
            pass
            

        # ===============================
        # TRUE 5% RISK CALCULATION
        # ===============================

        risk_capital = usdt_balance * TRADE_RISK

        # stop distance (same as your SL logic)
        if side == "long":
            stop_price = price * 0.985
        else:
            stop_price = price * 1.015

        stop_distance = abs(price - stop_price)

        # position value required to risk exactly 5%
        position_value = (risk_capital * price) / stop_distance


        # Apply precision
        market = trade_exchange.market(symbol)
        # convert to contracts
        amount = position_value / price
        amount = adjust_amount(symbol, amount, price)

        print(f"{symbol} | Balance: {usdt_balance:.2f} | Risk: {risk_capital:.2f} | Leverage: {leverage}x | Amount: {amount}")

        # 3️⃣ Prevent invalid order size
        if amount <= 0:
            print("Trade amount invalid.")
            return

        if side == "long":

            order = trade_exchange.create_market_buy_order(symbol, amount)

        else:

            order = trade_exchange.create_market_sell_order(symbol, amount)

        entry = order['average'] if order['average'] else price

        if side == "long":

            sl = entry * 0.985
            tp = entry * 1.03
            close_side = "sell"

        else:

            sl = entry * 1.015
            tp = entry * 0.97
            close_side = "buy"

        trade_exchange.create_order(
            symbol,
            "STOP_MARKET",
            close_side,
            amount,
            None,
            {"stopPrice": sl}
        )

        trade_exchange.create_order(
            symbol,
            "TAKE_PROFIT_MARKET",
            close_side,
            amount,
            None,
            {"stopPrice": tp}
        )

        trade_info = (
            f"\n\n🚨 TRADE EXECUTED\n"
            f"Side: {side.upper()}\n"
            f"Entry: {entry}\n"
            f"TP: {tp}\n"
            f"SL: {sl}\n"
        )

        ACTIVE_TRADES.add(symbol)
                
        return trade_info

    except Exception as e:
        print("Trade execution failed:", e)
        return ""

 #########################################       
def adjust_amount(symbol, amount, price):

    market = trade_exchange.market(symbol)

    min_qty = market['limits']['amount']['min']
    min_notional = market['limits']['cost']['min']

    # Apply Binance precision
    amount = float(trade_exchange.amount_to_precision(symbol, amount))

    # Ensure minimum quantity
    if min_qty and amount < min_qty:
        amount = min_qty

    # Ensure minimum notional
    if min_notional and amount * price < min_notional:
        amount = min_notional / price
        amount = float(trade_exchange.amount_to_precision(symbol, amount))

    return amount
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
            daily = exchange.fetch_ohlcv(symbol, timeframe='1d', limit=2)
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

    try:
        balance = trade_exchange.fetch_balance()
        usdt_balance = balance['USDT']['total']
    except Exception as e:
        usdt_balance = "Error fetching balance"

    print(f"Next scan aligned in {int(wait_seconds)} seconds | Futures Balance: {usdt_balance} USDT")

    time.sleep(wait_seconds)



def calculate_roi(entry_price, current_price, side, leverage):

    if side == "long":
        pnl_pct = (current_price - entry_price) / entry_price
    else:
        pnl_pct = (entry_price - current_price) / entry_price

    roi = pnl_pct * leverage * 100
    return roi

def manage_breakeven(symbol):

    try:

        pos = get_position(symbol)

        if not pos:
            return

        contracts = float(pos["contracts"])

        if contracts == 0:
            return

        entry_price = pos.get("entryPrice")
        side = pos.get("side")
        leverage = pos.get("leverage")

        if not entry_price or not leverage:
            return

        entry_price = float(entry_price)
        leverage = float(leverage)

        ticker = trade_exchange.fetch_ticker(symbol)
        current_price = ticker["last"]

        roi = calculate_roi(entry_price, current_price, side, leverage)

        if roi >= 100 and symbol not in BREAKEVEN_MOVED:

            print(f"{symbol} hit 100% ROI → moving SL to breakeven")

            # Cancel old stop orders
            orders = trade_exchange.fetch_open_orders(symbol)

            for o in orders:
                if o["type"] == "stop_market":
                    trade_exchange.cancel_order(o["id"], symbol)

            params = {
                "stopPrice": entry_price,
                "closePosition": True
            }

            if side == "long":

                trade_exchange.create_order(
                    symbol,
                    "STOP_MARKET",
                    "sell",
                    None,
                    None,
                    params
                )

            else:

                trade_exchange.create_order(
                    symbol,
                    "STOP_MARKET",
                    "buy",
                    None,
                    None,
                    params
                )

            BREAKEVEN_MOVED.add(symbol)

    except Exception as e:
        print("Breakeven manager error:", e)

MAX_CONTINUATION_DISTANCE = 0.003   # 0.3% from PDH/PDL
BREAK_VALID_CANDLES = 3             # breakout must be recent


# ============================================================
# MAIN SCAN
# ============================================================

async def scan_all():

    exchange = ccxt.binance({'options': {'defaultType': 'future'}})

    try:

        exchange.load_markets()
        print("🔄 Starting Liquidity Radar Scan.")

        separator = "\n━━━━━━━━━━━━━━━━━━━━\n"

        donation_message = (
            "\n💙If this tool helps your trading,\n"
            "you can support development:\n\n"
            "USDT BSC BEP20\n"
            "0x7070f252c95df9a42a9c4df536b4166927a5e670\n"
        )

        tickers = exchange.fetch_tickers()

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

            trade_info = ""

            if symbol not in daily_levels:
                continue

            try:

                # ===============================
                # FETCH TICKER & FUNDING
                # ===============================
                ticker = exchange.fetch_ticker(symbol)
                current_price = ticker['last']

                funding_rate = None
                try:
                    funding = exchange.fetch_funding_rate(symbol)
                    funding_rate = funding['fundingRate']
                except:
                    funding_rate = None

                funding_text = f"{funding_rate * 100:.4f}%" if funding_rate else "N/A"

                prev_day_high = daily_levels[symbol]['high']
                prev_day_low = daily_levels[symbol]['low']

                distance_high = abs(current_price - prev_day_high) / prev_day_high
                distance_low = abs(current_price - prev_day_low) / prev_day_low

                ohlcv = exchange.fetch_ohlcv(symbol, '15m', limit=21)
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
                # CONTEXT CLASSIFICATION
                # ===============================
                structure = "Bullish" if df['c'].iloc[-1] > df['c'].iloc[-5] else "Bearish"

                impulse_strength = (
                    "Strong Expansion" if volatility_ratio > 1.3
                    else "Moderate" if volatility_ratio > 0.9
                    else "Weak"
                )

                behavior = (
                    "Compression" if volatility_ratio < 0.9
                    else "Expansion" if volatility_ratio > 1.1
                    else "Normal"
                )

                volume_state = (
                    "Increasing" if volume_ratio > 1.0
                    else "Decreasing" if volume_ratio < 0.8
                    else "Stable"
                )

                volatility_state = (
                    "Expanding" if volatility_ratio > 1.1
                    else "Contracting" if volatility_ratio < 0.9
                    else "Stable"
                )

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
                # LIQUIDITY SWEEP DETECTION
                # ===============================
                pdl_sweep = prev['l'] < prev_day_low and prev['c'] > prev_day_low
                pdh_sweep = prev['h'] > prev_day_high and prev['c'] < prev_day_high

                # ===============================
                # SWEEP STRENGTH
                # ===============================
                wick_size = abs(prev['h'] - prev['l'])
                body_size = abs(prev['c'] - prev['o'])
                wick_ratio = wick_size / body_size if body_size > 0 else 0

                score = 0
                if wick_ratio > 2: score += 3
                if volume_ratio > 1.3: score += 3
                if volatility_ratio > 1.2: score += 2
                if pdl_sweep or pdh_sweep: score += 2

                sweep_strength = min(score, 10)
                # ===============================
                # BREAKOUT STRENGTH MODEL
                # ===============================

                break_score = 0

                # volume momentum
                if volume_ratio > 1.4:
                    break_score += 3

                # volatility expansion
                if volatility_ratio > 1.3:
                    break_score += 3

                # strong close beyond liquidity
                if prev['c'] > prev_day_high * 1.002 or prev['c'] < prev_day_low * 0.998:
                    break_score += 2

                # follow through momentum
                if abs(last['c'] - prev['c']) > avg_range * 0.6:
                    break_score += 2

                break_strength = min(break_score, 10)

                bullish_breakout = prev['c'] > prev_day_high * 1.001
                bearish_breakout = prev['c'] < prev_day_low * 0.999

                # ===============================
                # REVERSAL SIGNAL
                # ===============================
                square_symbol = format_square_symbol(symbol)

                if pdl_sweep and sweep_strength >= 5:

                    if sweep_strength >= 7:
                        trade_info = await execute_trade(symbol, "long", current_price)
                    else:
                        trade_info = ""

                    alerts.append(
                        f"🔥 {square_symbol}\n"
                        f"Price: {current_price}\n"
                        f"PDH: {prev_day_high}\n"
                        f"PDL: {prev_day_low}\n\n"
                        f"Potential Bullish Reversal\n"
                        f"Sweep Strength: {sweep_strength}/10\n"
                        f"Funding Rate: {funding_text}\n"
                        f"{trade_info}\n"
                        f"{liquidity_bias}\n"
                    )
                    alerts.append(separator)
                    continue

                elif pdh_sweep and sweep_strength >= 5:

                    if sweep_strength >= 7:
                        trade_info = await execute_trade(symbol, "short", current_price)

                    alerts.append(
                        f"🔥 {square_symbol}\n"
                        f"Price: {current_price}\n"
                        f"PDH: {prev_day_high}\n"
                        f"PDL: {prev_day_low}\n\n"
                        f"Potential Bearish Reversal\n"
                        f"Sweep Strength: {sweep_strength}/10\n"
                        f"Funding Rate: {funding_text}\n"
                        f"{trade_info}\n"
                        f"{liquidity_bias}\n"
                    )
                    alerts.append(separator)
                    continue

                # ===============================
                # CONTINUATION DETECTION (UPGRADED)
                # ===============================

                prev_close = prev['c']

                bullish_break = prev_close > prev_day_high * 1.001
                bearish_break = prev_close < prev_day_low * 0.999

                # Distance protection
                distance_from_pdh = abs(current_price - prev_day_high) / prev_day_high
                distance_from_pdl = abs(current_price - prev_day_low) / prev_day_low

                near_break_long = distance_from_pdh <= MAX_CONTINUATION_DISTANCE
                near_break_short = distance_from_pdl <= MAX_CONTINUATION_DISTANCE


                # Pullback detection (price revisits level)
                pullback_long = (
                    last['l'] <= prev_day_high * 1.002 and
                    last['c'] > prev_day_high
                )

                pullback_short = (
                    last['h'] >= prev_day_low * 0.998 and
                    last['c'] < prev_day_low
                )


                # Momentum confirmation
                momentum_confirm = (
                    volume_ratio > 1.2
                    and volatility_ratio > 1.1
                )


                # Break freshness check (avoid late chasing)
                recent_break = (
                    bullish_break or bearish_break
                )


                # ===============================
                # CONTINUATION TRADE EXECUTION
                # ===============================

                if (
                    bullish_break
                    and near_break_long
                    and pullback_long
                    and momentum_confirm
                    and break_strength >= 6
                ):

                    trade_info = await execute_trade(symbol, "long", current_price)

                    alerts.append(
                        f"🚀 {square_symbol}\n"
                        f"Price: {current_price}\n"
                        f"PDH: {prev_day_high}\n"
                        f"PDL: {prev_day_low}\n\n"
                        f"Bullish Break → Pullback → Reclaim\n"
                        f"Break Strength: {break_strength}/10\n"
                        f"Distance From PDH: {distance_from_pdh*100:.2f}%\n"
                        f"Funding Rate: {funding_text}\n"
                        f"{trade_info}\n"
                        f"{liquidity_bias}\n"
                    )

                    alerts.append(separator)
                    continue


                elif (
                    bearish_break
                    and near_break_short
                    and pullback_short
                    and momentum_confirm
                    and break_strength >= 6
                ):

                    trade_info = await execute_trade(symbol, "short", current_price)

                    alerts.append(
                        f"🚀 {square_symbol}\n"
                        f"Price: {current_price}\n"
                        f"PDH: {prev_day_high}\n"
                        f"PDL: {prev_day_low}\n\n"
                        f"Bearish Break → Pullback → Reclaim\n"
                        f"Break Strength: {break_strength}/10\n"
                        f"Distance From PDL: {distance_from_pdl*100:.2f}%\n"
                        f"Funding Rate: {funding_text}\n"
                        f"{trade_info}\n"
                        f"{liquidity_bias}\n"
                    )

                    alerts.append(separator)
                    continue
                

                # ===============================
                # RADAR ALERTS
                # ===============================
                if distance_high <= DISTANCE_THRESHOLD:
                    model_bias, action = get_model_action(
                        structure, impulse_strength, behavior, volume_state, volatility_state
                    )

                    alerts.append(
                        f"🚀 {square_symbol}\n"
                        f"Price: {current_price}\n"
                        f"PDH: {prev_day_high}\n"
                        f"PDL: {prev_day_low}\n\n"
                        f"Approaching PDH ({distance_high*100:.2f}%)\n\n"
                        f"Context:\n"
                        f"• 15m Structure: {structure}\n"
                        f"• Impulse: {impulse_strength}\n"
                        f"• Behavior: {behavior}\n"
                        f"• Volume: {volume_state}\n"
                        f"• Volatility: {volatility_state}\n"
                        f"Model Bias:\n→ {model_bias}\n"
                        f"Action:\n{action}\n"
                        f"{liquidity_bias}\n"
                    )
                    alerts.append(separator)

                elif distance_low <= DISTANCE_THRESHOLD:
                    model_bias, action = get_model_action(
                        structure, impulse_strength, behavior, volume_state, volatility_state
                    )

                    alerts.append(
                        f"🚀{square_symbol}\n"
                        f"Price: {current_price}\n"
                        f"PDH: {prev_day_high}\n"
                        f"PDL: {prev_day_low}\n\n"
                        f"Approaching PDL ({distance_low*100:.2f}%)\n\n"
                        f"Context:\n"
                        f"• 15m Structure: {structure}\n"
                        f"• Impulse: {impulse_strength}\n"
                        f"• Behavior: {behavior}\n"
                        f"• Volume: {volume_state}\n"
                        f"• Volatility: {volatility_state}\n"
                        f"Model Bias:\n→ {model_bias}\n"
                        f"Action:\n{action}\n"
                        f"{liquidity_bias}\n"
                    )
                    alerts.append(separator)

            except Exception as e:
                print(symbol, "scan error:", e)
                continue

        # TELEGRAM SEND
        if alerts:
            countdown = get_daily_countdown()
            message = f"⚠️ <b>RADAR</b>\n\nDaily Candle Close In: {countdown}\n\n"

            for alert in alerts:
                message += alert + "\n"

            message += separator + donation_message

            send_telegram_message(message)
            print("Liquidity alerts sent.")

        else:
            print("No liquidity proximity detected.")

    except Exception as e:
        print(f"Scan error: {e}")

    finally:
        print("Exchange session closed cleanly.")
# ============================================================
# LOOP
# ============================================================

def run_scan():
    asyncio.run(scan_all())

if __name__ == "__main__":

    print("=== Liquidity Radar Started ===")
    

    while True:
        
        for symbol in ACTIVE_TRADES:
            manage_breakeven(symbol)

        wait_until_next_5min()

        print("\n🔄 Running synchronized scan...\n")
        print(f"\nSCAN TIME UTC: {datetime.utcnow().strftime('%H:%M:%S')}")

        run_scan()