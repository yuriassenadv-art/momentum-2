# momentum-2/control/commands.py
"""Command Processor — External control via cmd.json.

Supports pause, resume, and status commands.
Reads cmd.json from base_dir, processes it, and deletes after handling.
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from config import Config


def check_for_commands(base_dir: str) -> str | None:
    """Check for and process a command from cmd.json.

    Supported commands:
        {"action": "pause"}   — Pauses the system.
        {"action": "resume"}  — Resumes the system.
        {"action": "status"}  — Returns current pause state (no side effects).

    Args:
        base_dir: Project base directory containing cmd.json.

    Returns:
        Action string ('pause', 'resume', 'status') or None if no command.
    """
    cmd_path = os.path.join(base_dir, 'cmd.json')
    pause_path = os.path.join(base_dir, 'pause_state.json')

    if not os.path.exists(cmd_path):
        return None

    try:
        with open(cmd_path, 'r') as f:
            cmd = json.load(f)
    except (json.JSONDecodeError, IOError):
        os.remove(cmd_path)
        return None

    action = cmd.get('action')

    if action == 'pause':
        _write_pause_state(pause_path, paused=True)
        print(f"[CONTROL] System PAUSED at {time.strftime('%Y-%m-%d %H:%M:%S')}")

    elif action == 'resume':
        _write_pause_state(pause_path, paused=False)
        print(f"[CONTROL] System RESUMED at {time.strftime('%Y-%m-%d %H:%M:%S')}")

    elif action == 'status':
        paused = is_paused(base_dir)
        status = 'PAUSED' if paused else 'RUNNING'
        print(f"[CONTROL] Status: {status}")

    # Delete cmd.json after processing
    try:
        os.remove(cmd_path)
    except OSError:
        pass

    return action


def is_paused(base_dir: str) -> bool:
    """Check if the system is currently paused.

    Args:
        base_dir: Project base directory containing pause_state.json.

    Returns:
        True if system is paused, False otherwise.
    """
    pause_path = os.path.join(base_dir, 'pause_state.json')

    if not os.path.exists(pause_path):
        return False

    try:
        with open(pause_path, 'r') as f:
            state = json.load(f)
        return state.get('paused', False)
    except (json.JSONDecodeError, IOError):
        return False


def _write_pause_state(pause_path: str, paused: bool) -> None:
    """Write the pause state file.

    Args:
        pause_path: Path to pause_state.json.
        paused: Whether the system is paused.
    """
    state = {
        'paused': paused,
        'since': time.time(),
        'since_human': time.strftime('%Y-%m-%d %H:%M:%S'),
    }

    with open(pause_path, 'w') as f:
        json.dump(state, f, indent=2)
