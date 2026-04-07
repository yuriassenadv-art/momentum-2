"""Momentum 2 — Polymarket Independent Signal

Connects to the Polymarket CLOB API to fetch crypto-related prediction markets
and generates an independent directional signal. This signal is NOT merged with
Gemini — it stands alone and can optionally report alignment.
"""

import sys
import os
import time
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from config import Config

import requests

logger = logging.getLogger(__name__)

BASE_URL = 'https://clob.polymarket.com'

# Keywords used to find crypto-relevant markets
_CRYPTO_KEYWORDS = [
    'bitcoin', 'btc', 'ethereum', 'eth', 'crypto',
    'fed', 'federal reserve', 'interest rate', 'rate cut',
    'sec', 'regulation', 'etf', 'stablecoin',
]

# Keywords that indicate bullish crypto sentiment
_BULLISH_KEYWORDS = [
    'above', 'over', 'exceed', 'higher', 'rise', 'rally',
    'rate cut', 'etf approval', 'approve',
]

# Keywords that indicate bearish crypto sentiment
_BEARISH_KEYWORDS = [
    'below', 'under', 'crash', 'fall', 'decline', 'drop',
    'rate hike', 'ban', 'reject', 'crackdown',
]


def _get_headers(cfg: Config) -> dict:
    """Build authentication headers for the Polymarket CLOB API."""
    return {
        'POLY_API_KEY': cfg.poly_api_key,
        'Content-Type': 'application/json',
    }


def _fetch_markets(cfg: Config) -> list[dict]:
    """Fetch markets from Polymarket CLOB and filter for crypto relevance."""
    headers = _get_headers(cfg)
    all_markets = []

    try:
        resp = requests.get(
            f'{BASE_URL}/markets',
            headers=headers,
            params={'limit': 100, 'active': True},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        markets = data if isinstance(data, list) else data.get('data', data.get('markets', []))
    except requests.RequestException as e:
        logger.error("Failed to fetch Polymarket markets: %s", e)
        return []

    for market in markets:
        question = (market.get('question', '') or market.get('title', '')).lower()
        description = (market.get('description', '')).lower()
        combined = f'{question} {description}'

        if any(kw in combined for kw in _CRYPTO_KEYWORDS):
            all_markets.append(market)

    return all_markets


def _classify_market(market: dict) -> str:
    """Classify a market as bullish, bearish, or neutral for crypto."""
    question = (market.get('question', '') or market.get('title', '')).lower()

    bullish_score = sum(1 for kw in _BULLISH_KEYWORDS if kw in question)
    bearish_score = sum(1 for kw in _BEARISH_KEYWORDS if kw in question)

    if bullish_score > bearish_score:
        return 'bullish'
    elif bearish_score > bullish_score:
        return 'bearish'
    return 'neutral'


def _get_market_price(market: dict) -> float:
    """Extract the current probability/price from a market.

    Polymarket prices represent the probability of YES outcome (0-1).
    """
    # Different possible field names in the API response
    for field in ('price', 'best_ask', 'last_trade_price', 'mid_price'):
        val = market.get(field)
        if val is not None:
            return float(val)

    # Try tokens array
    tokens = market.get('tokens', [])
    if tokens and isinstance(tokens, list):
        for token in tokens:
            if token.get('outcome', '').upper() == 'YES':
                price = token.get('price')
                if price is not None:
                    return float(price)

    return 0.5


def _get_price_change(market: dict) -> float:
    """Estimate recent price movement from available data.

    Returns percentage point change (e.g., 0.05 means +5pp).
    """
    # Check for explicit change fields
    for field in ('price_change_24h', 'change_24h', 'price_change'):
        val = market.get(field)
        if val is not None:
            return float(val)

    # Check for historical price
    current = _get_market_price(market)
    prev = market.get('previous_price', market.get('open_price'))
    if prev is not None:
        return current - float(prev)

    return 0.0


def get_polymarket_signal(gemini_direction: str = None) -> dict:
    """Generate an independent directional signal from Polymarket data.

    Args:
        gemini_direction: Optional direction from Gemini ("bullish" or "bearish")
                          for alignment check. Does NOT affect the signal itself.

    Returns:
        dict with: direction (bullish/bearish/neutral), confidence (0-1),
                   aligned_with_gemini (bool), key_markets (list of dicts).
    """
    cfg = Config()

    if not cfg.poly_api_key:
        logger.warning("Polymarket API key not configured")
        return {
            'direction': 'neutral',
            'confidence': 0.0,
            'aligned_with_gemini': False,
            'key_markets': [],
        }

    markets = _fetch_markets(cfg)
    if not markets:
        return {
            'direction': 'neutral',
            'confidence': 0.0,
            'aligned_with_gemini': False,
            'key_markets': [],
        }

    bullish_weight = 0.0
    bearish_weight = 0.0
    key_markets = []
    alignment_signals = 0

    for market in markets:
        price = _get_market_price(market)
        classification = _classify_market(market)
        change = _get_price_change(market)
        question = market.get('question', '') or market.get('title', 'Unknown')

        market_info = {
            'question': question,
            'price': round(price, 4),
            'classification': classification,
            'change': round(change, 4),
        }
        key_markets.append(market_info)

        # Crypto-bullish event with high odds -> bullish signal
        if classification == 'bullish' and price > 0.70:
            bullish_weight += price
        elif classification == 'bearish' and price > 0.70:
            bearish_weight += price
        # Inverse: bullish event with LOW odds -> bearish signal
        elif classification == 'bullish' and price < 0.30:
            bearish_weight += (1 - price)
        elif classification == 'bearish' and price < 0.30:
            bullish_weight += (1 - price)

        # Check alignment: price moved >5pp in same direction as Gemini
        if gemini_direction and abs(change) > 0.05:
            if change > 0 and classification == 'bullish' and gemini_direction == 'bullish':
                alignment_signals += 1
            elif change > 0 and classification == 'bearish' and gemini_direction == 'bearish':
                alignment_signals += 1
            elif change < 0 and classification == 'bullish' and gemini_direction == 'bearish':
                alignment_signals += 1
            elif change < 0 and classification == 'bearish' and gemini_direction == 'bullish':
                alignment_signals += 1

    # Determine direction
    total_weight = bullish_weight + bearish_weight
    if total_weight < 0.01:
        direction = 'neutral'
        confidence = 0.0
    elif bullish_weight > bearish_weight:
        direction = 'bullish'
        confidence = bullish_weight / total_weight
    elif bearish_weight > bullish_weight:
        direction = 'bearish'
        confidence = bearish_weight / total_weight
    else:
        direction = 'neutral'
        confidence = 0.0

    # Cap confidence at 1.0
    confidence = min(confidence, 1.0)

    # Alignment check
    aligned = False
    if gemini_direction and gemini_direction == direction and alignment_signals > 0:
        aligned = True

    # Limit key_markets to the top 10 most relevant
    key_markets = key_markets[:10]

    return {
        'direction': direction,
        'confidence': round(confidence, 4),
        'aligned_with_gemini': aligned,
        'key_markets': key_markets,
    }
