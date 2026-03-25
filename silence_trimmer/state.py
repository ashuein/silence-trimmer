"""
state.py — lightweight persistence for TUI preferences.
"""

from __future__ import annotations

import json
from pathlib import Path


STATE_PATH = Path.home() / ".silence_trimmer_state.json"


def load_ui_state(path: Path | None = None) -> dict:
    state_path = path or STATE_PATH
    try:
        if not state_path.exists():
            return {}
        data = json.loads(state_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_ui_state(updates: dict, path: Path | None = None) -> dict:
    state_path = path or STATE_PATH
    data = load_ui_state(state_path)
    for key, value in updates.items():
        if value in (None, ""):
            data.pop(key, None)
        else:
            data[key] = value

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data
