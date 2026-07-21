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
 .github/workflows/crawl.yml  hourly refresh, embeddings, detection, alerts
 .github/workflows/enrich.yml slower product-page enrichment every three hours
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

Initial snapshot taken **21 July 2026 AWST**, followed by remediation later the
same day. The original findings are retained below for audit history; the final
Claude handover at the end of this section supersedes their old status. Re-check
external state before acting and do not print or copy runtime secrets while
diagnosing hosts.

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

### Original audit findings (historical)

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

### Update — later 21 July (Claude), against the items above

- **#3 (ARM retry):** owner approved another attempt after this audit; a
  fresh bounded retry window was relaunched (see `AGENT_STATE.md` for the
  outcome - do not assume success without checking there).
- **#4 (Big W/Kmart):** Big W's local sweep really was never firing on its
  own schedule (Task Scheduler had it `Disabled`, `LastRunTime` at the
  Windows epoch default) - re-enabled and verified with a real run (168
  kept, 110 snapshots, fresh heartbeat written). Kmart's VM sweep was
  hitting its own 1,800s internal timeout before it could ever write a
  heartbeat (real progress, just never reaching the success line) - raised
  to 5,400s; first full-length run's outcome is in `AGENT_STATE.md`, not
  re-asserted as fixed here without that confirmation. The CI/VM one-lane
  overlap this audit flagged should resolve on its own once a clean
  heartbeat lands, since the CI cadence gate already keys off it.
- **#6 (network isolation) - partially mitigated, not closed.** Auth was
  already strict (SSL+SCRAM only); added `%h` to Postgres's
  `log_line_prefix` and installed `fail2ban` on both hosts (custom
  `pricewatch-postgres` jail: 5 failed attempts/10min → 24h ban, plus the
  default `sshd` jail) - this stops the free brute-force attempts already
  visible in the logs, but **5432 is still publicly reachable**. Actually
  closing it needs a self-hosted GH Actions runner in the VCN or a tunnel
  CI joins before connecting - real new infrastructure, intentionally left
  for the owner to choose rather than picked here.
- **#8 (unit drift) - partially fixed.** `pricewatch-cycle.service`'s stale
  failed status was cleared (`systemctl reset-failed`; the timer was
  already correctly disabled, this was purely cosmetic). `infra/oci/README.md`
  still describes the old design - not touched.
- **New: a full local config backup** now exists at
  `C:\Users\tarun\Downloads\pricewatch_4\oci-backup\` (sibling to this repo,
  not inside it - real secrets), covering every systemd unit actually
  deployed on both boxes, `postgresql.conf`/`pg_hba.conf`, the nginx vhost,
  hand-added `iptables` rules, and a Terraform state snapshot. This does
  **not** close gap #1/#2 (Terraform/cloud-init still can't rebuild either
  box from scratch) - it's a faster manual-recovery reference until that
  automation exists, not a substitute for fixing it.
- **Not yet touched:** #1/#2 (cloud-init/Terraform rebuild), #5 (hourly
  workflow overrun - Myer/Supercheap enrichment still the long pole), #7
  (hardcoded endpoints), the website performance/accessibility list below.

### Original website improvement list (completed)

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

### Original suggested order (remediated; live follow-up below)

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

### Final state for Claude handover — 21 July 2026

This supersedes the original audit and the intermediate Claude update above.
The remediation is in commits `bccb32c` through `9056b64`.

- **Website:** deployed to `dealwatch.com.au`. The homepage uses one
  `homepage_bootstrap()` RPC plus its paged deal feed, renders 24 initial
  cards, supports Load More, serves product images through same-origin `/img`,
  uses system fonts, and includes intrinsic image dimensions. Final mobile
  Lighthouse is **96 Performance / 100 Accessibility / 100 Best Practices /
  100 SEO** (FCP 1.1 s, LCP 2.7 s, TBT 70 ms, CLS 0). The SSR `/healthz`,
  homepage RPC, image proxy, and a real Officeworks product page returned 200.
- **Crawler correctness:** PostgreSQL bulk upserts explicitly cast every
  input column and Kmart normalises Constructor brand values to text, removing
  the mixed numeric/string `smallint` inference failure. Five focused unit
  tests pass, including the regression case.
- **Workflow ownership:** hourly `crawl.yml` now contains only bulk refresh,
  embeddings, and detection. Slow product enrichment is in `enrich.yml` every
  three hours with `max-parallel: 3`; detection no longer waits for those
  jobs. Failures are visible, and heartbeat checks prevent healthy local/OCI
  owners from being duplicated by CI.
- **Private database path:** PostgreSQL port 5432 is VCN-only. GitHub Actions
  uses a retrying SSH local forward through a dedicated `ci-tunnel` identity
  restricted to the DB private address and denied shell/TTY access. Public
  SSH remains necessary for dynamic hosted-runner addresses and is protected
  by keys-only authentication and fail2ban.
- **OCI/Terraform:** web and DB roles now have separate valid cloud-init
  templates. DB bootstrap installs PostgreSQL 17, pgvector, pinned PostgREST
  14.15, roles, nginx, schema/views, and finalisation tooling. ARM creation is
  opt-in (`enable_arm_db = false`); the x86 DB remains production. Source-image
  and first-boot cloud-init drift cannot replace live instances. The final
  live Terraform plan reported **No changes**.
- **Configuration drift:** runtime endpoints moved to environment/deployment
  configuration where possible, `/img` is same-origin, the obsolete
  hardcoded nginx file was removed, `infra/oci/README.md` describes the
  current two-role stack, and `CLAUDE.md` no longer instructs agents to deploy
  the retired Supabase architecture.
- **Kmart production proof:** The last failure at 09:44 UTC belonged to the
  sweep process that began at 08:23, before the corrected code was deployed,
  so it still raised the old `Formula 10.0.6` type error. A new sweep began
  immediately at 09:44 on deployed commit `5abd497` (which contains the
  `bccb32c` cast fix) and was still active, without a new error, at the
  10:23 UTC handover. It has not yet written `kmart_vm_heartbeat`; Claude
  must verify its eventual exit and heartbeat before calling Kmart recovered.

#### Pending live verification for Claude

1. Watch `pricewatch-kmart.service` and `/var/log/pricewatch-kmart.log` on
   the OCI web host. Confirm the 09:44 UTC run exits 0, reports listings
   seen/kept, and writes `kmart_vm_heartbeat`. If it fails, diagnose that new
   failure; do not confuse it with the 08:23 pre-deploy process reported at
   09:44.
2. Watch enrichment run
   `https://github.com/JohanLiebert-2004/pricewatch/actions/runs/29820953050`.
   It uses the final tunnel retry, `max-parallel: 3`, and action-v7 workflow.
   At handover Big W, Target, and Officeworks had succeeded; Good Guys, Myer,
   and Supercheap were active with successful private tunnels.
3. After the first Kmart heartbeat is present, run or observe one complete
   hourly `crawl.yml`. Kmart must skip via its cadence gate, all other refresh
   jobs should finish, and detection should succeed. A run started before the
   first heartbeat can still duplicate the long VM sweep; if that bootstrap
   race recurs, add a short-lived VM-start lease marker rather than masking
   failures or reopening PostgreSQL.

Do not retry ARM capacity, replace either OCI host, reopen PostgreSQL, or
restore the retired combined crawler timer. For future work, start with
`AGENT_STATE.md`, inspect the latest scheduled workflow and freshness rows,
and remember that pushing `master` does not automatically update OCI.
