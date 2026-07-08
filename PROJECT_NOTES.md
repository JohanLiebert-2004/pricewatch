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
        │  scrape / refresh / crawl / detect
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
- **Domain:** still on the free `web-pi-blush-48.vercel.app` URL. User plans
  to buy a real domain later and point it at Vercel via its native custom-
  domain support — no need to migrate off Vercel for this, it's free either
  way. Nothing to do until the user picks a name.

## Retailers covered (7)

| Retailer     | Method                                              | Notes |
|--------------|-----------------------------------------------------|-------|
| Kmart        | Constructor.io listing API                          | ~105k products; images captured |
| Big W        | `__NEXT_DATA__` category listings                   | Akamai; 2.5s delay after July soft-flag (owner floor 1.75s); no images yet |
| Officeworks  | Bulk price API by SKU list + sitemap                | images captured (s3 PIM) |
| Target       | Category listings                                   | Akamai; no images yet |
| JB Hi-Fi     | Shopify `/products.json` (250/page, 100-page cap = 25k most recent) | no bot protection; 1.0s delay; images via Shopify CDN |
| The Good Guys| Product sitemap (`product_sitemap_1-4.xml`, 8,629 URLs) + schema.org JSON-LD per page | headless Shopify Hydrogen, no `/products.json`; no bot protection; no RRP field anywhere (relies on 90-day history fallback); crawl batch raised to 500/run (vs default 40) since it's unprotected — first full sweep ≈9h instead of ~4.5 days |
| Supercheap Auto | Deal/clearance category pages (server-rendered, first page only) + JSON-LD & GA4 dataLayer per product page | Salesforce Commerce Cloud; no bot protection; **clearance-focused by design** — full catalogue is ~518k auto parts (won't fit free-tier DB) and robots.txt disallows pagination params (`start=`, `sz=`, `format=ajax`), so only the server-rendered `/clearance` page (~34 rotating items/cycle, accumulating in the queue) is enumerated — other deal pages build their grids client-side; was-price = price + dataLayer `discount`; images via demandware.static |

**Bunnings — investigated and ruled out** (July 2026): robots.txt disallows
`/api/` and `/apis/` (where any bulk endpoint would live), no product-level
sitemap (only category listings), and active Cloudflare Bot Management
fingerprinting on the very first plain request. Doesn't fit the no-evasion
policy. Revisit only if a public, non-disallowed data source turns up.

Politeness is non-negotiable: no delays below the owner-approved floors,
no parallel requests to one retailer. `Blocked` exceptions are expected
(datacenter IPs), not bugs — batches resume next run.

**Proxy policy (updated 2026-07-08):** residential/rotating proxies are now
allowed for the three Akamai-fronted retailers (Big W, Kmart, Target) to
cut down on datacenter-IP flags, since the goal is a real production
service rather than a hobby deployment. `scrapers/base.py` supports a
`PROXY_URL` env var + per-scraper `use_proxy` flag (all three already set);
still needs a provider account + `gh secret set PROXY_URL` before it does
anything. Delay floors and the one-request-at-a-time rule above are
unchanged. Bunnings stays ruled out for now (Cloudflare fingerprinting,
not just IP reputation) — this proxy allowance doesn't reopen that.

## What's built

### Data pipeline (`run.py` subcommands)
- `index` — load a retailer's full catalogue URLs into `crawl_queue`.
- `crawl` — work the queue oldest-first, resumable. Batch size is set per
  retailer in `crawl.yml` (`matrix.crawl_batch`, defaults to 40; Good Guys
  uses 500 since it has no bot protection to be cautious of).
- `refresh` — fast bulk price sweep via listing/API pages; snapshots only
  written when a price actually changed. $40 keep-floor for *new* items;
  already-tracked items always stay refreshed. No-ops for retailers with
  no `refresh_listings` method (currently just Good Guys).
- `detect` — categorise new products, run the anomaly engine (records 50%+
  drops as `deals`), send Telegram alerts, then refresh the `discount_feed`
  materialized view (~0.5s).
- `url` / `scrape` / `deals` — one-off ingest, smoke-test scrape, list deals.

### Scraper pattern (`scrapers/`)
- `base.py` — `BaseScraper`: generic sitemap discovery (`discover`/
  `discover_all`) + generic schema.org JSON-LD `parse_product`. Retailers
  without JSON-LD (Officeworks, Big W, Kmart/Target's Constructor.io feed)
  override `parse_product`; those with a bulk listing API (JB Hi-Fi) add
  `refresh_listings`. The Good Guys is the first retailer to use the base
  class's default JSON-LD parsing almost as-is (small override just to fix
  the `is_marketplace` seller-name substring check and add `image_url`,
  which the base default doesn't set).
- `Blocked` exception = bot protection triggered; expected, not a bug.

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
- `index.html` — **deal feed, redesigned as a card grid** (v4, see Design
  system below): search bar, store chips (incl. Good Guys), category chips,
  sort dropdown (biggest discount / price asc / price desc / newest drops),
  price min/max filter, 0–99% discount slider, Everything/Error-tier/
  Marketplace type chips — all server-side via PostgREST, all composable.
- `catalogue.html` — browse everything tracked; store/category/price
  filters + text search, exact counts via `Prefer: count=exact`.
- `search.html` — latest tracked price + "as of" date for any product by
  name/SKU. Runs **one query per retailer in parallel** (not one shared
  query) so a store with 100k+ products can't crowd smaller stores out of
  the results — fixed after a real bug where searching "ps5" only returned
  Kmart because the old single-query/recency-order approach starved JB
  Hi-Fi's matches.
- `growth.html` — per-day new products and price checks per retailer.
- `style.css` — **design system v4** ("Bellroy palette", July 2026):
  light-only (no dark mode — an earlier dark variant was explicitly
  rejected; `color-scheme: light` forces native controls light too), warm
  off-white ground (`#faf9f7`), charcoal ink, burnt-orange accent
  (`#d3572b`), flat hairline borders (no heavy shadows), Figtree font.
  Chosen by building 3 live sample pages (card grid / dark terminal /
  warm editorial) and letting the user pick, rather than guessing —
  see the design-preferences memory for why that approach works better
  here. v1–v3 (serif+grain, then cool-neutral dark-capable) were both
  superseded; don't resurrect dark mode without asking again.
- Thumbnails are downsized at render time (`thumbSrc()`): Shopify
  `?width=200/300/400`, Officeworks `JPEG_300x300`, Kmart
  `width:200,height:250`. Good Guys images are on `cdn.shopify.com` too,
  so they're covered by the existing Shopify branch with no extra code.

### Telegram alerts (`alerts.py`)
- After every `detect`, deals with score ≥ 0.80 **and** reference ≥ $100
  get a message to the owner's personal chat (bot @underp22bot); stamped
  `deals.alerted_at` so each fires once. `python alerts.py whoami|test`.
- Public channel broadcasting was considered and deferred ("keep it
  personal for now").

### CI (`.github/workflows/crawl.yml`)
- Matrix: one refresh + crawl job pair per retailer, batch size for the
  crawl lane is per-retailer (`matrix.crawl_batch || 40`). Runs every 30
  min. Detect job follows with Telegram secrets. First-run catalogue index
  line stays commented out (`index` was run once per retailer manually
  against Supabase instead, including Good Guys's 8,629-URL seed).

## Key decisions

- **Amazon: no scraping.** Their Conditions of Use forbid it and it would
  require bot-detection evasion. Route chosen: Product Advertising API —
  Associates account approved (ID `tarunsudan`), but PA-API access now
  requires **10 qualifying sales in the trailing 30 days**. Agreed fallback
  (deferred): tagged "Check Amazon" links + the required "As an Amazon
  Associate I earn from qualifying purchases" disclosure. Keepa (~€49/mo)
  noted as a paid alternative.
- **Bunnings: ruled out**, see retailer table above.
- **Target expansion** via Commission Factory affiliate feed — designed,
  blocked on user signup.
- **"Only 5 deals" problem**: the anomaly engine only records 50%+ drops by
  design; the fix was the products-level `discount_feed` (~1,500+ items ≥1%
  off) plus the slider, not loosening the engine.
- **JB Hi-Fi 25k window**: Shopify caps page×limit at 25,000; the 100 most
  recent pages (published_at desc) are the actively merchandised stock, so
  the cap is accepted rather than crawling junk collections.
- **Reference price** = greatest(current RRP, 90-day snapshot high), with a
  $40 floor — deals on cheap items aren't worth tracking, but a big drop on
  an expensive item is always captured. Retailers with no RRP field at all
  (Good Guys) rely entirely on the 90-day history side of that formula.
- **Design process**: when the user rejects a redesign ("looks cheap/AI
  made" happened twice), build small *live* sample pages hitting real data
  and let them pick, instead of iterating blind on describing colours in
  words. This is how v4 (Bellroy palette, card grid) was reached in two
  rounds instead of many.
- **Custom domain**: Vercel supports it natively on the free tier — no
  reason to move off Vercel. Just needs the user to pick and buy a name.

## What's left / deferred

- [ ] User to pick a domain name; then point DNS at Vercel (still free).
- [ ] Amazon affiliate links + footer disclosure (waiting on Partner Tag;
      user said "later").
- [ ] Target via Commission Factory (waiting on user account).
- [ ] Big W and Target product images (media fields not yet extracted).
- [ ] Watch Good Guys's first full crawl sweep (~9h at the raised batch
      size) for any blocking — none seen in testing, but unverified at
      full scale/CI network egress.
- [ ] Watch JB Hi-Fi's GitHub Actions runs for Cloudflare blocking of
      datacenter IPs (fallback: run it in the local task instead).
- [ ] Officeworks full SKU sweep continues incrementally via Actions.
- [ ] Telegram mailing-list/channel mode if the site gets an audience.
- [ ] Revisit Bunnings if a non-robots-disallowed data source appears.
