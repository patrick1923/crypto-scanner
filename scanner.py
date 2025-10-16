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


@st.cache_data(ttl=3600)
def get_daily_forecast():
    """
    Fetches daily data for BTC & ETH to determine the overall market trend
    based on the 'Altseason Cheatsheet' logic.
    """
    try:
        exchange = ccxt.binance({'options': {'defaultType': 'future'}})

        # 1. Fetch Daily Data
        # [day before, yesterday, today]
        btc_ohlcv = exchange.fetch_ohlcv('BTC/USDT', '1d', limit=3)
        eth_ohlcv = exchange.fetch_ohlcv('ETH/USDT', '1d', limit=3)

        # Get 'yesterday's' candle (index -2)
        btc_yesterday = btc_ohlcv[-2]
        eth_yesterday = eth_ohlcv[-2]

        # 2. Calculate Percent Change for Yesterday
        btc_change = (
            (btc_yesterday[4] - btc_yesterday[1]) / btc_yesterday[1]) * 100
        eth_change = (
            (eth_yesterday[4] - eth_yesterday[1]) / eth_yesterday[1]) * 100

        # 3. Define BTC Trend
        btc_trend = "STABLE"
        if btc_change > 1.5:
            btc_trend = "UP"
        elif btc_change < -1.5:
            btc_trend = "DOWN"

        # 4. Define "BTC Dominance" Trend (ETH vs BTC Proxy)
        btcd_trend = "STABLE"
        if btc_change > (eth_change + 0.5):
            btcd_trend = "UP"     # BTC significantly outperformed ETH
        elif eth_change > (btc_change + 0.5):
            btcd_trend = "DOWN"  # ETH significantly outperformed BTC

        # 5. Apply Cheatsheet Logic
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

    except Exception as e:
        print(f"Error in daily forecast: {e}")
        return None, None, None


