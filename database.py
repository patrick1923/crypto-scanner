import sqlite3
from datetime import datetime

conn = sqlite3.connect("liquidity_radar.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS liquidity_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_time TEXT,
    symbol TEXT,
    price REAL,
    signal TEXT,
    score INTEGER,
    funding REAL,
    volume_ratio REAL,
    volatility_ratio REAL,
    target REAL,
    distance REAL,
    trade_taken INTEGER DEFAULT 0,
    trade_result TEXT DEFAULT NULL
)
""")

conn.commit()


def log_liquidity_context(symbol, price, signal, score, funding,
                          volume_ratio, volatility_ratio,
                          target, distance, scan_time):

    cursor.execute("""
        INSERT INTO liquidity_logs
        (scan_time, symbol, price, signal, score, funding,
         volume_ratio, volatility_ratio, target, distance)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        scan_time,
        symbol,
        price,
        signal,
        score,
        funding,
        volume_ratio,
        volatility_ratio,
        target,
        distance
    ))

    conn.commit()