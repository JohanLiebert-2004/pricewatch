# Bargain Radar review — 29 October 2026

Reviewed against the current Dealwatch source in `pricewatch/`.

## Evidence and limits

Inspected the public [homepage](https://www.bargainradar.com.au/), downloaded HTML and its public `main-QXLA5FVI.js` bundle, and examined search-indexed [FAQ](https://bargainradar.com.au/faq) and product content. The live page is an Angular client application; the downloaded main bundle is approximately 1.67 MB before compression. That is a source-size observation, not a measured download time or performance score.

The browser connector exposed no available browsers. No account was created, alert email sent, authenticated API accessed, or end-to-end delivery tested. Search-indexed product prices may be old; they are feature examples, not current shopping recommendations. Backend technology, collection frequency, retailer coverage completeness and claimed real-time behaviour cannot be established from these public pages alone.

## How the product works from a shopper's perspective

1. Discover products using search, store/category selection, price and discount ranges, sorting and featured reductions. The live bundle contains these filter states and word-matching/semantic-search options; their result quality was not tested.
2. Inspect a product's current/reference prices, recorded minimum, last movement and history chart. The [FoodSaver page](https://bargainradar.com.au/item/fb9e4dc7-b925-4203-9332-4a480dc426fe) also illustrates a same-product comparison section and a table-view option.
3. Create either a specific-product alert from a bell or a keyword alert covering selected stores and price/discount ranges. The FAQ describes duplicate suppression and account-based alert management.
4. Use history indicators to distinguish large drops, recorded lows, returning sales, repeatedly changing sale prices and increased RRPs. These are more informative than an RRP percentage alone, provided the underlying history supports them.
5. Report unavailable products or incorrect sale status from the product page.

The [Canon example](https://bargainradar.com.au/item/0ba37f77-e1a2-4dbc-8fc2-9f51e99c3e1b) includes a name-history section and a warning that a retailer may have changed what an existing listing represents. This is an especially useful data-integrity idea.

## Fit for Dealwatch

| Capability | Current Dealwatch source | Useful next step |
|---|---|---|
| Search and browse filters | Store/category/search/price controls; catalogue percentage sorting | Keep the existing lightweight frontend; improve filter discoverability if user testing identifies friction |
| Price-history decisions | Chart, history table, recorded lows/highs | Explain today's price in a short sentence; avoid treating a first sighting as an established low |
| Cross-store comparisons | Exact-barcode matches and separately labelled suggestions | Improve matching coverage; retain evidence boundaries between an exact match and a similar product |
| Alerts | Product target-price email watches; Telegram item/store subscriptions | Keyword + multiple-store + budget alerts are the clearest larger feature gap |
| Account management | Device-local alert list; confirmed watches persist on server | Consider cross-device management after demand is established; document the current local-list behaviour |
| Misleading sales | Retailer/history/cross-store reference sources in detection | Derive recurring-sale and RRP-change indicators from adequate historical evidence |
| Listing identity | Product identity relies on retailer/SKU and matching metadata | Detect material barcode/model/title changes and separate historical series where identity changed |
| Reports | Product-page incorrect-price email link | Structured stock/price reports with moderation would be easier to analyse at scale |

## Implemented locally

- Product-page price assessment: at the minimum in available history, dollar amount above that minimum, or insufficient observations. The explanation limits the comparison to this retailer and excludes delivery.
- Alert target shortcuts: use the recorded low where there are observations on multiple dates, or 10% below the current price. These fill the target input; the user still submits and confirms the existing email flow.
- Corrected the 90-day minimum calculation for change-only snapshots: include the price already active at the window boundary. Previously it could omit that price, or use a much older all-history low when there were no changes inside the window. No recent observation now displays a dash rather than an unrelated historical minimum.
- No schema changes, new retailer requests, freshness controls, or production deployment.

## Priorities after this change

1. Keyword alerts across selected stores with minimum/maximum price and discount, email confirmation, cancellation and deduplication. Reuse existing watch infrastructure after defining matching and notification semantics. An alert for “iPhone 17” must let users avoid cases/accessories; the competitor's bundle explicitly warns about that problem.
2. Protect history when a retailer reuses a product URL/SKU for a different item. Start with strong barcode/model changes, then review title changes; do not reset history for harmless title punctuation.
3. Add history-backed indicators for recurring sales and rising reference prices, with understandable explanations and minimum evidence requirements.
4. Add moderated structured reports and measure whether shoppers complete search → product history → retailer click or confirmed alert. Do not send email addresses or cancellation tokens to analytics.

Avoid copying their badge density without testing comprehension. Dealwatch can improve decisions with plain language and its existing history/comparison features.

## Validation

`node tests/price_context.cjs` passed calculations, edge cases and inline-script syntax. `python -m unittest discover -s tests -p test_preview_seo.py` passed all 10 tests, covering shared product-template rendering and metadata. Browser appearance and live alert delivery were not verified. Changes are local and have not been deployed.
