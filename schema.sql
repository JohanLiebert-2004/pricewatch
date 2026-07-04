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
