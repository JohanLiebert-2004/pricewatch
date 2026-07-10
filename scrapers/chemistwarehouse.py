"""Chemist Warehouse scraper.

Next.js storefront (commercetools) - product pages are fully server-rendered
with price, RRP, SKU, brand and stock inside __NEXT_DATA__, and no bot
challenge was seen on impersonated requests. There is NO fast listing path:
robots.txt disallows /api/ (so the JSON API is off-limits), and category
pages don't embed their product lists server-side. Coverage therefore comes
entirely from the crawl-queue lane, seeded once from the products sitemap
(~26k URLs) - same pattern as Good Guys / Supercheap.

Prescription items (cwr-au-prescription-type != none) are skipped outright:
their PBS/concession pricing isn't a "deal", and advertising prescription
medicine prices to the public is restricted in Australia.
"""
import json
import re

from db import ProductRecord
from .base import BaseScraper

# their product "type" -> our site categories (rest fall back to
# categorize.py's title-based tagging)
_TYPE_CAT = {
    "skincare": "beauty", "cosmetics": "beauty", "fragrance": "beauty",
    "personal_care": "beauty", "haircare": "beauty", "beauty": "beauty",
}


class ChemistWarehouseScraper(BaseScraper):
    name = "chemistwarehouse"
    sitemap_index = "https://static.chemistwarehouse.com.au/AMS/sitemap/cwh_sitemap.xml"
    product_url_pattern = r"/buy/\d+/"
    delay = 1.5
    needs_impersonation = True
    impersonate = "chrome124"

    def parse_product(self, url, html):
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                      html, re.S)
        if not m:
            return None
        try:
            pp = (json.loads(m.group(1))["props"]["pageProps"]["product"])
        except (json.JSONDecodeError, KeyError, TypeError):
            return None
        prod = pp.get("product") or {}
        variants = prod.get("variants") or []
        primary = next((v for v in variants if v.get("isPrimary")),
                       variants[0] if variants else None)
        if not primary:
            return None
        attrs = {a.get("key"): a.get("value") for a in primary.get("attributes") or []}
        rx = attrs.get("cwr-au-prescription-type") or {}
        if isinstance(rx, dict) and rx.get("key") not in (None, "none"):
            return None   # prescription-only: not trackable as a deal
        sku = str(primary.get("sku") or "")
        price = rrp = None
        for entry in pp.get("prices") or []:
            if str(entry.get("sku")) != sku:
                continue
            p = entry.get("price") or {}
            price = ((p.get("value") or {}).get("amount"))
            rrp = ((p.get("rrp") or {}).get("amount"))
            break
        if price is None or not sku:
            return None
        in_stock = None
        for a in pp.get("availability") or []:
            if str(a.get("sku")) == sku:
                in_stock = a.get("status") == "in-stock"
                break
        brand = (primary.get("brand") or {}).get("label")
        images = primary.get("images") or []
        return ProductRecord(
            retailer=self.name,
            sku=sku,
            gtin=None,   # not exposed on the page
            title=prod.get("name") or sku,
            brand=brand,
            category=_TYPE_CAT.get(str(prod.get("type") or "").lower()),
            url=url.split("?")[0],
            image_url=(images[0].get("url") if images else None),
            price=float(price),
            rrp=float(rrp) if rrp and float(rrp) > float(price) else None,
            in_stock=in_stock,
            is_marketplace=False,
        )
