# Review triage - 13 September 2026

The supplied external review is useful direction, but several claimed gaps
already have implementations. Owner subsequently authorized implementation
in phases; AGENT_STATE.md tracks the active phase and remaining decisions.

Phases 1 and 2 are now live: default marketplace exclusion/opt-in and alert
copy, separate visible barcode comparisons, and a free/no-affiliate About
page with navigation and canonical redirects. Owner supplied the no-affiliate
policy. Phase 3 is saved as a working checkpoint at the owner's request:
batched sparklines, category fixes and visual polish are implemented and
locally tested; the additive history RPC is live, but the Phase 3 frontend
and categorisation code are not deployed. See AGENT_STATE.md for remaining
real-data visual review, category repair and deployment steps.

| Suggestion | Verified state | Proposed scope |
|---|---|---|
| Marketplace filtering | Homepage labels marketplace stock but has no first-party filter. | Prioritize a sold-by-retailer filter and review default ranking. Current data tracks retailer/SKU, not separate marketplace seller identities, so do not promise seller-specific histories. |
| Card sparklines | Product pages already have price-history charts. Feed cards do not. | Use batched or precomputed history; avoid one database request per card. |
| More retailers | Separate integration and reliability work. | Verify official feed/API access before committing to any retailer. |
| Alerts clarity | Product pages already offer email and Telegram controls. | Explain both channels beside feed CTAs. Back-in-stock is a separate feature requiring reliable stock transitions. |
| Cross-retailer matching | Product pages already distinguish exact barcodes, model codes and similar listings. | Make exact matches more prominent; preserve variant/seller distinctions. |
| About/methodology | /how-it-works.html is live (HTTP 200) and linked in footers. No separate about.html. | Improve navigation and funding explanation; ask owner what identity they wish to publish. |
| Crowdsourced freshness | Incorrect-price reporting exists. Earlier community panel was removed at owner request. | Consider lightweight reports, but verify them before expiring deals. Do not restore the removed panel without new direction. |
| Categories | Existing scrapers use retailer-native subcategories where available; some categories remain inferred. | Audit actual misclassifications and prioritize native taxonomy before heuristic/embedding changes. |

Recommended order: production reliability, marketplace presentation, alerts
clarity, exact comparisons, then sparklines. Expanding retailer coverage should
not take priority over freshness and reliability of current coverage.
