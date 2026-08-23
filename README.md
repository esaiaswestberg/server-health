# server-health

An AI-powered server health check. A long-running Docker container
continuously samples CPU/memory/GPU usage in the background, and on a
configurable cron schedule inspects the host it runs on (that continuous
window, disk, Docker containers, pending OS updates, internet connectivity/
speed, recent system log errors, and optionally TLS certificate expiry),
hands the raw data to [Codex CLI](https://developers.openai.com/codex) for
an AI diagnosis, pushes the result to your phone/desktop via
[ntfy](https://ntfy.sh), and serves a live web dashboard of everything it
just found.

Codex is authenticated with your **ChatGPT account** (Plus/Pro/Team/Enterprise
login), not a metered API key.

## What it checks

| Category | What |
|---|---|
| System (continuous) | CPU %, memory %, and CPU temperature, sampled every `SAMPLE_INTERVAL_SECONDS` in the background and summarized as min/avg/max/p95 over the whole window since the last run |
| GPU (continuous) | NVIDIA GPU utilization/VRAM/temperature, sampled the same way (skipped entirely if no GPU is detected) |
| System (point-in-time) | Load average, swap, uptime |
| Disk | Usage % + inodes per real host filesystem, `docker system df` breakdown |
| Updates | Pending OS package updates, reboot-required flag, failed systemd units |
| Docker | Container status/health, whether a newer image is available in the registry |
| Network | Ping reachability/latency, throughput speed test (own cadence, see below) |
| Logs | Warning/error-level lines from journald (or syslog fallback) since the last run |
| Certs | TLS expiry watch for a configured `host:port` list, plus hostnames auto-discovered from Traefik router labels and/or a dynamic config file (see below) |

The continuous CPU/memory/GPU sampling (`app/sampler.py`) is the one piece
that isn't tied to the cron schedule at all - it runs for the whole
container lifetime, recording a data point every few seconds to the SQLite
database. This means a 10-minute CPU spike or memory pressure event between
two scheduled checks still shows up in the next report, where a single
instant snapshot at check-time would have completely missed it.

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

   Or skip building locally and use the image a GitHub Actions workflow
   publishes to GHCR on every push to `main`
   (`ghcr.io/esaiaswestberg/server-health:latest`) - swap `build: .` for
   `image: ghcr.io/esaiaswestberg/server-health:latest` in
   `docker-compose.yml` if you'd rather pull than build.

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

## Deploying via Portainer

Every setting in `docker-compose.yml` is a `${VAR:-default}` substitution
rather than something read from a physical `.env` file (via `env_file:`),
specifically so this deploys cleanly from Portainer's "Stacks" feature
(Git repository or Web editor), which doesn't create a `.env` file next to
the compose file the way a local `docker compose up` does.

1. Create the stack, pointing it at this repo (or pasting the compose file
   into the web editor).
2. In the stack's **Environment variables** section, add each setting you
   want to override as a `KEY=value` pair - at minimum `NTFY_TOPIC`. This
   works exactly like a `.env` file would; Portainer passes them through as
   substitution variables when it runs the deploy, no file needed. See
   `.env.example` for the full list of what's available and their
   defaults - everything not set there falls back to the same default it
   would locally.
3. `NTFY_TOPIC` has no default and deploying without it set fails fast with
   a clear error, rather than the container starting and the scheduled
   check silently failing every run.
4. The one setting that's still edited directly into `docker-compose.yml`
   rather than as a variable is the Traefik basic-auth password hash - see
   "Web dashboard" below for why, and do this in Portainer's stack editor
   (the compose file content itself) rather than via environment variables.

A real `.env` file (for plain `docker compose up`, not Portainer) still
works exactly as before - Compose auto-loads one from the project root for
substitution regardless of how the service's variables are declared.

**Set `DATA_DIR` to an absolute host path in Portainer - don't leave it as
the relative default.** Portainer versions every stack update into its own
subfolder (e.g. `.../v1/docker-compose.yml`, then `.../v2/...` on the next
update). A relative `./data` resolves against that folder, so it can point
at a different, empty directory each time you update the stack - the
scheduled-check history and continuous CPU/memory/GPU samples silently
reset instead of persisting. Set `DATA_DIR` in the stack's environment
variables to something stable and absolute, e.g. `/opt/server-health/data`
(the directory is created automatically if it doesn't exist). Same
reasoning applies to `CODEX_AUTH_DIR` (default `~/.codex`) - set it
explicitly too rather than relying on `~` resolving to wherever Portainer
happens to run as.

## Web dashboard

> **No authentication of its own.** The dashboard is entirely read-only (no
> buttons, no forms, nothing to interact with) but has **no login built
> in**. It displays real information about your server: hostnames,
> container names, disk paths, and raw log-line snippets among them. By
> default (below) it's routed through Traefik with a basic-auth middleware
> - don't remove that auth without putting something equivalent in its
> place, and don't skip straight to the `ports:` fallback further down
> without understanding it has no auth at all.

It shows the same things ntfy gets notified about, live and continuously:

- The latest AI report (status badge, summary, highlights, recommendations).
- Every individual check result from the most recent run, grouped by category.
- A table of recent runs, so you can see status changes over time at a
  glance - click any row to see that run's full report and check breakdown.
- Charts of CPU, memory, CPU temperature (when a readable sensor is found),
  and per-GPU utilization/VRAM/temperature over the last
  `DASHBOARD_CHART_HOURS` (default 6), built from the same continuous
  samples the scheduled check summarizes.

It's a small Flask app (`app/web.py`), started alongside the sampler and
cron by `entrypoint.sh`. The page itself polls itself every 30 seconds via
[htmx](https://htmx.org) for the status/checks/history, and separately
re-fetches `/api/metrics` every 30 seconds to redraw the charts
([Chart.js](https://www.chartjs.org), loaded from a CDN by the browser -
nothing added to the image for it). There's nothing to click; just leave it
open on a second monitor or check in on it.

### Setting it up behind Traefik (default)

`docker-compose.yml` ships with this wired up by default: the service joins
your Traefik instance's Docker network directly (no port published on the
host at all) and carries Traefik labels with a basic-auth middleware, so
the *only* way to reach the dashboard is through Traefik with a password.
Three things need setting up once:

1. **The network.** `docker-compose.yml` attaches to the external network
   named by `TRAEFIK_NETWORK` in `.env` (default `traefik`). Check what
   your Traefik container is actually attached to:

   ```sh
   docker inspect <your-traefik-container> --format '{{json .NetworkSettings.Networks}}'
   ```

   If it's not called `traefik`, set `TRAEFIK_NETWORK` in `.env` to the
   real name. If the network doesn't exist as a standalone Docker network
   yet (e.g. Traefik's compose file defines it as its own default network
   with a fixed name, which already creates it), create it once with
   `docker network create traefik`.

2. **The hostname.** Set `DASHBOARD_DOMAIN` in `.env` to whatever hostname
   you want the dashboard reachable at, e.g. `health.yourdomain.com`. The
   `labels:` block already includes `routers.server-health.tls=true` -
   **don't remove it**, even if you're not using a custom cert resolver.
   Without it, Traefik's HTTPS router matching silently ignores this
   router entirely (it'll still show up fine in the Traefik dashboard as a
   valid, "Success"-status router - the failure only shows up as every
   request 404ing, which is a nasty one to debug). If your other
   Traefik-routed services also set explicit `entrypoints` or
   `tls.certresolver` labels, add matching ones for this service too - see
   the commented example right in `docker-compose.yml`'s `labels:` block.

3. **The password.** Generate an htpasswd hash:

   ```sh
   docker run --rm httpd:2.4-alpine htpasswd -nbB admin 'your-password-here'
   ```

   That prints something like `admin:$2y$05$abc...xyz`. Open
   `docker-compose.yml` and replace the placeholder in the
   `basicauth.users` label with it - **doubling every single `$` to `$$`**
   (so `$2y$05$abc` becomes `$$2y$$05$$abc`; this is a docker-compose
   escaping requirement, not a Traefik one). This is the one setting in the
   whole project that's edited directly in `docker-compose.yml` instead of
   `.env` - Compose's `.env`-file interpolation has known bugs with
   `$`-heavy values passed through into labels this way, so keeping it out
   of `.env` avoids that entirely.

Then `docker compose up -d` as usual.

### Not using Traefik

Comment out the whole `labels:` block in `docker-compose.yml` and uncomment
the `ports:` block right below it instead - that publishes the dashboard on
`WEB_PORT` (default 8080, `.env`). It still has **no authentication of its
own** in this mode, so put some other reverse proxy or auth layer in front
before exposing it beyond a trusted LAN.

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
  the Codex call itself fails. CPU/memory are now checked against the window
  since the last run (max for CRIT, max-or-avg for WARN), not an instant value.
- `SAMPLE_INTERVAL_SECONDS` (default 10) / `METRICS_RETENTION_HOURS`
  (default 48) - how often the background sampler records a CPU/memory/GPU
  data point, and how long those samples are kept before being pruned.
- `CPU_TEMP_WARN_C` / `CPU_TEMP_CRIT_C` - CPU temperature thresholds,
  max-based (a brief spike matters, same reasoning as GPU temperature).
  Ignored if no readable sensor is found on the host (VMs and some ARM
  boards don't expose one).
- `GPU_TEMP_WARN_C` / `GPU_TEMP_CRIT_C` / `GPU_MEM_WARN_PCT` /
  `GPU_MEM_CRIT_PCT` - GPU status thresholds. Deliberately based on
  temperature and VRAM usage, not utilization - a GPU sitting at 100%
  compute utilization is often exactly what you want (transcoding,
  inference), so treating that as a warning would just be noise. Ignored
  entirely if no NVIDIA GPU is detected.
- `CERT_HOSTS` - manual `host:port` list (port defaults to 443), combined
  with whatever Traefik auto-discovery below turns up.
- `CERT_AUTO_DISCOVER_TRAEFIK` - `true` (default) auto-discovers hostnames
  from running containers' `traefik.http.routers.*.rule=Host(\`...\`)` /
  `traefik.tcp.routers.*.rule=HostSNI(\`...\`)` labels via docker.sock, so a
  Traefik-fronted setup gets TLS-expiry monitoring with zero manual config.
  Containers labeled `traefik.enable=false` are skipped; everything else is
  included, matching Traefik's own default. Set to `false` to disable.
- `TRAEFIK_DYNAMIC_CONFIG_PATH` - optional host path to a Traefik dynamic
  configuration directory or file (YAML or TOML, the file provider) -
  hostnames from its `http`/`tcp` routers' `rule` fields are discovered the
  same way as the Docker-label source, for routers defined outside of
  container labels. Only the file provider is covered (not Consul/etcd/
  Kubernetes CRD/etc.), and directories aren't scanned recursively, matching
  Traefik's own file-provider behavior. Unset by default (skipped).
- `TRAEFIK_NETWORK` (default `traefik`) / `DASHBOARD_DOMAIN` - which Docker
  network the dashboard joins to reach Traefik, and the hostname Traefik
  routes to it. See "Web dashboard" above for the full one-time setup
  (including the basic-auth password, which is set directly in
  `docker-compose.yml` rather than here).
- `WEB_PORT` (default 8080) - only used in the non-Traefik `ports:`
  fallback mode. See "Web dashboard" above.
- `DASHBOARD_CHART_HOURS` (default 6) - how much history the dashboard's
  charts show.
- `HISTORY_MAX_RUNS` (default 500) - how many past scheduled runs (report +
  full check breakdown) are kept for the dashboard's history table.

## A note on the mounts

`docker-compose.yml` mounts several host paths so the container can see
*your* server instead of just its own tiny sandbox:

- `/proc:/host/proc:ro` and `/:/host/root:ro` - real host CPU/memory/disk
  numbers (via `psutil.PROCFS_PATH`) and disk usage, instead of the
  container's own cgroup/overlay view.
- `/sys:/host/root/sys:ro` - CPU temperature sensors
  (`/sys/class/thermal`, `/sys/class/hwmon`), same reasoning as `/proc`
  above (it's normally its own separate mount, so it doesn't come along
  with the `/:/host/root` bind). Read-only; just exposes hardware sensor
  metadata, nothing sensitive.
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
- `/dev:/host/root/dev:ro` - lets `chroot /host/root nvidia-smi` reach the
  NVIDIA device nodes for GPU sampling, using the host's own `nvidia-smi`
  and matching driver libraries (no NVIDIA Container Toolkit needed). **This
  is the broadest-access mount in the project** - unlike the others, it
  exposes *every* host device node (disks, TTYs, everything under `/dev`),
  not just the GPU, even mounted read-only. If your server has no GPU, just
  delete this line (and the `device_cgroup_rules:` block right above it)
  from `docker-compose.yml` - the sampler detects the missing `nvidia-smi`
  and skips GPU sampling gracefully either way.
- `device_cgroup_rules: ["c 195:* rmw"]` - the `/dev` bind mount above only
  makes the GPU device nodes *visible*; Docker's cgroup device controller
  separately blocks actually opening them by default, regardless of the
  mount. Without this, GPU sampling silently stays off
  (`nvidia-smi` fails with `Failed to initialize NVML: Unknown Error`, even
  though `ls /host/root/dev` shows the device files just fine). `195` is
  NVIDIA's fixed device major number - this grants exactly the access
  needed, without `privileged: true` or the full NVIDIA Container Toolkit.
- `~/.codex:/root/.codex` - Codex's ChatGPT-account auth, read-write so it
  can refresh its own token in place.
- `./data:/data` - the SQLite database (state, continuous CPU/memory/GPU
  samples, run history for the web dashboard). Nothing sensitive.

All of the above except the last two are mounted `:ro`.

## Extending

Each check lives in its own file under `app/checks/` and returns a list of
small `CheckResult(category, name, status, note, data)` objects - add a new
module and wire it into `app/main.py`'s `main()` to add another check.
