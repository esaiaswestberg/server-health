"""Summarizes the continuous CPU/memory/GPU samples collected by
app/sampler.py since the last scheduled run, instead of a point-in-time
snapshot that could miss what happened in between (e.g. a 10-minute CPU
spike that's already over by the time the scheduled check runs).
"""

from datetime import datetime, timedelta, timezone

from . import CheckResult, Status
from ..metrics_store import load_samples


def _percentile(values, pct):
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (pct / 100)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def _window_stat_line(values, unit=""):
    return (
        f"min {min(values):.1f}{unit}, avg {sum(values) / len(values):.1f}{unit}, "
        f"max {max(values):.1f}{unit}, p95 {_percentile(values, 95):.1f}{unit}"
    )


def _cpu_mem_results(samples, config) -> list:
    results = []

    cpu_values = [s["cpu_pct"] for s in samples if "cpu_pct" in s]
    if cpu_values:
        avg = sum(cpu_values) / len(cpu_values)
        status = Status.OK
        if max(cpu_values) >= config["cpu_crit_pct"]:
            status = Status.CRIT
        elif max(cpu_values) >= config["cpu_warn_pct"] or avg >= config["cpu_warn_pct"]:
            status = Status.WARN
        results.append(CheckResult(
            category="system", name="cpu", status=status,
            note=f"{_window_stat_line(cpu_values, '%')} over {len(cpu_values)} samples",
            data={"samples": len(cpu_values)},
        ))

    mem_values = [s["mem_pct"] for s in samples if "mem_pct" in s]
    if mem_values:
        avg = sum(mem_values) / len(mem_values)
        status = Status.OK
        if max(mem_values) >= config["mem_crit_pct"]:
            status = Status.CRIT
        elif max(mem_values) >= config["mem_warn_pct"] or avg >= config["mem_warn_pct"]:
            status = Status.WARN
        results.append(CheckResult(
            category="system", name="memory", status=status,
            note=f"{_window_stat_line(mem_values, '%')} over {len(mem_values)} samples",
            data={"samples": len(mem_values)},
        ))

    temp_values = [s["cpu_temp_c"] for s in samples if "cpu_temp_c" in s]
    if temp_values:
        # Peak matters most for thermal safety (a brief spike is still a
        # real spike), so this is max-based only - same reasoning as GPU
        # temperature below, unlike CPU/memory % where sustained average
        # load is also meaningful.
        status = Status.OK
        if max(temp_values) >= config["cpu_temp_crit_c"]:
            status = Status.CRIT
        elif max(temp_values) >= config["cpu_temp_warn_c"]:
            status = Status.WARN
        results.append(CheckResult(
            category="system", name="cpu-temp", status=status,
            note=f"{_window_stat_line(temp_values, '°C')} over {len(temp_values)} samples",
            data={"samples": len(temp_values)},
        ))

    return results


def _gpu_results(samples, config) -> list:
    by_index = {}
    for s in samples:
        for gpu in s.get("gpu", []):
            by_index.setdefault(gpu["index"], []).append(gpu)

    results = []
    for index, readings in sorted(by_index.items()):
        util = [r["util_pct"] for r in readings]
        mem = [r["mem_pct"] for r in readings]
        temp = [r["temp_c"] for r in readings]

        # Status is driven by temperature/VRAM only, not utilization -
        # sustained 100% compute utilization is normal for a GPU doing real
        # work (transcoding, inference), so flagging it would just be noise.
        status = Status.OK
        if max(temp) >= config["gpu_temp_crit_c"] or max(mem) >= config["gpu_mem_crit_pct"]:
            status = Status.CRIT
        elif max(temp) >= config["gpu_temp_warn_c"] or max(mem) >= config["gpu_mem_warn_pct"]:
            status = Status.WARN

        results.append(CheckResult(
            category="gpu", name=f"gpu{index}", status=status,
            note=(
                f"util {_window_stat_line(util, '%')}; "
                f"VRAM {_window_stat_line(mem, '%')}; "
                f"temp {_window_stat_line(temp, '°C')} "
                f"over {len(readings)} samples"
            ),
            data={"samples": len(readings)},
        ))
    return results


def run(config, state) -> list:
    last_run = state.get("last_run")
    since = None
    if last_run:
        try:
            since = datetime.fromisoformat(last_run)
        except ValueError:
            since = None
    if since is None:
        since = datetime.now(timezone.utc) - timedelta(hours=1)

    samples = load_samples(since)
    if not samples:
        return [
            CheckResult(
                category="system", name="cpu-memory-window", status=Status.OK,
                note="no continuous samples yet (sampler may have just started)",
            )
        ]

    return _cpu_mem_results(samples, config) + _gpu_results(samples, config)
