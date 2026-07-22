"""Home-IP JB Hi-Fi sweep - runs on the always-on Ubuntu laptop via
systemd --user, same pattern as local_bigw_sweep.py and
local_chemistwarehouse_sweep.py.

JB Hi-Fi's Shopify storefront (/products.json) started returning HTTP 429
on request #1 of every single hourly GitHub Actions run starting ~21 July
2026 (confirmed across multiple consecutive runs, all identical: blocked
immediately, zero listings). A single direct request from a residential IP
the same day returned a clean 200 with no rate limiting at all - same shape
of problem as Big W's Akamai block (datacenter/shared CI IP ranges get
throttled harder than a home connection), just manifesting as a 429 instead
of a JS challenge. Same fix: run the real refresh lane directly from home.

Coordination with CI (one crawler per retailer at a time, always):
- On a successful refresh this writes an ISO timestamp to the
  `jbhifi_local_heartbeat` kv row. The CI cadence gate in crawl.yml skips
  its own (currently-blocked) refresh attempt while that heartbeat is
  younger than 90 minutes, and automatically resumes trying if this
  machine stays off.

A full JB Hi-Fi sweep is ~100 requests at ~1.25s each (~2 minutes) - cheap
enough to run every 30 minutes, well inside the 90-minute gate threshold
even if one cycle is missed.

Logs append to local_jbhifi.log next to this file (gitignored,
self-trimmed). Uses scripts/db_tunnel.py exactly like the other two local
sweeps - Postgres's public port is closed, so every off-VCN client goes
through the private SSH forward on the web VM's ci-tunnel account.
"""
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import db_tunnel  # noqa: E402

LOG = os.path.join(ROOT, "local_jbhifi.log")
LOG_KEEP_LINES = 2000
REFRESH_BUDGET = "100"   # matches MAX_PAGES in scrapers/jbhifi.py - Shopify
                         # caps page*limit at 25,000 regardless of budget
TUNNEL_KEY = os.environ.get(
    "TUNNEL_SSH_KEY",
    os.path.expanduser("~/.ssh/pricewatch_tunnel_ubuntu_ed25519"))


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
        try:
            with db_tunnel.open_db_tunnel(TUNNEL_KEY) as endpoint:
                env["DATABASE_URL"] = db_tunnel.tunneled_database_url(
                    env["DATABASE_URL"], endpoint)
                _run_sweep(env, f)
        except Exception as e:
            log(f, f"tunnel: {type(e).__name__}: {e} - sweep skipped")
        log(f, "=== sweep end ===")


def _run_sweep(env, f):
    refresh_ok = False
    try:
        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, "run.py"),
             "refresh", "jbhifi", "--budget", REFRESH_BUDGET],
            cwd=ROOT, env=env, capture_output=True, text=True,
            timeout=1800)
        tail = (r.stdout + r.stderr).strip().splitlines()[-3:]
        log(f, f"refresh: exit {r.returncode} | " + " / ".join(tail))
        # Exit 0 alone is NOT success: a fully-blocked sweep also exits 0
        # ("BLOCKED mid-refresh (keeping what we got)"). JB Hi-Fi's block
        # is all-or-nothing on request #1 (unlike Big W's Akamai block,
        # which sometimes partially degrades), so any listings at all is a
        # clean signal the home IP got through.
        m = re.search(r"-> (\d+) listings seen", r.stdout)
        refresh_ok = bool(m) and int(m.group(1)) > 0
        if not refresh_ok:
            log(f, "refresh saw no listings - NOT writing heartbeat; "
                   "CI fallback attempt stays active")
    except Exception as e:
        log(f, f"refresh: {type(e).__name__}: {e}")
    if refresh_ok:
        try:
            import psycopg
            conn = psycopg.connect(env["DATABASE_URL"], connect_timeout=15)
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            conn.execute(
                "INSERT INTO kv (k, v) VALUES ('jbhifi_local_heartbeat', %s) "
                "ON CONFLICT (k) DO UPDATE SET v = excluded.v", (now,))
            conn.commit()
            conn.close()
            log(f, f"heartbeat written: {now}")
        except Exception as e:
            log(f, f"heartbeat FAILED: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
