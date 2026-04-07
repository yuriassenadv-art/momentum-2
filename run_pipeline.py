# momentum-2/run_pipeline.py
"""Momentum 2 — Master Pipeline

Runs every 5 minutes. Simple flow:
  Phase 1: Data (Scanner + News + Social + Polymarket)
  Phase 2: Prediction (Monte Carlo + Gemini Flash)
  Phase 3: Analytical (RSI, MACD, Volume, Funding, S/R)
  Phase 5: Decision (Gemini + Polymarket aligned + technicals confirm)
  Phase 4: Orchestration (FSM: Flat → Active → Flat)
  Phase 6: Execution (market orders)
  Phase 7: Audit (logging)

Guardian (separate process) handles exits every 10 seconds.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import Config
from data.collector import run_scanner
from data.news_collector import run_news_collector
from data.social_sentiment import collect_social_sentiment
from data.briefing_generator import generate_briefing
from prediction.monte_carlo import run_mc_for_asset, get_sizing_factor, validate_sl
from prediction.gemini_analyst import analyze_with_gemini
from prediction.polymarket_client import get_polymarket_signal
from analytical.engine import run_all_analytics
from orchestration.fsm import FSMManager
from decision.engine import should_enter
from execution.executor import execute_entry, get_balance
from audit.logger import generate_audit
from control.commands import check_for_commands, is_paused

CYCLE_INTERVAL = 5 * 60  # 5 minutes


def run_pipeline_once(cfg=None):
    """Execute one full pipeline cycle."""
    if cfg is None:
        cfg = Config()

    print("\n" + "=" * 60)
    print(f"MOMENTUM 2 — PIPELINE CYCLE — {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # ── Phase 0: Commands ──
    cmd = check_for_commands(cfg.base_dir)
    if cmd:
        print(f"[Phase 0] Command: {cmd}")
    if is_paused(cfg.base_dir):
        print("[Phase 0] System PAUSED. Skipping cycle.")
        return

    # ── Phase 1A: Market Data (Hyperliquid OHLCV 1m + 5m) ──
    print("\n[Phase 1A] Scanner — Hyperliquid market data...")
    market_data = run_scanner(cfg)
    if not market_data:
        print("  No pairs found. Pipeline halted.")
        return
    print(f"  {len(market_data)} pairs scanned.")

    # ── Phase 1B: News + Social ──
    print("\n[Phase 1B] News + Social intelligence...")
    try:
        news_path = os.path.join(cfg.base_dir, 'news_data.json')
        news_data = run_news_collector(news_path)
        social_data = collect_social_sentiment()
        briefing_text = generate_briefing(news_data, social_data, cfg.base_dir)
        print(f"  Briefing: {len(briefing_text)} chars")
    except Exception as e:
        print(f"  [ERROR] News/Social: {e}")
        briefing_text = None

    # ── Phase 2A: Monte Carlo (all assets, ~60ms) ──
    print("\n[Phase 2A] Monte Carlo — volatility analysis...")
    mc_results = {}
    for symbol, data in market_data.items():
        ohlcv = data.get('ohlcv_5m', [])
        if len(ohlcv) >= 3:
            mc_results[symbol] = run_mc_for_asset(ohlcv)
    print(f"  MC completed for {len(mc_results)} assets.")

    # ── Phase 2B: Gemini Flash (all assets, ~2s) ──
    gemini_signals = {}
    if briefing_text:
        print("\n[Phase 2B] Gemini Flash — sentiment analysis...")
        all_symbols = sorted(market_data.keys(),
                             key=lambda s: market_data[s].get('volume_24h', 0),
                             reverse=True)
        gemini_signals = analyze_with_gemini(briefing_text, all_symbols) or {}
        print(f"  Gemini: signals for {len(gemini_signals)} assets.")

    # ── Phase 2C: Polymarket (independent signal) ──
    print("\n[Phase 2C] Polymarket — independent signal...")
    # Determine overall Gemini direction for alignment check
    bullish_count = sum(1 for s in gemini_signals.values()
                        if s.get('sentiment_score', 0) > 0.3)
    bearish_count = sum(1 for s in gemini_signals.values()
                        if s.get('sentiment_score', 0) < -0.3)
    gemini_direction = 'bullish' if bullish_count > bearish_count else (
        'bearish' if bearish_count > bullish_count else 'neutral')

    try:
        poly_signal = get_polymarket_signal(gemini_direction)
        print(f"  Polymarket: {poly_signal.get('direction', 'N/A')}, "
              f"aligned={poly_signal.get('aligned_with_gemini', False)}")
    except Exception as e:
        print(f"  [WARN] Polymarket: {e}")
        poly_signal = {'direction': 'neutral', 'confidence': 0,
                       'aligned_with_gemini': False, 'key_markets': []}

    # Save predictions
    predictions = {**mc_results}
    for sym in gemini_signals:
        if sym not in predictions:
            predictions[sym] = {}
        predictions[sym]['gemini'] = gemini_signals[sym]
    predictions['_polymarket'] = poly_signal
    with open(cfg.predictions_path, 'w') as f:
        json.dump(predictions, f, indent=2)

    # ── Phase 3: Analytical Engine ──
    print("\n[Phase 3] Analytical engine — RSI, MACD, Volume, Funding...")
    analytics = run_all_analytics(market_data, cfg.analytics_path)
    print(f"  Analytics for {len(analytics)} assets.")

    # ── Phase 5: Decision Engine ──
    print("\n[Phase 5] Decision engine — evaluating entries...")
    mgr = FSMManager(cfg.state_path)
    mgr.load()

    entries = []
    vetoed = 0

    for symbol in market_data:
        fsm = mgr.get_or_create(symbol)
        if fsm.state != 'Flat':
            continue  # Already in a position

        gem_sig = gemini_signals.get(symbol, {})
        anal = analytics.get(symbol, {})
        mc = mc_results.get(symbol, {})

        result = should_enter(symbol, gem_sig, poly_signal, anal, mc, cfg)

        if result['enter']:
            entries.append({
                'symbol': symbol,
                'direction': result['direction'],
                'sizing_factor': result['sizing_factor'],
                'reason': result['reason'],
            })
            print(f"  -> {symbol} {result['direction']}: {result['reason']}")
        elif result.get('reason') and 'sentiment' in result.get('reason', '').lower():
            vetoed += 1

    print(f"  {len(entries)} entries, {vetoed} vetoed by sentiment.")

    # ── Phase 6: Execute entries ──
    if entries:
        print(f"\n[Phase 6] Executing {len(entries)} entries...")
        for entry in entries:
            result = execute_entry(
                entry['symbol'], entry['direction'],
                entry['sizing_factor'], cfg, cfg.dry_run
            )
            if result['status'] == 'success':
                fsm = mgr.get_or_create(entry['symbol'])
                fsm.enter(result['entry_price'], result['size'], entry['direction'])
                print(f"  {entry['symbol']} {entry['direction']} @ "
                      f"${result['entry_price']:,.2f} | size={result['size']:.6f}")
        mgr.save()
    else:
        print("\n[Phase 6] No entries this cycle.")

    # ── Phase 7: Audit ──
    print("\n[Phase 7] Audit...")
    trades = []
    if os.path.exists(cfg.trade_history_path):
        with open(cfg.trade_history_path) as f:
            trades = json.load(f)
    state = {s: f.to_dict() for s, f in mgr.fsms.items()}
    generate_audit(state, trades, cfg.audit_log_path)

    active = mgr.active_positions()
    print(f"\n{'='*60}")
    print(f"CYCLE COMPLETE | {len(active)} active | next in {CYCLE_INTERVAL//60}min")
    print(f"Guardian handles exits every 10s (separate process)")
    print(f"{'='*60}")


def run_daemon():
    """Run pipeline in continuous loop."""
    cfg = Config()
    print(f"Momentum 2 Pipeline starting. Cycle: {CYCLE_INTERVAL//60}min")
    print(f"DRY_RUN: {cfg.dry_run} | Balance: ${cfg.sim_balance}")

    while True:
        try:
            run_pipeline_once(cfg)
        except KeyboardInterrupt:
            print("\nPipeline stopped.")
            break
        except Exception as e:
            print(f"\n[ERROR] Cycle failed: {e}")
            import traceback
            traceback.print_exc()

        try:
            time.sleep(CYCLE_INTERVAL)
        except KeyboardInterrupt:
            print("\nPipeline stopped.")
            break


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Momentum 2 Pipeline')
    parser.add_argument('--daemon', action='store_true', help='Run continuous loop')
    parser.add_argument('--once', action='store_true', help='Run one cycle')
    args = parser.parse_args()

    if args.daemon:
        run_daemon()
    else:
        run_pipeline_once()
