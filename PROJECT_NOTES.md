# Dealwatch (Pricewatch) — project notes

*Last updated: 17 July 2026*

An AU retail price-anomaly tracker. Crawlers watch retailer catalogues,
an anomaly engine flags big price drops, and a public website shows the
deals. Everything runs on free tiers.

**Live site:** https://dealwatch.com.au (custom domain, live 17 July;
https://web-pi-blush-48.vercel.app still resolves to the same deployment)
**Repo:** https://github.com/JohanLiebert-2004/pricewatch (public)

The product was called "Underpriced" until 17 July 2026, when it was
rebranded to **Dealwatch** to match the purchased domain (commit `7245560`).
If "Underpriced" turns up anywhere in code, copy, or a commit message after
that point, it's a regression — grep the repo for it before trusting old
docs or memory.

## Architecture

```
GitHub Actions (hourly crawl matrix + detect job)
        │  scrape / refresh / crawl / detect
        ▼
Supabase Postgres (Sydney, ap-southeast-2)
   base tables locked by RLS; anon role can SELECT only 5 read views
        │  PostgREST (anon key, read-only)
        ▼
Vercel static site (web/ folder, no build step)  +  Telegram alerts
        │  /p/* rewrite + <img> loads
        ▼
OCI free-tier VM 159.13.59.184 (https://159-13-59-184.sslip.io, nginx + LE)
   pricewatch-web:  /p/<retailer>/<sku> SSR previews + /img proxy cache
   pricewatch-bot:  @underp22bot subscriptions (long polling)
```

**OCI VM services (added 2026-07-11):** the previously-parked VM now hosts
three always-on pieces GitHub Actions can't:
- **Telegram subscriptions** — visitors tap "Telegram alerts" on any product
  page → `t.me/underp22bot?start=i_<retailer>_<sku>` (every price change for
  that item) or `s_<retailer>` (every new deal at that store). The bot
  (`services/telegram_bot.py`, long polling — owns getUpdates, so `alerts.py
  whoami` conflicts while it runs) writes `telegram_subs`; fan-out runs in
  the existing Actions pipeline (`alerts.send_item_watch` in both crawl
  lanes + retailer subs inside `send_alerts`). Adding TELEGRAM_* secrets to
  the crawl matrix also fixed the jbhifi RAM watch, which had silently never
  fired from CI (its job had no token).
- **SSR previews** — `/p/<retailer>/<sku>` (Vercel rewrite → OCI) serves
  product.html with real title/OG/Twitter meta for crawlers and link
  previews; humans get the identical page (its JS also parses path params).
  The SSR template is the VM's repo clone of web/product.html — after
  changing it, `git pull` + restart `pricewatch-web` on the VM.
- **Image proxy** — `/img?u=` (host-whitelisted, 8MB cap, disk cache in
  /var/cache/pricewatch-img); every page's `thumbSrc()` routes through it.
Ops notes: services run as www-data off `/opt/pricewatch.env` (root:www-data
640); cert renewals via certbot.timer; VM iptables needed 80/443 opened
separately from the OCI security list (Oracle images REJECT-all by default);
Terraform now lifecycle-ignores instance `metadata` — without it, cloud-init
drift plans a full VM replacement.

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

### Backups and data-quality safeguards (2026-07-12)

- **Database backups:** OCI runs a daily PostgreSQL 17 `pg_dump` at 03:17 UTC.
  It uploads a custom-format dump and SHA-256 checksum to a private OCI Object
  Storage bucket. The VM uses an instance principal with upload access limited
  to that bucket; the lifecycle policy deletes backups after 30 days. The first
  backup was verified at 23 MB. A restore drill is still recommended before
  relying on the backup for incident recovery.
- **JB Hi-Fi RAM alerts:** the Shopify feed can retain delisted products. The
  special RAM watcher now live-checks the retailer page and price before sending;
  dead, unreadable, or mismatched listings are skipped. Alert links open the
  durable Underpriced history page rather than a retailer page that may expire.
