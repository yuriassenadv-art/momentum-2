# momentum-2/prediction/market_data_client.py
"""Market Data Client — Crypto-specific data via yfinance + pandas.

Replaces Polymarket as independent signal source.
Provides real market data analysis:
  - BTC dominance trend (rising = risk-off, falling = altcoin season)
  - ETH/BTC ratio (trending up = ETH outperforming = risk-on)
  - Total crypto market cap momentum
  - Fear & Greed Index
  - Top movers (24h gainers/losers from CoinGecko)
  - Correlation matrix (detect when alts decouple from BTC)

All data is factual, no opinion. The decision engine uses these signals
alongside Gemini sentiment for entry decisions.
"""
import sys
import os
import time
import logging
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from config import Config

import requests
import numpy as np

logger = logging.getLogger(__name__)

# CoinGecko free endpoints (no auth)
_CG_BASE = 'https://api.coingecko.com/api/v3'
_HEADERS = {'User-Agent': 'Momentum2/1.0', 'Accept': 'application/json'}


def _fetch_json(url, params=None, timeout=15):
    """Fetch JSON with retry on rate limit."""
    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, headers=_HEADERS, timeout=timeout)
            if resp.status_code == 429:
                wait = 2 ** (attempt + 1)
                logger.warning(f"Rate limited, waiting {wait}s")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if attempt == 2:
                logger.error(f"Failed to fetch {url}: {e}")
                return None
            time.sleep(1)
    return None


def get_btc_dominance():
    """Get BTC dominance % and its trend.

    Rising BTC dominance = money flowing to BTC (risk-off for alts)
    Falling BTC dominance = altcoin season (risk-on for alts)
    """
    data = _fetch_json(f'{_CG_BASE}/global')
    if not data:
        return {'dominance': 0, 'direction': 'neutral'}

    market = data.get('data', {})
    btc_dom = market.get('market_cap_percentage', {}).get('btc', 0)
    change_24h = market.get('market_cap_change_percentage_24h_usd', 0)

    return {
        'dominance': round(btc_dom, 2),
        'market_cap_change_24h': round(change_24h, 2),
        'direction': 'risk_off' if btc_dom > 55 else ('altseason' if btc_dom < 45 else 'neutral'),
    }


def get_fear_greed():
    """Get crypto Fear & Greed Index (0-100).

    0-25: Extreme Fear (contrarian buy signal)
    25-45: Fear
    45-55: Neutral
    55-75: Greed
    75-100: Extreme Greed (contrarian sell signal)
    """
    data = _fetch_json('https://api.alternative.me/fng/?limit=1')
    if not data or not data.get('data'):
        return {'value': 50, 'label': 'neutral', 'direction': 'neutral'}

    entry = data['data'][0]
    value = int(entry.get('value', 50))
    label = entry.get('value_classification', 'neutral').lower()

    if value < 25:
        direction = 'extreme_fear'
    elif value < 45:
        direction = 'fear'
    elif value > 75:
        direction = 'extreme_greed'
    elif value > 55:
        direction = 'greed'
    else:
        direction = 'neutral'

    return {'value': value, 'label': label, 'direction': direction}


def get_top_movers():
    """Get top 24h gainers and losers from CoinGecko.

    Shows market momentum: many gainers = bullish, many losers = bearish.
    """
    data = _fetch_json(f'{_CG_BASE}/coins/markets', params={
        'vs_currency': 'usd',
        'order': 'market_cap_desc',
        'per_page': 50,
        'page': 1,
        'sparkline': 'false',
        'price_change_percentage': '1h,24h',
    })

    if not data:
        return {'gainers': [], 'losers': [], 'bullish_ratio': 0.5}

    gainers = []
    losers = []

    for coin in data:
        symbol = coin.get('symbol', '').upper()
        change_24h = coin.get('price_change_percentage_24h', 0) or 0
        change_1h = coin.get('price_change_percentage_1h_in_currency', 0) or 0

        entry = {
            'symbol': symbol,
            'price': coin.get('current_price', 0),
            'change_1h': round(change_1h, 2),
            'change_24h': round(change_24h, 2),
            'volume_24h': coin.get('total_volume', 0),
        }

        if change_24h > 2:
            gainers.append(entry)
        elif change_24h < -2:
            losers.append(entry)

    gainers.sort(key=lambda x: x['change_24h'], reverse=True)
    losers.sort(key=lambda x: x['change_24h'])

    total = len(gainers) + len(losers)
    bullish_ratio = len(gainers) / total if total > 0 else 0.5

    return {
        'gainers': gainers[:10],
        'losers': losers[:10],
        'bullish_ratio': round(bullish_ratio, 2),
        'gainers_count': len(gainers),
        'losers_count': len(losers),
    }


