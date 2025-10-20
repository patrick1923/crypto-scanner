import asyncio
import sys
# --- FIX: Set the Windows event loop policy AT THE VERY TOP ---
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
# --- End of fix ---

import pandas as pd
import ccxt
import ccxt.pro as ccxt_pro
import time
import schedule
from datetime import datetime, timezone, timedelta
import database as db  # Ensure database.py is in the same folder
import pytz
import tkinter as tk  # Use Tkinter for GUI
from tkinter import messagebox, ttk  # Added ttk for themed widgets
import threading  # To run Tkinter in a separate thread
from plyer import notification  # Still used for the simple Zero Balance alert

# --- Configuration ---
KSA_TIMEZONE = pytz.timezone('Asia/Riyadh')
DEFAULT_RISK_PERCENT = 1.0  # Default risk

# --- Function to get API keys securely ---


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
            print(
                "Warning: API_KEY or API_SECRET not found/readable in .streamlit/secrets.toml.")
            return None, None
        return api_key, api_secret
    except FileNotFoundError:
        print("Error: '.streamlit/secrets.toml' file not found.")
        return None, None
    except Exception as e:
        print(f"Error reading secrets.toml: {e}.")
        return None, None

# --- Fetch Balance Function ---


def fetch_balance(api_key, api_secret):
    if not api_key or not api_secret:
        return 0
    try:
        exchange = ccxt.binance(
            {'apiKey': api_key, 'secret': api_secret, 'options': {'defaultType': 'future'}})
        balance = exchange.fetch_balance()
        usdt_info = balance.get('USDT', {})
        usdt_balance = usdt_info.get('total', 0)
        return float(usdt_balance) if usdt_balance is not None else 0
    except ccxt.AuthenticationError:
        print("Authentication Error: Failed to fetch balance. Check API keys.")
        return 0
    except Exception as e:
        print(f"Error fetching balance: {e}")
        return 0

# --- Analysis Logic ---


