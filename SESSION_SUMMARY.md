# Dealwatch progress — 28 October 2026

Current operational details are in [AGENT_STATE.md](AGENT_STATE.md).

## Completed website work

- Installed GA4 measurement ID `G-3V1XFPB2L7` across static and SSR pages;
  verified collection. Sensitive URL parameters are excluded from analytics.
- Added highest/lowest percentage-discount sorting to the catalogue.
- Removed the price-freshness controls, labels and recent-only filtering at
  the owner's request. Catalogue still includes all tracked products.
- Added private server-log visitor reports with country/IP breakdowns.
  Treat these as estimates, not verified people; GA4 is the audience measure.
- Retained explicit Big W reference prices where the source provides them.

## Collector and infrastructure work

- Deployed bounded database writes, queue ordering, resumable collection,
  failure reporting, supporting indexes and publication/detection fixes.
- Refusals now stop the collector, including live-price verification;
  automatic fresh-session retries after blocks were removed.
- Standard Firefox is available for Big W and Sephora on the Ubuntu laptop.
  No personal browser profile/cookies, stealth or challenge bypass is used.
- Big W: test verified 144 catalogue products and the LEGO product page;
  saved 68 tracked listings and 19 price-change snapshots. Firefox is enabled,
  but a later scheduled refresh failed at 10:19 UTC. Full recovery is **not**
  confirmed; inspect the latest health detail/log before further changes.
- Sephora: enabled standard Firefox with AU country headers, two API pages
  per normal run and a persisted page cursor. Timer runs at :08/:38 each hour
  with jitter. CI skips while the laptop owns a run or has a recent successful
  window. Cursor advances only after pending product writes commit.
- Latest Sephora success at 11:47:55 UTC: 1,000 listings checked, 812 retained,
  111 changed-price snapshots. Health is `ok`, heartbeat is 11:47:54 UTC,
  cursor is next_page=13, and no running lease remains. Timer active/waiting.
  A full 15-page rotation has not yet been verified.
- JB Hi-Fi and Chemist Warehouse have successful recent laptop runs.
- QBD: product page loaded in Firefox, but its sitemap returned HTTP 403.
  No new QBD Firefox job enabled; product identity also needs validation.
- No new Oracle VM was created: earlier Always Free capacity checks failed.
  The existing capacity-retry worker remains configured.

## Verification and source

- Latest implementation verification: 96 Python tests passed. Changed Python
  compiled, workflow YAML parsed, and user units validated. Deployed laptop
  Sephora/crawler checks passed. Updated Sephora prices were verified through
  the public HTTPS catalogue API.
- Big W Firefox source: `d1fede46f7c628673b07e10d6f53865acf9f0abe`.
- Sephora source and CI gate: `3bd767a74035368c173e240efa8de3fdec41a813`;
  rolling-window update: `76fb1c1e9f5b6117aaad918613ebda83810ae6e1`.
- Previous published handoff: `c69ae38c2f26351aa1ed00addbfc684529ed3d4b`.
- The public collector changes are published to GitHub. A local checkpoint
  preserves the remaining public working-tree changes; unrelated edits are
  not overwritten or blindly deployed. Private environment files, keys,
  database files and Terraform state are excluded from checkpoint additions.
- Local checkpoint branch: `checkpoint/progress-20261028`, commit
  `28b2ff800bec885c50dc13585ccfa231c338a74a`, containing 94 current public
  working files. The working branch and default Git index were preserved.
- Setup/rollback: [Big W](infra/oci/BIGW-FIREFOX.md),
  [Sephora](infra/oci/SEPHORA-FIREFOX.md).

## Monetization plan discussed

Start with affiliate commissions on retailer buttons, followed by display
ads, clearly labelled sponsorships and optional premium alerts when audience
and demand justify them. No affiliate applications, advertising accounts,
subscriptions, payment processing or monetization changes were made.

Practical next steps:

1. Apply as Dealwatch, an Australian price-comparison/deals publisher, to
   [The Good Guys affiliate programme](https://www.thegoodguys.com.au/affiliates)
   via Partnerize. At the time checked, it advertised 3% default commission
   and a 14-day referral window, with eligibility exclusions.
2. Explore appropriate merchant programmes through
   [Awin](https://www.awin.com/au/faqs). Product-feed access depends on the
   advertiser and approval; it is not guaranteed by network membership.
3. After approval, use approved affiliate links and add a clear commission
   disclosure. Keep discount sorting based on actual discount values.
4. Add retailer-button click tracking to GA4 and measure actual commissions
   through affiliate reporting. GA4 alone cannot observe retailer purchases.
5. Review several weeks of reliable audience/click data before investing in
   ads or paid features. Revenue examples discussed were illustrative only.

## Next technical checks

- Investigate Big W's later scheduled failure without assuming the single
  successful browser test proves sustained collection.
- Verify Sephora wraps through its full catalogue and resumes correctly;
  keep monitoring the database I/O constraint and timer duration.
- QBD discovery remains blocked; do not claim it is recovered.
- Do not reintroduce price-freshness UI without the owner's request.