def get_eth_btc_ratio():
    """Get ETH/BTC price ratio and trend.

    Rising ETH/BTC = risk appetite increasing (bullish for alts)
    Falling ETH/BTC = flight to BTC safety (bearish for alts)
    """
    eth_data = _fetch_json(f'{_CG_BASE}/simple/price', params={
        'ids': 'ethereum',
        'vs_currencies': 'btc',
        'include_24hr_change': 'true',
    })

    if not eth_data or 'ethereum' not in eth_data:
        return {'ratio': 0, 'change_24h': 0, 'direction': 'neutral'}

    eth = eth_data['ethereum']
    ratio = eth.get('btc', 0)
    change = eth.get('btc_24h_change', 0) or 0

    direction = 'neutral'
    if change > 2:
        direction = 'risk_on'
    elif change < -2:
        direction = 'risk_off'

    return {
        'ratio': round(ratio, 6),
        'change_24h': round(change, 2),
        'direction': direction,
    }


def get_market_signal():
    """Generate comprehensive crypto market signal.

    Aggregates all data sources into a single directional signal.
    This replaces Polymarket as the independent data source.

    Returns:
        dict with direction, confidence, components, and market_regime.
    """
    # Collect all signals (with delays to respect rate limits)
    btc_dom = get_btc_dominance()
    time.sleep(1.5)
    fear_greed = get_fear_greed()
    time.sleep(1.5)
    movers = get_top_movers()
    time.sleep(1.5)
    eth_btc = get_eth_btc_ratio()

    # Score: -1 (bearish) to +1 (bullish)
    scores = []

    # Fear & Greed: extreme fear = contrarian bullish, extreme greed = contrarian bearish
    fg = fear_greed['value']
    if fg < 25:
        scores.append(('fear_greed', 0.6, 'extreme_fear=contrarian_buy'))
    elif fg < 40:
        scores.append(('fear_greed', 0.3, 'fear=mild_buy'))
    elif fg > 75:
        scores.append(('fear_greed', -0.6, 'extreme_greed=contrarian_sell'))
    elif fg > 60:
        scores.append(('fear_greed', -0.3, 'greed=mild_sell'))
    else:
        scores.append(('fear_greed', 0.0, 'neutral'))

    # Market movers: more gainers = bullish
    br = movers.get('bullish_ratio', 0.5)
    if br > 0.65:
        scores.append(('movers', 0.5, f'gainers_dominant={br:.0%}'))
    elif br < 0.35:
        scores.append(('movers', -0.5, f'losers_dominant={br:.0%}'))
    else:
        scores.append(('movers', 0.0, f'balanced={br:.0%}'))

    # BTC dominance: high = risk-off (bearish for alts), low = altseason
    dom = btc_dom.get('dominance', 50)
    if dom > 58:
        scores.append(('btc_dominance', -0.3, f'high={dom:.0f}%_risk_off'))
    elif dom < 42:
        scores.append(('btc_dominance', 0.3, f'low={dom:.0f}%_altseason'))
    else:
        scores.append(('btc_dominance', 0.0, f'normal={dom:.0f}%'))

    # ETH/BTC: rising = risk-on
    eth_change = eth_btc.get('change_24h', 0)
    if eth_change > 2:
        scores.append(('eth_btc', 0.3, f'rising={eth_change:+.1f}%_risk_on'))
    elif eth_change < -2:
        scores.append(('eth_btc', -0.3, f'falling={eth_change:+.1f}%_risk_off'))
    else:
        scores.append(('eth_btc', 0.0, f'stable={eth_change:+.1f}%'))

    # Market cap momentum
    mc_change = btc_dom.get('market_cap_change_24h', 0)
    if mc_change > 3:
        scores.append(('market_cap', 0.4, f'expanding={mc_change:+.1f}%'))
    elif mc_change < -3:
        scores.append(('market_cap', -0.4, f'contracting={mc_change:+.1f}%'))
    else:
        scores.append(('market_cap', 0.0, f'stable={mc_change:+.1f}%'))

    # Aggregate
    if not scores:
        return {
            'direction': 'neutral', 'confidence': 0.0,
            'score': 0.0, 'components': [], 'market_regime': 'unknown',
        }

    total_score = sum(s[1] for s in scores) / len(scores)
    confidence = min(1.0, abs(total_score) * 2)

    if total_score > 0.15:
        direction = 'bullish'
    elif total_score < -0.15:
        direction = 'bearish'
    else:
        direction = 'neutral'

    # Market regime
    if fg < 25 and br < 0.4:
        regime = 'capitulation'
    elif fg > 75 and br > 0.6:
        regime = 'euphoria'
    elif abs(total_score) < 0.1:
        regime = 'ranging'
    elif total_score > 0:
        regime = 'accumulation'
    else:
        regime = 'distribution'

    return {
        'direction': direction,
        'confidence': round(confidence, 4),
        'score': round(total_score, 4),
        'components': [{'name': s[0], 'score': s[1], 'detail': s[2]} for s in scores],
        'market_regime': regime,
        'fear_greed': fear_greed,
        'btc_dominance': btc_dom,
        'eth_btc': eth_btc,
        'movers_summary': {
            'bullish_ratio': movers['bullish_ratio'],
            'gainers': len(movers['gainers']),
            'losers': len(movers['losers']),
        },
    }


if __name__ == '__main__':
    import json
    signal = get_market_signal()
    print(json.dumps(signal, indent=2))
