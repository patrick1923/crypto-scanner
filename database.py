import sqlite3
import pandas as pd
from datetime import datetime, timedelta

DB_FILE = "scanner_data.db"


def create_tables():
    """Creates both the signals and positions_log tables if they don't exist."""
    with sqlite3.connect(DB_FILE) as conn:
        # Signals Table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY,
                scan_time TEXT NOT NULL,
                symbol TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                signal_price REAL,
                grade TEXT,
                analysis TEXT,
                price_change_2h REAL,
                volume_ratio_2h REAL,
                volume_24h REAL,
                volatility_contraction INTEGER,
                dominant_pressure TEXT,
                outcome TEXT,
                notes TEXT
            )
        """)
        # Positions Log Table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS positions_log (
                log_id INTEGER PRIMARY KEY,
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


def log_signals(signals_df, signal_type):
    """Logs a DataFrame of new signals to the database using KSA time."""
    ksa_time = datetime.utcnow() + timedelta(hours=3)
    scan_time = ksa_time.strftime('%Y-%m-%d %H:%M:%S')

    records_to_insert = []
    for _, row in signals_df.iterrows():
        records_to_insert.append((
            scan_time, row['Symbol'], signal_type, row['Price'],
            row['Grade'], row['Analysis'], row['Price Change (2h) %'],
            row['Volume Ratio (2h)'], row['24h Volume'],
            1 if row['Volatility Contraction'] else 0,
            row['Dominant Pressure']
        ))

    with sqlite3.connect(DB_FILE) as conn:
        conn.executemany("""
            INSERT INTO signals (
                scan_time, symbol, signal_type, signal_price, grade, analysis,
                price_change_2h, volume_ratio_2h, volume_24h,
                volatility_contraction, dominant_pressure
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, records_to_insert)


def log_position_snapshot(positions_df):
    """Logs the current state of open positions to the positions_log table."""
    if positions_df is None or positions_df.empty:
        return  # Don't log if there are no positions or an error occurred

    ksa_time = datetime.utcnow() + timedelta(hours=3)
    log_time = ksa_time.strftime('%Y-%m-%d %H:%M:%S')

    records_to_insert = []
    for _, row in positions_df.iterrows():
        records_to_insert.append((
            log_time,
            row['Symbol'],
            row['Side'],
            row.get('Size', 0),  # Use .get for safety
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
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, records_to_insert)
    # print(f"Logged {len(records_to_insert)} open positions at
