# momentum-2/execution/guardian.py
"""Position Guardian — Monitors open positions every 10 seconds.

Fetches live prices, recalculates indicators, checks exit conditions,
and closes positions when triggered.
"""
import json
import os
import sys
import time

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from config import Config

from analytical.engine import calculate_rsi, calculate_macd
from decision.engine import should_exit
from orchestration.fsm import FSMManager
from execution.executor import execute_exit

HL_INFO_URL = 'https://api.hyperliquid.xyz/info'


def get_live_prices(symbols: list) -> dict:
    """Fetch current mid prices for a list of symbols.

    Args:
        symbols: List of trading symbols (e.g. ['BTC', 'ETH']).

    Returns:
        Dict mapping symbol to mid price as float.
    """
    resp = requests.post(HL_INFO_URL, json={'type': 'allMids'}, timeout=10)
    resp.raise_for_status()
    mids = resp.json()

    prices = {}
    for sym in symbols:
        if sym in mids:
            prices[sym] = float(mids[sym])
    return prices


def _fetch_candles_5m(symbol: str, count: int = 100) -> list:
    """Fetch 5-minute candles from Hyperliquid.

    Args:
        symbol: Trading symbol.
        count: Number of candles to fetch.

    Returns:
        List of [timestamp, open, high, low, close, volume] candles.
    """
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (count * 5 * 60 * 1000)

    payload = {
        'type': 'candleSnapshot',
        'req': {
            'coin': symbol,
            'interval': '5m',
            'startTime': start_ms,
            'endTime': now_ms,
        },
    }

    resp = requests.post(HL_INFO_URL, json=payload, timeout=15)
    resp.raise_for_status()
    raw = resp.json()

    candles = []
    for c in raw:
        candles.append([
            c.get('t', 0),
            float(c.get('o', 0)),
            float(c.get('h', 0)),
            float(c.get('l', 0)),
            float(c.get('c', 0)),
            float(c.get('v', 0)),
        ])

    return candles


def check_positions(config: Config) -> int:
    """Main guardian cycle: check all active positions for exit conditions.

    1. Load FSM state
    2. Get active positions
    3. Fetch live prices
    4. For each position, fetch 5m candles and calculate RSI + MACD
    5. Check should_exit()
    6. If exit triggered, execute exit and update FSM to Flat

    Args:
        config: Config instance.

    Returns:
        Number of exit actions taken.
    """
    fsm = FSMManager(config.state_path)
    state = fsm.load()
    positions = state.get('positions', {})

    if not positions:
        return 0

    symbols = list(positions.keys())
    live_prices = get_live_prices(symbols)
    actions = 0

    for symbol, pos in list(positions.items()):
        current_price = live_prices.get(symbol)
        if current_price is None:
            continue

        entry_price = pos.get('entry_price', current_price)
        direction = pos.get('direction', 'LONG')
        size = pos.get('size', 0.0)

        # Fetch latest 5m candles and compute indicators
        try:
            candles = _fetch_candles_5m(symbol)
        except Exception:
            continue

        closes = [c[4] for c in candles] if candles else []
        rsi = calculate_rsi(closes)
        macd_data = calculate_macd(closes)

        analytics = {
            'rsi': rsi,
            'macd': macd_data['macd'],
            'macd_signal': macd_data['signal'],
            'macd_histogram': macd_data['histogram'],
        }

        # Check exit conditions
        exit_decision = should_exit(
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            current_price=current_price,
            analytics=analytics,
            config=config,
        )

        if exit_decision['exit']:
            result = execute_exit(
                symbol=symbol,
                entry_price=entry_price,
                current_price=current_price,
                size=size,
                direction=direction,
                reason=exit_decision['reason'],
                config=config,
                dry_run=config.dry_run,
            )

            # Remove position and update FSM to Flat
            del positions[symbol]
            state['positions'] = positions
            fsm.update(state)

            print(f"[GUARDIAN] EXIT {symbol} {direction} | reason={exit_decision['reason']} | "
                  f"pnl={result['pnl_usd']:.2f} USD ({result['pnl_pct']:.2f}%)")
            actions += 1

    return actions


def run_guardian() -> None:
    """Run the guardian loop: check positions every 10 seconds."""
    config = Config()
    print("[GUARDIAN] Starting position guardian...")
    print(f"[GUARDIAN] Mode: {'DRY_RUN' if config.dry_run else 'LIVE'}")
    print(f"[GUARDIAN] Interval: {config.guardian_interval}s")

    while True:
        try:
            actions = check_positions(config)
            if actions > 0:
                print(f"[GUARDIAN] Cycle complete: {actions} action(s) taken")
        except KeyboardInterrupt:
            print("[GUARDIAN] Stopped by user.")
            break
        except Exception as e:
            print(f"[GUARDIAN] Error: {e}")

        time.sleep(config.guardian_interval)


if __name__ == '__main__':
    run_guardian()
