# Dealwatch SEO rollout

## Deployment status — 19 September 2026

Static SEO changes are live at https://dealwatch.com.au via deployment
https://web-kli7m9t1e-trest2.vercel.app. Verified live titles on the homepage,
clearance, catalogue and search pages; homepage 308 redirect; raw-template
noindex; and successful robots, sitemap and bootstrap responses. Clearance
layout checks pass at 320, 390 and 1440px; the homepage browser suite passes.

The server-rendered retailer/category improvements still require deploying
services/preview_app.py and web/landing.html together on OCI. SSH access remains
unavailable. Search Console inspection/submission is also outstanding.

## Implemented locally — 19 September 2026

- Descriptive Australian shopping titles for catalogue, clearance and search;
  catalogue description explains price history and alerts.
- Visible homepage buying guidance with crawlable retailer/category links.
- Category-specific comparison advice and independent-retailer context in
  server-rendered landing pages, plus contextual links and breadcrumb markup.
- Recorded check dates on landing cards; removed unsupported freshness and
  unqualified lowest-price claims from those pages.
- Permanent `/index.html` redirect to `/`; `X-Robots-Tag: noindex, follow`
  for the raw `/landing.html` template only. Rendered landing pages remain indexable.

These changes do not guarantee indexing or a particular ranking. The owner-selected
focus is Australian clearance deals and cheap products, with price history as
supporting evidence. Homepage and clearance titles, descriptions and visible
content reflect this focus without claiming every deal is clearance or the
cheapest price in Australia.

## Live audit baseline

Read-only checks on 19 September returned HTTP 200 for robots.txt, sitemap.xml,
sitemap-pages.xml, /deals/books and /retailers/kmart. The sitemap index listed
398 child sitemaps and the static sitemap listed 29 URLs. Sample landing pages
had self-referencing canonicals. This is not proof that Google indexed the URLs;
individual product sitemap coverage and Google-selected canonicals still need
Search Console inspection.

The homepage's public freshness summary was still dated September 10–13.
Restoring publication is an operational priority: better copy cannot compensate
for old prices. See DEPLOY.md for the independent publication recovery command.

## Release and measurement

1. Restore authenticated Vercel, GitHub and OCI access. Deploy web/ to Vercel
   and services/preview_app.py plus web/landing.html to the OCI web service
   together, following DEPLOY.md. Static Vercel deployment is complete; OCI
   deployment remains outstanding.
2. Verify the `/index.html` permanent redirect, raw-template noindex header,
   rendered landing page canonicals/breadcrumbs, product pages, and sitemaps.
   Check desktop/mobile layouts and that links work with JavaScript disabled.
3. In the verified Google Search Console domain property, submit
   https://dealwatch.com.au/sitemap.xml. Inspect the homepage, one retailer,
   one category and representative product URLs. Review rendered HTML,
   crawl access, indexing reasons and Google-selected canonicals; request
   indexing for the changed representative pages.
4. Validate a product page and a landing page with Google's Rich Results Test.
   Passing local JSON checks is not equivalent to Google rich-result approval.
5. Record a 28-day Search Console baseline for impressions, clicks, CTR and
   average position, grouped by page and query. Review after recrawling and
   over subsequent weeks. Use actual queries to choose further useful content.
   Check Core Web Vitals before claiming any performance improvement.

No Search Console credentials, verification tokens, indexing reports or ranking
baseline were available in this session. Do not invent verification records,
ratings, review counts, stock guarantees or keyword-ranking claims.

## Google guidance

- [Search developer guide](https://developers.google.com/search/docs/fundamentals/get-started-developers)
- [Descriptive title links](https://developers.google.com/search/docs/appearance/title-link)
- [Crawlable links](https://developers.google.com/search/docs/crawling-indexing/links-crawlable)
