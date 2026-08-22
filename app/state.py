"""Small key/value state store (SQLite-backed, see app/db.py) for cross-run
bookkeeping: when logs were last scanned (so we only report new lines) and
when the speed test last ran (so it can have its own, less frequent cadence
than the main schedule).
"""

from datetime import datetime, timezone

from . import db

_DEFAULTS = {
    "last_run": None,
    "last_log_scan": None,
    "last_speedtest": None,
}


def load() -> dict:
    state = dict(_DEFAULTS)
    with db.connect() as conn:
        rows = conn.execute("SELECT key, value FROM state").fetchall()
    for row in rows:
        state[row["key"]] = row["value"]
    return state


def save(state: dict) -> None:
    with db.connect() as conn:
        conn.executemany(
            "INSERT INTO state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            list(state.items()),
        )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(value):
    if not value:
        return None
    return datetime.fromisoformat(value)
