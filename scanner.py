import streamlit as st
import pandas as pd
import ccxt
import ccxt.pro as ccxt_pro
import asyncio
from datetime import timezone, datetime
import database as db

db.create_table()
st.set_page_config(page_title="Scanner", page_icon="⚡", layout="wide")
st.title('⚡ High-Speed Market Scanner')
st.caption("Now with advanced A-F Signal Grading.")

# --- Initialize Session State ---
if 'connected' not in st.session_state:
    st.session_state.connected = False
if 'usdt_balance' not in st.session_state:
    st.session_state.usdt_balance = 0
if 'api_key' not in st.session_state:
    st.session_state.api_key = ''
if 'api_secret' not in st.session_state:
    st.session_state.api_secret = ''
if 'scanner_results' not in st.session_state:
    st.session_state.scanner_results = pd.DataFrame()
if 'pump_candidates' not in st.session_state:
    st.session_state.pump_candidates = pd.DataFrame()
if 'dump_candidates' not in st.session_state:
    st.session_state.dump_candidates = pd.DataFrame()
if 'last_scan_time' not in st.session_state:
    st.session_state.last_scan_time = None

# --- Corrected Auto-Connection Logic ---
if not st.session_state.connected:
    try:
        API_KEY = st.secrets["API_KEY"]
        API_SECRET = st.secrets["API_SECRET"]
        st.session_state.api_key = API_KEY
        st.session_state.api_secret = API_SECRET

        exchange = ccxt.binance({
            'apiKey': API_KEY,
            'secret': API_SECRET,
            'options': {'defaultType': 'future'}
        })
        with st.spinner("Auto-connecting..."):
            balance = exchange.fetch_balance()
            usdt_balance = balance['USDT']['total']
            st.session_state.usdt_balance = usdt_balance
            st.session_state.connected = True
    except Exception as e:
        st.session_state.connected = False
        if 'API_KEY' in st.secrets:
            st.error(f"Failed to auto-connect using Secrets: {e}")
        pass


def fetch_account_data():
    if not st.session_state.connected:
        return None, None
    try:
        exchange = ccxt.binance({'apiKey': st.session_state.api_key,
                                'secret': st.session_state.api_secret, 'options': {'defaultType': 'future'}})
        balance_data = exchange.fetch_balance()
        margin_ratio = balance_data['info'].get('marginRatio', '0')
        maint_margin = balance_data['info'].get('totalMaintMargin', '0')
        account_health = {'margin_ratio': float(
            margin_ratio) * 100, 'maint_margin': float(maint_margin)}
        positions_raw = exchange.fetch_positions()
        open_positions = [
            p for p in positions_raw if float(p['contracts']) > 0]
        positions_data = [{'Symbol': p['symbol'], 'Side': p['side'].capitalize(), 'Size': p['contracts'], 'Entry Price': p['entryPrice'],
                           'Mark Price': p['markPrice'], 'Unrealized PnL': p['unrealizedPnl']} for p in open_positions]
        return pd.DataFrame(positions_data), account_health
    except Exception:
        return None, None


positions_df, health_data = None, None
if st.session_state.connected:
    positions_df, health_data = fetch_account_data()


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
        is_contraction = pre_signal_range < (avg_range_10 * 0.5)
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
        return {'Symbol': symbol, 'Price': signal_candle['close'], 'Signal Time': signal_timestamp, 'Grade': grade, 'Analysis': analysis, 'Price Change (2h) %': price_change, 'Volume Ratio (2h)': volume_ratio, 'Dominant Pressure': pressure, 'Volatility Contraction': is_contraction}
    except Exception:
        return None


@st.cache_data(ttl=120)
def run_scanner(): return asyncio.run(scan_all_markets())


async def scan_all_markets():
    exchange = ccxt_pro.binance({'options': {'defaultType': 'future'}})
    try:
        await exchange.load_markets()
        symbols = [s for s in exchange.symbols if s.endswith(':USDT')]
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


