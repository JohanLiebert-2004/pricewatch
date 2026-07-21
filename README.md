# Dealwatch

[Dealwatch](https://dealwatch.com.au) is an Australian retail price-drop and
clearance discovery service. It tracks current prices, retailer reference
prices, and price history so shoppers can find unusually large discounts
without treating every sale badge as a genuine deal.

The repository contains the retailer crawlers, anomaly engine, PostgreSQL
schema and public views, static website, server-rendered product and landing
pages, alerting services, and deployment infrastructure.

## What it does

- Tracks catalogue prices across 13 Australian retailers.
- Detects prices at least 50% below a retailer reference price, recent
  historical median, or matched cross-retailer median.
- Separates retailer-labelled clearance products from inferred deals.
- Publishes searchable deal, catalogue, retailer, category, product-history,
  growth, and clearance pages.
- Supports email and Telegram price-watch notifications.
- Generates crawlable product pages and product sitemaps for search engines.
- Optionally builds self-hosted product embeddings for similar-item results.

Drops of 80% or more are treated as error-tier candidates. They still need
normal shopper judgement: stock, delivery, marketplace sellers, stale pages,
and retailer pricing-error policies can all affect whether an order proceeds.

## Current architecture

```text
Australian retailer sites
        |
        v
Python refresh and crawl jobs ----> PostgreSQL
        |                               |
        |                               v
        |                          PostgREST API
        |                               |
        v                               v
alerts and detection             Vercel static site
                                        |
                                        v
                         OCI SSR pages and product sitemaps
```

The static frontend lives in `web/` and is deployed on Vercel. Same-origin
rewrites route public data requests to self-hosted PostgREST and dynamic
product, retailer, category, and sitemap requests to `services/preview_app.py`.
Scheduled collection and detection are defined in `.github/workflows/crawl.yml`,
with retailer-specific sweeps used where a normal hosted runner is unsuitable.

PostgreSQL is the production database. Local development automatically falls
back to `pricewatch.db` (SQLite) when `DATABASE_URL` is not set.

## Retailers

The scraper registry currently includes:

| Retailer | Registry name |
|---|---|
| BIG W | `bigw` |
| Kmart | `kmart` |
| Target | `target` |
| Officeworks | `officeworks` |
| JB Hi-Fi | `jbhifi` |
| The Good Guys | `goodguys` |
| Myer | `myer` |
| Supercheap Auto | `supercheap` |
| Sephora | `sephora` |
| Chemist Warehouse | `chemistwarehouse` |
| Booktopia | `booktopia` |
| QBD Books | `qbd` |
| IKEA | `ikea` |

Retailer access can change without notice. Some sites require slow crawl
delays, a seeded sitemap queue, or a retailer-specific network path; a blocked
batch stops cleanly and resumes on a later run.

## Local setup

Python 3.12 is used in automation.

```bash
python -m venv .venv
python -m pip install -r requirements.txt
```

The SSR service and focused tests also require FastAPI and Uvicorn:

```bash
python -m pip install fastapi uvicorn
```

Activate the virtual environment using the command for your shell, then run a
small Officeworks sample:

```bash
python run.py scrape officeworks --limit 5
python run.py detect
python run.py deals
```

With no `DATABASE_URL`, these commands use the local SQLite database. The
database and `.env` files are intentionally ignored by Git.

## Catalogue workflow

Seed sitemap-only retailers once, then process their queues in resumable
batches:

```bash
python run.py index officeworks
python run.py crawl officeworks --batch 500
python run.py detect
```

For retailers with an efficient listing or catalogue endpoint, `refresh`
updates many products without fetching every product page:

```bash
python run.py refresh officeworks --budget 100
```

Useful CLI commands:

| Command | Purpose |
|---|---|
| `scrape <retailer> --limit N` | Discover and ingest a small sample. |
| `index <retailer>` | Seed the full crawl queue from retailer sitemaps. |
| `crawl <retailer> --batch N` | Process the oldest or never-scraped queue rows. |
| `refresh <retailer> --budget N` | Run the retailer's fast catalogue refresh path. |
| `url <product-url>` | Ingest one supported product URL. |
| `detect` | Score changed products and update deals and public views. |
| `deals` | Print current detected deals. |

Use `all` where supported to operate on every registered retailer. Full
catalogue jobs can take hours or days; do not remove delays or parallelise
requests to the same retailer.

## Configuration

Runtime configuration is supplied through environment variables. Important
ones include:

| Variable | Used for |
|---|---|
| `DATABASE_URL` | PostgreSQL connection; omit for local SQLite. |
| `PROXY_URL` | Approved retailer-specific proxy path; currently scoped by scraper policy. |
| `PRICEWATCH_API_URL` | PostgREST origin used by the SSR service. |
| `SITE_URL` | Canonical public site URL. |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Telegram alert delivery. |
| `RESEND_API_KEY`, `RESEND_FROM` | Email price-watch delivery. |

Never commit `.env`, database credentials, API tokens, proxy credentials,
Terraform state, or private keys.

For a new PostgreSQL database, apply `schema.sql` first and `views.sql`
second. The optional similarity pipeline additionally uses
`requirements-embed.txt` and `embed_products.py`.

## Verification

Run the focused unit tests:

```bash
python -m unittest discover -s tests -v
```

A safe local smoke check is:

```bash
python run.py scrape officeworks --limit 5
python run.py detect
python run.py deals
```

Live retailer requests are inherently less deterministic than unit tests, so
an explicit block response should be treated as an operational condition
unless parsing or persistence also fails.

## Repository map

```text
run.py                       command-line entry point
db.py                        SQLite/PostgreSQL storage layer
anomaly.py                   deal scoring and detection
scrapers/                    retailer discovery, refresh, and product parsing
schema.sql                   production database schema
views.sql                    public feeds, materialized views, and RPCs
web/                         static Dealwatch frontend and Vercel configuration
services/preview_app.py      SSR product/landing pages and dynamic sitemaps
services/telegram_bot.py     Telegram subscriptions and notifications
embed_products.py            optional product-embedding batch job
.github/workflows/crawl.yml  scheduled crawling, embeddings, detection, alerts
infra/oci/                   Terraform and systemd service definitions
tests/                       focused automated tests
```

For the latest deployment state and active handoffs, read `AGENT_STATE.md`.
Historical deployment documents may describe superseded infrastructure, so
verify them against that current state before operating production.

## Responsible operation

Keep the configured rate limits, honour retailer robots rules, avoid concurrent
requests to one retailer, and prefer official affiliate feeds or APIs when
available. Dealwatch surfaces public price observations; it does not guarantee
stock, price accuracy, or retailer acceptance of an order.

## Claude handover: live site and OCI audit

Snapshot taken **21 July 2026 AWST**. This is a point-in-time operational
handover, not a replacement for `AGENT_STATE.md`. Re-check external state before
acting, preserve the existing uncommitted P16 SEO work, and do not print or copy
runtime secrets while diagnosing hosts.

### Verified live state

| Area | State at audit | Evidence |
|---|---|---|
| Public website | Healthy | The apex, catalogue, clearance, search, robots, sitemap index, product sitemap, one SSR product page, category landing page, and retailer landing page all returned HTTP 200. `www` returned one permanent redirect to the apex. |
| Public data API | Healthy | Same-origin PostgREST returned HTTP 200 with current catalogue data. |
| Mobile quality | Good, with targeted fixes | Lighthouse: Performance 92, Accessibility 96, Best Practices 100, SEO 100; FCP/LCP 2.7 s, TBT 0 ms, CLS 0.005. |
| GitHub collection | Working but over cadence | The latest two completed workflows succeeded and the current run was active. Four preceding scheduled runs were cancelled after long runtimes; slow retailer jobs can exceed the hourly schedule. |
| OCI web/SSR host | Core services healthy | `pricewatch-web` and `pricewatch-bot` were active; Kmart and backup timers were active; about 308 MB RAM and 37 GB disk were available. `pricewatch-embed` was not installed/active. |
| OCI DB host | Healthy but small | PostgreSQL, PostgREST, and nginx were active; local PostgREST returned 200; the daily backup completed successfully and uploaded a 418 MB dump; about 415 MB RAM and 37 GB disk were available. |
| Intended ARM DB host | Not provisioned | Terraform state contains the original web host and the x86 DB fallback, but no `pricewatch_db` ARM instance. Four capacity-retry windows have exhausted without success. The x86 host is production. |

The public freshness view showed uneven coverage. Good Guys, Sephora, and
Supercheap were approximately 99–100% fresh, while BIG W was 37/19,962
(0.2%), Kmart 5,200/82,092 (6.3%), Chemist Warehouse 136/2,220 (6.1%),
Myer 13,406/112,593 (11.9%), and Target 81/299 (27.1%). This metric counts
products seen within 36 hours; a low percentage is a coverage warning, not
proof that every older product is invalid.

### Confirmed problems and risks

1. **Cloud-init is invalid.** `infra/oci/cloud-init.yaml.tftpl` fails YAML
   parsing at the unindented `/opt/pricewatch.env` heredoc (`DATABASE_URL=`,
   `PROXY_URL=`, and following lines). `terraform validate` still passes
   because it does not parse rendered cloud-init YAML. The original web host
   has `cloud-final.service` failed from its first boot.
2. **Terraform cannot rebuild production.** All three instance resources use
   the same crawler-oriented cloud-init. It does not install/configure the
   PostgreSQL server, PostgREST, nginx/TLS, roles, schema/views, restore flow,
   or committed service units that make the manually configured DB host work.
   Split provisioning by role and add a rendered-cloud-init validation test
   before treating disaster recovery as automated.
3. **The capacity-blocked ARM resource is unconditional.** A normal apply
   continues to include `oci_core_instance.pricewatch_db`. Add an explicit
   enable/count switch and make its outputs and backup dynamic-group membership
   conditional. Do not launch a fifth retry burst without the owner's approval.
4. **Crawler coordination is degraded.** The local BIG W scheduled task is
   absent on this Windows host and its last log entry is from 18 July, so BIG W
   currently relies on the six-hour proxy fallback. Old Kmart VM runs timed out
   at 1,800 seconds; the 5,400-second fix is deployed and its first longer run
   was still in progress during this audit. CI also ran Kmart successfully
   while that VM run was active because no fresh heartbeat existed, temporarily
   violating the one-lane-per-retailer rule. Confirm one clean VM heartbeat,
   then choose exactly one primary Kmart lane.
5. **The hourly workflow is longer than an hour.** A recent successful run
   took about 93 minutes; Myer alone spent about 89 minutes in enrichment and
   Supercheap about 69 minutes. Split fast refresh/detect from deep sitemap
   enrichment, stagger slow retailers, or lower their batches. Detection should
   not wait for every slow enrichment job.
6. **Network roles are not isolated.** One shared security list applies to all
   instances and exposes ports 80, 443, and 5432 publicly. The preferred fix is
   per-role NSGs plus DB-writing jobs inside the OCI private network (or through
   an authenticated tunnel), then closing public PostgreSQL. Keep SCRAM/TLS,
   least-privilege roles, connection monitoring, and tested backups as defence
   in depth.
7. **Endpoints are hardcoded.** Public/private IPs appear in `web/vercel.json`,
   nginx and systemd templates, `services/preview_app.py`, and the Kmart sweep.
   Use reserved addresses or owned DNS names, feed endpoints from Terraform or
   deployment configuration, and proxy `/img` through the Dealwatch origin.
8. **Repository docs and unit state contain drift.** `infra/oci/README.md`
   still describes the old Supabase/crawler-only design. The retired
   `pricewatch-cycle.service` remains failed although its timer is correctly
   disabled. Remove or reset obsolete units after recording why they are off.

### Website improvements, in order

1. Fix accessible contrast for `--flag` usage. Lighthouse measured only 3.67:1
   for white text on `#3b82f6` and 3.35:1 for track-link text on the soft blue
   background. This affects the hero search button, selected filter chips,
   eyebrow text, and price-tracking links.
2. Add explicit `width` and `height` (or an intrinsic `aspect-ratio`) to
   product-card and recent-item images. This removes the unsized-image audit
   and makes loading more predictable.
3. Reduce initial render work. The homepage fetches and paints up to 60 deal
   cards and Lighthouse attributed most main-thread work to rendering/layout.
   Render a smaller first page, then append on demand or with an intersection
   observer.
4. Consolidate the six first-load API calls into one small homepage-bootstrap
   RPC/endpoint, or defer freshness, subcategories, and trending data until the
   main deals render. This reduces connection and layout fan-out.
5. Self-host/subset Figtree or preload the exact font asset; Google Fonts was
   the largest render-blocking dependency. Keep the system-font fallback.
6. Deploy the pending P16 product-cache change. Production SSR product pages
   still returned `Cache-Control: no-store`; the working tree intentionally
   changes them to a short CDN cache and improves SSR/SEO/sitemap behaviour.
7. Serve the image proxy at same-origin `/img` instead of exposing the OCI
   `sslip.io` host in every card. This simplifies CSP, hides backend addressing,
   and makes future host migration independent of frontend HTML.

### Suggested order for Claude

1. Preserve and finish/deploy P16; verify one real product page, landing page,
   and product sitemap with the exact browser request headers.
2. Stabilise BIG W and Kmart ownership, then split slow deep crawls away from
   hourly refresh/detect. Verify two consecutive scheduled workflows.
3. Fix and test rendered cloud-init, introduce role-specific provisioning and
   an ARM enable flag, then update `infra/oci/README.md` to match reality.
4. Add per-role network controls and remove hardcoded addresses before any host
   replacement or ARM migration.
5. Apply the contrast, image-sizing, initial-render, API-bootstrap, and font
   improvements; rerun mobile Lighthouse and retain the current 100 SEO score.
6. Keep the x86 DB host until a replacement is restored, checksummed, compared,
   and cut over with a rollback path. Do not destroy either fallback during the
   first migration pass.
