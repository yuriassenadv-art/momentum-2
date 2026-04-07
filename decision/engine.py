# momentum-2/decision/engine.py
"""Decision Engine — 3-tier entry + aggressive scalping exits.

ENTRY TIERS (any tier can trigger entry):
  FULL (100% sizing):     Gemini + Polymarket aligned + technicals
  STANDARD (60% sizing):  Gemini strong + technicals (no Polymarket needed)
  SCOUT (30% sizing):     Technicals alone (RSI extreme + Volume high + Funding)

EXIT RULES:
  FULL/STANDARD: RSI inverts zone OR MACD crosses against OR SL -3%
  SCOUT:         Profit > 0.3% OR RSI/MACD against OR SL -3%

Philosophy: Polymarket is an AMPLIFIER, not a gate.
The system must generate 20-30+ trades/day to hit 3% daily target.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from config import Config


def _determine_direction(gemini_signal, analytics):
    """Determine trade direction from available signals.

    Priority: Gemini sentiment > RSI extremes > neutral.
    """
    sentiment = gemini_signal.get('sentiment', 0.0) if gemini_signal else 0.0

    if abs(sentiment) > 0.2:
        return 'LONG' if sentiment > 0 else 'SHORT'

    # Fallback: RSI extremes for SCOUT trades
    rsi = analytics.get('rsi', 50.0)
    if rsi < 25:
        return 'LONG'   # Oversold → bounce expected
    if rsi > 75:
        return 'SHORT'  # Overbought → pullback expected

    # Mild sentiment
    if sentiment > 0:
        return 'LONG'
    if sentiment < 0:
        return 'SHORT'

    return 'LONG'  # Default


def should_enter(
    symbol: str,
    gemini_signal: dict,
    poly_signal: dict,
    analytics: dict,
    mc_result: dict,
    config: Config,
) -> dict:
    """Decide whether to enter a position using 3-tier conviction.

    Returns:
        Dict with enter, direction, sizing_factor, tier, reason.
    """
    sentiment = gemini_signal.get('sentiment', 0.0) if gemini_signal else 0.0
    poly_aligned = poly_signal.get('aligned_with_gemini', False) if poly_signal else False
    rsi = analytics.get('rsi', 50.0)
    vol_ratio = analytics.get('volume_ratio', 0.0)
    funding = analytics.get('funding_rate', 0.0)
    mc_sizing = mc_result.get('sizing_factor', 0.7) if mc_result else 0.7

    direction = _determine_direction(gemini_signal, analytics)

    # Funding alignment check (soft — not a hard gate)
    funding_ok = True
    if direction == 'LONG' and funding > 0.001:
        funding_ok = False
    elif direction == 'SHORT' and funding < -0.001:
        funding_ok = False

    # RSI not in counter-extreme (don't buy at top, don't short at bottom)
    rsi_ok = True
    if direction == 'LONG' and rsi > 75:
        rsi_ok = False
    elif direction == 'SHORT' and rsi < 25:
        rsi_ok = False

    # ── TIER 1: FULL (100% sizing) ──
    # Gemini strong + Polymarket aligned + Volume ok
    if (abs(sentiment) >= config.gemini_threshold
            and poly_aligned
            and vol_ratio >= 1.2
            and rsi_ok):
        return {
            'enter': True,
            'direction': direction,
            'sizing_factor': min(1.0, mc_sizing),
            'tier': 'FULL',
            'reason': f'FULL: sent={sentiment:+.2f} poly=aligned vol={vol_ratio:.1f}x rsi={rsi:.0f}',
        }

    # ── TIER 2: STANDARD (60% sizing) ──
    # Gemini strong + Volume ok (no Polymarket needed)
    if (abs(sentiment) >= config.gemini_threshold
            and vol_ratio >= 1.3
            and rsi_ok):
        return {
            'enter': True,
            'direction': direction,
            'sizing_factor': min(0.6, mc_sizing * 0.6),
            'tier': 'STANDARD',
            'reason': f'STANDARD: sent={sentiment:+.2f} vol={vol_ratio:.1f}x rsi={rsi:.0f}',
        }

    # ── TIER 3: SCOUT (30% sizing) ──
    # Technicals only — RSI extreme + Volume high + Funding confirms
    rsi_extreme_long = rsi < 25  # Oversold
    rsi_extreme_short = rsi > 75  # Overbought

    if rsi_extreme_long and vol_ratio >= 1.5 and funding_ok:
        return {
            'enter': True,
            'direction': 'LONG',
            'sizing_factor': 0.3,
            'tier': 'SCOUT',
            'reason': f'SCOUT: rsi={rsi:.0f}(oversold) vol={vol_ratio:.1f}x funding={funding:.6f}',
        }

    if rsi_extreme_short and vol_ratio >= 1.5 and funding_ok:
        return {
            'enter': True,
            'direction': 'SHORT',
            'sizing_factor': 0.3,
            'tier': 'SCOUT',
            'reason': f'SCOUT: rsi={rsi:.0f}(overbought) vol={vol_ratio:.1f}x funding={funding:.6f}',
        }

    # ── TIER 4: MOMENTUM (40% sizing) ──
    # Gemini has mild sentiment (>0.3) + any volume above average
    if (abs(sentiment) >= 0.3
            and vol_ratio >= 1.0
            and rsi_ok
            and funding_ok):
        return {
            'enter': True,
            'direction': direction,
            'sizing_factor': 0.4,
            'tier': 'MOMENTUM',
            'reason': f'MOMENTUM: sent={sentiment:+.2f} vol={vol_ratio:.1f}x rsi={rsi:.0f}',
        }

    # ── No entry ──
    blockers = []
    if abs(sentiment) < 0.3:
        blockers.append(f'sent_weak={sentiment:+.2f}')
    if vol_ratio < 1.0:
        blockers.append(f'vol_low={vol_ratio:.1f}x')
    if not rsi_ok:
        blockers.append(f'rsi_counter={rsi:.0f}')

    return {
        'enter': False,
        'direction': direction,
        'sizing_factor': 0,
        'tier': 'NONE',
        'reason': '; '.join(blockers) if blockers else 'no_signal',
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
    """Decide whether to exit a position.

    SCOUT exits are faster (profit > 0.3% is enough).
    FULL/STANDARD let winners run until RSI/MACD invalidate.

    Args:
        tier: 'FULL', 'STANDARD', 'SCOUT', or 'MOMENTUM'.
    """
    rsi_now = analytics_current.get('rsi', 50.0)
    rsi_prev = analytics_previous.get('rsi', 50.0)
    macd_now = analytics_current.get('macd', 0.0)
    macd_signal_now = analytics_current.get('macd_signal', 0.0)
    macd_prev = analytics_previous.get('macd', 0.0)
    macd_signal_prev = analytics_previous.get('macd_signal', 0.0)

    # PnL calculation
    if entry_price > 0:
        if direction == 'LONG':
            pnl_pct = (current_price - entry_price) / entry_price
        else:
            pnl_pct = (entry_price - current_price) / entry_price
    else:
        pnl_pct = 0.0

    # --- 1. Emergency stop-loss (all tiers) ---
    if pnl_pct <= -config.sl_emergency_pct:
        return {'exit': True, 'reason': f'SL_EMERGENCY: pnl={pnl_pct*100:+.2f}%'}

    # --- 2. SCOUT/MOMENTUM quick profit take ---
    if tier in ('SCOUT', 'MOMENTUM') and pnl_pct >= 0.003:  # +0.3%
        return {'exit': True, 'reason': f'SCOUT_TP: pnl={pnl_pct*100:+.2f}% (>{0.3}%)'}

    # --- 3. RSI zone inversion ---
    if direction == 'LONG' and rsi_prev >= 70 and rsi_now < 70:
        return {'exit': True, 'reason': f'RSI_CROSS_DOWN: {rsi_prev:.0f}->{rsi_now:.0f}'}

    if direction == 'SHORT' and rsi_prev <= 30 and rsi_now > 30:
        return {'exit': True, 'reason': f'RSI_CROSS_UP: {rsi_prev:.0f}->{rsi_now:.0f}'}

    # --- 4. MACD crosses against ---
    if direction == 'LONG' and macd_prev >= macd_signal_prev and macd_now < macd_signal_now:
        return {'exit': True, 'reason': f'MACD_BEARISH: macd crossed below signal'}

    if direction == 'SHORT' and macd_prev <= macd_signal_prev and macd_now > macd_signal_now:
        return {'exit': True, 'reason': f'MACD_BULLISH: macd crossed above signal'}

    # --- 5. All tiers: take profit at +1.5% (don't be greedy) ---
    if pnl_pct >= 0.015:
        return {'exit': True, 'reason': f'TP_MAX: pnl={pnl_pct*100:+.2f}% (>1.5%)'}

    return {'exit': False, 'reason': f'hold: pnl={pnl_pct*100:+.2f}%'}
