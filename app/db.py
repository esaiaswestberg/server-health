"""Shared SQLite storage for everything that needs to survive between
processes/runs: small state values, continuous CPU/mem/GPU samples, and
scheduled-run history.

Single file at DB_PATH (default /data/health.db), WAL mode for safe
concurrent access between the three processes that touch it: the sampler
(writes a metrics row every ~10s), the scheduled check (writes state +
one run per cron tick), and the web dashboard (reads only).
"""

import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get("DB_PATH", "/data/health.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    cpu_pct REAL,
    mem_pct REAL,
    cpu_temp_c REAL
);
CREATE INDEX IF NOT EXISTS idx_metrics_ts ON metrics(ts);

CREATE TABLE IF NOT EXISTS gpu_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_id INTEGER NOT NULL,
    ts TEXT NOT NULL,
    gpu_index INTEGER NOT NULL,
    util_pct REAL,
    mem_pct REAL,
    temp_c REAL
);
CREATE INDEX IF NOT EXISTS idx_gpu_metrics_ts ON gpu_metrics(ts);
CREATE INDEX IF NOT EXISTS idx_gpu_metrics_metric_id ON gpu_metrics(metric_id);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    status TEXT NOT NULL,
    title TEXT,
    summary TEXT,
    highlights_json TEXT,
    recommendations_json TEXT,
    checks_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_ts ON runs(ts);
"""


@contextmanager
def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _migrate(conn) -> None:
    """Lightweight migrations for columns added after a database already
    exists - CREATE TABLE IF NOT EXISTS alone won't add them to an
    existing table. Each check is a cheap PRAGMA read, safe to run on
    every startup."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(metrics)")}
    if "cpu_temp_c" not in columns:
        conn.execute("ALTER TABLE metrics ADD COLUMN cpu_temp_c REAL")


def init_db() -> None:
    """Idempotent - safe to call from every process at startup even if
    another process is creating the schema at the same moment (WAL +
    busy_timeout serialize the two CREATE TABLE IF NOT EXISTS calls)."""
    with connect() as conn:
        conn.executescript(_SCHEMA)
        _migrate(conn)
