"""BIG W scraper.

JSON-LD Product blocks on product pages. Marketplace listings link to
/marketplace/sellers and show "Sold & shipped by <seller>". The strikethrough
reference price renders as $<amount> near a wasPrice flag in the page state.
Akamai-protected: requires curl_cffi (chrome99_android profile passes).

Fast path (July 2026): live category pages server-render up to 144 products
per page (?page=N&perPage=144) inside __NEXT_DATA__ at
props.pageProps.results.organic.results - each with SKU code, GTIN, brand,
price (cents) and a marketplace flag. serializedData.category on any category
page carries the full ~4k-node category tree for enumeration.
"""
import html as htmllib
import json
import re

from db import ProductRecord
from .base import BaseScraper, Blocked


class BigWScraper(BaseScraper):
    name = "bigw"
    sitemap_index = "https://www.bigw.com.au/sitemap.xml"
    product_url_pattern = r"/product/.+/p/\d+"
    delay = 2.5   # 1.75s got the IP flagged within hours (403 on request #1);
                  # 2.5s + 3-hourly sweeps is the proven-stable rate
    needs_impersonation = True
    warmup_url = "https://www.bigw.com.au/"

    def parse_product(self, url, raw):
        rec = super().parse_product(url, raw)
        if not rec:
            return None
        text = htmllib.unescape(raw)
        if rec.rrp is None:
            rec.rrp = self._was_price(text, rec.price)
        if not rec.is_marketplace:
            rec.is_marketplace = "/marketplace/sellers" in text
        return rec

    # -- fast listing refresh ----------------------------------------------
    per_page = 144  # max the site honours; 200 returns nothing
    seed_category = "/toys/lego/c/6822101"   # any live category works as seed

    def _next_data(self, url):
        html = self.get(url)
        m = re.search(r'<script id="__NEXT_DATA__" type="application/json">'
                      r"(.*?)</script>", html, re.S)
        if not m:
            raise Blocked(f"{self.name}: no __NEXT_DATA__ on {url}")
        return json.loads(m.group(1))

    def _category_paths(self):
        """Leaf category url paths from the tree embedded in any category page.

        Entries look like {"id": "6822101", "t": "LEGO", "s": "lego",
        "d": 3, "e": true, "h": false}. Category ids nest by prefix
        (68 -> 681 -> 6822101), so a leaf is an id no other id extends.
        The site routes /{slug}/c/{id} on the id alone.
        """
        data = self._next_data(f"https://www.bigw.com.au{self.seed_category}")
        cats = (data["props"]["pageProps"].get("serializedData", {})
                .get("category") or [])
        nodes = {str(c["id"]): str(c.get("s") or "c")
                 for c in cats
                 if isinstance(c, dict) and c.get("id")
                 and c.get("e") and not c.get("h")}
        ids = sorted(nodes)
        leaves = []
        for i, cid in enumerate(ids):
            nxt = ids[i + 1] if i + 1 < len(ids) else ""
            if not nxt.startswith(cid):    # sorted order puts children next
                leaves.append(f"/{nodes[cid]}/c/{cid}")
        return leaves

    def refresh_listings(self, budget: int = 1600, state: dict | None = None):
        """Yield ProductRecords from category listing pages (144/page).

        `state` maps category path -> ISO timestamp of its last completed
        sweep. Categories are visited stalest-first and the dict is mutated
        in place as each one completes, so a run that gets blocked mid-way
        doesn't make the next run re-sweep the same head categories while
        the tail starves — clearance drops anywhere in the store get seen
        within a few runs.
        """
        from datetime import datetime, timezone
        state = state if state is not None else {}
        used = 0
        try:
            paths = self._category_paths()
        except Blocked:
            raise
        used += 1
        paths.sort(key=lambda p: state.get(p, ""))
        for path in paths:
            page = 0
            while used < budget:
                url = (f"https://www.bigw.com.au{path}"
                       f"?page={page}&perPage={self.per_page}")
                data = self._next_data(url)
                used += 1
                org = (data["props"]["pageProps"].get("results") or {}) \
                    .get("organic") or {}
                results = org.get("results") or []
                for item in results:
                    rec = self._record_from_listing(item)
                    if rec:
                        yield rec
                page += 1
                if len(results) < self.per_page or \
                        page >= (org.get("pageCount") or 1):
                    state[path] = datetime.now(timezone.utc).isoformat(
                        timespec="seconds")
                    break
            if used >= budget:
                return

    def _record_from_listing(self, item) -> ProductRecord | None:
        code = item.get("code")
        derived = item.get("derived") or {}
        info = item.get("information") or {}
        amount = ((derived.get("priceRange") or {}).get("min") or {}).get("amount")
        if not code or amount is None:
            return None
        idents = item.get("identifiers") or {}
        return ProductRecord(
            retailer=self.name,
            sku=str(code),
            gtin=str(idents.get("gtin") or idents.get("ean") or "") or None,
            title=info.get("name") or str(code),
            brand=info.get("brand"),
            url=f"https://www.bigw.com.au/product/p/{code}",
            price=float(amount) / 100.0,
            rrp=None,
            in_stock=not derived.get("soldOut", False),
            is_marketplace=bool(derived.get("marketplaceProduct")),
        )

    @staticmethod
    def _was_price(text, price):
        pats = (
            r"[Ww]as\s*\$\s*([\d,]+\.?\d*)",
            r'was[-_]?price[^$]{0,200}?\$([\d,]+\.?\d*)',
            r'"was[A-Za-z]*"\s*:\s*\{[^}]*?"?(?:value|amount)"?\s*:\s*"?([\d.]+)',
            r'"wasPrice"\s*:\s*"?([\d.]+)"?',
        )
        for pat in pats:
            for m in re.finditer(pat, text, re.I):
                try:
                    v = float(m.group(1).replace(",", ""))
                except ValueError:
                    continue
                if v > price:
                    return v
        return None
