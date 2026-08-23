"""Persists scheduled-check run history (the AI report plus the raw check
results) to SQLite (see app/db.py) so the web dashboard (app/web.py) can
show the current status and recent history without re-running anything.

Independent of app/state.py (small cursor/bookkeeping values) and
app/sampler.py's continuous CPU/mem/GPU samples.
"""

import json

from . import db


def append_run(entry: dict, max_runs: int = 500) -> None:
    """`entry` is {"ts": iso_str, "report": {...}, "checks": [...]}."""
    report = entry.get("report") or {}
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO runs (ts, status, title, summary, highlights_json, "
            "recommendations_json, checks_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                entry["ts"],
                report.get("status", "ok"),
                report.get("title", ""),
                report.get("summary", ""),
                json.dumps(report.get("highlights", [])),
                json.dumps(report.get("recommendations", [])),
                json.dumps(entry.get("checks", [])),
            ),
        )
        conn.execute(
            "DELETE FROM runs WHERE id NOT IN "
            "(SELECT id FROM runs ORDER BY ts DESC LIMIT ?)",
            (max_runs,),
        )


def _row_to_entry(row) -> dict:
    return {
        "id": row["id"],
        "ts": row["ts"],
        "report": {
            "status": row["status"],
            "title": row["title"],
            "summary": row["summary"],
            "highlights": json.loads(row["highlights_json"] or "[]"),
            "recommendations": json.loads(row["recommendations_json"] or "[]"),
        },
        "checks": json.loads(row["checks_json"] or "[]"),
    }


def load_recent(limit: int = 50) -> list:
    """Returns up to `limit` most recent run entries, newest first."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM runs ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_to_entry(row) for row in rows]


def load_by_id(run_id: int):
    """Returns a single run entry by id, or None if it doesn't exist
    (e.g. already pruned)."""
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return _row_to_entry(row) if row else None
