"""Supercheap Auto scraper.

Salesforce Commerce Cloud (Demandware) storefront, no bot challenge on plain
requests. Every product page carries a schema.org Product JSON-LD block
(price nested under offers.priceSpecification, not offers.price) plus a GA4
dataLayer items[] blob with the SKU (item_id), category tree, and — on
discounted items — the dollar `discount`, so was-price = price + discount.

Full-catalogue discovery via the product sitemaps (~518k URLs across the
`sitemap_N-product.xml` children of sitemap_index.xml) using the base
class's generic discover/discover_all — no override needed, same pattern as
Officeworks/Good Guys. robots.txt only disallows pagination query params on
category browsing pages (start=, sz=, format=ajax); sitemap XML files are
unaffected. Storage checked empirically before this expansion (~336
bytes/row measured against production) - see PROJECT_NOTES.md.
"""
from db import ProductRecord
from .base import BaseScraper, extract_jsonld, _num, _brand


class SupercheapScraper(BaseScraper):
    name = "supercheap"
    sitemap_index = "https://www.supercheapauto.com.au/sitemap_index.xml"
    product_url_pattern = r"/p/[^/]+/[A-Za-z0-9]+\.html"
    delay = 1.5

    def parse_product(self, url, html):
        for block in extract_jsonld(html):
            if block.get("@type") != "Product":
                continue
            offer = block.get("offers") or {}
            if isinstance(offer, list):
                offer = offer[0] if offer else {}
            spec = offer.get("priceSpecification") or []
            if isinstance(spec, dict):
                spec = [spec]
            price = _num(offer.get("price")) or \
                (_num(spec[0].get("price")) if spec else None)
            if price is None:
                continue
            sku, rrp = self._datalayer(html, price)
            images = block.get("image") or []
            if isinstance(images, str):
                images = [images]
            return ProductRecord(
                retailer=self.name,
                sku=sku or url.rstrip("/").split("/")[-1].removesuffix(".html"),
                gtin=str(block.get("gtin13") or block.get("gtin") or "") or None,
                title=block.get("name") or "",
                brand=_brand(block.get("brand")),
                url=url,
                image_url=images[0] if images else None,
                price=price,
                rrp=rrp,
                in_stock="InStock" in str(offer.get("availability", "")),
                is_marketplace=False,   # SCA sells first-party only
            )
        return None

    @staticmethod
    def _datalayer(html, price):
        """SKU and was-price from the GA4 items[] blob.

        `discount` is the dollar amount off, so the pre-sale price is
        price + discount (verified against clearance items' strike price).
        """
        m = re.search(r'"item_id"\s*:\s*"(\d+)"', html)
        sku = m.group(1) if m else None
        rrp = None
        d = re.search(r'"discount"\s*:\s*([\d.]+)', html)
        if d:
            try:
                off = float(d.group(1))
                if off > 0:
                    rrp = round(price + off, 2)
            except ValueError:
                pass
        return sku, rrp
