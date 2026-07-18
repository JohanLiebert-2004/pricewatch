"""Backfill AI "similar items" embeddings for products.

Computes a sentence embedding per product from title/brand/category using
fastembed (ONNX, all-MiniLM-L6-v2, 384-dim, no API key, no torch) and stores
it in products.embedding (pgvector). Powers the similar_products RPC in
views.sql. Production-only: SQLite dev has no vector column, so this is a
no-op unless DATABASE_URL is set. Run repeatedly (e.g. hourly in CI) with a
budget cap - the catalogue is large enough that one run won't clear it.
"""
import argparse
import time

import db


def _to_pgvector(vector):
    # pgvector's text input format; explicit float() avoids numpy repr quirks.
    return "[" + ",".join(f"{float(x):.6f}" for x in vector) + "]"


def embed_batch(model, rows):
    texts = [f"{(r['brand'] or '')} {(r['title'] or '')} {(r['category'] or '')}".strip()
             for r in rows]
    return [_to_pgvector(v) for v in model.embed(texts)]


def _update_with_retry(conn, row_id, vector, attempts=3):
    """Commit one row at a time (not batched): the `embed` CI job runs
    concurrently with the 14-job crawl matrix, which also writes to
    `products`. A single UPDATE's lock is held for milliseconds, so
    collisions are rare; holding many rows' locks across one big commit (the
    original design) is exactly the lock-contention pattern that caused the
    2-day detect outage documented in anomaly.py - same fix, applied here.
    Deadlocks are still possible, just unlikely, so retry a couple of times
    before giving up on this one row (it stays NULL and is picked up next run).
    """
    import psycopg
    for attempt in range(attempts):
        try:
            conn.execute("UPDATE products SET embedding = ?::vector WHERE id = ?",
                         (vector, row_id))
            conn.commit()
            return True
        except psycopg.errors.DeadlockDetected:
            conn.rollback()
            if attempt == attempts - 1:
                print(f"embed_products: giving up on product {row_id} after "
                      f"{attempts} deadlock retries, will retry next run")
                return False
            time.sleep(0.5 * (attempt + 1))
    return False


def run(budget=20000, embed_batch_size=500):
    if not db.DATABASE_URL:
        print("embed_products: DATABASE_URL not set, nothing to do (production-only feature)")
        return
    from fastembed import TextEmbedding
    model = TextEmbedding(model_name="sentence-transformers/all-MiniLM-L6-v2")

    conn = db.connect()
    rows = conn.execute(
        "SELECT id, title, brand, category FROM products "
        "WHERE current_price IS NOT NULL AND embedding IS NULL "
        "ORDER BY id LIMIT ?", (budget,)).fetchall()
    conn.commit()  # close the read transaction before the encode/write loop
    if not rows:
        print("embed_products: no products need embedding")
        return

    done = 0
    for start in range(0, len(rows), embed_batch_size):
        chunk = rows[start:start + embed_batch_size]
        vectors = embed_batch(model, chunk)  # CPU-bound, no open transaction
        for row, vector in zip(chunk, vectors):
            if _update_with_retry(conn, row["id"], vector):
                done += 1
        print(f"embed_products: {done}/{len(rows)} embedded")
    print(f"embed_products: done, {done} products embedded")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=20000)
    args = ap.parse_args()
    run(budget=args.budget)
