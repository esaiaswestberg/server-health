"""Traefik host discovery for the TLS cert-expiry check (app/checks/certs.py).

Two independent, best-effort sources, combined and deduped by certs.py:
- Docker provider: hostnames pulled from running containers' router-rule
  labels via the mounted docker.sock.
- File provider: hostnames pulled from a Traefik dynamic configuration
  directory/file (YAML or TOML) at a host path you set explicitly via
  TRAEFIK_DYNAMIC_CONFIG_PATH. Only the file provider is covered - Traefik's
  other dynamic providers (Consul, etcd, Kubernetes CRD, ...) aren't.

Neither source has a pass/fail status of its own - any failure (Docker
unreachable, path missing, a file that doesn't parse) just means that
source silently contributes no hosts, rather than failing the whole check.
"""

import json
import os
import re
import subprocess
import tomllib

try:
    import yaml
except ImportError:
    yaml = None

from .hostexec import HOST_ROOT

_ENV = dict(os.environ, DOCKER_CLI_EXPERIMENTAL="enabled")

# Matches Traefik router-rule label/config keys, e.g. traefik.http.routers.app.rule
# or traefik.tcp.routers.app.rule (also matches the namespaced Swarm form
# traefik.<name>.http.routers...).
_TRAEFIK_RULE_LABEL = re.compile(r"traefik(\.[^.]+)?\.(?:http|tcp)\.routers\.[^.]+\.rule$")
# Pulls every `Host(\`example.com\`)` (or Host(\`a\`,\`b\`)) out of a rule value.
_TRAEFIK_HOST_PATTERN = re.compile(r"Host(?:SNI)?\(([^)]*)\)")
_BACKTICK_VALUE = re.compile(r"`([^`]+)`")

_CONFIG_EXTENSIONS = {".yml", ".yaml", ".toml"}


def _hosts_from_rule(rule) -> set:
    hosts = set()
    if not isinstance(rule, str):
        return hosts
    for host_expr in _TRAEFIK_HOST_PATTERN.findall(rule):
        hosts.update(_BACKTICK_VALUE.findall(host_expr))
    return hosts


# --- Docker-label discovery ---

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


def discover_from_docker_labels() -> list:
    """Hostnames from running containers' Traefik router-rule labels
    (`traefik.http.routers.<name>.rule=Host(\\`example.com\\`)`). Containers
    explicitly opted out via `traefik.enable=false` are skipped; everything
    else is included, matching Traefik's own default of exposing containers
    unless told not to."""
    hosts = set()
    for labels in _running_container_labels():
        if not labels or labels.get("traefik.enable", "").strip().lower() == "false":
            continue
        for key, value in labels.items():
            if _TRAEFIK_RULE_LABEL.search(key):
                hosts.update(_hosts_from_rule(value))
    return sorted(hosts)


# --- Dynamic-config-file discovery ---

def _iter_config_files(host_path: str):
    local_path = os.path.join(HOST_ROOT, host_path.lstrip("/"))
    if os.path.isfile(local_path):
        yield local_path
    elif os.path.isdir(local_path):
        # Traefik's file provider doesn't recurse into subdirectories by
        # default, so neither do we.
        for entry in sorted(os.listdir(local_path)):
            full = os.path.join(local_path, entry)
            if os.path.isfile(full) and os.path.splitext(entry)[1].lower() in _CONFIG_EXTENSIONS:
                yield full


def _parse_config_file(path: str):
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return None

    try:
        if path.lower().endswith(".toml"):
            return tomllib.loads(raw.decode("utf-8"))
        if yaml is not None:
            return yaml.safe_load(raw)
    except Exception:
        return None
    return None


def _hosts_from_parsed_config(data) -> set:
    hosts = set()
    if not isinstance(data, dict):
        return hosts
    for protocol in ("http", "tcp"):
        routers = (data.get(protocol) or {}).get("routers")
        if not isinstance(routers, dict):
            continue
        for router in routers.values():
            if isinstance(router, dict):
                hosts.update(_hosts_from_rule(router.get("rule")))
    return hosts


def discover_from_dynamic_config(path: str) -> list:
    """Hostnames from a Traefik file-provider dynamic config directory/file
    (YAML or TOML) at the given host path."""
    if not path:
        return []
    hosts = set()
    for file_path in _iter_config_files(path):
        data = _parse_config_file(file_path)
        if data:
            hosts.update(_hosts_from_parsed_config(data))
    return sorted(hosts)


def discover_hosts(config) -> list:
    """Combines both discovery sources per config, deduped."""
    hosts = set()
    if config.get("cert_auto_discover_traefik"):
        hosts.update(discover_from_docker_labels())
    dynamic_path = config.get("traefik_dynamic_config_path")
    if dynamic_path:
        hosts.update(discover_from_dynamic_config(dynamic_path))
    return sorted(hosts)
