import streamlit as st
import pandas as pd
import ccxt
import ccxt.pro as ccxt_pro
import asyncio

# ==============================================================================
# Page Configuration
# ==============================================================================
st.set_page_config(
    page_title="High-Speed Market Scanner & Trade Planner",
    page_icon="⚡",
    layout="wide",
)

st.title('⚡ High-Speed Market Scanner & Trade Planner')
st.caption("From scanning opportunities to generating a full execution plan.")

# ==============================================================================
# Account Connection & State Management
# ==============================================================================

# Initialize session state
if 'connected' not in st.session_state:
    st.session_state.connected = False
if 'usdt_balance' not in st.session_state:
    st.session_state.usdt_balance = 0
if 'api_key' not in st.session_state:
    st.session_state.api_key = ''
if 'api_secret' not in st.session_state:
    st.session_state.api_secret = ''

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
                st.session_state.connected = False
            except Exception as e:
                st.error(f"An error occurred: {e}")
                st.session_state.connected = False

if st.session_state.connected:
    st.sidebar.success(
        f"Connected ✅\n\nBalance: {st.session_state.usdt_balance:,.2f} USDT")
else:
    st.sidebar.warning("Not Connected 🔴")

# ==============================================================================
# Data Fetching & Analysis (Scanner Logic)
# ==============================================================================


