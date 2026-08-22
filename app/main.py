"""Orchestrator: run all checks, hand them to Codex for analysis, notify via
ntfy, persist state. Invoked on a schedule by entrypoint.sh's cron job as
`python3 -m app.main`.
"""

import logging
import os

import psutil

from . import codex_client, notify
from . import state as state_module
from .checks import Status, certs as check_certs, disk as check_disk
from .checks import docker_checks
from .checks import logs as check_logs
from .checks import network as check_network
from .checks import system as check_system
from .checks import updates as check_updates
from .checks import worst

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("health-check")


def _configure_procfs():
    if os.path.isdir("/host/proc"):
        psutil.PROCFS_PATH = "/host/proc"


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def load_config():
    ntfy_topic = os.environ.get("NTFY_TOPIC")
    if not ntfy_topic:
        raise SystemExit("NTFY_TOPIC is required (set it in .env)")

    cert_hosts_raw = os.environ.get("CERT_HOSTS", "")
    cert_hosts = [h for h in cert_hosts_raw.split(",") if h.strip()]

    return {
        "ntfy_url": os.environ.get("NTFY_URL", "https://ntfy.sh"),
        "ntfy_topic": ntfy_topic,
        "ntfy_token": os.environ.get("NTFY_TOKEN") or None,
        "notify_on_ok": os.environ.get("NTFY_NOTIFY_ON_OK", "true").lower() != "false",
        "speedtest_interval_hours": _env_float("SPEEDTEST_INTERVAL_HOURS", 24),
        "expected_download_mbps": _env_float("EXPECTED_DOWNLOAD_MBPS", 0) or None,
        "expected_upload_mbps": _env_float("EXPECTED_UPLOAD_MBPS", 0) or None,
        "cert_hosts": cert_hosts,
        "log_lines_max": _env_int("LOG_LINES_MAX", 200),
        "cpu_warn_pct": _env_float("CPU_WARN_PCT", 80),
        "cpu_crit_pct": _env_float("CPU_CRIT_PCT", 95),
        "mem_warn_pct": _env_float("MEM_WARN_PCT", 85),
        "mem_crit_pct": _env_float("MEM_CRIT_PCT", 95),
        "disk_warn_pct": _env_float("DISK_WARN_PCT", 80),
        "disk_crit_pct": _env_float("DISK_CRIT_PCT", 95),
        "codex_timeout_seconds": _env_int("CODEX_TIMEOUT_SECONDS", 120),
    }


def _deterministic_report(check_results, error_note=None):
    """Safety-net report used when the Codex call fails, so alerting never
    goes silent just because the AI step errored out."""
    status = worst(r.status for r in check_results)
    non_ok = [r.to_prompt_line() for r in check_results if r.status != Status.OK]
    summary = "AI analysis unavailable this run - showing raw check results."
    if error_note:
        summary += f" ({error_note})"
    return {
        "status": status.value,
        "title": "Server health (raw checks, AI unavailable)",
        "summary": summary,
        "highlights": non_ok,
        "recommendations": [],
    }


def main():
    _configure_procfs()
    config = load_config()
    state = state_module.load()

    check_results = []
    check_results += check_system.run(config)
    check_results += check_disk.run(config)
    check_results += check_updates.run(config)
    check_results += docker_checks.run(config)
    check_results += check_network.run(config, state)
    check_results += check_logs.run(config, state)
    check_results += check_certs.run(config)

    log.info("collected %d check results", len(check_results))

    report, error = codex_client.get_report(check_results, config)
    if report is None:
        log.warning("Codex report unavailable, falling back to raw report: %s", error)
        report = _deterministic_report(check_results, error)
    else:
        log.info("Codex report: status=%s title=%r", report["status"], report["title"])

    try:
        notify.send(report, config)
    except Exception:
        log.exception("failed to send ntfy notification")

    state["last_run"] = state_module.now_iso()
    state["last_log_scan"] = state_module.now_iso()
    state_module.save(state)


if __name__ == "__main__":
    main()
