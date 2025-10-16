import streamlit as st
import pandas as pd
import database as db

# --- Page 3: Historical Analysis ---

st.set_page_config(page_title="Signal History", page_icon="📈", layout="wide")
st.title("📈 Historical Signal Analysis")
st.caption("Review past signals and log your trade outcomes.")

if st.button("🔄 Refresh History"):
    st.rerun()

historical_df = db.get_historical_signals()

if not historical_df.empty:
    with st.form(key='outcome_form'):
        st.subheader("📝 Log Trade Outcome")
        col1, col2, col3 = st.columns(3)
        with col1:
            signal_id_to_update = st.selectbox("Select Signal ID", historical_df.sort_values(
                'id', ascending=False)['id'].tolist())
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
