import streamlit as st
import pandas as pd
import database as db

# ----- Page Setup -----
st.set_page_config(page_title="Signal History", page_icon="📈", layout="wide")
st.title("📈 Historical Signal Analysis")
st.caption("Review past signals and log your trade outcomes.")

# ----- Refresh Button -----
if st.button("🔄 Refresh History"):
    st.rerun()

# ----- Load Historical Signal Data -----
historical_df = db.get_historical_signals()

if not historical_df.empty:
    # Sort signals by ID (latest on top)
    historical_df = historical_df.sort_values(by="id", ascending=False)

    # ----- Outcome Logging Form -----
    with st.form(key="outcome_form"):
        st.subheader("📝 Log Trade Outcome")
        col1, col2, col3 = st.columns(3)

        with col1:
            signal_id_to_update = st.selectbox(
                "Select Signal ID",
                historical_df['id'].tolist()
            )

        with col2:
            trade_outcome = st.selectbox(
                "Outcome", ["", "Win", "Loss", "Breakeven"]
            )

        with col3:
            trade_notes = st.text_input(
                "Notes (e.g., Hit TP2, moved SL to BE)")

        submitted = st.form_submit_button("Save Outcome")
        if submitted:
            if signal_id_to_update and trade_outcome:
                db.update_signal_outcome(
                    signal_id_to_update, trade_outcome, trade_notes)
                st.success(
                    f"✅ Outcome saved for Signal ID {signal_id_to_update}")
                st.rerun()
            else:
                st.warning("⚠️ Please select a Signal ID and an Outcome.")

    # ----- Display Table -----
    st.subheader("📊 Full Signal History")

    show_all_grades = st.checkbox(
        "Show all signals (including C and F grades)", value=False)

    if not show_all_grades:
        tradable_grades = ['A+ (Explosive)', 'A (Prime)',
                           'A (High Volume)', 'B+ (Noisy)', 'B (Weak)']
        df_to_display = historical_df[historical_df['grade'].isin(
            tradable_grades)].copy()
    else:
        df_to_display = historical_df.copy()

    display_columns = [
        'id', 'scan_time', 'symbol', 'signal_type', 'signal_price',
        'grade', 'analysis', 'price_change_2h', 'volume_ratio_2h',
        'volatility_contraction', 'outcome', 'notes'
    ]
    df_to_display = df_to_display[[
        col for col in display_columns if col in df_to_display.columns]]

    st.dataframe(df_to_display, use_container_width=True)

    # ----- Charts -----
    st.subheader("📈 Summary Charts")
    st.caption("Charts reflect only filtered signals above.")

    df_to_display['scan_time'] = pd.to_datetime(
        df_to_display['scan_time'], errors='coerce')
    df_to_display['hour'] = df_to_display['scan_time'].dt.hour

    col1, col2, col3 = st.columns(3)

    with col1:
        st.write("⏳ Signals by Time of Day (KSA)")
        st.bar_chart(df_to_display['hour'].value_counts().sort_index())

    with col2:
        st.write("💱 Top 10 Most Frequent Pairs")
        st.bar_chart(df_to_display['symbol'].value_counts().head(10))

    with col3:
        st.write("🏷️ Signal Grade Distribution")
        st.bar_chart(df_to_display['grade'].value_counts())

    # ----- Clear Database -----
    if st.button("🗑️ Clear Signal History"):
        db.clear_database()
        st.warning("✅ All historical signals have been cleared.")
        st.rerun()

else:
    st.info("No historical data yet. Run a few scans to build your signal history.")
