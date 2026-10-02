"""Install the small API view that supplies hourly homepage deal rotation."""
import os
from urllib.parse import urlparse

import psycopg
from psycopg.rows import dict_row


dsn = os.environ.get("DATABASE_URL", "")
host = urlparse(dsn).hostname or ""
if not host or host.endswith((".supabase.com", ".supabase.co")):
    raise SystemExit("Refusing to deploy the retired database")

with psycopg.connect(dsn, connect_timeout=20, row_factory=dict_row) as conn:
    conn.execute("SET statement_timeout = '30s'")
    if not conn.execute("SELECT to_regclass('public.discount_feed')").fetchone()["to_regclass"]:
        raise SystemExit("Current discount feed is missing")
    conn.execute("""CREATE OR REPLACE VIEW public.hourly_deal_feed AS
        SELECT d.*,
               CASE WHEN d.category = 'books' THEN 1 ELSE 0 END AS book_priority,
               md5(floor(extract(epoch FROM statement_timestamp()) / 3600)::bigint::text
                   || ':' || d.retailer || ':' || d.sku) AS rotation_key
        FROM public.discount_feed d""")
    conn.execute("REVOKE ALL ON public.hourly_deal_feed FROM PUBLIC, anon, authenticated")
    conn.execute("GRANT SELECT ON public.hourly_deal_feed TO anon")
    conn.execute("NOTIFY pgrst, 'reload schema'")
    conn.commit()

    rows = conn.execute("""SELECT retailer, sku, category, book_priority,
                       rotation_key,
                       floor(extract(epoch FROM statement_timestamp()) / 3600)::bigint AS hour_bucket
                       FROM public.hourly_deal_feed
                       WHERE price_updated_at::timestamptz >= now() - interval '7 days'
                       ORDER BY book_priority, rotation_key, retailer, sku
                       LIMIT 100""").fetchall()
    if rows and any(row["book_priority"] for row in rows):
        raise SystemExit("Books reached the first 100 positions; check category tagging")
    if not rows:
        raise SystemExit("No recent deals in the hourly view")
    print(f"Installed hourly rotation for bucket {rows[0]['hour_bucket']}; "
          f"checked {len(rows)} rows; books are below the first 100.", flush=True)
