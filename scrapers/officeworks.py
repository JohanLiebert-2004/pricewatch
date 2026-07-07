"""Officeworks scraper.

Verified July 2026: product URLs come from sitemap-products.xml (~40k products).
Product pages have NO JSON-LD; price lives in an embedded Redux state as cents:
  "owProductPrice":{"price":{"<SKU>":{"price":7000,...}}}
GTIN and brand live in the attributes array of the same state.

Fast path: /catalogue-app/api/recommendations?skus=A,B,C returns
{products, prices, availabilities} for 60+ SKUs per call, no bot protection.
SKUs are recoverable straight from sitemap product URLs (last path token).
"""
import re
import time

import httpx

from db import ProductRecord
from .base import BaseScraper


class OfficeworksScraper(BaseScraper):
    name = "officeworks"
    sitemap_index = "https://www.officeworks.com.au/sitemap-products.xml"
    product_url_pattern = r"/shop/officeworks/p/"

    def parse_product(self, url, html):
        # price in cents, keyed by SKU
        m = re.search(r'"owProductPrice":\{"price":\{"([A-Z0-9\-]+)":\{"price":(\d+)', html)
        if not m:
            return super().parse_product(url, html)  # fallback if they add JSON-LD
        sku, cents = m.group(1), int(m.group(2))

        def attr(attr_id):
            a = re.search(r'\{"id":"%s","name":"[^"]*","value":"([^"]*)"' % attr_id, html)
            return a.group(1) if a else None

        title = None
        t = re.search(r'og:title[^>]*content="([^"]+)"', html) or \
            re.search(r'content="([^"]+)"[^>]*og:title', html)
        if t:
            title = re.sub(r"\s*\|\s*Officeworks.*$", "", t.group(1))

        was = re.search(r'"was(?:Price)?"\s*:\s*(\d+)', html)
        rrp = int(was.group(1)) / 100 if was else None
        price = cents / 100
        return ProductRecord(
            retailer=self.name,
            sku=sku,
            gtin=attr("gtin"),
            title=title or sku,
            brand=attr("brand"),
            url=url,
            price=price,
            rrp=rrp if (rrp and rrp > price) else None,
            in_stock=None,
        )

    # -- fast bulk refresh via the catalogue API ----------------------------
    api_delay = 0.8
    skus_per_call = 50

    @staticmethod
    def sku_from_url(url: str) -> str | None:
        """Product URLs end in the SKU: .../p/<slug>-<sku>. SKUs are the last
        hyphen-token uppercased (e.g. ...-acntrsa005 -> ACNTRSA005)."""
        tail = url.rstrip("/").rsplit("/", 1)[-1]
        sku = tail.rsplit("-", 1)[-1].upper()
        return sku if re.fullmatch(r"[A-Z0-9]{4,}", sku) else None

    def refresh_listings(self, budget: int = 900, skus: list[str] | None = None):
        """Yield ProductRecords for known SKUs in bulk (50 per API call).

        `skus` comes from the caller (products table + crawl_queue URLs);
        without it there is nothing to refresh.
        """
        if not skus:
            return
        client = httpx.Client(
            timeout=25, follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                                   "Chrome/126.0.0.0 Safari/537.36",
                     "Accept": "application/json"})
        used = 0
        with client:
            for i in range(0, len(skus), self.skus_per_call):
                if used >= budget:
                    return
                batch = skus[i:i + self.skus_per_call]
                time.sleep(self.api_delay)
                r = client.get(
                    "https://www.officeworks.com.au/catalogue-app/api/"
                    "recommendations",
                    params={"storeId": "W615", "postcode": "6000",
                            "skus": ",".join(batch)})
                used += 1
                if r.status_code != 200:
                    continue
                j = r.json()
                prices = j.get("prices") or {}
                for p in j.get("products") or []:
                    sku = p.get("sku")
                    cents = (prices.get(sku) or {}).get("price")
                    if not sku or cents is None:
                        continue
                    seo = p.get("seoPath") or p.get("urlKeyword") or ""
                    img = p.get("image") or ""
                    yield ProductRecord(
                        retailer=self.name,
                        sku=sku,
                        gtin=None,   # page crawl enriches GTIN
                        title=p.get("name") or sku,
                        brand=None,
                        url=(f"https://www.officeworks.com.au/shop/officeworks"
                             f"/p/{seo}" if seo else ""),
                        image_url=(f"https:{img}" if img.startswith("//")
                                   else img or None),
                        price=cents / 100.0,
                        rrp=None,
                        in_stock=bool(p.get("rangedOnline", True)),
                    )
