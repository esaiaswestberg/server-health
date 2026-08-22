# server-health

An AI-powered server health check. A long-running Docker container wakes up
on a configurable cron schedule, inspects the host it runs on (CPU, memory,
disk, Docker containers, pending OS updates, internet connectivity/speed,
recent system log errors, and optionally TLS certificate expiry), hands the
raw data to [Codex CLI](https://developers.openai.com/codex) for an AI
diagnosis, and pushes the result to your phone/desktop via
[ntfy](https://ntfy.sh).

Codex is authenticated with your **ChatGPT account** (Plus/Pro/Team/Enterprise
login), not a metered API key.

## What it checks

| Category | What |
|---|---|
| System | CPU %, load average, memory, swap, uptime |
| Disk | Usage % + inodes per real host filesystem, `docker system df` breakdown |
| Updates | Pending OS package updates, reboot-required flag, failed systemd units |
| Docker | Container status/health, whether a newer image is available in the registry |
| Network | Ping reachability/latency, throughput speed test (own cadence, see below) |
| Logs | Warning/error-level lines from journald (or syslog fallback) since the last run |
| Certs | Optional TLS expiry watch for a configured `host:port` list |

Every run, all of this is fed to Codex, which returns a structured verdict
(`ok` / `warning` / `critical`) plus a plain-language summary, highlights,
and recommendations. If the Codex call fails for any reason (auth expired,
network issue, timeout), the check **doesn't go silent** - it falls back to
a plain report built from the raw threshold checks so you still get notified.

## One-time setup

1. Copy `.env.example` to `.env` and fill in at least `NTFY_TOPIC` (pick a
   private, hard-to-guess topic name if using the public `ntfy.sh`).

2. Build the image:

   ```sh
   docker compose build
   ```

3. Log in to Codex with your ChatGPT account. This has to be interactive
   once, since headless `codex exec` can't complete an OAuth login itself:

   ```sh
   docker compose run --rm health-check codex login
   ```

   Follow the printed URL/instructions. Credentials are written to
   `~/.codex/auth.json` on the **host** (via the mount configured by
   `CODEX_AUTH_DIR` in `.env`, default `~/.codex`), so every subsequent
   scheduled run reuses them - no re-login needed, and Codex refreshes the
   token in that file as needed.

4. Start it:

   ```sh
   docker compose up -d
   ```

   It'll log the configured schedule and then run `python3 -m app.main`
   inside the container each time that schedule fires. Watch it with
   `docker compose logs -f`.

## One-off run (no waiting for the schedule)

```sh
docker compose run --rm health-check python3 -m app.main
```

Useful for testing your `.env` and confirming a notification actually
arrives at your ntfy topic.

## Configuration

See `.env.example` for the full list with defaults. The notable ones:

- `CRON_SCHEDULE` - standard 5-field cron expression, default hourly
  (`0 * * * *`). Change it and re-run `docker compose up -d` to apply.
- `NTFY_NOTIFY_ON_OK` - `true` (default) sends a notification every run,
  even when everything's fine, as a dead-man's-switch so you know the
  checker itself is alive. Set `false` to only be notified on warning/critical.
- `SPEEDTEST_INTERVAL_HOURS` - the throughput speed test uses real bandwidth,
  so it runs on its own cadence (default daily) independent of `CRON_SCHEDULE`.
- `CPU_WARN_PCT` / `CPU_CRIT_PCT` / `MEM_*` / `DISK_*` - thresholds used both
  to size what's handed to the AI and as the deterministic fallback status if
  the Codex call itself fails.

## A note on the mounts

`docker-compose.yml` mounts several host paths so the container can see
*your* server instead of just its own tiny sandbox:

- `/proc:/host/proc:ro` and `/:/host/root:ro` - real host CPU/memory/disk
  numbers (via `psutil.PROCFS_PATH`) and disk usage, instead of the
  container's own cgroup/overlay view.
- `/var/run/docker.sock:/var/run/docker.sock:ro` - container status and
  image-update checks. **This is still root-equivalent access to your
  Docker daemon**, even mounted read-only, since anything that can talk to
  the socket can ask it to do anything (including running privileged
  containers). Only run this image from a source you trust.
- `/run/systemd:/host/root/run/systemd:ro` and
  `/var/log/journal:/host/root/var/log/journal:ro` - let the container
  `chroot /host/root` and run the **host's own** `journalctl`/`systemctl`
  binaries read-only, instead of installing a second copy of systemd inside
  the image. If your host doesn't use systemd, these mounts are harmless
  no-ops and the log/failed-unit checks fall back gracefully (log scanning
  falls back to grepping `/host/root/var/log/*`).
- `~/.codex:/root/.codex` - Codex's ChatGPT-account auth, read-write so it
  can refresh its own token in place.
- `./data:/data` - small JSON state file (last run time, log-scan cursor,
  last speed test time). Nothing sensitive.

All of the above except the last two are mounted `:ro`.

## Extending

Each check lives in its own file under `app/checks/` and returns a list of
small `CheckResult(category, name, status, note, data)` objects - add a new
module and wire it into `app/main.py`'s `main()` to add another check.
