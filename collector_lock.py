"""Session locks shared by scheduled PC collectors and their CI fallbacks."""
from contextlib import contextmanager
from datetime import datetime, timezone

LOCAL_RETAILERS = frozenset({'bigw', 'chemistwarehouse', 'jbhifi', 'sephora'})


class CollectorPaused(RuntimeError):
    """An operator pause prevents requests from every participating host."""


@contextmanager
def collector_lock(database_url, retailer):
    if not database_url or retailer not in LOCAL_RETAILERS:
        yield True
        return
    import psycopg
    conn = psycopg.connect(database_url, autocommit=True, connect_timeout=15,
                           application_name='dealwatch-collector-lock',
                           keepalives=1, keepalives_idle=30,
                           keepalives_interval=10, keepalives_count=3)
    try:
        conn.execute("SET statement_timeout = '15s'")
        pause = conn.execute("SELECT v FROM kv WHERE k=%s",
                             ('collector_pause_until:' + retailer,)).fetchone()
        if pause:
            until = datetime.fromisoformat(pause[0])
            if until > datetime.now(timezone.utc):
                raise CollectorPaused(f'{retailer}: collection paused until {until.isoformat()}; no retailer requests made')
        row = conn.execute("SELECT pg_try_advisory_lock(hashtext('dealwatch:collector'), hashtext(%s))",
                           (retailer,)).fetchone()
        yield bool(row and row[0])
    finally:
        # Closing the session releases its locks, including after an exception.
        conn.close()
