"""Kmart and Target AU scrapers.

Both are Kmart Group sites behind Akamai; product pages require curl_cffi
Chrome impersonation and still get challenged often from datacenter IPs.

Fast path (July 2026):
- Kmart's storefront search/browse is Constructor.io. Its public API at
  ac.cnstrc.com (Constructor's own CDN, NOT behind Kmart's Akamai) returns
  200 products per request with price, list/promo price history, APN (GTIN)
  and seller - the whole ~250k catalogue is coverable in ~1,300 requests.
- Target renders 48 products per category listing page inside __NEXT_DATA__
  (offerPrice, wasPrice, RRP included); /c/all-products/AP01 paginates the
  full ~26k catalogue in ~550 listing fetches.
"""
import json
import re
import time

import httpx

from db import ProductRecord
from .base import BaseScraper, Blocked

KMART_CONSTRUCTOR_KEY = "key_GZTqlLr41FS2p7AY"  # public, shipped in page JS


class KmartScraper(BaseScraper):
    name = "kmart"
    sitemap_index = "https://www.kmart.com.au/sitemap.xml"
    product_url_pattern = r"/product/.+-\d+/?$"
    delay = 2.5
    needs_impersonation = True
    warmup_url = "https://www.kmart.com.au/"

    # -- fast listing refresh via Constructor.io ---------------------------
    api_delay = 0.6           # polite pause between API calls (not Akamai)
    per_page = 200

    def _api(self):
        return httpx.Client(
            timeout=25,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                     "Accept": "application/json"},
            params={"key": KMART_CONSTRUCTOR_KEY, "i": "pricewatch", "s": "1",
                    "c": "ciojs-client-2.35.2"})

    def _leaf_groups(self, client):
        """Yield (group_id, name, count) for groups small enough to paginate.

        Constructor caps the paging window (~10k items), so descend into
        children until each group is under the cap.
        """
        r = client.get("https://ac.cnstrc.com/browse/groups")
        r.raise_for_status()
        roots = r.json().get("response", {}).get("groups", [])

        def walk(g):
            count = g.get("count")
            kids = g.get("children") or []
            if count is not None and count <= 9800:
                if count:
                    yield g["group_id"], g.get("display_name", ""), count
            elif kids:
                for k in kids:
                    yield from walk(k)
            else:  # unknown or oversized leaf: page until empty (capped)
                yield g["group_id"], g.get("display_name", ""), None

        for root in roots:
            yield from walk(root)

    def refresh_listings(self, budget: int = 1400):
        """Yield ProductRecords for the whole catalogue via the browse API.

        budget = max API requests this run.
        """
        used = 0
        with self._api() as client:
            try:
                groups = list(self._leaf_groups(client))
            except Exception as e:
                raise Blocked(f"kmart: constructor groups fetch failed: {e}")
            used += 1
            for gid, gname, count in groups:
                max_window = 9800 // self.per_page
                pages = (min((count + self.per_page - 1) // self.per_page,
                             max_window) if count else max_window)
                for page in range(1, pages + 1):
                    if used >= budget:
                        return
                    time.sleep(self.api_delay)
                    r = client.get(
                        f"https://ac.cnstrc.com/browse/group_id/{gid}",
                        params={"num_results_per_page": self.per_page,
                                "page": page})
                    used += 1
                    if r.status_code != 200:
                        break
                    results = (r.json().get("response") or {}).get("results") or []
                    for item in results:
                        rec = self._record_from_api(item)
                        if rec:
                            yield rec
                    if len(results) < self.per_page:
                        break

    def _record_from_api(self, item) -> ProductRecord | None:
        d = item.get("data") or {}
        price = d.get("price")
        if price is None:
            return None
        sku = str(d.get("id") or "").removeprefix("P_")
        if not sku:
            return None
        # prices: list = everyday shelf price, promo = current promotion
        rrp = None
        for p in d.get("prices") or []:
            if p.get("type") == "list":
                try:
                    amt = float(p.get("amount"))
                except (TypeError, ValueError):
                    continue
                if amt > float(price):
                    rrp = amt
        seller = (d.get("Seller") or ["Kmart"])[0]
        return ProductRecord(
            retailer=self.name,
            sku=sku,
            gtin=str(d["apn"]) if d.get("apn") else None,
            title=str(item.get("value") or d.get("MerchClassName") or sku),
            brand=d.get("Brand"),
            url="https://www.kmart.com.au" + d.get("url", ""),
            image_url=d.get("image_url"),
            price=float(price),
            rrp=rrp,
            in_stock=None,
            is_marketplace=str(seller).lower() != "kmart",
        )


class TargetScraper(BaseScraper):
    name = "target"
    sitemap_index = "https://www.target.com.au/medias/feeds/sitemap/sitemap.xml"
    product_url_pattern = r"/p/.+/\w+$"
    delay = 2.5
    needs_impersonation = True
    warmup_url = "https://www.target.com.au/"

    per_page = 48  # fixed by the site
    all_products_path = "/c/all-products/AP01"

    def refresh_listings(self, budget: int = 700):
        """Yield ProductRecords by paging the all-products listing.

        Server-rendered __NEXT_DATA__ carries 48 products per page with
        offer/was/RRP prices. Still behind Akamai, so this can raise Blocked -
        the caller stores whatever was yielded before the block.
        """
        used = 0
        page = 1
        while used < budget:
            url = (f"https://www.target.com.au{self.all_products_path}"
                   f"?page={page}")
            html = self.get(url)          # politeness + challenge detection
            used += 1
            m = re.search(
                r'<script id="__NEXT_DATA__" type="application/json">(.*?)'
                r"</script>", html, re.S)
            if not m:
                raise Blocked(f"{self.name}: no __NEXT_DATA__ on {url}")
            try:
                pl = (json.loads(m.group(1))["props"]["pageProps"]
                      ["metadata"]["productList"])
            except (KeyError, TypeError, json.JSONDecodeError):
                break
            products = pl.get("products") or []
            for p in products:
                rec = self._record_from_listing(p)
                if rec:
                    yield rec
            total = pl.get("totalNumProducts") or 0
            if not products or page * self.per_page >= total:
                break
            page += 1

    def _record_from_listing(self, p) -> ProductRecord | None:
        price = (p.get("price") or {})
        offer = price.get("offerPrice")
        if not offer:
            return None
        was = price.get("wasPrice") or 0
        rrp = price.get("recommendedRetailPrice") or 0
        ref = max(float(was), float(rrp))
        return ProductRecord(
            retailer=self.name,
            sku=str(p.get("id") or ""),
            gtin=None,  # not exposed in listings; page crawl enriches it
            title=p.get("title") or "",
            brand=None,
            url=p.get("baseProductUrl") or "",
            price=float(offer),
            rrp=ref if ref > float(offer) else None,
            in_stock=None,
            is_marketplace=False,
        )
