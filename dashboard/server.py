# momentum-2/dashboard/server.py
"""Momentum 2 Dashboard — Backend

All metrics calculated server-side. Frontend only renders.
Background thread fetches Hyperliquid prices every 10s.
Dashboard refreshes every 10 seconds.
"""
import json
import os
import http.server
import socketserver
import signal
import sys
import threading
import urllib.request
import time as _time
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs

PORT = 3010
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.join(BASE_DIR, '..')

SIM_BALANCE = 1000
LEVERAGE = 3.0
TAKER_FEE = 0.00045   # 0.045% — all market orders

_live_prices = {}
_live_prices_ts = 0
_file_cache = {}
_file_cache_ttl = 5


def read_json(filename):
    now = _time.time()
    cached = _file_cache.get(filename)
    if cached and now - cached['ts'] < _file_cache_ttl:
        return cached['data']
    path = os.path.join(PROJECT_DIR, filename)
    try:
        with open(path, 'r') as f:
            data = json.load(f)
        _file_cache[filename] = {'data': data, 'ts': now}
        return data
    except Exception:
        return {}


def read_json_list(filename):
    data = read_json(filename)
    return data if isinstance(data, list) else []


def _background_price_fetcher():
    global _live_prices, _live_prices_ts
    while True:
        try:
            req = urllib.request.Request(
                'https://api.hyperliquid.xyz/info',
                data=json.dumps({'type': 'allMids'}).encode(),
                headers={'Content-Type': 'application/json'},
            )
            resp = urllib.request.urlopen(req, timeout=10)
            _live_prices = json.loads(resp.read().decode())
            _live_prices_ts = _time.time()
        except Exception:
            pass
        _time.sleep(10)


def send_json_response(handler, data):
    handler.send_response(200)
    handler.send_header('Content-type', 'application/json')
    handler.send_header('Access-Control-Allow-Origin', '*')
    handler.end_headers()
    handler.wfile.write(json.dumps(data, default=str).encode())


def build_dashboard_data():
    now = _time.time()
    trades = read_json_list('trade_history.json')
    exits = [t for t in trades if t.get('operation') == 'EXIT' or t.get('type') == 'EXIT']
    state_raw = read_json('state.json')
    state = state_raw.get('fsms', state_raw) if isinstance(state_raw, dict) else {}
    market = read_json('market_data.json')
    live = _live_prices

    # Metrics
    total_pnl_usd = sum(t.get('pnl_usd', 0) for t in exits)
    total_fees = sum(t.get('fee_total', 0) for t in exits)
    wins = [t for t in exits if t.get('pnl_usd', 0) > 0]
    losses = [t for t in exits if t.get('pnl_usd', 0) <= 0]
    win_rate = len(wins) / len(exits) if exits else 0
    current_balance = round(SIM_BALANCE + total_pnl_usd, 2)
    capital_pct = round((total_pnl_usd / SIM_BALANCE) * 100, 4) if SIM_BALANCE > 0 else 0

    # Max drawdown
    running = SIM_BALANCE
    peak = SIM_BALANCE
    max_dd = 0
    for t in exits:
        running += t.get('pnl_usd', 0)
        if running > peak:
            peak = running
        dd = ((peak - running) / peak) * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    # Open positions
    positions = []
    pos_total_pnl = 0
    pos_total_fees = 0

    for symbol, fsm in state.items():
        if not isinstance(fsm, dict) or fsm.get('state') != 'Active':
            continue
        entry = fsm.get('entry_price', 0)
        if not entry:
            continue
        direction = fsm.get('direction', 'LONG')
        size = fsm.get('size', 0)

        current = float(live.get(symbol, 0))
        if not current and symbol in market:
            ohlcv = market[symbol].get('ohlcv_1m', [])
            current = ohlcv[-1].get('close', 0) if ohlcv else 0

        if direction == 'SHORT':
            pnl_pct = ((entry - current) / entry * 100) if entry else 0
            pnl_usd = (entry - current) * size
        else:
            pnl_pct = ((current - entry) / entry * 100) if entry else 0
            pnl_usd = (current - entry) * size

        fee_entry = entry * size * TAKER_FEE
        fee_exit = current * size * TAKER_FEE
        fees = fee_entry + fee_exit
        pnl_net = pnl_usd - fees

        pos_total_pnl += pnl_net
        pos_total_fees += fees

        positions.append({
            'symbol': symbol,
            'side': direction,
            'entry_price': round(entry, 6),
            'current_price': round(current, 6),
            'pnl_pct': round(pnl_pct, 4),
            'pnl_usd': round(pnl_net, 4),
            'fees_if_exit': round(fees, 4),
            'state': fsm.get('state'),
            'entered_at': fsm.get('entered_at'),
        })

    # Chart: daily capital curve
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    chart_labels = ['00:00']
    chart_data = [0.0]

    pnl_before_today = sum(
        t.get('pnl_usd', 0) for t in exits
        if t.get('timestamp', '')[:10] < today
    )
    balance_sod = SIM_BALANCE + pnl_before_today

    day_cum_pnl = 0
    for t in exits:
        ts = t.get('timestamp', '')
        if not ts or ts[:10] != today:
            continue
        day_cum_pnl += t.get('pnl_usd', 0)
        cap_pct = round((day_cum_pnl / balance_sod) * 100, 4) if balance_sod > 0 else 0
        time_label = ts[11:16] if len(ts) >= 16 else ts
        chart_labels.append(time_label)
        chart_data.append(cap_pct)

    now_label = datetime.now(timezone.utc).strftime('%H:%M')
    if chart_labels[-1] != now_label:
        chart_labels.append(now_label)
        chart_data.append(chart_data[-1] if chart_data else 0.0)

    # Scanner
    analytics = read_json('analytics.json')
    predictions = read_json('predictions.json')
    scanner = []
    for symbol in set(list(state.keys()) + list(analytics.keys())):
        if symbol.startswith('_'):
            continue
        fsm = state.get(symbol, {})
        anal = analytics.get(symbol, {})
        pred = predictions.get(symbol, {})
        gemini = pred.get('gemini', {})
        scanner.append({
            'symbol': symbol,
            'state': fsm.get('state', 'Flat') if isinstance(fsm, dict) else 'Flat',
            'rsi': anal.get('rsi', 0),
            'volume_ratio': anal.get('volume_ratio', 0),
            'funding_rate': anal.get('funding_rate', 0),
            'sentiment': gemini.get('sentiment_score', 0),
        })
    scanner.sort(key=lambda x: abs(x.get('sentiment', 0)), reverse=True)

    # Market signal (replaced Polymarket)
    market_sig = predictions.get('_market', {})

    # Daily PnL
    exits_today = [t for t in exits if t.get('timestamp', '').startswith(today)]
    daily_pnl_usd = sum(t.get('pnl_usd', 0) for t in exits_today)
    daily_pnl_pct = round((daily_pnl_usd / balance_sod * 100), 4) if balance_sod > 0 else 0

    return {
        'metrics': {
            'win_rate': round(win_rate, 4),
            'wins': len(wins),
            'losses': len(losses),
            'total_trades': len(exits),
            'capital_pct': capital_pct,
            'capital_usd': round(total_pnl_usd, 2),
            'current_balance': current_balance,
            'sim_balance': SIM_BALANCE,
            'max_drawdown_pct': round(max_dd, 4),
            'total_fees_usd': round(total_fees, 4),
            'open_positions': len(positions),
            'daily_pnl_pct': daily_pnl_pct,
            'daily_pnl_usd': round(daily_pnl_usd, 2),
            'trades_today': len(exits_today),
        },
        'positions': {
            'items': positions,
            'total_pnl_usd': round(pos_total_pnl, 2),
            'total_fees_if_exit': round(pos_total_fees, 4),
        },
        'chart': {'labels': chart_labels, 'data': chart_data},
        'scanner': scanner[:20],
        'market_signal': market_sig,
        'analyst': _get_latest_analyst_report(),
        '_meta': {
            'prices_age_sec': round(now - _live_prices_ts, 0) if _live_prices_ts else -1,
        },
    }


