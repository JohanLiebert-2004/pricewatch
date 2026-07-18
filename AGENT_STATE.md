# Pricewatch — shared agent state

*Updated: 18 July 2026 AWST. This is the durable summary for Codex and Claude;
it deliberately records decisions and outcomes, not chat transcripts or secrets.*

## Start here

Before changing anything:

1. Read `CLAUDE.md`, this file, and `AGENT_PROTOCOL.md`.
2. Run `git status -sb` and `git log --oneline -5`.
3. Claim one unowned task below before editing. Do not edit another claimed
   task's files.
4. Never print, copy, or commit `.env`, Terraform state, SSH keys, tokens, or
   secret values. See `CLAUDE.md` for the authoritative secret list.
5. **This directory (`C:\Users\tarun\Downloads\pricewatch_4\pricewatch`) should
   stay checked out on `master` at all times** - it's a shared working tree
   between agents, and a stray `git checkout <branch>` here can land another
   agent's in-progress uncommitted edits on the wrong branch (happened live
   18 July: a Claude session checked out `v2` here mid-way through a Codex
   scraper fix sitting uncommitted in the working tree, which nearly got
   swept onto the wrong branch). The `v2` UI redesign branch has its own
   separate worktree instead - see the v2 entry below. If you need a branch
   other than `master` checked out for more than a couple of commands, use
   `git worktree add ../pricewatch-<name> <branch>` rather than switching
   this directory.

## Current production state

- **18 July, fixed same-day: `web/watch-confirm.js` had a live infinite-loop
  bug** (commit `354ae62`) that froze the browser tab for any shopper who
  successfully submitted the price-watch form. Its `MutationObserver` on
  `#wresult` replaced its own children with new HTML that itself contained
  the `.watchok` marker it was watching for, re-triggering itself forever.
  Fixed with a one-shot guard flag on the container's own `dataset` (an
  element's own attributes survive an `innerHTML` rewrite of its children).
  Deployed to both Vercel and the OCI VM's `pricewatch-web`; verified live
  on production with a fetch-intercepted test submission (page stayed fully
  responsive, confirmation message rendered exactly once). This had been
  live and undetected since the double-opt-in feature shipped - nobody had
  exercised the actual success path in a real browser until now. If you're
  touching this file again: never let a `MutationObserver`'s own callback
  produce a mutation that matches its own trigger condition without a
  guard.
- **Live site:** https://dealwatch.com.au (custom domain, live 17 July - see
  the "Custom domain + rebrand" entry below). The old
  https://web-pi-blush-48.vercel.app alias still resolves to the same
  deployment and is not being removed.
- **Brand:** the site is called **Dealwatch**, not Underpriced - it was
  renamed 17 July to match the purchased domain. If you see "Underpriced" in
  any file, that's stale; it should not exist anywhere after commit `7245560`.
- **Production branch:** `master` is current. The Vercel static frontend
  is deployed from `3892a26` (17 July SEO work); later commits change
  backend, OCI, and Supabase infrastructure and have been applied directly to
  production.
- **Hosting:** static `web/` on Vercel. Web Analytics is enabled; its
  cookie-free tracking script is installed on every public page. Dashboard
  reports will only contain visits from after enablement.
- **Data:** Supabase Postgres; GitHub Actions is the only active crawler.
  OCI hosts the Telegram subscription bot, SSR previews, and image proxy, but
  its old crawler timer remains disabled.
- **Backups:** a daily Supabase `pg_dump` runs on OCI at 03:17 UTC and uploads
  to a private Object Storage bucket; the lifecycle rule keeps 30 days.
- **Retailers:** 13 (kmart, bigw, officeworks, target, jbhifi, goodguys,
  myer, supercheap, sephora, chemistwarehouse, and - newly producing real
  data as of 18 July, see the IKEA/Booktopia/QBD entry below - ikea,
  booktopia, qbd). Telegram item/store subscriptions are live.

## Latest completed work

| Commit | Outcome |
|---|---|
| `daf6dd7` | Main deals grid capped at 60; private localStorage recently-viewed and category-based "For you" sections; anonymous trending-search schema/RPC/view; Big W cents parser fixed. |
| 11 July growth fix | `growth_daily` is now a materialized view with a unique index and is refreshed by each detect run. The live public endpoint returned data after the migration. |
| 11 July homepage stats fix | `catalogue_stats` is now a materialized view, refreshed by each detect run. The public product-total request fell from about 1.75 seconds to about 0.1 seconds after warm-up. |
| `ef6df9e` | Removed the obsolete local Big W scheduled task and script; made price tracking visible and moved the tracking panel above the chart. |
| 12 July RAM alert validation | The JB Hi-Fi RAM watcher now re-checks the live retailer product page and price before sending. Dead, unparseable, or price-mismatched listings are skipped; the verified dead `10009166` alert is rejected. |
| 12 July OCI backups | First verified PostgreSQL 17 dump (23 MB) and SHA-256 checksum uploaded to OCI Object Storage. Daily timer enabled; private bucket lifecycle deletes backups after 30 days. |
| `40302cb` / `5d5aeb6` | Retailer-native category chips and correct item counts. |
| 12 July Myer category fix | Removed bare `ink` as a tech signal and prioritised expanded apparel terms. Corrected 4,022 existing Myer clothing records and refreshed public feeds; reported Chino now returns `clothing`. |
| 12 July freshness fix | Public deal feed now excludes products not seen for 36 hours; direct verification found zero stale cards after rebuild. Big W changed from an hourly to a three-hour proxy cadence after 403 blocks; monitor its next scheduled run. |

