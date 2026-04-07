# momentum-2/backtest/metrics.py
"""Performance Metrics — Comprehensive quant metrics for backtest results.

Calculates win rate, Sharpe, Sortino, max drawdown, Calmar ratio,
Kelly criterion, and breakdowns by tier/direction/hour.
"""
import sys
import os
import math
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import numpy as np


def kelly_criterion(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """Calculate Kelly Criterion optimal sizing.

    f* = (p * b - q) / b
    where p = win_rate, q = 1-p, b = avg_win/avg_loss

    Returns fraction of capital (0 to 1), capped at 0.25 for safety.
    """
    if avg_loss == 0 or win_rate <= 0:
        return 0.0

    b = abs(avg_win / avg_loss)
    q = 1.0 - win_rate
    f_star = (win_rate * b - q) / b

    return max(0.0, min(f_star, 0.25))


def _calc_drawdown(equity_curve: list) -> tuple:
    """Calculate max drawdown from equity curve.

    Returns (max_drawdown_pct, max_drawdown_usd).
    """
    if len(equity_curve) < 2:
        return 0.0, 0.0

    balances = [pt['balance'] for pt in equity_curve]
    peak = balances[0]
    max_dd_pct = 0.0
    max_dd_usd = 0.0

    for bal in balances:
        if bal > peak:
            peak = bal
        dd_usd = peak - bal
        dd_pct = dd_usd / peak if peak > 0 else 0.0
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct
            max_dd_usd = dd_usd

    return max_dd_pct, max_dd_usd


def _group_metrics(trades: list) -> dict:
    """Calculate basic metrics for a group of trades."""
    if not trades:
        return {
            'total': 0, 'wins': 0, 'losses': 0,
            'win_rate': 0.0, 'pnl_usd': 0.0,
        }

    wins = [t for t in trades if t['pnl_usd'] > 0]
    losses = [t for t in trades if t['pnl_usd'] <= 0]

    return {
        'total': len(trades),
        'wins': len(wins),
        'losses': len(losses),
        'win_rate': len(wins) / len(trades) if trades else 0.0,
        'pnl_usd': sum(t['pnl_usd'] for t in trades),
    }


def calculate_metrics(trades: list, initial_balance: float, equity_curve: list) -> dict:
    """Calculate comprehensive quant metrics.

    Args:
        trades: List of trade dicts with keys:
            symbol, direction, tier, entry_price, exit_price,
            entry_time, exit_time, pnl_usd, pnl_pct, fee_usd,
            size_usd, reason
        initial_balance: Starting capital in USD.
        equity_curve: List of {timestamp, balance} dicts.

    Returns:
        Dict with all performance metrics.
    """
    if not trades:
        return {
            'total_trades': 0, 'wins': 0, 'losses': 0, 'win_rate': 0.0,
            'total_pnl_usd': 0.0, 'total_pnl_pct': 0.0,
            'avg_win': 0.0, 'avg_loss': 0.0,
            'profit_factor': 0.0, 'expectancy': 0.0,
            'sharpe_ratio': 0.0, 'sortino_ratio': 0.0,
            'max_drawdown_pct': 0.0, 'max_drawdown_usd': 0.0,
            'calmar_ratio': 0.0, 'avg_hold_time_minutes': 0.0,
            'trades_per_day': 0.0, 'best_trade': 0.0, 'worst_trade': 0.0,
            'by_tier': {}, 'by_direction': {}, 'by_hour': {},
            'kelly_criterion': 0.0, 'fee_impact_pct': 0.0,
        }

    # Basic counts
    wins = [t for t in trades if t['pnl_usd'] > 0]
    losses = [t for t in trades if t['pnl_usd'] <= 0]
    win_rate = len(wins) / len(trades)
    loss_rate = 1.0 - win_rate

    # PnL
    total_pnl_usd = sum(t['pnl_usd'] for t in trades)
    total_pnl_pct = (total_pnl_usd / initial_balance) * 100.0

    # Averages
    avg_win = np.mean([t['pnl_usd'] for t in wins]) if wins else 0.0
    avg_loss = np.mean([t['pnl_usd'] for t in losses]) if losses else 0.0

    # Profit factor
    gross_wins = sum(t['pnl_usd'] for t in wins)
    gross_losses = abs(sum(t['pnl_usd'] for t in losses))
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else float('inf')

    # Expectancy
    expectancy = (win_rate * float(avg_win)) - (loss_rate * abs(float(avg_loss)))

    # Returns series for Sharpe/Sortino
    returns = np.array([t['pnl_pct'] for t in trades])

    # Sharpe ratio (annualized, assuming ~8760 hours/year and avg hold time)
    mean_ret = np.mean(returns)
    std_ret = np.std(returns, ddof=1) if len(returns) > 1 else 1e-10

    # Estimate trades per year for annualization
    if len(trades) >= 2:
        first_time = trades[0]['entry_time']
        last_time = trades[-1]['exit_time']
        duration_hours = max((last_time - first_time) / 3600.0, 1.0)
        trades_per_hour = len(trades) / duration_hours
        trades_per_year = trades_per_hour * 8760.0
    else:
        trades_per_year = 365.0

    sharpe_ratio = (mean_ret / std_ret) * math.sqrt(trades_per_year) if std_ret > 1e-10 else 0.0

    # Sortino ratio (downside deviation only)
    negative_returns = returns[returns < 0]
    downside_std = np.std(negative_returns, ddof=1) if len(negative_returns) > 1 else 1e-10
    sortino_ratio = (mean_ret / downside_std) * math.sqrt(trades_per_year) if downside_std > 1e-10 else 0.0

    # Drawdown
    max_dd_pct, max_dd_usd = _calc_drawdown(equity_curve)

    # Calmar ratio (annualized return / max drawdown)
    if len(trades) >= 2:
        first_time = trades[0]['entry_time']
        last_time = trades[-1]['exit_time']
        duration_years = max((last_time - first_time) / (365.25 * 24 * 3600), 1e-6)
        annualized_return_pct = (total_pnl_usd / initial_balance) / duration_years
    else:
        annualized_return_pct = total_pnl_usd / initial_balance

    calmar_ratio = annualized_return_pct / max_dd_pct if max_dd_pct > 0 else 0.0

    # Hold time
    hold_times = []
    for t in trades:
        hold_min = (t['exit_time'] - t['entry_time']) / 60.0
        hold_times.append(hold_min)
    avg_hold_time = float(np.mean(hold_times)) if hold_times else 0.0

    # Trades per day
    if len(trades) >= 2:
        duration_days = max((trades[-1]['exit_time'] - trades[0]['entry_time']) / 86400.0, 1e-6)
        trades_per_day = len(trades) / duration_days
    else:
        trades_per_day = float(len(trades))

    # Best / worst trade
    best_trade = max(t['pnl_usd'] for t in trades)
    worst_trade = min(t['pnl_usd'] for t in trades)

    # Fee impact
    total_fees = sum(t.get('fee_usd', 0.0) for t in trades)
    gross_pnl = total_pnl_usd + total_fees
    fee_impact_pct = (total_fees / abs(gross_pnl)) * 100.0 if abs(gross_pnl) > 0 else 0.0

    # Kelly
    kelly = kelly_criterion(win_rate, float(avg_win), float(avg_loss))

    # ── Breakdowns ──

    # By tier
    tiers = set(t.get('tier', 'UNKNOWN') for t in trades)
    by_tier = {}
    for tier in sorted(tiers):
        tier_trades = [t for t in trades if t.get('tier') == tier]
        by_tier[tier] = _group_metrics(tier_trades)

    # By direction
    by_direction = {}
    for d in ('LONG', 'SHORT'):
        dir_trades = [t for t in trades if t.get('direction') == d]
        by_direction[d] = _group_metrics(dir_trades)

    # By hour of day (UTC)
    by_hour = {}
    for t in trades:
        hour = datetime.utcfromtimestamp(t['entry_time']).hour
        if hour not in by_hour:
            by_hour[hour] = []
        by_hour[hour].append(t)
    by_hour_metrics = {}
    for hour in sorted(by_hour.keys()):
        h_trades = by_hour[hour]
        h_wins = len([t for t in h_trades if t['pnl_usd'] > 0])
        by_hour_metrics[hour] = {
            'total': len(h_trades),
            'win_rate': h_wins / len(h_trades) if h_trades else 0.0,
            'pnl_usd': sum(t['pnl_usd'] for t in h_trades),
        }

    # By symbol
    symbols = set(t['symbol'] for t in trades)
    by_symbol = {}
    for sym in symbols:
        sym_trades = [t for t in trades if t['symbol'] == sym]
        by_symbol[sym] = _group_metrics(sym_trades)

    return {
        'total_trades': len(trades),
        'wins': len(wins),
        'losses': len(losses),
        'win_rate': win_rate,
        'total_pnl_usd': total_pnl_usd,
        'total_pnl_pct': total_pnl_pct,
        'avg_win': float(avg_win),
        'avg_loss': float(avg_loss),
        'profit_factor': profit_factor,
        'expectancy': expectancy,
        'sharpe_ratio': sharpe_ratio,
        'sortino_ratio': sortino_ratio,
        'max_drawdown_pct': max_dd_pct * 100.0,
        'max_drawdown_usd': max_dd_usd,
        'calmar_ratio': calmar_ratio,
        'avg_hold_time_minutes': avg_hold_time,
        'trades_per_day': trades_per_day,
        'best_trade': best_trade,
        'worst_trade': worst_trade,
        'by_tier': by_tier,
        'by_direction': by_direction,
        'by_hour': by_hour_metrics,
        'by_symbol': by_symbol,
        'kelly_criterion': kelly,
        'fee_impact_pct': fee_impact_pct,
        'total_fees_usd': total_fees,
    }