def _get_latest_analyst_report():
    """Get the latest performance analyst report."""
    reports = read_json_list('performance_reports.json')
    if not reports:
        return None
    latest = reports[-1]
    return {
        'narrative': latest.get('narrative', ''),
        'summary': latest.get('summary', {}),
        'by_tier': latest.get('by_tier', {}),
        'by_direction': latest.get('by_direction', {}),
        'exit_reasons': latest.get('exit_reasons', {}),
        'streaks': latest.get('streaks', {}),
        'top_winners': latest.get('by_asset', {}).get('top_winners', []),
        'top_losers': latest.get('by_asset', {}).get('top_losers', []),
        'timestamp': latest.get('timestamp', ''),
    }


def filter_history(trades, params):
    exits = [t for t in trades if t.get('operation') == 'EXIT']
    date_from = params.get('from', [None])[0]
    date_to = params.get('to', [None])[0]
    result = params.get('result', [None])[0]
    asset = params.get('asset', [None])[0]

    if date_from:
        exits = [t for t in exits if t.get('timestamp', '') >= date_from]
    if date_to:
        exits = [t for t in exits if t.get('timestamp', '') <= date_to + ' 23:59:59']
    if result == 'win':
        exits = [t for t in exits if t.get('pnl_usd', 0) > 0]
    elif result == 'loss':
        exits = [t for t in exits if t.get('pnl_usd', 0) <= 0]
    if asset:
        exits = [t for t in exits if t.get('asset', '') == asset]

    total_pnl = sum(t.get('pnl_usd', 0) for t in exits)
    total_fees = sum(t.get('fee_total', 0) for t in exits)
    wins = len([t for t in exits if t.get('pnl_usd', 0) > 0])

    return {
        'trades': exits,
        'totals': {
            'count': len(exits), 'wins': wins,
            'losses': len(exits) - wins,
            'pnl_usd': round(total_pnl, 2),
            'fees_usd': round(total_fees, 4),
            'win_rate': round(wins / len(exits), 4) if exits else 0,
        }
    }


class MyHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == '/' or path == '/momentum':
            self.path = 'dashboard.html'
            return super().do_GET()
        elif path == '/api/dashboard':
            send_json_response(self, build_dashboard_data())
        elif path == '/api/history':
            trades = read_json_list('trade_history.json')
            send_json_response(self, filter_history(trades, params))
        elif path == '/api/audit':
            send_json_response(self, read_json('audit_log.json'))
        else:
            return super().do_GET()

    def do_POST(self):
        if self.path == '/api/control':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            try:
                cmd = json.loads(body)
                cmd_file = os.path.join(PROJECT_DIR, 'cmd.json')
                with open(cmd_file, 'w') as f:
                    json.dump(cmd, f)
                send_json_response(self, {"status": "ok", "action": cmd.get("action")})
            except Exception as e:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())
        else:
            self.send_response(404)
            self.end_headers()


class ThreadingServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    price_thread = threading.Thread(target=_background_price_fetcher, daemon=True)
    price_thread.start()
    print(f"Background price fetcher started (every 10s)")

    httpd = ThreadingServer(("", PORT), MyHandler)

    def shutdown(sig, frame):
        httpd.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    print(f"Momentum 2 Dashboard at http://localhost:{PORT}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
