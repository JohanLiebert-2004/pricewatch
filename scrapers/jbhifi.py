"""JB Hi-Fi scraper.

Shopify store behind Cloudflare Workers — no bot challenge on the public
storefront JSON. /products.json?limit=250&page=N pages through the catalogue
ordered by published_at desc, but Shopify caps page*limit at 25,000, so one
sweep sees the 25k most recently published products (JB republishes items it
is actively merchandising, which is the slice that produces clearance deals).

Each product carries variants with sku, price and compare_at_price (the
strike-through price when discounted), plus product_type — a real category,
better than guessing from titles.
"""
import json

from db import ProductRecord
from .base import BaseScraper

# JB product_type -> our site categories
_TYPE_CAT = {
    "COMPUTERS": "tech", "IT": "tech", "AUDIO": "tech", "CAMERAS": "tech",
    "VISUAL": "tech", "COMMUNICATIONS": "tech", "ACCESSORIES": "tech",
    "GAMES HARDWARE": "tech", "GAMES SOFTWARE": "toys",
    "HOME TECH": "tech", "WEARABLES & OUTDOOR": "tech",
    "TELCO SERVICES": "tech",
    "SMALL APPLIANCES": "kitchen", "WHITEGOODS": "home",
    "HEALTH & BEAUTY": "beauty",
    "MOVIES": "other", "MUSIC": "other",
}

# JB product_type -> store-specific chip labels (products.subcategory)
_TYPE_SUBCAT = {
    "COMPUTERS": "Computers & Tablets", "IT": "Computers & Tablets",
    "COMMUNICATIONS": "Phones", "TELCO SERVICES": "Phones",
    "VISUAL": "TVs & Home Cinema", "HOME TECH": "Smart Home",
    "AUDIO": "Audio & Headphones", "CAMERAS": "Cameras & Drones",
    "GAMES HARDWARE": "Gaming", "GAMES SOFTWARE": "Gaming",
    "SMALL APPLIANCES": "Appliances", "WHITEGOODS": "Appliances",
    "HEALTH & BEAUTY": "Health & Beauty",
    "MOVIES": "Movies & Music", "MUSIC": "Movies & Music",
    "WEARABLES & OUTDOOR": "Wearables & Outdoor",
    "ACCESSORIES": "Accessories",
}

PAGE_SIZE = 250
MAX_PAGES = 100          # Shopify refuses page*limit > 25,000


class JBHiFiScraper(BaseScraper):
    name = "jbhifi"
    sitemap_index = "https://www.jbhifi.com.au/sitemap.xml"
    product_url_pattern = r"/products/."
    delay = 1.0

    # -- fast listing refresh ------------------------------------------------
    def refresh_listings(self, budget: int = 120):
        pages = min(budget, MAX_PAGES)
        for page in range(1, pages + 1):
            raw = self.get(f"https://www.jbhifi.com.au/products.json"
                           f"?limit={PAGE_SIZE}&page={page}")
            products = json.loads(raw).get("products") or []
            for item in products:
                rec = self._record_from_listing(item)
                if rec:
                    yield rec
            if len(products) < PAGE_SIZE:
                break

    def _record_from_listing(self, item) -> ProductRecord | None:
        variants = item.get("variants") or []
        priced = []
        for v in variants:
            try:
                priced.append((float(v["price"]), v))
            except (KeyError, TypeError, ValueError):
                continue
        if not priced:
            return None
        # cheapest orderable variant is the price a shopper actually sees
        price, var = min(priced, key=lambda t: t[0])
        rrp = None
        try:
            cap = float(var.get("compare_at_price") or 0)
            if cap > price:
                rrp = cap
        except (TypeError, ValueError):
            pass
        images = item.get("images") or []
        return ProductRecord(
            retailer=self.name,
            sku=str(var.get("sku") or item["id"]),
            gtin=str(var.get("barcode") or "") or None,
            title=item.get("title") or str(item["id"]),
            brand=item.get("vendor"),
            category=_TYPE_CAT.get((item.get("product_type") or "").upper()),
            subcategory=_TYPE_SUBCAT.get((item.get("product_type") or "").upper()),
            url=f"https://www.jbhifi.com.au/products/{item['handle']}",
            image_url=(images[0].get("src") if images else None),
            price=price,
            rrp=rrp,
            in_stock=any(v.get("available") for v in variants),
            is_marketplace=False,      # JB has no third-party marketplace
        )
