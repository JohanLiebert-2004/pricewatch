"""Booktopia and QBD book scrapers using public sitemap/product markup."""
import gzip
import random
import re
from html import unescape

from db import ProductRecord
from .base import BaseScraper, Blocked, _brand, _find_rrp, _image, _num, extract_jsonld


class _GzipSitemapScraper(BaseScraper):
    def _sitemap_locs(self, url: str) -> list[str]:
        raw = self.get_bytes(url)
        if url.endswith(".gz"):
            raw = gzip.decompress(raw)
        return re.findall(r"<loc>\s*(.*?)\s*</loc>", raw.decode("utf-8", "ignore"))

    def discover(self, limit: int = 50) -> list[str]:
        locs = self._sitemap_locs(self.sitemap_index)
        children = [u for u in locs if u.endswith((".xml", ".xml.gz"))]
        urls = self._match(locs)
        for child in children[:self.max_child_sitemaps]:
            if len(urls) >= limit * 3:
                break
            try:
                urls += self._match(self._sitemap_locs(child))
            except Blocked:
                raise
            except Exception as exc:
                print(f"  ! {self.name} sitemap {child}: {exc}")
        if len(urls) > limit:
            random.seed(0)
            urls = random.sample(urls, limit)
        return urls[:limit]

    def discover_all(self):
        locs = self._sitemap_locs(self.sitemap_index)
        yield from self._match(locs)
        for child in (u for u in locs if u.endswith((".xml", ".xml.gz"))):
            try:
                yield from self._match(self._sitemap_locs(child))
            except Blocked:
                raise
            except Exception as exc:
                print(f"  ! {self.name} sitemap {child}: {exc}")


class BooktopiaScraper(_GzipSitemapScraper):
    name = "booktopia"
    sitemap_index = "https://www.booktopia.com.au/sitemap.xml.gz"
    product_url_pattern = r"/book/\d{10,13}\.html$"
    delay = 10.0  # published Crawl-Delay in robots.txt
    max_child_sitemaps = 1

    def parse_product(self, url, html):
        for block in extract_jsonld(html):
            kind = block.get("@type")
            kinds = set(kind if isinstance(kind, list) else [kind])
            if "Product" not in kinds:
                continue
            offers = block.get("offers") or {}
            offer = offers[0] if isinstance(offers, list) and offers else offers
            price = _num((offer or {}).get("price") or (offer or {}).get("lowPrice"))
            if price is None or price <= 0:
                continue
            rrp = _find_rrp(html, price)
            retail = re.search(r'"retailPrice"\s*:\s*([\d.]+)', html)
            if retail:
                candidate = _num(retail.group(1))
                if candidate and candidate > price:
                    rrp = candidate
            availability = str((offer or {}).get("availability", ""))
            return ProductRecord(
                retailer=self.name,
                sku=str(block.get("productID") or block.get("sku") or url.rsplit("/", 1)[-1]),
                gtin=str(block.get("gtin13") or block.get("isbn") or block.get("productID") or "") or None,
                title=block.get("name") or "",
                brand=_brand(block.get("brand")) or _brand(block.get("publisher")),
                category="books", url=url, image_url=_image(block.get("image")),
                price=price, rrp=rrp,
                in_stock="InStock" in availability if availability else None,
                is_marketplace=False,
            )
        return None


class QBDScraper(_GzipSitemapScraper):
    name = "qbd"
    sitemap_index = "https://www.qbd.com.au/sitemap_index_by_category.xml"
    product_url_pattern = r"https://www\.qbd\.com\.au/.+/\d{10,13}/$"
    delay = 2.5
    max_child_sitemaps = 2

    def parse_product(self, url, html):
        match = re.search(
            r'<button\b[^>]*\bdata-isbn="([^"]+)"[^>]*\bdata-title="([^"]+)"'
            r'[^>]*\bdata-price="([\d.]+)"', html, re.I)
        if not match:
            return None
        sku, title, raw_price = match.groups()
        price = _num(raw_price)
        if price is None or price <= 0:
            return None
        image = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', html, re.I)
        author = re.search(r'<meta[^>]+name="author"[^>]+content="([^"]+)"', html, re.I)
        return ProductRecord(
            retailer=self.name, sku=unescape(sku),
            gtin=unescape(sku) if re.fullmatch(r"\d{13}", sku) else None,
            title=unescape(title), brand=unescape(author.group(1)) if author else None,
            category="books", url=url,
            image_url=unescape(image.group(1)) if image else None,
            price=price, rrp=_find_rrp(html, price),
            in_stock="Add to Cart" in html, is_marketplace=False,
        )