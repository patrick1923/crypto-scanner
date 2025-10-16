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
    # --- LOGGING FORM (uses all signals) ---
    with st.form(key='outcome_form'):
        st.subheader("📝 Log Trade Outcome")
        col1, col2, col3 = st.columns(3)
        with col1:
            # The form still needs all IDs, so you can log a loss on a bad trade
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

    # --- NEW: Filter Logic ---
    show_all_grades = st.checkbox(
        "Show all signals (including C and F grades)")

    if show_all_grades:
        df_to_display = historical_df
    else:
        # By default, only show the tradable grades
        tradable_grades = ['A+ (Explosive)', 'A (Prime)',
                           'A (High Volume)', 'B+ (Noisy)', 'B (Weak)']
        df_to_display = historical_df[historical_df['grade'].isin(
            tradable_grades)]

    # --- Display the (now filtered) data table ---
    display_columns = [
        'id', 'scan_time', 'symbol', 'signal_type', 'signal_price',
        'grade', 'analysis', 'price_change_2h', 'volume_ratio_2h',
        'volatility_contraction', 'outcome', 'notes'
    ]
    display_columns = [
        col for col in display_columns if col in df_to_display.columns]
    st.dataframe(df_to_display[display_columns], width='stretch')

    st.subheader("Summary Charts")
    st.caption("Charts will reflect the filter above.")

    chart_col1, chart_col2, chart_col3 = st.columns(3)
    with chart_col1:
        st.write("Signals by Time of Day (KSA)")
        df_to_display['scan_time'] = pd.to_datetime(df_to_display['scan_time'])
        df_to_display['hour'] = df_to_display['scan_time'].dt.hour
        signal_counts_by_hour = df_to_display['hour'].value_counts(
        ).sort_index()
        st.bar_chart(signal_counts_by_hour)

    with chart_col2:
        st.write("Top 10 Most Frequent Pairs")
        signal_counts_by_pair = df_to_display['symbol'].value_counts().head(10)
        st.bar_chart(signal_counts_by_pair)

    with chart_col3:
        st.write("Signal Grade Distribution")
        signal_counts_by_grade = df_to_display['grade'].value_counts()
        st.bar_chart(signal_counts_by_grade)

    if st.button("Clear Signal History"):
        db.clear_database()
        st.toast("Historical data has been cleared.")
        st.rerun()
else:
    st.info("No historical data yet. Run a few scans to start collecting data.")
