"""Momentum 2 — Monte Carlo Prediction Engine

Geometric Brownian Motion simulation for:
  (a) Validating stop-loss levels by checking how often SL is hit before target
  (b) Adjusting position sizing based on realized volatility

Uses only numpy. No scipy, no pandas.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from config import Config

import numpy as np

# Z-scores for common confidence levels
_Z_SCORES = {
    0.90: 1.645,
    0.95: 1.960,
    0.99: 2.576,
}


def _log_returns(closes: np.ndarray) -> np.ndarray:
    """Calculate log returns from close prices."""
    return np.log(closes[1:] / closes[:-1])


def _optimal_sample_size(
    sigma: float,
    confidence_level: float = 0.95,
    margin_of_error: float = 0.05,
) -> int:
    """Determine Monte Carlo sample size using n = (Z^2 * sigma^2) / E^2.

    Clamped to [100, 5000].
    """
    z = _Z_SCORES.get(confidence_level, 1.960)
    n = (z ** 2 * sigma ** 2) / (margin_of_error ** 2)
    return int(np.clip(n, 100, 5000))


def run_mc_for_asset(
    ohlcv: list[dict],
    confidence_level: float = 0.95,
    margin_of_error: float = 0.05,
) -> dict:
    """Run a Geometric Brownian Motion Monte Carlo simulation.

    Args:
        ohlcv: List of dicts with at least a 'close' key.
        confidence_level: For Z-score lookup (0.90, 0.95, 0.99).
        margin_of_error: Desired precision for sample size formula.

    Returns:
        dict with keys: prediction (prob of up), confidence, expected_range,
                        n_trials, sigma.
    """
    closes = np.array([c['close'] for c in ohlcv], dtype=np.float64)
    if len(closes) < 10:
        return {
            'prediction': 0.5,
            'confidence': 0.0,
            'expected_range': (0.0, 0.0),
            'n_trials': 0,
            'sigma': 0.0,
        }

    log_ret = _log_returns(closes)
    mu = float(np.mean(log_ret))
    sigma = float(np.std(log_ret, ddof=1))

    if sigma < 1e-12:
        return {
            'prediction': 0.5,
            'confidence': 0.0,
            'expected_range': (float(closes[-1]), float(closes[-1])),
            'n_trials': 0,
            'sigma': 0.0,
        }

    n_trials = _optimal_sample_size(sigma, confidence_level, margin_of_error)
    horizon = min(len(log_ret), 30)

    # Vectorized GBM: S(t) = S(0) * exp(cumsum of daily shocks)
    # Each row is one simulated path of `horizon` steps
    rng = np.random.default_rng()
    shocks = rng.normal(
        loc=mu - 0.5 * sigma ** 2,
        scale=sigma,
        size=(n_trials, horizon),
    )
    cumulative = np.cumsum(shocks, axis=1)
    final_log_returns = cumulative[:, -1]

    prob_up = float(np.mean(final_log_returns > 0))

    # Expected range at the chosen confidence level
    alpha = (1 - confidence_level) / 2
    final_prices = closes[-1] * np.exp(final_log_returns)
    lower = float(np.quantile(final_prices, alpha))
    upper = float(np.quantile(final_prices, 1 - alpha))

    return {
        'prediction': round(prob_up, 4),
        'confidence': round(confidence_level, 2),
        'expected_range': (round(lower, 4), round(upper, 4)),
        'n_trials': n_trials,
        'sigma': round(sigma, 6),
    }


def validate_sl(
    ohlcv: list[dict],
    sl_pct: float = 0.03,
    hold_candles: int = 30,
) -> dict:
    """Simulate paths and check how often a stop-loss level is hit.

    Args:
        ohlcv: OHLCV data with 'close' key.
        sl_pct: Stop-loss percentage (e.g. 0.03 for -3%).
        hold_candles: Number of candles to simulate forward.

    Returns:
        dict with: sl_hit_rate, avg_max_drawdown, suggested_sl_adjustment.
    """
    closes = np.array([c['close'] for c in ohlcv], dtype=np.float64)
    if len(closes) < 10:
        return {
            'sl_hit_rate': 0.0,
            'avg_max_drawdown': 0.0,
            'suggested_sl_adjustment': sl_pct,
        }

    log_ret = _log_returns(closes)
    mu = float(np.mean(log_ret))
    sigma = float(np.std(log_ret, ddof=1))

    n_trials = _optimal_sample_size(sigma)
    horizon = hold_candles

    rng = np.random.default_rng()
    shocks = rng.normal(
        loc=mu - 0.5 * sigma ** 2,
        scale=sigma,
        size=(n_trials, horizon),
    )
    # Cumulative log-returns at each step
    cumulative = np.cumsum(shocks, axis=1)
    # Price paths relative to entry (ratio)
    price_ratios = np.exp(cumulative)

    # Drawdowns from entry price (entry = 1.0)
    drawdowns = 1.0 - price_ratios  # positive means price dropped
    max_drawdowns = np.max(drawdowns, axis=1)

    # How often does the path drop below -sl_pct at any point?
    sl_hits = np.sum(max_drawdowns >= sl_pct)
    sl_hit_rate = float(sl_hits / n_trials)
    avg_max_dd = float(np.mean(max_drawdowns))

    # Suggest adjustment: if SL is hit >50% of the time, widen it
    # If hit <20%, tighten it. Otherwise keep.
    if sl_hit_rate > 0.50:
        # Widen to the 75th percentile of max drawdowns
        suggested = float(np.percentile(max_drawdowns, 75))
        suggested = round(max(suggested, sl_pct * 1.2), 4)
    elif sl_hit_rate < 0.20:
        suggested = round(sl_pct * 0.85, 4)
    else:
        suggested = sl_pct

    return {
        'sl_hit_rate': round(sl_hit_rate, 4),
        'avg_max_drawdown': round(avg_max_dd, 4),
        'suggested_sl_adjustment': suggested,
    }


def get_sizing_factor(ohlcv: list[dict]) -> float:
    """Return a 0.5-1.0 multiplier for position sizing based on volatility.

    High volatility -> lower sizing (closer to 0.5).
    Low volatility  -> higher sizing (closer to 1.0).
    """
    closes = np.array([c['close'] for c in ohlcv], dtype=np.float64)
    if len(closes) < 10:
        return 0.75  # Default middle value

    log_ret = _log_returns(closes)
    sigma = float(np.std(log_ret, ddof=1))

    # Annualized vol (assuming ~1440 1-min candles per day, but we use
    # a generic sqrt(252) for daily-equivalent scaling)
    # For intraday data the raw sigma already captures the regime.
    # Map sigma to [0.5, 1.0] linearly:
    #   sigma <= 0.005 -> 1.0  (very calm)
    #   sigma >= 0.04  -> 0.5  (very volatile)
    low_vol = 0.005
    high_vol = 0.04

    if sigma <= low_vol:
        return 1.0
    if sigma >= high_vol:
        return 0.5

    # Linear interpolation: high vol -> low factor
    factor = 1.0 - 0.5 * (sigma - low_vol) / (high_vol - low_vol)
    return round(factor, 4)
