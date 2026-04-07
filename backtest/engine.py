# momentum-2/backtest/engine.py
"""Backtesting Engine — Replay historical Hyperliquid data through the pipeline.

Downloads OHLCV candles, replays them chronologically, simulates entries/exits
using the same 5-tier logic as the live system (minus Gemini/market signal),
and calculates comprehensive performance metrics.
"""
import sys
import os
import json
import time
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from config import Config

import requests
import numpy as np
import pandas as pd

from backtest.metrics import calculate_metrics
from backtest.report import generate_report, save_report

# ── Hyperliquid API ──

HL_INFO_URL = 'https://api.hyperliquid.xyz/info'
HEADERS = {'Content-Type': 'application/json'}


def _hl_post(payload: dict, timeout: int = 15) -> dict:
    """POST to Hyperliquid /info with basic retry on 429."""
    for attempt in range(5):
        resp = requests.post(HL_INFO_URL, headers=HEADERS, json=payload, timeout=timeout)
        if resp.status_code == 429:
            wait = min(2.0 ** attempt, 30.0)
            print(f'  [rate-limited] waiting {wait:.1f}s...')
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()
    return resp.json()


# ── Technical Indicators ──

def calc_rsi(closes: list, window: int = 14) -> float:
    """Calculate RSI from a list of closing prices."""
    if len(closes) < window + 1:
        return 50.0
    delta = pd.Series(closes, dtype=float).diff()
    gain = delta.clip(lower=0).rolling(window=window).mean()
    loss = (-delta.clip(upper=0)).rolling(window=window).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    val = rsi.iloc[-1]
    return float(val) if not np.isnan(val) else 50.0


