"""Storage layer. Uses SQLite locally; schema mirrors schema.sql (Postgres)."""
import os
import random
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "pricewatch.db"
# If DATABASE_URL is set (e.g. Supabase Postgres), use it; else local SQLite.
DATABASE_URL = os.environ.get("DATABASE_URL")
APPLY_SCHEMA_ON_CONNECT = os.environ.get("APPLY_SCHEMA_ON_CONNECT") == "1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    retailer TEXT NOT NULL,
    sku TEXT NOT NULL,
    gtin TEXT,
    title TEXT,
    brand TEXT,
    category TEXT,
    subcategory TEXT,
    url TEXT,
    image_url TEXT,
    is_marketplace INTEGER DEFAULT 0,
    region TEXT,
    first_seen TEXT,
    last_seen TEXT,
    current_price REAL,
    current_rrp REAL,
    price_updated_at TEXT,
    UNIQUE (retailer, sku, region)
);
CREATE TABLE IF NOT EXISTS kv (
    k TEXT PRIMARY KEY,
    v TEXT
);
CREATE TABLE IF NOT EXISTS price_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id),
    price REAL NOT NULL,
    rrp REAL,
    in_stock INTEGER,
    scraped_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snap_product ON price_snapshots(product_id, scraped_at);
CREATE INDEX IF NOT EXISTS idx_products_gtin ON products(gtin);
CREATE TABLE IF NOT EXISTS crawl_queue (
    retailer TEXT NOT NULL,
    url TEXT NOT NULL,
    last_scraped TEXT,
    fails INTEGER DEFAULT 0,
    PRIMARY KEY (retailer, url)
);
CREATE TABLE IF NOT EXISTS deals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id),
    price REAL NOT NULL,
    reference_price REAL,
    signal TEXT,
    score REAL,
    status TEXT DEFAULT 'new',
    detected_at TEXT NOT NULL,
    UNIQUE (product_id, price, signal)
);
CREATE TABLE IF NOT EXISTS watches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id),
    email TEXT NOT NULL,
    target_price REAL NOT NULL,
    token TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    confirmed_at TEXT,
    confirmation_sent_at TEXT,
    fired_at TEXT,
    cancelled_at TEXT
);
CREATE TABLE IF NOT EXISTS store_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id),
    suburb TEXT NOT NULL,
    price REAL NOT NULL,
    reported_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    reviewed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_store_reports_review
    ON store_reports(product_id, status, reported_at);
