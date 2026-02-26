import asyncio
import sys
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import pandas as pd
import ccxt
import ccxt.pro as ccxt_pro
import time
import schedule
from datetime import datetime
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

# ============================================================
# BTC REGIME FILTER
# ============================================================

async def get_btc_regime_score(exchange):
    """
    Returns:
        +1  → Stable BTC (good alt environment)
         0  → Neutral
        -1  → High volatility BTC (riskier environment)
    """

    try:
        ohlcv = await exchange.fetch_ohlcv('BTC/USDT:USDT', '2h', limit=20)
        df = pd.DataFrame(ohlcv, columns=['ts','o','h','l','c','v'])

        recent_range = df['h'].iloc[-1] - df['l'].iloc[-1]
        avg_range = (df['h'] - df['l']).mean()

        volatility_ratio = recent_range / avg_range if avg_range > 0 else 1

        # Calm regime
        if volatility_ratio < 1.5:
            return 1

        # Extreme regime
        elif volatility_ratio > 3:
            return -1

        # Normal regime
        else:
            return 0

    except:
        return 0

# ============================================================
# HTF BIAS (8H STRUCTURE)
# ============================================================

async def get_htf_bias(exchange, symbol):
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, '8h', limit=20)
        df = pd.DataFrame(ohlcv, columns=['ts','o','h','l','c','v'])
        recent_high = df['h'].iloc[-6:-1].max()
        recent_low = df['l'].iloc[-6:-1].min()
        last_close = df['c'].iloc[-1]

        if last_close > recent_high:
            return "bullish"
        elif last_close < recent_low:
            return "bearish"
        return None
    except:
        return None

# ============================================================
# WHALE FILTER
# ============================================================

async def get_whale_signals(exchange, symbol):
    try:
        exchange = ccxt.binance({'options': {'defaultType': 'future'}})
        ob = exchange.fetch_order_book(symbol, limit=10)
        bids = ob['bids'][:5]
        asks = ob['asks'][:5]

        bid_vol = sum(b[1] for b in bids)
        ask_vol = sum(a[1] for a in asks)

        imbalance = bid_vol / ask_vol if ask_vol > 0 else 1
        whale_bid = imbalance >= 3
        whale_ask = imbalance <= 0.33

        ticker = exchange.fetch_ticker(symbol)
        last = ticker['last']
        best_bid = ticker['bid']
        best_ask = ticker['ask']

        sweep_buy = last > best_ask
        sweep_sell = last < best_bid

        flags = [whale_bid, whale_ask, sweep_buy, sweep_sell]
        score = sum(flags)

        return score
    except:
        return 0

# ============================================================
# PROBABILITY SCORING MODEL (0–10)
# ============================================================

def calculate_probability_score(row, whale_score, htf_bias, btc_score, oi_score):

    score = 0

    # Strong volume expansion
    if row['Volume Ratio'] >= 3:
        score += 3
    elif row['Volume Ratio'] >= 2:
        score += 2
    elif row['Volume Ratio'] >= 1.5:
        score += 1

    # Liquidity + Structure
    if row['Structure Break']:
        score += 2

    if row.get('Daily Liquidity Model', False):
        score += 2

    # OI strength
    if oi_score == 1:
        score += 2
    elif oi_score == 0:
        score += 1

    # BTC regime
    score += btc_score

    return score
# ============================================================
# 2H ANALYSIS
# ============================================================