def calc_macd(closes: list, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple:
    """Calculate MACD line and signal line. Returns (macd, signal)."""
    if len(closes) < slow + signal:
        return 0.0, 0.0
    s = pd.Series(closes, dtype=float)
    ema_fast = s.ewm(span=fast, adjust=False).mean()
    ema_slow = s.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    sig = macd.ewm(span=signal, adjust=False).mean()
    return float(macd.iloc[-1]), float(sig.iloc[-1])


# ── Data Fetching ──

def fetch_top_symbols(limit: int = 20, min_volume: float = 1_000_000) -> list:
    """Fetch top symbols by 24h volume from Hyperliquid.

    Args:
        limit: Max number of symbols to return.
        min_volume: Minimum 24h notional volume in USD.

    Returns:
        List of symbol name strings, sorted by volume descending.
    """
    print('  Fetching Hyperliquid meta (symbol list + volumes)...')
    data = _hl_post({'type': 'metaAndAssetCtxs'})
    universe = data[0]['universe']
    asset_ctxs = data[1]

    pairs = []
    for asset_info, ctx in zip(universe, asset_ctxs):
        vol = float(ctx.get('dayNtlVlm', 0))
        if vol >= min_volume:
            pairs.append({
                'symbol': asset_info['name'],
                'volume': vol,
                'funding': float(ctx.get('funding', 0)),
            })

    pairs.sort(key=lambda x: x['volume'], reverse=True)
    selected = pairs[:limit]
    print(f'  Selected {len(selected)} symbols (min vol ${min_volume:,.0f})')
    return selected


def _fetch_candles(symbol: str, interval: str, start_ms: int, end_ms: int) -> list:
    """Fetch candles from Hyperliquid for a given range.

    Returns list of dicts with keys: t, o, h, l, c, v
    """
    payload = {
        'type': 'candleSnapshot',
        'req': {
            'coin': symbol,
            'interval': interval,
            'startTime': start_ms,
            'endTime': end_ms,
        },
    }
    raw = _hl_post(payload)
    candles = []
    for c in raw:
        candles.append({
            't': int(c['t']),
            'o': float(c['o']),
            'h': float(c['h']),
            'l': float(c['l']),
            'c': float(c['c']),
            'v': float(c['v']),
        })
    # Sort by timestamp ascending (oldest first)
    candles.sort(key=lambda x: x['t'])
    return candles


def fetch_historical_data(
    symbols: list,
    hours: int,
    timeframe_entry: str = '1m',
    timeframe_analysis: str = '5m',
) -> dict:
    """Fetch historical candles from Hyperliquid.

    Args:
        symbols: List of dicts with 'symbol' and 'funding' keys.
        hours: Number of hours of history to fetch.
        timeframe_entry: Entry timeframe (1m).
        timeframe_analysis: Analysis timeframe (5m).

    Returns:
        Dict of symbol -> {ohlcv_1m: [...], ohlcv_5m: [...], funding: float}
    """
    end_ms = int(time.time() * 1000)
    # Extra lookback: 26 candles * 5m = 130min for MACD warmup
    lookback_buffer_ms = 35 * 5 * 60 * 1000  # 35 candles of 5m
    start_ms = end_ms - (hours * 3600 * 1000) - lookback_buffer_ms

    result = {}
    total = len(symbols)

    for i, sym_info in enumerate(symbols):
        symbol = sym_info['symbol']
        funding = sym_info.get('funding', 0.0)
        print(f'  [{i+1}/{total}] Fetching {symbol}...')

        try:
            # Fetch 5m candles
            candles_5m = _fetch_candles(symbol, timeframe_analysis, start_ms, end_ms)
            time.sleep(0.1)

            # Fetch 1m candles — may need multiple calls for large ranges
            # Hyperliquid returns max ~5000 candles per call
            candles_1m = _fetch_candles(symbol, timeframe_entry, start_ms, end_ms)
            time.sleep(0.1)

            if not candles_5m or not candles_1m:
                print(f'    Skipping {symbol}: no candle data')
                continue

            result[symbol] = {
                'ohlcv_1m': candles_1m,
                'ohlcv_5m': candles_5m,
                'funding': funding,
            }
            print(f'    {symbol}: {len(candles_5m)} 5m candles, {len(candles_1m)} 1m candles')

        except Exception as e:
            print(f'    Error fetching {symbol}: {e}')
            continue

    return result


# ── Backtest Core ──

def _determine_direction_technical(rsi: float, macd: float, macd_signal: float) -> str:
    """Determine direction from technicals only (no Gemini)."""
    # RSI extremes first
    if rsi < 35:
        return 'LONG'
    if rsi > 65:
        return 'SHORT'

    # MACD direction
    if macd > macd_signal:
        return 'LONG'
    if macd < macd_signal:
        return 'SHORT'

    return 'LONG'


def _check_entry(
    rsi: float,
    macd: float,
    macd_signal: float,
    direction: str,
) -> dict:
    """Check backtest entry tiers (technical-only, no Gemini/market signal).

    Tiers available in backtest:
      MOMENTUM: MACD confirms + RSI not counter-extreme
      SCOUT:    RSI < 25 (LONG) or RSI > 75 (SHORT)
      MICRO:    RSI trending (< 40 or > 60) + MACD confirms

    Returns dict with 'enter', 'tier', 'direction', 'sizing_factor'.
    """
    macd_confirms = (
        (direction == 'LONG' and macd > macd_signal) or
        (direction == 'SHORT' and macd < macd_signal)
    )

    rsi_counter = (
        (direction == 'LONG' and rsi > 78) or
        (direction == 'SHORT' and rsi < 22)
    )

    # MOMENTUM: MACD confirms + RSI not counter-extreme
    if macd_confirms and not rsi_counter:
        return {
            'enter': True, 'tier': 'MOMENTUM',
            'direction': direction, 'sizing_factor': 0.4,
        }

    # SCOUT: RSI extreme reversal
    if rsi < 25 and direction == 'LONG':
        return {
            'enter': True, 'tier': 'SCOUT',
            'direction': 'LONG', 'sizing_factor': 0.3,
        }
    if rsi > 75 and direction == 'SHORT':
        return {
            'enter': True, 'tier': 'SCOUT',
            'direction': 'SHORT', 'sizing_factor': 0.3,
        }

    # MICRO: RSI trending + MACD confirms
    if rsi < 40 and macd_confirms and direction == 'LONG':
        return {
            'enter': True, 'tier': 'MICRO',
            'direction': 'LONG', 'sizing_factor': 0.2,
        }
    if rsi > 60 and macd_confirms and direction == 'SHORT':
        return {
            'enter': True, 'tier': 'MICRO',
            'direction': 'SHORT', 'sizing_factor': 0.2,
        }

    return {'enter': False, 'tier': 'NONE', 'direction': direction, 'sizing_factor': 0.0}


def _check_exit(
    entry_price: float,
    current_price: float,
    direction: str,
    tier: str,
    rsi_now: float,
    rsi_prev: float,
    macd_now: float,
    macd_signal_now: float,
    macd_prev: float,
    macd_signal_prev: float,
    sl_pct: float,
) -> dict:
    """Check exit conditions.

    Returns dict with 'exit' and 'reason'.
    """
    # PnL calculation
    if entry_price > 0:
        if direction == 'LONG':
            pnl_pct = (current_price - entry_price) / entry_price
        else:
            pnl_pct = (entry_price - current_price) / entry_price
    else:
        pnl_pct = 0.0

    # 1. Emergency SL
    if pnl_pct <= -sl_pct:
        return {'exit': True, 'reason': f'SL:{pnl_pct*100:+.2f}%'}

    # 2. MICRO/SCOUT quick TP at +0.2%
    if tier in ('MICRO', 'SCOUT') and pnl_pct >= 0.002:
        return {'exit': True, 'reason': f'QUICK_TP:{pnl_pct*100:+.2f}%'}

    # 3. MOMENTUM TP at +0.3%
    if tier == 'MOMENTUM' and pnl_pct >= 0.003:
        return {'exit': True, 'reason': f'MOM_TP:{pnl_pct*100:+.2f}%'}

    # 4. RSI zone inversion (5m)
    if direction == 'LONG' and rsi_prev >= 70 and rsi_now < 70:
        return {'exit': True, 'reason': f'RSI_DOWN:{rsi_prev:.0f}->{rsi_now:.0f}'}
    if direction == 'SHORT' and rsi_prev <= 30 and rsi_now > 30:
        return {'exit': True, 'reason': f'RSI_UP:{rsi_prev:.0f}->{rsi_now:.0f}'}

    # 5. MACD crosses against direction (5m)
    if direction == 'LONG' and macd_prev >= macd_signal_prev and macd_now < macd_signal_now:
        return {'exit': True, 'reason': 'MACD_CROSS_DOWN'}
    if direction == 'SHORT' and macd_prev <= macd_signal_prev and macd_now > macd_signal_now:
        return {'exit': True, 'reason': 'MACD_CROSS_UP'}

    return {'exit': False, 'reason': f'hold:{pnl_pct*100:+.2f}%'}


def run_backtest(
    hours: int = 72,
    initial_balance: float = 1000,
    leverage: float = 3.0,
    position_pct: float = 0.05,
    sl_emergency_pct: float = 0.03,
    taker_fee: float = 0.00045,
    gemini_threshold: float = 0.5,
    max_symbols: int = 20,
    min_volume: float = 1_000_000,
) -> dict:
    """Run full backtest simulation.

    Downloads historical data, replays candles chronologically, simulates
    entry/exit decisions, and calculates performance metrics.

    Flow per 5-minute window:
    1. Calculate RSI(14), MACD(12,26,9) from 5m candles up to current point
    2. Determine direction from MACD + RSI (technical-only in backtest)
    3. Apply 3-tier entry logic (MOMENTUM, SCOUT, MICRO)
    4. For active positions, check exit on each 1m candle within the window
    5. Track balance, fees, PnL per trade

    Args:
        hours: Number of hours of historical data to backtest.
        initial_balance: Starting capital in USD.
        leverage: Leverage multiplier.
        position_pct: Fraction of balance per trade (0.05 = 5%).
        sl_emergency_pct: Stop-loss emergency percentage (0.03 = 3%).
        taker_fee: Taker fee rate (0.00045 = 0.045%).
        gemini_threshold: Gemini sentiment threshold (unused in backtest).
        max_symbols: Max symbols to trade.
        min_volume: Minimum 24h volume to include symbol.

    Returns:
        Dict with keys: trades, metrics, equity_curve, report.
    """
    print('\n' + '=' * 50)
    print(f'  MOMENTUM 2 BACKTESTER — {hours}h')
    print('=' * 50)
    print(f'  Balance: ${initial_balance}  Leverage: {leverage}x')
    print(f'  Position: {position_pct*100}%  SL: {sl_emergency_pct*100}%')
    print(f'  Fee: {taker_fee*100:.3f}%')
    print()

    # ── Phase 1: Fetch data ──
    print('[1/3] Fetching historical data...')
    symbols = fetch_top_symbols(limit=max_symbols, min_volume=min_volume)
    if not symbols:
        print('  No symbols found. Aborting.')
        return {'trades': [], 'metrics': {}, 'equity_curve': [], 'report': 'No data'}

    historical = fetch_historical_data(symbols, hours)
    if not historical:
        print('  No historical data fetched. Aborting.')
        return {'trades': [], 'metrics': {}, 'equity_curve': [], 'report': 'No data'}

    print(f'  Data ready for {len(historical)} symbols.\n')

    # ── Phase 2: Build unified timeline ──
    print('[2/3] Running simulation...')

    # Determine backtest window from actual data
    all_5m_timestamps = set()
    for sym, data in historical.items():
        for c in data['ohlcv_5m']:
            all_5m_timestamps.add(c['t'])

    if not all_5m_timestamps:
        print('  No 5m timestamps found. Aborting.')
        return {'trades': [], 'metrics': {}, 'equity_curve': [], 'report': 'No data'}

    sorted_5m_ts = sorted(all_5m_timestamps)

    # Skip first 35 candles for indicator warmup
    warmup_candles = 35
    if len(sorted_5m_ts) <= warmup_candles:
        print('  Not enough candles for warmup. Aborting.')
        return {'trades': [], 'metrics': {}, 'equity_curve': [], 'report': 'Insufficient data'}

    backtest_start_ts = sorted_5m_ts[warmup_candles]
    backtest_end_ts = sorted_5m_ts[-1]

    print(f'  Backtest window: {datetime.utcfromtimestamp(backtest_start_ts/1000).strftime("%Y-%m-%d %H:%M")} '
          f'to {datetime.utcfromtimestamp(backtest_end_ts/1000).strftime("%Y-%m-%d %H:%M")} UTC')

    # Pre-index 1m candles per symbol by timestamp for fast lookup
    candles_1m_by_sym = {}
    for sym, data in historical.items():
        candles_1m_by_sym[sym] = sorted(data['ohlcv_1m'], key=lambda x: x['t'])

    # Pre-index 5m candles per symbol as sorted list
    candles_5m_by_sym = {}
    for sym, data in historical.items():
        candles_5m_by_sym[sym] = sorted(data['ohlcv_5m'], key=lambda x: x['t'])

    # ── Phase 3: Simulate ──
    balance = initial_balance
    trades = []
    equity_curve = [{'timestamp': backtest_start_ts / 1000.0, 'balance': balance}]

    # Active positions: symbol -> {entry_price, entry_time, direction, tier, size_usd,
    #                               size_units, prev_rsi, prev_macd, prev_macd_signal}
    positions = {}

    # Process each 5m window
    active_5m_ts = [ts for ts in sorted_5m_ts if ts >= backtest_start_ts]
    total_windows = len(active_5m_ts)
    progress_step = max(total_windows // 20, 1)

    for w_idx, window_ts in enumerate(active_5m_ts):
        if w_idx % progress_step == 0:
            pct = (w_idx / total_windows) * 100
            print(f'  Progress: {pct:.0f}%  |  Balance: ${balance:.2f}  |  Trades: {len(trades)}  |  Open: {len(positions)}')

        # For each symbol, compute 5m indicators at this point
        for sym in list(historical.keys()):
            # Get all 5m candles up to and including this window
            all_5m = candles_5m_by_sym.get(sym, [])
            candles_up_to_now = [c for c in all_5m if c['t'] <= window_ts]

            if len(candles_up_to_now) < 35:
                continue

            closes_5m = [c['c'] for c in candles_up_to_now]

            # Calculate indicators on 5m data
            rsi = calc_rsi(closes_5m, window=14)
            macd, macd_sig = calc_macd(closes_5m, fast=12, slow=26, signal=9)

            # Previous 5m candle indicators (for exit crossover detection)
            closes_5m_prev = closes_5m[:-1]
            rsi_prev = calc_rsi(closes_5m_prev, window=14) if len(closes_5m_prev) >= 15 else rsi
            macd_prev, macd_sig_prev = calc_macd(closes_5m_prev) if len(closes_5m_prev) >= 35 else (macd, macd_sig)

            # ── Check exits on 1m candles within this 5m window ──
            if sym in positions:
                pos = positions[sym]
                window_end_ts = window_ts + 5 * 60 * 1000  # next 5m boundary

                # Get 1m candles in this window
                candles_1m = [
                    c for c in candles_1m_by_sym.get(sym, [])
                    if window_ts <= c['t'] < window_end_ts
                ]

                exited = False
                for c1m in candles_1m:
                    current_price = c1m['c']

                    exit_result = _check_exit(
                        entry_price=pos['entry_price'],
                        current_price=current_price,
                        direction=pos['direction'],
                        tier=pos['tier'],
                        rsi_now=rsi,
                        rsi_prev=pos.get('prev_rsi', rsi),
                        macd_now=macd,
                        macd_signal_now=macd_sig,
                        macd_prev=pos.get('prev_macd', macd),
                        macd_signal_prev=pos.get('prev_macd_signal', macd_sig),
                        sl_pct=sl_emergency_pct,
                    )

                    if exit_result['exit']:
                        # Calculate PnL
                        if pos['direction'] == 'LONG':
                            raw_pnl_pct = (current_price - pos['entry_price']) / pos['entry_price']
                        else:
                            raw_pnl_pct = (pos['entry_price'] - current_price) / pos['entry_price']

                        # PnL in USD (leveraged position size)
                        pnl_usd = pos['size_usd'] * raw_pnl_pct

                        # Exit fee
                        exit_fee = pos['size_usd'] * taker_fee
                        total_fee = pos['entry_fee'] + exit_fee
                        net_pnl = pnl_usd - exit_fee

                        balance += net_pnl
                        exit_time = c1m['t'] / 1000.0

                        trades.append({
                            'symbol': sym,
                            'direction': pos['direction'],
                            'tier': pos['tier'],
                            'entry_price': pos['entry_price'],
                            'exit_price': current_price,
                            'entry_time': pos['entry_time'],
                            'exit_time': exit_time,
                            'pnl_usd': net_pnl,
                            'pnl_pct': raw_pnl_pct,
                            'fee_usd': total_fee,
                            'size_usd': pos['size_usd'],
                            'reason': exit_result['reason'],
                        })

                        equity_curve.append({
                            'timestamp': exit_time,
                            'balance': balance,
                        })

                        del positions[sym]
                        exited = True
                        break

                if exited:
                    continue

                # Update stored indicator values for next window
                positions[sym]['prev_rsi'] = rsi
                positions[sym]['prev_macd'] = macd
                positions[sym]['prev_macd_signal'] = macd_sig

            # ── Check entry (only if no position for this symbol) ──
            if sym not in positions and balance > 10:
                direction = _determine_direction_technical(rsi, macd, macd_sig)
                entry_result = _check_entry(rsi, macd, macd_sig, direction)

                if entry_result['enter']:
                    tier = entry_result['tier']
                    sizing = entry_result['sizing_factor']

                    # Position size: balance * position_pct * sizing_factor * leverage
                    size_usd = balance * position_pct * sizing * leverage

                    # Entry fee
                    entry_fee = size_usd * taker_fee

                    # Deduct fee from balance immediately
                    balance -= entry_fee

                    # Entry price = close of current 5m candle
                    entry_price = candles_up_to_now[-1]['c']
                    entry_time = window_ts / 1000.0

                    positions[sym] = {
                        'entry_price': entry_price,
                        'entry_time': entry_time,
                        'direction': entry_result['direction'],
                        'tier': tier,
                        'size_usd': size_usd,
                        'entry_fee': entry_fee,
                        'prev_rsi': rsi,
                        'prev_macd': macd,
                        'prev_macd_signal': macd_sig,
                    }

    # ── Force-close any remaining positions at last price ──
    for sym, pos in list(positions.items()):
        all_1m = candles_1m_by_sym.get(sym, [])
        if all_1m:
            last_price = all_1m[-1]['c']
        else:
            all_5m = candles_5m_by_sym.get(sym, [])
            last_price = all_5m[-1]['c'] if all_5m else pos['entry_price']

        if pos['direction'] == 'LONG':
            raw_pnl_pct = (last_price - pos['entry_price']) / pos['entry_price']
        else:
            raw_pnl_pct = (pos['entry_price'] - last_price) / pos['entry_price']

        pnl_usd = pos['size_usd'] * raw_pnl_pct
        exit_fee = pos['size_usd'] * taker_fee
        total_fee = pos['entry_fee'] + exit_fee
        net_pnl = pnl_usd - exit_fee
        balance += net_pnl

        trades.append({
            'symbol': sym,
            'direction': pos['direction'],
            'tier': pos['tier'],
            'entry_price': pos['entry_price'],
            'exit_price': last_price,
            'entry_time': pos['entry_time'],
            'exit_time': backtest_end_ts / 1000.0,
            'pnl_usd': net_pnl,
            'pnl_pct': raw_pnl_pct,
            'fee_usd': total_fee,
            'size_usd': pos['size_usd'],
            'reason': 'BACKTEST_END',
        })

        equity_curve.append({
            'timestamp': backtest_end_ts / 1000.0,
            'balance': balance,
        })

    positions.clear()

    print(f'  Progress: 100%  |  Balance: ${balance:.2f}  |  Trades: {len(trades)}  |  Open: 0')
    print(f'\n[3/3] Calculating metrics...')

    # ── Phase 4: Metrics ──
    metrics = calculate_metrics(trades, initial_balance, equity_curve)

    # Generate report
    report_text = generate_report(metrics, trades, equity_curve, hours)
    print(report_text)

    # Save results
    output_dir = os.path.join(os.path.dirname(__file__), '..', 'data')
    save_report(metrics, trades, equity_curve, hours, output_dir)

    return {
        'trades': trades,
        'metrics': metrics,
        'equity_curve': equity_curve,
        'report': report_text,
    }


# ── CLI Entry Point ──

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Momentum 2 Backtester')
    parser.add_argument('--hours', type=int, default=72, help='Hours of history (default: 72)')
    parser.add_argument('--balance', type=float, default=1000, help='Initial balance (default: 1000)')
    parser.add_argument('--leverage', type=float, default=3.0, help='Leverage (default: 3.0)')
    parser.add_argument('--position-pct', type=float, default=0.05, help='Position size %% (default: 0.05)')
    parser.add_argument('--sl', type=float, default=0.03, help='Emergency SL %% (default: 0.03)')
    parser.add_argument('--symbols', type=int, default=20, help='Max symbols (default: 20)')
    parser.add_argument('--min-volume', type=float, default=1_000_000, help='Min 24h volume (default: 1000000)')
    args = parser.parse_args()

    result = run_backtest(
        hours=args.hours,
        initial_balance=args.balance,
        leverage=args.leverage,
        position_pct=args.position_pct,
        sl_emergency_pct=args.sl,
        max_symbols=args.symbols,
        min_volume=args.min_volume,
    )
