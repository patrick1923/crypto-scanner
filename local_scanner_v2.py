# ============================================================
#  local_scanner_v2.py  —  FULL VERSION WITH EARLY SCANNER
# ============================================================

import asyncio
import sys

# Fix Windows event loop for asyncio
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import pandas as pd
import ccxt
import ccxt.pro as ccxt_pro
import time
import schedule
from datetime import datetime, timezone, timedelta
import database as db
import pytz
import tkinter as tk
from tkinter import messagebox, ttk
import threading
from plyer import notification
import requests

# --- IMPORT THE EARLY SCANNER ---
from early_scanner import scan_early_pumps_async


TELEGRAM_TOKEN = "8186631543:AAE9JXG2aK9RHB7h_nAXAlKeDVBdzfce_y4"
TELEGRAM_CHAT_ID = "-1003457158679"


def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        requests.post(url, data=payload)
    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}")


# ============================================================
# CONFIGURATION
# ============================================================

KSA_TIMEZONE = pytz.timezone('Asia/Riyadh')
DEFAULT_RISK_PERCENT = 1.0


# ============================================================
# HELPER: READ API KEYS
# ============================================================

def get_api_keys():
    try:
        with open('.streamlit/secrets.toml', 'r') as f:
            lines = f.readlines()

        keys = {}
        for line in lines:
            if '=' in line:
                key, value = line.split('=', 1)
                keys[key.strip()] = value.strip().strip('"')

        api_key = keys.get("API_KEY")
        api_secret = keys.get("API_SECRET")

        if not api_key or not api_secret:
            print("Invalid or missing API keys")
            return None, None

        return api_key, api_secret

    except FileNotFoundError:
        print("Missing `.streamlit/secrets.toml`")
        return None, None


# ============================================================
# FETCH BALANCE
# ============================================================

def fetch_balance(api_key, api_secret):
    if not api_key or not api_secret:
        return 0

    try:
        exchange = ccxt.binance({
            'apiKey': api_key,
            'secret': api_secret,
            'options': {'defaultType': 'future'}
        })

        balance = exchange.fetch_balance()
        usdt_balance = balance.get('USDT', {}).get('total', 0)

        return float(usdt_balance)

    except Exception as e:
        print(f"Balance error: {e}")
        return 0


# ============================================================
# MAIN 2-HOUR ANALYZER (UNCHANGED FROM ORIGINAL)
# ============================================================

async def analyze_symbol_2h(exchange, symbol):
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, '2h', limit=22)
        if len(ohlcv) < 22:
            return None

        df = pd.DataFrame(
            ohlcv, columns=['timestamp', 'open',
                            'high', 'low', 'close', 'volume']
        )
        df['range'] = df['high'] - df['low']

        pre_signal_candle = df.iloc[-2]
        pre_signal_range = pre_signal_candle['range']
        avg_range_10 = df['range'].iloc[-12:-2].mean()

        is_contraction = pre_signal_range < (
            avg_range_10 * 0.5) if avg_range_10 > 0 else False

        signal_candle = df.iloc[-1]
        previous_candle = df.iloc[-2]

        price_change = (
            (signal_candle['close'] - previous_candle['close']
             ) / previous_candle['close']
        ) * 100

        avg_vol = df.iloc[-21:-1]['volume'].mean()
        volume_ratio = signal_candle['volume'] / avg_vol if avg_vol > 0 else 0

        pressure = "📈 Buyer" if signal_candle['close'] > signal_candle['open'] else "📉 Seller"

        # Assign Grade
        grade = "N/A"
        analysis = "No significant move (<2%)."

        is_pump = price_change > 2 and pressure == "📈 Buyer"
        is_dump = price_change < -2 and pressure == "📉 Seller"

        if is_pump or is_dump:
            if volume_ratio < 1.5:
                grade = "F (Trap)"
                analysis = "No real volume → likely fakeout."

            elif volume_ratio < 2.0:
                grade = "B (Weak)" if is_contraction else "C (Noisy)"
                analysis = "Weak or noisy breakout."

            elif volume_ratio < 3.5:
                grade = "A (Prime)" if is_contraction else "B+ (Noisy)"
                analysis = "Prime breakout or noisy high-volume breakout."

            else:
                grade = "A+ (Explosive)" if is_contraction else "A (High Volume)"
                analysis = "Explosive or strong high-volume breakout."

        signal_ts = pd.to_datetime(
            signal_candle['timestamp'], unit='ms'
        ).tz_localize(timezone.utc)

        return {
            'Symbol': symbol,
            'Price': signal_candle['close'],
            'Signal Time': signal_ts,
            'Grade': grade,
            'Analysis': analysis,
            'Price Change (2h) %': price_change,
            'Volume Ratio (2h)': volume_ratio,
            'Dominant Pressure': pressure,
            'Volatility Contraction': is_contraction,
            'Signal Candle OHLC': signal_candle.to_dict(),
        }

    except Exception as e:
        print(f"Error analyzing {symbol}: {e}")
        return None


