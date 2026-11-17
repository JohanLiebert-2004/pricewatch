# Amazon on Dealwatch

The collector, history and fleet code live in `amazon_watch/`. The public page is `web/amazon.html`, with external `amazon.js` and `amazon.css` using Dealwatch's existing light theme and navigation. It keeps the requested three columns, total and matching counts, price bounds, discount sorting and both price directions.

Install collector requirements from `amazon_watch/requirements.txt`. Set `AMAZON_WATCH_DATA_DIR` to the existing private runtime directory to retain its SQLite history, SSH inventory and logs. From the repository root, run `python -m amazon_watch.fleet watch`. The Windows scheduler must stay awake; Linux Firefox checks products for Perth 6000. Oracle nodes currently collect community leads only because their bounded Firefox probes failed. No independent-machine double check or confirmed pricing error is claimed.

`generate_report` writes `amazon-public.json` beside the private data. Its explicit field allowlist excludes fleet diagnostics, infrastructure names, keys, job IDs and seller identifiers. Only AU/6000 observations with a positive integer item price can supply a catalogue price. Community quotes remain separate and unverified. Recent prices hides checks older than 15 minutes; the other catalogue views label older checks.

Publication is opt-in through a private `publish-target.json` in the runtime directory containing `host`, `key` (a local key path) and `remote_directory` (`/var/lib/dealwatch-amazon/public`). Existing strict-host-key SSH is used; keys are not copied. A validated JSON upload is atomically renamed into place at most once a minute. Failures are stored in private `publication-status.json` and do not stop price collection.

The static release's `amazon-deals.json` symlink points to that public snapshot, so future price changes need no site rebuild or nginx reload. `scripts/deploy_amazon_release.sh` copies the exact current public release, overlays the three Amazon assets, adds navigation/sitemap entries and bumps the service-worker cache. It preserves the TLS vhost, database and other collectors. It records the old release, atomically switches `current`, and restores it if its HTTP checks fail. Prior releases are retained.

Verification: `python -m pytest amazon_watch/tests tests/test_amazon_publication.py`, the repository unittest suite, Python compilation, `node --check web/amazon.js`, Terraform fmt/validate, and public page/feed/navigation/API/image/product/sitemap checks. Browser QA depends on available browser control and must not be claimed from HTTP checks alone.

## Sweep throughput and counts

The counter distinguishes all tracked products, successfully checked products, recorded AU item prices and prices refreshed within 15 minutes. The larger coverage figures do not claim current stock or confirmed pricing errors.

Discovery and verification are dispatched together, so a slow community VM no longer delays the Linux price worker. Community-only jobs use HTTP and a 75-second process deadline; product jobs have a 240-second work budget and a 300-second process deadline. Product requests remain serial with the original delay. The default assignment is up to 35 products, with bounded Amazon deals/electronics/computers/kitchen/toys discovery every third cycle.

Private attempt history backs off identity failures for six hours and other failed product checks for thirty minutes, instead of retrying the same unvisited ASIN every cycle. A failed browser can restart once for a later product; access refusals and verification challenges still stop that host. Publication uses one checksum-verified SSH connection on a background thread, with stale-lock recovery, so a slow publication host cannot block collection.

## Complete catalogue pagination

The default All tracked products view includes every valid tracked ASIN, paginated at 25/50/100 rows with Previous/Next and numbered pages. The new allowlisted catalogue field includes pending and unavailable products without exposing diagnostics or inventing prices. Missing prices display as a dash. Recent prices remains fresh-only; With prices includes marked older prices. Search and numeric filters operate across the whole view, with unknown prices excluded only when numeric bounds are active. Changing a filter resets page one. URL state preserves page, view, size, search and sorting, supports browser back navigation, and live refresh retains the selected page or clamps it if the result count shrinks. The original priced-products array remains compatible with older clients.

Run `node tests/amazon_pagination.cjs` to verify page coverage, distinct rows, filters, URL/back navigation and refresh retention. Set `AMAZON_LIVE_FIXTURE` to a downloaded public snapshot to verify every live catalogue ASIN is reachable across pages. Browser visual QA remains dependent on available browser control.
