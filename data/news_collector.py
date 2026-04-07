# momentum-2/data/news_collector.py
"""News collector for crypto market context.

Sources:
- Google News RSS (Bitcoin, Ethereum, crypto market)
- CoinGecko public API (trending coins, global data)

No API keys required. Uses only stdlib + requests.
"""
import sys
import os
import json
import time
import logging
import xml.etree.ElementTree as ET
from urllib.request import urlopen, Request
from urllib.error import URLError
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from config import Config

import requests

logger = logging.getLogger(__name__)

# Google News RSS feeds for crypto topics
GNEWS_RSS_URLS = [
    "https://news.google.com/rss/search?q=bitcoin+crypto+market&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=ethereum+defi&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=cryptocurrency+regulation&hl=en&gl=US&ceid=US:en",
]

COINGECKO_BASE = "https://api.coingecko.com/api/v3"


def fetch_google_news(max_per_feed: int = 5) -> list[dict]:
    """Fetch headlines from Google News RSS feeds."""
    articles = []

    for url in GNEWS_RSS_URLS:
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=10) as resp:
                xml_data = resp.read()

            root = ET.fromstring(xml_data)
            channel = root.find("channel")
            if channel is None:
                continue

            items = channel.findall("item")
            for item in items[:max_per_feed]:
                title = item.findtext("title", "").strip()
                pub_date = item.findtext("pubDate", "").strip()
                source = item.findtext("source", "").strip()

                if title:
                    articles.append({
                        "title": title,
                        "source": source,
                        "published": pub_date,
                    })

        except (URLError, ET.ParseError, Exception) as e:
            logger.warning(f"Failed to fetch RSS from {url}: {e}")
            continue

        time.sleep(0.3)

    # Deduplicate by title
    seen = set()
    unique = []
    for a in articles:
        if a["title"] not in seen:
            seen.add(a["title"])
            unique.append(a)

    return unique


def fetch_coingecko_trending() -> list[dict]:
    """Fetch trending coins from CoinGecko."""
    try:
        resp = requests.get(
            f"{COINGECKO_BASE}/search/trending",
            headers={"accept": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        trending = []
        for item in data.get("coins", []):
            coin = item.get("item", {})
            trending.append({
                "name": coin.get("name", ""),
                "symbol": coin.get("symbol", "").upper(),
                "market_cap_rank": coin.get("market_cap_rank"),
                "score": coin.get("score"),
            })
        return trending

    except Exception as e:
        logger.warning(f"Failed to fetch CoinGecko trending: {e}")
        return []


def fetch_coingecko_global() -> dict:
    """Fetch global market data from CoinGecko (total mcap, dominance, etc.)."""
    try:
        resp = requests.get(
            f"{COINGECKO_BASE}/global",
            headers={"accept": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})

        return {
            "total_market_cap_usd": data.get("total_market_cap", {}).get("usd", 0),
            "total_volume_24h_usd": data.get("total_volume", {}).get("usd", 0),
            "btc_dominance": round(data.get("market_cap_percentage", {}).get("btc", 0), 2),
            "eth_dominance": round(data.get("market_cap_percentage", {}).get("eth", 0), 2),
            "market_cap_change_24h_pct": round(
                data.get("market_cap_change_percentage_24h_usd", 0), 2
            ),
            "active_cryptocurrencies": data.get("active_cryptocurrencies", 0),
        }

    except Exception as e:
        logger.warning(f"Failed to fetch CoinGecko global: {e}")
        return {}


def fetch_fear_greed() -> dict:
    """Fetch Fear & Greed Index from alternative.me API."""
    try:
        resp = requests.get(
            "https://api.alternative.me/fng/?limit=1",
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [{}])[0]

        return {
            "value": int(data.get("value", 50)),
            "label": data.get("value_classification", "Neutral"),
        }

    except Exception as e:
        logger.warning(f"Failed to fetch Fear & Greed: {e}")
        return {"value": 50, "label": "Neutral"}


def collect_news(cfg: Config = None) -> dict:
    """Main news collection routine.

    Returns:
        Dict with headlines, trending coins, global data, fear & greed.
    """
    if cfg is None:
        cfg = Config()

    logger.info("Collecting news data...")

    headlines = fetch_google_news(max_per_feed=5)
    trending = fetch_coingecko_trending()
    global_data = fetch_coingecko_global()
    fear_greed = fetch_fear_greed()

    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "headlines": headlines,
        "trending_coins": trending,
        "global_market": global_data,
        "fear_greed": fear_greed,
    }

    # Save to disk
    output_path = os.path.join(cfg.base_dir, "news_data.json")
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    logger.info(
        f"Saved news data: {len(headlines)} headlines, "
        f"{len(trending)} trending, F&G={fear_greed.get('value')}"
    )
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    data = collect_news()
    print(f"Headlines: {len(data['headlines'])}")
    print(f"Trending: {[c['symbol'] for c in data['trending_coins']]}")
    print(f"Fear & Greed: {data['fear_greed']}")
