import streamlit as st
import pandas as pd
import ccxt
import ccxt.pro as ccxt_pro
import asyncio
from datetime import timezone
import database as db

db.create_table()

st.set_page_config(
    page_title="High-Speed Market Scanner & Trade Planner",
    page_icon="⚡",
    layout="wide",
)

st.title('⚡ High-Speed Market Scanner & Trade Planner')
st.caption("Now with historical data logging and analysis.")

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
    except Exception as e:
        st.sidebar.error(f"Failed to fetch account data.")
        return None, None


positions_df, health_data = None, None
if st.session_state.connected:
    positions_df, health_data = fetch_account_data()
    with st.sidebar:
        st.success(
            f"Connected ✅\n\nBalance: {st.session_state.usdt_balance:,.2f} USDT")
        if health_data:
            st.metric("Maintenance Margin",
                      f"${health_data['maint_margin']:,.2f}")
            st.metric("Margin Ratio", f"{health_data['margin_ratio']:.2f}%")
            if health_data['margin_ratio'] > 80:
                st.warning("⚠️ Margin Ratio is high! Risk of liquidation.")
            else:
                st.info("Margin Ratio is safe.")
else:
    st.sidebar.warning("Not Connected 🔴")


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

placeholder = st.empty()
with placeholder.container():
    st.info("🚀 Starting the high-speed scan... This will take a few seconds.")
df = run_scanner()

num_pumps, num_dumps = 0, 0
if not df.empty:
    pump_candidates = df[(df['Price Change (2h) %'] > 2) & (df['Volume Ratio (2h)'] > 2.0) & (
        df['Dominant Pressure'] == '📈 Buyer') & (df['High 24h Volume'] == True)]
    dump_candidates = df[(df['Price Change (2h) %'] < -2) & (df['Volume Ratio (2h)'] > 2.0)
                         & (df['Dominant Pressure'] == '📉 Seller') & (df['High 24h Volume'] == True)]
    num_pumps = len(
        pump_candidates[pump_candidates['Volatility Contraction'] == True])
    num_dumps = len(
        dump_candidates[dump_candidates['Volatility Contraction'] == True])
    if not pump_candidates.empty:
        db.log_signals(pump_candidates, 'Pump')
    if not dump_candidates.empty:
        db.log_signals(dump_candidates, 'Dump')

with st.sidebar:
    st.write("---")
    st.subheader("🌡️ Market Pulse (A+ Setups)")
    st.metric("Pump Signals Found", f"{num_pumps} 🟢")
    st.metric("Dump Signals Found", f"{num_dumps} 🔴")

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

if not df.empty:
    placeholder.success(f"✅ Scan complete! Analyzed {len(df)} pairs.")
    filter_option = st.radio("Filter Results:", ("Show All",
                             "Show Pump Candidates", "Show Dump Candidates"), horizontal=True)
    if filter_option == "Show Pump Candidates":
        df_to_display = pump_candidates
    elif filter_option == "Show Dump Candidates":
        df_to_display = dump_candidates
    else:
        df_to_display = df
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
else:
    placeholder.error("An error occurred during the scan.")