@st.cache_data(ttl=3600)
def get_daily_forecast():
    try:
        exchange = ccxt.binance({'options': {'defaultType': 'future'}})
        btc_ohlcv = exchange.fetch_ohlcv('BTC/USDT', '1d', limit=3)
        eth_ohlcv = exchange.fetch_ohlcv('ETH/USDT', '1d', limit=3)
        btc_yesterday = btc_ohlcv[-2]
        eth_yesterday = eth_ohlcv[-2]
        btc_change = (
            (btc_yesterday[4] - btc_yesterday[1]) / btc_yesterday[1]) * 100
        eth_change = (
            (eth_yesterday[4] - eth_yesterday[1]) / eth_yesterday[1]) * 100
        btc_trend = "STABLE"
        if btc_change > 1.5:
            btc_trend = "UP"
        elif btc_change < -1.5:
            btc_trend = "DOWN"
        btcd_trend = "STABLE"
        if btc_change > (eth_change + 0.5):
            btcd_trend = "UP"
        elif eth_change > (btc_change + 0.5):
            btcd_trend = "DOWN"
        alt_trend = "STABLE"
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
    except Exception:
        return None, None, None


st.header("Daily Market Forecast")
btc_trend, btcd_trend, alt_trend = get_daily_forecast()
if alt_trend:
    col1, col2, col3 = st.columns(3)
    col1.metric("Bitcoin Trend (Yesterday)", btc_trend)
    col2.metric("Money Flow (ETH vs. BTC)",
                f"Alts {'📈' if btcd_trend == 'DOWN' else '📉' if btcd_trend == 'UP' else '😐'}")
    col3.metric("Today's Altcoin Bias", alt_trend)
else:
    st.error("Could not fetch daily market forecast.")

st.write("---")
if st.session_state.connected:
    st.header("📊 Live Positions Dashboard")
    if positions_df is not None and not positions_df.empty:
        styled_positions = positions_df.style.format({'Entry Price': '{:,.4f}', 'Mark Price': '{:,.4f}',
                                                     'Unrealized PnL': '{:,.2f} USDT'}).background_gradient(cmap='RdYlGn', subset=['Unrealized PnL'])
        st.dataframe(styled_positions, width='stretch', hide_index=True)
    elif positions_df is not None:
        st.info("You have no open positions.")
    st.write("---")

st.header("⚡ 2-Hour Breakout Scanner")
if st.button("🔄 Refresh Scan Data (This may take ~10 seconds)"):
    with st.spinner("🚀 Starting the high-speed scan..."):
        df = run_scanner()
        if not df.empty:
            st.session_state.scanner_results = df
            st.session_state.last_scan_time = datetime.now()

            tradable_grades = [
                'A+ (Explosive)', 'A (Prime)', 'A (High Volume)', 'B+ (Noisy)', 'B (Weak)']
            tradable_pumps = df[(df['Grade'].isin(tradable_grades)) & (
                df['Dominant Pressure'] == '📈 Buyer') & (df['High 24h Volume'] == True)]
            tradable_dumps = df[(df['Grade'].isin(tradable_grades)) & (
                df['Dominant Pressure'] == '📉 Seller') & (df['High 24h Volume'] == True)]

            st.session_state.pump_candidates = tradable_pumps
            st.session_state.dump_candidates = tradable_dumps

            all_signals_to_log = df[df['Grade'] != 'N/A']
            pumps_to_log = all_signals_to_log[all_signals_to_log['Dominant Pressure'] == '📈 Buyer']
            dumps_to_log = all_signals_to_log[all_signals_to_log['Dominant Pressure'] == '📉 Seller']

            if not pumps_to_log.empty:
                db.log_signals(pumps_to_log, 'Pump')
            if not dumps_to_log.empty:
                db.log_signals(dumps_to_log, 'Dump')
            st.success("✅ Scan complete!")
        else:
            st.error("An error occurred during the scan.")

