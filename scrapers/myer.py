"""Myer scraper.

No bot challenge seen on plain requests. Myer relaunched product pages on
Next.js some time around 2026-07-22, which dropped the schema.org Product
JSON-LD block the base class's default parse_product relied on - every crawl
since then silently stored 0 products (no exception, so CI stayed green;
caught by comparing `retailer_freshness.last_seen` against the workflow
runs, not by any error). Price/variant/media data now lives in the
`__NEXT_DATA__` react-query cache instead
(props.pageProps.dehydratedState.queries, entry keyed `["product", <seoToken>]`),
so parse_product is overridden below to read that shape. Marketplace items
are identified by a non-empty variant `miraklSkuId` (Myer's marketplace
platform is Mirakl); images are served from myer-media.com.au with a
`{{size}}` placeholder in the path.

The only other wrinkle: Myer's sitemap files are served as literal gzip
bytes (sitemap_YYYYN_M.xml.gz), not as an HTTP Content-Encoding httpx/curl_cffi
would transparently decode, so the base class's plain-text sitemap walker
can't read them. discover()/discover_all() are overridden here to fetch raw
bytes (BaseScraper.get_bytes) and gzip.decompress() before regexing <loc>
tags; the rest of the logic mirrors the base implementation.
"""
import gzip
import json
import random
import re

from db import ProductRecord
from .base import BaseScraper, Blocked


class MyerScraper(BaseScraper):
    name = "myer"
    sitemap_index = "https://www.myer.com.au/sitemap/sitemap_20251.xml.gz"
    product_url_pattern = r"^https://www\.myer\.com\.au/p/[a-z0-9][a-z0-9-]*$"
    delay = 1.5

    @staticmethod
    def _pick_variant(variants):
        """Pick the variant to treat as *the* product's price.

        A Myer product URL covers every size/colour under one page, and the
        page's own top-level priceFrom/listPriceFrom is just min() across
        variants - including data-entry glitches on Myer's own site. Seen
        live 2026-08-01: a "BedT ... Sheet Set" listed 4 sane sized variants
        ($87-$134, all ~30% off with savedAmount/promo fields set) plus a
        fifth "King Sheet Set" variant priced $10/$10 with none of those
        discount fields - a broken catalogue row, not a real $10 option.
        Blindly taking priceFrom picked that $10 row, and since the
        product's price history (from earlier, sane crawls) still had a
        ~$130 high, discount_feed's history_drop signal turned that into a
        fake "92% off" listing. Drop any variant priced under 30% of the
        group's median before picking the cheapest of what's left - loose
        enough to keep legitimate multi-size spreads (a single sheet vs a
        super king is rarely more than ~2x apart) but catches a lone
        near-zero outlier like this one.
        """
        priced = [v for v in variants
                  if isinstance(v.get("price"), (int, float)) and v["price"] > 0]
        if not priced:
            return None
        if len(priced) > 1:
            prices = sorted(v["price"] for v in priced)
            mid = len(prices) // 2
            median = prices[mid] if len(prices) % 2 else (prices[mid - 1] + prices[mid]) / 2
            sane = [v for v in priced if v["price"] >= median * 0.3]
            if sane:
                priced = sane
        return min(priced, key=lambda v: v["price"])

    def parse_product(self, url, html):
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                      html, re.S)
        if not m:
            return None
        try:
            queries = (json.loads(m.group(1))["props"]["pageProps"]
                       ["dehydratedState"]["queries"])
            data = next(q["state"]["data"] for q in queries
                        if (q.get("queryKey") or [None])[0] == "product")
        except (json.JSONDecodeError, KeyError, TypeError, StopIteration):
            return None
        sku = str(data.get("id") or "")
        if not sku:
            return None
        variants = data.get("variants") or []
        is_marketplace = any(v.get("miraklSkuId") for v in variants)
        variant = self._pick_variant(variants)
        if variant is not None:
            price, rrp, gtin = variant.get("price"), variant.get("listPrice"), variant.get("ean")
        else:
            price, rrp = data.get("priceFrom"), data.get("listPriceFrom")
            gtin = next((str(v["ean"]) for v in variants if v.get("ean")), None)
        if price is None or price <= 0:
            return None
        gtin = str(gtin) if gtin else None
        image_url = None
        media = data.get("media") or []
        base_path = media[0].get("baseUrl") if media else None
        if base_path:
            # 720x928 is the only pre-generated derivative confirmed live
            # (a plausible-looking arbitrary size like 1000x1000 404s - the
            # bucket only serves specific baked sizes).
            image_url = ("https://myer-media.com.au/wcsstore/"
                         f"MyerCatalogAssetStore/{base_path}"
                         ).replace("{{size}}", "720x928")
        return ProductRecord(
            retailer=self.name,
            sku=sku,
            gtin=gtin,
            title=data.get("title") or data.get("name") or "",
            brand=data.get("brand"),
            url=url,
            price=float(price),
            rrp=float(rrp) if rrp and float(rrp) > float(price) else None,
            image_url=image_url,
            in_stock=data.get("isAvailable"),
            is_marketplace=is_marketplace,
        )

    def _sitemap_locs(self, url: str) -> list[str]:
        raw = self.get_bytes(url)
        xml = gzip.decompress(raw).decode("utf-8", "ignore") if url.endswith(".gz") \
            else raw.decode("utf-8", "ignore")
        return re.findall(r"<loc>\s*(.*?)\s*</loc>", xml)

    def discover(self, limit: int = 50) -> list[str]:
        locs = self._sitemap_locs(self.sitemap_index)
        children = [l for l in locs if l.endswith(".xml.gz")]
        urls = self._match(locs)
        for child in children[: self.max_child_sitemaps]:
            if len(urls) >= limit * 3:
                break
            try:
                urls += self._match(self._sitemap_locs(child))
            except Blocked:
                raise
            except Exception as e:
                print(f"  ! {self.name} sitemap {child}: {e}")
        if len(urls) > limit:
            random.seed(0)  # reproducible runs; remove for variety
            urls = random.sample(urls, limit)
        return urls[:limit]

    def discover_all(self):
        locs = self._sitemap_locs(self.sitemap_index)
        yield from self._match(locs)
        children = [l for l in locs if l.endswith(".xml.gz")]
        for child in children:
            try:
                yield from self._match(self._sitemap_locs(child))
            except Blocked:
                raise
            except Exception as e:
                print(f"  ! {self.name} sitemap {child}: {e}")
