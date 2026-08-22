"""Shared reader for the continuous CPU/mem/GPU samples app/sampler.py
writes to SQLite (see app/db.py).

Used by app/checks/continuous.py (window summary for the scheduled check)
and app/web.py (time-series data for the dashboard's charts).
"""

from datetime import datetime

from . import db


def load_samples(since=None) -> list:
    """Returns sample dicts shaped like
    {"ts": datetime, "cpu_pct": .., "mem_pct": .., "gpu": [{"index", "util_pct",
    "mem_pct", "temp_c"}, ...]}, optionally filtered to samples strictly
    after `since`, oldest first."""
    query = "SELECT id, ts, cpu_pct, mem_pct FROM metrics"
    params = []
    if since is not None:
        query += " WHERE ts > ?"
        params.append(since.isoformat())
    query += " ORDER BY ts ASC"

    with db.connect() as conn:
        metric_rows = conn.execute(query, params).fetchall()
        if not metric_rows:
            return []

        ids = [row["id"] for row in metric_rows]
        placeholders = ",".join("?" * len(ids))
        gpu_rows = conn.execute(
            f"SELECT metric_id, gpu_index, util_pct, mem_pct, temp_c "
            f"FROM gpu_metrics WHERE metric_id IN ({placeholders})",
            ids,
        ).fetchall()

    gpu_by_metric = {}
    for row in gpu_rows:
        gpu_by_metric.setdefault(row["metric_id"], []).append({
            "index": row["gpu_index"],
            "util_pct": row["util_pct"],
            "mem_pct": row["mem_pct"],
            "temp_c": row["temp_c"],
        })

    samples = []
    for row in metric_rows:
        sample = {
            "ts": datetime.fromisoformat(row["ts"]),
            "cpu_pct": row["cpu_pct"],
            "mem_pct": row["mem_pct"],
        }
        gpu = gpu_by_metric.get(row["id"])
        if gpu:
            sample["gpu"] = gpu
        samples.append(sample)
    return samples
