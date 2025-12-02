# early_scanner.py
# Real-time style 1m "Early Pump" scanner for Binance Futures

import asyncio
from datetime import timezone
import pandas as pd
import ccxt
import ccxt.pro as ccxt_pro


async def _get_top_usdt_symbols(exchange, limit=60):
    """
    Get top-USDT futures symbols by 24h quote volume.
    This keeps the early scanner fast and focused on big movers.
    """
    all_tickers = await exchange.fetch_tickers()
    rows = []
    for symbol, t in all_tickers.items():
        # Futures symbols end with ':USDT' in your setup
        if symbol.endswith(':USDT'):
            quote_volume = t.get('quoteVolume') or 0
            rows.append((symbol, quote_volume))
    if not rows:
        return []

    df = pd.DataFrame(rows, columns=['Symbol', 'QuoteVolume'])
    df = df.sort_values('QuoteVolume', ascending=False)
    top = df.head(limit)['Symbol'].tolist()
    return top


async def _analyze_symbol_1m_early(exchange, symbol,
                                   min_price_move_pct=0.35,
                                   min_volume_ratio=2.0):
    """
    1-minute 'early' detector:

    - Uses last ~40 candles of 1m data
    - Compares last 20 candles vs prior 20 for compression
    - Checks last candle for:
        * Price change >= min_price_move_pct
        * Volume ratio >= min_volume_ratio
    """
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, '1m', limit=40)
        if len(ohlcv) < 25:
            return None

        df = pd.DataFrame(
            ohlcv, columns=['timestamp', 'open',
                            'high', 'low', 'close', 'volume']
        )

        # Split into "old range" and "recent compressed range"
        old_window = df.iloc[-40:-20]
        recent_window = df.iloc[-21:-1]  # 20 candles before the last
        last = df.iloc[-1]

        if old_window.empty or recent_window.empty:
            return None

        old_range_avg = (old_window['high'] - old_window['low']).mean()
        recent_range_avg = (
            recent_window['high'] - recent_window['low']).mean()
        recent_vol_avg = recent_window['volume'].mean()

        if old_range_avg <= 0 or recent_vol_avg <= 0:
            return None

        # Compression: recent range significantly smaller than old range
        is_compressed = recent_range_avg < (old_range_avg * 0.7)

        # Compare last close to previous close (previous 1m candle)
        prev_close = recent_window['close'].iloc[-1]
        price_change_pct = ((last['close'] - prev_close) / prev_close) * 100

        # Volume ratio: last 1m vs avg of last 20m
        vol_ratio = last['volume'] / \
            recent_vol_avg if recent_vol_avg > 0 else 0

        # Require both price + volume explosion
        if abs(price_change_pct) < min_price_move_pct:
            return None
        if vol_ratio < min_volume_ratio:
            return None

        pressure = "📈 Buyer" if last['close'] > last['open'] else "📉 Seller"
        signal_ts = pd.to_datetime(
            last['timestamp'], unit='ms').tz_localize(timezone.utc)

        # Optional: simple early-grade label
        early_grade = "EP+" if vol_ratio >= 3.5 else "EP"

        return {
            'Symbol': symbol,
            'Price': last['close'],
            'Signal Time': signal_ts,
            'Price Change (1m) %': price_change_pct,
            'Volume Ratio (1m)': vol_ratio,
            'Volatility Compression (20 vs 20)': is_compressed,
            'Dominant Pressure': pressure,
            'Early Grade': early_grade,
        }
    except Exception as e:
        # Be quiet on most symbols; just debug when needed
        print(f"[EarlyScanner] Error analyzing {symbol}: {e}")
        return None


async def scan_early_pumps_async(limit_symbols=60,
                                 min_price_move_pct=0.35,
                                 min_volume_ratio=2.0):
    """
    Core async function: scans top-volume USDT futures for early pumps/dumps
    using 1m data.
    """
    exchange = ccxt_pro.binance({'options': {'defaultType': 'future'}})
    try:
        await exchange.load_markets()
        symbols = await _get_top_usdt_symbols(exchange, limit=limit_symbols)
        if not symbols:
            return pd.DataFrame()

        print(
            f"[EarlyScanner] Scanning {len(symbols)} top-volume futures pairs (1m)...")

        tasks = [
            _analyze_symbol_1m_early(
                exchange,
                s,
                min_price_move_pct=min_price_move_pct,
                min_volume_ratio=min_volume_ratio,
            )
            for s in symbols
        ]
        results = await asyncio.gather(*tasks)

        df = pd.DataFrame([r for r in results if r is not None])
        if df.empty:
            return df

        # Sort by strongest early effects
        df['Abs Price Change'] = df['Price Change (1m) %'].abs()
        df['Score'] = df['Abs Price Change'] * df['Volume Ratio (1m)']
        df = df.sort_values('Score', ascending=False)

        return df
    finally:
        await exchange.close()


def scan_early_pumps(limit_symbols=60,
                     min_price_move_pct=0.35,
                     min_volume_ratio=2.0):
    """
    Sync wrapper, so you can call from Streamlit or normal scripts:
        df = scan_early_pumps()
    """
    return asyncio.run(
        scan_early_pumps_async(
            limit_symbols=limit_symbols,
            min_price_move_pct=min_price_move_pct,
            min_volume_ratio=min_volume_ratio,
        )
    )