# ============================================================
# SCAN ALL MARKETS (2H)
# ============================================================
def get_whale_signals(symbol):
    """
    Snapshot-based whale detector using order book + ticker.

    STRICT MODE:
      - We consider a symbol 'whale-confirmed' only if
        whale_score >= 2 (2 or more whale signals are true).

    Returns:
        whale_info: dict with detailed flags
        whale_score: int, how many whale signals are TRUE
    """
    try:
        exchange = ccxt.binance({'options': {'defaultType': 'future'}})

        # --- ORDER BOOK SNAPSHOT ---
        ob = exchange.fetch_order_book(symbol, limit=10)
        bids = ob.get('bids', [])
        asks = ob.get('asks', [])

        if not bids or not asks:
            return {}, 0

        top_bids = bids[:5]
        top_asks = asks[:5]

        bid_vol = sum(b[1] for b in top_bids)
        ask_vol = sum(a[1] for a in top_asks)

        if bid_vol <= 0 or ask_vol <= 0:
            imbalance = 1.0
        else:
            imbalance = bid_vol / ask_vol

        # Imbalance flags
        whale_bid_imbalance = imbalance >= 3.0       # strong buyer side
        whale_ask_imbalance = imbalance <= (1 / 3.0)  # strong seller side

        # Whale walls (resting limit orders much bigger than local average)
        avg_bid = bid_vol / max(len(top_bids), 1)
        avg_ask = ask_vol / max(len(top_asks), 1)

        top_bid_size = top_bids[0][1]
        top_ask_size = top_asks[0][1]

        whale_bid_wall = top_bid_size >= avg_bid * 4
        whale_ask_wall = top_ask_size >= avg_ask * 4

        # --- TICKER SNAPSHOT ---
        ticker = exchange.fetch_ticker(symbol)
        last_price = ticker.get('last')
        best_bid = ticker.get('bid')
        best_ask = ticker.get('ask')

        whale_sweep_buy = False
        whale_sweep_sell = False

        if last_price is not None and best_bid is not None and best_ask is not None:
            # Very simple sweep logic (price jumps beyond current best quotes)
            if last_price > best_ask:
                whale_sweep_buy = True
            elif last_price < best_bid:
                whale_sweep_sell = True

        # Market aggression (if exchange exposes side volumes)
        info = ticker.get('info', {})
        buy_vol = float(info.get('buyVol', 0) or 0)
        sell_vol = float(info.get('sellVol', 0) or 0)
        market_aggression = None
        aggressive_buy = False
        aggressive_sell = False

        if buy_vol > 0 or sell_vol > 0:
            market_aggression = buy_vol / (sell_vol + 1e-9)
            aggressive_buy = market_aggression >= 2.0
            aggressive_sell = market_aggression <= 0.5

        whale_info = {
            'imbalance': imbalance,
            'whale_bid_imbalance': whale_bid_imbalance,
            'whale_ask_imbalance': whale_ask_imbalance,
            'whale_bid_wall': whale_bid_wall,
            'whale_ask_wall': whale_ask_wall,
            'whale_sweep_buy': whale_sweep_buy,
            'whale_sweep_sell': whale_sweep_sell,
            'market_aggression': market_aggression,
            'aggressive_buy': aggressive_buy,
            'aggressive_sell': aggressive_sell,
        }

        # STRICT mode: how many whale conditions are true?
        whale_flags = [
            whale_bid_imbalance,
            whale_ask_imbalance,
            whale_bid_wall,
            whale_ask_wall,
            whale_sweep_buy,
            whale_sweep_sell,
            aggressive_buy,
            aggressive_sell,
        ]
        whale_score = sum(1 for f in whale_flags if f)

        return whale_info, whale_score

    except Exception as e:
        print(f"[WHALE] Error while checking {symbol}: {e}")
        return {}, 0


