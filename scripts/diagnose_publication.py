"""Read-only operational diagnostics; never log credentials or query text."""
import json
import os

import psycopg
from psycopg.rows import dict_row


with psycopg.connect(os.environ['DATABASE_URL'], connect_timeout=20,
                     application_name='dealwatch-diagnostics', autocommit=True,
                     row_factory=dict_row) as conn:
    conn.execute("SET statement_timeout = '15s'")
    conn.execute("SET default_transaction_read_only = on")
    checks = {
        'sessions': """SELECT usename, state, wait_event_type, wait_event, count(*)
                       FROM pg_stat_activity WHERE datname=current_database()
                       GROUP BY 1,2,3,4""",
        'active': """SELECT pid, usename, application_name, state, wait_event_type,
                     wait_event, clock_timestamp()-query_start AS age,
                     pg_blocking_pids(pid) AS blockers
                     FROM pg_stat_activity WHERE datname=current_database()
                     AND pid<>pg_backend_pid() AND state <> 'idle'
                     ORDER BY query_start LIMIT 40""",
        'tables': """SELECT relname,n_live_tup,n_dead_tup,last_autovacuum,last_autoanalyze
                     FROM pg_stat_user_tables WHERE relname IN
                     ('products','price_snapshots','deals')""",
        'view_sizes': """SELECT c.relname,pg_size_pretty(pg_total_relation_size(c.oid)) AS size
                         FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                         WHERE n.nspname='public' AND c.relname IN
                         ('products','price_snapshots','retailer_freshness','discount_feed')""",
    }
    for name, sql in checks.items():
        try:
            print(name, json.dumps(conn.execute(sql).fetchall(), default=str), flush=True)
        except psycopg.Error as exc:
            print(name, type(exc).__name__, flush=True)
