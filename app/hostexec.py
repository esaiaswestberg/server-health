"""Shared helper for running the *host's* own binaries read-only.

The container's own filesystem/package database is irrelevant for anything
that needs to inspect the host (package manager, systemctl, journalctl,
nvidia-smi, ...). Since the host root filesystem is bind-mounted read-only
at /host/root, we `chroot` into it and run the host's own binaries against
the host's real state - no need to install a second copy of any of this in
the image, and the read-only mount means it can't modify anything.
"""

import os
import socket
import subprocess

HOST_ROOT = "/host/root"


def chroot_run(cmd, timeout=30):
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


def host_has(path_in_host: str) -> bool:
    return os.path.exists(os.path.join(HOST_ROOT, path_in_host.lstrip("/")))


def get_hostname() -> str:
    """The *host's* hostname, not the container's own (Docker gives every
    container its own UTS namespace, so socket.gethostname() alone would
    return something like a random container ID)."""
    try:
        with open(os.path.join(HOST_ROOT, "etc/hostname")) as f:
            name = f.read().strip()
            if name:
                return name
    except OSError:
        pass
    return socket.gethostname()
