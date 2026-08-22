"""Recent warning/error-level system log lines, since the last run.

Primary path: `chroot /host/root journalctl` (host's own binary + journal,
via chroot - see updates.py for why). Falls back to grepping common syslog
files under /host/root/var/log when journalctl isn't available (non-systemd
hosts, or no persistent journal).
"""

import glob
import os
import re
import subprocess

from . import CheckResult, Status

HOST_ROOT = "/host/root"
_ERROR_PATTERN = re.compile(r"\b(error|err|fail(ed|ure)?|critical|crit|panic)\b", re.IGNORECASE)
_FALLBACK_LOG_GLOBS = [
    "var/log/syslog",
    "var/log/syslog.1",
    "var/log/messages",
    "var/log/kern.log",
]


def _chroot_run(cmd, timeout=30):
    if not os.path.isdir(HOST_ROOT):
        return None
    try:
        return subprocess.run(
            ["chroot", HOST_ROOT] + cmd,
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired):
        return None


def _journalctl_lines(since_iso: str, max_lines: int):
    proc = _chroot_run(
        ["journalctl", "--no-pager", "-p", "warning", "--since", since_iso, "-o", "short-iso"],
        timeout=30,
    )
    if proc is None or proc.returncode != 0:
        return None
    lines = [l for l in proc.stdout.strip().splitlines() if l.strip()]
    return lines[-max_lines:]


def _fallback_lines(max_lines: int):
    matches = []
    for pattern in _FALLBACK_LOG_GLOBS:
        for path in glob.glob(os.path.join(HOST_ROOT, pattern)):
            try:
                with open(path, "r", errors="ignore") as f:
                    tail = f.readlines()[-2000:]
            except OSError:
                continue
            for line in tail:
                if _ERROR_PATTERN.search(line):
                    matches.append(line.strip())
    return matches[-max_lines:]


def run(config, state) -> list:
    max_lines = config["log_lines_max"]
    since_iso = state.get("last_log_scan") or "24 hours ago"

    lines = _journalctl_lines(since_iso, max_lines)
    source = "journalctl"
    if lines is None:
        lines = _fallback_lines(max_lines)
        source = "syslog fallback (best-effort, not time-scoped)"

    if not lines:
        return [
            CheckResult(
                category="logs", name="errors", status=Status.OK,
                note=f"no warning/error log lines since last run (source: {source})",
            )
        ]

    status = Status.WARN
    return [
        CheckResult(
            category="logs", name="errors", status=status,
            note=f"{len(lines)} warning/error log line(s) since last run (source: {source})",
            data={"lines": lines, "source": source},
        )
    ]
