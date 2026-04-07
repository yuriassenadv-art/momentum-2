# momentum-2/analytical/engine.py
"""Analytical Engine — Technical indicators from OHLCV data.

Calculates RSI, MACD, Volume Ratio, ATR from candle data.
All indicators are pure functions operating on lists/arrays.
"""
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from config import Config

import numpy as np
import pandas as pd


def calculate_rsi(closes: list, window: int = 14) -> float:
    """Calculate RSI from a list of closing prices.

    Args:
        closes: List of closing prices (oldest first).
        window: RSI lookback period (default 14).

    Returns:
        Last RSI value as float (0-100).
    """
    if len(closes) < window + 1:
        return 50.0  # neutral if insufficient data

    prices = pd.Series(closes, dtype=float)
    deltas = prices.diff()

    gains = deltas.clip(lower=0)
    losses = (-deltas).clip(lower=0)

    avg_gain = gains.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    avg_loss = losses.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))

    last_val = rsi.iloc[-1]
    return float(last_val) if not np.isnan(last_val) else 50.0


def calculate_macd(closes: list, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """Calculate MACD from closing prices.

    Args:
        closes: List of closing prices (oldest first).
        fast: Fast EMA period.
        slow: Slow EMA period.
        signal: Signal line period.

    Returns:
        Dict with keys: macd, signal, histogram.
    """
    if len(closes) < slow + signal:
        return {'macd': 0.0, 'signal': 0.0, 'histogram': 0.0}

    prices = pd.Series(closes, dtype=float)
    ema_fast = prices.ewm(span=fast, adjust=False).mean()
    ema_slow = prices.ewm(span=slow, adjust=False).mean()

    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line

    return {
        'macd': float(macd_line.iloc[-1]),
        'signal': float(signal_line.iloc[-1]),
        'histogram': float(histogram.iloc[-1]),
    }


def calculate_volume_ratio(volumes: list, window: int = 10) -> float:
    """Calculate volume ratio: current volume vs rolling average.

    Args:
        volumes: List of volume values from 1m candles (oldest first).
        window: Number of candles for average (default 10).

    Returns:
        Ratio of current volume to average.
    """
    if len(volumes) < window + 1:
        return 1.0

    avg = float(np.mean(volumes[-(window + 1):-1]))
    current = float(volumes[-1])

    if avg <= 0:
        return 1.0

    return current / avg


def calculate_atr(highs: list, lows: list, closes: list, window: int = 14) -> float:
    """Calculate Average True Range from OHLCV data.

    Args:
        highs: List of high prices.
        lows: List of low prices.
        closes: List of closing prices.
        window: ATR lookback period (default 14).

    Returns:
        Last ATR value as float.
    """
    if len(closes) < window + 1:
        return 0.0

    h = np.array(highs, dtype=float)
    l = np.array(lows, dtype=float)
    c = np.array(closes, dtype=float)

    prev_c = np.roll(c, 1)
    prev_c[0] = c[0]

    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_c), np.abs(l - prev_c)))

    atr_series = pd.Series(tr).ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    return float(atr_series.iloc[-1])


def analyze_asset(market_data_entry: dict) -> dict:
    """Run all indicators for a single asset.

    Args:
        market_data_entry: Dict with keys:
            ohlcv_5m: list of [ts, open, high, low, close, volume]
            ohlcv_1m: list of [ts, open, high, low, close, volume]
            funding_rate: float

    Returns:
        Dict with rsi, macd, macd_signal, macd_histogram,
        volume_ratio, atr, funding_rate.
    """
    candles_5m = market_data_entry.get('ohlcv_5m', [])
    candles_1m = market_data_entry.get('ohlcv_1m', [])
    funding = market_data_entry.get('funding_rate', 0.0)

    # Extract OHLCV columns from 5m candles
    closes_5m = [c[4] for c in candles_5m] if candles_5m else []
    highs_5m = [c[2] for c in candles_5m] if candles_5m else []
    lows_5m = [c[3] for c in candles_5m] if candles_5m else []

    # Extract volumes from 1m candles
    volumes_1m = [c[5] for c in candles_1m] if candles_1m else []

    rsi = calculate_rsi(closes_5m)
    macd_data = calculate_macd(closes_5m)
    vol_ratio = calculate_volume_ratio(volumes_1m)
    atr = calculate_atr(highs_5m, lows_5m, closes_5m)

    return {
        'rsi': round(rsi, 2),
        'macd': round(macd_data['macd'], 6),
        'macd_signal': round(macd_data['signal'], 6),
        'macd_histogram': round(macd_data['histogram'], 6),
        'volume_ratio': round(vol_ratio, 2),
        'atr': round(atr, 4),
        'funding_rate': funding,
    }


def run_all_analytics(market_data: dict, output_path: str) -> dict:
    """Run analytics for all assets and save results.

    Args:
        market_data: Dict keyed by symbol, each value is a market_data_entry.
        output_path: Path to save analytics.json.

    Returns:
        Dict keyed by symbol with analytics results.
    """
    results = {}
    for symbol, data in market_data.items():
        results[symbol] = analyze_asset(data)

    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2)

    return results
