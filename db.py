"""Storage layer. Uses SQLite locally; schema mirrors schema.sql (Postgres)."""
import os
import sqlite3
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
    UNIQUE (retailer, sku, region)
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
    return conn


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
    return _PgShim(conn)


def upsert(conn: sqlite3.Connection, rec: ProductRecord) -> int | None:
    """Insert/refresh product and append a price snapshot. Returns product id."""
    if rec.price is None:
        return None
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        """INSERT INTO products (retailer, sku, gtin, title, brand, category, url,
                                 is_marketplace, region, first_seen, last_seen)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(retailer, sku, region) DO UPDATE SET
             gtin=COALESCE(excluded.gtin, products.gtin), title=excluded.title,
             brand=COALESCE(excluded.brand, products.brand), url=excluded.url,
             is_marketplace=excluded.is_marketplace, last_seen=excluded.last_seen""",
        (rec.retailer, rec.sku, rec.gtin, rec.title, rec.brand, rec.category,
         rec.url, rec.is_marketplace, rec.region or "", now, now),
    )
    row = conn.execute(
        "SELECT id FROM products WHERE retailer=? AND sku=? AND region=?",
        (rec.retailer, rec.sku, rec.region or ""),
    ).fetchone()
    pid = row["id"]
    conn.execute(
        "INSERT INTO price_snapshots (product_id, price, rrp, in_stock, scraped_at) VALUES (?,?,?,?,?)",
        (pid, rec.price, rec.rrp, rec.in_stock, now),
    )
    conn.commit()
    return pid


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
