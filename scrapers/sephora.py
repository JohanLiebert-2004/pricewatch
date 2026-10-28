"""Sephora AU scraper.

Sephora Asia platform (one backend serving AU/NZ/SG/MY/PH...) behind Akamai -
the local collector opts into standard Firefox after HTTP-client refusals.
The legacy HTTP transport remains the default elsewhere. Product pages are a client-rendered Vue app with no
JSON-LD, so everything goes through the storefront JSON:API instead:

  /api/v2.6/products?page[size]=500&page[number]=N&include=brand
      pages the full AU catalogue (~7.6k products) in ~16 requests.
  /api/v2.6/products/<slug>?v=<slug>
      single product, used for parse_product / live price verification.

CRITICAL: requests must send BOTH `X-Platform: Web` and `X-Site-Country: AU`
(country uppercase). Without them the API silently answers from a stale or
wrong-country price book - observed $149 vs the real $159 on the same SKU,
and Philippine-peso catalogues on list endpoints - so a missing header
poisons prices without any error. Prices are integer cents.
"""
import json
import os
import re

from db import ProductRecord
from .base import BaseScraper

API = "https://www.sephora.com.au/api/v2.6/products"
PAGE_SIZE = 500


class SephoraScraper(BaseScraper):
    name = "sephora"
    sitemap_index = "https://www.sephora.com.au/sitemap.xml"
    product_url_pattern = r"/products/[a-z0-9-]+$"
    delay = 2.0
    needs_impersonation = True
    impersonate = "chrome124"   # validated against Sephora's Akamai tier
    warmup_url = "https://www.sephora.com.au/"
    use_proxy = False  # local browser and legacy HTTP both use direct access

    # Explicit country headers keep every request on the AU price book.
    _COUNTRY_HEADERS = {"X-Platform": "Web", "X-Site-Country": "AU"}

    def __init__(self):
        mode = os.environ.get('SEPHORA_BROWSER', '').strip().lower()
        self._firefox = mode == 'firefox'
        if not mode:
            super().__init__()
        elif self._firefox:
            if os.environ.get('PROXY_URL'):
                raise ValueError('Sephora Firefox mode requires direct local collection')
            from .firefox import FirefoxSession
            self.session = FirefoxSession(host='www.sephora.com.au')
            self._cffi = False
            self._warmed = False
        else:
            raise ValueError('Unknown SEPHORA_BROWSER mode')

    def _request(self, url):
        if getattr(self, '_firefox', False):
            if url.startswith(API + '?') or url.startswith(API + '/'):
                r = self.session.fetch(url, headers=self._COUNTRY_HEADERS)
            else:
                r = self.session.get(url)
        elif self._cffi:
            r = self.session.get(url, timeout=25, allow_redirects=True,
                                 headers=self._COUNTRY_HEADERS)
        else:
            r = self.session.get(url, headers=self._COUNTRY_HEADERS)
        self._remember_response_headers(r)
        return r.status_code, r.text

    # -- fast listing refresh ------------------------------------------------
    def refresh_listings(self, budget: int = 25, state=None):
        page = max(1, int((state or {}).get('next_page', 1)))
        processed = 0
        while processed < budget:
            raw = self.get(f"{API}?page[size]={PAGE_SIZE}"
                           f"&page[number]={page}&include=brand")
            d = json.loads(raw)
            brands = {b["id"]: (b["attributes"] or {}).get("name")
                      for b in d.get("included") or []
                      if b.get("type") == "brands"}
            data = d.get("data") or []
            for item in data:
                rec = self._record_from_api(item, brands)
                if rec:
                    yield rec
            total_pages = int((d.get("meta") or {}).get("total-pages") or 0)
            if state is not None and total_pages > 0 and page > total_pages:
                state['next_page'] = 1
                raise RuntimeError('Sephora catalogue shortened; cursor reset')
            if not data or total_pages <= 0:
                raise RuntimeError('Sephora catalogue page is empty or has no page count')
            if page >= total_pages:
                if state is not None:
                    state['next_page'] = 1
                print(f"Sephora catalogue complete: {page} pages", flush=True)
                if state is not None:
                    print(f"Sephora catalogue window complete: {processed + 1} pages", flush=True)
                return
            page += 1
            processed += 1
            if state is not None:
                state['next_page'] = page
        if state is not None and processed:
            print(f"Sephora catalogue window complete: {processed} pages", flush=True)
            return
        raise RuntimeError('Sephora catalogue budget exhausted before completion')

    def _record_from_api(self, item, brands=None) -> ProductRecord | None:
        a = item.get("attributes") or {}
        price = a.get("price")
        slug = a.get("slug-url")
        if price is None or not slug:
            return None
        rel = ((item.get("relationships") or {}).get("brand") or {}).get("data") or {}
        brand = (brands or {}).get(rel.get("id")) or None
        # brand lives outside the name ("Facial Treatment Mask" under SK-II);
        # compose it in so catalogue/search rows aren't ambiguous
        name = str(a.get("name") or "").strip()
        title = f"{brand} {name}".strip() if brand and brand.lower() not in name.lower() else (name or slug)
        rrp = None
        try:
            orig = float(a.get("original-price") or 0)
            if orig > float(price):
                rrp = orig / 100
        except (TypeError, ValueError):
            pass
        images = a.get("default-image-urls") or a.get("image-urls") or []
        return ProductRecord(
            retailer=self.name,
            sku=str(item.get("id") or slug),
            gtin=None,   # not exposed by the API
            title=title,
            brand=brand,
            category="beauty",
            # not the API's web-url: that points at the sephora.au vanity
            # domain, and every other path here (run.py url matching,
            # delisting checks, verify_price) expects the canonical host
            url=f"https://www.sephora.com.au/products/{slug}",
            image_url=images[0] if images else None,
            price=float(price) / 100,
            rrp=rrp,
            in_stock=not a.get("sold-out"),
            is_marketplace=False,   # no third-party marketplace
        )

    # -- single product (verify_price / `run.py url` / delisting check) -----
    def parse_product(self, url, html):
        # the storefront page renders prices client-side only, so `html` is
        # useless - resolve the slug and ask the JSON API instead (the extra
        # page fetch already served as the politeness/404 check)
        m = re.search(r"/products/([a-z0-9-]+)", url)
        if not m:
            return None
        slug = m.group(1)
        raw = self.get(f"{API}/{slug}?v={slug}")
        d = json.loads(raw).get("data") or {}
        a = d.get("attributes") or {}
        if a.get("price") is None:
            return None
        rec = self._record_from_api(d)
        if rec:
            rec.brand = a.get("brand-name") or rec.brand
            if rec.brand and rec.brand.lower() not in rec.title.lower():
                rec.title = f"{rec.brand} {rec.title}".strip()
            rec.url = url.split("?")[0]
        return rec
