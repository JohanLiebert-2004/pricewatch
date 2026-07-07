# Underpriced (Pricewatch) — project notes

*Last updated: 7 July 2026*

An AU retail price-anomaly tracker. Crawlers watch retailer catalogues,
an anomaly engine flags big price drops, and a public website shows the
deals. Everything runs on free tiers.

**Live site:** https://web-pi-blush-48.vercel.app
**Repo:** https://github.com/JohanLiebert-2004/pricewatch (public)

## Architecture

```
GitHub Actions (half-hourly crawl matrix + detect job)
        │  scrape / refresh / detect
        ▼
Supabase Postgres (Sydney, ap-southeast-2)
   base tables locked by RLS; anon role can SELECT only 5 read views
        │  PostgREST (anon key, read-only)
        ▼
Vercel static site (web/ folder, no build step)  +  Telegram alerts
```

- Local dev uses SQLite (`pricewatch.db`); setting `DATABASE_URL` switches
  every module to Postgres via the psycopg shim in `db.py`.
- The anon key embedded in the web pages is public by design (RLS-limited).
- Secrets (`DATABASE_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) live in
  the gitignored `.env` locally and in GitHub Actions secrets. Never commit
  `.env` or `pricewatch.db`.

## Retailers covered (5)

| Retailer    | Method                                              | Notes |
|-------------|-----------------------------------------------------|-------|
| Kmart       | Constructor.io listing API                          | ~105k products; images captured |
| Big W       | `__NEXT_DATA__` category listings                   | Akamai; 2.5s delay after July soft-flag (owner floor 1.75s); no images yet |
| Officeworks | Bulk price API by SKU list + sitemap                | images captured (s3 PIM) |
| Target      | Category listings                                   | Akamai; no images yet |
| JB Hi-Fi    | Shopify `/products.json` (250/page, 100-page cap = 25k most recent) | no bot protection; 1.0s delay; images via Shopify CDN |

Politeness is non-negotiable: no delays below the owner-approved floors,
no parallel requests to one retailer. `Blocked` exceptions are expected
(datacenter IPs), not bugs — batches resume next run.

## What's built

### Data pipeline (`run.py` subcommands)
- `index` — load a retailer's full catalogue URLs into `crawl_queue`.
- `crawl` — work the queue oldest-first, resumable.
- `refresh` — fast bulk price sweep via listing/API pages; snapshots only
  written when a price actually changed. $40 keep-floor for *new* items;
  already-tracked items always stay refreshed.
- `detect` — categorise new products, run the anomaly engine (records 50%+
  drops as `deals`), send Telegram alerts, then refresh the `discount_feed`
  materialized view (~0.5s).
- `url` / `scrape` / `deals` — one-off ingest, smoke-test scrape, list deals.

### Database (schema.sql + views.sql)
- Tables: `products` (incl. `image_url`, `category`, `current_rrp`,
  `price_updated_at`), `price_snapshots` (only on change), `deals`
  (incl. `alerted_at`), `crawl_queue`, `kv`.
- Anon read views (canonical SQL in `views.sql`, re-run after DDL changes,
  grants must be re-issued after a drop):
  - `deal_feed` — anomaly-engine deals (50%+), reference ≥ $40.
  - `discount_feed` — **materialized** view of every discounted product at
    any depth (reference = max(RRP, 90-day snapshot high), ≥$40 floor,
    seen in last 10 days). Unique index on (retailer, sku); refreshed
    concurrently by every `detect`. Was a plain view but anon ilike queries
    hit Supabase's statement timeout re-aggregating 126k snapshots.
  - `product_search`, `catalogue_stats`, `growth_daily`.

### Website (`web/`, static, Vercel)
- `index.html` — deal feed: store/category chips, 0–99% discount slider,
  Everything/Error-tier/Marketplace type chips, name/SKU search that stacks
  with all filters (all server-side via PostgREST), grouped by retailer,
  product thumbnails.
- `catalogue.html` — browse everything tracked; store/category/price
  filters + text search, exact counts via `Prefer: count=exact`.
- `search.html` — latest tracked price + "as of" date for any product by
  name/SKU. Ordered by title (recency ordering starved small retailers
  after big sweeps — the "ps5 shows only Kmart" bug).
- `growth.html` — per-day new products and price checks per retailer.
- `style.css` — design system v3: cool neutral palette (light `#fafafb`
  paper / dark `#0c0d10`, green `--deal`, red `--flag`), Inter + Spline
  Sans Mono only, hairline row separators, 64px thumbnail tiles with
  retailer-monogram fallback. (v1/v2 with serif/grain/glow were rejected
  as looking cheap.)
- Thumbnails are downsized at render time (`thumbSrc()`): Shopify
  `?width=200`, Officeworks `JPEG_300x300`, Kmart `width:200,height:250`.

### Telegram alerts (`alerts.py`)
- After every `detect`, deals with score ≥ 0.80 **and** reference ≥ $100
  get a message to the owner's personal chat (bot @underp22bot); stamped
  `deals.alerted_at` so each fires once. `python alerts.py whoami|test`.
- Public channel broadcasting was considered and deferred ("keep it
  personal for now").

### CI (`.github/workflows/crawl.yml`)
- Matrix refresh job per retailer (jbhifi budget 100) + detect job with
  Telegram secrets. First-run catalogue index line stays commented out.

## Key decisions

- **Amazon: no scraping.** Their Conditions of Use forbid it and it would
  require bot-detection evasion. Route chosen: Product Advertising API —
  Associates account approved (ID `tarunsudan`), but PA-API access now
  requires **10 qualifying sales in the trailing 30 days**. Agreed fallback
  (deferred): tagged "Check Amazon" links + the required "As an Amazon
  Associate I earn from qualifying purchases" disclosure. Keepa (~€49/mo)
  noted as a paid alternative.
- **Target expansion** via Commission Factory affiliate feed — designed,
  blocked on user signup.
- **"Only 5 deals" problem**: the anomaly engine only records 50%+ drops by
  design; the fix was the products-level `discount_feed` (~1,500 items ≥1%
  off) plus the slider, not loosening the engine.
- **JB Hi-Fi 25k window**: Shopify caps page×limit at 25,000; the 100 most
  recent pages (published_at desc) are the actively merchandised stock, so
  the cap is accepted rather than crawling junk collections.
- **Reference price** = greatest(current RRP, 90-day snapshot high), with a
  $40 floor — deals on cheap items aren't worth tracking, but a big drop on
  an expensive item is always captured.

## What's left / deferred

- [ ] Amazon affiliate links + footer disclosure (waiting on Partner Tag;
      user said "later").
- [ ] Target via Commission Factory (waiting on user account).
- [ ] Big W and Target product images (media fields not yet extracted).
- [ ] Watch JB Hi-Fi's GitHub Actions runs for Cloudflare blocking of
      datacenter IPs (fallback: run it in the local task instead).
- [ ] Officeworks full SKU sweep continues incrementally via Actions.
- [ ] Telegram mailing-list/channel mode if the site gets an audience.
