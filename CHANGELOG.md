# Changelog

## 2026-09-13 - Phase 2 (ready for production verification)

- Product pages place barcode-matched retailer comparisons above alerts,
  with a shortcut near the product title, prices, check dates and marketplace
  labels. Valid GTINs use check digits and normalized leading-zero aliases.
- Recent matches are separate from older checks; savings use only recent
  valid prices. Model/title suggestions are separate and never claim savings.
  Slow/failing suggestions no longer delay barcode matches. Comparison errors
  show retry; missing barcodes and no matches have distinct explanations.
- About page states the owner's confirmed policy: free for anyone to use,
  no affiliate links. No personal identity was published. Homepage discovery
  links and all public footers expose About/methodology. `/about` and
  `/how-it-works` redirect to their canonical pages; About is in the sitemap.
- Methodology describes match/freshness limitations and avoids presenting
  scheduled checks as a guarantee that every product is current. Cache v11.
- Both browser suites, 13 Python tests, compilation, Terraform fmt/validate,
  configuration JSON/sitemap XML parsing and diff checks pass. Phase 2 browser
  cases include invalid/equivalent GTINs, stale/zero prices, suggestions,
  independent requests, retry, missing identifiers and 390/1440px layout.

## 2026-09-13 - Phased improvements

- GitHub sync completed after explicit owner approval: commits 266c636,
  b922ed8 and b06e35c are on origin/master. Earlier push-block notes below
  are historical; Phase 1 was already deployed and verified on production.

- Search recovery verified: live keyword results from all 13 retailers,
  plus expected SKU/barcode matches. Website-role query plan uses the new
  trigram indexes; database statistics confirm ANALYZE completed.
- Phase 1 deployed and verified: default marketplace exclusion in the main
  deal feed, opt-in checkbox, visible seller badges, and marketplace-free
  personalized deal suggestions. Feed filtering is server-side and preserved
  during pagination; seller selection is shown in the result summary.
- Cards explain email/Telegram alerts and link directly to the watch panel.
- Clear search & filters now resets all its advertised controls. API failures
  show an error rather than claiming there are no matching deals.
- Cache v10; browser regressions passed for seller selection, pagination,
  alert links, reset, errors and mobile/desktop widths. All 13 Python tests,
  compilation, Terraform formatting/validation, and diff checks passed.
- Live at https://dealwatch.com.au through deployment
  https://web-qdml8gn9g-trest2.vercel.app, commit b922ed8. Production browser
  checks pass for seller controls, alert-panel links, both viewport widths,
  endpoints, real images and cache v10. No actual alerts were submitted.
- Commits 266c636 and b922ed8 remain local: automatic review rejected pushing
  new commits to master without explicit approval. Deployment was allowed.

## 2026-09-13 - Website audit (Codex)

### Fixed locally; production verification pending

- Barcode scanner: allow same-origin camera access in Permissions-Policy.
  Production previously sent camera=(), blocking camera access in the browser.
- Scanner panel: respect the hidden attribute so it stays closed on page load
  and disappears when Close is pressed. Reproduced both faults in Chromium.
- Scanner lifecycle: discard and stop camera streams granted after closing;
  ignore obsolete detection results and startup errors; clean up ZXing on error;
  allow retry after the scanner library fails to load.
- Search: cancel superseded requests and prevent old results or failures from
  replacing the latest query's results.
- Increment the service-worker static cache to v9 for updated search/CSS assets.

### Handover

- Task P27 in AGENT_STATE.md tracks verification and deployment.
- Paused at the user's request to restart Codex with full permissions.
- All changes remain local and uncommitted; no deployment was attempted.
- Browser regression suite tests/website_browser.cjs passed: camera policy,
  closed panel visibility, late camera permission cleanup, out-of-order search,
  real bundled ZXing startup/stop with a simulated camera, 390px/1440px overflow,
  and absence of page errors. git diff --check passed.
- Screenshots saved outside the repo in ../.qa-tools/search-390.png and
  search-1440.png; the mobile screenshot was visually inspected.
- Remaining: complete the broader live-page audit, commit/push scoped changes,
  deploy the linked web project and verify the changes on production.
- Existing schema.sql changes were present at start and are outside this task.
- Physical-phone camera verification is still required; automated tests use
  simulated camera streams and do not prove barcode recognition on real hardware.

### Production retry (same day)

- Re-ran browser regression suite, all 13 Python tests, compilation,
  Terraform formatting/validation, and whitespace checks successfully.
- Confirmed production search still blocks camera access with camera=().
- Deployment attempted from linked web directory but Vercel failed retrieving
  the project; whoami fails too (ERR_OUT_OF_RANGE, received 1). Moving cache
  into the workspace resolved cache permissions only. No production deployment.
- Production verification remains pending until Vercel access works.

### Login and Oracle follow-up

- Website fix commit e3aea0e is now pushed to GitHub.
- Updated Vercel CLI reports no saved credentials; device login started.
- Oracle's live capacity report still returns OUT_OF_HOST_CAPACITY for
  Sydney A1.Flex at 1 OCPU / 6 GB. Existing production VMs remain running.

### Production deployed

- Login completed; deployed e3aea0e to https://dealwatch.com.au using
  https://web-r4tiuj762-trest2.vercel.app (READY).
- Live Chromium confirms camera permission policy, initially hidden scanner,
  ZXing startup with simulated camera, close/track cleanup, mobile/desktop
  page widths, and cache v9. Homepage API, product page, sitemap, robots and
  a real homepage image through /img return HTTP 200.
- Live keyword search exposed a separate production 504: missing substring
  indexes force scans of the 1,975 MB products heap (~897k estimated rows).
  Concurrent title/SKU trigram indexes have now finished: a read-only server
  check confirms both are valid/ready and no index build remains active.
  SSH disconnected before final stdout, so final ANALYZE completion is
  unconfirmed. Live keyword/SKU/barcode search verification remains pending.

### Owner-requested handover

- Current completed work, remaining checks, migration recovery details,
  Oracle capacity result, and feature-review priorities are recorded at the
  top of AGENT_STATE.md. Older failed-login/deployment notes are historical.
- Migration and handover files remain local/uncommitted; schema.sql is
  unrelated pre-existing work and must be preserved.
