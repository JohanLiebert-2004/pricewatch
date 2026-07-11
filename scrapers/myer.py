"""Myer scraper.

Salesforce Commerce Cloud-style storefront. No bot challenge seen on plain
requests - a single request already returns a full schema.org Product
JSON-LD block (price, sku, brand, images) plus a "listPrice" field embedded
in page state whenever the item is discounted, which the base class's
_find_rrp fallback already knows how to pick up. No override of
parse_product needed.

The only wrinkle: Myer's sitemap files are served as literal gzip bytes
(sitemap_YYYYN_M.xml.gz), not as an HTTP Content-Encoding httpx/curl_cffi
would transparently decode, so the base class's plain-text sitemap walker
can't read them. discover()/discover_all() are overridden here to fetch raw
bytes (BaseScraper.get_bytes) and gzip.decompress() before regexing <loc>
tags; the rest of the logic mirrors the base implementation.
"""
import gzip
import random
import re

from .base import BaseScraper, Blocked


class MyerScraper(BaseScraper):
    name = "myer"
    sitemap_index = "https://www.myer.com.au/sitemap/sitemap_20251.xml.gz"
    product_url_pattern = r"^https://www\.myer\.com\.au/p/[a-z0-9][a-z0-9-]*$"
    delay = 1.5

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
