"""CLI entry point.

  python run.py scrape officeworks --limit 20   scrape one retailer
  python run.py scrape all --limit 20           scrape every retailer
  python run.py url <product-url>               ingest one specific product page
  python run.py detect                          run anomaly engine
  python run.py deals                           show current deals
"""
import argparse

import db
from anomaly import run as detect
from scrapers import REGISTRY
from scrapers.base import Blocked


def cmd_scrape(args):
    conn = db.connect()
    names = list(REGISTRY) if args.retailer == "all" else [args.retailer]
    for name in names:
        scraper = REGISTRY[name]()
        print(f"== {name}: discovering up to {args.limit} products ==")
        n = 0
        try:
            for rec in scraper.scrape(limit=args.limit):
                db.upsert(conn, rec)
                n += 1
                rrp = f" (rrp ${rec.rrp:.2f})" if rec.rrp else ""
                mp = " [marketplace]" if rec.is_marketplace else ""
                print(f"  ${rec.price:>8.2f}{rrp}{mp}  {rec.title[:60]}")
        except Blocked as e:
            print(f"  BLOCKED: {e}")
        print(f"  -> stored {n} records\n")


def cmd_index(args):
    """Load the retailer's FULL product catalogue URLs into the crawl queue."""
    conn = db.connect()
    names = list(REGISTRY) if args.retailer == "all" else [args.retailer]
    for name in names:
        scraper = REGISTRY[name]()
        print(f"== {name}: indexing full catalogue ==")
        n = 0
        try:
            batch = []
            for url in scraper.discover_all():
                batch.append((name, url))
                if len(batch) >= 1000:
                    conn.executemany(
                        "INSERT OR IGNORE INTO crawl_queue (retailer, url) VALUES (?,?)", batch)
                    conn.commit(); n += len(batch); batch = []
                    print(f"  ...{n} URLs indexed")
            if batch:
                conn.executemany(
                    "INSERT OR IGNORE INTO crawl_queue (retailer, url) VALUES (?,?)", batch)
                conn.commit(); n += len(batch)
        except Blocked as e:
            print(f"  BLOCKED: {e}")
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM crawl_queue WHERE retailer=?", (name,)).fetchone()["n"]
        print(f"  -> {total} URLs in queue for {name}\n")


def cmd_crawl(args):
    """Work through the crawl queue: oldest/never-scraped first. Resumable -
    run it on a schedule and the whole catalogue gets covered in rolling passes."""
    from datetime import datetime, timezone
    conn = db.connect()
    scraper = REGISTRY[args.retailer]()
    rows = conn.execute(
        """SELECT url FROM crawl_queue WHERE retailer=? AND fails < 3
           ORDER BY last_scraped IS NOT NULL, last_scraped LIMIT ?""",
        (args.retailer, args.batch)).fetchall()
    if not rows:
        print(f"queue empty - run: python run.py index {args.retailer}")
        return
    ok = 0
    for r in rows:
        url = r["url"]
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            rec = scraper.parse_product(url, scraper.get(url))
        except Blocked as e:
            print(f"BLOCKED, stopping batch: {e}")
            # Without this, this exact URL's last_scraped stays NULL forever,
            # so the oldest-first ordering keeps re-picking it first on every
            # future cycle and the batch never advances past it.
            conn.execute("UPDATE crawl_queue SET fails=fails+1, last_scraped=? "
                         "WHERE retailer=? AND url=?", (now, args.retailer, url))
            conn.commit()
            break
        except Exception as e:
            print(f"  parse error on {url}: {type(e).__name__}: {e}")
            conn.execute("UPDATE crawl_queue SET fails=fails+1, last_scraped=? "
                         "WHERE retailer=? AND url=?", (now, args.retailer, url))
            conn.commit()
            continue
        try:
            conn.execute("UPDATE crawl_queue SET last_scraped=?, fails=0 "
                         "WHERE retailer=? AND url=?", (now, args.retailer, url))
            if rec:
                db.upsert(conn, rec)
                ok += 1
            else:
                conn.execute("UPDATE crawl_queue SET fails=fails+1 "
                             "WHERE retailer=? AND url=?", (args.retailer, url))
            conn.commit()
        except Exception as e:
            # A pooled Postgres connection can occasionally throw a transient
            # error (e.g. a stale prepared statement); don't let one bad row
            # crash the whole batch - roll back, count it as a fail, move on.
            conn.rollback()
            print(f"  DB error on {url}: {e}")
            conn.execute("UPDATE crawl_queue SET fails=fails+1 "
                         "WHERE retailer=? AND url=?", (args.retailer, url))
            conn.commit()
    print(f"batch done: {ok}/{len(rows)} products stored "
          f"(rerun to continue through the queue)")


