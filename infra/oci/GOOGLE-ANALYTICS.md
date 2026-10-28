# Google Analytics 4

GA4 measurement ID: `G-3V1XFPB2L7`. Collection began 28 October 2026.

The website loads `/analytics.js` once from each of its 13 HTML templates.
Product and landing templates used by the SSR service also include it.
The script installs the Google tag directly; the existing Tag Manager
container `GTM-MF4PFW6M` remains installed. Do not configure another GA4
tag for this same property in GTM, which would duplicate page views.

One automatic page view is sent by the Google tag config. Page/referrer
query strings and fragments are removed. URLs containing sensitive parameter
names or email-like query values skip GA4 entirely. Google signals and
advertising personalization are disabled in the configuration. The privacy
page describes browser analytics and cookies.

Deploy `analytics.js` with every static release. `scripts/install_ga4.py`
adds the shared script to existing HTML without replacing unrelated page
content. Apply it to the static release and `/opt/pricewatch/web` product and
landing templates; the service reads those templates per request, so no
backend restart is needed. Keep the prior release and template backups.
The existing nginx CSP already permits the required Google endpoints.

Validation: `node tests/analytics.cjs` checks all templates, idempotent
initialization, URL/referrer sanitation and sensitive-link exclusion.
Live Chromium verification observed one `page_view` accepted with HTTP 204
on the homepage, catalogue, product and retailer routes, clean event URLs,
no collection on a test token URL and no CSP or JavaScript errors.

Use Google Analytics Reports > Realtime for new activity. Country, device
and acquisition reports require processing; no historical server-log counts
are imported. An HTTP 204 proves event receipt by Google's collection
endpoint, not that reports have completed processing.
