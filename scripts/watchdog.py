"""Independent external health watchdog - runs on the Ubuntu laptop, outside
both OCI VMs and outside GitHub Actions entirely.

health_alerts.py already covers per-retailer staleness, but it runs *inside*
the CI pipeline (the detect job) - which means it structurally cannot detect
"CI itself has stopped running" (confirmed live this session: P19's port
closure broke both local sweeps silently for hours, and Kmart's CI lane
failed silently for 2+ days before anyone noticed). This script checks the
things nothing else can see from the inside:

1. GitHub Actions itself is still triggering and completing runs.
2. Each local-sweep heartbeat (Kmart VM, Big W, Chemist Warehouse) is fresh
   within its own expected cadence.
3. Both OCI VMs are still reachable over SSH.

State is a local JSON file, not the production DB - this is monitoring,
not application data, and must not depend on the thing it's checking.
Sends one Telegram alert on OK->BAD transitions and one recovery notice on
BAD->OK, never repeats for an unchanged state (same debounce shape as
health_alerts.py's kv-based approach, just local instead of DB-backed).
"""
import argparse
import json
import os
import socket
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import db_tunnel  # noqa: E402

STATE_DIR = os.path.expanduser(os.environ.get('PRICEWATCH_STATE_DIR', '~/.local/state/pricewatch-runtime'))
LOG = os.path.join(STATE_DIR, "watchdog.log")
STATE_FILE = os.path.join(STATE_DIR, "watchdog-state.json")
LOG_KEEP_LINES = 2000
TUNNEL_KEY = os.environ.get(
    "TUNNEL_SSH_KEY", os.path.expanduser("~/.ssh/pricewatch_tunnel_ubuntu_ed25519"))

REPO = "JohanLiebert-2004/pricewatch"
GH_STALE_AFTER = timedelta(hours=2)          # crawl.yml runs hourly
HEARTBEATS = {
    "kmart_vm_heartbeat": timedelta(minutes=150),      # hourly-ish timer, generous
    "bigw_local_heartbeat": timedelta(hours=30),        # 3h timer, 24h CI-gate window + slack
    "chemistwarehouse_local_heartbeat": timedelta(hours=8),  # 2h timer, 6h CI-gate window + slack
    "jbhifi_local_heartbeat": timedelta(minutes=150),   # 30min timer, 90min CI-gate window + slack
    "sephora_local_heartbeat": timedelta(minutes=150),
}
OCI_HOSTS = {"web VM (159.13.59.184)": "159.13.59.184",
             "DB VM (192.9.163.208)": "192.9.163.208"}


def log(f, msg):
    f.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}\n")
    f.flush()


def _load_state():
    if os.path.exists(STATE_FILE):
        try:
            return json.load(open(STATE_FILE))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_state(state):
    temporary = STATE_FILE + '.tmp'
    with open(temporary, 'w') as stream:
        json.dump(state, stream)
    os.chmod(temporary, 0o600)
    os.replace(temporary, STATE_FILE)


def _send_telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    try:
        resp = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage",
                         json={"chat_id": chat_id, "text": text}, timeout=15)
        return resp.status_code == 200 and bool(resp.json().get("ok"))
    except (httpx.HTTPError, ValueError):
        # Exception text may contain the bot credential in its request URL.
        return False


def check_github_actions(f):
    try:
        r = httpx.get(f"https://api.github.com/repos/{REPO}/actions/workflows/crawl.yml/runs",
                      params={"per_page": 5}, timeout=15)
        r.raise_for_status()
        runs = r.json().get("workflow_runs", [])
        if not runs:
            return "no workflow runs found at all"
        latest = runs[0]
        created = datetime.fromisoformat(latest["created_at"].replace("Z", "+00:00"))
        age = datetime.now(timezone.utc) - created
        if age > GH_STALE_AFTER:
            return f"latest workflow run is {age} old (expected hourly)"
        recent_conclusions = [r["conclusion"] for r in runs if r["conclusion"]]
        if recent_conclusions and all(c not in ("success", "cancelled") for c in recent_conclusions):
            return f"last {len(recent_conclusions)} runs all failed: {recent_conclusions}"
        return None
    except Exception as e:
        log(f, f"  github check error: {type(e).__name__}: {e}")
        return f"could not check GitHub Actions: {type(e).__name__}"


