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

21 July (P19 follow-up): Postgres's public port 5432 closed as part of the
network hardening, which broke this script along with every other
off-VCN client - it was silently timing out every run. Now opens a
private SSH tunnel to the DB (scripts/db_tunnel.py) before connecting;
DATABASE_URL from .env is rewritten to the tunnel endpoint for this
process only, never used directly. TUNNEL_SSH_KEY lets the same script
run unchanged on any machine (Windows PC, Ubuntu laptop) with its own
dedicated, forwarding-only key.
"""
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import db_tunnel  # noqa: E402

LOG = os.path.join(ROOT, "local_chemistwarehouse.log")
LOG_KEEP_LINES = 2000
CRAWL_BATCH = "400"   # ~10s+jitter delay floor -> ~70min/run, every 2h leaves margin
TUNNEL_KEY = os.environ.get(
    "TUNNEL_SSH_KEY",
    os.path.expanduser("~/.ssh/pricewatch_tunnel_local_ed25519"))


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
        try:
            with db_tunnel.open_db_tunnel(TUNNEL_KEY) as endpoint:
                env["DATABASE_URL"] = db_tunnel.tunneled_database_url(
                    env["DATABASE_URL"], endpoint)
                _run_sweep(env, f)
        except Exception as e:
            log(f, f"tunnel: {type(e).__name__}: {e} - sweep skipped")
        log(f, "=== sweep end ===")


def _run_sweep(env, f):
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


if __name__ == "__main__":
    main()
