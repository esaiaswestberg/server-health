"""TLS certificate expiry watch.

Checks a manually configured `host:port` list (CERT_HOSTS) plus, by
default, every hostname auto-discovered from Traefik (Docker labels and/or
a dynamic config file - see app/traefik.py) - so a Traefik-fronted setup
gets cert-expiry monitoring for free, without hand-maintaining a host list.
Entirely skipped (returns no results) if no source turns up anything.
"""

import socket
import ssl
from datetime import datetime, timezone

from . import CheckResult, Status
from .. import traefik

WARN_DAYS = 21
CRIT_DAYS = 7
DEFAULT_PORT = 443


def _check_one(host: str, port: int) -> CheckResult:
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert()
        not_after = datetime.strptime(
            cert["notAfter"], "%b %d %H:%M:%S %Y %Z"
        ).replace(tzinfo=timezone.utc)
        days_left = (not_after - datetime.now(timezone.utc)).days

        if days_left <= CRIT_DAYS:
            status = Status.CRIT
        elif days_left <= WARN_DAYS:
            status = Status.WARN
        else:
            status = Status.OK

        return CheckResult(
            category="certs", name=f"{host}:{port}", status=status,
            note=f"expires in {days_left} day(s) ({not_after.date().isoformat()})",
            data={"days_left": days_left},
        )
    except Exception as exc:
        return CheckResult(
            category="certs", name=f"{host}:{port}", status=Status.WARN,
            note=f"TLS check failed: {exc}",
        )


def _parse_entry(entry: str):
    entry = entry.strip()
    if not entry:
        return None
    if ":" in entry:
        host, port_str = entry.rsplit(":", 1)
        try:
            return host, int(port_str)
        except ValueError:
            return None
    return entry, DEFAULT_PORT


def run(config) -> list:
    targets = set()

    for entry in config.get("cert_hosts") or []:
        parsed = _parse_entry(entry)
        if parsed:
            targets.add(parsed)

    for host in traefik.discover_hosts(config):
        parsed = _parse_entry(host)
        if parsed:
            targets.add(parsed)

    return [_check_one(host, port) for host, port in sorted(targets)]
