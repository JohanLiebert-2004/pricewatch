"""Home-IP Big W sweep - runs on the owner's PC via Windows Task Scheduler.

Big W's Akamai blocks datacenter IPs (GitHub Actions runners) but accepts
direct requests from a residential connection (verified 18 July 2026 from
the owner's Optus line: product pages and 144-product listing pages both
pass cleanly with no proxy). This script runs the same refresh + crawl
lanes as CI, but direct - free and unlimited, unlike the byte-capped
Webshare proxy lane.

Coordination with CI (one crawler per retailer at a time, always):
- PROXY_URL is stripped from the environment, so scrapers/bigw.py applies
  no byte caps and cmd_crawl records nothing against the Webshare budget.
- On a successful refresh this writes an ISO timestamp to the
  `bigw_local_heartbeat` kv row. The CI Big W gate in crawl.yml skips its
  proxy lane while the heartbeat is younger than 24h, and automatically
  resumes (byte-capped) if this PC stays off for a day.

Scheduled task (created 18 July 2026, runs hidden via pythonw.exe):
  schtasks /Create /TN "Dealwatch BigW sweep" /SC HOURLY /MO 3 ...
Logs append to local_bigw.log next to this file (gitignored, self-trimmed).
"""
import os
import subprocess
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(ROOT, "local_bigw.log")
LOG_KEEP_LINES = 2000
REFRESH_BUDGET = "400"   # full catalogue is ~190 listing pages; headroom
CRAWL_BATCH = "200"      # product-page enrichment (images, was-prices)


def log(f, msg):
    f.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}\n")
    f.flush()


def main():
    env = dict(os.environ)
    for line in open(os.path.join(ROOT, ".env"), encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env.setdefault(k, v)
    env.pop("PROXY_URL", None)   # direct from the home IP - never the proxy
    if os.path.exists(LOG):
        lines = open(LOG, encoding="utf-8", errors="replace").readlines()
        if len(lines) > LOG_KEEP_LINES:
            open(LOG, "w", encoding="utf-8").writelines(lines[-LOG_KEEP_LINES:])
    with open(LOG, "a", encoding="utf-8") as f:
        log(f, "=== sweep start ===")
        refresh_ok = False
        for name, cmd in (
                ("refresh", ["refresh", "bigw", "--budget", REFRESH_BUDGET]),
                ("crawl", ["crawl", "bigw", "--batch", CRAWL_BATCH])):
            try:
                r = subprocess.run(
                    [sys.executable, os.path.join(ROOT, "run.py"), *cmd],
                    cwd=ROOT, env=env, capture_output=True, text=True,
                    timeout=5400)
                tail = (r.stdout + r.stderr).strip().splitlines()[-3:]
                log(f, f"{name}: exit {r.returncode} | " + " / ".join(tail))
                if name == "refresh" and r.returncode == 0:
                    # Exit 0 alone is NOT success: a fully-blocked sweep also
                    # exits 0 ("BLOCKED mid-refresh (keeping what we got)").
                    # Only claim the Big W lane - and silence the CI proxy
                    # fallback - when real listings actually came back.
                    m = __import__("re").search(
                        r"-> (\d+) listings seen", r.stdout)
                    refresh_ok = bool(m) and int(m.group(1)) >= 144
                    if not refresh_ok:
                        log(f, "refresh saw no/too few listings - NOT writing "
                               "heartbeat; CI proxy fallback stays active")
            except Exception as e:
                log(f, f"{name}: {type(e).__name__}: {e}")
        if refresh_ok:
            try:
                import psycopg
                conn = psycopg.connect(env["DATABASE_URL"], connect_timeout=15)
                now = datetime.now(timezone.utc).isoformat(timespec="seconds")
                conn.execute(
                    "INSERT INTO kv (k, v) VALUES ('bigw_local_heartbeat', %s) "
                    "ON CONFLICT (k) DO UPDATE SET v = excluded.v", (now,))
                conn.commit()
                conn.close()
                log(f, f"heartbeat written: {now}")
            except Exception as e:
                log(f, f"heartbeat FAILED: {type(e).__name__}: {e}")
        log(f, "=== sweep end ===")


if __name__ == "__main__":
    main()
