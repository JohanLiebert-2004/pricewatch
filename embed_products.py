"""Backfill AI "similar items" embeddings for products.

Computes a sentence embedding per product from title/brand/category using
fastembed (ONNX, all-MiniLM-L6-v2, 384-dim, no API key, no torch) and stores
it in products.embedding (pgvector). Powers the similar_products RPC in
views.sql. Production-only: SQLite dev has no vector column, so this is a
no-op unless DATABASE_URL is set. Run repeatedly (e.g. hourly in CI) with a
budget cap - the catalogue is large enough that one run won't clear it.
"""
import argparse

import db


def _to_pgvector(vector):
    # pgvector's text input format; explicit float() avoids numpy repr quirks.
    return "[" + ",".join(f"{float(x):.6f}" for x in vector) + "]"


def embed_batch(model, rows):
    texts = [f"{(r['brand'] or '')} {(r['title'] or '')} {(r['category'] or '')}".strip()
             for r in rows]
    return [_to_pgvector(v) for v in model.embed(texts)]


def run(budget=20000, commit_every=500):
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
    if not rows:
        print("embed_products: no products need embedding")
        return

    done = 0
    for start in range(0, len(rows), commit_every):
        chunk = rows[start:start + commit_every]
        vectors = embed_batch(model, chunk)
        for row, vector in zip(chunk, vectors):
            conn.execute("UPDATE products SET embedding = ?::vector WHERE id = ?",
                         (vector, row["id"]))
        conn.commit()
        done += len(chunk)
        print(f"embed_products: {done}/{len(rows)} embedded")
    print(f"embed_products: done, {done} products embedded")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=20000)
    args = ap.parse_args()
    run(budget=args.budget)