async def analyze_symbol(exchange, symbol, daily_data):

    try:
        # === 15M DATA ===
        ohlcv = await exchange.fetch_ohlcv(symbol, '15m', limit=120)
        df = pd.DataFrame(ohlcv, columns=['ts','o','h','l','c','v'])

        if len(df) < 50:
            return []

        signal = df.iloc[-1]
        sweep_candle = df.iloc[-2]
        prev_section = df.iloc[-22:-2]

        recent_high = prev_section['h'].max()
        recent_low = prev_section['l'].min()

        direction = None
        structure_break = False
        liquidity_model = False

        if not daily_data:
            return []

        prev_day_high = daily_data['high']
        prev_day_low = daily_data['low']

        # ==========================
        # BUY SIDE MODEL
        # ==========================
        sweep_up = (
            sweep_candle['h'] > prev_day_high and
            sweep_candle['c'] < prev_day_high
        )

        displacement_up = (
            signal['c'] > sweep_candle['h'] and
            (signal['h'] - signal['l']) >
            prev_section['h'].sub(prev_section['l']).mean() * 1.5
        )

        continuation_up = signal['c'] > recent_high

        if sweep_up and displacement_up and continuation_up:
            direction = "buy"
            structure_break = True
            liquidity_model = True

        # ==========================
        # SELL SIDE MODEL
        # ==========================
        sweep_down = (
            sweep_candle['l'] < prev_day_low and
            sweep_candle['c'] > prev_day_low
        )

        displacement_down = (
            signal['c'] < sweep_candle['l'] and
            (signal['h'] - signal['l']) >
            prev_section['h'].sub(prev_section['l']).mean() * 1.5
        )

        continuation_down = signal['c'] < recent_low

        if sweep_down and displacement_down and continuation_down:
            direction = "sell"
            structure_break = True
            liquidity_model = True

        if not liquidity_model:
            return []

        # ==========================
        # VOLUME CONFIRMATION
        # ==========================
        avg_vol = prev_section['v'].mean()
        volume_ratio = signal['v'] / avg_vol if avg_vol > 0 else 0

        if volume_ratio < 1.5:
            return []

       

        # ==========================
        # 1M DISPLACEMENT CHECK
        # ==========================
        one_min = await exchange.fetch_ohlcv(symbol, '1m', limit=10)
        last = one_min[-1]
        avg_range = sum([c[2] - c[3] for c in one_min[:-1]]) / 9
        displacement_1m = (last[2] - last[3]) > avg_range * 1.2

        if not displacement_1m:
            return []

        return [{
            "Symbol": symbol,
            "Direction": direction,
            "Volume Ratio": volume_ratio,
            "Structure Break": structure_break,
            "Daily Liquidity Model": liquidity_model
        }]

    except Exception as e:
        print(f"{symbol} error: {e}")
        return []
# ============================================================
# RETRACEMENT ENTRY MODEL
# ============================================================

def build_trade_plan_from_1m(last_candle, direction):

    high = last_candle[2]
    low = last_candle[3]
    range_size = high - low

    if direction == "buy":
        entry = high - range_size * 0.5
        sl = low
        risk = entry - sl
        tp1 = entry + risk * 2
        tp2 = entry + risk * 3
    else:
        entry = low + range_size * 0.5
        sl = high
        risk = sl - entry
        tp1 = entry - risk * 2
        tp2 = entry - risk * 3

    return entry, sl, tp1, tp2

async def get_open_interest_score(exchange, symbol):

    try:
        # Binance Futures OI history (5m resolution)
        oi_history = await exchange.fapiPublic_get_openinteresthist({
            "symbol": symbol.replace("/", "").replace(":USDT", ""),
            "period": "5m",
            "limit": 24  # last 2 hours (24 x 5m)
        })

        if len(oi_history) < 12:
            return 0

        df = pd.DataFrame(oi_history)
        df['sumOpenInterest'] = df['sumOpenInterest'].astype(float)

        # Compare first half vs second half of 2H window
        first_half = df['sumOpenInterest'].iloc[:12].mean()
        second_half = df['sumOpenInterest'].iloc[12:].mean()

        change_pct = (second_half - first_half) / first_half

        if change_pct > 0.01:
            return 1   # strong build-up
        elif change_pct < -0.01:
            return -1  # position closing
        else:
            return 0

    except:
        return 0
    

