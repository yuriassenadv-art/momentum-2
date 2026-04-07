# momentum-2/decision/engine.py
"""Decision Engine — Entry and exit logic.

All conditions must pass for entry. Any single condition triggers exit.
No adaptive weights, no ML — pure rule-based gating.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from config import Config


def should_enter(
    symbol: str,
    gemini_signal: dict,
    poly_signal: dict,
    analytics: dict,
    mc_result: dict,
    config: Config,
) -> dict:
    """Decide whether to enter a position.

    All conditions must be True to enter.

    Args:
        symbol: Asset symbol.
        gemini_signal: Dict with 'sentiment' float (-1 to 1).
        poly_signal: Dict with 'aligned_with_gemini' bool.
        analytics: Dict from analytical engine with rsi, volume_ratio,
                   funding_rate, etc.
        mc_result: Dict from Monte Carlo with 'sizing_factor' (0.5-1.0)
                   and 'sl_hit_rate' float.
        config: Config instance.

    Returns:
        Dict with enter (bool), direction (str), sizing_factor (float),
        reason (str).
    """
    reasons = []

    # --- 1. Gemini sentiment must exceed threshold ---
    sentiment = gemini_signal.get('sentiment', 0.0)
    if abs(sentiment) <= config.gemini_threshold:
        reasons.append(f'gemini_weak: |{sentiment:.2f}| <= {config.gemini_threshold}')

    direction = 'LONG' if sentiment > 0 else 'SHORT'

    # --- 2. Polymarket alignment ---
    if not poly_signal.get('aligned_with_gemini', False):
        reasons.append('poly_misaligned')

    # --- 3. RSI not in extreme zone ---
    rsi = analytics.get('rsi', 50.0)
    if direction == 'LONG':
        if rsi < config.rsi_oversold or rsi > config.rsi_overbought:
            reasons.append(f'rsi_extreme: {rsi:.1f} outside [{config.rsi_oversold}-{config.rsi_overbought}]')
    else:  # SHORT
        # For shorts: inverted — RSI should be between overbought..oversold inverted
        # i.e., RSI should NOT be in extreme low (oversold) for shorts
        if rsi < config.rsi_oversold or rsi > config.rsi_overbought:
            reasons.append(f'rsi_extreme: {rsi:.1f} outside [{config.rsi_oversold}-{config.rsi_overbought}]')

    # --- 4. Volume confirmation ---
    vol_ratio = analytics.get('volume_ratio', 0.0)
    if vol_ratio < config.volume_confirmation:
        reasons.append(f'vol_low: {vol_ratio:.2f} < {config.volume_confirmation}')

    # --- 5. Funding rate alignment ---
    funding = analytics.get('funding_rate', 0.0)
    if direction == 'LONG' and funding > 0:
        reasons.append(f'funding_against_long: {funding:.6f}')
    elif direction == 'SHORT' and funding < 0:
        reasons.append(f'funding_against_short: {funding:.6f}')

    # --- 6. Monte Carlo sizing factor ---
    sizing_factor = mc_result.get('sizing_factor', 0.5)
    sizing_factor = max(0.5, min(1.0, sizing_factor))

    # --- 7. Monte Carlo SL validation ---
    sl_hit_rate = mc_result.get('sl_hit_rate', 1.0)
    if sl_hit_rate >= 0.40:
        reasons.append(f'mc_sl_risky: sl_hit_rate={sl_hit_rate:.2f} >= 0.40')

    if reasons:
        return {
            'enter': False,
            'direction': direction,
            'sizing_factor': sizing_factor,
            'reason': '; '.join(reasons),
        }

    return {
        'enter': True,
        'direction': direction,
        'sizing_factor': sizing_factor,
        'reason': 'all_gates_passed',
    }


def should_exit(
    symbol: str,
    analytics_current: dict,
    analytics_previous: dict,
    entry_price: float,
    current_price: float,
    direction: str,
    config: Config,
) -> dict:
    """Decide whether to exit a position.

    Any single condition triggers exit.

    Args:
        symbol: Asset symbol.
        analytics_current: Current analytics dict (rsi, macd, macd_signal).
        analytics_previous: Previous cycle analytics dict.
        entry_price: Position entry price.
        current_price: Current market price.
        direction: 'LONG' or 'SHORT'.
        config: Config instance.

    Returns:
        Dict with exit (bool), reason (str).
    """
    rsi_now = analytics_current.get('rsi', 50.0)
    rsi_prev = analytics_previous.get('rsi', 50.0)
    macd_now = analytics_current.get('macd', 0.0)
    macd_signal_now = analytics_current.get('macd_signal', 0.0)
    macd_prev = analytics_previous.get('macd', 0.0)
    macd_signal_prev = analytics_previous.get('macd_signal', 0.0)

    # --- 1. RSI zone inversion ---
    if direction == 'LONG':
        # RSI crosses 70 downward
        if rsi_prev >= 70 and rsi_now < 70:
            return {'exit': True, 'reason': f'rsi_cross_down: {rsi_prev:.1f} -> {rsi_now:.1f}'}
    else:  # SHORT
        # RSI crosses 30 upward
        if rsi_prev <= 30 and rsi_now > 30:
            return {'exit': True, 'reason': f'rsi_cross_up: {rsi_prev:.1f} -> {rsi_now:.1f}'}

    # --- 2. MACD crosses against direction ---
    if direction == 'LONG':
        # MACD was above signal, now below
        if macd_prev >= macd_signal_prev and macd_now < macd_signal_now:
            return {'exit': True, 'reason': f'macd_bearish_cross: macd={macd_now:.6f} < signal={macd_signal_now:.6f}'}
    else:  # SHORT
        # MACD was below signal, now above
        if macd_prev <= macd_signal_prev and macd_now > macd_signal_now:
            return {'exit': True, 'reason': f'macd_bullish_cross: macd={macd_now:.6f} > signal={macd_signal_now:.6f}'}

    # --- 3. Emergency stop-loss ---
    if entry_price > 0:
        if direction == 'LONG':
            pnl_pct = (current_price - entry_price) / entry_price
        else:
            pnl_pct = (entry_price - current_price) / entry_price

        if pnl_pct <= -config.sl_emergency_pct:
            return {'exit': True, 'reason': f'sl_emergency: pnl={pnl_pct:.4f} <= -{config.sl_emergency_pct}'}

    return {'exit': False, 'reason': 'hold'}
