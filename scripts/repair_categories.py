"""Review/apply high-confidence category repairs in bounded batches.

Run from the repository: python scripts/repair_categories.py [--apply]
Only ISBN-13 and unambiguous native categories override an existing label.
Title refinements only repair 'other', never a retailer-assigned category.
Use --after-id to resume the printed cursor; defaults to a dry run.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import db
from categorize import trusted_category, categorize


def repairs(rows):
    for row in rows:
        category = trusted_category(row["retailer"], row["gtin"], row["subcategory"])
        if not category and row["category"] == "other":
            category = categorize(row["title"])
        if category and category != row["category"]:
            yield category, row["id"], row["category"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--after-id', type=int, default=0)
    parser.add_argument('--limit', type=int, default=5000)
    parser.add_argument('--feed-only', action='store_true',
                        help='Review only products currently in the public deal feed (PostgreSQL).')
    args = parser.parse_args()
    if not 1 <= args.limit <= 5000:
        parser.error('--limit must be between 1 and 5000')
    conn = db.connect()
    if args.feed_only and not db.DATABASE_URL:
        parser.error('--feed-only requires the production PostgreSQL database')
    feed_filter = ("AND EXISTS (SELECT 1 FROM discount_feed d WHERE "
                   "d.retailer=p.retailer AND d.sku=p.sku) " if args.feed_only else "")
    rows = conn.execute(
        "SELECT id, retailer, gtin, subcategory, title, category FROM products p "
        "WHERE id > ? AND current_price > 0 " + feed_filter + "ORDER BY id LIMIT ?",
        (args.after_id, args.limit)).fetchall()
    updates = list(repairs(rows))
    for category, row_id, old in updates[:10]:
        print(f'{row_id}: {old} -> {category}')
    print(f'Inspected {len(rows)}; repairs {len(updates)}; '
          f'next --after-id {rows[-1]["id"] if rows else args.after_id}')
    if args.apply:
        for i in range(0, len(updates), 100):
            # Optimistic category guard preserves concurrent scraper updates.
            conn.executemany('UPDATE products SET category=? WHERE id=? '
                             'AND (category=? OR (category IS NULL AND ? IS NULL))',
                             [(cat, row_id, old, old) for cat, row_id, old
                              in updates[i:i + 100]])
            conn.commit()
        print('Applied reviewed category rules; refresh feed views through the next detect cycle.')
    else:
        print('Dry run: no changes. Use --apply to write this batch.')
    # Production adapter exposes its psycopg connection as _c.
    getattr(conn, '_c', conn).close()


if __name__ == '__main__':
    main()
