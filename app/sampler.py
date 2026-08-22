"""Continuous background sampler: records CPU/memory/GPU utilization every
SAMPLE_INTERVAL_SECONDS to /data/metrics.jsonl, so the scheduled check can
summarize the whole window between runs (min/avg/max/p95) instead of a
single instant snapshot that could miss a transient spike entirely.

Started once by entrypoint.sh (wrapped in a restart loop), not by cron -
this process runs for the container's whole lifetime. `app/checks/continuous.py`
is the reader; this module is the sole writer, and never mutates the file
except by atomic replace during pruning, so the two never race destructively.
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import psutil

from .hostexec import chroot_run

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sampler")

METRICS_PATH = os.environ.get("METRICS_PATH", "/data/metrics.jsonl")
SAMPLE_INTERVAL_SECONDS = float(os.environ.get("SAMPLE_INTERVAL_SECONDS", 10))
METRICS_RETENTION_HOURS = float(os.environ.get("METRICS_RETENTION_HOURS", 48))
PRUNE_EVERY_N_SAMPLES = 360  # ~1h at the default 10s interval

GPU_QUERY_FIELDS = "index,utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu"


def _configure_procfs():
    if os.path.isdir("/host/proc"):
        psutil.PROCFS_PATH = "/host/proc"


def _detect_gpu() -> bool:
    proc = chroot_run(["nvidia-smi", "-L"], timeout=15)
    return bool(proc is not None and proc.returncode == 0 and proc.stdout.strip())


def _sample_gpu():
    proc = chroot_run(
        ["nvidia-smi", f"--query-gpu={GPU_QUERY_FIELDS}", "--format=csv,noheader,nounits"],
        timeout=15,
    )
    if proc is None or proc.returncode != 0:
        return None

    readings = []
    for line in proc.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 6:
            continue
        try:
            index, util_gpu, util_mem, mem_used, mem_total, temp = parts
            mem_used_mb = float(mem_used)
            mem_total_mb = float(mem_total)
            readings.append({
                "index": int(index),
                "util_pct": float(util_gpu),
                "mem_pct": (mem_used_mb / mem_total_mb * 100) if mem_total_mb else 0.0,
                "mem_used_mb": mem_used_mb,
                "mem_total_mb": mem_total_mb,
                "temp_c": float(temp),
            })
        except ValueError:
            continue
    return readings or None


def _take_sample(gpu_available: bool) -> dict:
    sample = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "cpu_pct": psutil.cpu_percent(interval=None),
        "mem_pct": psutil.virtual_memory().percent,
    }
    if gpu_available:
        gpu = _sample_gpu()
        if gpu:
            sample["gpu"] = gpu
    return sample


def _append_sample(sample: dict):
    os.makedirs(os.path.dirname(METRICS_PATH), exist_ok=True)
    with open(METRICS_PATH, "a") as f:
        f.write(json.dumps(sample) + "\n")


def _prune():
    """Rewrites metrics.jsonl keeping only samples within the retention
    window, via temp-file + atomic os.replace so a concurrent reader
    (app/checks/continuous.py) never sees a torn/partial file."""
    if not os.path.exists(METRICS_PATH):
        return

    cutoff = datetime.now(timezone.utc) - timedelta(hours=METRICS_RETENTION_HOURS)
    kept = []
    dropped = 0
    with open(METRICS_PATH, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ts = datetime.fromisoformat(json.loads(line)["ts"])
            except (json.JSONDecodeError, KeyError, ValueError):
                continue
            if ts >= cutoff:
                kept.append(line)
            else:
                dropped += 1

    if dropped == 0:
        return

    tmp_path = METRICS_PATH + ".tmp"
    with open(tmp_path, "w") as f:
        for line in kept:
            f.write(line + "\n")
    os.replace(tmp_path, METRICS_PATH)
    log.info("pruned %d sample(s) older than %sh (%d remaining)", dropped, METRICS_RETENTION_HOURS, len(kept))


def main():
    _configure_procfs()
    psutil.cpu_percent(interval=None)  # prime it - the first call's own value is meaningless

    gpu_available = _detect_gpu()
    log.info(
        "sampler starting: interval=%ss retention=%sh gpu=%s",
        SAMPLE_INTERVAL_SECONDS, METRICS_RETENTION_HOURS, gpu_available,
    )

    count = 0
    while True:
        time.sleep(SAMPLE_INTERVAL_SECONDS)
        try:
            _append_sample(_take_sample(gpu_available))
        except Exception:
            log.exception("failed to take/append sample")
            continue

        count += 1
        if count % PRUNE_EVERY_N_SAMPLES == 0:
            try:
                _prune()
            except Exception:
                log.exception("failed to prune metrics file")


if __name__ == "__main__":
    main()
