"""Refresh public website feeds without invoking crawlers or notifications."""
import time


PUBLIC_VIEWS = (
    "retailer_freshness", "catalogue_stats", "discount_feed",
    "growth_daily", "subcategory_stats",
)


def refresh_public_views(conn):
    """Publish each view independently; report failures after trying them all."""
    failed = []
    for view in PUBLIC_VIEWS:
        started = time.monotonic()
        try:
            # Production feed refreshes have exceeded the ordinary query
            # timeout. Keep this higher allowance local to each transaction.
            conn.execute("SET LOCAL statement_timeout = '180s'")
            conn.execute(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {view}")
            conn.commit()
            print(f"Published {view} in {time.monotonic() - started:.1f}s", flush=True)
        except Exception as exc:
            conn.rollback()
            failed.append(view)
            print(f"Publication failed for {view}: {type(exc).__name__}: {exc}", flush=True)
    if failed:
        raise RuntimeError("Website publication failed: " + ", ".join(failed))
