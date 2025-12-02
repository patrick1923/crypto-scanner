import sqlite3
import pandas as pd
from datetime import datetime, timedelta

DB_FILE = "scanner_data.db"


def create_tables():
    """Create tables for 2H signals, popup history, and position logs."""
    with sqlite3.connect(DB_FILE) as conn:
        # --- 2H / SCANNER SIGNALS TABLE (original) ---
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_time TEXT NOT NULL,
                symbol TEXT NOT NULL,
                signal_type TEXT NOT NULL,   -- Pump / Dump / etc.
                signal_price REAL,
                grade TEXT,
                analysis TEXT,
                price_change_2h REAL,
                volume_ratio_2h REAL,
                volume_24h REAL,
                volatility_contraction INTEGER,
                dominant_pressure TEXT,
                outcome TEXT,
                notes TEXT,
                status TEXT DEFAULT 'Active'
            )
        """)

        # --- POPUP SIGNALS HISTORY TABLE (new) ---
        conn.execute("""
            CREATE TABLE IF NOT EXISTS popup_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                popup_time TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,           -- buy / sell
                grade TEXT,
                analysis TEXT,
                entry_price REAL,
                stop_loss REAL,
                tp1 REAL,
                tp2 REAL,
                size REAL,
                whale_score INTEGER,
                signal_origin TEXT,          -- '2H' or '1M' etc.
                outcome TEXT DEFAULT 'Pending',  -- Win / Loss / BE / Skipped
                notes TEXT DEFAULT ''
            )
        """)

        # --- POSITIONS LOG TABLE (same as before) ---
        conn.execute("""
            CREATE TABLE IF NOT EXISTS positions_log (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                log_time TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT,
                size REAL,
                entry_price REAL,
                mark_price REAL,
                unrealized_pnl REAL,
                entry_time_ksa TEXT
            )
        """)


# -------------------------------------------------------------------
# 2H / scanner signals logging  (used by local_scanner_v2.py)
# -------------------------------------------------------------------
def log_signals(signals_df, signal_type):
    """
    Logs new 2H scanner signals to the `signals` table using KSA time.
    This is the original function that local_scanner_v2.py calls.
    """
    if signals_df is None or signals_df.empty:
        return

    ksa_time = datetime.utcnow() + timedelta(hours=3)
    scan_time = ksa_time.strftime('%Y-%m-%d %H:%M:%S')

    records_to_insert = []
    for _, row in signals_df.iterrows():
        records_to_insert.append((
            scan_time,
            row['Symbol'],
            signal_type,
            row.get('Price', None),
            row.get('Grade', None),
            row.get('Analysis', None),
            row.get('Price Change (2h) %', None),
            row.get('Volume Ratio (2h)', None),
            row.get('24h Volume', None),
            1 if row.get('Volatility Contraction', False) else 0,
            row.get('Dominant Pressure', None),
            None,    # outcome
            None,    # notes
            'Active'
        ))

    with sqlite3.connect(DB_FILE) as conn:
        conn.executemany("""
            INSERT INTO signals (
                scan_time, symbol, signal_type, signal_price, grade, analysis,
                price_change_2h, volume_ratio_2h, volume_24h,
                volatility_contraction, dominant_pressure, outcome, notes, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, records_to_insert)
        conn.commit()


def get_recent_signals(limit=50):
    """Fetch latest active 2H signals."""
    with sqlite3.connect(DB_FILE) as conn:
        query = """
            SELECT *
            FROM signals
            WHERE status = 'Active'
            ORDER BY datetime(scan_time) DESC
            LIMIT ?
        """
        return pd.read_sql_query(query, conn, params=(limit,))


def mark_signal_expired(symbol):
    """Marks a 2H signal as expired by symbol."""
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
            UPDATE signals
            SET status = 'Expired'
            WHERE symbol = ? AND status = 'Active'
        """, (symbol,))
        conn.commit()


# -------------------------------------------------------------------
# Popup history logging (every popup you see)
# -------------------------------------------------------------------
def log_popup_signal(signal_dict):
    """
    Records EVERY popup into the popup_signals table.

    signal_dict expected keys:
      symbol, side, grade, analysis,
      entry_price, stop_loss, tp1, tp2, size,
      whale_score, signal_origin
    """
    ksa_time = datetime.utcnow() + timedelta(hours=3)
    popup_time = ksa_time.strftime('%Y-%m-%d %H:%M:%S')

    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
            INSERT INTO popup_signals (
                popup_time, symbol, side, grade, analysis,
                entry_price, stop_loss, tp1, tp2, size,
                whale_score, signal_origin, outcome, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            popup_time,
            signal_dict.get("symbol"),
            signal_dict.get("side"),
            signal_dict.get("grade"),
            signal_dict.get("analysis"),
            signal_dict.get("entry_price"),
            signal_dict.get("stop_loss"),
            signal_dict.get("tp1"),
            signal_dict.get("tp2"),
            signal_dict.get("size"),
            signal_dict.get("whale_score", 0),
            signal_dict.get("signal_origin", "Unknown"),
            "Pending",
            ""
        ))
        conn.commit()


def update_outcome(id_value, outcome, notes=""):
    """
    Manually set WIN / LOSS / BE / SKIPPED plus notes for a popup signal.
    """
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
            UPDATE popup_signals
            SET outcome = ?, notes = ?
            WHERE id = ?
        """, (outcome, notes, id_value))
        conn.commit()


def get_popup_history(limit=200):
    """Return popup signals sorted by newest first."""
    with sqlite3.connect(DB_FILE) as conn:
        query = """
            SELECT *
            FROM popup_signals
            ORDER BY datetime(popup_time) DESC
            LIMIT ?
        """
        return pd.read_sql_query(query, conn, params=(limit,))


# -------------------------------------------------------------------
# Positions log (unchanged)
# -------------------------------------------------------------------
def log_position_snapshot(positions_df):
    """Logs open positions to the positions_log table."""
    if positions_df is None or positions_df.empty:
        return

    ksa_time = datetime.utcnow() + timedelta(hours=3)
    log_time = ksa_time.strftime('%Y-%m-%d %H:%M:%S')

    records_to_insert = []
    for _, row in positions_df.iterrows():
        records_to_insert.append((
            log_time,
            row['Symbol'],
            row['Side'],
            row.get('Size', 0),
            row.get('Entry Price', 0),
            row.get('Mark Price', 0),
            row.get('Unrealized PnL', 0),
            row.get('Entry Time (KSA)', 'N/A')
        ))

    with sqlite3.connect(DB_FILE) as conn:
        conn.executemany("""
            INSERT INTO positions_log (
                log_time, symbol, side, size, entry_price,
                mark_price, unrealized_pnl, entry_time_ksa
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, records_to_insert)
        conn.commit()
