"""Supercheap Auto scraper.

Salesforce Commerce Cloud (Demandware) storefront, no bot challenge on plain
requests. The per-product schema.org Product JSON-LD block used to be
static HTML but as of ~July 2026 SCA moved it behind client-side JS
(`jsonLdScripts = document.querySelectorAll('script[type="application/
ld+json"]')` executed in-browser) — a plain HTTP fetch never sees it
anymore, which silently broke this scraper (every parse_product() call
returned None). The GA4 `view_item` dataLayer event is still emitted
server-side as inline JS (`GTM.updateDataLayerByJson({"event":"view_item",
...})`), so that's now the primary (only) data source: SKU (item_id),
title, brand, category tree, price, and — on discounted items — the dollar
`discount`, so was-price = price + discount. og:image meta tag covers the
image (JSON-LD used to provide this). No reliable stock-status signal is
present in the static HTML, so in_stock defaults to True.

Full-catalogue discovery via the product sitemaps (~518k URLs across the
`sitemap_N-product.xml` children of sitemap_index.xml) using the base
class's generic discover/discover_all — no override needed, same pattern as
Officeworks/Good Guys. robots.txt only disallows pagination query params on
category browsing pages (start=, sz=, format=ajax); sitemap XML files are
unaffected. Storage checked empirically before this expansion (~336
bytes/row measured against production) - see PROJECT_NOTES.md.
"""
import json
import re
from html import unescape

from db import ProductRecord
from .base import BaseScraper, _brand


class SupercheapScraper(BaseScraper):
    name = "supercheap"
    sitemap_index = "https://www.supercheapauto.com.au/sitemap_index.xml"
    product_url_pattern = r"/p/[^/]+/[A-Za-z0-9]+\.html"
    delay = 1.5

    def parse_product(self, url, html):
        m = re.search(r'GTM\.updateDataLayerByJson\(\s*(\{.*?"event"\s*:\s*'
                       r'"view_item".*?\})\s*\)\s*;', html)
        if not m:
            return None
        try:
            item = json.loads(m.group(1))["ecommerce"]["items"][0]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
            return None
        price = item.get("price")
        if price is None:
            return None
        price = float(price)
        discount = item.get("discount") or 0
        rrp = round(price + discount, 2) if discount > 0 else None
        img = re.search(r'property="og:image"\s+content="([^"]+)"', html)
        # item_category is the nav root ("Shop by Category"); item_category2
        # is the real department (e.g. "Car Care", "Tools", "Oils & Fluids")
        subcat = item.get("item_category2") or None
        if subcat == "Shop by Category":
            subcat = None
        return ProductRecord(
            retailer=self.name,
            sku=item.get("item_id") or url.rstrip("/").split("/")[-1].removesuffix(".html"),
            gtin=None,
            title=item.get("item_name") or "",
            brand=_brand(item.get("item_brand")),
            subcategory=subcat,
            url=url,
            image_url=unescape(img.group(1)) if img else None,
            price=price,
            rrp=rrp,
            in_stock=True,
            is_marketplace=False,   # SCA sells first-party only
        )
