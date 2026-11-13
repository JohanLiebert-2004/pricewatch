# Black Friday 2026

Public page: https://dealwatch.com.au/black-friday.html

`web/black-friday.html`, its scoped CSS/JS, and `black-friday-offers.json`
combine a live tracked shortlist with manually researched official offers.
There are nine initial product offers, four retailer promotions and a
directory of 24 Australian retailer offer pages. Directory links alone do
not indicate active offers or continuous price tracking.

The tracked shortlist reads up to 12 discounts of 20–85% per supported store,
with four read-only API tasks at a time. It checks each candidate against
`product_search`: matching current price, seen within 36 hours, and no
marketplace seller. Larger reference-price gaps require individual review.
The existing catalogue remains the route to a full search. Partial API
failures are reported and never converted to cached prices or zero prices.

Recommendation ordering considers percentage savings, dollar savings,
recorded 30-day lows and store variety. Reference-price labels are retained;
neither RRP nor the recorded 90-day high establishes a market-wide low.

## Reviewing external offers

Manually sourced product prices stop showing after 48 hours without review,
or at `expires_at`, whichever is earlier. The browser rechecks expiry every
minute. Each record can override `reviewed_at`; the root date is the default.
Confirm the exact model, colour, bundle, sale price, reference basis, stock
and conditions on the official source before updating an offer and its date.
Do not advance the review date without rechecking. Preserve source URLs.

Initial sources reviewed 13 November 2026:

- Dyson: https://www.dyson.com.au/deals and the linked product pages.
- JB Hi-Fi: https://catalogue.jbhifi.com.au/2026/11/12-11-bfb/index.html
- adidas: https://www.adidas.com.au/black_friday and linked product pages.
  Listed sale prices exclude the additional Singles Day cart promotion.
- IKEA: https://www.ikea.com/au/en/files/pdf/e0/d5/e0d55881/ikea-au-black-friday-2026-10-off-almost-everything-terms-and-conditions.pdf
- Lenovo: https://www.lenovo.com/au/en/d/deals/black-friday-sale/
- Breville: https://www.breville.com/en-au and linked Black Friday 2026 terms.
- Samsung: official Australian Newsroom announcement of 4 November 2026,
  linked from the page. Individual flash discounts are not assumed current.

Promotion cards are dated editorial references, not automatically refreshed
storewide claims. Review their text and dates as promotions change.
Local product thumbnails come from the linked retailers' image CDNs.

## Validation and release

`tests/black_friday_browser.cjs` exercises filters, price mismatches, stale
listings, expired/manual offers, API failure/recovery and responsive layout.
Use the existing Playwright/Chromium setup described in other browser tests.

`scripts/black_friday_navigation.py <public-web-directory>` adds the entry
banner/navigation on five primary pages, the sitemap entry and service-worker
version. It is idempotent. For production, apply it to a copy of the current
live release, then overlay only the new Black Friday files. Follow `DEPLOY.md`
for the atomic release switch; do not deploy unrelated working-tree files.
