"""CLI entry point.

  python run.py scrape officeworks --limit 20   scrape one retailer
  python run.py scrape all --limit 20           scrape every retailer
  python run.py url <product-url>               ingest one specific product page
  python run.py detect                          run anomaly engine
  python run.py publish                         refresh public website feeds
  python run.py deals                           show current deals
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
import time
from urllib.parse import urlparse

import alerts
import categorize as categorize_mod
import db
import watch_alerts
from anomaly import BIG_DROP, run as detect
from publication import refresh_public_views
from scrapers import REGISTRY
from scrapers.base import Blocked, NotFound, verify_price


def record_scraper_health(conn, retailer: str, status: str, *, stored: int = 0,
                          attempted: int = 0, detail: str = ""):
    """Persist the most recent crawler outcome for the health-alert job.

    A blocked request is otherwise easy to miss because a crawl batch exits
    cleanly after recording the queue failure so the next scheduled batch can
    continue. This marker makes that operational state observable.
    """
    value = json.dumps({"status": status, "stored": stored,
                        "attempted": attempted, "detail": detail[:300],
                        "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    conn.execute("INSERT OR IGNORE INTO kv (k, v) VALUES (?, ?)",
                 (f"scraper_health:{retailer}", value))
    conn.execute("UPDATE kv SET v=? WHERE k=?", (value, f"scraper_health:{retailer}"))
    conn.commit()


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
            record_scraper_health(conn, args.retailer, "budget_exhausted",
                                  detail="Proxy byte budget exhausted; no crawl performed")
            raise RuntimeError("Big W proxy budget exhausted")
    rows = conn.execute(
        """SELECT url FROM crawl_queue WHERE retailer=? AND fails < 3
           ORDER BY last_scraped ASC NULLS FIRST, url ASC LIMIT ?""",
        (args.retailer, args.batch)).fetchall()
    conn.commit()  # Do not hold a read transaction across retailer requests.
    if not rows:
        detail = f"queue empty - run: python run.py index {args.retailer}"
        record_scraper_health(conn, args.retailer, "empty", detail=detail)
        raise RuntimeError(detail)
    ok = 0
    blocked = False
    watch_hits = []   # already-tracked products whose price moved this batch
    for r in rows:
        url = r["url"]
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            rec = scraper.parse_product(url, scraper.get(url))
        except Blocked as e:
            print(f"BLOCKED, stopping batch: {e}")
            blocked = True
            record_scraper_health(conn, args.retailer, "blocked", stored=ok,
                                  attempted=len(rows), detail=str(e))
            # Without this, this exact URL's last_scraped stays NULL forever,
            # so the oldest-first ordering keeps re-picking it first on every
            # future cycle and the batch never advances past it.
            # A store-wide block is not evidence of a broken product URL.
            # Advance the cursor without exhausting this URL's retry budget.
            conn.execute("UPDATE crawl_queue SET last_scraped=? "
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
    if not blocked:
        record_scraper_health(conn, args.retailer, "ok" if ok else "empty",
                              stored=ok, attempted=len(rows),
                              detail="No products stored" if not ok else "")
    print(f"batch done: {ok}/{len(rows)} products stored "
          f"(rerun to continue through the queue)")
    if blocked or not ok:
        raise RuntimeError(f"{args.retailer}: crawl {'blocked' if blocked else 'stored no products'}")


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
    failures = []
    for name in names:
        scraper = REGISTRY[name]()
        if not hasattr(scraper, "refresh_listings"):
            print(f"== {name}: no fast refresh path, skipping ==")
            continue
        print(f"== {name}: bulk refresh (budget {args.budget} requests) ==")
        kwargs = {}
        if name == "officeworks":
            # bulk API needs a SKU list: items worth watching (>=$50 or not
            # yet priced) + SKUs recoverable from queued sitemap URLs
            skus = {r["sku"] for r in conn.execute(
                "SELECT sku FROM products WHERE retailer=?", (name,))}
            for r in conn.execute(
                    "SELECT url FROM crawl_queue WHERE retailer=?", (name,)):
                sku = scraper.sku_from_url(r["url"])
                if sku:
                    skus.add(sku)
            kwargs["skus"] = sorted(skus)
            print(f"  {len(skus)} SKUs to refresh")
        cat_state = None
        state_key = 'bigw_cat_state' if name == 'bigw' else 'jbhifi_refresh_state'
        if name in {"bigw", "jbhifi"}:
            # stalest-categories-first sweep order, persisted across runs
            row = conn.execute("SELECT v FROM kv WHERE k=?",
                               (state_key,)).fetchone()
            cat_state = json.loads(row["v"]) if row else {}
            kwargs["state"] = cat_state
        conn.commit()  # Preload reads must not remain open during network I/O.
        seen = 0
        kept = 0
        sweep_blocked = False
        sweep_error = None
        snaps = 0
        batch = []
        seen_skus = set()

        def flush_pending():
            nonlocal batch, snaps, kept
            if not batch:
                return
            # Only cheap listings need an existence check. Looking up their
            # keys in a bounded batch avoids preloading an entire retailer.
            cheap = sorted({r.sku for r in batch if r.price is not None
                            and 0 < r.price < MIN_KEEP_PRICE
                            and not (r.rrp and r.rrp >= MIN_KEEP_PRICE)})
            known = {r['sku'] for r in conn.execute(
                "SELECT sku FROM products WHERE retailer=? AND sku IN (" +
                ','.join('?' for _ in cheap) + ')', (name, *cheap)).fetchall()} if cheap else set()
            conn.commit()
            selected = [r for r in batch if r.price is not None and r.price > 0
                        and (r.sku in known or r.price >= MIN_KEEP_PRICE
                             or (r.rrp and r.rrp >= MIN_KEEP_PRICE))]
            changed = db.bulk_upsert(conn, selected)
            snaps += len(changed)
            kept += len(selected)
            batch = []
            if name == 'jbhifi':
                alerts.send_ram_watch(changed)
            alerts.send_item_watch(conn, changed)

        try:
            for rec in scraper.refresh_listings(budget=args.budget, **kwargs):
                seen += 1
                seen_skus.add(rec.sku)
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
                if len(batch) >= 100:
                    flush_pending()
                    if seen % 4000 == 0:
                        print(f"  ...{seen} listings, {snaps} price changes")
        except Blocked as e:
            print(f"  BLOCKED mid-refresh (keeping what we got): {e}")
            sweep_blocked = True
            record_scraper_health(conn, name, "blocked", stored=kept,
                                  attempted=seen, detail=str(e))
        except Exception as e:
            print(f"  refresh error after {seen} listings: "
                  f"{type(e).__name__}: {e}")
            conn.rollback()   # a failed transaction would poison the flush
            sweep_error = f"{type(e).__name__}: {e}"
        flush_pending()
        if cat_state is not None:
            conn.execute("INSERT OR IGNORE INTO kv (k, v) VALUES (?,?)",
                         (state_key, "{}"))
            conn.execute("UPDATE kv SET v=? WHERE k=?",
                         (json.dumps(cat_state), state_key))
            conn.commit()
        print(f"  -> {seen} listings seen, {kept} kept (>= ${MIN_KEEP_PRICE:.0f} "
              f"or already tracked), {snaps} snapshots written")
        if not seen and name in {"bigw", "kmart", "target", "officeworks", "jbhifi", "sephora"}:
            sweep_error = sweep_error or "Listing refresh returned no products"
        if sweep_error:
            record_scraper_health(conn, name, "error", stored=kept,
                                  attempted=seen, detail=sweep_error)
        elif not sweep_blocked:
            record_scraper_health(conn, name, "ok", stored=kept, attempted=seen)
        if sweep_blocked or sweep_error:
            failures.append(name)

        # After a Blocked sweep the delist probes would run on a connection
        # Akamai just flagged - its 404s are less trustworthy, and with few/no
        # listings seen "missing" barely means anything. Skip them; a healthy
        # future sweep resumes the delist backlog. (Retailers whose refresh
        # lane is a legitimate no-op - Myer, Good Guys - are unaffected: they
        # end with seen=0 but no Blocked, and this stays their delist path.)
        candidates = ([] if sweep_blocked or sweep_error else conn.execute(
            "SELECT sku,url FROM products WHERE retailer=? AND current_price IS NOT NULL "
            "ORDER BY last_seen ASC,sku ASC LIMIT ?", (name, MISSING_CHECK_BUDGET * 10)).fetchall())
        conn.commit()
        missing_urls = {r['sku']: r['url'] for r in candidates if r['sku'] not in seen_skus}
        missing = list(missing_urls)[:MISSING_CHECK_BUDGET]
        delisted = 0
        for sku in missing:
            url = missing_urls.get(sku)
            if not url:
                continue
            try:
                rec = scraper.parse_product(url, scraper.get(url))
                if rec:
                    db.upsert(conn, rec)
                    conn.commit()
            except Blocked as exc:
                record_scraper_health(conn, name, "blocked", stored=kept,
                                      attempted=seen, detail=str(exc))
                failures.append(name)
                break
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
    if failures:
        raise RuntimeError("Refresh incomplete: " + ", ".join(sorted(set(failures))))


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
    repaired_books = categorize_mod.repair_misclassified_books(conn)
    if repaired_books:
        print(f"corrected {repaired_books} misclassified book listings")
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
    if db.DATABASE_URL and not getattr(args, "skip_publish", False):
        refresh_public_views(conn)


def cmd_publish(args):
    """Publish independently, even when detection or notifications fail."""
    host = urlparse(db.DATABASE_URL or "").hostname or ""
    if not host or host.endswith((".supabase.com", ".supabase.co")):
        raise RuntimeError("Publication requires the current OCI PostgreSQL connection")
    conn = db.connect()
    try:
        refresh_public_views(conn)
    finally:
        conn.close()


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
    dt = sub.add_parser("detect")
    dt.add_argument("--skip-publish", action="store_true",
                    help="publish separately using the independent publication job")
    dt.set_defaults(fn=cmd_detect)
    sub.add_parser("publish").set_defaults(fn=cmd_publish)
    sub.add_parser("deals").set_defaults(fn=cmd_deals)
    a = ap.parse_args()
    a.fn(a)
