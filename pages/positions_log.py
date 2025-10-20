import streamlit as st
import pandas as pd
import database as db

# --- Page 4: Positions Log ---

st.set_page_config(page_title="Positions Log", page_icon="📓", layout="wide")
st.title("📓 Live Positions Log")
st.caption(
    "A historical snapshot of your open positions every time the main page is loaded/refreshed.")

if st.button("🔄 Refresh Log"):
    st.rerun()

positions_log_df = db.get_positions_log()

if not positions_log_df.empty:
    st.dataframe(positions_log_df, width='stretch')

    if st.button("Clear Positions Log History"):
        db.clear_database(table_name="positions_log")
        st.toast("Positions log data has been cleared.")
        st.rerun()
else:
    st.info("No position history has been logged yet. Open the main 'app' page while connected to start logging.")