async def preload_daily_levels(exchange, symbols):
    """
    Preloads Previous Day High/Low for all symbols once.
    Returns dictionary:
    {
        "BTC/USDT:USDT": {"high": xxx, "low": xxx},
        ...
    }
    """

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

        print("🔄 Starting new scan cycle...")
        try:
            send_telegram_message("🔄 Starting new scan cycle...")
        except:
            print("Telegram send failed.")

        # ===============================
        # BTC REGIME
        # ===============================
        btc_score = await get_btc_regime_score(exchange)
        print(f"BTC Regime Score: {btc_score}")

        # ===============================
        # TOP 50 ULTRA-LIQUID FUTURES
        # ===============================
        print("Fetching tickers for volume ranking...")
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
        print(f"Loaded daily levels for {len(daily_levels)} symbols")

        # ===============================
        # THROTTLED SCANNING
        # ===============================
        semaphore = asyncio.Semaphore(3)

        async def limited_task(symbol):
            async with semaphore:
                return await analyze_symbol(exchange, symbol, daily_levels.get(symbol))

        tasks = [limited_task(s) for s in symbols]
        results = await asyncio.gather(*tasks)

        qualified_setups = []

        # ===============================
        # PROCESS RESULTS
        # ===============================
        for result in results:
            if not result:
                continue

            for row in result:

                # OI confirmation (already strict inside analyze_symbol but re-check safe)
                oi_score = await get_open_interest_score(exchange, row['Symbol'])
                if oi_score != 1:
                    continue

                # Score system
                probability = calculate_probability_score(
                    row,
                    whale_score=0,          # Whale removed for rate safety
                    htf_bias=None,          # 8H removed
                    btc_score=btc_score,
                    oi_score=oi_score
                )

                if probability < 5:
                    continue

                # ===============================
                # 1M EXECUTION
                # ===============================
                one_min = await exchange.fetch_ohlcv(row['Symbol'], '1m', limit=2)
                last_candle = one_min[-1]

                high = last_candle[2]
                low = last_candle[3]
                range_size = high - low

                direction = row['Direction']

                if direction == "buy":
                    entry = high - range_size * 0.5
                    sl = low
                    risk = entry - sl
                    tp1 = entry + risk * 2
                    tp2 = entry + risk * 3
                else:
                    entry = low + range_size * 0.5
                    sl = high
                    risk = sl - entry
                    tp1 = entry - risk * 2
                    tp2 = entry - risk * 3

                qualified_setups.append({
                    "symbol": row['Symbol'],
                    "score": probability,
                    "direction": direction,
                    "entry": entry,
                    "sl": sl,
                    "tp1": tp1,
                    "tp2": tp2
                })

        # ===============================
        # RANKING (TOP 5 ONLY)
        # ===============================
        if not qualified_setups:
            print("❌ No institutional continuation setups this cycle.")
            try:
                send_telegram_message("❌ No institutional continuation setups this cycle.")
            except:
                print("Telegram send failed.")
            return

        top5 = sorted(
            qualified_setups,
            key=lambda x: x['score'],
            reverse=True
        )[:5]

        telegram_msg = "🔥 <b>TOP 5 CONTINUATION SETUPS</b>\n\n"

        print("\n===== TOP 5 CONTINUATION SETUPS =====")

        for setup in top5:

            if setup['score'] >= 8:
                grade = "A+"
            elif setup['score'] >= 7:
                grade = "A"
            elif setup['score'] >= 6:
                grade = "B"
            else:
                grade = "C"

            line = (
                f"{setup['symbol']} | Score: {setup['score']} | Grade: {grade}\n"
                f"Direction: {setup['direction'].upper()}\n"
                f"Entry: {setup['entry']:.6f}\n"
                f"SL: {setup['sl']:.6f}\n"
                f"TP1: {setup['tp1']:.6f}\n"
                f"TP2: {setup['tp2']:.6f}\n\n"
            )

            print(line)
            telegram_msg += line

        try:
            send_telegram_message(telegram_msg)
        except:
            print("Telegram send failed.")

        print("=====================================\n")

    except Exception as e:
        print(f"Scan error: {e}")

    finally:
        await exchange.close()
        print("Exchange session closed cleanly.")
# ============================================================
# SCHEDULER
# ============================================================

def run_scan():
    print(f"\n[{datetime.now(KSA_TIMEZONE)}] Running scan...")
    asyncio.run(scan_all())

if __name__ == "__main__":
    print("=== Institutional Scanner Started ===")

    while True:
        print("\nStarting new scan cycle...")
        run_scan()
        print("Scan cycle complete. Waiting 5 minutes...\n")
        time.sleep(300)  # 5 minutes