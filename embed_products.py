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


def _update_batch_with_retry(conn, rows, vectors, attempts=3):
    """Commit in small batches (COMMIT_BATCH rows), not one row at a time.

    The `embed` CI job runs concurrently with the 14-job crawl matrix, which
    also writes to `products`, so this still deliberately keeps each
    transaction's lock window short - the original one-big-commit design
    was the lock-contention pattern that caused the 2-day detect outage
    documented in anomaly.py. But committing every single row (the first
    fix for that) meant one Postgres round trip per row, which is why the
    `embed` job was hitting its time budget and getting cancelled before
    finishing a batch: confirmed live via `gh run list` - every recent run's
    embed job got cancelled at almost exactly its timeout. A batch of
    COMMIT_BATCH UPDATEs still completes in well under a second (nowhere
    near the multi-second lock hold that caused the original outage), while
    cutting round trips ~50x.
    """
    import psycopg
    for attempt in range(attempts):
        try:
            for row, vector in zip(rows, vectors):
                conn.execute("UPDATE products SET embedding = ?::vector WHERE id = ?",
                             (vector, row["id"]))
            conn.commit()
            return len(rows)
        except psycopg.errors.DeadlockDetected:
            conn.rollback()
            if attempt == attempts - 1:
                print(f"embed_products: giving up on a batch of {len(rows)} "
                      f"products after {attempts} deadlock retries, "
                      "will retry next run")
                return 0
            time.sleep(0.5 * (attempt + 1))
    return 0


COMMIT_BATCH = 100


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
        for bstart in range(0, len(chunk), COMMIT_BATCH):
            brows = chunk[bstart:bstart + COMMIT_BATCH]
            bvectors = vectors[bstart:bstart + COMMIT_BATCH]
            done += _update_batch_with_retry(conn, brows, bvectors)
        print(f"embed_products: {done}/{len(rows)} embedded")
    print(f"embed_products: done, {done} products embedded")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=20000)
    args = ap.parse_args()
    run(budget=args.budget)
