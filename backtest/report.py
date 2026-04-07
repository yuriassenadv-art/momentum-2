# momentum-2/backtest/report.py
"""Report Generator — Human-readable console report and JSON export.

Formats backtest results into a clean ASCII table and saves raw data
for further analysis.
"""
import sys
import os
import json
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def generate_report(metrics: dict, trades: list, equity_curve: list, hours: int) -> str:
    """Generate a human-readable console report.

    Args:
        metrics: Dict from calculate_metrics().
        trades: List of trade dicts.
        equity_curve: List of {timestamp, balance} dicts.
        hours: Backtest duration in hours.

    Returns:
        Formatted string report.
    """
    sep = '=' * 50
    lines = []
    lines.append('')
    lines.append(sep)
    lines.append(f'  MOMENTUM 2 — BACKTEST REPORT ({hours}h)')
    lines.append(sep)

    # Performance
    lines.append('')
    lines.append('  PERFORMANCE')
    lines.append(f'    Total Trades:    {metrics["total_trades"]}')
    lines.append(f'    Win Rate:        {metrics["win_rate"]*100:.1f}%')
    pnl_sign = '+' if metrics['total_pnl_usd'] >= 0 else ''
    lines.append(f'    PnL:             {pnl_sign}${metrics["total_pnl_usd"]:.2f} ({pnl_sign}{metrics["total_pnl_pct"]:.2f}%)')
    lines.append(f'    Sharpe Ratio:    {metrics["sharpe_ratio"]:.2f}')
    lines.append(f'    Sortino Ratio:   {metrics["sortino_ratio"]:.2f}')
    lines.append(f'    Max Drawdown:    -{metrics["max_drawdown_pct"]:.1f}%')
    lines.append(f'    Profit Factor:   {metrics["profit_factor"]:.2f}')
    lines.append(f'    Expectancy:      ${metrics["expectancy"]:.2f}/trade')
    lines.append(f'    Kelly Sizing:    {metrics["kelly_criterion"]*100:.1f}%')
    lines.append(f'    Calmar Ratio:    {metrics["calmar_ratio"]:.2f}')
    lines.append(f'    Trades/Day:      {metrics["trades_per_day"]:.1f}')
    lines.append(f'    Avg Hold Time:   {metrics["avg_hold_time_minutes"]:.1f} min')
    lines.append(f'    Best Trade:      ${metrics["best_trade"]:.2f}')
    lines.append(f'    Worst Trade:     ${metrics["worst_trade"]:.2f}')
    lines.append(f'    Total Fees:      ${metrics.get("total_fees_usd", 0):.2f}')
    lines.append(f'    Fee Impact:      {metrics["fee_impact_pct"]:.1f}%')

    # By Tier
    by_tier = metrics.get('by_tier', {})
    if by_tier:
        lines.append('')
        lines.append('  BY TIER')
        tier_sizing = {'MOMENTUM': '40%', 'SCOUT': '30%', 'MICRO': '20%',
                       'FULL': '100%', 'STANDARD': '60%'}
        for tier in ('FULL', 'STANDARD', 'MOMENTUM', 'SCOUT', 'MICRO'):
            if tier not in by_tier:
                continue
            t = by_tier[tier]
            sz = tier_sizing.get(tier, '?')
            wr = t['win_rate'] * 100
            pnl_s = '+' if t['pnl_usd'] >= 0 else ''
            lines.append(f'    {tier:<12} ({sz:>4})  {t["total"]:>4} trades  WR: {wr:.0f}%  PnL: {pnl_s}${t["pnl_usd"]:.2f}')

    # By Direction
    by_dir = metrics.get('by_direction', {})
    if by_dir:
        lines.append('')
        lines.append('  BY DIRECTION')
        for d in ('LONG', 'SHORT'):
            if d not in by_dir:
                continue
            t = by_dir[d]
            wr = t['win_rate'] * 100
            pnl_s = '+' if t['pnl_usd'] >= 0 else ''
            lines.append(f'    {d:<12}        {t["total"]:>4} trades  WR: {wr:.0f}%  PnL: {pnl_s}${t["pnl_usd"]:.2f}')

    # Top/Worst performers by symbol
    by_sym = metrics.get('by_symbol', {})
    if by_sym:
        sorted_syms = sorted(by_sym.items(), key=lambda x: x[1]['pnl_usd'], reverse=True)

        top = sorted_syms[:5]
        if top and top[0][1]['pnl_usd'] > 0:
            lines.append('')
            lines.append('  TOP PERFORMERS')
            for sym, s in top:
                if s['pnl_usd'] <= 0:
                    break
                wr = s['win_rate'] * 100
                lines.append(f'    {sym:<8} {s["total"]:>3} trades  WR: {wr:.0f}%  PnL: +${s["pnl_usd"]:.2f}')

        worst = sorted_syms[-3:]
        worst_neg = [(s, d) for s, d in worst if d['pnl_usd'] < 0]
        if worst_neg:
            lines.append('')
            lines.append('  WORST PERFORMERS')
            for sym, s in reversed(worst_neg):
                wr = s['win_rate'] * 100
                lines.append(f'    {sym:<8} {s["total"]:>3} trades  WR: {wr:.0f}%  PnL: ${s["pnl_usd"]:.2f}')

    # Best hours
    by_hour = metrics.get('by_hour', {})
    if by_hour:
        # Find best 3-hour window
        hours_sorted = sorted(by_hour.items(), key=lambda x: x[1]['win_rate'], reverse=True)
        best_hours = [(h, d) for h, d in hours_sorted if d['total'] >= 3][:3]
        if best_hours:
            lines.append('')
            lines.append('  BEST HOURS (UTC)')
            for h, d in best_hours:
                lines.append(f'    {h:02d}:00-{h:02d}:59  {d["total"]:>3} trades  WR: {d["win_rate"]*100:.0f}%')

    # Recommendations
    lines.append('')
    lines.append('  RECOMMENDATIONS')

    kelly_pct = metrics['kelly_criterion'] * 100
    lines.append(f'    - Kelly suggests {kelly_pct:.1f}% sizing (current: 5%)')

    long_pnl = by_dir.get('LONG', {}).get('pnl_usd', 0)
    short_pnl = by_dir.get('SHORT', {}).get('pnl_usd', 0)
    if short_pnl > long_pnl and short_pnl > 0:
        lines.append(f'    - SHORT outperforms LONG in this period')
    elif long_pnl > short_pnl and long_pnl > 0:
        lines.append(f'    - LONG outperforms SHORT in this period')

    if best_hours:
        h, d = best_hours[0]
        lines.append(f'    - Best hour: {h:02d} UTC (WR: {d["win_rate"]*100:.0f}%)')

    if metrics['max_drawdown_pct'] > 5.0:
        lines.append(f'    - WARNING: Max drawdown {metrics["max_drawdown_pct"]:.1f}% exceeds 5% threshold')

    if metrics['profit_factor'] < 1.0 and metrics['total_trades'] > 10:
        lines.append(f'    - WARNING: Profit factor < 1.0 — system is net negative')

    lines.append('')
    lines.append(sep)

    return '\n'.join(lines)


def save_report(metrics: dict, trades: list, equity_curve: list, hours: int, output_dir: str):
    """Save backtest results to JSON file.

    Args:
        metrics: Dict from calculate_metrics().
        trades: List of trade dicts.
        equity_curve: List of {timestamp, balance} dicts.
        hours: Backtest duration in hours.
        output_dir: Directory to write results.
    """
    os.makedirs(output_dir, exist_ok=True)

    timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    filename = f'backtest_{hours}h_{timestamp}.json'
    filepath = os.path.join(output_dir, filename)

    result = {
        'meta': {
            'hours': hours,
            'timestamp': datetime.utcnow().isoformat(),
            'total_trades': metrics['total_trades'],
        },
        'metrics': metrics,
        'trades': trades,
        'equity_curve': equity_curve,
    }

    # Convert any numpy/float types to native Python for JSON serialization
    def _convert(obj):
        if hasattr(obj, 'item'):
            return obj.item()
        if isinstance(obj, float) and (obj != obj):  # NaN check
            return 0.0
        raise TypeError(f'Not serializable: {type(obj)}')

    with open(filepath, 'w') as f:
        json.dump(result, f, indent=2, default=_convert)

    print(f'  Results saved to {filepath}')
    return filepath
