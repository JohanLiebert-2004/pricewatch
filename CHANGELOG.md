# Changelog

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
