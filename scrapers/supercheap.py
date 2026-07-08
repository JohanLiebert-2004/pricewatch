"""Supercheap Auto scraper.

Salesforce Commerce Cloud (Demandware) storefront, no bot challenge on plain
requests. Every product page carries a schema.org Product JSON-LD block
(price nested under offers.priceSpecification, not offers.price) plus a GA4
dataLayer items[] blob with the SKU (item_id), category tree, and — on
discounted items — the dollar `discount`, so was-price = price + discount.

Coverage is deliberately clearance-focused, not full-catalogue: the sitemap
enumerates ~518k auto parts (way past our storage budget) and robots.txt
disallows the pagination params (start=, sz=, format=ajax) that a polite
full listing sweep would need. Instead, discovery reads the server-rendered
/clearance page (first page only, no disallowed params) and queues just
those products — exactly the items a price-drop site cares about. Re-run
`index supercheap` each cycle so each clearance rotation is captured.
"""
import re

from db import ProductRecord
from .base import BaseScraper, extract_jsonld, _num, _brand

# Only /clearance server-renders its product grid (~34 items, rotating).
# The other deal categories (/deals-1, /linking-categories/on-sale, ...)
# build their grids client-side, so their HTML carries nav links only.
# Re-indexing every cycle accumulates each rotation into the queue
# (INSERT OR IGNORE), so tracked clearance coverage compounds over time.
DEAL_PAGES = ("/clearance",)


class SupercheapScraper(BaseScraper):
    name = "supercheap"
    sitemap_index = "https://www.supercheapauto.com.au/sitemap_index.xml"
    product_url_pattern = r"/p/.+/\d+\.html"
    delay = 1.5

    def discover_all(self):
        """Product URLs from the server-rendered deal category pages."""
        seen = set()
        for path in DEAL_PAGES:
            try:
                html = self.get(f"https://www.supercheapauto.com.au{path}")
            except Exception as e:
                print(f"  ! {self.name} deal page {path}: {e}")
                continue
            for m in re.finditer(r'href="(/p/[^"?#]+\.html)', html):
                url = f"https://www.supercheapauto.com.au{m.group(1)}"
                if url not in seen:
                    seen.add(url)
                    yield url

    def discover(self, limit: int = 50):
        urls = []
        for url in self.discover_all():
            urls.append(url)
            if len(urls) >= limit:
                break
        return urls

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
