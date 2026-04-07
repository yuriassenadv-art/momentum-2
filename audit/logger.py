# momentum-2/audit/logger.py
"""Audit Logger — Trade logging and daily summary generation.

Writes audit snapshots and computes daily performance metrics.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from config import Config

SIM_BALANCE = 1000.0


def generate_audit(state: dict, trades: list, output_path: str) -> None:
    """Write an audit log with current state snapshot and trade history.

    Args:
        state: Current FSM state dict.
        trades: List of all trade records.
        output_path: Path to write audit_log.json.
    """
    audit = {
        'timestamp': time.time(),
        'datetime': datetime.now(timezone.utc).isoformat(),
        'state_snapshot': state,
        'total_trades': len(trades),
        'trades': trades,
    }

    with open(output_path, 'w') as f:
        json.dump(audit, f, indent=2)


def daily_summary(trades: list) -> dict:
    """Generate a daily performance summary from trade history.

    Args:
        trades: List of trade records (all types).

    Returns:
        Dict with:
            date, total_trades, wins, losses, win_rate,
            total_pnl_usd, total_pnl_pct, best_trade, worst_trade,
            total_fees, assets_traded.
    """
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    # Filter to today's EXIT trades
    exit_trades = []
    for t in trades:
        if t.get('type') != 'EXIT':
            continue
        ts = t.get('timestamp', 0)
        trade_date = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%d')
        if trade_date == today:
            exit_trades.append(t)

    if not exit_trades:
        return {
            'date': today,
            'total_trades': 0,
            'wins': 0,
            'losses': 0,
            'win_rate': 0.0,
            'total_pnl_usd': 0.0,
            'total_pnl_pct': 0.0,
            'best_trade': None,
            'worst_trade': None,
            'total_fees': 0.0,
            'assets_traded': [],
        }

    wins = [t for t in exit_trades if t.get('pnl_usd', 0) > 0]
    losses = [t for t in exit_trades if t.get('pnl_usd', 0) <= 0]

    total_pnl = sum(t.get('pnl_usd', 0) for t in exit_trades)
    total_fees = sum(t.get('fee', 0) for t in exit_trades)
    total_pnl_pct = (total_pnl / SIM_BALANCE) * 100

    assets = list(set(t.get('symbol', '') for t in exit_trades))

    best = max(exit_trades, key=lambda t: t.get('pnl_usd', 0))
    worst = min(exit_trades, key=lambda t: t.get('pnl_usd', 0))

    win_rate = (len(wins) / len(exit_trades) * 100) if exit_trades else 0.0

    return {
        'date': today,
        'total_trades': len(exit_trades),
        'wins': len(wins),
        'losses': len(losses),
        'win_rate': round(win_rate, 2),
        'total_pnl_usd': round(total_pnl, 4),
        'total_pnl_pct': round(total_pnl_pct, 4),
        'best_trade': {
            'symbol': best.get('symbol'),
            'pnl': best.get('pnl_usd', 0),
        },
        'worst_trade': {
            'symbol': worst.get('symbol'),
            'pnl': worst.get('pnl_usd', 0),
        },
        'total_fees': round(total_fees, 4),
        'assets_traded': sorted(assets),
    }
