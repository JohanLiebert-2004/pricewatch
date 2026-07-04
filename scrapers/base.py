"""Common scraper interface. Each retailer implements discover() + parse_product().

HTTP layer: uses curl_cffi with Chrome TLS impersonation when installed
(needed for Akamai-protected sites like BIG W / Kmart / Target), otherwise
falls back to httpx. Politeness defaults: 1-2 s between requests, hard limits.
"""
import json
import random
import re
import time

try:
    from curl_cffi import requests as cffi_requests
    HAVE_CFFI = True
except ImportError:
    cffi_requests = None
    HAVE_CFFI = False

import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-AU,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}


class Blocked(Exception):
    """Raised when the retailer's bot protection blocks us (403/503)."""


# Akamai sometimes returns this interstitial with HTTP 200 instead of a 4xx -
# a "behavioral" JS challenge page with no product data at all. Left
# undetected, it looks like an ordinary page with no Product block, so the
# parser silently returns None for every URL instead of surfacing a block.
_CHALLENGE_MARKERS = ("sec-if-cpt-container", "Powered and protected by")


def _is_challenge_page(text: str) -> bool:
    return any(m in text for m in _CHALLENGE_MARKERS)


class BaseScraper:
    name = "base"
    sitemap_index = None            # sitemap index or product sitemap URL
    product_url_pattern = None      # regex a product URL must match
    delay = 1.0                     # seconds between requests
    needs_impersonation = False     # Akamai/Cloudflare-protected: require curl_cffi
    impersonate = "chrome99_android"  # profile that passes AU retail WAF tiers
    warmup_url = None               # homepage to visit once for cookies
    max_child_sitemaps = 3          # cap when following generic child sitemaps

    def __init__(self):
        if self.needs_impersonation and HAVE_CFFI:
            self.session = cffi_requests.Session(impersonate=self.impersonate)
            self.session.headers.update({"Accept-Language": "en-AU,en;q=0.9"})
            self._cffi = True
        else:
            self.session = httpx.Client(
                http2=True, headers=HEADERS, timeout=25, follow_redirects=True
            )
            self._cffi = False
        if self.needs_impersonation and not HAVE_CFFI:
            print(f"  ({self.name}: tip - `pip install curl_cffi` greatly improves "
                  "success against this retailer's bot protection)")
        self._warmed = False

    # -- HTTP helpers ------------------------------------------------------
    def get(self, url: str) -> str:
        if self.warmup_url and not self._warmed:
            self._warmed = True
            try:
                self._request(self.warmup_url)   # collect cookies like a browser
                time.sleep(self.delay)
            except Exception:
                pass
        time.sleep(self.delay + random.uniform(0, 0.5))
        status, text = self._request(url)
        blocked = status in (403, 429, 503) or _is_challenge_page(text)
        if blocked and self._cffi:
            # Akamai blocks are often session-scoped: retry once fresh
            from curl_cffi import requests as _cr
            time.sleep(5 + self.delay)
            self.session = _cr.Session(impersonate=self.impersonate)
            if self.warmup_url:
                try:
                    self._request(self.warmup_url)
                    time.sleep(self.delay)
                except Exception:
                    pass
            status, text = self._request(url)
            blocked = status in (403, 429, 503) or _is_challenge_page(text)
        if blocked:
            hint = "" if (self._cffi or not self.needs_impersonation) else \
                " Install curl_cffi (`pip install curl_cffi`) and retry."
            raise Blocked(f"{self.name}: HTTP {status} for {url} - bot protection."
                          f"{hint} Otherwise lower the rate or use an official feed.")
        if status == 404:
            raise NotFound(url)
        if status >= 400:
            raise RuntimeError(f"HTTP {status} for {url}")
        return text

    def _request(self, url: str):
        if self._cffi:
            r = self.session.get(url, timeout=25, allow_redirects=True)
            return r.status_code, r.text
        r = self.session.get(url)
        return r.status_code, r.text

    # -- Discovery ---------------------------------------------------------
    def discover(self, limit: int = 50) -> list[str]:
        """Return up to `limit` product URLs from the retailer's sitemap(s).

        Prefers child sitemaps named like 'product'; if none exist, follows a
        few generic child sitemaps and pattern-matches URLs inside them.
        Samples across the sitemap rather than taking the (often stale) head.
        """
        urls = []
        xml = self.get(self.sitemap_index)
        locs = re.findall(r"<loc>\s*(.*?)\s*</loc>", xml)
        children = [l for l in locs if l.endswith(".xml")]
        product_children = [c for c in children if "product" in c.lower()]
        queue = product_children or children[: self.max_child_sitemaps]
        # direct product URLs in the index itself
        urls += self._match(locs)
        for child in queue:
            if len(urls) >= limit * 3:
                break
            try:
                urls += self._match(re.findall(r"<loc>\s*(.*?)\s*</loc>", self.get(child)))
            except Blocked:
                raise
            except Exception as e:
                print(f"  ! {self.name} sitemap {child}: {e}")
        # spread the sample across the catalogue instead of taking the head
        if len(urls) > limit:
            random.seed(0)  # reproducible runs; remove for variety
            urls = random.sample(urls, limit)
        return urls[:limit]

    def discover_all(self):
        """Yield every product URL in the retailer's sitemaps (full catalogue)."""
        xml = self.get(self.sitemap_index)
        locs = re.findall(r"<loc>\s*(.*?)\s*</loc>", xml)
        yield from self._match(locs)
        children = [l for l in locs if l.endswith(".xml")]
        product_children = [c for c in children if "product" in c.lower()] or children
        for child in product_children:
            try:
                yield from self._match(re.findall(r"<loc>\s*(.*?)\s*</loc>", self.get(child)))
            except Blocked:
                raise
            except Exception as e:
                print(f"  ! {self.name} sitemap {child}: {e}")

    def _match(self, locs):
        if not self.product_url_pattern:
            return []
        return [l for l in locs if re.search(self.product_url_pattern, l)]

    # -- Parsing -----------------------------------------------------------
    def parse_product(self, url: str, html: str):
        """Return a ProductRecord or None. Default: JSON-LD Product blocks."""
        from db import ProductRecord

        for block in extract_jsonld(html):
            if block.get("@type") not in ("Product", ["Product"]):
                continue
            offer = block.get("offers") or {}
            if isinstance(offer, list):
                offer = offer[0] if offer else {}
            price = _num(offer.get("price") or offer.get("lowPrice"))
            if price is None:
                continue
            seller = (offer.get("seller") or {})
            seller_name = seller.get("name", "") if isinstance(seller, dict) else ""
            return ProductRecord(
                retailer=self.name,
                sku=str(block.get("sku") or block.get("productID") or url.rstrip("/").split("/")[-1]),
                gtin=str(block.get("gtin13") or block.get("gtin") or block.get("gtin12") or "") or None,
                title=block.get("name") or "",
                brand=_brand(block.get("brand")),
                url=url,
                price=price,
                rrp=_num(offer.get("highPrice")) or _find_rrp(html, price),
                in_stock="InStock" in str(offer.get("availability", "")),
                is_marketplace=bool(seller_name) and self.name.lower() not in seller_name.lower(),
            )
        return None

    def scrape(self, limit: int = 20):
        """Discover product URLs and yield ProductRecords."""
        for url in self.discover(limit):
            try:
                rec = self.parse_product(url, self.get(url))
                if rec:
                    yield rec
                else:
                    print(f"  ? no product data at {url} ({self._diagnose(url)})")
            except Blocked:
                raise
            except NotFound:
                continue  # stale sitemap entry
            except Exception as e:
                print(f"  ! {self.name} parse failed for {url}: {e}")


    def _diagnose(self, url):
        """Explain why a 200 page yielded nothing (called after parse fails)."""
        try:
            status, text = self._request(url)
        except Exception:
            return "page unreadable on recheck"
        if len(text) < 5000:
            return f"tiny {len(text)}B response - likely a soft block, retry later"
        if "Access Denied" in text or "captcha" in text.lower():
            return "bot challenge page"
        return f"{len(text)//1024}KB page with no embedded price - this retailer loads first-party prices client-side; marketplace items usually still work"


class NotFound(Exception):
    pass


# -- shared utilities -------------------------------------------------------
def extract_jsonld(html: str) -> list[dict]:
    out = []
    for m in re.finditer(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.S,
    ):
        try:
            data = json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            out.extend(d for d in data if isinstance(d, dict))
        elif isinstance(data, dict):
            out.extend(data.get("@graph", [data]) if "@graph" in data else [data])
    return out


def _num(v):
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace("$", "").replace(",", ""))
    except ValueError:
        return None


def _brand(b):
    if isinstance(b, dict):
        return b.get("name")
    return b if isinstance(b, str) else None


def _find_rrp(html: str, price: float):
    """Fallback: look for a strikethrough/was price in the raw HTML state."""
    for pat in (r'"wasPrice"\s*:\s*"?([\d.]+)', r'"rrp"\s*:\s*"?([\d.]+)',
                r'"listPrice"\s*:\s*"?([\d.]+)', r'"strikethroughPrice"\s*:\s*"?([\d.]+)'):
        m = re.search(pat, html)
        if m:
            v = _num(m.group(1))
            if v and v > price:
                return v
    return None