| 12 July deal trust and discovery | Homepage cards now show whether the current price equals the 30-day low and whether the comparison is retailer RRP or a 90-day observed high. Selecting one retailer shows its last checked time and current listing count; quick filters cover 50%+, under-$50, new drops, and 30-day lows. Card clicks now open the internal history page first. |
| 17 July custom domain + rebrand (`7245560`) | User bought `dealwatch.com.au`. Added to the Vercel `web` project (apex + www), DNS A records pointed at `76.76.21.21`, SSL issued and verified live. Site renamed Underpriced -> Dealwatch everywhere: HTML titles/meta/logo/footer, the `underpriced_recent` -> `dealwatch_recent` localStorage key (index.html and product.html both changed together - **do not let these drift apart**, product.html writes it and index.html reads it), and user-facing strings in `services/preview_app.py`, `services/telegram_bot.py`, `watch_alerts.py`. `SITE_URL` updated in the GitHub secret **and** separately in `/opt/pricewatch.env` on the OCI VM (these are two different places that both need updating - the VM does not read the GitHub secret). Verified live: canonical/og:url on a real `/p/bigw/...` preview page now show `dealwatch.com.au`. |
| 17 July SEO pass (`3892a26`) | Added `robots.txt`, a sitemap index (`sitemap.xml`) referencing a static `sitemap-pages.xml` plus a **dynamic** `sitemap-products.xml` served from the OCI `preview_app.py` (queries `product_search` for `retailer,sku,price_updated_at` only, capped at 5000 rows, disk-cached 6h - deliberately cheap against the Supabase egress budget, see the egress section below). Added canonical/OG/Twitter/JSON-LD (`WebSite`+`SearchAction` on the homepage, `Product`+`Offer` on preview pages) to every page. **Important fix:** internal product links across `index.html`/`catalogue.html`/`search.html` were pointing at `product.html?retailer=&sku=` (a client-rendered URL with zero server-side meta) instead of the SSR'd `/p/:retailer/:sku` route - switched all of them, since this is what Google actually crawls from on-page links. `search.html` now reads `?q=` on load so the SearchAction schema is functional, not decorative. `growth.html` marked `noindex` (internal stats page, low search value, deliberately excluded from indexing budget). |
| 17 July Myer image fix (`30ee38d`) | Root cause of "Myer photos not showing": `scrapers/base.py`'s default JSON-LD parser read price/rrp/brand/gtin from a product's schema.org block but never its `image` field, so `image_url` stayed `NULL` forever for any retailer with no scraper-level override. Myer has zero bulk-listing lane (its `refresh` step is a documented no-op), so this was its *only* ingestion path - 100% of Myer images were missing. Added a generic `_image()` helper (handles string / list / ImageObject) and wired it into the shared `parse_product`. Existing rows self-heal: both `db.upsert` and `_upsert_chunk_pg` already do `image_url=COALESCE(excluded.image_url, products.image_url)`, so no backfill migration was needed - the next Myer crawl pass (full sweep ~3 days) fills them in. Verified live: a 5-URL test crawl (`python run.py crawl myer --batch 5`) produced a row with a real `myer-media.com.au` image_url immediately after the fix. **Known follow-up, not fixed:** Big W's bulk `refresh_listings` lane builds `ProductRecord`s directly from its Next.js listing JSON (`_record_from_listing` in `scrapers/bigw.py`) and never calls the shared `parse_product` at all, so it's unaffected by this fix and still has no image field wired up. I did not blind-patch it - didn't have a confirmed field name from a live listing payload (direct requests to bigw.com.au are blocked per the proxy policy, and I wasn't going to burn Webshare proxy budget just to inspect a field name). Whoever picks this up next should fetch one listing page through the approved proxy path and check for an image/media key on `item`, then mirror the `_image()` pattern above into `_record_from_listing`. |
| 17 July Myer image backfill acceleration (`b7beace`) | User reported "myer is still not fixed" a few hours after `30ee38d` landed - correctly: the parser fix only applies to URLs re-crawled *after* it deployed, and Myer's crawl queue (154,256 URLs, ~79k already-tracked products) was only processing 1000/hour, prioritising never-scraped URLs first. At that rate it would have taken ~3 days just to clear the never-scraped backlog before the crawler even started revisiting - and image-backfilling - already-tracked products (~6.5 days for a full cycle). Verified via direct query: only 199/79,107 Myer products had an image_url a few hours post-fix. Myer has no bot protection on plain requests (same profile as supercheap), so raised `crawl_batch` 1000 -> 2400 in `.github/workflows/crawl.yml` - still the same 1.5s politeness delay, just more requests within the existing hourly window (2400*1.5s=60min, comfortably under the 110min job timeout). Full queue cycle now ~2.7 days instead of ~6.5. Manually triggered an extra `crawl-and-detect` run (29584304464) rather than waiting for the next scheduled hour, confirmed running with the new batch size. **If asked again "why no Myer photo" before ~19-20 July, this is expected** - check `SELECT count(*) FROM products WHERE retailer='myer' AND image_url IS NOT NULL` trending upward, not a new bug. |
| 18 July Myer crawl_batch revert (`42d5d4d`) | The `2400` batch size above was **actively counterproductive**, not just slow: actual per-item time ran 2-3x the assumed 1.5s delay floor, so each myer job took 1.5-2h instead of the estimated 60min. Every hourly cron tick queued a new run for the `crawl-myer` concurrency group, which cancelled the still-running previous run before it finished. Reverted `crawl_batch` 2400 -> 1000 (the last value confirmed to reliably complete inside an hour). Note: at revert time this was suspected as the cause of the detect failures too - **that was wrong**, see the detect outage entry below (the outage predates the batch bump by 2 days). |
| 18 July detect outage root-caused and fixed | **`detect` had been failing on every scheduled run since 15 July ~22:00 UTC** - 2+ days with no new deals, no Telegram alerts, and no materialized-view refreshes (`retailer_freshness` et al were stale at 15 July values; every run since 29453360602 concluded `cancelled`). Root cause chain, confirmed live via `pg_stat_activity`: (1) `anomaly.run()` did one INSERT round trip per found deal inside a single long transaction - with 245k live products and a US runner talking to Sydney Supabase, the unique-index locks on `deals (product_id, price, signal)` were held for many minutes per run; (2) the detect *job* has no concurrency group (only crawl jobs do), so overlapping workflow runs ran two detects concurrently, each blocking the other's conflicting inserts past Supabase's 2-minute `statement_timeout` (`QueryCanceled ... while inserting index tuple in relation "deals"`) or grinding to the 15-min job kill; (3) a killed runner leaves its Postgres session **`idle in transaction` with the uncommitted deals insert still holding locks** (observed live: pid idle 14+ min), which starves the *next* detect too - self-perpetuating. Three-part fix: **anomaly.py** now pre-loads the existing deal keys (one cheap ~5.6k-row read), dedupes client-side, commits the read transaction *before* the minutes-long scoring loop, and writes all new deals in one short `executemany` burst at the end (lock window: seconds, not minutes); **run.py** `cmd_detect` sets `idle_in_transaction_session_timeout='5min'` on its own session so orphans self-reap; **crawl.yml** detect job got `concurrency: group: detect, cancel-in-progress: false`. Also terminated the live zombie session by hand and ran detect locally against production to verify: exit 0 in 88s, ~2,340 backlogged deals flushed, 30 Telegram alerts sent, all four materialized views refreshed (last_seen now current). **Watch the next 2-3 scheduled runs to confirm detect stays green in CI.** |
| 18 July Big W proxy budget: metering hole closed, tracker cycle-aligned | User asked "what about big w?" - it had been near-dark since 15 July: the self-imposed byte cap (700MB decompressed) tripped and paused the bulk refresh "until next month", leaving only the 40/run crawl trickle. Three real problems found and fixed: **(1) metering hole** - the byte counter only counted bulk-refresh listing pages; the crawl lane's product pages (~77MB/day billed, confirmed via the Webshare API) were invisible to the cap, on track to exhaust the actual 1GB plan around 25 July, two weeks before renewal. `BigWScraper.get()` now meters ALL traffic, and `cmd_crawl` gates + records bigw batches against the same kv counter. **(2) calendar-month vs billing-cycle mismatch** - the tracker reset on the 1st but Webshare renews on the 10th (subscription 2026-07-10 -> 2026-08-09), so two tracker windows could overlap one billed cycle. Tracker now keys on `_proxy_cycle` (10th-to-9th; `proxy_cycle()` in scrapers/bigw.py); prod kv row migrated by hand carrying the spent bytes forward. **(3) cap recalibrated with real data** - Webshare API says 450MB *billed* at 734MB *tracked-decompressed* (~0.61 ratio), so the cap moved 700MB -> `PROXY_CYCLE_BYTE_CAP=1300MB` decompressed (~800MB billed worst case, >20% plan headroom) - this un-pauses the bulk refresh now. To stretch the ~566MB (decompressed) remaining this cycle to the 9 Aug renewal instead of one blowout sweep: new `PROXY_RUN_BYTE_CAP=5MB` per refresh run (~2 listing pages ≈ ~290 products), Big W cadence gate halved to every 6 hours, crawl_batch pinned to 2 (kept barely alive as the only bigw image_url source, P9). Net: ~1,150 listing-refreshed products/day, continuous to renewal. **The real constraint is the 1GB plan size vs a 20k-product catalogue at ~2.5MB/page - if the user wants proper Big W coverage, the fix is buying more Webshare bandwidth (their call, per CLAUDE.md do not touch billing), then raising PROXY_CYCLE_BYTE_CAP and reverting the cadence to %3.** Verified live: crawl batch of 2 stored 2/2 and advanced the kv counter by exactly the fetched bytes. |
| 18 July Target fully blocked (visibility, not fixed) | Target's Akamai now 403s the **first request** of both lanes on every run (confirmed across 3+ consecutive runs: `BLOCKED mid-refresh` on catalogue page 1, `0/40 products stored` in the crawl lane). Per the established proxy findings (residential proxies do NOT defeat Kmart/Target's JS-challenge tier), there is no approved crawling fix - the action taken is honesty: Codex added a retailer-coverage table to `growth.html` (fed from the existing anon-readable `retailer_freshness` view) so stalled retailers visibly show their real last-checked age instead of blending in; the `deal_feed` 36-hour freshness cutoff already keeps stale Target prices off the homepage. Target's queue resumes automatically if/when the block lifts - Blocked is still a non-fatal early lane exit, by design. |
| 18 July Big W home-IP local sweep (primary lane) | Big W's block is IP-reputation-based: direct requests from the owner's residential Optus connection **pass cleanly with no proxy** (verified live 18 July: product page parsed, 144-product listing page returned). New `local_bigw_sweep.py` (repo root) runs the same refresh (budget 400 - covers the full ~190-page catalogue) + crawl (batch 200) lanes direct from the owner's PC, silently, via a Windows scheduled task "Dealwatch BigW sweep" (every 3h, `pythonw.exe` so no window, StartWhenAvailable to catch up after the PC was off, IgnoreNew so runs never stack). Logs to gitignored `local_bigw.log`, self-trimmed. **Coordination:** on each successful refresh it writes the `bigw_local_heartbeat` kv row; the CI Big W cadence gate now additionally skips the proxy lane while that heartbeat is <24h old, and automatically resumes the byte-capped Webshare fallback when the PC has been off for a day. To make direct traffic exempt from the proxy byte budget, `BigWScraper._proxied` (use_proxy AND PROXY_URL set) now gates all byte accounting and caps - with PROXY_URL unset, sweeps are uncapped and record nothing against the Webshare counter. The one-crawler-per-retailer politeness rule holds: local owns Big W while alive; CI only when it is not. Owner's router (Sagemcom F@ST 5393, locked Optus firmware) cannot run a tunnel, so the "OCI routes via home IP" variant needs a small always-on device (~$30-90) - offered, deferred until the user buys one. |
| 18 July Telegram + email alerts enabled | Telegram bot display name changed and verified as **Dealwatch** (the user separately changed its username). Resend sender-domain verification completed by the user; `RESEND_API_KEY` and `RESEND_FROM` are now GitHub Actions secrets. A one-off test sent through the production Resend sender to `alerts@dealwatch.com.au` was accepted and received, confirming email-alert delivery is operational. No secret values were recorded. |
| 18 July watch safety + discovery (`13923d5`) | Added a double-opt-in email watch flow: new watches receive a confirmation email first; `confirm_watch` records confirmation; unconfirmed watches never receive price alerts. Existing watches were retained as confirmed during the live schema migration. The public confirmation script is live and production checks confirmed the RPC/schema path; Resend delivery had already been verified separately. The homepage now shows a transparent Target-data delay notice instead of silently presenting stale coverage, and category/retailer discovery links plus the static sitemap were expanded. Vercel deployment was verified live. |
| 18 July Kmart public-feed-only refresh | Kmart bulk refresh is deliberately kept on Constructor's public catalogue feed, which exposes the required price, RRP, APN, image, and catalogue fields. The GitHub workflow now omits the separate protected Kmart product-page enrichment pass; it was redundant and the only path touching the Akamai-fronted storefront. Feed pacing is one serial request every **0.8–1.2 seconds** (previously a fixed 0.6 seconds), keeping a conservative non-mechanical cadence. One live read-only Constructor groups request returned HTTP 200 on 18 July. No proxy rotation, cookie manipulation, or browser-stealth bypass was added. |
| 18 July product timeline + trust pages | Replaced the misleading equal-spaced sparkline with a responsive, time-scaled step timeline built only from Dealwatch `price_snapshots`: price gridlines, date ticks, tooltips, compact recorded-change summary, and correct “price held until next observation” shape. Added `/contact.html`, `/privacy.html`, and `/terms.html`, surfaced all three in every public footer, and added them to the static sitemap. The contact page is intentionally an email link to `alerts@dealwatch.com.au`; it does not expose an email-provider key or store unprotected form submissions. Resend inbound is a separate future setup requiring a receiving domain/MX record plus a verified webhook, not enabled here. |
| 18 July product-route cache correction | The Vercel rewrite initially cached `/p/...` origin HTML for 10 minutes, so the newly deployed chart template was invisible at the ordinary product URL until cache expiry. This also risked replaying pages containing one-use watch/cancel query tokens. Product responses now send `Cache-Control: private, no-store, max-age=0` from both the OCI preview app and the Vercel `/p/(.*)` rule. This keeps live price pages and token-bearing URLs out of shared caches. |
| 18 July trust + methodology pass | Replaced “all-time” labels on product pages with “lowest/highest recorded price”, then made the scope explicit: tracking start, observation count and last checked date. Added a responsive price-history table (latest daily observations, accessible without chart interaction), a pre-filled **Report an incorrect price** email action on every product, and homepage deal evidence that now includes comparison source, marketplace flag and last-checked age. Added `/how-it-works.html`, linked it across every footer, and added it to the static sitemap. Contact messages are directed to the Resend receiving address; inbound message storage is handled by Resend’s dashboard. |
| 18 July homepage rendering incident (`405caaf`) | The deployed trust-line change used `ago(d.price_updated_at)` even though the homepage helper is named `since()`. The resulting browser `ReferenceError` occurred inside `cardHtml`, was caught by the broad refresh handler, and showed “We couldn’t load the deals” although Supabase was healthy. Replaced the call with `since()`, verified the inline script with `node --check`, pushed and Vercel-deployed `405caaf`. Final live check: homepage HTTP 200, fixed helper present, old helper absent, and the exact public `discount_feed` request returned HTTP 200 with a deal. |
| 18 July Chemist Warehouse restored (`036c312`) | Owner reported it "stopped" - Codex had already left an uncommitted rewrite in the working tree (Chrome impersonation was getting blocked; new approach drops the disguise and self-identifies honestly as `DealwatchBot/1.0` with a contact link, no proxy/impersonation, 10s+jitter delay, no retry after a block, plus a generic `request_headers` override hook added to `base.py` for any retailer wanting the same pattern). Claude found this in-progress work, tested it live before committing rather than guessing: 5/5 real product URLs (incl. the one reported blocked) returned clean HTTP 200 with real `__NEXT_DATA__` product JSON, sitemap fetch succeeds, `robots.txt` explicitly allows `/buy/` paths, and `parse_product` against a real fetched page correctly extracted price/RRP/brand/image/stock end-to-end. Committed and pushed. `crawl_batch` lowered 500->75 to match the slower delay. **If Chemist Warehouse stops again, this transparent-bot pattern is the one to extend to other blocked retailers before reaching for impersonation/proxies again.** |
### Production data correction

