"""Read-only web dashboard: current status, every check result, run
history, and CPU/memory/GPU time-series charts. No authentication of its
own - put a reverse proxy with basic auth (or similar) in front before
exposing this beyond a trusted network. See the README.

Started once by entrypoint.sh (wrapped in a restart loop), alongside the
cron-scheduled checker and the continuous sampler.
"""

import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, render_template, request

from . import db, history
from .hostexec import get_hostname
from .metrics_store import load_samples

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("web")

app = Flask(__name__)

DEFAULT_CHART_HOURS = float(os.environ.get("DASHBOARD_CHART_HOURS", 6))
MAX_CHART_HOURS = 24 * 7
HISTORY_DISPLAY_LIMIT = 50


def _relative_time(iso_ts):
    if not iso_ts:
        return "never"
    try:
        ts = datetime.fromisoformat(iso_ts)
    except ValueError:
        return iso_ts
    seconds = (datetime.now(timezone.utc) - ts).total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


def _grouped_checks(checks):
    groups = defaultdict(list)
    for c in checks:
        groups[c["category"]].append(c)
    return dict(sorted(groups.items()))


def _current_gpu_indices():
    since = datetime.now(timezone.utc) - timedelta(minutes=5)
    indices = set()
    for sample in load_samples(since):
        for gpu in sample.get("gpu", []):
            indices.add(gpu["index"])
    return sorted(indices)


def _cpu_temp_available():
    since = datetime.now(timezone.utc) - timedelta(minutes=5)
    return any("cpu_temp_c" in sample for sample in load_samples(since))


def _dashboard_context():
    runs = history.load_recent(HISTORY_DISPLAY_LIMIT)
    latest = runs[0] if runs else None
    return {
        "hostname": get_hostname(),
        "latest": latest,
        "latest_relative": _relative_time(latest["ts"]) if latest else None,
        "grouped_checks": _grouped_checks(latest["checks"]) if latest else {},
        "history": [{**r, "relative": _relative_time(r["ts"])} for r in runs],
    }


@app.route("/")
def dashboard():
    return render_template(
        "dashboard.html",
        chart_hours=DEFAULT_CHART_HOURS,
        gpu_indices=_current_gpu_indices(),
        cpu_temp_available=_cpu_temp_available(),
        **_dashboard_context(),
    )


@app.route("/fragment")
def fragment():
    return render_template("_fragment.html", **_dashboard_context())


@app.route("/run/<int:run_id>")
def run_detail(run_id):
    run = history.load_by_id(run_id)
    context = {
        "hostname": get_hostname(),
        "run": run,
        "relative": _relative_time(run["ts"]) if run else None,
        "grouped_checks": _grouped_checks(run["checks"]) if run else {},
    }
    status_code = 200 if run else 404
    return render_template("run_detail.html", **context), status_code


def _downsample(points, max_points=300):
    """Buckets (timestamp, value) points into at most max_points,
    averaging each bucket, so long windows don't ship thousands of points
    to the browser."""
    n = len(points)
    if n <= max_points:
        return points
    bucket_size = n / max_points
    result = []
    i = 0.0
    while int(i) < n:
        start = int(i)
        end = min(int(i + bucket_size), n)
        if end <= start:
            end = start + 1
        chunk = points[start:end]
        result.append((chunk[len(chunk) // 2][0], sum(v for _, v in chunk) / len(chunk)))
        i += bucket_size
    return result


def _series(points, multi_day: bool):
    label_fmt = "%m-%d %H:%M" if multi_day else "%H:%M"
    downsampled = _downsample(points)
    return {
        "labels": [ts.strftime(label_fmt) for ts, _ in downsampled],
        "values": [round(v, 1) for _, v in downsampled],
    }


@app.route("/api/metrics")
def api_metrics():
    try:
        hours = float(request.args.get("hours", DEFAULT_CHART_HOURS))
    except ValueError:
        hours = DEFAULT_CHART_HOURS
    hours = max(0.1, min(hours, MAX_CHART_HOURS))

    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    samples = load_samples(since)
    multi_day = hours > 24

    cpu_points = [(s["ts"], s["cpu_pct"]) for s in samples if "cpu_pct" in s]
    mem_points = [(s["ts"], s["mem_pct"]) for s in samples if "mem_pct" in s]
    cpu_temp_points = [(s["ts"], s["cpu_temp_c"]) for s in samples if "cpu_temp_c" in s]

    gpu_by_index = defaultdict(lambda: {"util": [], "mem": [], "temp": []})
    for s in samples:
        for gpu in s.get("gpu", []):
            bucket = gpu_by_index[gpu["index"]]
            bucket["util"].append((s["ts"], gpu["util_pct"]))
            bucket["mem"].append((s["ts"], gpu["mem_pct"]))
            bucket["temp"].append((s["ts"], gpu["temp_c"]))

    payload = {
        "cpu": _series(cpu_points, multi_day),
        "mem": _series(mem_points, multi_day),
        "cpu_temp": _series(cpu_temp_points, multi_day),
        "gpu": {
            str(idx): {
                "util": _series(data["util"], multi_day),
                "mem": _series(data["mem"], multi_day),
                "temp": _series(data["temp"], multi_day),
            }
            for idx, data in sorted(gpu_by_index.items())
        },
    }
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response


def main():
    db.init_db()
    port = int(os.environ.get("WEB_PORT", 8080))
    log.info("dashboard starting on 0.0.0.0:%d", port)
    app.run(host="0.0.0.0", port=port, threaded=True)


if __name__ == "__main__":
    main()
