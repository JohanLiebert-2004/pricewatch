"""The Good Guys scraper.

Headless Shopify Plus (Hydrogen/Oxygen) storefront — the usual /products.json
bulk endpoint doesn't exist on this custom frontend, but product_sitemap_1-4.xml
enumerate the full catalogue with flat one-segment URLs
(thegoodguys.com.au/<slug>, no /products/ prefix), and every product page
still carries a standard schema.org Product JSON-LD block with price, sku,
gtin and images. No bot challenge seen on plain requests - just Cloudflare CDN.

No RRP/compare-at field appears anywhere in the page (checked several
in-stock and discounted-looking items) - reference price is left to the
90-day price-history fallback (discount_feed's history_drop signal), same
as any retailer with no published RRP.
"""
import json
import re

from db import ProductRecord
from .base import BaseScraper, extract_jsonld, _num, _brand


class GoodGuysScraper(BaseScraper):
    name = "goodguys"
    sitemap_index = "https://www.thegoodguys.com.au/sitemap.xml"
    # product sitemap URLs are exactly one path segment: thegoodguys.com.au/<slug>
    product_url_pattern = r"^https://www\.thegoodguys\.com\.au/[a-z0-9][a-z0-9-]*$"
    delay = 1.5

    def parse_product(self, url, html):
        for block in extract_jsonld(html):
            if block.get("@type") != "Product":
                continue
            offer = block.get("offers") or {}
            if isinstance(offer, list):
                offer = offer[0] if offer else {}
            price = _num(offer.get("price") or offer.get("lowPrice"))
            if price is None:
                continue
            seller = offer.get("seller") or {}
            seller_name = seller.get("name", "") if isinstance(seller, dict) else ""
            # "goodguys" (self.name) never appears in "The Good Guys" (seller
            # name) - match on the spaced form instead of the base class's
            # substring check
            images = block.get("image") or []
            if isinstance(images, str):
                images = [images]
            return ProductRecord(
                retailer=self.name,
                sku=str(block.get("sku") or url.rstrip("/").split("/")[-1]),
                gtin=str(block.get("gtin13") or block.get("gtin") or block.get("gtin12") or "") or None,
                title=block.get("name") or "",
                brand=_brand(block.get("brand")),
                url=url,
                image_url=images[0] if images else None,
                price=price,
                rrp=_num(offer.get("highPrice")),
                in_stock="InStock" in str(offer.get("availability", "")),
                is_marketplace=bool(seller_name) and "good guys" not in seller_name.lower(),
            )
        return None
