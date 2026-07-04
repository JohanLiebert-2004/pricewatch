"""Officeworks scraper.

Verified July 2026: product URLs come from sitemap-products.xml (~40k products).
Product pages have NO JSON-LD; price lives in an embedded Redux state as cents:
  "owProductPrice":{"price":{"<SKU>":{"price":7000,...}}}
GTIN and brand live in the attributes array of the same state.
"""
import re

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