- **Myer category correction:** bare `Ink` was incorrectly treated as a tech
  signal. Apparel matching now runs first and includes terms such as `chino`,
  `vest`, and `blouse`; printer ink requires a specific `ink cartridge` or
  `ink refill` phrase. Corrected 4,022 existing Myer clothing rows and
  refreshed the public feeds.

## Retailers covered (10)

| Retailer     | Method                                              | Notes |
|--------------|-----------------------------------------------------|-------|
| Kmart        | Constructor.io listing API                          | ~105k products; images captured |
| Big W        | `__NEXT_DATA__` category listings                   | Akamai; 2.5s delay after July soft-flag (owner floor 1.75s); no images yet |
| Officeworks  | Bulk price API by SKU list + sitemap                | images captured (s3 PIM) |
| Target       | Category listings                                   | Akamai; no images yet |
| JB Hi-Fi     | Shopify `/products.json` (250/page, 100-page cap = 25k most recent) | no bot protection; 1.0s delay; images via Shopify CDN; RAM/DDR/DIMM listings also get an independent Telegram ping on every price change (`alerts.send_ram_watch`), not just anomaly-engine deals |
| The Good Guys| Product sitemap (`product_sitemap_1-4.xml`, 8,629 URLs) + schema.org JSON-LD per page | headless Shopify Hydrogen, no `/products.json`; no bot protection; no RRP field anywhere (relies on 90-day history fallback); crawl batch raised to 500/run (vs default 40) since it's unprotected |
| Supercheap Auto | Full product sitemap (104 `sitemap_N-product.xml` files, ~518k URLs) + JSON-LD & GA4 dataLayer per product page | Salesforce Commerce Cloud; no bot protection; **full catalogue**, indexed 2026-07-08 after empirical storage testing showed it fits the free tier (~518k rows × ~336 bytes ≈ 166MB added, 292MB total DB); crawl_batch raised to 1000/cycle (~11-day first sweep); was-price = price + dataLayer `discount`; images via demandware.static. (SKUs are alphanumeric, e.g. `SPO81491` — the product URL regex must allow letters, not just digits, or most sitemap files silently match zero URLs.) |
| Sephora      | Storefront JSON:API (`/api/v2.6/products`, 500/page, ~16 requests for full AU catalogue) | shared Asia-Pacific backend behind Akamai — needs curl_cffi impersonation *and* `X-Platform: Web` + `X-Site-Country: AU` headers, or it silently serves a stale/wrong-country price book (observed PHP-peso catalogues, wrong prices) with no error; prices are integer cents; opted out of Telegram pings (too noisy) |
| Chemist Warehouse | Next.js `__NEXT_DATA__` per product page, seeded from products sitemap (~26k URLs) | commercetools backend; no bot challenge on impersonated requests; no fast listing path at all (robots.txt disallows `/api/`, category pages aren't SSR'd) so coverage is 100% crawl-queue lane, crawl_batch 500 (~26h first sweep); prescription items are skipped outright (PBS pricing isn't a "deal", and advertising Rx medicine prices is restricted in Australia) |
| Myer         | Sitemap (`sitemap_20251_N.xml.gz`, ~154k URLs) + schema.org JSON-LD per product page | Salesforce Commerce Cloud-style; no bot protection seen; sitemaps are served as **literal gzip bytes**, not an HTTP Content-Encoding, so the base scraper's text sitemap walker couldn't read them — added `BaseScraper.get_bytes()` + gzip-aware `discover()`/`discover_all()` overrides in `myer.py`; RRP comes from an embedded `"listPrice":N` field the existing `_find_rrp` fallback already matches, no `parse_product` override needed; crawl_batch 1000; first CI batch measured ~4.7s/URL end-to-end (politeness delay + Supabase round-trip), so the full first sweep is ~5-6 days, with ~94% of URLs storing a product (rest are stale sitemap entries); **sold-out items carry `price:"0"` in their JSON-LD** — 14 got ingested on day one and showed as fake "-100%" deals until $0 prices were rejected at parser/db/view level (commit 72e495f); added 2026-07-11 |

**Bunnings — investigated and ruled out** (July 2026): robots.txt disallows
`/api/` and `/apis/` (where any bulk endpoint would live), no product-level
sitemap (only category listings), and active Cloudflare Bot Management
fingerprinting on the very first plain request. Doesn't fit the no-evasion
policy. Revisit only if a public, non-disallowed data source turns up.

Politeness is non-negotiable: no delays below the owner-approved floors,
no parallel requests to one retailer. `Blocked` exceptions are expected
(datacenter IPs), not bugs — batches resume next run.

**Proxy (updated 2026-07-11):** a Webshare residential plan (1GB/mo) is
live, scoped to **Big W only** (`use_proxy = True` on bigw alone — Kmart/
Target's Akamai JS-challenge isn't IP-based so a proxy doesn't help them;
see CLAUDE.md). Current working `PROXY_URL` form:
`http://<user>-AU-rotate:<pass>@p.webshare.io:80` (AU-geo rotating), set in
local `.env`, the GitHub secret, and the VM's `/opt/pricewatch.env`.
**Ops lessons from the 2026-07-11 outage:** Webshare rotates the proxy
*username* when a plan changes — a stale credential gets HTTP 402
("payment required") on every CONNECT even while the dashboard shows the
plan active; and because `Blocked`/parse errors are tolerated by design,
Big W crawling can silently stall for days while every CI job still shows
"success". Diagnostics: suspiciously low GB usage on the Webshare
dashboard is a red flag, and the correct credentials can always be fetched
from the Webshare API (`WEBSHARE_API_KEY` in local `.env`;
`GET https://proxy.webshare.io/api/v2/proxy/config/`) rather than
eyeballing the dashboard. Delay floors and the one-request-at-a-time rule
are unchanged. Bunnings stays ruled out (Cloudflare fingerprinting, not
just IP reputation) — the proxy allowance doesn't reopen that.

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
  without JSON-LD (Officeworks, Big W, Kmart/Target's Constructor.io feed,
  Chemist Warehouse's `__NEXT_DATA__`) override `parse_product`; those with
  a bulk listing API (JB Hi-Fi) add `refresh_listings`. The Good Guys and
  Myer both use the base class's default JSON-LD parsing almost as-is
  (Good Guys needed a small override to fix the `is_marketplace`
  seller-name substring check; Myer needed none at all — its embedded
  `"listPrice"` field is already covered by the base `_find_rrp` fallback).
- `get_bytes()` — added 2026-07-11 for Myer: some retailers' sitemap files
  are literal gzip bytes rather than an HTTP Content-Encoding, so `get()`'s
  text decoding mangles them. Retailers with `.gz` sitemaps should override
  `discover`/`discover_all` to fetch via `get_bytes()` + `gzip.decompress()`
  (see `myer.py`) rather than relying on the base text-based walker.
- `Blocked` exception = bot protection triggered; expected, not a bug.

### Database (schema.sql + views.sql)
- Tables: `products` (incl. `image_url`, `category`, `subcategory`,
  `current_rrp`, `price_updated_at`), `price_snapshots` (only on change),
  `deals` (incl. `alerted_at`), `crawl_queue`, `kv`, `telegram_subs`.
- Anon read views (canonical SQL in `views.sql`, re-run after DDL changes,
  grants must be re-issued after a drop):
  - `deal_feed` — anomaly-engine deals (50%+), reference ≥ $40.
  - `discount_feed` — **materialized** view of every discounted product at
    any depth (reference = max(RRP, 90-day snapshot high), ≥$40 floor,
    price > 0, seen in last 10 days). Unique index on (retailer, sku);
    refreshed concurrently by every `detect`. Was a plain view but anon
    ilike queries hit Supabase's statement timeout re-aggregating 126k
    snapshots.
  - `product_search`, `catalogue_stats`, `growth_daily`,
    `subcategory_stats` (per-store chip labels + counts).

### Categories: shared buckets + per-store sections (2026-07-11)
Two-level system. `products.category` keeps the 8 shared title-guessed
buckets (tech/home/kitchen/toys/clothing/beauty/books/other) used for
all-stores browsing. `products.subcategory` holds **each store's own
sections**, which the site shows as the category chip row whenever exactly
one retailer is selected:
- **Native data** (populated inside the scraper, overwritten each sweep):
  Kmart = Constructor level-2 groups via `SECTION_LABEL` (merch
  pseudo-groups Brands/Clearance/Back To School/etc. map to None so they
  can't clobber real sections); Big W = category-tree top level (leaf paths
  now travel as `(path, top_title)` tuples); JB Hi-Fi = `product_type` via
  `_TYPE_SUBCAT`; Supercheap = GA4 `item_category2` (`item_category` is the
  useless "Shop by Category" root); Chemist Warehouse = product `type`,
  prettified.
- **Title rules** (`categorize.SUBCAT_RULES`, backfilled every `detect` via
  `backfill_subcategories`) for stores with no per-product category
  anywhere in their source: Myer, Good Guys, Officeworks, Target, Sephora.
  ~11.4k tagged at launch.
- PostgREST filter uses double-quoted `in.()` values because labels contain
  `&`/spaces: `subcategory=in.("Fridges %26 Freezers",...)`.
- Coverage note: native retailers only pick up tags as sweeps touch
  products — Kmart/JB fill within a run or two, Big W over days,
  Supercheap/Chemist Warehouse as their crawl queues recycle (~11d/instant
  respectively at next crawl). UI simply hides chips that have no data yet.

### Website (`web/`, static, Vercel)
- `index.html` — **deal feed as a card grid**: search bar, store chips (all
  10 retailers), category chips (dynamic: one store selected → that store's
  own sections from `subcategory_stats`, top 12; else shared buckets), sort
  dropdown, price min/max filter, 0–99% discount slider — all server-side
  via PostgREST, all composable. Card pills prefer `subcategory` over
  `category`. Retailer clicks clear both category selections (the chip row
  changes shape underneath them).
- `catalogue.html` — browse everything tracked; same dynamic per-store
  category chips (with counts), store/price filters + text search, exact
  counts via `Prefer: count=exact` (also forced whenever a subcategory chip
  is active, since the local stats cache only knows retailer×category).
- `search.html` — latest tracked price + "as of" date for any product by
  name/SKU. Runs **one query per retailer in parallel** (not one shared
  query) so a store with 100k+ products can't crowd smaller stores out of
  the results — fixed after a real bug where searching "ps5" only returned
  Kmart because the old single-query/recency-order approach starved JB
  Hi-Fi's matches.
- `growth.html` — per-day new products and price checks per retailer.
- `style.css` — current design (commit `f7f810d`, 9 July 2026): **BetsAPI-
  inspired blue palette** with nav icons — white cards, blue accent
  `#2563eb`, Figtree font, light-only (`color-scheme: light`; dark mode was
  explicitly rejected — never reintroduce without asking). This superseded
  the earlier "Bellroy" warm/orange v4, which superseded two rejected
  designs; the card-grid *layout* has been stable throughout, only the
  palette churns. The user redirects palettes by linking a reference site —
  ask for one before any visual work, and check `git log -- web/style.css`
  before trusting any doc/memory's claim about the current palette.
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
  crawl lane is per-retailer (`matrix.crawl_batch || 40`; raised for the
  four retailers with no bulk listing API and no bot protection — Good
  Guys 500, Chemist Warehouse 500, Supercheap 1000, Myer 1000). Runs
  hourly (was 30 min; halved 2026-07-12 for the Supabase egress budget —
  see the Egress section below). Detect job follows with Telegram secrets. First-run catalogue
  index line stays commented out (`index` was run once per retailer
  manually against Supabase instead — Good Guys 8,629 URLs, Chemist
  Warehouse ~26k, Supercheap ~518k, Myer ~154k).
- **Delisting confirmation reliability (2026-07-11):** `cmd_refresh`'s
  missing-SKU reconciliation (confirms a stale-looking product is really
  gone via a direct 404 before hiding it from the site) runs right after
  a retailer's bulk-listing budget is already spent, making it the part of
  the run most likely to hit a transient rate limit. A `Blocked` response
  there used to be treated identically to "inconclusive" and just skipped
  — so a genuinely delisted item (confirmed via a real 404, not a bot
  block) could sit on the public site indefinitely if every confirmation
  attempt happened to land on a rate limit. Found via a live bug report
  (a JB Hi-Fi RAM listing showing as a "76% off" deal after JB delisted
  it). Fixed with one retry-after-backoff before falling back to
  fail-open in `run.py`'s missing-SKU loop.

## Supabase egress budget (2026-07-12) — DO NOT regress this

Supabase emailed that the org blew the free tier's ~5GB/month egress
(grace until 11 Aug). Egress = bytes read OUT of Postgres; writes are
free. The stack was reading ~4.5GB/DAY, almost all of it the pipeline
re-downloading its own data. Measured with
`sum(pg_column_size(...))` and fixed in commit `2b792e3`:

| Offender | Was | Now |
|---|---|---|
| `anomaly.py` `SELECT * FROM products` per detect | 62MB/run | 1.6MB (seven explicit columns) |
| Cross-retailer matching (gtin/brand/title for every live product) | ~9MB every run | only in runs where UTC hour % 6 == 0; delisted rows excluded |
| `bulk_upsert` reading every swept row back to diff prices client-side | ~7MB per Kmart sweep | `_upsert_chunk_pg`: one CTE statement does upsert + old-price diff + snapshot insert server-side, returns changed keys only |
| `cmd_refresh` fetching sku+url for every tracked product | ~10MB/cycle | sku-only; URLs fetched just for the <= 15 delisting checks |
| Cron cadence | every 30 min | hourly |

Estimated ~0.13GB/day (~3.7GB/month) after. Rules of thumb this
encodes: never `SELECT *` from products/price_snapshots in anything that
runs on a schedule; push row-diffing into the database and return only
what changed (VALUES payloads in are free); prefer explicit column lists
sized with `pg_column_size` before adding a new scheduled read. In
`_upsert_chunk_pg`, both sides of the price comparison are cast to
`numeric(10,2)` — comparing REAL to numeric raw marks every row changed
and re-bloats snapshots. If usage still trends over: Pro plan ($25/mo)
or further cadence cuts.

## Custom domain, rebrand, and SEO (2026-07-17)

The user bought `dealwatch.com.au`. Wired up end to end:

- Added apex + `www` to the Vercel `web` project; DNS A records point at
  `76.76.21.21`. SSL issued and verified. `SITE_URL` updated in **two**
  separate places that don't share state — the GitHub Actions secret and
  `/opt/pricewatch.env` on the OCI VM — both had to be changed by hand.
- Renamed the product Underpriced → Dealwatch everywhere (see the note at
  the top of this file), including the `underpriced_recent` →
  `dealwatch_recent` localStorage key shared between `index.html` (reads)
  and `product.html` (writes) — these two must stay in sync if either
  changes again.
- Added `robots.txt`, a sitemap index (`web/sitemap.xml`) referencing a
  static `web/sitemap-pages.xml` and a **dynamic**
  `sitemap-products.xml` served from OCI's `preview_app.py` — deliberately
  capped at 5,000 URLs and disk-cached 6h so it doesn't reopen the egress
  problem above.
- Added canonical/Open Graph/Twitter/JSON-LD tags site-wide (`WebSite` +
  `SearchAction` on the homepage; `Product` + `Offer` on SSR product
  pages). `search.html` now actually handles `?q=` so the SearchAction
  isn't decorative.
- Fixed a real bug found while doing this: internal links from the
  homepage/catalogue/search cards pointed at
  `product.html?retailer=&sku=` — a client-rendered URL with **no**
  server-side meta tags — instead of the SSR'd `/p/:retailer/:sku` route.
  Google crawls what's actually linked on the page, so this meant every
  discovered product URL had an empty JS-shell title/description. All
  internal links now point at `/p/:retailer/:sku`.
- Google Search Console submission is still unstarted — needs the user's
  own Google account. Ranking for competitive terms takes months of
  authority-building beyond what's fixed here; that's a separate,
  longer-running effort.

**Also found and fixed the same day:** the OCI VM was several commits
behind (`git pull` had not been run there since an earlier session), so it
was silently serving the old brand and the old `SITE_URL` even after the
repo and Vercel deploy looked current. The VM does not auto-deploy — see
`AGENT_STATE.md`'s handoff notes for the check-before-you-trust-it rule.

**Also fixed:** `scrapers/base.py`'s default JSON-LD parser never read a
product's `image` field, so Myer (which has no bulk-listing lane — its
`refresh` step is a no-op) had zero product images, ever. Added a small
`_image()` helper and wired it in; existing rows self-heal via the
`COALESCE(excluded.image_url, products.image_url)` already in both upsert
paths, verified live against a real crawl batch. Big W's bulk
`refresh_listings` lane builds records from listing JSON directly and
still bypasses this fix — flagged as task P9 in `AGENT_STATE.md`, not
patched blind.

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
- [ ] Myer first sweep in progress (~5-6 days at real CI pace, ~4.7s/URL):
      first batch healthy — ~94% store rate, individual failures only (no
      block pattern), $0 sold-out placeholders now rejected. Nothing to do
      unless the store rate drops sharply on later batches.
- [ ] User to test the Telegram bot end-to-end: open @underp22bot via a
      product page's subscribe button (I can't message the bot as them).
      Bot service is polling and healthy; only the human tap is unverified.
