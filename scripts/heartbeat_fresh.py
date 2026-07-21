"""Exit successfully when a crawler-owner heartbeat is recent.

Used by GitHub Actions cadence gates. Connection failures deliberately count
as stale so the fallback lane runs instead of silently skipping coverage.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import psycopg


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: heartbeat_fresh.py KEY MAX_AGE_SECONDS")
    key, max_age_raw = sys.argv[1:]
    try:
        max_age = timedelta(seconds=max(1, int(max_age_raw)))
        conn = psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=10)
        with conn:
            row = conn.execute("SELECT v FROM kv WHERE k=%s", (key,)).fetchone()
        heartbeat = datetime.fromisoformat(row[0]) if row else None
        return 0 if heartbeat and datetime.now(timezone.utc) - heartbeat < max_age else 1
    except Exception as exc:
        print(f"heartbeat check unavailable: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
