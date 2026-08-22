"""Docker container status + "is a newer image available" checks.

Talks to the Docker daemon via the mounted docker.sock (the `docker` CLI
defaults to /var/run/docker.sock). Image-update checks use
`docker manifest inspect`, which only fetches the manifest, not the image
layers - cheap compared to an actual `docker pull`.
"""

import json
import os
import re
import subprocess

from . import CheckResult, Status

_ENV = dict(os.environ, DOCKER_CLI_EXPERIMENTAL="enabled")

# Matches Traefik router-rule label keys, e.g. traefik.http.routers.app.rule
# or traefik.tcp.routers.app.rule (also matches the namespaced Swarm form
# traefik.<name>.http.routers...).
_TRAEFIK_RULE_LABEL = re.compile(r"traefik(\.[^.]+)?\.(?:http|tcp)\.routers\.[^.]+\.rule$")
# Pulls every `Host(\`example.com\`)` (or Host(\`a\`,\`b\`)) out of a rule value.
_TRAEFIK_HOST_PATTERN = re.compile(r"Host(?:SNI)?\(([^)]*)\)")
_BACKTICK_VALUE = re.compile(r"`([^`]+)`")


def _docker_available() -> bool:
    try:
        proc = subprocess.run(
            ["docker", "info"], capture_output=True, text=True, timeout=10, env=_ENV
        )
        return proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _list_containers():
    proc = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{json .}}"],
        capture_output=True, text=True, timeout=20, env=_ENV,
    )
    if proc.returncode != 0:
        return []
    containers = []
    for line in proc.stdout.strip().splitlines():
        try:
            containers.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return containers


def _status_check(container: dict) -> CheckResult:
    name = container.get("Names", "?")
    state = container.get("State", "unknown")
    status_text = container.get("Status", "")

    if state == "running":
        status = Status.CRIT if "unhealthy" in status_text.lower() else Status.OK
    elif state in ("exited", "dead"):
        status = Status.CRIT
    else:
        status = Status.WARN

    return CheckResult(
        category="docker", name=f"{name}-status", status=status,
        note=f"{state}: {status_text}",
        data={"container": name, "image": container.get("Image", "")},
    )


def _remote_digest(image: str):
    try:
        proc = subprocess.run(
            ["docker", "manifest", "inspect", "-v", image],
            capture_output=True, text=True, timeout=15, env=_ENV,
        )
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0:
        return None
    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    # `docker manifest inspect -v` returns either one object or a list (multi-arch).
    entries = parsed if isinstance(parsed, list) else [parsed]
    for entry in entries:
        digest = entry.get("Descriptor", {}).get("digest")
        if digest:
            return digest
    return None


def _local_digest(image: str):
    proc = subprocess.run(
        ["docker", "image", "inspect", image, "--format", "{{json .RepoDigests}}"],
        capture_output=True, text=True, timeout=10, env=_ENV,
    )
    if proc.returncode != 0:
        return None
    try:
        digests = json.loads(proc.stdout.strip())
    except json.JSONDecodeError:
        return None
    for d in digests or []:
        if "@" in d:
            return d.split("@", 1)[1]
    return None


def _update_check(container: dict) -> CheckResult:
    name = container.get("Names", "?")
    image = container.get("Image", "")

    local = _local_digest(image)
    if local is None:
        return CheckResult(
            category="docker", name=f"{name}-update", status=Status.OK,
            note=f"{image}: no registry digest locally (custom/locally-built image?), skipping",
        )

    remote = _remote_digest(image)
    if remote is None:
        return CheckResult(
            category="docker", name=f"{name}-update", status=Status.OK,
            note=f"{image}: could not reach registry to check for updates",
        )

    if remote != local:
        return CheckResult(
            category="docker", name=f"{name}-update", status=Status.WARN,
            note=f"{image}: newer image available in registry",
            data={"local_digest": local, "remote_digest": remote},
        )
    return CheckResult(
        category="docker", name=f"{name}-update", status=Status.OK,
        note=f"{image}: up to date",
    )


def _running_container_labels():
    try:
        proc = subprocess.run(
            ["docker", "ps", "-q"], capture_output=True, text=True, timeout=15, env=_ENV
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []

    ids = proc.stdout.split()
    if not ids:
        return []

    try:
        proc = subprocess.run(
            ["docker", "inspect"] + ids, capture_output=True, text=True, timeout=20, env=_ENV
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []

    try:
        entries = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    return [(e.get("Config") or {}).get("Labels") or {} for e in entries]


def discover_traefik_hosts() -> list:
    """Pulls hostnames out of running containers' Traefik router-rule labels
    (`traefik.http.routers.<name>.rule=Host(\\`example.com\\`)`), for
    auto-feeding into the TLS cert-expiry check. Containers explicitly opted
    out via `traefik.enable=false` are skipped; everything else is included,
    matching Traefik's own default of exposing containers unless told not to.
    Returns [] on any Docker/parsing failure - this is a best-effort data
    source, not a check with its own pass/fail status.
    """
    hosts = set()
    for labels in _running_container_labels():
        if not labels or labels.get("traefik.enable", "").strip().lower() == "false":
            continue
        for key, value in labels.items():
            if not _TRAEFIK_RULE_LABEL.search(key):
                continue
            for host_expr in _TRAEFIK_HOST_PATTERN.findall(value):
                hosts.update(_BACKTICK_VALUE.findall(host_expr))
    return sorted(hosts)


def run(config) -> list:
    if not _docker_available():
        return [
            CheckResult(
                category="docker", name="daemon", status=Status.OK,
                note="Docker socket not reachable, skipping Docker checks",
            )
        ]

    containers = _list_containers()
    if not containers:
        return [
            CheckResult(
                category="docker", name="containers", status=Status.OK,
                note="no containers found",
            )
        ]

    results = [_status_check(c) for c in containers]
    running = [c for c in containers if c.get("State") == "running"]
    for c in running:
        results.append(_update_check(c))
    return results