async def analyze_symbol(exchange, symbol):
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, '2h', limit=21)
        if len(ohlcv) < 21:
            return None
        df = pd.DataFrame(
            ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        current_candle = df.iloc[-1]
        previous_candle = df.iloc[-2]
        price_change = (
            (current_candle['close'] - previous_candle['close']) / previous_candle['close']) * 100
        average_volume = df.iloc[:-1]['volume'].mean()
        volume_ratio = current_candle['volume'] / \
            average_volume if average_volume > 0 else 0
        pressure = "📈 Buyer" if current_candle['close'] > current_candle['open'] else "📉 Seller"
        price_score = 0
        if abs(price_change) > 6:
            price_score = 50
        elif abs(price_change) > 4:
            price_score = 35
        elif abs(price_change) > 2:
            price_score = 20
        volume_score = 0
        if volume_ratio > 5.0:
            volume_score = 50
        elif volume_ratio > 3.5:
            volume_score = 35
        elif volume_ratio > 2.0:
            volume_score = 20
        confidence_score = price_score + volume_score
        return {'Symbol': symbol, 'Price': current_candle['close'], 'Confidence Score': confidence_score, 'Price Change (1h) %': price_change, 'Volume Ratio': volume_ratio, 'Dominant Pressure': pressure}
    except Exception:
        return None


@st.cache_data(ttl=60)
def run_scanner():
    return asyncio.run(scan_all_markets())


async def scan_all_markets():
    exchange = ccxt_pro.binance({'options': {'defaultType': 'future'}})
    try:
        await exchange.load_markets()
        symbols = [s for s in exchange.symbols if s.endswith(':USDT')]
        tasks = [analyze_symbol(exchange, symbol) for symbol in symbols]
        results = await asyncio.gather(*tasks)
        return pd.DataFrame([res for res in results if res is not None])
    finally:
        await exchange.close()

# ==============================================================================
# Main UI Display & Filtering
# ==============================================================================

st.write("---")
placeholder = st.empty()
placeholder.info(
    "🚀 Starting the high-speed scan... This will take a few seconds.")

try:
    df = run_scanner()
    if not df.empty:
        placeholder.success(f"✅ Scan complete! Analyzed {len(df)} pairs.")
        filter_option = st.radio(
            "Filter Results:", ("Show All", "Show Pump Candidates", "Show Dump Candidates"), horizontal=True)
        df_to_display = df
        if filter_option == "Show Pump Candidates":
            df_to_display = df[(df['Price Change (1h) %'] > 2) & (
                df['Volume Ratio'] > 2.0) & (df['Dominant Pressure'] == '📈 Buyer')]
        elif filter_option == "Show Dump Candidates":
            df_to_display = df[(df['Price Change (1h) %'] < -2) & (
                df['Volume Ratio'] > 2.0) & (df['Dominant Pressure'] == '📉 Seller')]

        if not df_to_display.empty:
            df_sorted = df_to_display.sort_values(
                by='Confidence Score', ascending=False)
            styled_df = df_sorted.style.format({'Price': '{:,.4f}', 'Confidence Score': '{:,.0f}%', 'Price Change (1h) %': '{:.2f}%', 'Volume Ratio': '{:.2f}x'}).background_gradient(
                cmap='Greens', subset=['Confidence Score']).background_gradient(cmap='coolwarm', subset=['Price Change (1h) %']).set_properties(**{'text-align': 'left'})
            st.dataframe(styled_df, width='stretch',
                         height=500, hide_index=True)
        else:
            st.warning("No pairs currently meet the selected filter criteria.")
    else:
        placeholder.warning("Could not retrieve market data.")
except Exception as e:
    placeholder.error("An error occurred during the scan.")
    st.exception(e)

# ==============================================================================
# Trade Execution Planner
# ==============================================================================

st.write("---")
st.header("Trade Execution Planner")

if not df_to_display.empty and st.session_state.connected:
    candidate_symbols = df_to_display['Symbol'].tolist()
    selected_symbol = st.selectbox(
        "Select a Candidate to Plan a Trade:", candidate_symbols)

    if selected_symbol:
        col1, col2 = st.columns(2)
        with col1:
            exchange = ccxt.binance()
            markets = exchange.load_markets()
            min_notional = markets[selected_symbol]['limits']['cost']['min']
            min_risk_percent = (min_notional / st.session_state.usdt_balance) * \
                100 if st.session_state.usdt_balance > 0 else 0.5
            risk_percent = st.slider("Risk per Trade (% of Account)", min_value=float(
                f"{min_risk_percent:.2f}"), max_value=5.0, value=max(float(f"{min_risk_percent:.2f}"), 1.0), step=0.1)

        if st.button("Generate Execution Plan"):
            ohlcv = exchange.fetch_ohlcv(selected_symbol, '1h', limit=2)
            # The candle that just closed and triggered the signal
            signal_candle = ohlcv[0]

            is_pump = df_to_display[df_to_display['Symbol'] ==
                                    selected_symbol]['Dominant Pressure'].iloc[0] == '📈 Buyer'

            if is_pump:
                st.subheader(f"🟢 Long (Buy) Plan for {selected_symbol}")
                # --- NEW: Set entry just above the high of the signal candle ---
                # Entry is signal candle's high + 0.1% buffer
                entry_price = signal_candle[2] * 1.001
                # Stop is signal candle's low - 0.1% buffer
                stop_loss_price = signal_candle[3] * 0.999
                risk_distance = entry_price - stop_loss_price
                tp1_price, tp2_price, tp3_price, tp4_price = entry_price + (risk_distance * 1.5), entry_price + (
                    risk_distance * 2.5), entry_price + (risk_distance * 3.5), entry_price + (risk_distance * 4.5)
            else:  # is_dump
                st.subheader(f"🔴 Short (Sell) Plan for {selected_symbol}")
                # --- NEW: Set entry just below the low of the signal candle ---
                # Entry is signal candle's low - 0.1% buffer
                entry_price = signal_candle[3] * 0.999
                # Stop is signal candle's high + 0.1% buffer
                stop_loss_price = signal_candle[2] * 1.001
                risk_distance = stop_loss_price - entry_price
                tp1_price, tp2_price, tp3_price, tp4_price = entry_price - (risk_distance * 1.5), entry_price - (
                    risk_distance * 2.5), entry_price - (risk_distance * 3.5), entry_price - (risk_distance * 4.5)

            risk_amount_usd = st.session_state.usdt_balance * \
                (risk_percent / 100)
            total_position_size = risk_amount_usd / \
                risk_distance if risk_distance > 0 else 0
            partial_close_size = total_position_size * 0.25

            st.subheader(f"📋 Full Execution Plan for {selected_symbol}")
            st.info(f"**Action:** Place the following orders immediately on Binance.")

            st.markdown("#### Step 1: Initial Entry & Protection")
            c1, c2 = st.columns(2)
            c1.metric(f"**{'🟢 BUY STOP' if is_pump else '🔴 SELL STOP'} Order**", f"${entry_price:,.4f}",
                      delta=f"Size: {total_position_size:,.4f} {selected_symbol.split('/')[0]}")
            c2.metric(f"**STOP-LOSS ({'SELL' if is_pump else 'BUY'})**",
                      f"${stop_loss_price:,.4f}", delta=f"Risk: ${risk_amount_usd:,.2f}")

            st.markdown(
                "#### Step 2: Set 4 Partial Take-Profit Orders (Limit)")
            tp_c1, tp_c2, tp_c3, tp_c4 = st.columns(4)
            tp_c1.metric(
                "TP1 Price", f"${tp1_price:,.4f}", f"Close {partial_close_size:,.4f}")
            tp_c2.metric(
                "TP2 Price", f"${tp2_price:,.4f}", f"Close {partial_close_size:,.4f}")
            tp_c3.metric(
                "TP3 Price", f"${tp3_price:,.4f}", f"Close {partial_close_size:,.4f}")
            tp_c4.metric(
                "TP4 Price", f"${tp4_price:,.4f}", f"Close {partial_close_size:,.4f}")

            st.success(
                f"**💡 IMPORTANT RULE:** When the price hits **TP2 (${tp2_price:,.4f})**, immediately cancel your original Stop-Loss and place a new one at your entry price **(${entry_price:,.4f})**. This guarantees a risk-free trade.")

elif not st.session_state.connected:
    st.warning("Please connect to your Binance account to generate a trade plan.")
else:
    st.info("Waiting for scanner results to populate the planner.")
