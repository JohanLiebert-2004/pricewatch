# Pricewatch — shared agent state

*Updated: 12 July 2026 AWST. This is the durable summary for Codex and Claude;
it deliberately records decisions and outcomes, not chat transcripts or secrets.*

## Start here

Before changing anything:

1. Read `CLAUDE.md`, this file, and `AGENT_PROTOCOL.md`.
2. Run `git status -sb` and `git log --oneline -5`.
3. Claim one unowned task below before editing. Do not edit another claimed
   task's files.
4. Never print, copy, or commit `.env`, Terraform state, SSH keys, tokens, or
   secret values. See `CLAUDE.md` for the authoritative secret list.

## Current production state

- **Live site:** https://web-pi-blush-48.vercel.app
- **Production branch:** `master` is current. The Vercel static frontend
  remains deployed from `daf6dd7`; later commits change backend, OCI, and
  Supabase infrastructure and have been applied directly to production.
- **Hosting:** static `web/` on Vercel. Web Analytics is enabled; its
  cookie-free tracking script is installed on every public page. Dashboard
  reports will only contain visits from after enablement.
- **Data:** Supabase Postgres; GitHub Actions is the only active crawler.
  OCI hosts the Telegram subscription bot, SSR previews, and image proxy, but
  its old crawler timer remains disabled.
- **Backups:** a daily Supabase `pg_dump` runs on OCI at 03:17 UTC and uploads
  to a private Object Storage bucket; the lifecycle rule keeps 30 days.
- **Retailers:** 10. Telegram item/store subscriptions are live.

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
| P5 | Custom domain and Resend sender setup | User | Waiting | Needs a domain choice/purchase and external verification. |

| P6 | Daily Supabase backup to OCI Object Storage | Codex | **Done 12 July** | Private bucket, instance-principal upload, 30-day lifecycle, daily timer, and first dump verified. |
| P8 | CartSavvy-inspired trust and discovery improvements | Codex | **Done 12 July** | Production views verified through anon API: 14,844 deal cards and 10 retailer freshness rows. |
## Handoff notes

- `PROJECT_NOTES.md` contains the long technical history. Treat this document
  as the current checkpoint when it conflicts with older handoff prose.
- `HANDOFF_CODEX.md` is historical and contains stale pre-11-July instructions;
  preserve it but do not treat it as the current task list.
- The user permits normal repo, GitHub, Vercel, and production changes needed
  for requested work; still do not make irreversible billing, domain purchase,
  account, or legal/communications-policy decisions without a direct request.

## End-of-session checklist

1. Update this file's completed work/task queue with concise facts.
2. Commit and push code/docs together when the work is complete.
3. State exact production verification performed and any external dependency.