- [ ] Personalization (user asked 2026-07-11 "use cookies to show things
      based on browsing history"): recommended NO tracking cookies —
      instead (a) localStorage recently-viewed row + category-weighted
      deal ordering, all client-side, and/or (b) anonymous server-side
      "trending searches" counts. User was asked which to build; **no
      answer yet** — don't start until they pick.
- [ ] Watch native subcategory fill-rates: Kmart/JB should be tagged after
      their next 1-2 CI sweeps, Big W within days, Supercheap/Chemist
      Warehouse as crawl queues recycle. If a store's chips look thin
      after that window, check its scraper's subcategory plumbing.
- [ ] Watch JB Hi-Fi's GitHub Actions runs for Cloudflare blocking of
      datacenter IPs (if it starts, fall back to the OCI VM — NOT a local
      scheduled task: the old "Pricewatch BigW refresh" Windows task was
      removed 2026-07-11 at the user's request, local_refresh.ps1 deleted;
      nothing may crawl from the user's PC).
- [ ] Officeworks full SKU sweep continues incrementally via Actions.
- [ ] Telegram mailing-list/channel mode if the site gets an audience.
- [ ] Revisit Bunnings if a non-robots-disallowed data source appears.
- [ ] Consider whether `MISSING_CHECK_BUDGET` (15/run) is still enough
      headroom now that JB Hi-Fi has 700-1800 stale-tracked SKUs at any
      time (backlog roughly break-even with the check rate) — the
      2026-07-11 retry fix addresses false-negatives, not throughput;
      only worth revisiting if delisted items are still lingering days
      after being confirmed gone.
