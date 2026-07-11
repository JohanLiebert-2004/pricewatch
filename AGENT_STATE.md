# Pricewatch — shared agent state

*Updated: 11 July 2026 AWST. This is the durable summary for Codex and Claude;
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
- **Production branch/commit:** `master` at `daf6dd7` — pushed and deployed
  on 11 July 2026.
- **Hosting:** static `web/` on Vercel. Web Analytics is enabled; its
  cookie-free tracking script is installed on every public page. Dashboard
  reports will only contain visits from after enablement.
- **Data:** Supabase Postgres; GitHub Actions is the only active crawler.
  OCI hosts the Telegram subscription bot, SSR previews, and image proxy, but
  its old crawler timer remains disabled.
- **Retailers:** 10. Telegram item/store subscriptions are live.

## Latest completed work

| Commit | Outcome |
|---|---|
| `daf6dd7` | Main deals grid capped at 60; private localStorage recently-viewed and category-based "For you" sections; anonymous trending-search schema/RPC/view; Big W cents parser fixed. |
| `ef6df9e` | Removed the obsolete local Big W scheduled task and script; made price tracking visible and moved the tracking panel above the chart. |
| `40302cb` / `5d5aeb6` | Retailer-native category chips and correct item counts. |

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

## Task queue

| ID | Task | Owner | Status | Allowed files / notes |
|---|---|---|---|---|
| P1 | Verify Vercel Analytics receives first real visitor data | Unassigned | Waiting for traffic | Vercel dashboard only; do not add another analytics vendor. |
| P2 | Test Telegram subscription end-to-end from a real user account | User | Waiting for user action | User must press Start in Telegram. |
| P3 | Monitor Big W proxy quota / 402 response | Unassigned | Investigate before changing | Local probe on 11 July got proxy HTTP 402. Check GitHub secret/provider account without exposing `PROXY_URL`. |
| P4 | Consider browser push alerts | Unassigned | Proposed | Build only if user asks; no cookies or third-party tracker. |
| P5 | Custom domain and Resend sender setup | User | Waiting | Needs a domain choice/purchase and external verification. |

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