Big W SKU `41041` (Harry Potter Hufflepuff skirt) was corrected directly in
Supabase: current price is `$11.00`, reference price is `$35.80` (not a false
`$3,580`). The materialized `discount_feed` was refreshed. The parser now
converts Big W product-page JSON-LD cent values for both price and RRP.
Verified 11 July (later session): product row and snapshots are clean, zero
other Big W rows with rrp > 50x price, and the skirt is **correctly absent**
from the deals feed - its true $35.80 reference is under the feed's $40
floor. Its earlier appearance was purely an artifact of the cents bug; do
not "fix" its absence.

### Notification decisions

- **Telegram:** free, live, and the preferred public messaging channel.
- **Web push:** recommended next free alert channel if requested.
- **SMS:** no ongoing free production option. Do not use an ALDI Mobile SIM
  or an Android emulator as an automated public gateway: emulators cannot send
  carrier SMS, and ALDI's acceptable-use policy forbids commercial/automated
  SMS traffic. A paid A2P provider is needed for production SMS.
- **WhatsApp:** possible through the official Business Platform, but outbound
  price alerts are paid template messages. Do not start setup without an
  explicit user decision to accept Meta onboarding and message costs.

### Supabase egress quota (12 July)

Supabase emailed: the org blew the free tier's egress quota (grace until
Aug 11; keep egress under 5.5GB/month). Root cause measured with
pg_column_size: anomaly.py's `SELECT * FROM products` (62MB x 48 runs/day
~= 3GB/day) plus per-sweep full-row readbacks in bulk_upsert and
sku+url fetches in cmd_refresh. Fixed in `2b792e3`: explicit columns
(62MB -> 1.6MB), cross-retailer matching only when UTC hour %% 6 == 0,
server-side change detection in a new `_upsert_chunk_pg` (single CTE
| P7 | Verify Big W three-hour proxy refresh | Codex | Waiting for scheduled run | Next scheduled Big W attempt after the 12 July cadence fix; inspect its Actions log before changing proxy or scraper settings. |
statement, returns changed keys only; both price sides cast to
numeric(10,2) or float error marks everything changed), sku-only refresh
bookkeeping, and the cron halved to hourly. Estimated ~0.13GB/day
(~3.7GB/month) after, vs ~4.5GB/day before. All verified against
production (synthetic-retailer upsert tests + a real jbhifi refresh).
**Do not reintroduce `SELECT *` against products, or client-side price
diffing, without re-checking the egress budget.** If usage still trends
over, next levers: Pro plan ($25/mo) or further cadence cuts.