st.write("---")
st.header("Trade Execution Planner")
if not df.empty and not df_to_display.empty and st.session_state.connected:
    candidate_symbols = df_to_display['Symbol'].tolist()
    selected_symbol = st.selectbox(
        "Select a Candidate to Plan a Trade:", candidate_symbols)
    if selected_symbol:
        risk_percent = st.slider(
            "Risk per Trade (% of Account)", min_value=0.5, max_value=5.0, value=1.0, step=0.1)
        if st.button("Generate Execution Plan"):
            with st.spinner("Fetching latest data for plan..."):
                exchange = ccxt.binance()
                ticker = exchange.fetch_ticker(selected_symbol)
                current_live_price = ticker['last']
                ohlcv = exchange.fetch_ohlcv(selected_symbol, '2h', limit=2)
                signal_candle = ohlcv[0]
                signal_price = signal_candle[4]
                is_pump = df_to_display[df_to_display['Symbol'] ==
                                        selected_symbol]['Dominant Pressure'].iloc[0] == '📈 Buyer'
                st.subheader(f"Price Analysis for {selected_symbol}")
                price_col1, price_col2 = st.columns(2)
                price_col1.metric(
                    "Signal Price", f"${signal_price:,.4f}", help="Price at the close of the 2-hour signal candle.")
                price_col2.metric("Current Live Price", f"${current_live_price:,.4f}",
                                  delta=f"{((current_live_price-signal_price)/signal_price)*100:.2f}% since signal", delta_color="normal")
                if is_pump:
                    entry_price = signal_candle[2] * 1.001
                    stop_loss_price = signal_candle[3] * 0.999
                    risk_distance = entry_price - stop_loss_price
                    tp1_price = entry_price + (risk_distance * 1.5)
                    tp2_price = entry_price + (risk_distance * 2.5)
                    tp3_price = entry_price + (risk_distance * 3.5)
                    tp4_price = entry_price + (risk_distance * 4.5)
                else:
                    entry_price = signal_candle[3] * 0.999
                    stop_loss_price = signal_candle[2] * 1.001
                    risk_distance = stop_loss_price - entry_price
                    tp1_price = max(0, entry_price - (risk_distance * 1.5))
                    tp2_price = max(0, entry_price - (risk_distance * 2.5))
                    tp3_price = max(0, entry_price - (risk_distance * 3.5))
                    tp4_price = max(0, entry_price - (risk_distance * 4.5))
                risk_amount_usd = st.session_state.usdt_balance * \
                    (risk_percent / 100)
                total_position_size = risk_amount_usd / \
                    risk_distance if risk_distance > 0 else 0
                partial_close_size = total_position_size * 0.25
                st.subheader(f"📋 Full Execution Plan for {selected_symbol}")
                st.info(
                    f"**Action:** Place the following orders immediately on Binance.")
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
elif not df.empty and not st.session_state.connected:
    st.warning("Please connect to your Binance account to generate a trade plan.")
else:
    st.info("Waiting for scanner results to populate the planner.")

st.write("---")
st.header("📈 Historical Signal Analysis")
historical_df = db.get_historical_signals()
if not historical_df.empty:
    with st.form(key='outcome_form'):
        st.subheader("📝 Log Trade Outcome")
        col1, col2, col3 = st.columns(3)
        with col1:
            signal_id_to_update = st.selectbox(
                "Select Signal ID", historical_df['id'].tolist())
        with col2:
            trade_outcome = st.selectbox(
                "Outcome", ["", "Win", "Loss", "Breakeven"])
        with col3:
            trade_notes = st.text_input("Notes (e.g., Hit TP2, Moved to BE)")
        submitted = st.form_submit_button("Save Outcome")
        if submitted:
            if signal_id_to_update and trade_outcome:
                db.update_signal_outcome(
                    signal_id_to_update, trade_outcome, trade_notes)
                st.toast(f"Outcome for signal ID {signal_id_to_update} saved!")
                st.rerun()
            else:
                st.warning("Please select a Signal ID and an Outcome.")
    st.subheader("Full Signal History")
    st.dataframe(historical_df, width='stretch')
    st.subheader("Summary Charts")
    chart_col1, chart_col2 = st.columns(2)
    with chart_col1:
        st.write("Signals by Time of Day (KSA)")
        historical_df['scan_time'] = pd.to_datetime(historical_df['scan_time'])
        historical_df['hour'] = historical_df['scan_time'].dt.hour
        signal_counts_by_hour = historical_df['hour'].value_counts(
        ).sort_index()
        st.bar_chart(signal_counts_by_hour)
    with chart_col2:
        st.write("Top 10 Most Frequent Pairs")
        signal_counts_by_pair = historical_df['symbol'].value_counts().head(10)
        st.bar_chart(signal_counts_by_pair)
    if st.button("Clear Signal History"):
        db.clear_database()
        st.toast("Historical data has been cleared.")
        st.rerun()
else:
    st.info("No historical data yet. Run a few scans to start collecting data.")
