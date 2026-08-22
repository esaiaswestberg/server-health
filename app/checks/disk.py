"""Disk usage: host filesystems (via the /host/root bind mount) + Docker's
own storage breakdown (images/containers/volumes/build cache).
"""

import json
import os
import shutil
import subprocess

from . import CheckResult, Status

HOST_ROOT = "/host/root"
HOST_PROC_MOUNTS = "/host/proc/mounts"

_SKIP_FSTYPES = {
    "proc", "sysfs", "tmpfs", "devtmpfs", "devpts", "cgroup", "cgroup2",
    "overlay", "squashfs", "mqueue", "debugfs", "tracefs", "securityfs",
    "pstore", "bpf", "autofs", "binfmt_misc", "rpc_pipefs", "nsfs",
    "fusectl", "configfs", "hugetlbfs",
}


def _iter_host_mounts():
    """Yield (mountpoint, local_path) for real host filesystems.

    Reads /host/proc/mounts to discover them, but only yields ones actually
    reachable through the /host/root bind mount - separate partitions that
    weren't recursively bound simply get skipped when opening them fails.
    """
    if not os.path.isfile(HOST_PROC_MOUNTS):
        yield "/", HOST_ROOT
        return

    seen = set()
    with open(HOST_PROC_MOUNTS) as f:
        for line in f:
            parts = line.split()
            if len(parts) < 3:
                continue
            mountpoint, fstype = parts[1], parts[2]
            if fstype in _SKIP_FSTYPES or mountpoint in seen:
                continue
            seen.add(mountpoint)
            local_path = HOST_ROOT if mountpoint == "/" else os.path.join(
                HOST_ROOT, mountpoint.lstrip("/")
            )
            yield mountpoint, local_path


def _filesystem_checks(config) -> list:
    results = []
    warn_pct = config["disk_warn_pct"]
    crit_pct = config["disk_crit_pct"]

    for mountpoint, local_path in _iter_host_mounts():
        try:
            usage = shutil.disk_usage(local_path)
            statvfs = os.statvfs(local_path)
        except OSError:
            # Not reachable through the bind mount (e.g. a separate
            # partition that wasn't recursively bound) - skip it.
            continue

        used_pct = (usage.used / usage.total * 100) if usage.total else 0
        inodes_total = statvfs.f_files
        inodes_free = statvfs.f_ffree
        inode_pct = ((inodes_total - inodes_free) / inodes_total * 100) if inodes_total else 0

        status = Status.OK
        if used_pct >= crit_pct or inode_pct >= crit_pct:
            status = Status.CRIT
        elif used_pct >= warn_pct or inode_pct >= warn_pct:
            status = Status.WARN

        free_gb = usage.free / (1024 ** 3)
        results.append(
            CheckResult(
                category="disk",
                name=mountpoint,
                status=status,
                note=(
                    f"{used_pct:.1f}% used ({free_gb:.1f} GB free), "
                    f"inodes {inode_pct:.1f}% used"
                ),
                data={
                    "mountpoint": mountpoint,
                    "used_pct": round(used_pct, 1),
                    "free_bytes": usage.free,
                    "total_bytes": usage.total,
                    "inode_pct": round(inode_pct, 1),
                },
            )
        )
    return results


def _docker_storage_check() -> list:
    try:
        proc = subprocess.run(
            ["docker", "system", "df", "--format", "{{json .}}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return [
            CheckResult(
                category="disk",
                name="docker-storage",
                status=Status.OK,
                note=f"Docker storage check skipped: {exc}",
            )
        ]

    if proc.returncode != 0:
        return [
            CheckResult(
                category="disk",
                name="docker-storage",
                status=Status.OK,
                note=f"Docker storage check skipped: {proc.stderr.strip()[:200]}",
            )
        ]

    lines = []
    for line in proc.stdout.strip().splitlines():
        try:
            lines.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if not lines:
        return []

    summary = "; ".join(
        f"{row.get('Type', '?')}: {row.get('Size', '?')} "
        f"(reclaimable {row.get('Reclaimable', '?')})"
        for row in lines
    )
    return [
        CheckResult(
            category="disk",
            name="docker-storage",
            status=Status.OK,
            note=summary,
            data={"rows": lines},
        )
    ]


def run(config) -> list:
    return _filesystem_checks(config) + _docker_storage_check()
