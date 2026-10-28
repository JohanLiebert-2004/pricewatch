"""Scheduled direct-connection Sephora AU Firefox catalogue refresh."""
import os
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'scripts'))
import db_tunnel  # noqa: E402

LOG = ROOT / 'local_sephora.log'
# A one-page first verification can be selected without changing the normal
# two-page schedule. Never let an environment override remove this bound.
REFRESH_BUDGET = '1' if os.environ.get('SEPHORA_REFRESH_BUDGET') == '1' else '2'
TUNNEL_KEY = os.environ.get(
    'TUNNEL_SSH_KEY', os.path.expanduser('~/.ssh/pricewatch_tunnel_ubuntu_ed25519'))


def log(stream, message):
    stream.write(f'{datetime.now(timezone.utc).isoformat(timespec="seconds")} {message}\n')
    stream.flush()


def _run_sweep(env, stream):
    import psycopg
    started = datetime.now(timezone.utc).isoformat(timespec='seconds')
    with psycopg.connect(env['DATABASE_URL'], connect_timeout=15) as conn:
        conn.execute("INSERT INTO kv (k,v) VALUES ('sephora_local_running',%s) "
                     "ON CONFLICT (k) DO UPDATE SET v=excluded.v", (started,))
    success = False
    try:
        result = subprocess.run(
            [sys.executable, str(ROOT / 'run.py'), 'refresh', 'sephora',
             '--budget', REFRESH_BUDGET], cwd=ROOT, env=env,
            capture_output=True, text=True, timeout=3600)
        log(stream, f'refresh: exit {result.returncode}')
        for line in result.stdout.strip().splitlines()[-8:]:
            log(stream, line)
        count = re.search(r'-> (\d+) listings seen', result.stdout)
        complete = re.search(r'Sephora catalogue window complete: (\d+) pages', result.stdout)
        success = (result.returncode == 0 and bool(count) and int(count[1]) > 0
                   and bool(complete) and int(complete[1]) > 0)
        if success:
            with psycopg.connect(env['DATABASE_URL'], connect_timeout=15) as conn:
                now = datetime.now(timezone.utc).isoformat(timespec='seconds')
                conn.execute("INSERT INTO kv (k,v) VALUES ('sephora_local_heartbeat',%s) "
                             "ON CONFLICT (k) DO UPDATE SET v=excluded.v", (now,))
            log(stream, 'catalogue window and cursor committed; heartbeat written')
        else:
            log(stream, 'refresh incomplete; success heartbeat unchanged')
    except Exception as exc:
        log(stream, f'refresh failed: {type(exc).__name__}')
        success = False
    finally:
        with psycopg.connect(env['DATABASE_URL'], connect_timeout=15) as conn:
            conn.execute("DELETE FROM kv WHERE k='sephora_local_running' AND v=%s", (started,))
    return success


def main():
    import fcntl  # Ubuntu laptop; keep platform-specific locking out of imports.
    with (ROOT / 'local_sephora.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Sephora collection is already running')
        env = dict(os.environ)
        for line in (ROOT / '.env').read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                env.setdefault(key, value)
        for key in ('PROXY_URL', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY',
                    'http_proxy', 'https_proxy', 'all_proxy'):
            env.pop(key, None)
        env['SEPHORA_BROWSER'] = 'firefox'
        if LOG.exists():
            lines = LOG.read_text(errors='replace').splitlines()
            if len(lines) > 2000:
                LOG.write_text('\n'.join(lines[-2000:]) + '\n')
        success = False
        with LOG.open('a') as stream:
            log(stream, '=== sweep start ===')
            try:
                with db_tunnel.open_db_tunnel(TUNNEL_KEY) as endpoint:
                    env['DATABASE_URL'] = db_tunnel.tunneled_database_url(env['DATABASE_URL'], endpoint)
                    success = _run_sweep(env, stream)
            except Exception as exc:
                log(stream, f'sweep failed: {type(exc).__name__}')
            log(stream, '=== sweep end ===')
        if not success:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
