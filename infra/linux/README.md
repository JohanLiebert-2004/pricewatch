# Local collectors and external watchdog

The Linux PC runs Big W, Chemist Warehouse, JB Hi-Fi and Sephora collectors,
plus monitoring outside both Oracle VMs. The standalone daily database backup
has its own runtime and credentials; see `LOCAL-BACKUP.md` in the main working copy.

## Runtime layout

- Source: `~/.local/share/pricewatch-runtime/source` (public Git checkout).
- Python: `~/.local/share/pricewatch-runtime/venv` with `requirements.txt`.
- Private environment: `~/.config/pricewatch-runtime/collector.env` (0600,
  parent directory 0700); source `.env` links here for the existing wrappers.
- `~/pricewatch` is a convenience symlink to the durable source directory.
- Watchdog state/log: `~/.local/state/pricewatch-runtime`.
- Collector logs: `local_<retailer>.log` in the source checkout.

The private environment supplies the current OCI `DATABASE_URL`, the existing
PC forwarding-only `TUNNEL_SSH_KEY`, `PYTHONUNBUFFERED=1`, and
`PRICEWATCH_STATE_DIR`. Browser settings are `BIGW_BROWSER=firefox`,
`GECKODRIVER=/snap/bin/geckodriver` and
`FIREFOX_BINARY=/snap/firefox/current/usr/lib/firefox/firefox`. Sephora selects
Firefox in its wrapper. Telegram settings and `SITE_URL` retain the owner's
existing watchdog destination. Never copy environment contents, keys, dumps
or notification state into Git.

## Services

Install the ten files in `services/` under `~/.config/systemd/user/`, preserving
any previous units first, then `systemctl --user daemon-reload` and
`systemd-analyze --user verify ~/.config/systemd/user/pricewatch-*.service`.
After verifying real runs, enable the applicable timers with
`systemctl --user enable --now pricewatch-<name>.timer`.

Big W runs every three hours; Chemist Warehouse and JB Hi-Fi every thirty
minutes, at different offsets; Sephora at :08/:38; watchdog every thirty
minutes. Timers persist missed runs and keep their original jitter. User
lingering must be enabled so jobs continue without an interactive login.
Deadlines terminate the entire service process group, including its tunnel.

The CLI holds a PostgreSQL session advisory lock for each locally owned
retailer, shared with the current GitHub Actions code. A second process skips
collection without requests or a success heartbeat. Database/lock errors fail
closed. Heartbeat gates still avoid unnecessary CI work. Keep CI on the same
guarded CLI before restoring overlapping schedules.

Retailer access stays serial with the existing delays. Firefox uses its
ordinary identity and disposable profiles. Sephora's AU price-book headers
include the explicit `en-AU` API locale. A retired Big W category returning
404 consumes its request budget and moves behind unvisited categories;
403/429/challenge responses still stop the batch. Do not change transport or
identity after a refusal.

## Verification

Service exit status alone is insufficient. Check the collector's output,
committed cursor/products and fresh `*_local_heartbeat` row. Failed or empty
runs leave success heartbeats unchanged.

Run the watchdog with `--check-only` through the same unit environment for a
JSON health report without notifications or notification-state changes. It
checks the actual `crawl.yml` workflow, five collector heartbeats (including
Kmart on OCI), both SSH endpoints, the public website and catalogue API.
Scheduled monitoring debounces confirmed Telegram deliveries; failed delivery
is retried on the next check.

## 13 November 2026 repair evidence

The old services referenced a deleted `~/pricewatch/.venv`; all five service
and timer pairs were restored independently of the working backup. Original
units are saved privately under
`~/.local/state/pricewatch-runtime/repair-20261113T100417Z`.

The actual two-session database test proved exclusion and release. A Sephora
two-page production run completed at 10:11:49 UTC: 1,000 listings, 18 changed
price snapshots, committed cursor and fresh heartbeat. JB's bulk endpoint
returned no listings, including at page one; a separate public product check
returned HTTP 429. Further JB requests were stopped; no replacement identity,
transport or synthetic success marker was used.

The watchdog check-only run detected the stale collection workflow and Kmart
VM heartbeat while confirming the public service and both SSH endpoints. A
read-only GitHub diagnostic run succeeded, proving Actions and the database
tunnel still work; this does not prove the hourly scheduler has recovered.

Big W resumed past the retired category and checked 1,440 listings, storing
779 products and 563 changed-price snapshots, before an HTTP 403 at
10:16:14 UTC. It stopped and skipped product enrichment; the success heartbeat
was correctly left unchanged. This is partial data recovery, not a completed
healthy sweep. JB and Big W access refusals remain external limitations.
