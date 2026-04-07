# momentum-2/orchestration/fsm.py
"""Finite State Machine — Simplified 2-state FSM per asset.

States: Flat, Active. That's it.
Each asset has independent FSM tracking position state.
"""
import json
import time
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from config import Config


class AssetFSM:
    """Two-state FSM for a single asset: Flat or Active."""

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.state = 'Flat'
        self.direction = None       # 'LONG' or 'SHORT'
        self.entry_price = 0.0
        self.size = 0.0
        self.entered_at = 0.0       # timestamp
        self.tier = 'STANDARD'      # FULL, STANDARD, MOMENTUM, SCOUT

    def enter(self, price: float, size: float, direction: str, tier: str = 'STANDARD'):
        """Transition from Flat to Active."""
        if self.state != 'Flat':
            raise ValueError(f'{self.symbol}: cannot enter, already {self.state}')
        if direction not in ('LONG', 'SHORT'):
            raise ValueError(f'Invalid direction: {direction}')

        self.state = 'Active'
        self.direction = direction
        self.entry_price = price
        self.size = size
        self.entered_at = time.time()
        self.tier = tier

    def exit(self):
        """Transition from Active to Flat. Resets all fields.

        Raises:
            ValueError: If already Flat.
        """
        if self.state != 'Active':
            raise ValueError(f'{self.symbol}: cannot exit, already Flat')

        self.state = 'Flat'
        self.direction = None
        self.entry_price = 0.0
        self.size = 0.0
        self.entered_at = 0.0
        self.tier = 'STANDARD'

    def to_dict(self) -> dict:
        """Serialize FSM state to dict."""
        return {
            'symbol': self.symbol,
            'state': self.state,
            'direction': self.direction,
            'entry_price': self.entry_price,
            'size': self.size,
            'entered_at': self.entered_at,
            'tier': self.tier,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'AssetFSM':
        """Deserialize FSM state from dict."""
        fsm = cls(data['symbol'])
        fsm.state = data.get('state', 'Flat')
        fsm.direction = data.get('direction')
        fsm.entry_price = data.get('entry_price', 0.0)
        fsm.size = data.get('size', 0.0)
        fsm.entered_at = data.get('entered_at', 0.0)
        fsm.tier = data.get('tier', 'STANDARD')
        return fsm


class FSMManager:
    """Manages all asset FSMs. Persists to state.json."""

    def __init__(self, config=None):
        self._config = config if isinstance(config, Config) else Config()
        self._fsms = {}
        self.fsms = self._fsms  # Public access for iteration

    def get_or_create(self, symbol: str) -> AssetFSM:
        """Get existing FSM or create new Flat FSM for symbol."""
        if symbol not in self._fsms:
            self._fsms[symbol] = AssetFSM(symbol)
        return self._fsms[symbol]

    def active_positions(self) -> list:
        """Return list of symbols with Active positions."""
        return [s for s, fsm in self._fsms.items() if fsm.state == 'Active']

    def load(self, path: str = None):
        """Load FSM states from JSON file.

        Args:
            path: File path. Defaults to config.state_path.
        """
        path = path or self._config.state_path
        if not os.path.exists(path):
            return

        with open(path, 'r') as f:
            data = json.load(f)

        self._fsms = {}
        for symbol, fsm_data in data.get('fsms', {}).items():
            self._fsms[symbol] = AssetFSM.from_dict(fsm_data)
        self.fsms = self._fsms

    def save(self, path: str = None):
        """Save FSM states to JSON file.

        Args:
            path: File path. Defaults to config.state_path.
        """
        path = path or self._config.state_path
        data = {
            'fsms': {s: fsm.to_dict() for s, fsm in self._fsms.items()},
        }
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)
