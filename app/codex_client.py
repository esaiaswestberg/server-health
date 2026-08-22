"""Feeds collected check results to Codex CLI for an AI-generated diagnosis.

Codex is authenticated via a ChatGPT account login persisted in the mounted
~/.codex directory (see README) - not a metered API key. Callers should
treat a `None` report as "Codex is unavailable this run" and fall back to a
deterministic report built from the raw check statuses, so alerting never
goes silent just because the AI step failed.
"""

import json
import os
import re
import socket
import subprocess

SCHEMA_PATH = os.environ.get(
    "HEALTH_SCHEMA_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schema", "health_report.schema.json"),
)

INSTRUCTION = (
    "You are a server health monitoring assistant. Below is structured data "
    "collected from a single Linux server by an automated health-check script: "
    "system resource usage, disk usage, pending OS updates, Docker container "
    "status, network connectivity, and recent error/warning log lines. "
    "Analyze it and produce a concise health report for the server's owner. "
    "Use your judgement about severity - a check being individually flagged "
    "WARN/CRIT doesn't automatically mean the overall status should match; "
    "weigh how serious and urgent each finding actually is. Be specific and "
    "reference actual numbers/names from the data, don't restate generic advice."
)


def _build_prompt(check_results, hostname: str) -> str:
    lines = [f"Hostname: {hostname}", ""]
    by_category = {}
    for r in check_results:
        by_category.setdefault(r.category, []).append(r)

    for category, results in by_category.items():
        lines.append(f"## {category}")
        for r in results:
            lines.append(r.to_prompt_line())
        lines.append("")

    return "\n".join(lines)


def _extract_json(text: str):
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Fall back to the largest {...} block in the output, in case any stray
    # text made it onto stdout alongside the JSON.
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return None


def get_report(check_results, config):
    """Returns (report_dict, None) on success, or (None, error_message) on failure."""
    hostname = socket.gethostname()
    prompt_data = _build_prompt(check_results, hostname)

    cmd = [
        "codex", "exec",
        "--sandbox", "read-only",
        "--skip-git-repo-check",
        "--output-schema", SCHEMA_PATH,
        INSTRUCTION,
    ]

    try:
        proc = subprocess.run(
            cmd,
            input=prompt_data,
            capture_output=True,
            text=True,
            timeout=config["codex_timeout_seconds"],
            cwd="/data",
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return None, f"codex exec failed to run: {exc}"

    if proc.returncode != 0:
        return None, f"codex exec exited {proc.returncode}: {proc.stderr.strip()[:500]}"

    report = _extract_json(proc.stdout)
    if report is None:
        return None, f"could not parse codex output as JSON: {proc.stdout.strip()[:500]}"

    required = {"status", "title", "summary", "highlights", "recommendations"}
    missing = required - report.keys()
    if missing:
        return None, f"codex output missing fields {missing}: {report}"

    return report, None
