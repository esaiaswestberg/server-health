"""Small JSON-file-backed state store for cross-run bookkeeping.

Tracks things that need to survive between cron invocations: when logs were
last scanned (so we only report new lines) and when the speed test last ran
(so it can have its own, less frequent cadence than the main schedule).
"""

import json
import os
from datetime import datetime, timezone

STATE_PATH = os.environ.get("STATE_PATH", "/data/state.json")

_DEFAULTS = {
    "last_run": None,
    "last_log_scan": None,
    "last_speedtest": None,
}


def load() -> dict:
    if not os.path.exists(STATE_PATH):
        return dict(_DEFAULTS)
    try:
        with open(STATE_PATH, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return dict(_DEFAULTS)
    merged = dict(_DEFAULTS)
    merged.update(data)
    return merged


def save(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp_path = STATE_PATH + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp_path, STATE_PATH)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(value):
    if not value:
        return None
    return datetime.fromisoformat(value)