## Task queue

| ID | Task | Owner | Status | Allowed files / notes |
|---|---|---|---|---|
| P1 | Verify Vercel Analytics receives first real visitor data | Unassigned | Waiting for traffic | Vercel dashboard only; do not add another analytics vendor. |
| P2 | Test Telegram subscription end-to-end from a real user account | User | Waiting for user action | User must press Start in Telegram. |
| P3 | Big W proxy 402 | Claude | **Resolved 11 July** | Root cause: stale proxy *username* (password was current) - Webshare rotated it; every CONNECT got 402 from both CI and local, so Big W silently stopped crawling right after setup (dashboard's 0.02GB usage corroborates). Fixed via the Webshare API (key in local .env as WEBSHARE_API_KEY; user should rotate it - it was pasted in chat): PROXY_URL now `<user>-AU-rotate@p.webshare.io:80`, updated in local .env + GitHub secret + VM env. Also fixed daf6dd7's cents regression (JSON-LD price is DOLLARS; only page-state wasPrice is cents - commit 96a5175); no bad rows were written because the proxy was down the whole time. Verified live: SKU 41041 parses $11.00 / rrp $35.80 through the proxy. Workflow run 29147446498 kicked to confirm in CI. |
| P4 | Consider browser push alerts | Unassigned | Proposed | Build only if user asks; no cookies or third-party tracker. |
| P5 | Custom domain setup | Claude | **Done 18 July** | dealwatch.com.au is live on Vercel with SSL, `SITE_URL` is updated in GitHub and OCI, and the Resend domain/sender is verified. `RESEND_API_KEY` and `RESEND_FROM` are configured in GitHub Actions; a test delivery to `alerts@dealwatch.com.au` was received. |

| P6 | Daily Supabase backup to OCI Object Storage | Codex | **Done 12 July** | Private bucket, instance-principal upload, 30-day lifecycle, daily timer, and first dump verified. |
| P8 | CartSavvy-inspired trust and discovery improvements | Codex | **Done 12 July** | Production views verified through anon API: 14,844 deal cards and 10 retailer freshness rows. |
| P9 | Big W bulk-listing lane has no product images | Unassigned | Open | `scrapers/bigw.py`'s `_record_from_listing` builds `ProductRecord`s from Next.js listing JSON directly, bypassing the JSON-LD image fix in commit `30ee38d`. Needs a confirmed image/media field name from a real listing payload (fetch via the approved Webshare proxy, not a bare request - direct hits get 403'd) before patching. |
| P10 | SEO follow-through: domain, crawlability, and Search Console readiness | Codex + Claude | **Done 17 July (DNS propagation)** | Codex built canonical-host redirect, SSR `/deals/:category` + `/retailers/:retailer` landing pages, 25-page product sitemap, noindex/sitemap conflict fix, and metadata (left uncommitted in the working tree - Claude reviewed, verified, committed as `f0b5e80`, and deployed to Vercel + OCI). Claude then found and fixed a real bug in that same session: `SITEMAP_PAGE_SIZE=25_000` assumed PostgREST would return up to 25k rows/request, but Supabase enforces a hard 1000-row `db-max-rows` cap - confirmed live with a `limit=5000` request that still came back as exactly 1000 rows. Each of the 5 sitemap pages was only covering its first 1000 rows before a ~24000-row gap to the next page's window. Fixed in `ad04756` (page size now matches the real cap; total coverage is now 25,000 fresh URLs across 25 contiguous sitemap pages). Domain verified in Google Search Console 17 July (TXT record, user's own login); user submitted `sitemap.xml`. **Status:** the sitemap now covers the 25,000 freshest product URLs across 25 contiguous 1,000-URL pages. Monitor Search Console coverage before increasing it toward the full ~237k-product catalogue. The authoritative DNS now points at Vercel; public recursive resolvers may retain the previous apex IP until the 14,400-second TTL expires. **Temporary availability fallback (17 July):** disabled the Vercel `www`→apex redirect because Cloudflare still returned the previous, TLS-invalid apex IP; `www.dealwatch.com.au` now serves a 200 page with the apex canonical so users have a working route during propagation. Restore the permanent redirect only after public resolvers converge on the Vercel apex IP. |
| P11 | Book data quality + product history redesign | Codex | **Ready to deploy** | `categorize.py`, `run.py`, `web/product.html`, email contact links. Local category + JS parse tests pass; this session cannot write `.git/index.lock` or reach GitHub/Vercel due its sandbox/proxy. Commit, push, deploy Vercel and update OCI, then trigger `crawl-and-detect` so the repair runs. Booktopia is deliberately not activated until robots permissions are verified. |
| P12 | Hot-now, PWA barcode lookup, related-price suggestions | Codex | **Ready to deploy** | `schema.sql`, `views.sql`, `web/`; anonymous aggregate product-interest view (5-minute per-product server limit), installable web app + camera/manual barcode search, and GTIN/model/labelled-similar comparisons. Python/JS/manifest checks pass. Deploy requires applying `schema.sql` then `views.sql`, Vercel static deploy, OCI update/restart for product previews; current sandbox cannot write Git locks or reach external deployment services. |
| P13 | Mobile-first visual polish | Codex | **Ready to deploy** | `web/style.css`, `web/*.html`; system-aware dark theme, enlarged rotating-prompt home search, mobile bottom nav, sticky watch action, skeleton loaders, toast feedback and chart average guide (only after 4 points). Manifest + browser-script validation pass; deploy with P11/P12 bundle once Git/network access is available. |
| P14 | AI-powered "similar items" suggestions (self-hosted embeddings) | Claude | **Ready to deploy** | `schema.sql`, `views.sql`, `requirements-embed.txt` (new), `embed_products.py` (new), `.github/workflows/crawl.yml`, `web/product.html`, `web/index.html`. Added a `products.embedding vector(384)` column + HNSW index, a `similar_products(retailer, sku, limit)` RPC (same `security definer`/regex-validation/revoke-grant shape as `log_product_interest`), a standalone `embed_products.py` (fastembed/all-MiniLM-L6-v2, no-ops without `DATABASE_URL`, budget-capped/resumable like the crawlers) wired into a new `embed` CI job, and two new frontend call sites (`#aiSimilar` on `product.html`, "Similar to..." row on `index.html`, both reusing the existing `.hot-card`/`hotCard()` pattern). **Also fixed a real, unrelated bug found while editing `crawl.yml`**: commit `ff8d9cab` (18 July) had left a stray duplicated matrix block (booktopia/qbd/ikea) pasted inside the `detect:` job, making the whole workflow file invalid YAML - confirmed with `yaml.safe_load` before and after; the entire `crawl-and-detect` workflow could not have been parsed by GitHub Actions since that commit. Removed the stray block. Verified in this session: `ast.parse` on `embed_products.py` (and the existing Python suite), `python embed_products.py` no-ops cleanly with no `DATABASE_URL` set, both modified HTML files' inline `<script>` blocks parse with `node -e "new Function(...)"`, and `crawl.yml` now parses as valid YAML with jobs `[crawl, embed, detect]`. **This session has no `DATABASE_URL`, no Vercel/Supabase credentials, no OCI SSH key, and no `.env` file** (checked directly - `gh`/`vercel` aren't installed either), so none of the following could be done here and are left for the human or a credentialed session: apply the `schema.sql`/`views.sql` diffs to production (Supabase SQL Editor or `psql "$DATABASE_URL" -f schema.sql -f views.sql`; confirm `pg_extension` has `vector` and the column/index exist), run `pip install -r requirements-embed.txt && python embed_products.py --budget 200` against production as a smoke test, curl the RPC for one embedded product to sanity-check neighbours, merge and trigger `crawl-and-detect` to confirm the new `embed` job completes inside its 20-minute budget, then `vercel --prod` deploy `web/` and check both new sections live. |