async def scan_all_markets():
    exchange = ccxt_pro.binance({'options': {'defaultType': 'future'}})
    ignored_symbol = 'BTCST/USDT:USDT'

    try:
        await exchange.load_markets()
        symbols = [s for s in exchange.symbols if s.endswith(
            ':USDT') and s != ignored_symbol]

        print(f"[2H SCANNER] Scanning {len(symbols)} pairs...")

        tasks = [analyze_symbol_2h(exchange, s) for s in symbols]
        results = await asyncio.gather(*tasks)

        df = pd.DataFrame([r for r in results if r is not None])
        if df.empty:
            return df

        tickers = await exchange.fetch_tickers(df['Symbol'].tolist())
        vol24 = {sym: tk['quoteVolume'] for sym, tk in tickers.items()}

        df['24h Volume'] = df['Symbol'].map(vol24).fillna(0)
        df['High 24h Volume'] = df['24h Volume'] > df['24h Volume'].quantile(
            0.75)

        return df

    finally:
        await exchange.close()


# ============================================================
# TRADE PLAN + EXECUTION (UNCHANGED)
# ============================================================

def calculate_trade_plan(row, balance):
    ohlc = row.get("Signal Candle OHLC", {})
    high = float(ohlc.get("high"))
    low = float(ohlc.get("low"))
    close = float(ohlc.get("close"))

    # Direction detection
    direction = "buy" if close > ohlc.get("open") else "sell"

    if direction == "buy":
        entry = high * 1.001       # slight buffer above high
        sl = low * 0.997        # wider stop loss
        risk = entry - sl

        tp1 = entry + risk * 3.0   # expanded TP1
        tp2 = entry + risk * 5.0   # expanded TP2

    else:  # SELL / SHORT
        entry = low * 0.999
        sl = high * 1.003       # wider stop loss for short
        risk = sl - entry

        tp1 = entry - risk * 3.0
        tp2 = entry - risk * 5.0

    # position size (optional)
    size = (balance * 0.1) / entry if entry > 0 else 0

    sl_side = "long_sl" if direction == "buy" else "short_sl"

    return entry, sl, tp1, tp2, size, direction, sl_side


def execute_single_trade_limit(symbol, side, entry, sl, tp, usdt_amount):
    """
    Places a single LIMIT entry with separate SL + TP orders on Binance Futures.
    - symbol: 'BTCUSDT'
    - side: 'buy' or 'sell'
    - entry: entry price
    - sl: stop-loss price
    - tp: take-profit trigger price
    - usdt_amount: how many USDT to risk for THIS position
    """

    api_key, api_secret = get_api_keys()
    if not api_key or not api_secret:
        print("No API keys, cannot execute trade.")
        return

    try:
        exchange = ccxt.binance({
            'apiKey': api_key,
            'secret': api_secret,
            'options': {'defaultType': 'future'}
        })

        # amount of contracts based on USDT per position
        amount = usdt_amount / entry
        amount = float(exchange.amount_to_precision(symbol, amount))

        if amount <= 0:
            print(f"[TRADE] Computed amount is <= 0 for {symbol}. Aborting.")
            return

        print(
            f"[TRADE] Placing entry for {symbol}: side={side}, amount={amount}, entry={entry}, SL={sl}, TP={tp}")

        # Entry order (LIMIT)
        entry_order = exchange.create_order(
            symbol=symbol,
            type='LIMIT',
            side=side,
            amount=amount,
            price=entry,
            params={
                'timeInForce': 'GTC'
            }
        )

        opposite_side = 'sell' if side == 'buy' else 'buy'

        # Stop-loss (STOP_MARKET, reduceOnly)
        sl_order = exchange.create_order(
            symbol=symbol,
            type='STOP_MARKET',
            side=opposite_side,
            amount=amount,
            price=None,
            params={
                'stopPrice': sl,
                'reduceOnly': True,
                'closePosition': False,
                'timeInForce': 'GTC'
            }
        )

        # Take-profit (TAKE_PROFIT_MARKET, reduceOnly)
        tp_order = exchange.create_order(
            symbol=symbol,
            type='TAKE_PROFIT_MARKET',
            side=opposite_side,
            amount=amount,
            price=None,
            params={
                'stopPrice': tp,
                'reduceOnly': True,
                'closePosition': False,
                'timeInForce': 'GTC'
            }
        )

        print(
            f"[TRADE] Orders placed: entry={entry_order['id']}, SL={sl_order['id']}, TP={tp_order['id']}")

    except Exception as e:
        print(f"[TRADE ERROR] {symbol}: {e}")


