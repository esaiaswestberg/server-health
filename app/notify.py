"""Formats a health report and pushes it to ntfy."""

import requests

from .hostexec import get_hostname

_PRIORITY = {"ok": "3", "warning": "4", "critical": "5"}
_TAGS = {"ok": "white_check_mark", "warning": "warning", "critical": "rotating_light"}


def _build_body(report: dict) -> str:
    parts = [report["summary"]]

    if report.get("highlights"):
        parts.append("")
        parts.extend(f"- {h}" for h in report["highlights"])

    if report.get("recommendations"):
        parts.append("")
        parts.append("Recommended actions:")
        parts.extend(f"- {r}" for r in report["recommendations"])

    return "\n".join(parts)


def send(report: dict, config) -> None:
    status = report.get("status", "ok")

    if status == "ok" and not config["notify_on_ok"]:
        return

    hostname = get_hostname()
    url = f"{config['ntfy_url'].rstrip('/')}/{config['ntfy_topic']}"
    headers = {
        "Title": f"{hostname}: {report.get('title', 'Health check')}",
        "Priority": _PRIORITY.get(status, "3"),
        "Tags": _TAGS.get(status, "information_source"),
    }
    if config.get("ntfy_token"):
        headers["Authorization"] = f"Bearer {config['ntfy_token']}"

    body = _build_body(report).encode("utf-8")

    response = requests.post(url, data=body, headers=headers, timeout=30)
    response.raise_for_status()
