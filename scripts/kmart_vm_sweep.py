"""VM-side Kmart-only sweep - runs on the OCI web VM (159.13.59.184) via
systemd timer, separately from the GitHub Actions crawl matrix.

Kmart's CI refresh (`python run.py refresh kmart --budget 1400`) has been
silently crashing every run since ~19 July: `crawl.yml` runs all 13
retailers' refresh/crawl lanes as concurrent GH Actions jobs, each opening
its own connection to the tiny fallback DB VM (pricewatch-db-x86,
1 OCPU/1GB). Its Postgres log shows checkpoints taking 100s of seconds under
that concurrent write load; Kmart's refresh (the largest single write
volume of any retailer - Constructor returns multiple merchandising-group
rows per product) is the one that reliably outlives its connection during a
stall (`psycopg.OperationalError: consuming input failed: SSL error:
unexpected eof while reading` / "the connection is lost", inside
db.bulk_upsert). The `|| true` on that workflow step swallows the crash, so
CI has shown green while writing nothing for 2+ days.

This script runs the same refresh, alone, from a separate VM, off the GH
Actions matrix's concurrent-write burst - removing Kmart's transaction from
the contention that's been killing it, without needing to touch the other
12 retailers' behaviour. Talks to the DB over the shared OCI subnet's
private IP (10.42.1.9), not the public one.

Coordination with CI (mirrors local_bigw_sweep.py / local_chemistwarehouse_
sweep.py): on a real success this writes an ISO timestamp to the
`kmart_vm_heartbeat` kv row. The CI cadence gate in crawl.yml skips its own
(currently-crashing) refresh attempt while that heartbeat is younger than
90 minutes (this sweep runs hourly), and resumes trying if the VM sweep
ever stops writing it.

Systemd units (see infra/oci install notes / AGENT_STATE.md):
  /etc/systemd/system/pricewatch-kmart.service (oneshot)
  /etc/systemd/system/pricewatch-kmart.timer (hourly, :20)
Env: /opt/pricewatch.env (TELEGRAM_*) + /etc/pricewatch-kmart.env
     (DATABASE_URL override, points at the DB VM's private IP - the shared
     /opt/pricewatch.env's DATABASE_URL is intentionally left alone, it
     still backs the not-yet-migrated pricewatch-bot/embed services).
Logs append to /var/log/pricewatch-kmart.log (self-trimmed).
"""
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

ROOT = "/opt/pricewatch"
LOG = "/var/log/pricewatch-kmart.log"
LOG_KEEP_LINES = 2000
BUDGET = "1400"
TIMEOUT_S = 1800   # bulk API feed, not a per-item delay lane - should
                    # finish in minutes when the connection survives


def log(f, msg):
    f.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}\n")
    f.flush()


def main():
    if os.path.exists(LOG):
        lines = open(LOG, encoding="utf-8", errors="replace").readlines()
        if len(lines) > LOG_KEEP_LINES:
            open(LOG, "w", encoding="utf-8").writelines(lines[-LOG_KEEP_LINES:])
    with open(LOG, "a", encoding="utf-8") as f:
        log(f, "=== sweep start ===")
        refresh_ok = False
        try:
            r = subprocess.run(
                [sys.executable, os.path.join(ROOT, "run.py"),
                 "refresh", "kmart", "--budget", BUDGET],
                cwd=ROOT, capture_output=True, text=True, timeout=TIMEOUT_S)
            tail = (r.stdout + r.stderr).strip().splitlines()[-5:]
            log(f, f"refresh: exit {r.returncode} | " + " / ".join(tail))
            m = re.search(r"-> (\d+) listings seen, (\d+) kept", r.stdout)
            refresh_ok = r.returncode == 0 and bool(m) and int(m.group(1)) > 0
            if not refresh_ok:
                log(f, "refresh did not complete cleanly - NOT writing "
                       "heartbeat; CI will keep trying its own attempt")
        except Exception as e:
            log(f, f"refresh: {type(e).__name__}: {e}")
        if refresh_ok:
            try:
                import psycopg
                conn = psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=15)
                now = datetime.now(timezone.utc).isoformat(timespec="seconds")
                conn.execute(
                    "INSERT INTO kv (k, v) VALUES ('kmart_vm_heartbeat', %s) "
                    "ON CONFLICT (k) DO UPDATE SET v = excluded.v", (now,))
                conn.commit()
                conn.close()
                log(f, f"heartbeat written: {now}")
            except Exception as e:
                log(f, f"heartbeat FAILED: {type(e).__name__}: {e}")
        log(f, "=== sweep end ===")


if __name__ == "__main__":
    main()
