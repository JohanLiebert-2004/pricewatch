"""Home-IP Chemist Warehouse sweep - runs on the owner's PC via Windows Task
Scheduler.

Chemist Warehouse sits behind Cloudflare, which blocks GitHub Actions' IP
ranges outright regardless of user-agent honesty - confirmed 18 July 2026
via two separate CI runs, both getting an instant HTTP 403 on request #1.
The same request from the owner's residential IP passes cleanly. Same shape
of problem as Big W's Akamai block; same fix - run the real crawl lane
directly from home instead.

Chemist Warehouse has no bulk-listing lane (robots.txt disallows /api/, and
category pages aren't server-rendered), so unlike Big W there is no refresh
step here - only the crawl (queue) lane matters.

Coordination with CI (one crawler per retailer at a time, always):
- On a successful batch this writes an ISO timestamp to the
  `chemistwarehouse_local_heartbeat` kv row. The CI cadence gate in
  crawl.yml skips its own (always-blocked) crawl attempt while that
  heartbeat is younger than 6h, and resumes trying if this PC stays off.

Scheduled task (created 18 July 2026, runs hidden via pythonw.exe):
  schtasks /Create /TN "Dealwatch ChemistWarehouse sweep" /SC HOURLY /MO 2 ...
Logs append to local_chemistwarehouse.log next to this file (gitignored,
self-trimmed).
"""
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(ROOT, "local_chemistwarehouse.log")
LOG_KEEP_LINES = 2000
CRAWL_BATCH = "400"   # ~10s+jitter delay floor -> ~70min/run, every 2h leaves margin


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
    if os.path.exists(LOG):
        lines = open(LOG, encoding="utf-8", errors="replace").readlines()
        if len(lines) > LOG_KEEP_LINES:
            open(LOG, "w", encoding="utf-8").writelines(lines[-LOG_KEEP_LINES:])
    with open(LOG, "a", encoding="utf-8") as f:
        log(f, "=== sweep start ===")
        crawl_ok = False
        try:
            r = subprocess.run(
                [sys.executable, os.path.join(ROOT, "run.py"),
                 "crawl", "chemistwarehouse", "--batch", CRAWL_BATCH],
                cwd=ROOT, env=env, capture_output=True, text=True,
                timeout=5400)
            tail = (r.stdout + r.stderr).strip().splitlines()[-3:]
            log(f, f"crawl: exit {r.returncode} | " + " / ".join(tail))
            # Exit 0 alone is NOT success: a blocked-on-request-#1 batch also
            # exits 0 ("batch done: 0/N products stored"). Only claim the
            # lane - and silence the CI fallback attempt - when real
            # product pages actually came back.
            m = re.search(r"batch done: (\d+)/\d+ products stored", r.stdout)
            crawl_ok = bool(m) and int(m.group(1)) > 0
            if not crawl_ok:
                log(f, "crawl stored 0 products - NOT writing heartbeat; "
                       "CI will keep trying its own (likely blocked) attempt")
        except Exception as e:
            log(f, f"crawl: {type(e).__name__}: {e}")
        if crawl_ok:
            try:
                import psycopg
                conn = psycopg.connect(env["DATABASE_URL"], connect_timeout=15)
                now = datetime.now(timezone.utc).isoformat(timespec="seconds")
                conn.execute(
                    "INSERT INTO kv (k, v) VALUES ('chemistwarehouse_local_heartbeat', %s) "
                    "ON CONFLICT (k) DO UPDATE SET v = excluded.v", (now,))
                conn.commit()
                conn.close()
                log(f, f"heartbeat written: {now}")
            except Exception as e:
                log(f, f"heartbeat FAILED: {type(e).__name__}: {e}")
        log(f, "=== sweep end ===")


if __name__ == "__main__":
    main()
