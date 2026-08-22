"""CPU, memory, swap, load average, uptime.

Relies on `psutil.PROCFS_PATH` having already been pointed at /host/proc
(done once in main.py) so these numbers reflect the host, not the container.
"""

import os
import time

import psutil

from . import CheckResult, Status


def _cpu_check(config) -> CheckResult:
    pct = psutil.cpu_percent(interval=1)
    status = Status.OK
    if pct >= config["cpu_crit_pct"]:
        status = Status.CRIT
    elif pct >= config["cpu_warn_pct"]:
        status = Status.WARN
    return CheckResult(
        category="system",
        name="cpu",
        status=status,
        note=f"{pct:.1f}% utilization",
        data={"percent": pct, "cores": psutil.cpu_count() or 0},
    )


def _load_check() -> CheckResult:
    # /proc/loadavg reflects host-wide load even from inside a container
    # (load average isn't namespaced), so no /host/proc redirect needed here.
    load1, load5, load15 = os.getloadavg()
    cores = psutil.cpu_count() or 1
    ratio = load1 / cores
    status = Status.OK
    if ratio >= 2.0:
        status = Status.CRIT
    elif ratio >= 1.0:
        status = Status.WARN
    return CheckResult(
        category="system",
        name="load",
        status=status,
        note=f"load avg {load1:.2f}, {load5:.2f}, {load15:.2f} across {cores} cores",
        data={"load1": load1, "load5": load5, "load15": load15, "cores": cores},
    )


def _memory_check(config) -> CheckResult:
    mem = psutil.virtual_memory()
    status = Status.OK
    if mem.percent >= config["mem_crit_pct"]:
        status = Status.CRIT
    elif mem.percent >= config["mem_warn_pct"]:
        status = Status.WARN
    return CheckResult(
        category="system",
        name="memory",
        status=status,
        note=f"{mem.percent:.1f}% used ({mem.available / (1024**3):.1f} GB available)",
        data={"percent": mem.percent, "total_bytes": mem.total, "available_bytes": mem.available},
    )


def _swap_check() -> CheckResult:
    swap = psutil.swap_memory()
    status = Status.OK
    if swap.total > 0 and swap.percent >= 80:
        status = Status.WARN
    return CheckResult(
        category="system",
        name="swap",
        status=status,
        note=(
            f"{swap.percent:.1f}% used of {swap.total / (1024**3):.1f} GB"
            if swap.total
            else "no swap configured"
        ),
        data={"percent": swap.percent, "total_bytes": swap.total},
    )


def _uptime_check() -> CheckResult:
    boot_time = psutil.boot_time()
    uptime_seconds = time.time() - boot_time
    days = uptime_seconds / 86400
    return CheckResult(
        category="system",
        name="uptime",
        status=Status.OK,
        note=f"up {days:.1f} days",
        data={"uptime_seconds": uptime_seconds, "boot_time": boot_time},
    )


def run(config) -> list:
    return [
        _cpu_check(config),
        _load_check(),
        _memory_check(config),
        _swap_check(),
        _uptime_check(),
    ]
