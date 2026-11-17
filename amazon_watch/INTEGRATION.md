# Amazon on Dealwatch

The collector, history and fleet code live in `amazon_watch/`. The public page is `web/amazon.html`, with external `amazon.js` and `amazon.css` using Dealwatch's existing light theme and navigation. It keeps the requested three columns, total and matching counts, price bounds, discount sorting and both price directions.

Install collector requirements from `amazon_watch/requirements.txt`. Set `AMAZON_WATCH_DATA_DIR` to the existing private runtime directory to retain its SQLite history, SSH inventory and logs. From the repository root, run `python -m amazon_watch.fleet watch`. The Windows scheduler must stay awake; Linux Firefox checks products for Perth 6000. Oracle nodes currently collect community leads only because their bounded Firefox probes failed. No independent-machine double check or confirmed pricing error is claimed.

`generate_report` writes `amazon-public.json` beside the private data. Its explicit field allowlist excludes fleet diagnostics, infrastructure names, keys, job IDs, seller identifiers, and foreign-delivery observations. Only AU/6000 observations with a positive integer item price are exported. Community quotes remain separate and unverified. Stale product prices stop appearing after 15 minutes on the page.

Publication is opt-in through a private `publish-target.json` in the runtime directory containing `host`, `key` (a local key path) and `remote_directory` (`/var/lib/dealwatch-amazon/public`). Existing strict-host-key SSH is used; keys are not copied. A validated JSON upload is atomically renamed into place at most once a minute. Failures are stored in private `publication-status.json` and do not stop price collection.

The static release's `amazon-deals.json` symlink points to that public snapshot, so future price changes need no site rebuild or nginx reload. `scripts/deploy_amazon_release.sh` copies the exact current public release, overlays the three Amazon assets, adds navigation/sitemap entries and bumps the service-worker cache. It preserves the TLS vhost, database and other collectors. It records the old release, atomically switches `current`, and restores it if its HTTP checks fail. Prior releases are retained.

Verification: `python -m pytest amazon_watch/tests tests/test_amazon_publication.py`, the repository unittest suite, Python compilation, `node --check web/amazon.js`, Terraform fmt/validate, and public page/feed/navigation/API/image/product/sitemap checks. Browser QA depends on available browser control and must not be claimed from HTTP checks alone.
