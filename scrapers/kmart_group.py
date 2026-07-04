"""Kmart and Target AU scrapers.

Both are Kmart Group sites on near-identical platforms with JSON-LD Product
blocks. Both sit behind Akamai: require curl_cffi Chrome impersonation.
Target's sitemap index uses generically named child sitemaps - the base
discover() follows them and pattern-matches product URLs.
"""
from .base import BaseScraper


class KmartScraper(BaseScraper):
    name = "kmart"
    sitemap_index = "https://www.kmart.com.au/sitemap.xml"
    product_url_pattern = r"/product/.+-\d+/?$"
    delay = 2.5
    needs_impersonation = True
    warmup_url = "https://www.kmart.com.au/"


class TargetScraper(BaseScraper):
    name = "target"
    sitemap_index = "https://www.target.com.au/medias/feeds/sitemap/sitemap.xml"
    product_url_pattern = r"/p/.+/\w+$"
    delay = 2.5
    needs_impersonation = True
    warmup_url = "https://www.target.com.au/"