@st.cache_data(ttl=3600)  # Cache for 1 hour
def get_top_watchlist(df_all):
    """Finds top 3 alts based on yesterday's relative volume."""
    try:
        exchange = ccxt.binance({'options': {'defaultType': 'future'}})
        # Use the liquid pairs from our 24h volume check
        liquid_symbols = df_all[df_all['High 24h Volume']
                                == True]['Symbol'].tolist()

        watchlist = []
        for symbol in liquid_symbols:
            if symbol in ['BTC/USDT', 'ETH/USDT']:
                continue
            # 20 for avg, 1 for yesterday
            ohlcv = exchange.fetch_ohlcv(symbol, '1d', limit=21)
            if len(ohlcv) < 21:
                continue

            df = pd.DataFrame(
                ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            avg_vol = df.iloc[:-1]['volume'].mean()
            yesterday_vol = df.iloc[-1]['volume']

            rel_vol = yesterday_vol / avg_vol if avg_vol > 0 else 0
            watchlist.append({'Symbol': symbol, 'Rel. Vol': rel_vol})

        watchlist_df = pd.DataFrame(watchlist).sort_values(
            by='Rel. Vol', ascending=False)
        return watchlist_df.head(3)

    except Exception as e:
        print(f"Error creating watchlist: {e}")
        return pd.DataFrame()


# --- Display Daily Forecast ---
st.header("Daily Market Forecast")
btc_trend, btcd_trend, alt_trend = get_daily_forecast()

if alt_trend:
    col1, col2, col3 = st.columns(3)
    col1.metric("Bitcoin Trend (Yesterday)", btc_trend)
    col2.metric("Money Flow (ETH vs. BTC)",
                f"Alts {'📈' if btcd_trend == 'DOWN' else '📉' if btcd_trend == 'UP' else '😐'}")
    col3.metric("Today's Altcoin Bias", alt_trend)

    if alt_trend == "ALT SEASON 🚀":
        st.success(
            "BIAS: Strong bullish. Focus on **Pump Candidates**. Be aggressive with A+ setups.")
    elif alt_trend in ["ALTS UP 📈"]:
        st.info(
            "BIAS: Mildly bullish. Focus on **Pump Candidates** but be more selective.")
    elif alt_trend == "ALTS DUMP 🚨":
        st.error(
            "BIAS: Strong bearish. Focus only on **Dump Candidates**. Avoid longs.")
    else:
        st.warning(
            "BIAS: Neutral / Choppy. Be highly selective. Wait for A+ signals only.")
else:
    st.error("Could not fetch daily market forecast.")


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
with st.expander("🔗 Connect to Your Binance Account"):
    api_key = st.text_input(
        "API Key", value=st.session_state.api_key, key="api_key_input")
    api_secret = st.text_input("API Secret", type="password",
                               value=st.session_state.api_secret, key="api_secret_input")
    if st.button("Connect & Check Balance"):
        if not api_key or not api_secret:
            st.warning("Please enter both API Key and API Secret.")
        else:
            try:
                exchange = ccxt.binance(
                    {'apiKey': api_key, 'secret': api_secret, 'options': {'defaultType': 'future'}})
                with st.spinner("Connecting and fetching balance..."):
                    balance = exchange.fetch_balance()
                    usdt_balance = balance['USDT']['total']
                    st.session_state.usdt_balance = usdt_balance
                    st.session_state.api_key = api_key
                    st.session_state.api_secret = api_secret
                    st.session_state.connected = True
                    st.success(
                        f"✅ Connection successful! Your total USDT balance is: **{usdt_balance:,.2f}**")
            except ccxt.AuthenticationError:
                st.error("Authentication Error: Your API keys are incorrect.")
            except Exception as e:
                st.error(f"An error occurred: {e}")


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
        price_score, volume_score = 0, 0
        if abs(price_change) > 6:
            price_score = 50
        elif abs(price_change) > 4:
            price_score = 35
        elif abs(price_change) > 2:
            price_score = 20
        if volume_ratio > 5.0:
            volume_score = 50
        elif volume_ratio > 3.5:
            volume_score = 35
        elif volume_ratio > 2.0:
            volume_score = 20
        confidence_score = price_score + volume_score
        signal_timestamp = pd.to_datetime(
            signal_candle['timestamp'], unit='ms').tz_localize(timezone.utc)
        return {'Symbol': symbol, 'Price': signal_candle['close'], 'Signal Time': signal_timestamp, 'Confidence Score': confidence_score, 'Price Change (2h) %': price_change, 'Volume Ratio (2h)': volume_ratio, 'Dominant Pressure': pressure, 'Volatility Contraction': is_contraction}
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
            pump_candidates = df[(df['Price Change (2h) %'] > 2) & (df['Volume Ratio (2h)'] > 2.0) & (
                df['Dominant Pressure'] == '📈 Buyer') & (df['High 24h Volume'] == True)]
            dump_candidates = df[(df['Price Change (2h) %'] < -2) & (df['Volume Ratio (2h)'] > 2.0) & (
                df['Dominant Pressure'] == '📉 Seller') & (df['High 24h Volume'] == True)]
            st.session_state.pump_candidates = pump_candidates
            st.session_state.dump_candidates = dump_candidates
            if not pump_candidates.empty:
                db.log_signals(pump_candidates, 'Pump')
            if not dump_candidates.empty:
                db.log_signals(dump_candidates, 'Dump')
            st.success("✅ Scan complete!")
        else:
            st.error("An error occurred during the scan.")

if st.session_state.last_scan_time:
    st.caption(
        f"Data last refreshed: {st.session_state.last_scan_time.strftime('%Y-%m-%d %H:%M:%S')}")
else:
    st.info("Click the 'Refresh Scan Data' button to start the first scan.")
    st.stop()

if not st.session_state.scanner_results.empty:
    df_to_display = st.session_state.scanner_results
    pump_candidates = st.session_state.pump_candidates
    dump_candidates = st.session_state.dump_candidates

    # --- NEW: Display Top 3 Watchlist ---
    st.subheader("Top 3 to Watch Today (Based on Yesterday's Volume)")
    watchlist_df = get_top_watchlist(st.session_state.scanner_results)
    if not watchlist_df.empty:
        st.dataframe(watchlist_df, width='stretch', hide_index=True)
    else:
        st.info("Analyzing yesterday's volume... run scan again if empty.")

    filter_option = st.radio("Filter Results:", ("Show All",
                             "Show Pump Candidates", "Show Dump Candidates"), horizontal=True)
    if filter_option == "Show Pump Candidates":
        df_to_display = pump_candidates
    elif filter_option == "Show Dump Candidates":
        df_to_display = dump_candidates

    if not df_to_display.empty:
        display_columns = ['Symbol', 'Price', 'Signal Time', 'Confidence Score',
                           'Price Change (2h) %', 'Volume Ratio (2h)', '24h Volume', 'Volatility Contraction', 'Dominant Pressure']
        df_sorted = df_to_display.sort_values(
            by='Confidence Score', ascending=False)
        styled_df = df_sorted[display_columns].style.format({'Price': '{:,.4f}', 'Signal Time': lambda t: t.strftime('%Y-%m-%d %H:%M'), 'Confidence Score': '{:,.0f}%', 'Price Change (2h) %': '{:.2f}%', 'Volume Ratio (2h)': '{:.2f}x',
                                                            '24h Volume': '{:,.0f} USDT', 'Volatility Contraction': lambda v: "✅ Yes" if v else "❌ No"}).background_gradient(cmap='Greens', subset=['Confidence Score']).background_gradient(cmap='coolwarm', subset=['Price Change (2h) %'])
        st.dataframe(styled_df, width='stretch', height=500, hide_index=True)
    else:
        st.warning("No pairs currently meet the selected filter criteria.")

num_pumps, num_dumps = 0, 0
if not st.session_state.pump_candidates.empty:
    num_pumps = len(
        st.session_state.pump_candidates[st.session_state.pump_candidates['Volatility Contraction'] == True])
if not st.session_state.dump_candidates.empty:
    num_dumps = len(
        st.session_state.dump_candidates[st.session_state.dump_candidates['Volatility Contraction'] == True])

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
