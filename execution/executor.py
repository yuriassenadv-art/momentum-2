# momentum-2/execution/executor.py
"""Execution — Entry and exit order management.

Handles position sizing, order placement (dry-run and live),
PnL calculation, and trade history logging.
"""
import json
import os
import sys
import time
import uuid

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from config import Config

SIM_BALANCE = 1000.0
HL_INFO_URL = 'https://api.hyperliquid.xyz/info'


def get_balance(dry_run: bool = True, trade_history_path: str = None) -> float:
    """Get current account balance.

    DRY_RUN: reads trade_history.json, sums EXIT pnl_usd, adds to SIM_BALANCE.
    LIVE: queries Hyperliquid SDK.

    Args:
        dry_run: If True, simulate from trade history.
        trade_history_path: Path to trade_history.json (defaults to config).

    Returns:
        Current balance as float.
    """
    if dry_run:
        if trade_history_path is None:
            cfg = Config()
            trade_history_path = cfg.trade_history_path

        balance = SIM_BALANCE
        if os.path.exists(trade_history_path):
            with open(trade_history_path, 'r') as f:
                trades = json.load(f)
            for trade in trades:
                if trade.get('type') == 'EXIT':
                    balance += trade.get('pnl_usd', 0.0)
        return balance
    else:
        from hyperliquid.info import Info

        cfg = Config()
        info = Info(cfg.hl_base_url, skip_ws=True)
        user_state = info.user_state(cfg.hl_wallet)
        return float(user_state.get('marginSummary', {}).get('accountValue', 0.0))


def _get_mid_price(symbol: str) -> float:
    """Fetch current mid price from Hyperliquid allMids endpoint.

    Args:
        symbol: Trading symbol (e.g. 'BTC', 'ETH').

    Returns:
        Mid price as float.

    Raises:
        ValueError: If symbol not found in response.
    """
    resp = requests.post(HL_INFO_URL, json={'type': 'allMids'}, timeout=10)
    resp.raise_for_status()
    mids = resp.json()

    if symbol not in mids:
        raise ValueError(f"Symbol {symbol} not found in allMids response")

    return float(mids[symbol])


def execute_entry(symbol: str, direction: str, sizing_factor: float,
                  config: Config, dry_run: bool = True) -> dict:
    """Execute a trade entry.

    Calculates position size: balance * 0.05 * sizing_factor * leverage / entry_price.
    Uses market orders (taker fee: 0.045%).

    Args:
        symbol: Trading symbol.
        direction: 'LONG' or 'SHORT'.
        sizing_factor: Multiplier for position sizing (0.0 to 1.0).
        config: Config instance.
        dry_run: If True, simulate the order.

    Returns:
        Dict with status, order_id, entry_price, size, fee, direction.
    """
    balance = get_balance(dry_run=dry_run, trade_history_path=config.trade_history_path)
    entry_price = _get_mid_price(symbol)

    notional = balance * config.position_pct * sizing_factor * config.leverage
    size = notional / entry_price
    fee = notional * config.taker_fee

    if dry_run:
        order_id = f"SIM-{uuid.uuid4().hex[:12]}"
        result = {
            'status': 'FILLED',
            'order_id': order_id,
            'entry_price': entry_price,
            'size': round(size, 6),
            'fee': round(fee, 4),
            'direction': direction,
            'symbol': symbol,
            'timestamp': time.time(),
            'type': 'ENTRY',
            'dry_run': True,
        }
    else:
        from hyperliquid.exchange import Exchange
        from hyperliquid.utils import constants

        exchange = Exchange(config.hl_private_key, constants.MAINNET_API_URL)
        is_buy = direction == 'LONG'
        order_result = exchange.market_open(symbol, is_buy, size, None)

        status = order_result.get('status', 'UNKNOWN')
        statuses = order_result.get('response', {}).get('data', {}).get('statuses', [{}])
        filled = statuses[0] if statuses else {}

        result = {
            'status': 'FILLED' if status == 'ok' else status,
            'order_id': filled.get('resting', {}).get('oid', str(uuid.uuid4().hex[:12])),
            'entry_price': entry_price,
            'size': round(size, 6),
            'fee': round(fee, 4),
            'direction': direction,
            'symbol': symbol,
            'timestamp': time.time(),
            'type': 'ENTRY',
            'dry_run': False,
        }

    return result


def execute_exit(symbol: str, entry_price: float, current_price: float,
                 size: float, direction: str, reason: str,
                 config: Config, dry_run: bool = True) -> dict:
    """Execute a trade exit.

    Calculates PnL (gross and net after fees) and logs to trade_history.json.

    Args:
        symbol: Trading symbol.
        entry_price: Original entry price.
        current_price: Current market price (exit price).
        size: Position size.
        direction: 'LONG' or 'SHORT'.
        reason: Exit reason (e.g. 'TP', 'SL', 'signal_reversal').
        config: Config instance.
        dry_run: If True, simulate the exit.

    Returns:
        Dict with status, pnl_pct, pnl_usd, fee, reason.
    """
    if direction == 'LONG':
        pnl_pct = (current_price - entry_price) / entry_price
    else:
        pnl_pct = (entry_price - current_price) / entry_price

    notional_exit = size * current_price
    fee = notional_exit * config.taker_fee
    pnl_gross = pnl_pct * size * entry_price
    pnl_net = pnl_gross - fee

    if not dry_run:
        from hyperliquid.exchange import Exchange
        from hyperliquid.utils import constants

        exchange = Exchange(config.hl_private_key, constants.MAINNET_API_URL)
        is_buy = direction == 'SHORT'  # close SHORT = buy, close LONG = sell
        exchange.market_open(symbol, is_buy, size, None)

    result = {
        'status': 'CLOSED',
        'symbol': symbol,
        'direction': direction,
        'entry_price': entry_price,
        'exit_price': current_price,
        'size': size,
        'pnl_pct': round(pnl_pct * 100, 4),
        'pnl_usd': round(pnl_net, 4),
        'pnl_gross': round(pnl_gross, 4),
        'fee': round(fee, 4),
        'reason': reason,
        'timestamp': time.time(),
        'type': 'EXIT',
        'dry_run': dry_run,
    }

    log_trade(result, config.trade_history_path)
    return result


def log_trade(entry: dict, history_path: str = None) -> None:
    """Append a trade record to trade_history.json.

    Args:
        entry: Trade dict to log.
        history_path: Path to trade_history.json (defaults to config).
    """
    if history_path is None:
        cfg = Config()
        history_path = cfg.trade_history_path

    trades = []
    if os.path.exists(history_path):
        with open(history_path, 'r') as f:
            trades = json.load(f)

    trades.append(entry)

    with open(history_path, 'w') as f:
        json.dump(trades, f, indent=2)
