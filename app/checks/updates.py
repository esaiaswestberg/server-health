"""OS-level maintenance signals: pending package updates, reboot-required
flag, failed systemd units.

The container's own package database is irrelevant here - we need the
*host's*. Since the host root filesystem is bind-mounted read-only at
/host/root, we `chroot` into it and run the host's own package-manager /
systemctl binaries against the host's real state. This is read-only (the
mount is `:ro`), so it can't modify anything on the host.
"""

import os
import subprocess

from . import CheckResult, Status

HOST_ROOT = "/host/root"
REBOOT_REQUIRED_PATH = os.path.join(HOST_ROOT, "var/run/reboot-required")


def _chroot_run(cmd, timeout=30):
    """Run `cmd` inside the host root via chroot. Returns CompletedProcess or None."""
    if not os.path.isdir(HOST_ROOT):
        return None
    try:
        return subprocess.run(
            ["chroot", HOST_ROOT] + cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired):
        return None


def _host_has(path_in_host: str) -> bool:
    return os.path.exists(os.path.join(HOST_ROOT, path_in_host.lstrip("/")))


def _pending_updates_check() -> CheckResult:
    if _host_has("usr/bin/apt") or _host_has("usr/bin/apt-get"):
        proc = _chroot_run(["apt", "list", "--upgradable"], timeout=60)
        if proc is None or proc.returncode != 0:
            return CheckResult(
                category="updates", name="pending-updates", status=Status.OK,
                note="apt upgrade check unavailable (chroot failed or apt errored)",
            )
        # First line is "Listing..." noise from apt.
        lines = [l for l in proc.stdout.strip().splitlines() if "/" in l and not l.startswith("Listing")]
        count = len(lines)
        status = Status.WARN if count > 0 else Status.OK
        return CheckResult(
            category="updates", name="pending-updates", status=status,
            note=f"{count} package(s) upgradable" if count else "system up to date",
            data={"count": count, "manager": "apt"},
        )

    if _host_has("usr/bin/dnf") or _host_has("usr/bin/yum"):
        binary = "dnf" if _host_has("usr/bin/dnf") else "yum"
        proc = _chroot_run([binary, "check-update", "--quiet"], timeout=90)
        if proc is None:
            return CheckResult(
                category="updates", name="pending-updates", status=Status.OK,
                note=f"{binary} upgrade check unavailable (chroot failed)",
            )
        # dnf/yum check-update: exit 100 means updates ARE available, 0 means none.
        if proc.returncode not in (0, 100):
            return CheckResult(
                category="updates", name="pending-updates", status=Status.OK,
                note=f"{binary} upgrade check errored: {proc.stderr.strip()[:200]}",
            )
        count = len([l for l in proc.stdout.strip().splitlines() if l.strip()])
        status = Status.WARN if proc.returncode == 100 else Status.OK
        return CheckResult(
            category="updates", name="pending-updates", status=status,
            note=f"~{count} package line(s) upgradable" if status == Status.WARN else "system up to date",
            data={"count": count, "manager": binary},
        )

    if _host_has("usr/bin/pacman"):
        proc = _chroot_run(["pacman", "-Qu"], timeout=60)
        if proc is None:
            return CheckResult(
                category="updates", name="pending-updates", status=Status.OK,
                note="pacman upgrade check unavailable (chroot failed)",
            )
        count = len([l for l in proc.stdout.strip().splitlines() if l.strip()])
        status = Status.WARN if count > 0 else Status.OK
        return CheckResult(
            category="updates", name="pending-updates", status=status,
            note=f"{count} package(s) upgradable" if count else "system up to date",
            data={"count": count, "manager": "pacman"},
        )

    return CheckResult(
        category="updates", name="pending-updates", status=Status.OK,
        note="no supported package manager found on host (apt/dnf/yum/pacman)",
    )


def _reboot_required_check() -> CheckResult:
    if os.path.exists(REBOOT_REQUIRED_PATH):
        return CheckResult(
            category="updates", name="reboot-required", status=Status.WARN,
            note="host has flagged that a reboot is required",
        )
    return CheckResult(
        category="updates", name="reboot-required", status=Status.OK,
        note="no reboot required flag present",
    )


def _failed_units_check() -> CheckResult:
    proc = _chroot_run(["systemctl", "--failed", "--no-legend", "--plain"], timeout=20)
    if proc is None or proc.returncode != 0:
        return CheckResult(
            category="updates", name="failed-systemd-units", status=Status.OK,
            note="systemd unit check unavailable (systemctl unreachable from container)",
        )
    units = [l.split()[0] for l in proc.stdout.strip().splitlines() if l.strip()]
    status = Status.WARN if units else Status.OK
    return CheckResult(
        category="updates", name="failed-systemd-units", status=status,
        note=f"{len(units)} failed unit(s): {', '.join(units[:10])}" if units else "no failed units",
        data={"units": units},
    )


def run(config) -> list:
    return [
        _pending_updates_check(),
        _reboot_required_check(),
        _failed_units_check(),
    ]
