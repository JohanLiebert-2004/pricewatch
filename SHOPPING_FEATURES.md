# Shopping tools

Open https://dealwatch.com.au/alerts.html#shopping for wishlists, saved searches,
keyword alerts and existing product watches. Wishlists and saved searches stay
in the current browser. Clearing browser data removes those lists; confirmed
email subscriptions continue until cancelled through their email link.

Keyword alerts match every keyword in a title, in any order. Excluded words
reject a title if any occurs. Store selection, minimum/maximum price and minimum
RRP discount are optional filters; maximum price is required. Marketplace offers
and records not checked within the last seven days do not trigger email. Checks
run through the existing hourly detect job; actual delivery follows that job's
completion. Each check emails up to ten new matches or products cheaper than
their last emailed price, with up to 100 subscriptions processed per run.

The management token returned during creation can cancel/check status but cannot
activate a subscription. The separate confirmation secret is sent only by email;
unconfirmed links expire after seven days. Anonymous roles cannot read tables.

## Setup and rollout

Apply scripts/shopping_features.sql as the PostgreSQL administrator before
publishing keyword_alerts.py and the watch_alerts.py integration. This additive
migration is separate from schema.sql/views.sql; it does not rebuild feeds.
It grants the existing pricewatch worker narrow access to the new tables.
The existing GitHub Actions RESEND_API_KEY, RESEND_FROM and SITE_URL secrets are
reused. Do not add a second scheduler or copy these secrets to the website host.

Serve shopping.js and shopping.css with the changed public pages. The SSR
product template at /opt/pricewatch/web/product.html must also be updated.
Production uses atomic releases under /var/www/dealwatch/releases. The source
homepage has earlier local changes beyond the live version; this release applied
only the shopping entry points/assets to the actual live homepage baseline.

## Verification and rollback

103 Python tests passed. tests/shopping_browser.cjs verifies wishlist persistence,
price-fetch failure, saved-filter restoration, keyword registration payload,
confirmation/cancellation, product confirmation, target shortcuts and mobile/
desktop overflow using isolated fixtures. tests/price_context.cjs and analytics
checks passed. PostgreSQL role and lifecycle tests passed with all test records
rolled back; no QA email was sent.

Live tests verified the actual catalogue-backed wishlist flow and keyword form/
API validation with no script errors. Homepage summary API initially timed out
under load and returned 200 on retry; the new migration does not change that RPC.
Recipient inbox delivery is not claimed as tested.

Source commit: b12623d360f18418b7b53ee287c131184f2ec1f1.
Website release: /var/www/dealwatch/releases/shopping-20261029T035052Z.
Previous: /var/www/dealwatch/releases/catalogue-20261028T064742Z.
Template backup: /var/backups/dealwatch/shopping-20261029T035052Z/product.html.
Host manifest: /var/tmp/dealwatch-shopping-release.json.

For rollback, atomically point current at the previous release, restore the
backed-up SSR template and revert the worker integration in a focused commit.
Leave the additive tables intact so subscriber information is preserved.