def cmd_refresh(args):
    """Fast bulk price refresh via listing pages / retailer APIs.

    Covers whole catalogues in hundreds of requests instead of one request
    per product; price_snapshots only grow when a price actually changed.
    """
    conn = db.connect()
    names = list(REGISTRY) if args.retailer == "all" else [args.retailer]
    for name in names:
        scraper = REGISTRY[name]()
        if not hasattr(scraper, "refresh_listings"):
            print(f"== {name}: no fast refresh path, skipping ==")
            continue
        print(f"== {name}: bulk refresh (budget {args.budget} requests) ==")
        kwargs = {}
        if name == "officeworks":
            # bulk API needs a SKU list: everything we know + queue-derived
            skus = {r["sku"] for r in conn.execute(
                "SELECT sku FROM products WHERE retailer=?", (name,))}
            for r in conn.execute(
                    "SELECT url FROM crawl_queue WHERE retailer=?", (name,)):
                sku = scraper.sku_from_url(r["url"])
                if sku:
                    skus.add(sku)
            kwargs["skus"] = sorted(skus)
            print(f"  {len(skus)} known SKUs to refresh")
        seen = 0
        snaps = 0
        batch = []
        try:
            for rec in scraper.refresh_listings(budget=args.budget, **kwargs):
                batch.append(rec)
                seen += 1
                if len(batch) >= 400:
                    snaps += db.bulk_upsert(conn, batch)
                    batch = []
                    if seen % 4000 == 0:
                        print(f"  ...{seen} listings, {snaps} price changes")
        except Blocked as e:
            print(f"  BLOCKED mid-refresh (keeping what we got): {e}")
        except Exception as e:
            print(f"  refresh error after {seen} listings: "
                  f"{type(e).__name__}: {e}")
        snaps += db.bulk_upsert(conn, batch)
        print(f"  -> {seen} listings processed, {snaps} snapshots written\n")


def cmd_url(args):
    """Ingest one specific product URL (auto-detects retailer from domain)."""
    conn = db.connect()
    for name, S in REGISTRY.items():
        if f"{name}.com" in args.url:
            s = S()
            rec = s.parse_product(args.url, s.get(args.url))
            if rec:
                db.upsert(conn, rec)
                rrp = f" (rrp ${rec.rrp:.2f})" if rec.rrp else ""
                print(f"stored: ${rec.price:.2f}{rrp}  {rec.title}")
            else:
                print("no product data found on that page")
            return
    print(f"no scraper matches that URL (have: {', '.join(REGISTRY)})")


def cmd_detect(args):
    conn = db.connect()
    deals = detect(conn)
    if not deals:
        print("No new anomalies (need RRP gaps, price history, or GTIN overlap).")
    for d in deals:
        mp = " [marketplace seller]" if d["marketplace"] else ""
        print(f"[{d['tier']}] {d['off']} off via {d['signal']}: {d['title'][:55]} "
              f"${d['price']:.2f} (ref ${d['reference']:.2f}) @ {d['retailer']}{mp}\n  {d['url']}")


def cmd_deals(args):
    conn = db.connect()
    rows = conn.execute(
        """SELECT d.*, p.title, p.retailer, p.url, p.is_marketplace
           FROM deals d JOIN products p ON p.id=d.product_id
           WHERE d.status != 'expired' ORDER BY d.score DESC LIMIT 50"""
    ).fetchall()
    if not rows:
        print("No deals recorded yet. Run: python run.py scrape all && python run.py detect")
    for r in rows:
        mp = " [marketplace seller]" if r["is_marketplace"] else ""
        print(f"[{r['status']}] {r['score']:.0%} off ({r['signal']}) "
              f"{r['title'][:55]} ${r['price']:.2f} @ {r['retailer']}{mp}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scrape")
    s.add_argument("retailer", choices=list(REGISTRY) + ["all"])
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(fn=cmd_scrape)
    ix = sub.add_parser("index")
    ix.add_argument("retailer", choices=list(REGISTRY) + ["all"])
    ix.set_defaults(fn=cmd_index)
    c = sub.add_parser("crawl")
    c.add_argument("retailer", choices=list(REGISTRY))
    c.add_argument("--batch", type=int, default=200)
    c.set_defaults(fn=cmd_crawl)
    rf = sub.add_parser("refresh")
    rf.add_argument("retailer", choices=list(REGISTRY) + ["all"])
    rf.add_argument("--budget", type=int, default=1400,
                    help="max listing/API requests this run")
    rf.set_defaults(fn=cmd_refresh)
    u = sub.add_parser("url")
    u.add_argument("url")
    u.set_defaults(fn=cmd_url)
    sub.add_parser("detect").set_defaults(fn=cmd_detect)
    sub.add_parser("deals").set_defaults(fn=cmd_deals)
    a = ap.parse_args()
    a.fn(a)
