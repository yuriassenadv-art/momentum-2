# momentum-2/config.py
"""Momentum 2 — Configuration

Simple config. No adaptive coefficients. No dynamic risk adjustments.
The market decides entry/exit via technicals. Config just sets the constants.
"""
import os

try:
    from dotenv import load_dotenv
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    if os.path.exists(env_path):
        load_dotenv(dotenv_path=env_path)
except ImportError:
    pass


class Config:
    def __init__(self):
        # Timeframes
        self.entry_timeframe = '1m'      # Execution: 1-minute candles
        self.analysis_timeframe = '5m'    # Analysis: 5-minute candles
        self.candle_count = 100           # Candles to fetch per timeframe

        # Scanner
        self.min_volume_24h = float(os.getenv('MIN_VOLUME_24H', '1000000'))

        # Risk
        self.position_pct = 0.05          # 5% of balance per trade
        self.leverage = 3.0               # 3x leverage
        self.sl_emergency_pct = 0.03      # -3% SL emergency
        self.dry_run = os.getenv('DRY_RUN', 'True') == 'True'

        # Fees (Hyperliquid perpetuals — all market orders)
        self.taker_fee = 0.00045          # 0.045%

        # Sentiment gates
        self.gemini_threshold = 0.5       # |sentiment| > 0.5 to enter
        self.volume_confirmation = 1.5    # Volume > 1.5x average

        # RSI bounds (don't enter in extremes)
        self.rsi_overbought = 75
        self.rsi_oversold = 25

        # Pipeline timing
        self.pipeline_interval = 5 * 60   # 5 minutes
        self.guardian_interval = 10        # 10 seconds

        # Simulation
        self.sim_balance = float(os.getenv('SIM_BALANCE', '1000'))

        # Gemini Flash API
        self.gemini_api_key = os.getenv('GEMINI_API_KEY', '')
        self.gemini_model = os.getenv('GEMINI_MODEL', 'gemini-2.5-flash-lite')

        # Polymarket CLOB API
        self.poly_api_key = os.getenv('POLYMARKET_API_KEY', '')
        self.poly_secret = os.getenv('POLYMARKET_SECRET', '')
        self.poly_passphrase = os.getenv('POLYMARKET_PASSPHRASE', '')

        # Hyperliquid
        self.hl_wallet = os.getenv('HYPERLIQUID_API_KEY', '')
        self.hl_private_key = os.getenv('HYPERLIQUID_API_SECRET', '')
        self.hl_base_url = 'https://api.hyperliquid.xyz'

        # Paths
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.market_data_path = os.path.join(self.base_dir, 'market_data.json')
        self.predictions_path = os.path.join(self.base_dir, 'predictions.json')
        self.analytics_path = os.path.join(self.base_dir, 'analytics.json')
        self.state_path = os.path.join(self.base_dir, 'state.json')
        self.trade_history_path = os.path.join(self.base_dir, 'trade_history.json')
        self.audit_log_path = os.path.join(self.base_dir, 'audit_log.json')
