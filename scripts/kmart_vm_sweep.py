"""VM-side Kmart-only sweep, run by systemd on the OCI web role.

This is the primary Kmart refresh lane. GitHub Actions checks the
`kmart_vm_heartbeat` row and runs its own refresh only when this lane is
stale. The workflow no longer masks refresh failures.

DATABASE_URL is supplied by `/etc/pricewatch-kmart.env` and points to the
database role over the OCI private network; no address is embedded here.

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
TIMEOUT_S = 5400   # matches the ~90min headroom CI's old 110min job timeout
                    # gave this same budget - the original 1800s guess
                    # (bulk API, assumed ~1s/request) was wrong in practice:
                    # real per-request latency from this VM made every run
                    # hit that timeout before reaching the success line,
                    # so it kept writing real partial progress but never a
                    # heartbeat, silently defeating the CI cadence gate.


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
        except subprocess.TimeoutExpired as e:
            # e.stdout/e.stderr hold whatever the process had printed before
            # being killed - real progress (bulk_upsert commits as it goes)
            # even though this run itself won't count as a clean success.
            partial = ((e.stdout or "") + (e.stderr or "")).strip().splitlines()[-5:]
            log(f, f"refresh: TimeoutExpired after {TIMEOUT_S}s | " + " / ".join(partial))
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
