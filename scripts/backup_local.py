"""Daily PostgreSQL backup to the Ubuntu laptop's local disk.

The only existing backup (scripts/backup_postgres_to_oci.sh) is a daily
pg_dump landing in OCI Object Storage - genuinely useful, but it lives in
the *same* OCI tenancy as the live database. An account/billing/tenancy-
level problem takes out the live DB and its only backup together. This
script is a second, independent copy on hardware outside OCI entirely,
reached the same way the sweeps reach the DB now that public 5432 is
closed: a private SSH tunnel through the web VM's restricted ci-tunnel
account (scripts/db_tunnel.py).

Retention mirrors the existing OCI lifecycle policy: 30 days.
Logs append to backup_local.log next to this file (gitignored, self-trimmed).
"""
import hashlib
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import db_tunnel  # noqa: E402

BACKUP_DIR = os.path.expanduser("~/pricewatch-backups")
LOG = os.path.join(ROOT, "backup_local.log")
LOG_KEEP_LINES = 2000
RETENTION_DAYS = 30
TUNNEL_KEY = os.environ.get(
    "TUNNEL_SSH_KEY", os.path.expanduser("~/.ssh/pricewatch_tunnel_ubuntu_ed25519"))


def log(f, msg):
    f.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}\n")
    f.flush()


def _env_file(path):
    env = dict(os.environ)
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env.setdefault(k, v)
    return env


def _prune_old(f):
    if not os.path.isdir(BACKUP_DIR):
        return
    cutoff = time.time() - RETENTION_DAYS * 86400
    for name in os.listdir(BACKUP_DIR):
        path = os.path.join(BACKUP_DIR, name)
        if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
            os.remove(path)
            log(f, f"pruned old backup: {name}")


def main():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    if os.path.exists(LOG):
        lines = open(LOG, encoding="utf-8", errors="replace").readlines()
        if len(lines) > LOG_KEEP_LINES:
            open(LOG, "w", encoding="utf-8").writelines(lines[-LOG_KEEP_LINES:])
    with open(LOG, "a", encoding="utf-8") as f:
        log(f, "=== backup start ===")
        try:
            env = _env_file(os.path.join(ROOT, ".env"))
            with db_tunnel.open_db_tunnel(TUNNEL_KEY) as endpoint:
                database_url = db_tunnel.tunneled_database_url(env["DATABASE_URL"], endpoint)
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                dump_file = os.path.join(BACKUP_DIR, f"pricewatch-{stamp}.dump")
                r = subprocess.run(
                    ["pg_dump", "--dbname", database_url, "--format=custom",
                     "--no-owner", "--no-privileges", "--file", dump_file],
                    capture_output=True, text=True, timeout=1800)
                if r.returncode != 0:
                    log(f, f"pg_dump failed: {r.stderr.strip()[-500:]}")
                    return
                size = os.path.getsize(dump_file)
                checksum = hashlib.sha256(open(dump_file, "rb").read()).hexdigest()
                with open(dump_file + ".sha256", "w") as cf:
                    cf.write(f"{checksum}  {os.path.basename(dump_file)}\n")
                log(f, f"backup OK: {os.path.basename(dump_file)} "
                       f"({size/1_048_576:.1f} MB)")
            _prune_old(f)
        except Exception as e:
            log(f, f"backup FAILED: {type(e).__name__}: {e}")
        log(f, "=== backup end ===")


if __name__ == "__main__":
    main()
