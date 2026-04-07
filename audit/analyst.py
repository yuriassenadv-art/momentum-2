# momentum-2/audit/analyst.py
"""Performance Analyst — Pattern Detection in Trade History.

OBSERVATION ONLY. Never auto-adjusts config or parameters.
Detects patterns, generates reports, surfaces insights for human review.

Runs after each pipeline cycle. Uses Gemini Flash for narrative analysis.
Saves reports to performance_reports.json for dashboard consumption.

Patterns tracked:
  - Win rate by tier (FULL/STANDARD/MOMENTUM/SCOUT/MICRO)
  - Win rate by direction (LONG vs SHORT)
  - Win rate by asset (which assets consistently win/lose)
  - Win rate by hour (time-of-day patterns)
  - Average hold time by tier
  - Average PnL by tier
  - Fee impact (fees as % of gross PnL)
  - Streak detection (consecutive wins/losses)
  - Best/worst performers
  - SL hit rate (how often emergency SL triggers)
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from config import Config


def _load_trades(cfg):
    """Load all completed trades (exits only)."""
    if not os.path.exists(cfg.trade_history_path):
        return []
    with open(cfg.trade_history_path) as f:
        trades = json.load(f)
    return [t for t in trades if t.get('type') == 'EXIT' or t.get('operation') == 'EXIT']


def _load_entries(cfg):
    """Load all entry trades."""
    if not os.path.exists(cfg.trade_history_path):
        return []
    with open(cfg.trade_history_path) as f:
        trades = json.load(f)
    return [t for t in trades if t.get('type') == 'ENTRY' or t.get('operation') == 'ENTRY']


def analyze_by_tier(exits):
    """Win rate, avg PnL, and count per tier."""
    tiers = defaultdict(lambda: {'wins': 0, 'losses': 0, 'pnl_sum': 0, 'fees_sum': 0, 'count': 0})

    for t in exits:
        tier = t.get('tier', 'UNKNOWN')
        pnl = t.get('pnl_usd', 0)
        fee = t.get('fee_total', t.get('fee', 0))

        tiers[tier]['count'] += 1
        tiers[tier]['pnl_sum'] += pnl
        tiers[tier]['fees_sum'] += fee
        if pnl > 0:
            tiers[tier]['wins'] += 1
        else:
            tiers[tier]['losses'] += 1

    result = {}
    for tier, data in tiers.items():
        total = data['count']
        result[tier] = {
            'count': total,
            'wins': data['wins'],
            'losses': data['losses'],
            'win_rate': round(data['wins'] / total, 4) if total > 0 else 0,
            'avg_pnl': round(data['pnl_sum'] / total, 4) if total > 0 else 0,
            'total_pnl': round(data['pnl_sum'], 4),
            'total_fees': round(data['fees_sum'], 4),
            'fee_impact_pct': round(data['fees_sum'] / abs(data['pnl_sum']) * 100, 2)
                if abs(data['pnl_sum']) > 0 else 0,
        }
    return result


def analyze_by_direction(exits):
    """Win rate per direction (LONG vs SHORT)."""
    dirs = defaultdict(lambda: {'wins': 0, 'losses': 0, 'pnl_sum': 0, 'count': 0})

    for t in exits:
        d = t.get('direction', 'UNKNOWN')
        pnl = t.get('pnl_usd', 0)
        dirs[d]['count'] += 1
        dirs[d]['pnl_sum'] += pnl
        if pnl > 0:
            dirs[d]['wins'] += 1
        else:
            dirs[d]['losses'] += 1

    result = {}
    for direction, data in dirs.items():
        total = data['count']
        result[direction] = {
            'count': total,
            'wins': data['wins'],
            'losses': data['losses'],
            'win_rate': round(data['wins'] / total, 4) if total > 0 else 0,
            'total_pnl': round(data['pnl_sum'], 4),
        }
    return result


def analyze_by_asset(exits):
    """Win rate and PnL per asset. Returns top winners and losers."""
    assets = defaultdict(lambda: {'wins': 0, 'losses': 0, 'pnl_sum': 0, 'count': 0})

    for t in exits:
        sym = t.get('symbol', t.get('asset', 'UNKNOWN'))
        pnl = t.get('pnl_usd', 0)
        assets[sym]['count'] += 1
        assets[sym]['pnl_sum'] += pnl
        if pnl > 0:
            assets[sym]['wins'] += 1
        else:
            assets[sym]['losses'] += 1

    result = {}
    for sym, data in assets.items():
        total = data['count']
        result[sym] = {
            'count': total,
            'win_rate': round(data['wins'] / total, 4) if total > 0 else 0,
            'total_pnl': round(data['pnl_sum'], 4),
        }

    # Sort by PnL
    sorted_assets = sorted(result.items(), key=lambda x: x[1]['total_pnl'], reverse=True)
    top_winners = sorted_assets[:5]
    top_losers = sorted_assets[-5:]

    return {
        'all': result,
        'top_winners': [{'symbol': s, **d} for s, d in top_winners],
        'top_losers': [{'symbol': s, **d} for s, d in top_losers],
    }


def analyze_by_hour(exits):
    """Win rate by hour of day (UTC)."""
    hours = defaultdict(lambda: {'wins': 0, 'losses': 0, 'count': 0})

    for t in exits:
        ts = t.get('timestamp', 0)
        if isinstance(ts, (int, float)) and ts > 0:
            hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        elif isinstance(ts, str):
            try:
                hour = int(ts[11:13])
            except (ValueError, IndexError):
                continue
        else:
            continue

        hours[hour]['count'] += 1
        if t.get('pnl_usd', 0) > 0:
            hours[hour]['wins'] += 1
        else:
            hours[hour]['losses'] += 1

    result = {}
    for hour in sorted(hours.keys()):
        data = hours[hour]
        total = data['count']
        result[str(hour)] = {
            'count': total,
            'win_rate': round(data['wins'] / total, 4) if total > 0 else 0,
        }
    return result


def analyze_exit_reasons(exits):
    """Count exit reasons to understand what triggers exits."""
    reasons = defaultdict(int)
    for t in exits:
        reason = t.get('reason', 'unknown')
        # Normalize: take first word/tag
        tag = reason.split(':')[0].strip() if ':' in reason else reason.split(' ')[0].strip()
        reasons[tag] += 1

    return dict(sorted(reasons.items(), key=lambda x: x[1], reverse=True))


def detect_streaks(exits):
    """Find longest win/loss streaks."""
    if not exits:
        return {'longest_win': 0, 'longest_loss': 0, 'current': 0, 'current_type': 'none'}

    current_streak = 0
    current_type = 'none'
    max_win = 0
    max_loss = 0

    for t in exits:
        if t.get('pnl_usd', 0) > 0:
            if current_type == 'win':
                current_streak += 1
            else:
                current_streak = 1
                current_type = 'win'
            max_win = max(max_win, current_streak)
        else:
            if current_type == 'loss':
                current_streak += 1
            else:
                current_streak = 1
                current_type = 'loss'
            max_loss = max(max_loss, current_streak)

    return {
        'longest_win': max_win,
        'longest_loss': max_loss,
        'current': current_streak,
        'current_type': current_type,
    }


def analyze_hold_times(exits):
    """Average hold time per tier."""
    tiers = defaultdict(list)

    for t in exits:
        entered = t.get('entered_at', 0)
        exited = t.get('timestamp', 0)
        tier = t.get('tier', 'UNKNOWN')

        if isinstance(entered, (int, float)) and isinstance(exited, (int, float)):
            if entered > 0 and exited > 0:
                hold_sec = exited - entered
                if 0 < hold_sec < 86400:  # Sanity: less than 24h
                    tiers[tier].append(hold_sec)

    result = {}
    for tier, times in tiers.items():
        if times:
            avg = sum(times) / len(times)
            result[tier] = {
                'avg_seconds': round(avg, 0),
                'avg_minutes': round(avg / 60, 1),
                'min_seconds': round(min(times), 0),
                'max_seconds': round(max(times), 0),
                'count': len(times),
            }
    return result


def generate_gemini_narrative(report, cfg):
    """Use Gemini to generate a human-readable narrative from the report.

    Returns a short narrative string or None if Gemini unavailable.
    """
    if not cfg.gemini_api_key:
        return None

    try:
        import google.generativeai as genai
        genai.configure(api_key=cfg.gemini_api_key)
        model = genai.GenerativeModel(cfg.gemini_model)

        # Compact summary for Gemini
        summary = json.dumps({
            'total_exits': report['summary']['total_exits'],
            'win_rate': report['summary']['win_rate'],
            'total_pnl': report['summary']['total_pnl_usd'],
            'by_tier': report['by_tier'],
            'by_direction': report['by_direction'],
            'exit_reasons': report['exit_reasons'],
            'streaks': report['streaks'],
            'top_winners': report['by_asset']['top_winners'][:3],
            'top_losers': report['by_asset']['top_losers'][:3],
        }, indent=2)

        prompt = f"""You are a quant trading analyst. Analyze this trading performance report and provide:
1. A 2-3 sentence summary of overall performance
2. The most important pattern you see (positive or negative)
3. One specific actionable suggestion

Keep it under 150 words. Be direct, no fluff.

REPORT:
{summary}"""

        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        return f"Gemini unavailable: {e}"


def generate_performance_report(cfg=None):
    """Generate full performance report. Saves to performance_reports.json.

    OBSERVATION ONLY — never modifies config or parameters.
    """
    if cfg is None:
        cfg = Config()

    exits = _load_trades(cfg)
    entries = _load_entries(cfg)

    if not exits:
        return None

    # Core metrics
    total_pnl = sum(t.get('pnl_usd', 0) for t in exits)
    total_fees = sum(t.get('fee_total', t.get('fee', 0)) for t in exits)
    wins = [t for t in exits if t.get('pnl_usd', 0) > 0]
    losses = [t for t in exits if t.get('pnl_usd', 0) <= 0]

    report = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'summary': {
            'total_exits': len(exits),
            'total_entries': len(entries),
            'wins': len(wins),
            'losses': len(losses),
            'win_rate': round(len(wins) / len(exits), 4) if exits else 0,
            'total_pnl_usd': round(total_pnl, 2),
            'total_fees_usd': round(total_fees, 4),
            'fee_impact_pct': round(total_fees / abs(total_pnl) * 100, 2)
                if abs(total_pnl) > 0 else 0,
            'avg_pnl_per_trade': round(total_pnl / len(exits), 4) if exits else 0,
        },
        'by_tier': analyze_by_tier(exits),
        'by_direction': analyze_by_direction(exits),
        'by_asset': analyze_by_asset(exits),
        'by_hour': analyze_by_hour(exits),
        'exit_reasons': analyze_exit_reasons(exits),
        'streaks': detect_streaks(exits),
        'hold_times': analyze_hold_times(exits),
    }

    # Gemini narrative (optional)
    narrative = generate_gemini_narrative(report, cfg)
    if narrative:
        report['narrative'] = narrative

    # Save report
    reports_path = os.path.join(cfg.base_dir, 'performance_reports.json')
    reports = []
    if os.path.exists(reports_path):
        try:
            with open(reports_path) as f:
                reports = json.load(f)
        except (json.JSONDecodeError, IOError):
            reports = []

    # Keep last 100 reports
    reports.append(report)
    reports = reports[-100:]

    with open(reports_path, 'w') as f:
        json.dump(reports, f, indent=2, default=str)

    return report


def print_report(report):
    """Print a human-readable summary to console."""
    if not report:
        print("  No trades to analyze.")
        return

    s = report['summary']
    print(f"  {s['total_exits']} exits | {s['wins']}W/{s['losses']}L | "
          f"WR: {s['win_rate']*100:.0f}% | PnL: ${s['total_pnl_usd']:+.2f} | "
          f"Fees: ${s['total_fees_usd']:.2f} ({s['fee_impact_pct']:.1f}% of PnL)")

    # By tier
    for tier, data in report['by_tier'].items():
        print(f"    [{tier:8s}] {data['count']} trades | "
              f"WR: {data['win_rate']*100:.0f}% | "
              f"PnL: ${data['total_pnl']:+.2f} | "
              f"Avg: ${data['avg_pnl']:+.4f}")

    # Streaks
    streaks = report['streaks']
    print(f"  Streaks: best_win={streaks['longest_win']} | "
          f"worst_loss={streaks['longest_loss']} | "
          f"current={streaks['current']} ({streaks['current_type']})")

    # Exit reasons
    reasons = report['exit_reasons']
    if reasons:
        top_reasons = list(reasons.items())[:5]
        reason_str = ', '.join(f"{r}={c}" for r, c in top_reasons)
        print(f"  Exit reasons: {reason_str}")

    # Narrative
    if report.get('narrative'):
        print(f"  Analyst: {report['narrative'][:200]}")


if __name__ == '__main__':
    cfg = Config()
    report = generate_performance_report(cfg)
    print("\n=== PERFORMANCE ANALYST ===")
    print_report(report)
