import streamlit as st
import pandas as pd
import ccxt
from datetime import datetime, timedelta
import database as db

st.set_page_config(page_title="Scanner Dashboard",
                   page_icon="⚡", layout="wide")
st.title('⚡ High-Speed Market Scanner')
st.caption("Displaying the latest signals found by the 24/7 automated worker.")

if 'connected' not in st.session_state:
    st.session_state.connected = False
if 'usdt_balance' not in st.session_state:
    st.session_state.usdt_balance = 0
if 'api_key' not in st.session_state:
    st.session_state.api_key = ''
if 'api_secret' not in st.session_state:
    st.session_state.api_secret = ''
if 'latest_signals' not in st.session_state:
    st.session_state.latest_signals = pd.DataFrame()
if 'pump_candidates' not in st.session_state:
    st.session_state.pump_candidates = pd.DataFrame()
if 'dump_candidates' not in st.session_state:
    st.session_state.dump_candidates = pd.DataFrame()

try:
    API_KEY = st.secrets["API_KEY"]
    API_SECRET = st.secrets["API_SECRET"]
    st.session_state.api_key = API_KEY
    st.session_state.api_secret = API_SECRET
except:
    API_KEY = ''
    API_SECRET = ''

if 'connected' not in st.session_state:
    if API_KEY and API_SECRET:
        try:
            exchange = ccxt.binance(
                {'apiKey': API_KEY, 'secret': API_SECRET, 'options': {'defaultType': 'future'}})
            balance = exchange.fetch_balance()
            usdt_balance = balance['USDT']['total']
            st.session_state.usdt_balance = usdt_balance
            st.session_state.connected = True
        except Exception as e:
            st.session_state.connected = False
            st.error(f"Failed to auto-connect using Secrets: {e}")
    else:
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
    except Exception:
        return None, None


positions_df, health_data = None, None
if st.session_state.connected:
    positions_df, health_data = fetch_account_data()


@st.cache_data(ttl=60)
def get_latest_scan_data():
    all_signals = db.get_historical_signals()
    if all_signals.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), None

    all_signals['scan_time'] = pd.to_datetime(all_signals['scan_time'])
    latest_scan_time = all_signals['scan_time'].max()
    latest_signals = all_signals[all_signals['scan_time'] == latest_scan_time]

    pumps = latest_signals[latest_signals['signal_type'] == 'Pump']
    dumps = latest_signals[latest_signals['signal_type'] == 'Dump']
    return latest_signals, pumps, dumps, latest_scan_time


latest_signals, pump_candidates, dump_candidates, last_scan_time = get_latest_scan_data()
st.session_state.latest_signals = latest_signals
st.session_state.pump_candidates = pump_candidates
st.session_state.dump_candidates = dump_candidates

num_pumps, num_dumps = 0, 0
if not pump_candidates.empty:
    num_pumps = len(
        pump_candidates[pump_candidates['volatility_contraction'] == 1])
if not dump_candidates.empty:
    num_dumps = len(
        dump_candidates[dump_candidates['volatility_contraction'] == 1])

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

if last_scan_time:
    st.success(
        f"Displaying latest scan results from: {last_scan_time.strftime('%Y-%m-%d %H:%M:%S')} (KSA Time)")
else:
    st.info("The automated worker has not run yet. Data will appear here after the next scheduled scan.")

if not latest_signals.empty:
    filter_option = st.radio("Filter Results:", ("Show All",
                             "Show Pump Candidates", "Show Dump Candidates"), horizontal=True)

    df_to_display = latest_signals
    if filter_option == "Show Pump Candidates":
        df_to_display = pump_candidates
    elif filter_option == "Show Dump Candidates":
        df_to_display = dump_candidates

    if not df_to_display.empty:
        display_columns = ['symbol', 'signal_price', 'confidence_score', 'price_change_2h',
                           'volume_ratio_2h', 'volume_24h', 'volatility_contraction', 'dominant_pressure']
        df_sorted = df_to_display.sort_values(
            by='confidence_score', ascending=False)
        styled_df = df_sorted[display_columns].style.format({
            'signal_price': '{:,.4f}',
            'confidence_score': '{:,.0f}%',
            'price_change_2h': '{:.2f}%',
            'volume_ratio_2h': '{:.2f}x',
            'volume_24h': '{:,.0f} USDT',
            'volatility_contraction': lambda v: "✅ Yes" if v == 1 else "❌ No"
        }).background_gradient(cmap='Greens', subset=['confidence_score']).background_gradient(cmap='coolwarm', subset=['price_change_2h'])
        st.dataframe(styled_df, width='stretch', height=500, hide_index=True)
    else:
        st.warning(
            "No pairs currently meet the selected filter criteria for this scan.")