## Handoff notes

- `PROJECT_NOTES.md` contains the long technical history. Treat this document
  as the current checkpoint when it conflicts with older handoff prose.
- `HANDOFF_CODEX.md` is historical and contains stale pre-11-July instructions;
  preserve it but do not treat it as the current task list.
- The user permits normal repo, GitHub, Vercel, and production changes needed
  for requested work; still do not make irreversible billing, domain purchase,
  account, or legal/communications-policy decisions without a direct request.
- **The OCI VM does not auto-deploy.** `git push` to `master` changes nothing
  there until someone SSHes in, runs `git pull`, and restarts
  `pricewatch-web`/`pricewatch-bot`. On 17 July it was found several commits
  behind (`c2b4119`, from an earlier session) - serving stale HTML/branding
  and a stale `SITE_URL` the whole time despite the repo looking current.
  **Check `ssh ubuntu@159.13.59.184 "cd /opt/pricewatch && git log -1"`
  against `origin/master` at the start of any session that touches
  `services/preview_app.py`, `services/telegram_bot.py`, or anything the OCI
  services serve/read (including `web/product.html`, which `preview_app.py`
  reads directly off disk).** SSH access is IP-allowlisted in the OCI
  security list (`infra/oci/terraform.tfvars`, gitignored) - if it times out,
  the deployer's public IP has probably changed since the list was last
  applied; `terraform plan`/`apply` after updating `ssh_allowed_cidr` fixes it
  with a clean single-rule diff, no instance recreate.

