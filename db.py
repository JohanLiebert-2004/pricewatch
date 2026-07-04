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

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    retailer TEXT NOT NULL,
    sku TEXT NOT NULL,
    gtin TEXT,
    title TEXT,
    brand TEXT,
    category TEXT,
    url TEXT,
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
                     ("price_updated_at", "TEXT")):
        if col in have:
            continue
        try:
            conn.execute(f"ALTER TABLE products ADD COLUMN {col} {typ}")
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
    # translate the SQLite schema to Postgres on first connect
    pg_schema = (SCHEMA
                 .replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
                 .replace("INTEGER", "BIGINT"))
    with conn.cursor() as cur:
        cur.execute(pg_schema)
    conn.commit()
    shim = _PgShim(conn)
    _migrate(shim)
    return shim


def upsert(conn: sqlite3.Connection, rec: ProductRecord) -> int | None:
    """Insert/refresh product and append a price snapshot. Returns product id."""
    if rec.price is None:
        return None
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    # Postgres' pooler negotiates parameter types from the unnamed portal and
    # sometimes guesses booleans as smallint; an explicit cast sidesteps that.
    # SQLite doesn't understand "::type" casts, so only apply it under Postgres.
    bool_cast = "::boolean" if DATABASE_URL else ""
    conn.execute(
        f"""INSERT INTO products (retailer, sku, gtin, title, brand, category, url,
                                 is_marketplace, region, first_seen, last_seen,
                                 current_price, current_rrp, price_updated_at)
           VALUES (?,?,?,?,?,?,?,?{bool_cast},?,?,?,?,?,?)
           ON CONFLICT(retailer, sku, region) DO UPDATE SET
             gtin=COALESCE(excluded.gtin, products.gtin), title=excluded.title,
             brand=COALESCE(excluded.brand, products.brand), url=excluded.url,
             is_marketplace=excluded.is_marketplace{bool_cast}, last_seen=excluded.last_seen,
             current_price=excluded.current_price, current_rrp=excluded.current_rrp,
             price_updated_at=excluded.price_updated_at""",
        (rec.retailer, rec.sku, rec.gtin, rec.title, rec.brand, rec.category,
         rec.url, rec.is_marketplace, rec.region or "", now, now,
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


def bulk_upsert(conn, recs: list) -> int:
    """Upsert many ProductRecords with few round trips (for listing refreshes).

    Snapshots are only written when a product's price actually changed (or on
    first sighting), so frequent refreshes don't bloat price_snapshots.
    Returns the number of snapshots written.
    """
    recs = [r for r in recs if r.price is not None]
    if not recs:
        return 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    written = 0
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
                written += _upsert_chunk(conn, chunk, now)
                break
            except Exception:
                conn.rollback()
                if attempt == 2:
                    raise
                time.sleep(random.uniform(0.5, 2.0) * (attempt + 1))
    return written


def _upsert_chunk(conn, chunk, now) -> int:
    """One transaction: upsert products, snapshot only real price changes."""
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
                url, is_marketplace, region, first_seen, last_seen,
                current_price, current_rrp, price_updated_at)
            VALUES (?,?,?,?,?,?,?,?{bool_cast},?,?,?,?,?,?)
            ON CONFLICT(retailer, sku, region) DO UPDATE SET
              gtin=COALESCE(excluded.gtin, products.gtin), title=excluded.title,
              brand=COALESCE(excluded.brand, products.brand), url=excluded.url,
              is_marketplace=excluded.is_marketplace{bool_cast},
              last_seen=excluded.last_seen,
              current_price=excluded.current_price,
              current_rrp=excluded.current_rrp,
              price_updated_at=excluded.price_updated_at""",
        [(r.retailer, r.sku, r.gtin, r.title, r.brand, r.category, r.url,
          r.is_marketplace, r.region or "", now, now,
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

    snaps = []
    for r in chunk:
        key = (r.retailer, r.sku, r.region or "")
        if key not in old:
            continue
        pid, prev = old[key]
        if prev is None or abs(float(prev) - r.price) > 0.004:
            snaps.append((pid, r.price, r.rrp, r.in_stock, now))
    if snaps:
        conn.executemany(
            f"INSERT INTO price_snapshots (product_id, price, rrp, in_stock, "
            f"scraped_at) VALUES (?,?,?,?{bool_cast},?)", snaps)
    conn.commit()
    return len(snaps)


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
