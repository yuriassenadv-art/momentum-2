# momentum-2/analytical/support_resistance.py
"""Support & Resistance Detection from 5m OHLCV data.

Detects swing highs/lows and clusters nearby levels into zones.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from config import Config

import numpy as np


def _find_swing_highs(highs: list, window: int = 5) -> list:
    """Find swing high points (local maxima).

    A swing high is a candle whose high is the maximum within
    `window` candles on each side.
    """
    results = []
    n = len(highs)
    for i in range(window, n - window):
        segment = highs[i - window:i + window + 1]
        if highs[i] == max(segment):
            results.append(highs[i])
    return results


def _find_swing_lows(lows: list, window: int = 5) -> list:
    """Find swing low points (local minima).

    A swing low is a candle whose low is the minimum within
    `window` candles on each side.
    """
    results = []
    n = len(lows)
    for i in range(window, n - window):
        segment = lows[i - window:i + window + 1]
        if lows[i] == min(segment):
            results.append(lows[i])
    return results


def _cluster_levels(prices: list, tolerance_pct: float = 0.005) -> list:
    """Cluster nearby price levels into zones.

    Levels within `tolerance_pct` (0.5%) of each other are merged.
    Returns list of {price, strength} sorted by strength descending.
    """
    if not prices:
        return []

    sorted_prices = sorted(prices)
    clusters = []
    current_cluster = [sorted_prices[0]]

    for price in sorted_prices[1:]:
        cluster_mean = float(np.mean(current_cluster))
        if abs(price - cluster_mean) / cluster_mean <= tolerance_pct:
            current_cluster.append(price)
        else:
            clusters.append(current_cluster)
            current_cluster = [price]

    clusters.append(current_cluster)

    levels = []
    for cluster in clusters:
        levels.append({
            'price': round(float(np.mean(cluster)), 6),
            'strength': len(cluster),
        })

    levels.sort(key=lambda x: x['strength'], reverse=True)
    return levels


def detect_levels(ohlcv_5m: list) -> dict:
    """Detect support and resistance levels from 5m OHLCV data.

    Args:
        ohlcv_5m: List of [ts, open, high, low, close, volume] candles.

    Returns:
        Dict with keys:
            supports: list of {price, strength}
            resistances: list of {price, strength}
    """
    if len(ohlcv_5m) < 15:
        return {'supports': [], 'resistances': []}

    def _v(c, key, idx):
        return float(c.get(key, c.get(key[0], 0)) if isinstance(c, dict) else c[idx])
    highs = [_v(c, 'high', 2) for c in ohlcv_5m]
    lows = [_v(c, 'low', 3) for c in ohlcv_5m]

    swing_highs = _find_swing_highs(highs)
    swing_lows = _find_swing_lows(lows)

    resistances = _cluster_levels(swing_highs)
    supports = _cluster_levels(swing_lows)

    return {
        'supports': supports,
        'resistances': resistances,
    }


def get_nearest_level(price: float, levels: list) -> dict:
    """Find the nearest S/R level to a given price.

    Args:
        price: Current price.
        levels: List of {price, strength} dicts.

    Returns:
        Dict with price, strength, distance_pct. Empty dict if no levels.
    """
    if not levels or price <= 0:
        return {}

    nearest = None
    min_dist = float('inf')

    for level in levels:
        dist = abs(level['price'] - price)
        if dist < min_dist:
            min_dist = dist
            nearest = level

    distance_pct = round((nearest['price'] - price) / price * 100, 4)

    return {
        'price': nearest['price'],
        'strength': nearest['strength'],
        'distance_pct': distance_pct,
    }
