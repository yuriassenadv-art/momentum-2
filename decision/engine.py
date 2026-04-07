# momentum-2/decision/engine.py
"""Decision Engine — 5-tier aggressive scalping.

ENTRY TIERS (checked in order, first match wins):
  FULL (100%):     Gemini strong + Polymarket aligned + RSI ok
  STANDARD (60%):  Gemini strong + RSI ok
  MOMENTUM (40%):  Gemini any direction + RSI not counter
  SCOUT (30%):     RSI extreme (oversold/overbought) reversal
  MICRO (20%):     RSI divergence from neutral (trending away from 50)

EXIT RULES:
  SCOUT/MICRO:          Profit > 0.2% OR RSI/MACD against OR SL -3%
  MOMENTUM:             Profit > 0.3% OR RSI/MACD against OR SL -3%
  FULL/STANDARD:        RSI/MACD against OR TP +1.5% OR SL -3%

Target: 20-30+ trades/day for 3% daily return.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from config import Config


def _determine_direction(gemini_signal, analytics):
    """Determine trade direction from available signals."""
    sentiment = gemini_signal.get('sentiment', 0.0) if gemini_signal else 0.0

    if abs(sentiment) > 0.1:
        return 'LONG' if sentiment > 0 else 'SHORT'

    rsi = analytics.get('rsi', 50.0)
    if rsi < 35:
        return 'LONG'
    if rsi > 65:
        return 'SHORT'

    # MACD direction
    macd = analytics.get('macd', 0)
    macd_signal = analytics.get('macd_signal', 0)
    if macd > macd_signal:
        return 'LONG'
    if macd < macd_signal:
        return 'SHORT'

    return 'LONG'


def should_enter(
    symbol: str,
    gemini_signal: dict,
    market_signal: dict,
    analytics: dict,
    mc_result: dict,
    config: Config,
) -> dict:
    """5-tier entry decision. First matching tier wins.

    Designed for high trade frequency: relaxed gates, controlled via sizing.
    """
    sentiment = gemini_signal.get('sentiment', 0.0) if gemini_signal else 0.0
    if sentiment == 0.0 and gemini_signal:
        sentiment = gemini_signal.get('sentiment_score', 0.0)

    # Market data signal (replaces Polymarket)
    mkt_direction = market_signal.get('direction', 'neutral') if market_signal else 'neutral'
    mkt_score = market_signal.get('score', 0) if market_signal else 0
    mkt_regime = market_signal.get('market_regime', 'unknown') if market_signal else 'unknown'
    rsi = analytics.get('rsi', 50.0)
    vol_ratio = analytics.get('volume_ratio', 0.0)
    funding = analytics.get('funding_rate', 0.0)
    macd = analytics.get('macd', 0.0)
    macd_signal = analytics.get('macd_signal', 0.0)
    mc_sizing = mc_result.get('sizing_factor', 0.7) if mc_result else 0.7

    direction = _determine_direction(gemini_signal, analytics)

    # RSI not in counter-extreme
    rsi_counter = False
    if direction == 'LONG' and rsi > 78:
        rsi_counter = True
    elif direction == 'SHORT' and rsi < 22:
        rsi_counter = True

    # MACD confirms direction
    macd_confirms = False
    if direction == 'LONG' and macd > macd_signal:
        macd_confirms = True
    elif direction == 'SHORT' and macd < macd_signal:
        macd_confirms = True

    # Market alignment: Gemini direction matches market macro direction
    mkt_aligned = (
        (direction == 'LONG' and mkt_direction == 'bullish') or
        (direction == 'SHORT' and mkt_direction == 'bearish')
    )

    # ── TIER 1: FULL (100%) ──
    # Gemini strong + Market macro aligned + RSI ok
    if (abs(sentiment) >= 0.5
            and mkt_aligned
            and not rsi_counter):
        return {
            'enter': True, 'direction': direction,
            'sizing_factor': min(1.0, mc_sizing),
            'tier': 'FULL',
            'reason': f'FULL: sent={sentiment:+.2f} mkt={mkt_direction}({mkt_score:+.2f}) rsi={rsi:.0f}',
        }

    # ── TIER 2: STANDARD (60%) ──
    # Gemini strong + RSI ok (market macro not required)
    if (abs(sentiment) >= 0.5
            and not rsi_counter):
        return {
            'enter': True, 'direction': direction,
            'sizing_factor': min(0.6, mc_sizing * 0.6),
            'tier': 'STANDARD',
            'reason': f'STD: sent={sentiment:+.2f} rsi={rsi:.0f} regime={mkt_regime}',
        }

    # ── TIER 3: MOMENTUM (40%) ──
    # Any directional sentiment + MACD confirms
    if (abs(sentiment) >= 0.15
            and macd_confirms
            and not rsi_counter):
        return {
            'enter': True, 'direction': direction,
            'sizing_factor': 0.4,
            'tier': 'MOMENTUM',
            'reason': f'MOM: sent={sentiment:+.2f} macd=Y rsi={rsi:.0f}',
        }

    # ── TIER 4: SCOUT (30%) ──
    # RSI extreme reversal
    if rsi < 25 and direction == 'LONG':
        return {
            'enter': True, 'direction': 'LONG',
            'sizing_factor': 0.3,
            'tier': 'SCOUT',
            'reason': f'SCOUT: rsi={rsi:.0f}(oversold)',
        }
    if rsi > 75 and direction == 'SHORT':
        return {
            'enter': True, 'direction': 'SHORT',
            'sizing_factor': 0.3,
            'tier': 'SCOUT',
            'reason': f'SCOUT: rsi={rsi:.0f}(overbought)',
        }

    # ── TIER 5: MICRO (20%) ──
    # RSI trending away from neutral + MACD confirms direction
    # This catches moves BEFORE they become extreme
    rsi_trending_long = rsi < 40 and macd_confirms and direction == 'LONG'
    rsi_trending_short = rsi > 60 and macd_confirms and direction == 'SHORT'

    if rsi_trending_long:
        return {
            'enter': True, 'direction': 'LONG',
            'sizing_factor': 0.2,
            'tier': 'MICRO',
            'reason': f'MICRO: rsi={rsi:.0f}(trending↓) macd=Y',
        }
    if rsi_trending_short:
        return {
            'enter': True, 'direction': 'SHORT',
            'sizing_factor': 0.2,
            'tier': 'MICRO',
            'reason': f'MICRO: rsi={rsi:.0f}(trending↑) macd=Y',
        }

    # ── No entry ──
    return {
        'enter': False, 'direction': direction,
        'sizing_factor': 0, 'tier': 'NONE',
        'reason': f'no_signal: sent={sentiment:+.2f} rsi={rsi:.0f} macd={macd_confirms}',
    }


def should_exit(
    symbol: str,
    analytics_current: dict,
    analytics_previous: dict,
    entry_price: float,
    current_price: float,
    direction: str,
    config: Config,
    tier: str = 'STANDARD',
) -> dict:
    """Exit decision. Faster exits for lower tiers."""
    rsi_now = analytics_current.get('rsi', 50.0)
    rsi_prev = analytics_previous.get('rsi', 50.0)
    macd_now = analytics_current.get('macd', 0.0)
    macd_signal_now = analytics_current.get('macd_signal', 0.0)
    macd_prev = analytics_previous.get('macd', 0.0)
    macd_signal_prev = analytics_previous.get('macd_signal', 0.0)

    # PnL
    if entry_price > 0:
        if direction == 'LONG':
            pnl_pct = (current_price - entry_price) / entry_price
        else:
            pnl_pct = (entry_price - current_price) / entry_price
    else:
        pnl_pct = 0.0

    # 1. Emergency SL (all tiers)
    if pnl_pct <= -config.sl_emergency_pct:
        return {'exit': True, 'reason': f'SL: {pnl_pct*100:+.2f}%'}

    # 2. MICRO/SCOUT quick TP at +0.2%
    if tier in ('MICRO', 'SCOUT') and pnl_pct >= 0.002:
        return {'exit': True, 'reason': f'QUICK_TP: {pnl_pct*100:+.2f}% [{tier}]'}

    # 3. MOMENTUM TP at +0.3%
    if tier == 'MOMENTUM' and pnl_pct >= 0.003:
        return {'exit': True, 'reason': f'MOM_TP: {pnl_pct*100:+.2f}%'}

    # 4. RSI zone inversion
    if direction == 'LONG' and rsi_prev >= 70 and rsi_now < 70:
        return {'exit': True, 'reason': f'RSI↓: {rsi_prev:.0f}->{rsi_now:.0f}'}
    if direction == 'SHORT' and rsi_prev <= 30 and rsi_now > 30:
        return {'exit': True, 'reason': f'RSI↑: {rsi_prev:.0f}->{rsi_now:.0f}'}

    # 5. MACD crosses against
    if direction == 'LONG' and macd_prev >= macd_signal_prev and macd_now < macd_signal_now:
        return {'exit': True, 'reason': f'MACD↓'}
    if direction == 'SHORT' and macd_prev <= macd_signal_prev and macd_now > macd_signal_now:
        return {'exit': True, 'reason': f'MACD↑'}

    # 6. Max TP for FULL/STANDARD at +1.5%
    if tier in ('FULL', 'STANDARD') and pnl_pct >= 0.015:
        return {'exit': True, 'reason': f'MAX_TP: {pnl_pct*100:+.2f}%'}

    return {'exit': False, 'reason': f'hold: {pnl_pct*100:+.2f}%'}
