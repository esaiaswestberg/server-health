#!/bin/bash
set -euo pipefail

mkdir -p /data
mkdir -p /root/.codex

CRON_SCHEDULE="${CRON_SCHEDULE:-0 * * * *}"

# Cron jobs run with a near-empty environment, so explicitly capture the
# config vars this app cares about into a file the cron job sources before
# running. printf '%q' gives shell-safe quoting for values with spaces/quotes.
ENV_VARS=(
    NTFY_URL
    NTFY_TOPIC
    NTFY_TOKEN
    NTFY_NOTIFY_ON_OK
    SPEEDTEST_INTERVAL_HOURS
    EXPECTED_DOWNLOAD_MBPS
    EXPECTED_UPLOAD_MBPS
    CERT_HOSTS
    CERT_AUTO_DISCOVER_TRAEFIK
    LOG_LINES_MAX
    CPU_WARN_PCT
    CPU_CRIT_PCT
    MEM_WARN_PCT
    MEM_CRIT_PCT
    DISK_WARN_PCT
    DISK_CRIT_PCT
    GPU_TEMP_WARN_C
    GPU_TEMP_CRIT_C
    GPU_MEM_WARN_PCT
    GPU_MEM_CRIT_PCT
    CODEX_TIMEOUT_SECONDS
)

: > /opt/health-check/.env.runtime
for var in "${ENV_VARS[@]}"; do
    if [ -n "${!var-}" ]; then
        printf 'export %s=%q\n' "$var" "${!var}" >> /opt/health-check/.env.runtime
    fi
done

cat > /etc/cron.d/health-check <<EOF
${CRON_SCHEDULE} root . /opt/health-check/.env.runtime; cd /opt/health-check && python3 -m app.main >> /proc/1/fd/1 2>> /proc/1/fd/2
EOF
chmod 0644 /etc/cron.d/health-check

echo "[entrypoint] starting continuous CPU/memory/GPU sampler"
( cd /opt/health-check && while true; do
    python3 -m app.sampler >> /proc/1/fd/1 2>> /proc/1/fd/2
    echo "[entrypoint] sampler exited, restarting in 5s" >> /proc/1/fd/1
    sleep 5
done ) &

echo "[entrypoint] health-check scheduled: ${CRON_SCHEDULE}"
echo "[entrypoint] starting cron in foreground"
exec cron -f