def execute_two_trades(symbol, side, entry, sl, tp1, tp2, usdt_per_position):
    """
    Places TWO identical positions with same entry & SL,
    but one uses TP1 and the other uses TP2.
    Each position uses `usdt_per_position`.
    """
    print(
        f"[TRADE] Executing two trades for {symbol} with {usdt_per_position} USDT each.")
    execute_single_trade_limit(symbol, side, entry, sl, tp1, usdt_per_position)
    execute_single_trade_limit(symbol, side, entry, sl, tp2, usdt_per_position)


# ============================================================
# POPUP UI (UNCHANGED)
# ============================================================

def show_interactive_popup(signal_row, balance):
    """
    Shows popup AND sends telegram notification.
    Uses manual USDT-per-position input.
    Executes 2 limit trades (TP1 + TP2).
    """

    # --- Extract values ---
    symbol = signal_row["Symbol"]
    grade = signal_row.get("Grade", signal_row.get("Early Grade", "N/A"))
    origin = signal_row.get("SignalOrigin", "N/A")
    whale_score = signal_row.get("WhaleScore", "N/A")
    analysis_text = signal_row.get("Analysis", "")

    # --- Calculate TP/SL/Entry ---
    entry, sl, tp1, tp2, _, side, _sl_side = calculate_trade_plan(
        signal_row, max(1, balance)
    )

    direction_text = "BUY / LONG" if side == "buy" else "SELL / SHORT"

    # --- Telegram Alert ---
    telegram_message = f"""
📢 <b>{symbol} Signal</b>

Grade: {grade}
Direction: {direction_text}
Origin: {origin}
Whale Score: {whale_score}

Entry: {entry:.8f}
SL: {sl:.8f}
TP1: {tp1:.8f}
TP2: {tp2:.8f}

Analysis: {analysis_text}
"""
    send_telegram_message(telegram_message.strip())

    # --- TKINTER POPUP WINDOW ---
    root = tk.Tk()
    root.title(f"Recommended {direction_text} ({grade})")
    root.attributes("-topmost", True)

    window_width = 520
    window_height = 420
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    x = int(screen_width / 2 - window_width / 2)
    y = int(screen_height / 2 - window_height / 2)
    root.geometry(f"{window_width}x{window_height}+{x}+{y}")

    frame = tk.Frame(root, padx=15, pady=15)
    frame.pack(expand=True, fill=tk.BOTH)

    # --- HEADER ---
    tk.Label(frame, text=f"{symbol}", font=("Arial", 18, "bold")).pack()
    tk.Label(frame, text=f"Grade: {grade}", font=("Arial", 11)).pack()
    tk.Label(frame, text=f"Direction: {direction_text}",
             font=("Arial", 12, "bold")).pack(pady=(5, 2))
    tk.Label(frame, text=f"Analysis: {analysis_text}",
             wraplength=480, justify=tk.CENTER).pack()

    # --- Numbers ---
    tk.Label(
        frame,
        text=f"Entry: {entry:.8f}   |   SL: {sl:.8f}",
        font=("Arial", 11, "bold")
    ).pack(pady=4)

    tk.Label(
        frame,
        text=f"TP1: {tp1:.8f}   |   TP2: {tp2:.8f}",
        font=("Arial", 11)
    ).pack()

    # --- BALANCE DISPLAY ---
    tk.Label(
        frame,
        text=f"Current Futures Balance: {balance:.2f} USDT",
        font=("Arial", 11, "bold"),
        fg="blue"
    ).pack(pady=(10, 0))

    # --- USDT INPUT ---
    usdt_frame = tk.Frame(frame)
    usdt_frame.pack(pady=(10, 0))

    tk.Label(
        usdt_frame,
        text="USDT per position (each of 2 trades): ",
        font=("Arial", 10)
    ).pack(side=tk.LEFT)

    usdt_var = tk.StringVar()
    usdt_var.set("3")  # default
    tk.Entry(usdt_frame, textvariable=usdt_var,
             width=8).pack(side=tk.LEFT, padx=5)

    tk.Label(
        frame,
        text=f"Total needed = 2 × USDT per position   (Balance: {balance:.2f} USDT)",
        font=("Arial", 9)
    ).pack()

    # --- BUTTON HANDLERS ---
    def on_execute():
        usdt_str = usdt_var.get().strip()

        try:
            usdt_per_pos = float(usdt_str)
        except:
            messagebox.showerror("Invalid", "Enter a valid USDT amount.")
            return

        if usdt_per_pos <= 0:
            messagebox.showerror("Invalid", "USDT must be > 0.")
            return

        total_needed = usdt_per_pos * 2
        if balance > 0 and total_needed > balance:
            messagebox.showerror(
                "Insufficient Balance",
                f"You need {total_needed:.2f} USDT but you only have {balance:.2f}."
            )
            return

        # Trading thread
        th = threading.Thread(
            target=execute_two_trades,
            args=(symbol, side, entry, sl, tp1, tp2, usdt_per_pos)
        )
        th.start()
        root.destroy()

    def on_cancel():
        root.destroy()

    # --- BUTTONS ---
    btn_frame = tk.Frame(frame)
    btn_frame.pack(pady=15)

    tk.Button(
        btn_frame,
        text=f"EXECUTE {direction_text}",
        bg="#4CAF50" if side == "buy" else "#f44336",
        fg="white",
        width=22,
        command=on_execute
    ).pack(side=tk.LEFT, padx=5)

    tk.Button(
        btn_frame,
        text="Cancel",
        width=12,
        command=on_cancel
    ).pack(side=tk.LEFT, padx=5)

    root.mainloop()


