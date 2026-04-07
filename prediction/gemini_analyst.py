"""Momentum 2 — Gemini Flash Analyst

Sends a compact market briefing to Gemini Flash 2.5 and parses a structured
JSON response with sentiment, probability, and volatility regime per asset.
"""

import sys
import os
import json
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from config import Config

import google.generativeai as genai

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a quantitative crypto analyst. You receive a compact market briefing "
    "and return ONLY a JSON array (no markdown, no commentary). Each element has:\n"
    '  "asset": string (symbol like "BTC", "ETH"),\n'
    '  "sentiment_score": float from -1 (max bearish) to +1 (max bullish),\n'
    '  "confidence": float 0 to 1,\n'
    '  "probability": float 0 to 1 (probability the asset goes up in the next hour),\n'
    '  "volatility_regime": one of "low", "medium", "high",\n'
    '  "time_horizon": string (e.g. "1h", "4h"),\n'
    '  "narrative": string (one sentence max explaining the call).\n'
    "Return ONLY the JSON array. No extra text."
)


def _build_briefing(market_data: dict) -> str:
    """Build a compact text briefing from market data.

    Args:
        market_data: dict of symbol -> dict with keys like price, change_24h,
                     volume_24h, rsi, indicators, etc.
    """
    lines = []
    for symbol, data in market_data.items():
        parts = [f"{symbol}:"]
        if 'price' in data:
            parts.append(f"price={data['price']:.2f}")
        if 'change_24h' in data:
            parts.append(f"chg24h={data['change_24h']:+.2f}%")
        if 'volume_24h' in data:
            parts.append(f"vol={data['volume_24h']:.0f}")
        if 'rsi' in data:
            parts.append(f"RSI={data['rsi']:.1f}")
        if 'ema_cross' in data:
            parts.append(f"EMA_cross={data['ema_cross']}")
        if 'macd_signal' in data:
            parts.append(f"MACD={data['macd_signal']}")
        if 'funding_rate' in data:
            parts.append(f"funding={data['funding_rate']:.4f}")
        lines.append(' '.join(parts))
    return '\n'.join(lines)


def analyze(market_data: dict) -> dict:
    """Send market data to Gemini Flash and return structured signals.

    Args:
        market_data: dict of symbol -> indicator dict.

    Returns:
        dict of symbol -> signal dict with keys: sentiment_score, confidence,
        probability, volatility_regime, time_horizon, narrative.
    """
    cfg = Config()
    if not cfg.gemini_api_key:
        logger.warning("Gemini API key not configured, returning empty signals")
        return {}

    genai.configure(api_key=cfg.gemini_api_key)
    model = genai.GenerativeModel(
        model_name=cfg.gemini_model,
        system_instruction=_SYSTEM_PROMPT,
    )

    briefing = _build_briefing(market_data)
    if not briefing.strip():
        return {}

    try:
        response = model.generate_content(briefing)
        text = response.text.strip()

        # Strip markdown fences if present
        if text.startswith('```'):
            text = text.split('\n', 1)[1] if '\n' in text else text[3:]
        if text.endswith('```'):
            text = text[:-3].strip()
        if text.startswith('json'):
            text = text[4:].strip()

        signals_list = json.loads(text)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse Gemini response as JSON: %s", e)
        return {}
    except Exception as e:
        logger.error("Gemini API call failed: %s", e)
        return {}

    # Convert list to dict keyed by asset symbol
    result = {}
    for entry in signals_list:
        symbol = entry.get('asset', '').upper()
        if not symbol:
            continue
        result[symbol] = {
            'sentiment_score': float(entry.get('sentiment_score', 0)),
            'confidence': float(entry.get('confidence', 0)),
            'probability': float(entry.get('probability', 0.5)),
            'volatility_regime': entry.get('volatility_regime', 'medium'),
            'time_horizon': entry.get('time_horizon', '1h'),
            'narrative': entry.get('narrative', ''),
        }

    return result
