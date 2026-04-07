# momentum-2/data/collector.py
"""Hyperliquid market data collector.

Fetches all perpetual pairs, filters by 24h volume, and collects
OHLCV candles in two timeframes (1m entry/exit, 5m analysis).
No SDK — raw HTTP via requests.
"""
from __future__ import annotations

import sys
import os
import json
import time
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from config import Config

import requests

logger = logging.getLogger(__name__)

HL_INFO_URL = "https://api.hyperliquid.xyz/info"
HEADERS = {"Content-Type": "application/json"}


def fetch_meta() -> dict:
    """Fetch metaAndAssetCtxs — returns all perpetual pairs with context."""
    resp = requests.post(
        HL_INFO_URL,
        headers=HEADERS,
        json={"type": "metaAndAssetCtxs"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def parse_pairs(meta: dict, min_volume: float) -> list[dict]:
    """Filter pairs by 24h volume. Returns list of dicts with symbol info."""
    universe = meta[0]["universe"]  # meta info
    asset_ctxs = meta[1]            # market contexts

    pairs = []
    for asset_info, ctx in zip(universe, asset_ctxs):
        symbol = asset_info["name"]
        volume_24h = float(ctx.get("dayNtlVlm", 0))
        funding_rate = float(ctx.get("funding", 0))
        open_interest = float(ctx.get("openInterest", 0))
        mark_price = float(ctx.get("markPx", 0))

        if volume_24h < min_volume:
            continue

        pairs.append({
            "symbol": symbol,
            "volume_24h": volume_24h,
            "funding_rate": funding_rate,
            "open_interest": open_interest,
            "mark_price": mark_price,
        })

    pairs.sort(key=lambda x: x["volume_24h"], reverse=True)
    return pairs


def fetch_candles(symbol: str, interval: str, count: int) -> list[dict]:
    """Fetch OHLCV candles for a symbol via candleSnapshot.

    Args:
        symbol: Coin name (e.g. "BTC")
        interval: Candle interval ("1m", "5m", etc.)
        count: Number of candles to fetch

    Returns:
        List of dicts with keys: t, o, h, l, c, v
    """
    now_ms = int(time.time() * 1000)

    # Calculate start time based on interval and count
    interval_map = {
        "1m": 60_000,
        "5m": 300_000,
        "15m": 900_000,
        "1h": 3_600_000,
        "4h": 14_400_000,
        "1d": 86_400_000,
    }
    interval_ms = interval_map.get(interval, 60_000)
    start_ms = now_ms - (interval_ms * count)

    payload = {
        "type": "candleSnapshot",
        "req": {
            "coin": symbol,
            "interval": interval,
            "startTime": start_ms,
            "endTime": now_ms,
        },
    }

    resp = requests.post(HL_INFO_URL, headers=HEADERS, json=payload, timeout=15)
    resp.raise_for_status()
    raw = resp.json()

    candles = []
    for c in raw:
        candles.append({
            "t": c["t"],
            "o": float(c["o"]),
            "h": float(c["h"]),
            "l": float(c["l"]),
            "c": float(c["c"]),
            "v": float(c["v"]),
        })

    return candles


def collect(cfg: Config = None) -> dict:
    """Main collection routine.

    Returns:
        Dict of symbol -> {ohlcv_1m, ohlcv_5m, volume_24h, funding_rate, open_interest}
    """
    if cfg is None:
        cfg = Config()

    logger.info("Fetching Hyperliquid meta...")
    meta = fetch_meta()
    pairs = parse_pairs(meta, cfg.min_volume_24h)
    logger.info(f"Found {len(pairs)} pairs above ${cfg.min_volume_24h:,.0f} volume")

    result = {}

    for pair in pairs:
        symbol = pair["symbol"]
        logger.info(f"Fetching candles for {symbol}...")

        try:
            ohlcv_1m = fetch_candles(symbol, cfg.entry_timeframe, cfg.candle_count)
            ohlcv_5m = fetch_candles(symbol, cfg.analysis_timeframe, cfg.candle_count)

            result[symbol] = {
                "ohlcv_1m": ohlcv_1m,
                "ohlcv_5m": ohlcv_5m,
                "volume_24h": pair["volume_24h"],
                "funding_rate": pair["funding_rate"],
                "open_interest": pair["open_interest"],
                "mark_price": pair["mark_price"],
            }
        except Exception as e:
            logger.warning(f"Failed to fetch candles for {symbol}: {e}")
            continue

        # Polite rate limiting
        time.sleep(0.1)

    # Save to disk
    output_path = cfg.market_data_path
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    logger.info(f"Saved market data for {len(result)} pairs to {output_path}")
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    data = collect()
    print(f"Collected {len(data)} pairs: {list(data.keys())}")