if st.session_state.last_scan_time:
    st.caption(
        f"Data last refreshed: {st.session_state.last_scan_time.strftime('%Y-%m-%d %H:%M:%S')}")
else:
    st.info("Click the 'Refresh Scan Data' button to start the first scan.")

if not st.session_state.scanner_results.empty:
    df_to_display = st.session_state.scanner_results
    pump_candidates = st.session_state.pump_candidates
    dump_candidates = st.session_state.dump_candidates

    filter_option = st.radio("Filter Results:", ("Show All",
                             "Show Tradable Pumps", "Show Tradable Dumps"), horizontal=True)
    if filter_option == "Show Tradable Pumps":
        df_to_display = pump_candidates
    elif filter_option == "Show Tradable Dumps":
        df_to_display = dump_candidates

    if not df_to_display.empty:
        display_columns = ['Symbol', 'Price', 'Signal Time', 'Grade', 'Analysis',
                           'Price Change (2h) %', 'Volume Ratio (2h)', 'Volatility Contraction', 'Dominant Pressure']

        grade_map = {'A+ (Explosive)': 0, 'A (Prime)': 1, 'A (High Volume)': 2,
                     'B+ (Noisy)': 3, 'B (Weak)': 4, 'C (Weak/Noisy)': 5, 'F (Trap)': 6, 'N/A': 7}
        df_to_display['Grade_Sort'] = df_to_display['Grade'].map(grade_map)
        df_sorted = df_to_display.sort_values(by='Grade_Sort')

        def grade_color(grade):
            if 'A+' in grade:
                return 'background-color: #2F855A'
            if 'A (' in grade:
                return 'background-color: #38A169'
            if 'B+' in grade:
                return 'background-color: #B2980A'
            if 'B (' in grade:
                return 'background-color: #D69E2E'
            if 'C (' in grade:
                return 'background-color: #BF4C1F'
            if 'F (' in grade:
                return 'background-color: #9B2C2C'
            return ''

        styled_df = df_sorted[display_columns].style.format({
            'Price': '{:,.4f}', 'Signal Time': lambda t: t.strftime('%Y-%m-%d %H:%M'),
            'Price Change (2h) %': '{:.2f}%', 'Volume Ratio (2h)': '{:.2f}x',
            'Volatility Contraction': lambda v: "✅ Yes" if v else "❌ No"
        }).map(grade_color, subset=['Grade'])

        st.dataframe(styled_df, width='stretch', height=500, hide_index=True)
    else:
        st.warning("No pairs currently meet the selected filter criteria.")

num_pumps, num_dumps = 0, 0
if not st.session_state.pump_candidates.empty:
    num_pumps = len(
        st.session_state.pump_candidates[st.session_state.pump_candidates['Grade'] == 'A+ (Explosive)'])
if not st.session_state.dump_candidates.empty:
    num_dumps = len(
        st.session_state.dump_candidates[st.session_state.dump_candidates['Grade'] == 'A+ (Explosive)'])

with st.sidebar:
    if st.session_state.connected:
        st.success(
            f"Connected ✅\n\nBalance: {st.session_state.usdt_balance:,.2f} USDT")
        if health_data:
            st.metric("Maintenance Margin",
                      f"${health_data['maint_margin']:,.2f}")
            st.metric("Margin Ratio", f"{health_data['margin_ratio']:.2f}%")
            if health_data['margin_ratio'] > 80:
                st.warning("⚠️ Margin Ratio is high!")
            else:
                st.info("Margin Ratio is safe.")
    else:
        st.sidebar.warning("Not Connected 🔴")

    st.write("---")
    st.subheader("🌡️ Market Pulse (A+ Setups)")
    st.metric("Pump Signals Found", f"{num_pumps} 🟢")
    st.metric("Dump Signals Found", f"{num_dumps} 🔴")