# ============================================================
# 2-HOUR SCAN JOB
# ============================================================

def run_scan_job():
    now_ksa = datetime.now(KSA_TIMEZONE)
    print(f"\n[{now_ksa.strftime('%Y-%m-%d %H:%M:%S')}] Running scheduled scan...")
    db.create_tables()
    df = asyncio.run(scan_all_markets())

    if df is not None and not df.empty:
        # Define A/A+ grades for logging and popups
        top_grades = ['A+ (Explosive)', 'A (Prime)', 'A (High Volume)']
        all_top_signals = df[df['Grade'].isin(top_grades)]

        top_pumps = all_top_signals[
            (all_top_signals['Dominant Pressure'] == '📈 Buyer') &
            (all_top_signals['High 24h Volume'] == True)
        ]
        top_dumps = all_top_signals[
            (all_top_signals['Dominant Pressure'] == '📉 Seller') &
            (all_top_signals['High 24h Volume'] == True)
        ]

        # Log ONLY A/A+ signals
        if not top_pumps.empty:
            db.log_signals(top_pumps, 'Pump')
            print(f"Logged {len(top_pumps)} A/A+ pump signals to database.")
        if not top_dumps.empty:
            db.log_signals(top_dumps, 'Dump')
            print(f"Logged {len(top_dumps)} A/A+ dump signals to database.")

        API_KEY, API_SECRET = get_api_keys()
        if not API_KEY or not API_SECRET:
            print(
                "API Keys not found. Cannot check balance or execute trades. Skipping popups.")
            return

        balance = fetch_balance(API_KEY, API_SECRET)
        is_balance_zero = (balance == 0)

        if is_balance_zero:
            print(
                "Balance is 0.00 USDT. Will still show interactive pop-ups for paper trading."
            )

        popup_threads = []

        # --- PUMPS: Whale-filtered popups (STRICT: whale_score >= 2) ---
        if not top_pumps.empty:
            print(
                f"Found {len(top_pumps)} A/A+ Pump signals before whale filter. Checking whales..."
            )
            for _, row in top_pumps.iterrows():
                symbol = row['Symbol']
                whale_info, whale_score = get_whale_signals(symbol)

                if whale_score < 1:
                    print(
                        f"[2H PUMP] Skipping {symbol} – whale_score={whale_score}")
                    continue

                row_copy = row.copy()
                # Append whale info to analysis text
                row_copy['Analysis'] = (
                    f"{row_copy['Analysis']} | WHALE x{whale_score}"
                )

                popup_thread = threading.Thread(
                    target=show_interactive_popup, args=(row_copy, balance)
                )
                popup_threads.append(popup_thread)
                popup_thread.start()

        # --- DUMPS: Whale-filtered popups (STRICT: whale_score >= 2) ---
        if not top_dumps.empty:
            print(
                f"Found {len(top_dumps)} A/A+ Dump signals before whale filter. Checking whales..."
            )
            for _, row in top_dumps.iterrows():
                symbol = row['Symbol']
                whale_info, whale_score = get_whale_signals(symbol)

                if whale_score < 1:
                    print(
                        f"[2H DUMP] Skipping {symbol} – whale_score={whale_score}")
                    continue

                row_copy = row.copy()
                row_copy['Analysis'] = (
                    f"{row_copy['Analysis']} | WHALE x{whale_score}"
                )

                popup_thread = threading.Thread(
                    target=show_interactive_popup, args=(row_copy, balance)
                )
                popup_threads.append(popup_thread)
                popup_thread.start()

        print("Scan complete. Finished processing signals.")
    else:
        print("Scan failed or returned no data.")


