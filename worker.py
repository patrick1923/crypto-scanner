import pandas as pd
import ccxt.pro as ccxt_pro
import asyncio
from datetime import timezone
import database as db


async def analyze_symbol_2h(exchange, symbol):
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, '2h', limit=22)
        if len(ohlcv) < 22:
            return None
        df = pd.DataFrame(
            ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['range'] = df['high'] - df['low']
        pre_signal_candle = df.iloc[-2]
        pre_signal_range = pre_signal_candle['range']
        avg_range_10 = df['range'].iloc[-12:-2].mean()
        is_contraction = pre_signal_range < (avg_range_10 * 0.5)
        signal_candle = df.iloc[-1]
        previous_candle = df.iloc[-2]
        price_change = (
            (signal_candle['close'] - previous_candle['close']) / previous_candle['close']) * 100
        average_volume = df.iloc[-21:-1]['volume'].mean()
        volume_ratio = signal_candle['volume'] / \
            average_volume if average_volume > 0 else 0
        pressure = "📈 Buyer" if signal_candle['close'] > signal_candle['open'] else "📉 Seller"
        price_score, volume_score = 0, 0
        if abs(price_change) > 6:
            price_score = 50
        elif abs(price_change) > 4:
            price_score = 35
        elif abs(price_change) > 2:
            price_score = 20
        if volume_ratio > 5.0:
            volume_score = 50
        elif volume_ratio > 3.5:
            volume_score = 35
        elif volume_ratio > 2.0:
            volume_score = 20
        confidence_score = price_score + volume_score
        signal_timestamp = pd.to_datetime(
            signal_candle['timestamp'], unit='ms').tz_localize(timezone.utc)
        return {'Symbol': symbol, 'Price': signal_candle['close'], 'Signal Time': signal_timestamp, 'Confidence Score': confidence_score, 'Price Change (2h) %': price_change, 'Volume Ratio (2h)': volume_ratio, 'Dominant Pressure': pressure, 'Volatility Contraction': is_contraction}
    except Exception:
        return None


async def scan_all_markets():
    exchange = ccxt_pro.binance({'options': {'defaultType': 'future'}})
    try:
        await exchange.load_markets()
        symbols = [s for s in exchange.symbols if s.endswith(':USDT')]
        tasks = [analyze_symbol_2h(exchange, symbol) for symbol in symbols]
        results = await asyncio.gather(*tasks)
        df = pd.DataFrame([res for res in results if res is not None])
        if df.empty:
            return df
        all_tickers = await exchange.fetch_tickers(df['Symbol'].tolist())
        volumes_24h = {symbol: ticker['quoteVolume']
                       for symbol, ticker in all_tickers.items()}
        df['24h Volume'] = df['Symbol'].map(volumes_24h).fillna(0)
        volume_threshold = df['24h Volume'].quantile(0.75)
        df['High 24h Volume'] = df['24h Volume'] > volume_threshold
        return df
    finally:
        await exchange.close()


def run_worker():
    print("Worker starting scan...")
    db.create_table()
    df = asyncio.run(scan_all_markets())

    if not df.empty:
        pump_candidates = df[(df['Price Change (2h) %'] > 2) & (df['Volume Ratio (2h)'] > 2.0) & (
            df['Dominant Pressure'] == '📈 Buyer') & (df['High 24h Volume'] == True)]
        dump_candidates = df[(df['Price Change (2h) %'] < -2) & (df['Volume Ratio (2h)'] > 2.0) & (
            df['Dominant Pressure'] == '📉 Seller') & (df['High 24h Volume'] == True)]

        if not pump_candidates.empty:
            db.log_signals(pump_candidates, 'Pump')
            print(f"Logged {len(pump_candidates)} new pump signals.")
        if not dump_candidates.empty:
            db.log_signals(dump_candidates, 'Dump')
            print(f"Logged {len(dump_candidates)} new dump signals.")
    print("Worker scan complete.")


if __name__ == "__main__":
    run_worker()