async def analyze_symbol_2h(exchange, symbol):
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, '2h', limit=22)
        if len(ohlcv) < 22:
            return None
        df = pd.DataFrame(
            ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['range'] = df['high'] - df['low']
        pre_signal_candle = df.iloc[-2]
        pre_signal_range = pre_signal_candle['range']
        avg_range_10 = df['range'].iloc[-12:-2].mean()
        is_contraction = pre_signal_range < (
            avg_range_10 * 0.5) if avg_range_10 > 0 else False
        signal_candle = df.iloc[-1]
        previous_candle = df.iloc[-2]
        price_change = (
            (signal_candle['close'] - previous_candle['close']) / previous_candle['close']) * 100
        average_volume = df.iloc[-21:-1]['volume'].mean()
        volume_ratio = signal_candle['volume'] / \
            average_volume if average_volume > 0 else 0
        pressure = "📈 Buyer" if signal_candle['close'] > signal_candle['open'] else "📉 Seller"
        signal_timestamp = pd.to_datetime(
            signal_candle['timestamp'], unit='ms').tz_localize(timezone.utc)
        grade = "N/A"
        analysis = "No significant price move. (Fails < 2% check)"
        is_pump_signal = price_change > 2 and pressure == "📈 Buyer"
        is_dump_signal = price_change < -2 and pressure == "📉 Seller"
        if is_pump_signal or is_dump_signal:
            if volume_ratio < 1.5:
                grade = "F (Trap)"
                analysis = "Price is moving with NO volume. High risk of fakeout."
            elif volume_ratio < 2.0:
                if is_contraction:
                    grade = "B (Weak)"
                    analysis = "Weak volume, but breakout came from consolidation. (B-Grade)"
                else:
                    grade = "C (Weak/Noisy)"
                    analysis = "Weak volume and a noisy breakout. Very high risk. (C-Grade)"
            elif volume_ratio < 3.5:
                if is_contraction:
                    grade = "A (Prime)"
                    analysis = "A-Grade setup. Breakout from consolidation with good volume."
                else:
                    grade = "B+ (Noisy)"
                    analysis = "Good volume, but did not come from a calm state. (B+-Grade)"
            else:
                if is_contraction:
                    grade = "A+ (Explosive)"
                    analysis = "A+ Setup. Explosive volume from a perfect consolidation."
                else:
                    grade = "A (High Volume)"
                    analysis = "A-Grade setup. Explosive volume from a noisy state."
        return {'Symbol': symbol, 'Price': signal_candle['close'], 'Signal Time': signal_timestamp, 'Grade': grade, 'Analysis': analysis, 'Price Change (2h) %': price_change, 'Volume Ratio (2h)': volume_ratio, 'Dominant Pressure': pressure, 'Volatility Contraction': is_contraction, 'Signal Candle OHLC': signal_candle.to_dict()}
    except Exception as e:
        if isinstance(e, ccxt.ExchangeError) and 'Invalid symbol status' in str(e):
            return None  # Silently skip
        print(f"Error analyzing {symbol}: {e}")
        return None


async def scan_all_markets():
    exchange = ccxt_pro.binance({'options': {'defaultType': 'future'}})
    symbol_to_ignore = 'BTCST/USDT:USDT'
    try:
        await exchange.load_markets()
        symbols = [s for s in exchange.symbols if s.endswith(
            ':USDT') and s != symbol_to_ignore]
        print(
            f"Scanning {len(symbols)} active USDT pairs (ignoring {symbol_to_ignore})...")
        tasks = [analyze_symbol_2h(exchange, symbol) for symbol in symbols]
        results = await asyncio.gather(*tasks)
        df = pd.DataFrame([res for res in results if res is not None])
        if df.empty:
            return df
        all_tickers = await exchange.fetch_tickers(df['Symbol'].tolist())
        volumes_24h = {symbol: ticker['quoteVolume']
                       for symbol, ticker in all_tickers.items()}
        df['24h Volume'] = df['Symbol'].map(volumes_24h).fillna(0)
        volume_threshold = df['24h Volume'].quantile(0.75)
        df['High 24h Volume'] = df['24h Volume'] > volume_threshold
        return df
    finally:
        await exchange.close()

# --- Daily Forecast Function (now synchronous) ---


def get_daily_forecast():
    try:
        exchange = ccxt.binance(
            {'options': {'defaultType': 'future'}})  # Use standard ccxt
        btc_ohlcv = exchange.fetch_ohlcv('BTC/USDT', '1d', limit=3)
        eth_ohlcv = exchange.fetch_ohlcv('ETH/USDT', '1d', limit=3)
        btc_yesterday = btc_ohlcv[-2]
        eth_yesterday = eth_ohlcv[-2]
        btc_change = (
            (btc_yesterday[4] - btc_yesterday[1]) / btc_yesterday[1]) * 100
        eth_change = (
            (eth_yesterday[4] - eth_yesterday[1]) / eth_yesterday[1]) * 100
        btc_trend = "STABLE"
        alt_trend = "STABLE"
        btcd_trend = "STABLE"
        if btc_change > 1.5:
            btc_trend = "UP"
        elif btc_change < -1.5:
            btc_trend = "DOWN"
        if btc_change > (eth_change + 0.5):
            btcd_trend = "UP"
        elif eth_change > (btc_change + 0.5):
            btcd_trend = "DOWN"
        if btcd_trend == "UP":
            if btc_trend == "UP":
                alt_trend = "ALTS DOWN 📉"
            elif btc_trend == "DOWN":
                alt_trend = "ALTS DUMP 🚨"
            else:
                alt_trend = "ALTS STABLE 😐"
        elif btcd_trend == "STABLE":
            if btc_trend == "UP":
                alt_trend = "ALTS UP 📈"
            elif btc_trend == "DOWN":
                alt_trend = "ALTS DOWN 📉"
            else:
                alt_trend = "ALTS STABLE 😐"
        elif btcd_trend == "DOWN":
            if btc_trend == "UP":
                alt_trend = "ALT SEASON 🚀"
            elif btc_trend == "DOWN":
                alt_trend = "ALTS STABLE 😐"
            else:
                alt_trend = "ALTS UP 📈"
        return btc_trend, btcd_trend, alt_trend
    except Exception as e:
        print(f"Error fetching daily forecast: {e}")
        return None, None, None


def calculate_trade_plan(signal_row, balance=1):
    risk_percent = DEFAULT_RISK_PERCENT
    signal_candle = signal_row['Signal Candle OHLC']
    is_pump = signal_row['Dominant Pressure'] == '📈 Buyer'
    if is_pump:
        entry_price = signal_candle['high'] * 1.001
        stop_loss_price = signal_candle['low'] * 0.999
        risk_distance = entry_price - stop_loss_price
        tp1_price = entry_price + (risk_distance * 1.5)
        tp2_price = entry_price + (risk_distance * 2.5)
        side = 'buy'
        sl_side = 'sell'
    else:
        entry_price = signal_candle['low'] * 0.999
        stop_loss_price = signal_candle['high'] * 1.001
        risk_distance = stop_loss_price - entry_price
        tp1_price = max(0, entry_price - (risk_distance * 1.5))
        tp2_price = max(0, entry_price - (risk_distance * 2.5))
        side = 'sell'
        sl_side = 'buy'
    risk_amount_usd = max(1, balance) * (risk_percent / 100)
    raw_position_size = risk_amount_usd / risk_distance if risk_distance > 0 else 0
    return entry_price, stop_loss_price, tp1_price, tp2_price, raw_position_size, side, sl_side


def execute_trade(symbol, side, entry_price, sl_price, final_position_size, sl_side):
    api_key, api_secret = get_api_keys()
    if not api_key or not api_secret:
        messagebox.showerror(
            "Error", "API Keys not found or invalid in secrets.toml.")
        return
    print(
        f"\nAttempting to execute trade for {symbol} with size {final_position_size}...")
    try:
        exchange = ccxt.binance(
            {'apiKey': api_key, 'secret': api_secret, 'options': {'defaultType': 'future'}})
        exchange.load_markets()
        market = exchange.market(symbol)
        amount_precision = market.get('precision', {}).get('amount')
        price_precision = market.get('precision', {}).get('price')
        min_amount = market.get('limits', {}).get('amount', {}).get('min')
        if amount_precision is None or min_amount is None or price_precision is None:
            raise ValueError(
                f"Could not determine precision or min amount for {symbol}")
        entry_price_formatted = exchange.price_to_precision(
            symbol, entry_price)
        sl_price_formatted = exchange.price_to_precision(symbol, sl_price)
        position_size_formatted = exchange.amount_to_precision(
            symbol, final_position_size)
        position_size_float = float(position_size_formatted)
        print(
            f"Formatted Size: {position_size_formatted}, Min Amount: {min_amount}")
        if position_size_float < min_amount:
            msg = f"Final size {position_size_float} is less than min size {min_amount}. Aborting."
            print(msg)
            messagebox.showwarning("Trade Failed", msg)
            return
        print(
            f"Placing {side.upper()} STOP order for {position_size_formatted} {symbol.split('/')[0]} at trigger {entry_price_formatted}")
        entry_order = exchange.create_order(symbol=symbol, type='STOP_MARKET', side=side, amount=position_size_float, params={
                                            'stopPrice': entry_price_formatted})
        print(f"Entry order placed: ID {entry_order['id']}")
        time.sleep(1)
        print(
            f"Placing STOP_LOSS ({sl_side.upper()}) order for {position_size_formatted} {symbol.split('/')[0]} at trigger {sl_price_formatted}")
        sl_order = exchange.create_order(symbol=symbol, type='STOP_MARKET', side=sl_side, amount=position_size_float, params={
                                         'stopPrice': sl_price_formatted, 'reduceOnly': True})
        print(f"Stop loss order placed: ID {sl_order['id']}")
        messagebox.showinfo(
            "Trade Executed", f"Placed entry and stop-loss orders for {symbol} on Binance.")
    except ccxt.InsufficientFunds as e:
        print(f"Insufficient funds: {e}")
        messagebox.showerror("Trade Failed", f"Insufficient funds: {e}")
    except ccxt.ExchangeError as e:
        print(f"Binance Error: {e}")
        messagebox.showerror("Trade Failed", f"Binance Error: {e}")
    except ValueError as e:
        print(f"Value Error: {e}")
        messagebox.showerror("Trade Failed", f"Value Error: {e}")
    except Exception as e:
        print(f"An unexpected error occurred during trade execution: {e}")
        messagebox.showerror(
            "Trade Failed", f"An unexpected error occurred: {e}")


def show_interactive_popup(signal_row, balance):
    entry, sl, tp1, tp2, raw_size, side, sl_side = calculate_trade_plan(
        signal_row, max(1, balance))
    symbol = signal_row['Symbol']
    grade = signal_row['Grade']
    is_balance_zero = (balance == 0)
    final_size = raw_size
    min_size_info = ""
    try:
        exchange_sync = ccxt.binance()
        market = exchange_sync.market(symbol)
        min_amount = market.get('limits', {}).get('amount', {}).get('min')
        if min_amount is not None:
            if raw_size < min_amount:
                final_size = min_amount
                min_size_info = f"(Adjusted to Min Size: {min_amount})"
            final_size = float(
                exchange_sync.amount_to_precision(symbol, final_size))
        else:
            print(f"Warning: Could not determine min amount for {symbol}")
            final_size = float(
                exchange_sync.amount_to_precision(symbol, raw_size))
    except Exception as e:
        print(f"Error getting market info for size adjustment: {e}")
        final_size = raw_size
        pass

    def on_trade_click():
        print(f"Trade button clicked for {symbol}. Final size: {final_size}")
        if is_balance_zero:
            messagebox.showwarning(
                "Cannot Execute", "Balance is zero. Cannot place real trade.")
            root.destroy()
            return
        trade_thread = threading.Thread(target=execute_trade, args=(
            symbol, side, entry, sl, final_size, sl_side))
        trade_thread.start()
        root.destroy()

    def on_cancel_click(): print(
        f"Cancel button clicked for {symbol}"); root.destroy()
    root = tk.Tk()
    is_pump = (side == 'buy')
    title = f"🟢 Recommended Buy ({grade})" if is_pump else f"🔴 Recommended Sell ({grade})"
    root.title(title)
    root.attributes('-topmost', True)
    window_width = 450
    window_height = 250
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    center_x = int(screen_width/2 - window_width / 2)
    center_y = int(screen_height/2 - window_height / 2)
    root.geometry(f'{window_width}x{window_height}+{center_x}+{center_y}')
    main_frame = tk.Frame(root, padx=15, pady=15)
    main_frame.pack(expand=True, fill=tk.BOTH)
    tk.Label(main_frame, text=f"{symbol}", font=(
        "Arial", 16, "bold")).pack(pady=(0, 5))
    tk.Label(main_frame, text=f"Grade: {grade}", font=("Arial", 10)).pack()
    tk.Label(
        main_frame, text=f"Analysis: {signal_row['Analysis']}", wraplength=400, justify=tk.CENTER).pack()
    tk.Label(main_frame, text=f"Entry: {entry:.8f} | SL: {sl:.8f}", font=(
        "Arial", 10, "bold")).pack(pady=(10, 2))
    tk.Label(main_frame, text=f"TP1: {tp1:.8f} | TP2: {tp2:.8f}", font=(
        "Arial", 10)).pack()
    tk.Label(main_frame, text=f"Position Size (1% Risk): {final_size:.4f} {symbol.split('/')[0]} {min_size_info}", font=(
        "Arial", 9)).pack(pady=(5, 0))
    button_frame = tk.Frame(main_frame)
    button_frame.pack(pady=(15, 0))
    buy_button = tk.Button(button_frame, text="Buy (Long)", command=on_trade_click,
                           bg="#4CAF50", fg="white", width=12, font=("Arial", 10))
    buy_button.pack(side=tk.LEFT, padx=5)
    if not is_pump:
        buy_button.config(state=tk.DISABLED, bg="#cccccc")
    sell_button = tk.Button(button_frame, text="Sell (Short)", command=on_trade_click,
                            bg="#f44336", fg="white", width=12, font=("Arial", 10))
    sell_button.pack(side=tk.LEFT, padx=5)
    if is_pump:
        sell_button.config(state=tk.DISABLED, bg="#cccccc")
    cancel_button = tk.Button(button_frame, text="Cancel",
                              command=on_cancel_click, width=12, font=("Arial", 10))
    cancel_button.pack(side=tk.LEFT, padx=5)
    root.mainloop()


def show_info_popup(signal_row):
    symbol = signal_row['Symbol']
    grade = signal_row['Grade']
    analysis = signal_row['Analysis']
    def on_ok_click(): root.destroy()
    root = tk.Tk()
    root.title(f"ℹ️ Signal Found ({grade}) - Balance Zero")
    root.attributes('-topmost', True)
    window_width = 400
    window_height = 150
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    center_x = int(screen_width/2 - window_width / 2)
    center_y = int(screen_height/2 - window_height / 2)
    root.geometry(f'{window_width}x{window_height}+{center_x}+{center_y}')
    main_frame = tk.Frame(root, padx=15, pady=15)
    main_frame.pack(expand=True, fill=tk.BOTH)
    tk.Label(main_frame, text=f"{symbol}", font=(
        "Arial", 14, "bold")).pack(pady=(0, 5))
    tk.Label(main_frame, text=f"Grade: {grade}").pack()
    tk.Label(main_frame, text=f"Analysis: {analysis}",
             wraplength=380, justify=tk.CENTER).pack(pady=(5, 0))
    ok_button = tk.Button(main_frame, text="OK", command=on_ok_click, width=10)
    ok_button.pack(pady=(15, 0))
    root.mainloop()


def send_simple_notification(title, message):
    try:
        notification.notify(title=title, message=message,
                            app_name='Crypto Scanner', timeout=20)
        print(f"Sent simple notification: {title}")
    except Exception as e:
        print(f"Error sending simple notification: {e}")

# --- Main Worker Job ---


def run_scan_job():
    now_ksa = datetime.now(KSA_TIMEZONE)
    print(f"\n[{now_ksa.strftime('%Y-%m-%d %H:%M:%S')}] Running scheduled scan...")
    db.create_tables()
    df = asyncio.run(scan_all_markets())

    if df is not None and not df.empty:
        # --- NEW: Get Daily Forecast ---
        btc_trend, btcd_trend, alt_trend = get_daily_forecast()
        if alt_trend:
            forecast_title = "📅 Daily Market Forecast"
            forecast_message = (
                f"BTC Trend: {btc_trend}\n"
                f"Money Flow: Alts {'📈' if btcd_trend == 'DOWN' else '📉' if btcd_trend == 'UP' else '😐'}\n"
                f"Altcoin Bias: {alt_trend}"
            )
            # Send forecast as a simple notification
            send_simple_notification(forecast_title, forecast_message)
        else:
            print("Could not get daily forecast.")

        # --- Log Signals ---
        tradable_grades = ['A+ (Explosive)', 'A (Prime)',
                           'A (High Volume)', 'B+ (Noisy)', 'B (Weak)']
        all_tradable_signals = df[df['Grade'].isin(tradable_grades)]
        tradable_pumps = all_tradable_signals[(all_tradable_signals['Dominant Pressure'] == '📈 Buyer') & (
            all_tradable_signals['High 24h Volume'] == True)]
        tradable_dumps = all_tradable_signals[(all_tradable_signals['Dominant Pressure'] == '📉 Seller') & (
            all_tradable_signals['High 24h Volume'] == True)]
        if not tradable_pumps.empty:
            db.log_signals(tradable_pumps, 'Pump')
            print(f"Logged {len(tradable_pumps)} TRADABLE pump signals.")
        if not tradable_dumps.empty:
            db.log_signals(tradable_dumps, 'Dump')
            print(f"Logged {len(tradable_dumps)} TRADABLE dump signals.")

        # --- Balance Check ---
        API_KEY, API_SECRET = get_api_keys()
        if not API_KEY or not API_SECRET:
            print("API Keys not found. Skipping balance check and trade popups.")
            return
        balance = fetch_balance(API_KEY, API_SECRET)
        is_balance_zero = (balance == 0)
        if is_balance_zero:
            print("Balance is 0.00 USDT. Showing interactive pop-ups for paper trading.")

        # --- Launch Popups ---
        popup_threads = []
        if not tradable_pumps.empty:
            print(
                f"Found {len(tradable_pumps)} tradable Pump signals. Launching popups...")
            for _, row in tradable_pumps.iterrows():
                popup_thread = threading.Thread(
                    target=show_interactive_popup, args=(row.copy(), balance))
                popup_threads.append(popup_thread)
                popup_thread.start()
        if not tradable_dumps.empty:
            print(
                f"Found {len(tradable_dumps)} tradable Dump signals. Launching popups...")
            for _, row in tradable_dumps.iterrows():
                popup_thread = threading.Thread(
                    target=show_interactive_popup, args=(row.copy(), balance))
                popup_threads.append(popup_thread)
                popup_thread.start()

        print(f"Scan complete. Finished processing signals.")
    else:
        print("Scan failed or returned no data.")


if __name__ == "__main__":
    print("Starting local scanner with interactive popups...")
    scan_times_ksa = ["01:30", "03:30", "05:30", "07:30", "09:30",
                      "11:30", "13:30", "15:30", "17:30", "19:30", "21:30", "23:30"]
    print(f"Will scan daily at KSA times: {', '.join(scan_times_ksa)}")
    for scan_time in scan_times_ksa:
        schedule.every().day.at(scan_time, KSA_TIMEZONE).do(run_scan_job)
    print("Running initial scan on startup...")
    run_scan_job()
    print("Initial scan finished. Waiting for next scheduled run...")
    while True:
        schedule.run_pending()
        time.sleep(1)
