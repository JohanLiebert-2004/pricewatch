# Pricewatch — AU retail price anomaly tracker (MVP)

Finds pricing discrepancies (e.g. a $139 quilt cover listed at $20) across
major Australian retailers.

## Quick start
```bash
pip install -r requirements.txt
python run.py scrape officeworks --limit 30   # quick sample scrape
python run.py url <product-url>               # ingest one specific product page
python run.py detect                          # score prices, record deals
python run.py deals                           # list current deals
```

## Full-catalogue mode (the real workflow)
```bash
python run.py index all                       # load EVERY product URL into the crawl queue (one-off, rerun weekly)
python run.py crawl officeworks --batch 500   # work through the queue; fully resumable
python run.py detect                          # after each crawl session
```
`index` reads the complete sitemaps (Officeworks ~40k, Target ~25k, etc.) into a
`crawl_queue` table keyed by (retailer, url). `crawl` always takes the
never-scraped/oldest URLs first, so repeated runs roll through the whole
catalogue and then keep refreshing it. Dead URLs are dropped after 3 failures.
Schedule crawl batches (Task Scheduler / cron) and the database builds itself.

Throughput math: at the polite 2.5s delay, one process does ~1,300 pages/hour,
so a 40k catalogue takes ~30 hours for the first full pass - run one process
per retailer in parallel (each has its own queue), start with the categories
you care about, or become an affiliate and use official product feeds for bulk
price data. Each product is keyed by (retailer, sku); every crawl appends a
snapshot, and drops are computed against each SKU's own recent median.

## Drop tiers
`detect` records anything >= 50% below its reference (RRP, own history, or
cross-retailer median). Drops >= 80% are tagged ERROR-TIER - those are your
"$100 quilt cover for $20" finds and what a future alert bot should push
instantly. Thresholds are constants at the top of `anomaly.py`.
Data lands in `pricewatch.db` (SQLite). For production, apply `schema.sql`
to Postgres and swap the connection in `db.py`.

## Retailers included
| Retailer    | Method                          | Status |
|-------------|---------------------------------|--------|
| Officeworks | product sitemap + embedded state (price in cents, GTIN, brand) | verified working |
| BIG W       | sitemap + JSON-LD, marketplace detection, "Was $" RRP when server-rendered | verified working (needs curl_cffi) |
| Kmart       | sitemap + JSON-LD               | verified working (needs curl_cffi) |
| Target      | nested feed sitemap + JSON-LD   | verified working (needs curl_cffi) |

BIG W / Kmart / Target sit behind Akamai, which fingerprints the TLS handshake:
plain Python HTTP clients get 403 even from residential IPs. `curl_cffi` with the
`chrome99_android` impersonation profile passes (configured per scraper). Blocks
are intermittent and session-scoped — the base scraper retries once with a fresh
session, and keeps a polite 2.5s delay. The long-term sanctioned route is
affiliate product feeds (Commission Factory / Impact).

Known limitation: BIG W *marketplace* listings hydrate their strikethrough
price client-side, so `rrp` is often null for them — the `history_drop` and
`cross_retailer` signals cover those (marketplace listings are flagged in the
DB either way). Target's sitemap contains some discontinued products that render
as empty shells; they're skipped with a `? no product data` notice.

## Anomaly signals (`anomaly.py`)
- `rrp_gap` — >= 70% below the listed RRP / was-price
- `history_drop` — >= 50% below the product's own recent median (needs >= 5 snapshots, i.e. run scrapes on a schedule for a few days)
- `cross_retailer` — >= 60% below the median price of the same GTIN at other retailers (needs >= 2 others)

Marketplace (third-party seller) listings are flagged so users know the price
may be a seller error rather than the retailer's.

## Structure
```
db.py               storage + ProductRecord model (region-aware for Bunnings/HN later)
scrapers/base.py    sitemap discovery, JSON-LD parsing, rate limiting, block detection
scrapers/*.py       per-retailer modules (implement discover/parse_product)
anomaly.py          scoring engine -> deals table
run.py              CLI
schema.sql          Postgres schema for production
```

## Adding a retailer
Subclass `BaseScraper`, set `sitemap_index` + `product_url_pattern`, override
`parse_product` only if the site lacks JSON-LD. Register it in `scrapers/__init__.py`.

## Notes
- Be polite: keep the built-in delays, honour robots.txt, identify traffic sensibly.
- Retailers may cancel orders made on genuine pricing errors; surface that on any public site.
