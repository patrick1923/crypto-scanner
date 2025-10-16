import streamlit as st
import ccxt
import pandas as pd

st.set_page_config(page_title="Trade Planner", page_icon="📋", layout="wide")
st.title("📋 Trade Execution Planner")
st.caption(
    "Plan your trade based on the latest signals found by the automated worker.")

if 'latest_signals' not in st.session_state or st.session_state.latest_signals.empty:
    st.warning(
        "No signals found in the latest scan. Please check back after the next automated run.")
    st.stop()

pump_candidates = st.session_state.get('pump_candidates', pd.DataFrame())
dump_candidates = st.session_state.get('dump_candidates', pd.DataFrame())

filter_option = st.radio("Show Candidates For:",
                         ("Pumps", "Dumps"), horizontal=True)

if filter_option == "Pumps":
    df_to_display = pump_candidates
    if df_to_display.empty:
        st.info("No Pump candidates found in the last scan.")
        st.stop()
else:
    df_to_display = dump_candidates
    if df_to_display.empty:
        st.info("No Dump candidates found in the last scan.")
        st.stop()

if not df_to_display.empty and st.session_state.get('connected', False):
    candidate_symbols = df_to_display['symbol'].tolist()
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

                is_pump = df_to_display[df_to_display['symbol'] ==
                                        selected_symbol]['signal_type'].iloc[0] == 'Pump'

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
elif not st.session_state.get('connected', False):
    st.warning("Please connect to your Binance account to generate a trade plan.")
else:
    st.info("No valid candidates found in the latest scan.")
