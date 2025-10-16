# database.py
import sqlite3
import pandas as pd
from datetime import datetime, timedelta

DB_FILE = "scanner_data.db"


def create_table():
    """Creates the signals table with all the necessary columns."""
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY,
                scan_time TEXT NOT NULL,
                symbol TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                signal_price REAL,
                confidence_score REAL,
                price_change_2h REAL,
                volume_ratio_2h REAL,
                volume_24h REAL,
                volatility_contraction INTEGER,
                dominant_pressure TEXT,
                outcome TEXT,
                notes TEXT
            )
        """)


def log_signals(signals_df, signal_type):
    """Logs a DataFrame of new signals to the database using KSA time."""
    # --- MODIFIED: Convert UTC to KSA time (UTC+3) ---
    ksa_time = datetime.utcnow() + timedelta(hours=3)
    scan_time = ksa_time.strftime('%Y-%m-%d %H:%M:%S')

    records_to_insert = []
    for _, row in signals_df.iterrows():
        records_to_insert.append((
            scan_time,
            row['Symbol'],
            signal_type,
            row['Price'],
            row['Confidence Score'],
            row['Price Change (2h) %'],
            row['Volume Ratio (2h)'],
            row['24h Volume'],
            1 if row['Volatility Contraction'] else 0,
            row['Dominant Pressure']
        ))

    with sqlite3.connect(DB_FILE) as conn:
        conn.executemany("""
            INSERT INTO signals (
                scan_time, symbol, signal_type, signal_price, confidence_score, 
                price_change_2h, volume_ratio_2h, volume_24h, 
                volatility_contraction, dominant_pressure
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, records_to_insert)


def get_historical_signals():
    """Fetches all historical signals from the database."""
    with sqlite3.connect(DB_FILE) as conn:
        df = pd.read_sql_query("SELECT * FROM signals", conn)
    return df


def clear_database():
    """Deletes all records from the signals table."""
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("DELETE FROM signals")
