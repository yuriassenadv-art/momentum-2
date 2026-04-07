# momentum-2/data/social_sentiment.py
"""Social sentiment collector via CoinGecko public API.

Fetches sentiment votes (UP/DOWN %) and trending status for top assets.
No API key required. Uses only stdlib + requests.
"""
from __future__ import annotations

import sys
import os
import json
import time
import logging
from datetime import datetime, timezone
from urllib.request import urlopen, Request
from urllib.error import URLError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from config import Config

import requests

logger = logging.getLogger(__name__)

COINGECKO_BASE = "https://api.coingecko.com/api/v3"

# CoinGecko IDs for the top assets we track
TRACKED_ASSETS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "binancecoin",
    "XRP": "ripple",
    "DOGE": "dogecoin",
    "ADA": "cardano",
    "AVAX": "avalanche-2",
    "LINK": "chainlink",
    "DOT": "polkadot",
}


def fetch_coin_sentiment(coin_id: str) -> dict | None:
    """Fetch sentiment votes for a single coin from CoinGecko.

    Returns:
        Dict with up_pct, down_pct or None on failure.
    """
    try:
        resp = requests.get(
            f"{COINGECKO_BASE}/coins/{coin_id}",
            params={
                "localization": "false",
                "tickers": "false",
                "market_data": "false",
                "community_data": "true",
                "developer_data": "false",
            },
            headers={"accept": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        up_pct = data.get("sentiment_votes_up_percentage", 0) or 0
        down_pct = data.get("sentiment_votes_down_percentage", 0) or 0

        return {
            "up_pct": round(float(up_pct), 1),
            "down_pct": round(float(down_pct), 1),
        }

    except Exception as e:
        logger.warning(f"Failed to fetch sentiment for {coin_id}: {e}")
        return None


def fetch_trending_symbols() -> set[str]:
    """Fetch trending coin symbols from CoinGecko."""
    try:
        resp = requests.get(
            f"{COINGECKO_BASE}/search/trending",
            headers={"accept": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        symbols = set()
        for item in data.get("coins", []):
            coin = item.get("item", {})
            symbol = coin.get("symbol", "").upper()
            if symbol:
                symbols.add(symbol)

        return symbols

    except Exception as e:
        logger.warning(f"Failed to fetch trending: {e}")
        return set()


def collect_sentiment(cfg: Config = None) -> dict:
    """Main sentiment collection routine.

    Returns:
        Dict of symbol -> {up_pct, down_pct, trending: bool}
    """
    if cfg is None:
        cfg = Config()

    logger.info("Collecting social sentiment...")

    trending_symbols = fetch_trending_symbols()
    logger.info(f"Trending symbols: {trending_symbols}")

    result = {}

    for symbol, coin_id in TRACKED_ASSETS.items():
        sentiment = fetch_coin_sentiment(coin_id)

        if sentiment is None:
            sentiment = {"up_pct": 50.0, "down_pct": 50.0}

        sentiment["trending"] = symbol in trending_symbols
        result[symbol] = sentiment

        logger.info(
            f"{symbol}: UP={sentiment['up_pct']}% DOWN={sentiment['down_pct']}% "
            f"trending={sentiment['trending']}"
        )

        # CoinGecko free tier rate limit: ~10-30 req/min
        time.sleep(2.5)

    # Add metadata
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "assets": result,
        "trending_all": sorted(trending_symbols),
    }

    # Save to disk
    output_path = os.path.join(cfg.base_dir, "social_sentiment.json")
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    logger.info(f"Saved sentiment data for {len(result)} assets")
    return output


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    data = collect_sentiment()
    for symbol, info in data["assets"].items():
        flag = " [TRENDING]" if info["trending"] else ""
        print(f"{symbol}: {info['up_pct']}% bullish{flag}")
