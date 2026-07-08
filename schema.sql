-- Postgres schema (production). SQLite equivalent is created automatically by db.py.
CREATE TABLE IF NOT EXISTS products (
    id BIGSERIAL PRIMARY KEY,
    retailer TEXT NOT NULL,
    sku TEXT NOT NULL,
    gtin TEXT,
    title TEXT,
    brand TEXT,
    category TEXT,
    url TEXT,
    image_url TEXT,
    is_marketplace BOOLEAN DEFAULT FALSE,
    region TEXT DEFAULT '',              -- store/region-priced retailers (Bunnings, Harvey Norman)
    first_seen TIMESTAMPTZ DEFAULT now(),
    last_seen TIMESTAMPTZ DEFAULT now(),
    UNIQUE (retailer, sku, region)
);
CREATE TABLE IF NOT EXISTS price_snapshots (
    id BIGSERIAL PRIMARY KEY,
    product_id BIGINT NOT NULL REFERENCES products(id),
    price NUMERIC(10,2) NOT NULL,
    rrp NUMERIC(10,2),
    in_stock BOOLEAN,
    scraped_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_snap_product ON price_snapshots(product_id, scraped_at DESC);
CREATE INDEX IF NOT EXISTS idx_products_gtin ON products(gtin);
CREATE TABLE IF NOT EXISTS crawl_queue (
    retailer TEXT NOT NULL,
    url TEXT NOT NULL,
    last_scraped TIMESTAMPTZ,
    fails INTEGER DEFAULT 0,
    PRIMARY KEY (retailer, url)
);
CREATE TABLE IF NOT EXISTS kv (
    k TEXT PRIMARY KEY,
    v TEXT
);
CREATE TABLE IF NOT EXISTS deals (
    id BIGSERIAL PRIMARY KEY,
    product_id BIGINT NOT NULL REFERENCES products(id),
    price NUMERIC(10,2) NOT NULL,
    reference_price NUMERIC(10,2),
    signal TEXT,                         -- rrp_gap | history_drop | cross_retailer
    score NUMERIC(6,4),
    status TEXT DEFAULT 'new',           -- new | verified | expired
    detected_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (product_id, price, signal)
);
CREATE TABLE IF NOT EXISTS watches (
    id BIGSERIAL PRIMARY KEY,
    product_id BIGINT NOT NULL REFERENCES products(id),
    email TEXT NOT NULL,
    target_price NUMERIC(10,2) NOT NULL,
    token TEXT NOT NULL UNIQUE,          -- unguessable id, used for the unsubscribe link
    created_at TIMESTAMPTZ DEFAULT now(),
    fired_at TIMESTAMPTZ,                -- stamped once the alert email is sent
    cancelled_at TIMESTAMPTZ             -- stamped by the anon unsubscribe action
);
CREATE INDEX IF NOT EXISTS idx_watches_unfired ON watches(product_id)
    WHERE fired_at IS NULL AND cancelled_at IS NULL;

-- Base tables are never public: only the anon-readable views in views.sql
-- (plus the narrow watches INSERT policy + cancel_watch() RPC also defined
-- there) are reachable with the public anon key. The crawler/detector
-- connect with the postgres role, which bypasses RLS, so none of this
-- affects the backend.
--
-- RLS-enabled-with-no-policy denies DIRECT table access from anon/
-- authenticated regardless of GRANTs — but that is NOT sufficient on its
-- own: every table here (not just crawl_queue/kv) shipped with Supabase's
-- default full INSERT/UPDATE/DELETE/TRUNCATE grants for anon/authenticated,
-- and a simple, single-table anon-readable VIEW over a table (e.g.
-- product_search over products) is auto-updatable by Postgres — writes
-- pass straight through to the base table using the VIEW OWNER's
-- privileges (postgres, which has BYPASSRLS), completely bypassing the
-- base table's RLS. This was exploited during testing via product_search.
-- The fix has two parts: revoke the stray grants here on every base table,
-- AND revoke-then-grant-only-select on every view in views.sql (see the
-- comment at the top of that file).
ALTER TABLE products ENABLE ROW LEVEL SECURITY;
ALTER TABLE price_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE deals ENABLE ROW LEVEL SECURITY;
ALTER TABLE crawl_queue ENABLE ROW LEVEL SECURITY;
ALTER TABLE kv ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON products FROM anon, authenticated;
REVOKE ALL ON price_snapshots FROM anon, authenticated;
REVOKE ALL ON deals FROM anon, authenticated;
REVOKE ALL ON crawl_queue FROM anon, authenticated;
REVOKE ALL ON kv FROM anon, authenticated;
-- watches' RLS + anon policies are set up in views.sql, next to the
-- cancel_watch() RPC they depend on.