## 18 July Codex handoff addendum

- Commits `c4147ba`, `ff8d9ca`, `53b1f4b` and `0313eeb` added mobile
  discovery/product matching, community in-store report foundations, Buy/Wait
  guidance, suggested cross-retailer items, catalogue price range plus low/high
  sorting, and faster catalogue requests.
- Catalogue and Deals retailer filters now use the same shopping groups:
  **Books = QBD + Booktopia**, **Auto = Supercheap Auto**, and
  **Cosmetics = Sephora**. Product cards retain the real retailer name.
- Crawler health markers and deduplicated incident/recovery emails now target
  `admin@dealwatch.com.au`. Chemist Warehouse remains HTTP 403-blocked:
  monitoring is implemented, but the scraper itself is not restored.
- Booktopia, QBD and IKEA scraper implementations and hourly workflow jobs are
  pushed. Their one-time crawl-queue indexing has **not** been run, so they are
  not collecting listings yet. Dymocks was excluded because its public site
  presented an active bot challenge; do not bypass it without an approved
  feed/API.
- The static site must be deployed from `web/` using the linked Vercel `web`
  project; a Git push alone did not update `dealwatch.com.au` in this session.
  The repo root is linked to a separate stale/error `pricewatch` Vercel project.
- **Unresolved visual issue, explicitly deferred to Claude:** commits `2614aa3`,
  `6151133` and `95548ea` removed blue tokens and restored the historical
  warm-white Bellroy palette. Live HTTP checks reported `#faf9f7` paper,
  `#33363b` ink, `#d3572b` accent and service-worker cache v6, but the owner
  still sees blue. Reproduce in the owner's real browser/PWA and inspect
  computed styles, the active service-worker controller and Cache Storage.
  Do not keep guessing at palette values.