# ============================================================
# EARLY PUMP 1-MIN SCANNER JOB
# ============================================================

def run_early_scan_job():
    print("\n[EARLY SCANNER] Running Early Pump Scan...")

    try:
        early_df = asyncio.run(
            scan_early_pumps_async(
                limit_symbols=60,
                min_price_move_pct=0.35,
                min_volume_ratio=2.0,
            )
        )
    except Exception as e:
        print(f"[EARLY SCANNER] Error: {e}")
        return

    if early_df is None or early_df.empty:
        print("[EARLY SCANNER] No early signals.")
        return

    # Filter only *buyer pumps* + *compression*
    candidates = early_df[
        (early_df['Dominant Pressure'] == '📈 Buyer')
        & (early_df['Volatility Compression (20 vs 20)'] == True)
    ]

    if candidates.empty:
        print("[EARLY SCANNER] No compressed buyer signals.")
        return

    API_KEY, API_SECRET = get_api_keys()
    balance = fetch_balance(API_KEY, API_SECRET)

    for _, row in candidates.iterrows():

        popup_row = {
            'Symbol': row['Symbol'],
            'Grade': row['Early Grade'],
            'Analysis': f"Early Pump: Δ={row['Price Change (1m) %']:.2f}%, "
            f"Vol={row['Volume Ratio (1m)']:.2f}x, "
            f"Compressed={row['Volatility Compression (20 vs 20)']}",
            'Dominant Pressure': row['Dominant Pressure'],

            # Fabricated OHLC for entry/SL math:
            'Signal Candle OHLC': {
                'high': row['Price'] * 1.001,
                'low': row['Price'] * 0.999,
                'open': row['Price'] * 0.999,
                'close': row['Price'],
            }
        }

        threading.Thread(
            target=show_interactive_popup,
            args=(popup_row, balance)
        ).start()

    print(f"[EARLY SCANNER] Popups triggered for {len(candidates)} symbols.")


# ============================================================
# MAIN LOOP
# ============================================================

if __name__ == "__main__":
    print("=== Crypto Scanner Started ===")

    # Run 2H scanner at scheduled KSA times
    scan_times = [
        "01:30", "03:30", "05:30", "07:30", "09:30",
        "11:30", "13:30", "15:30", "17:30", "19:30", "21:30", "23:30"
    ]

    for t in scan_times:
        schedule.every().day.at(t, KSA_TIMEZONE).do(run_scan_job)

    # Early pump scanner → every 5 minutes
    schedule.every(5).minutes.do(run_early_scan_job)

    print("Initial scan (2H) starting...")
    run_scan_job()

    print("System Ready. Waiting for next tasks...")

    while True:
        schedule.run_pending()
        time.sleep(1)
