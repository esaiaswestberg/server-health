"""Optional TLS certificate expiry watch for a configured host:port list.

Entirely skipped (returns no results) when CERT_HOSTS isn't set - most
single-server setups don't need this, but it's cheap to offer for anyone
terminating TLS themselves (reverse proxy, mail server, etc.).
"""

import socket
import ssl
from datetime import datetime, timezone

from . import CheckResult, Status

WARN_DAYS = 21
CRIT_DAYS = 7


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


def run(config) -> list:
    hosts = config.get("cert_hosts") or []
    results = []
    for entry in hosts:
        entry = entry.strip()
        if not entry:
            continue
        if ":" in entry:
            host, port_str = entry.rsplit(":", 1)
            port = int(port_str)
        else:
            host, port = entry, 443
        results.append(_check_one(host, port))
    return results