- Windows Codex is configured with the `unelevated` restricted-token sandbox.
  Elevated setup failed and produced no dedicated setup error in the available
  dated sandbox logs. This is why `apply_patch` refuses split writable roots;
  scoped elevated PowerShell edits were used as a fallback.
## 18 July Claude handoff addendum — the "owner still sees blue" mystery, solved

- Root cause of the palette mystery Codex flagged above: `web/style.css` had
  a leftover `@media(prefers-color-scheme:dark)` block (under a "system dark
  theme + mobile app polish" comment) that repainted `--flag` to `#60a5fa`
  (light blue) on a `#0f172a` navy `--paper` for any visitor on a dark-mode
  OS/browser — directly contradicting the `color-scheme:light` / "light-only
  by design" comment sitting right above the `:root` block. No amount of
  fixing the *light* palette values could have addressed this: the owner
  was seeing the *dark* palette the whole time. This block has been deleted
  entirely (commit `bdd979f`). There must be **no** dark-mode media query in
  `web/style.css` going forward — if a future report says "the background
  looks wrong" again, check for a reintroduced one before touching palette
  values.
- Per explicit owner instruction ("i dont like the new one... blue"), the
  palette itself was also changed in the same commit: a plain classic
  "Web 2.0" theme — stark white `#ffffff` background, standard primary blue
  `#007bff` for headers/buttons/links, plain green `#28a745` for drops, and a
  new `--bad:#dc3545` red now wired into `.row.err .off` / `.pill.err` for
  price increases (previously these silently reused `--flag`, which meant
  "error/increase" rendered in whatever the accent colour was that week).
  `web/icon.svg` and every page's `theme-color` meta / manifest were updated
  from `#faf9f7` to `#ffffff` to match.
- Verified live on `https://dealwatch.com.au/catalogue.html` post-deploy
  (Vercel prod alias re-pointed, `vercel deploy --prod` from `web/`): white
  background, blue accents, Codex's Books/Auto/Cosmetics retailer groupings
  all rendering correctly together with the new theme.

## v2 UI redesign — DONE, merged to master, LIVE on production (18 July)

- The `v2` branch (owner-approved after several preview-deploy review
  rounds) was fast-forward-merged into `master` and deployed - `master` and
  `v2` are now identical (`fc9a292`), both live on `dealwatch.com.au`. The
  `pricewatch-v2` worktree still exists at
  `C:\Users\tarun\Downloads\pricewatch_4\pricewatch-v2` but is no longer
  ahead of anything; treat `master` as authoritative going forward. Nobody
  asked for the worktree/branch to be deleted, so it was left in place.
- Scope was an owner-provided 7-section spec (chart, cards, typography,
  search bar, mobile nav/alerts, micro-interactions - colour excluded, see
  below). All 7 shipped; full breakdown of what changed and why is in the
  commit messages on `master` between `53d0cb7` and `fc9a292` - worth
  reading if touching the chart, cards, search bar, `web/alerts.html`, or
  `watch-confirm.js` again, since several non-obvious decisions are
  recorded there (e.g. why the chart keeps a step-line not a smoothed
  curve, why the double-opt-in flow means the track button can't say
  "Tracking ✓" immediately).
- **Two rounds of live owner feedback after the initial 7 items, both
  shipped to production too:**
  - Removed the "Buy now / Consider waiting / Watch the trend" advice
    banner and the "Seen a cheaper in-store price?" community-report panel
    from the product page entirely (`f903c32`) - owner didn't want the
    page pushing a decision on shoppers. `buyAdvice()`, `storeReportPanelHtml()`,
    `submitStoreReport()` and their CSS are gone from `web/product.html`.
    The backend (`submit_store_report` RPC, `store_reports` table) is
    untouched, just unreachable from the UI now, in case this gets
    revisited.
  - The blue/green palette tokens were judged "too showing out" (too
    saturated) and lightened (`fd2e375`: `--flag` `#3b82f6`, `--deal`
    `#22c55e`), then the new "Lowest Price!"/discount badges specifically
    were judged to still hurt the eye with a solid neon fill + glow, so
    both badges were switched to a soft pastel-pill treatment (light bg,
    dark text, gentler glow) matching the site's existing `.pill.deal`-
    style soft pills (`1b4050d`, `fc9a292`).
- **A real, severe, unrelated production bug was found and fixed while
  testing the button-morph micro-interaction** (`354ae62`, deployed same
  day): `watch-confirm.js` had a live infinite loop that froze the tab on
  every successful watch submission - see the "Current production state"
  entry above for the full writeup. Root-caused and fixed before it was
  ever traced back to a support complaint.
- Verified in a real browser throughout (search focus glow, card badges,
  chart tooltip/gradient, alerts page empty + populated states, the
  button-morph/localStorage flow via a fetch-intercepted test submission,
  the watch-confirm.js fix, the final palette on production). **Not
  verified: true narrow-viewport rendering of the mobile bottom nav/sticky
  button** - a browser-automation tooling limitation this session (window
  resize didn't take effect on the tab used for testing), not a known code
  issue - the underlying `@media(max-width:640px)` rule is pre-existing,
  unmodified, and already relied on elsewhere. **Worth a real phone check.**

## IKEA / Booktopia / QBD — fixed same day (18 July), root cause was never a scraper bug

`scrapers/ikea.py` (`IkeaScraper`) and `scrapers/books.py`
(`BooktopiaScraper`, `QBDScraper`) are correctly implemented sitemap-only
scrapers - no bulk listing API, so `refresh` is a documented no-op for all
three ("no fast refresh path, skipping"). They depend entirely on
`python run.py index <retailer>` (`run.py:60`, `cmd_index`) - a **one-time,
manual** command that walks the full sitemap and seeds `crawl_queue` - to
have anything for the hourly `crawl` step to work through.

That one-time seeding step had simply never been run for any of the three
since their scrapers were added. Confirmed directly from a real GitHub
Actions log line: `queue empty - run: python run.py index ikea`. Every
hourly cycle since had been silently doing nothing for these three
retailers (the step "succeeds" via `|| true`, masking the empty queue) -
zero rows in `products` for any of them, ever.

**Fixed by running the one-time index command against production** (from
the OCI VM): `python run.py index ikea` (22,170 URLs queued),
`python run.py index booktopia` (359,000 URLs queued - the sitemap is much
bigger; the index run's own stdout didn't fully flush over the SSH pipe
but the DB commits happened fine, confirmed by querying `crawl_queue`
directly rather than trusting the log), `python run.py index qbd` (256,000
URLs queued, same stdout-flush quirk). Then ran one manual 10-item crawl
batch per retailer to prove real product rows land, not just queue rows -
confirmed `products` now has 10 rows each for ikea/booktopia/qbd. No code
changes were needed; the existing hourly `crawl` step (already configured
in `crawl.yml` with `crawl_batch` 80/180/250 respectively) will work
through the now-populated queues on its normal schedule from here.
Booktopia's queue is not 100% of its full catalogue (the index run was
interrupted partway by an SSH-side timeout, but everything committed up to
that point is safe/real) - fine to leave as-is, 359k queued rows is far
more than the crawler will get through any time soon, but worth knowing if
someone wants full completeness later (`python run.py index booktopia`
again is safe to re-run, `INSERT OR IGNORE` dedupes).

## End-of-session checklist

1. Update this file's completed work/task queue with concise facts.
2. Commit and push code/docs together when the work is complete.
3. State exact production verification performed and any external dependency.
