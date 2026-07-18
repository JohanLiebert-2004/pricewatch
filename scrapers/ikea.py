"""IKEA Australia scraper using the public AU product sitemap and JSON-LD."""
import random
import re

from .base import BaseScraper, Blocked


class IkeaScraper(BaseScraper):
    name = "ikea"
    sitemap_index = "https://www.ikea.com/sitemaps/sitemap.xml"
    product_url_pattern = r"https://www\.ikea\.com/au/en/p/[^/]+/$"
    delay = 2.5

    def _au_sitemaps(self):
        locs = re.findall(r"<loc>\s*(.*?)\s*</loc>", self.get(self.sitemap_index))
        return [u for u in locs if "/prod-en-AU_" in u]

    def discover(self, limit: int = 50) -> list[str]:
        urls = []
        for child in self._au_sitemaps()[:2]:
            try:
                urls += self._match(re.findall(r"<loc>\s*(.*?)\s*</loc>", self.get(child)))
            except Blocked:
                raise
            except Exception as exc:
                print(f"  ! ikea sitemap {child}: {exc}")
        if len(urls) > limit:
            random.seed(0)
            urls = random.sample(urls, limit)
        return urls[:limit]

    def discover_all(self):
        for child in self._au_sitemaps():
            try:
                yield from self._match(re.findall(r"<loc>\s*(.*?)\s*</loc>", self.get(child)))
            except Blocked:
                raise
            except Exception as exc:
                print(f"  ! ikea sitemap {child}: {exc}")