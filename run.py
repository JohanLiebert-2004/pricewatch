"""CLI entry point.

  python run.py scrape officeworks --limit 20   scrape one retailer
  python run.py scrape all --limit 20           scrape every retailer
  python run.py url <product-url>               ingest one specific product page
  python run.py detect                          run anomaly engine
  python run.py deals                           show current deals
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
import time

import alerts
import categorize as categorize_mod
import db
import watch_alerts
from anomaly import BIG_DROP, run as detect
from scrapers import REGISTRY
from scrapers.base import Blocked, NotFound, verify_price


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
    proxy_state = None
    if args.retailer == "bigw" and os.environ.get("PROXY_URL"):
        # The crawl lane spends the same metered Webshare bytes as the bulk
        # refresh - gate and record it against the shared cycle cap too (it
        # used to be invisible to the cap: ~77MB/day billed, on track to
        # exhaust the plan weeks before its renewal). With PROXY_URL unset
        # (the home-IP local sweep) requests are direct and free: no cap.
        from scrapers.bigw import PROXY_CYCLE_BYTE_CAP, proxy_cycle
        row = conn.execute("SELECT v FROM kv WHERE k=?",
                           ("bigw_cat_state",)).fetchone()
        proxy_state = json.loads(row["v"]) if row else {}
        cycle = proxy_cycle()
        if proxy_state.get("_proxy_cycle") != cycle:
            proxy_state.pop("_proxy_month", None)
            proxy_state["_proxy_cycle"] = cycle
            proxy_state["_proxy_bytes"] = 0
        if proxy_state.get("_proxy_bytes", 0) >= PROXY_CYCLE_BYTE_CAP:
            print(f"bigw: proxy byte budget spent for cycle {cycle}, "
                  "skipping crawl batch until it renews")
            return
    rows = conn.execute(
        """SELECT url FROM crawl_queue WHERE retailer=? AND fails < 3
           ORDER BY last_scraped IS NOT NULL, last_scraped LIMIT ?""",
        (args.retailer, args.batch)).fetchall()
    if not rows:
        print(f"queue empty - run: python run.py index {args.retailer}")
        return
    ok = 0
    watch_hits = []   # already-tracked products whose price moved this batch
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
                # the crawl lane always snapshots, so detect real moves here
                # for bot item-watchers (refresh lane gets this for free from
                # bulk_upsert's changed-list)
                old = conn.execute(
                    "SELECT current_price FROM products "
                    "WHERE retailer=? AND sku=? AND region=?",
                    (args.retailer, rec.sku, rec.region or "")).fetchone()
                db.upsert(conn, rec)
                if (old and old["current_price"] is not None
                        and rec.price is not None
                        and abs(float(old["current_price"]) - rec.price) > 0.005):
                    watch_hits.append(rec)
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
    if watch_hits:
        alerts.send_item_watch(conn, watch_hits)
    if proxy_state is not None:
        proxy_state["_proxy_bytes"] = (proxy_state.get("_proxy_bytes", 0)
                                       + getattr(scraper, "_proxy_bytes_run", 0))
        conn.execute("INSERT OR IGNORE INTO kv (k, v) VALUES (?,?)",
                     ("bigw_cat_state", "{}"))
        conn.execute("UPDATE kv SET v=? WHERE k=?",
                     (json.dumps(proxy_state), "bigw_cat_state"))
        conn.commit()
    print(f"batch done: {ok}/{len(rows)} products stored "
          f"(rerun to continue through the queue)")


MIN_KEEP_PRICE = 40.0   # don't ingest NEW items normally under this - deals
                        # on cheap items aren't worth tracking. Items already
                        # in the DB always stay refreshed so a big price DROP
                        # on an expensive item (even to $5) is still captured.

MISSING_CHECK_BUDGET = 15   # fast listing feeds (esp. JB Hi-Fi's "25k most
                            # recently published" window) don't cover every
                            # tracked SKU every sweep, so "not seen this
                            # sweep" alone doesn't mean delisted. Bounded so a
                            # long tail outside the feed's window can't blow
                            # the politeness budget on confirmatory fetches;
                            # it self-heals over successive runs (crawl.yml
                            # runs every 30 min).


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
        # sku-only: pulling every URL too shipped ~10MB per cycle out of
        # Supabase across the matrix (egress quota); the <= MISSING_CHECK_BUDGET
        # URLs the delisting check needs are fetched individually below
        known_skus = [r["sku"] for r in conn.execute(
            "SELECT sku FROM products WHERE retailer=? AND current_price IS NOT NULL "
            "ORDER BY COALESCE(price_updated_at, '1970-01-01') ASC", (name,))]
        known = set(known_skus)
        kwargs = {}
        if name == "officeworks":
            # bulk API needs a SKU list: items worth watching (>=$50 or not
            # yet priced) + SKUs recoverable from queued sitemap URLs
            skus = {r["sku"] for r in conn.execute(
                "SELECT sku FROM products WHERE retailer=? AND "
                "(current_price IS NULL OR current_price >= ?)",
                (name, MIN_KEEP_PRICE))}
            for r in conn.execute(
                    "SELECT url FROM crawl_queue WHERE retailer=?", (name,)):
                sku = scraper.sku_from_url(r["url"])
                if sku and sku not in known:
                    skus.add(sku)
            kwargs["skus"] = sorted(skus)
            print(f"  {len(skus)} SKUs to refresh")
        cat_state = None
        if name == "bigw":
            # stalest-categories-first sweep order, persisted across runs
            row = conn.execute("SELECT v FROM kv WHERE k=?",
                               ("bigw_cat_state",)).fetchone()
            cat_state = json.loads(row["v"]) if row else {}
            kwargs["state"] = cat_state
        seen = 0
        kept = 0
        sweep_blocked = False
        snaps = 0
        batch = []
        seen_skus = set()

        def worth_keeping(rec):
            if rec.sku in known:
                return True          # never lose sight of a tracked item
            if rec.price is not None and rec.price >= MIN_KEEP_PRICE:
                return True
            return bool(rec.rrp and rec.rrp >= MIN_KEEP_PRICE)

        try:
            for rec in scraper.refresh_listings(budget=args.budget, **kwargs):
                seen += 1
                seen_skus.add(rec.sku)
                if not worth_keeping(rec):
                    continue
                # Listing feeds can go stale for individual SKUs (see
                # alerts.py's live-verify for the alert path) - a claimed
                # 80%+ discount gets a live page check before it's ever
                # written as current_price, so the site itself doesn't show
                # a price nobody can actually get.
                if (rec.price is not None and rec.rrp and rec.rrp > 0
                        and 1 - rec.price / rec.rrp >= BIG_DROP):
                    live = verify_price(scraper, rec.url, rec.price)
                    if live is not None and abs(live - rec.price) > max(0.5, rec.price * 0.05):
                        print(f"  ~ {name} {rec.sku}: listing said ${rec.price:.2f}, "
                              f"live page says ${live:.2f} - using live price")
                        rec.price = live
                batch.append(rec)
                kept += 1
                if len(batch) >= 400:
                    changed = db.bulk_upsert(conn, batch)
                    snaps += len(changed)
                    if name == "jbhifi":
                        alerts.send_ram_watch(changed)
                    alerts.send_item_watch(conn, changed)
                    batch = []
                    if seen % 4000 == 0:
                        print(f"  ...{seen} listings, {snaps} price changes")
        except Blocked as e:
            print(f"  BLOCKED mid-refresh (keeping what we got): {e}")
            sweep_blocked = True
        except Exception as e:
            print(f"  refresh error after {seen} listings: "
                  f"{type(e).__name__}: {e}")
            conn.rollback()   # a failed transaction would poison the flush
        changed = db.bulk_upsert(conn, batch)
        snaps += len(changed)
        if name == "jbhifi":
            alerts.send_ram_watch(changed)
        alerts.send_item_watch(conn, changed)
        if cat_state is not None:
            conn.execute("INSERT OR IGNORE INTO kv (k, v) VALUES (?,?)",
                         ("bigw_cat_state", "{}"))
            conn.execute("UPDATE kv SET v=? WHERE k=?",
                         (json.dumps(cat_state), "bigw_cat_state"))
            conn.commit()
        print(f"  -> {seen} listings seen, {kept} kept (>= ${MIN_KEEP_PRICE:.0f} "
              f"or already tracked), {snaps} snapshots written")

        # After a Blocked sweep the delist probes would run on a connection
        # Akamai just flagged - its 404s are less trustworthy, and with few/no
        # listings seen "missing" barely means anything. Skip them; a healthy
        # future sweep resumes the delist backlog. (Retailers whose refresh
        # lane is a legitimate no-op - Myer, Good Guys - are unaffected: they
        # end with seen=0 but no Blocked, and this stays their delist path.)
        missing = ([] if sweep_blocked else
                   [sku for sku in known_skus if sku not in seen_skus]
                   [:MISSING_CHECK_BUDGET])
        missing_urls = {r["sku"]: r["url"] for r in conn.execute(
            f"SELECT sku, url FROM products WHERE retailer=? "
            f"AND sku IN ({','.join('?' * len(missing))})",
            (name, *missing)).fetchall()} if missing else {}
        delisted = 0
        for sku in missing:
            url = missing_urls.get(sku)
            if not url:
                continue
            try:
                try:
                    rec = scraper.parse_product(url, scraper.get(url))
                except Blocked:
                    # This check runs right after the bulk listing sweep has
                    # already burned its request budget, so it's the part of
                    # the run most likely to catch a transient rate limit -
                    # without a retry, a real 404 reads as "inconclusive"
                    # every single time and a delisted item never clears.
                    # Worth one patient retry since this loop is small (<=
                    # MISSING_CHECK_BUDGET items).
                    time.sleep(10)
                    rec = scraper.parse_product(url, scraper.get(url))
            except NotFound:
                # confirmed HTTP 404 - not a bot-block, the listing is genuinely
                # gone (product delisted/discontinued). Stop showing it as a
                # live deal/link rather than leaving a stale price forever.
                conn.execute(
                    "UPDATE products SET current_price=NULL WHERE retailer=? AND sku=?",
                    (name, sku))
                conn.execute(
                    "UPDATE deals SET status='expired' WHERE status <> 'expired' "
                    "AND product_id = (SELECT id FROM products WHERE retailer=? AND sku=?)",
                    (name, sku))
                conn.commit()
                delisted += 1
                print(f"  x {name} {sku}: confirmed delisted (404), hidden from site")
            except Exception:
                continue   # inconclusive (blocked, network hiccup, page moved) - fail open
        if missing:
            print(f"  {len(missing)} previously-tracked SKU(s) missing from this sweep "
                  f"checked directly, {delisted} confirmed delisted\n")
        else:
            print()


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
    if db.DATABASE_URL:
        # If the runner kills this job mid-transaction, the orphaned session
        # must not sit "idle in transaction" holding deals locks - one such
        # zombie starved every subsequent detect into statement timeouts for
        # 2+ days (15-18 July). Server reaps the session after 5 idle minutes.
        conn.execute("SET idle_in_transaction_session_timeout = '5min'")
        conn.commit()
    tagged = categorize_mod.backfill(conn)
    if tagged:
        print(f"categorised {tagged} new products")
    subbed = categorize_mod.backfill_subcategories(conn)
    if subbed:
        print(f"subcategorised {subbed} products (title-rule retailers)")
    try:
        # anonymous search-term log only feeds a 7-day trending view; no
        # reason to keep anything older than 30 days
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        conn.execute("DELETE FROM search_terms WHERE searched_at < ?", (cutoff,))
        conn.commit()
    except Exception:
        conn.rollback()   # table absent (local SQLite dev DB)
    deals = detect(conn)
    if not deals:
        print("No new anomalies (need RRP gaps, price history, or GTIN overlap).")
    for d in deals:
        mp = " [marketplace seller]" if d["marketplace"] else ""
        print(f"[{d['tier']}] {d['off']} off via {d['signal']}: {d['title'][:55]} "
              f"${d['price']:.2f} (ref ${d['reference']:.2f}) @ {d['retailer']}{mp}\n  {d['url']}")
    pinged = alerts.send_alerts(conn)
    if pinged:
        print(f"telegram: {pinged} alert(s) sent")
    watched = watch_alerts.send_watch_alerts(conn)
    if watched:
        print(f"resend: {watched} watch alert(s) sent")
    if db.DATABASE_URL:
        # Keep the website's precomputed aggregate feeds current. Running these
        # scans during detect keeps public page loads fast and reliable.
        for view in ("discount_feed", "growth_daily", "catalogue_stats", "retailer_freshness"):
            try:
                conn.execute(f"refresh materialized view concurrently {view}")
                conn.commit()
            except Exception as e:
                conn.rollback()
                print(f"  ! {view} refresh failed: {e}")


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
