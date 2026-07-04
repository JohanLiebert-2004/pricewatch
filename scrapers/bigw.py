"""BIG W scraper.

JSON-LD Product blocks on product pages. Marketplace listings link to
/marketplace/sellers and show "Sold & shipped by <seller>". The strikethrough
reference price renders as $<amount> near a wasPrice flag in the page state.
Akamai-protected: requires curl_cffi (chrome99_android profile passes).
"""
import html as htmllib
import re

from .base import BaseScraper


class BigWScraper(BaseScraper):
    name = "bigw"
    sitemap_index = "https://www.bigw.com.au/sitemap.xml"
    product_url_pattern = r"/product/.+/p/\d+"
    delay = 2.5
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
