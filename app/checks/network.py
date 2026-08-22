"""Internet reachability/latency (every run) + a throughput speed test on its
own, much less frequent cadence (it uses real bandwidth), tracked via state.
"""

import re
import subprocess
from datetime import datetime, timezone

from . import CheckResult, Status

PING_TARGETS = ["1.1.1.1", "8.8.8.8"]


def _ping_check() -> CheckResult:
    reachable = 0
    latencies = []
    details = []

    for target in PING_TARGETS:
        try:
            proc = subprocess.run(
                ["ping", "-c", "3", "-W", "2", target],
                capture_output=True, text=True, timeout=15,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            details.append(f"{target}: ping unavailable")
            continue

        loss_match = re.search(r"(\d+)% packet loss", proc.stdout)
        rtt_match = re.search(r"= [\d.]+/([\d.]+)/", proc.stdout)
        loss_pct = int(loss_match.group(1)) if loss_match else 100

        if loss_pct < 100:
            reachable += 1
        if rtt_match:
            latencies.append(float(rtt_match.group(1)))
        details.append(f"{target}: {loss_pct}% loss")

    status = Status.OK
    if reachable == 0:
        status = Status.CRIT
    elif any("100% loss" in d for d in details):
        status = Status.WARN

    avg_latency = sum(latencies) / len(latencies) if latencies else None
    note = "; ".join(details)
    if avg_latency is not None:
        note += f"; avg latency {avg_latency:.1f} ms"

    return CheckResult(
        category="network", name="connectivity", status=status, note=note,
        data={"reachable_targets": reachable, "avg_latency_ms": avg_latency},
    )


def _speedtest_check(config, state) -> CheckResult:
    last = state.get("last_speedtest")
    interval_hours = config["speedtest_interval_hours"]

    if last:
        elapsed_hours = (
            datetime.now(timezone.utc) - datetime.fromisoformat(last)
        ).total_seconds() / 3600
        if elapsed_hours < interval_hours:
            remaining = interval_hours - elapsed_hours
            return CheckResult(
                category="network", name="speedtest", status=Status.OK,
                note=f"skipped (last run {elapsed_hours:.1f}h ago, next in {remaining:.1f}h)",
            )

    try:
        import speedtest

        st = speedtest.Speedtest()
        st.get_best_server()
        download_mbps = st.download() / 1_000_000
        upload_mbps = st.upload() / 1_000_000
    except Exception as exc:  # speedtest-cli raises a variety of its own exceptions
        return CheckResult(
            category="network", name="speedtest", status=Status.WARN,
            note=f"speed test failed: {exc}",
        )

    state["last_speedtest"] = datetime.now(timezone.utc).isoformat()

    status = Status.OK
    expected_down = config.get("expected_download_mbps")
    expected_up = config.get("expected_upload_mbps")
    if expected_down and download_mbps < expected_down * 0.5:
        status = Status.WARN
    if expected_up and upload_mbps < expected_up * 0.5:
        status = Status.WARN

    return CheckResult(
        category="network", name="speedtest", status=status,
        note=f"download {download_mbps:.1f} Mbps, upload {upload_mbps:.1f} Mbps",
        data={"download_mbps": download_mbps, "upload_mbps": upload_mbps},
    )


def run(config, state) -> list:
    return [_ping_check(), _speedtest_check(config, state)]