"""


@dataclass
class ProductRecord:
    retailer: str
    sku: str
    title: str
    url: str
    price: float | None
    rrp: float | None = None
    gtin: str | None = None
    brand: str | None = None
    category: str | None = None
    subcategory: str | None = None   # retailer-native category (per-store chips)
    image_url: str | None = None
    in_stock: bool | None = None
    is_marketplace: bool = False
    region: str | None = None


def connect(path: Path = DB_PATH):
    """Return a DB connection. Postgres if DATABASE_URL is set, else SQLite.

    The rest of the code uses a small compatibility shim so the same SQL
    (with ? placeholders and ON CONFLICT) runs on both.
    """
    if DATABASE_URL:
        return _connect_postgres()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn):
    """Add columns introduced after the first deploy; no-op once applied.

    Checks for the column FIRST: a bare ALTER TABLE takes an ACCESS EXCLUSIVE
    lock even when it is going to fail, which deadlocks the parallel
    per-retailer refresh jobs that connect at the same time.
    """
    if DATABASE_URL:
        have = {r["column_name"] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='products'").fetchall()}
    else:
        have = {r["name"] for r in conn.execute(
            "PRAGMA table_info(products)").fetchall()}
    for col, typ in (("current_price", "REAL"), ("current_rrp", "REAL"),
                     ("price_updated_at", "TEXT"), ("image_url", "TEXT"),
                     ("subcategory", "TEXT")):
        if col in have:
            continue
        try:
            conn.execute(f"ALTER TABLE products ADD COLUMN {col} {typ}")
            conn.commit()
        except Exception:
            conn.rollback()
    if DATABASE_URL:
        have_deals = {r["column_name"] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='deals'").fetchall()}
    else:
        have_deals = {r["name"] for r in conn.execute(
            "PRAGMA table_info(deals)").fetchall()}
    if "alerted_at" not in have_deals:
        try:
            conn.execute("ALTER TABLE deals ADD COLUMN alerted_at TEXT")
            conn.commit()
        except Exception:
            conn.rollback()
    if DATABASE_URL:
        have_watches = {r["column_name"] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='watches'").fetchall()}
        watch_type = "TIMESTAMPTZ"
    else:
        have_watches = {r["name"] for r in conn.execute(
            "PRAGMA table_info(watches)").fetchall()}
        watch_type = "TEXT"
    # Existing watches predate confirmation; preserve their opted-in status.
    added_confirmation = "confirmed_at" not in have_watches
    for col in ("confirmed_at", "confirmation_sent_at"):
        if col in have_watches:
            continue
        try:
            conn.execute(f"ALTER TABLE watches ADD COLUMN {col} {watch_type}")
            conn.commit()
        except Exception:
            conn.rollback()
    if added_confirmation:
        try:
            conn.execute("UPDATE watches SET confirmed_at=created_at "
                         "WHERE confirmed_at IS NULL")
            conn.commit()
        except Exception:
            conn.rollback()
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_watches_confirmed_unfired "
                     "ON watches(product_id) WHERE confirmed_at IS NOT NULL "
                     "AND fired_at IS NULL AND cancelled_at IS NULL")
        conn.commit()
    except Exception:
        conn.rollback()


def _connect_postgres():
    import psycopg
    from psycopg.rows import dict_row
    # prepare_threshold=None: Supabase's pooler (port 6543) is PgBouncer in
    # transaction mode, which can't guarantee a server-side prepared statement
    # survives to the next call on the same client connection.
    conn = psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False,
                            prepare_threshold=None)
    shim = _PgShim(conn)
    if APPLY_SCHEMA_ON_CONNECT:
        # Schema setup takes table locks. Keep it out of routine parallel
        # crawler/detect jobs; run it deliberately with APPLY_SCHEMA_ON_CONNECT=1.
        pg_schema = (SCHEMA
                     .replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
                     .replace("INTEGER", "BIGINT"))
        with conn.cursor() as cur:
            cur.execute(pg_schema)
        conn.commit()
        _migrate(shim)
    return shim


def upsert(conn: sqlite3.Connection, rec: ProductRecord) -> int | None:
    """Insert/refresh product and append a price snapshot. Returns product id."""
    if rec.price is None or rec.price <= 0:
        return None      # $0 = sold-out placeholder, not a price
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    # Postgres' pooler negotiates parameter types from the unnamed portal and
    # sometimes guesses booleans as smallint; an explicit cast sidesteps that.
    # SQLite doesn't understand "::type" casts, so only apply it under Postgres.
    bool_cast = "::boolean" if DATABASE_URL else ""
    conn.execute(
        f"""INSERT INTO products (retailer, sku, gtin, title, brand, category,
                                 subcategory, url,
                                 image_url, is_marketplace, region, first_seen,
                                 last_seen, current_price, current_rrp,
                                 price_updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?{bool_cast},?,?,?,?,?,?)
           ON CONFLICT(retailer, sku, region) DO UPDATE SET
             gtin=COALESCE(excluded.gtin, products.gtin), title=excluded.title,
             brand=COALESCE(excluded.brand, products.brand), url=excluded.url,
             subcategory=COALESCE(excluded.subcategory, products.subcategory),
             image_url=COALESCE(excluded.image_url, products.image_url),
             is_marketplace=excluded.is_marketplace{bool_cast}, last_seen=excluded.last_seen,
             current_price=excluded.current_price, current_rrp=excluded.current_rrp,
             price_updated_at=excluded.price_updated_at""",
        (rec.retailer, rec.sku, rec.gtin, rec.title, rec.brand, rec.category,
         rec.subcategory,
         rec.url, rec.image_url, rec.is_marketplace, rec.region or "", now, now,
         rec.price, rec.rrp, now),
    )
    row = conn.execute(
        "SELECT id FROM products WHERE retailer=? AND sku=? AND region=?",
        (rec.retailer, rec.sku, rec.region or ""),
    ).fetchone()
    pid = row["id"]
    conn.execute(
        f"INSERT INTO price_snapshots (product_id, price, rrp, in_stock, scraped_at) "
        f"VALUES (?,?,?,?{bool_cast},?)",
        (pid, rec.price, rec.rrp, rec.in_stock, now),
    )
    conn.commit()
    return pid


def bulk_upsert(conn, recs: list) -> list:
    """Upsert many ProductRecords with few round trips (for listing refreshes).

    Snapshots are only written when a product's price actually changed (or on
    first sighting), so frequent refreshes don't bloat price_snapshots.
    Returns the ProductRecords that actually got a new snapshot written
    (callers wanting just a count can use len() on the result).
    """
    recs = [r for r in recs if r.price is not None and r.price > 0]
    if not recs:
        return []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    changed = []
    for i in range(0, len(recs), 400):
        chunk = recs[i:i + 400]
        # de-dupe within the chunk (same sku can appear in two listings)
        seen = {}
        for r in chunk:
            seen[(r.retailer, r.sku, r.region or "")] = r
        # deterministic ordering = consistent lock order across the four
        # parallel retailer jobs writing to the same tables
        chunk = [seen[k] for k in sorted(seen)]
        for attempt in range(3):
            try:
                changed += _upsert_chunk(conn, chunk, now)
                break
            except Exception:
                conn.rollback()
                if attempt == 2:
                    raise
                time.sleep(random.uniform(0.5, 2.0) * (attempt + 1))
    return changed


def _upsert_chunk(conn, chunk, now) -> list:
    """One transaction: upsert products, snapshot only real price changes."""
    if DATABASE_URL:
        return _upsert_chunk_pg(conn, chunk, now)
    bool_cast = "::boolean" if DATABASE_URL else ""
    # what do we currently know about these products?
    old = {}
    for retailer in {r.retailer for r in chunk}:
        skus = [r.sku for r in chunk if r.retailer == retailer]
        for row in conn.execute(
                f"SELECT id, retailer, sku, region, current_price "
                f"FROM products WHERE retailer=? "
                f"AND sku IN ({','.join('?' * len(skus))})",
                (retailer, *skus)).fetchall():
            old[(row["retailer"], row["sku"], row["region"])] = (
                row["id"], row["current_price"])

    conn.executemany(
        f"""INSERT INTO products (retailer, sku, gtin, title, brand, category,
                subcategory, url, image_url, is_marketplace, region, first_seen,
                last_seen, current_price, current_rrp, price_updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?{bool_cast},?,?,?,?,?,?)
            ON CONFLICT(retailer, sku, region) DO UPDATE SET
              gtin=COALESCE(excluded.gtin, products.gtin), title=excluded.title,
              brand=COALESCE(excluded.brand, products.brand), url=excluded.url,
              subcategory=COALESCE(excluded.subcategory, products.subcategory),
              image_url=COALESCE(excluded.image_url, products.image_url),
              is_marketplace=excluded.is_marketplace{bool_cast},
              last_seen=excluded.last_seen,
              current_price=excluded.current_price,
              current_rrp=excluded.current_rrp,
              price_updated_at=excluded.price_updated_at""",
        [(r.retailer, r.sku, r.gtin, r.title, r.brand, r.category,
          r.subcategory, r.url,
          r.image_url, r.is_marketplace, r.region or "", now, now,
          r.price, r.rrp, now) for r in chunk])

    # ids for rows we hadn't seen before
    missing = [r for r in chunk
               if (r.retailer, r.sku, r.region or "") not in old]
    for retailer in {r.retailer for r in missing}:
        skus = [r.sku for r in missing if r.retailer == retailer]
        for row in conn.execute(
                f"SELECT id, retailer, sku, region FROM products "
                f"WHERE retailer=? AND sku IN ({','.join('?' * len(skus))})",
                (retailer, *skus)).fetchall():
            old.setdefault((row["retailer"], row["sku"], row["region"]),
                           (row["id"], None))

    changed = []
    for r in chunk:
        key = (r.retailer, r.sku, r.region or "")
        if key not in old:
            continue
        pid, prev = old[key]
        if prev is None or abs(float(prev) - r.price) > 0.004:
            changed.append((pid, r))
    if changed:
        conn.executemany(
            f"INSERT INTO price_snapshots (product_id, price, rrp, in_stock, "
            f"scraped_at) VALUES (?,?,?,?{bool_cast},?)",
            [(pid, r.price, r.rrp, r.in_stock, now) for pid, r in changed])
    conn.commit()
    return [r for pid, r in changed]


def _upsert_chunk_pg(conn, chunk, now) -> list:
    """Postgres path: one statement, change detection server-side.

    The SQLite-style path above reads every chunk row back to compare old
    prices client-side - over Supabase that shipped megabytes out of the DB
    on every sweep (48x/day across the retailer matrix) and blew the free
    tier's egress quota. Here the VALUES payload goes IN (ingress is free),
    the old-price comparison and snapshot writes happen inside the database,
    and only the keys of rows whose price actually changed come back.
    """
    by_key = {(r.retailer, r.sku, r.region or ""): r for r in chunk}
    row_sql = "(" + ",".join(["%s"] * 14) + ")"
    values_sql = ",".join([row_sql] * len(chunk))
    params = []
    for r in chunk:
        params += [r.retailer, r.sku, r.gtin, r.title, r.brand, r.category,
                   r.subcategory, r.url, r.image_url, r.is_marketplace,
                   r.region or "", r.price, r.rrp, r.in_stock]
    sql = f"""
    WITH v (retailer, sku, gtin, title, brand, category, subcategory, url,
            image_url, is_marketplace, region, price, rrp, in_stock) AS (
      VALUES {values_sql}
    ),
    old AS (
      -- pre-statement prices: every CTE sees the same snapshot, so this
      -- reads the state from before `up` writes
      SELECT p.id, p.current_price
      FROM products p
      JOIN v ON v.retailer = p.retailer AND v.sku = p.sku
            AND v.region = p.region
    ),
    up AS (
      INSERT INTO products (retailer, sku, gtin, title, brand, category,
          subcategory, url, image_url, is_marketplace, region, first_seen,
          last_seen, current_price, current_rrp, price_updated_at)
      SELECT retailer, sku, gtin, title, brand, category, subcategory, url,
             image_url, is_marketplace::boolean, region, %s, %s,
             price::numeric(10,2), rrp::numeric(10,2), %s
      FROM v
      ON CONFLICT (retailer, sku, region) DO UPDATE SET
        gtin=COALESCE(excluded.gtin, products.gtin), title=excluded.title,
        brand=COALESCE(excluded.brand, products.brand), url=excluded.url,
        subcategory=COALESCE(excluded.subcategory, products.subcategory),
        image_url=COALESCE(excluded.image_url, products.image_url),
        is_marketplace=excluded.is_marketplace,
        last_seen=excluded.last_seen,
        current_price=excluded.current_price,
        current_rrp=excluded.current_rrp,
        price_updated_at=excluded.price_updated_at
      RETURNING id, retailer, sku, region
    ),
    snap AS (
      INSERT INTO price_snapshots (product_id, price, rrp, in_stock, scraped_at)
      SELECT up.id, v.price::numeric(10,2), v.rrp::numeric(10,2),
             v.in_stock::boolean, %s
      FROM up
      JOIN v ON v.retailer = up.retailer AND v.sku = up.sku
            AND v.region = up.region
      LEFT JOIN old ON old.id = up.id
      -- current_price is REAL (float); cast BOTH sides to numeric(10,2) or
      -- float representation error (35.8 -> 35.7999...) marks every row
      -- changed and re-bloats the snapshots this design exists to avoid
      WHERE old.id IS NULL
         OR old.current_price::numeric(10,2) IS DISTINCT FROM v.price::numeric(10,2)
      RETURNING product_id
    )
    SELECT up.retailer, up.sku, up.region
    FROM up JOIN snap ON snap.product_id = up.id
    """
    rows = conn.execute(sql, (*params, now, now, now, now)).fetchall()
    conn.commit()
    return [by_key[(r["retailer"], r["sku"], r["region"])] for r in rows
            if (r["retailer"], r["sku"], r["region"]) in by_key]


class _PgShim:
    """Minimal adapter so Postgres accepts the SQLite-style SQL used here."""

    def __init__(self, conn):
        self._c = conn

    @staticmethod
    def _q(sql):
        sql = sql.replace("?", "%s")
        if "INSERT OR IGNORE INTO" in sql:
            sql = sql.replace("INSERT OR IGNORE INTO", "INSERT INTO") + " ON CONFLICT DO NOTHING"
        return sql

    def execute(self, sql, params=()):
        cur = self._c.cursor()
        cur.execute(self._q(sql), params)
        return cur

    def executemany(self, sql, seq):
        cur = self._c.cursor()
        cur.executemany(self._q(sql), list(seq))
        return cur

    def commit(self):
        self._c.commit()

    def rollback(self):
        self._c.rollback()