def check_heartbeats(f):
    problems = []
    try:
        env = dict(os.environ)
        for line in open(os.path.join(ROOT, ".env"), encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k, v)
        with db_tunnel.open_db_tunnel(TUNNEL_KEY) as endpoint:
            import psycopg
            database_url = db_tunnel.tunneled_database_url(env["DATABASE_URL"], endpoint)
            conn = psycopg.connect(database_url, connect_timeout=15)
            for key, max_age in HEARTBEATS.items():
                row = conn.execute("SELECT v FROM kv WHERE k=%s", (key,)).fetchone()
                if not row:
                    problems.append(f"{key}: never recorded")
                    continue
                ts = datetime.fromisoformat(row[0])
                age = datetime.now(timezone.utc) - ts
                if age > max_age:
                    problems.append(f"{key}: {age} old (limit {max_age})")
            conn.close()
    except Exception as e:
        log(f, f"  heartbeat check error: {type(e).__name__}: {e}")
        problems.append(f"could not reach the database: {type(e).__name__}")
    return problems


def check_oci_hosts(f):
    problems = []
    for label, host in OCI_HOSTS.items():
        try:
            with socket.create_connection((host, 22), timeout=8):
                pass
        except OSError as e:
            problems.append(f"{label} unreachable on SSH: {e}")
    return problems


def check_website(f):
    problems = []
    for label, path in [('website', '/'),
                        ('catalogue API', '/rest/v1/product_search?select=sku&retailer=eq.kmart&limit=1')]:
        try:
            response = httpx.get('https://dealwatch.com.au' + path, timeout=20,
                                 headers={'User-Agent': 'Dealwatch-Monitor/1.0'})
            response.raise_for_status()
            if label == 'catalogue API' and not isinstance(response.json(), list):
                problems.append('catalogue API returned an invalid response')
        except Exception as exc:
            problems.append(f'{label} unavailable: {type(exc).__name__}')
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-only', action='store_true',
                        help='report real health without notifications or notification-state changes')
    args = parser.parse_args(argv)
    os.umask(0o077)
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    if os.path.exists(LOG):
        lines = open(LOG, encoding="utf-8", errors="replace").readlines()
        if len(lines) > LOG_KEEP_LINES:
            open(LOG, "w", encoding="utf-8").writelines(lines[-LOG_KEEP_LINES:])
    with open(LOG, "a", encoding="utf-8") as f:
        log(f, "=== watchdog check start ===")
        problems = []
        gh_problem = check_github_actions(f)
        if gh_problem:
            problems.append(f"GitHub Actions: {gh_problem}")
        problems += [f"Heartbeat: {p}" for p in check_heartbeats(f)]
        problems += [f"OCI: {p}" for p in check_oci_hosts(f)]
        problems += [f"Public service: {p}" for p in check_website(f)]

        if args.check_only:
            print(json.dumps({'healthy': not bool(problems), 'problems': problems,
                              'notification_configured': bool(os.environ.get('TELEGRAM_BOT_TOKEN') and
                                                              os.environ.get('TELEGRAM_CHAT_ID')),
                              'notifications_sent': False,
                              'checked_at': datetime.now(timezone.utc).isoformat()}))
            return 0

        state = _load_state()
        notified_bad = state.get('notified_bad', False)
        is_bad = bool(problems)

        if is_bad:
            log(f, "PROBLEMS: " + " | ".join(problems))
        else:
            log(f, "all checks OK")

        if is_bad and not notified_bad:
            sent = _send_telegram("Dealwatch watchdog: problem detected\n\n" + "\n".join(problems))
            notified_bad = bool(sent)
            log(f, 'alert sent (OK -> BAD)' if sent else 'alert not delivered; will retry next check')
        elif not is_bad and notified_bad:
            sent = _send_telegram("Dealwatch watchdog: recovered, all checks OK now")
            if sent:
                notified_bad = False
            log(f, 'recovery notice sent (BAD -> OK)' if sent else 'recovery notice not delivered; will retry')

        _save_state({"bad": is_bad, "problems": problems,
                     "notified_bad": notified_bad,
                     "checked_at": datetime.now(timezone.utc).isoformat()})
        log(f, "=== watchdog check end ===")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
