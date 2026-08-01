"""Kmart and Target AU scrapers.

Both are Kmart Group sites behind Akamai; product pages require curl_cffi
Chrome impersonation and still get challenged often from datacenter IPs.

Fast path (July 2026):
- Kmart's storefront search/browse is Constructor.io. Its public API at
  ac.cnstrc.com (Constructor's own CDN, NOT behind Kmart's Akamai) returns
  200 products per request with price, list/promo price history, APN (GTIN)
  and seller - the whole ~250k catalogue is coverable in ~1,300 requests.
- Target prefers an approved CSV/JSON product feed when
  TARGET_PRODUCT_FEED_URL is configured. Its older __NEXT_DATA__ category
  path remains a serial fallback, but Akamai currently challenges it on
  hosted runners.
"""
import csv
import io
import json
import os
import random
import re
import time
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from db import ProductRecord
from .base import BaseScraper, Blocked

KMART_CONSTRUCTOR_KEY = "key_GZTqlLr41FS2p7AY"  # public, shipped in page JS


class KmartScraper(BaseScraper):
    name = "kmart"
    sitemap_index = "https://www.kmart.com.au/sitemap.xml"
    product_url_pattern = r"/product/.+-\d+/?$"
    delay = 2.5
    needs_impersonation = True
    warmup_url = "https://www.kmart.com.au/"
    use_proxy = False  # tested 2026-07-10: residential proxy still gets the
                        # Akamai JS-challenge interstitial on product pages
                        # (behavioral, not IP-reputation - a proxy can't
                        # solve it). Bulk refresh doesn't need it anyway
                        # (goes via Constructor.io, not kmart.com.au).

    # -- fast listing refresh via Constructor.io ---------------------------
    # Constructor is the sanctioned catalogue source. Keep one serial,
    # human-scale lane even though it is not protected by Kmart's storefront
    # WAF; the small jitter avoids a mechanically fixed request cadence.
    api_delay = 0.8
    api_jitter = 0.4
    per_page = 200

    def _api(self):
        return httpx.Client(
            timeout=25,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                     "Accept": "application/json"},
            params={"key": KMART_CONSTRUCTOR_KEY, "i": "pricewatch", "s": "1",
                    "c": "ciojs-client-2.35.2"})

    # Level-2 group -> site chip label. Merchandising pseudo-groups (Brands,
    # Clearance, Back To School, Winter, ...) overlap the real taxonomy and
    # would otherwise randomly overwrite it, so they map to None (their
    # products still get swept for prices; COALESCE keeps any existing tag).
    SECTION_LABEL = {
        "Home & Living": "Home & Living", "Entertainment": "Entertainment",
        "Toys": "Toys", "Sport & Outdoor": "Sport & Outdoor",
        "Beauty": "Beauty", "Womens": "Women", "Mens": "Men",
        "Kids & Baby": "Kids & Baby", "Tech & Gaming": "Tech & Gaming",
    }

    def _leaf_groups(self, client):
        """Yield (group_id, name, count, section) for pageable groups.

        Constructor caps the paging window (~10k items), so descend into
        children until each group is under the cap. `section` is the
        level-2 ancestor's display name (child of the "All" root) - the
        granularity the site's per-store category chips use.
        """
        r = client.get("https://ac.cnstrc.com/browse/groups")
        r.raise_for_status()
        roots = r.json().get("response", {}).get("groups", [])

        def walk(g, section):
            count = g.get("count")
            kids = g.get("children") or []
            if count is not None and count <= 9800:
                if count:
                    yield g["group_id"], g.get("display_name", ""), count, section
            elif kids:
                for k in kids:
                    yield from walk(k, section)
            else:  # unknown or oversized leaf: page until empty (capped)
                yield g["group_id"], g.get("display_name", ""), None, section

        for root in roots:                      # single "All" root
            for lvl2 in (root.get("children") or [root]):
                yield from walk(lvl2, lvl2.get("display_name", ""))

    def refresh_listings(self, budget: int = 1400):
        """Yield ProductRecords for the whole catalogue via the browse API.

        budget = max API requests this run.

        The same product appears in MULTIPLE merchandising groups (Clearance,
        Winter, brand groups, ...), and Constructor returns a different
        data.price per group for variant-priced products (e.g. apparel where
        sizes sell at $9/$10/$15). Yielding every occurrence made
        current_price flip several times within one sweep, each flip writing
        a bogus change-snapshot - one shirt collected 462 in nine days and
        its price-history chart rendered as solid stripes. So: collect the
        whole sweep first, then yield ONE record per SKU at its minimum
        price (the "from $X" a shopper can actually pay).
        """
        used = 0
        sweep = {}   # sku -> ProductRecord, minimum price wins
        done = False
        with self._api() as client:
            try:
                groups = list(self._leaf_groups(client))
            except Exception as e:
                raise Blocked(f"kmart: constructor groups fetch failed: {e}")
            used += 1
            try:
                for gid, gname, count, section in groups:
                    if done:
                        break
                    subcat = self.SECTION_LABEL.get(section)
                    max_window = 9800 // self.per_page
                    pages = (min((count + self.per_page - 1) // self.per_page,
                                 max_window) if count else max_window)
                    for page in range(1, pages + 1):
                        if used >= budget:
                            done = True
                            break
                        time.sleep(self.api_delay + random.uniform(0, self.api_jitter))
                        r = client.get(
                            f"https://ac.cnstrc.com/browse/group_id/{gid}",
                            params={"num_results_per_page": self.per_page,
                                    "page": page})
                        used += 1
                        if r.status_code != 200:
                            break
                        results = (r.json().get("response") or {}).get("results") or []
                        for item in results:
                            rec = self._record_from_api(
                                item, subcat, is_clearance=(section == "Clearance"))
                            if not rec:
                                continue
                            cur = sweep.get(rec.sku)
                            if cur is None:
                                sweep[rec.sku] = rec
                            else:
                                # A product can appear in the Clearance group
                                # AND other groups; whichever occurrence wins
                                # the min-price comparison below must not
                                # lose the clearance flag if either one had it.
                                if rec.is_clearance:
                                    cur.is_clearance = True
                                if (rec.price is not None
                                        and (cur.price is None or rec.price < cur.price)):
                                    rec.is_clearance = rec.is_clearance or cur.is_clearance
                                    sweep[rec.sku] = rec
                        if len(results) < self.per_page:
                            break
            except Exception as e:
                # keep the partial sweep rather than losing it - the caller
                # treats what we yield as "seen this sweep"
                print(f"  kmart sweep interrupted ({type(e).__name__}: {e}), "
                      f"keeping {len(sweep)} collected products")
        yield from sweep.values()

    def _record_from_api(self, item, subcat=None, is_clearance=False) -> ProductRecord | None:
        d = item.get("data") or {}
        price = d.get("price")
        if price is None:
            return None
        sku = str(d.get("id") or "").removeprefix("P_")
        if not sku:
            return None
        # prices: list = everyday shelf price, promo = current promotion
        rrp = None
        for p in d.get("prices") or []:
            if p.get("type") == "list":
                try:
                    amt = float(p.get("amount"))
                except (TypeError, ValueError):
                    continue
                if amt > float(price):
                    rrp = amt
        seller = (d.get("Seller") or ["Kmart"])[0]
        # Some marketplace sellers upload listings with a punctuation-only
        # placeholder name (observed: literal "." for several Partyrama
        # SKUs, mirrored verbatim from Kmart's own storefront) - treat that
        # as no title and fall back rather than showing a near-blank row.
        raw_title = re.sub(r"[\W_]", "", str(item.get("value") or ""))
        if raw_title:
            title = str(item["value"])
        else:
            merch = str(d.get("MerchClassName") or "").strip()
            brand = d.get("Brand")
            title = merch or (f"{brand} product" if brand else sku)
        return ProductRecord(
            retailer=self.name,
            sku=sku,
            gtin=str(d["apn"]) if d.get("apn") else None,
            title=title,
            # Constructor data is not type-stable here: some sellers publish
            # numeric brand identifiers while most publish names. Keep the DB
            # text contract explicit before records are chunked together.
            brand=str(d["Brand"]) if d.get("Brand") is not None else None,
            subcategory=subcat,
            url="https://www.kmart.com.au" + d.get("url", ""),
            image_url=d.get("image_url"),
            price=float(price),
            rrp=rrp,
            in_stock=None,
            is_marketplace=str(seller).lower() != "kmart",
            is_clearance=is_clearance,
        )


class TargetScraper(BaseScraper):
    name = "target"
    sitemap_index = "https://www.target.com.au/medias/feeds/sitemap/sitemap.xml"
    product_url_pattern = r"/p/.+/\w+$"
    delay = 2.5
    needs_impersonation = True
    warmup_url = "https://www.target.com.au/"
    use_proxy = False  # tested 2026-07-10: residential proxy still gets the
                        # Akamai JS-challenge interstitial on listing pages
                        # (behavioral, not IP-reputation - a proxy can't
                        # solve it).

    per_page = 48  # fixed by the site
    all_products_path = "/c/all-products/AP01"
    product_feed_env = "TARGET_PRODUCT_FEED_URL"
    max_feed_bytes = 100 * 1024 * 1024

    def refresh_listings(self, budget: int = 700):
        """Yield Target products from an approved feed or the storefront.

        Target's Akamai challenge currently blocks the storefront path on
        hosted runners. An approved affiliate/product feed avoids that WAF
        entirely and is therefore preferred whenever TARGET_PRODUCT_FEED_URL
        is configured. The URL is a secret and must never be logged.

        With no feed configured, retain the old serial storefront path as a
        visible fallback so a future block removal recovers automatically.
        """
        feed_url = os.environ.get(self.product_feed_env, "").strip()
        if feed_url:
            yield from self._feed_records(feed_url)
            return
        yield from self._storefront_records(budget)

    def _storefront_records(self, budget: int):
        """Yield ProductRecords by paging the all-products listing.

        Server-rendered __NEXT_DATA__ carries 48 products per page with
        offer/was/RRP prices. Still behind Akamai, so this can raise Blocked -
        the caller stores whatever was yielded before the block.
        """
        used = 0
        page = 1
        while used < budget:
            url = (f"https://www.target.com.au{self.all_products_path}"
                   f"?page={page}")
            html = self.get(url)          # politeness + challenge detection
            used += 1
            m = re.search(
                r'<script id="__NEXT_DATA__" type="application/json">(.*?)'
                r"</script>", html, re.S)
            if not m:
                raise Blocked(f"{self.name}: no __NEXT_DATA__ on {url}")
            try:
                pl = (json.loads(m.group(1))["props"]["pageProps"]
                      ["metadata"]["productList"])
            except (KeyError, TypeError, json.JSONDecodeError):
                break
            products = pl.get("products") or []
            for p in products:
                rec = self._record_from_listing(p)
                if rec:
                    yield rec
            total = pl.get("totalNumProducts") or 0
            if not products or page * self.per_page >= total:
                break
            page += 1

    def _feed_records(self, feed_url: str):
        """Download and normalize an Ultimate Web Scraper CSV/JSON export."""
        parsed = urlparse(feed_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise Blocked("target: configured product feed URL must use HTTPS")

        try:
            with httpx.Client(
                    timeout=90, follow_redirects=True,
                    headers={"Accept": "application/json,text/csv;q=0.9,*/*;q=0.5",
                             "User-Agent": "DealwatchFeedImporter/1.0"}) as client:
                response = client.get(feed_url)
                response.raise_for_status()
        except Exception as exc:
            # Never include the signed/private feed URL in logs or health data.
            raise Blocked(
                f"target: configured product feed fetch failed ({type(exc).__name__})"
            ) from exc

        if urlparse(str(response.url)).scheme != "https":
            raise Blocked("target: product feed redirected to a non-HTTPS URL")
        if len(response.content) > self.max_feed_bytes:
            raise Blocked("target: product feed exceeds the 100 MB safety limit")

        records = self._parse_feed(response.text,
                                   response.headers.get("content-type", ""))
        if not records:
            raise Blocked("target: configured product feed contained no valid AUD products")
        yield from records

    def _parse_feed(self, text: str, content_type: str = "") -> list[ProductRecord]:
        """Parse JSON or delimited feed text and deduplicate it by Target SKU."""
        stripped = text.lstrip("\ufeff\r\n\t ")
        rows = None
        if "json" in content_type.lower() or stripped.startswith(("[", "{")):
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise Blocked("target: configured product feed returned invalid JSON") from exc
            rows = self._json_rows(payload)
        else:
            try:
                dialect = csv.Sniffer().sniff(stripped[:8192], delimiters=",\t;|")
            except csv.Error:
                dialect = csv.excel
            rows = list(csv.DictReader(io.StringIO(stripped, newline=""), dialect=dialect))

        if rows is None:
            raise Blocked("target: configured product feed has an unsupported shape")

        by_sku = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            rec = self._record_from_feed_row(row)
            if not rec:
                continue
            current = by_sku.get(rec.sku)
            if current is None or rec.price < current.price:
                by_sku[rec.sku] = rec
        return list(by_sku.values())

    @staticmethod
    def _json_rows(payload):
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            return None
        for key in ("products", "items", "results", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                for nested in ("products", "items", "results"):
                    if isinstance(value.get(nested), list):
                        return value[nested]
        return None

    @staticmethod
    def _feed_values(row):
        return {
            re.sub(r"[^a-z0-9]", "", str(key).lower()): value
            for key, value in row.items()
        }

    @staticmethod
    def _first(values, *names):
        for name in names:
            value = values.get(name)
            if value is not None and str(value).strip() != "":
                return value
        return None

    @staticmethod
    def _money(value):
        if value is None:
            return None
        match = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
        if not match:
            return None
        try:
            return float(match.group(0))
        except ValueError:
            return None

    @staticmethod
    def _clean_identifier(value):
        if value is None:
            return None
        result = str(value).strip()
        if re.fullmatch(r"\d+\.0", result):
            result = result[:-2]
        return result or None

    @classmethod
    def _direct_target_url(cls, values, sku, title):
        candidates = (
            "targeturl", "advertiserurl", "producturl", "deeplinkurl",
            "deeplink", "url", "link",
        )
        for name in candidates:
            raw = cls._first(values, name)
            if not raw:
                continue
            candidate = str(raw).strip()
            parsed = urlparse(candidate)
            if (parsed.scheme == "https" and parsed.hostname
                    and (parsed.hostname == "target.com.au"
                         or parsed.hostname.endswith(".target.com.au"))):
                return candidate
            # Some affiliate links carry the original destination as a query
            # value. Unwrap only a real Target HTTPS URL.
            for query_values in parse_qs(parsed.query).values():
                for query_value in query_values:
                    destination = unquote(query_value)
                    dest = urlparse(destination)
                    if (dest.scheme == "https" and dest.hostname
                            and (dest.hostname == "target.com.au"
                                 or dest.hostname.endswith(".target.com.au"))):
                        return destination

        # Feed tracking links do not always expose their destination. Target's
        # product route is SKU-addressed, so construct a stable direct URL.
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        return f"https://www.target.com.au/p/{slug or 'product'}/{sku}"

    def _record_from_feed_row(self, row) -> ProductRecord | None:
        values = self._feed_values(row)
        sku = self._clean_identifier(self._first(
            values, "sku", "productid", "productcode", "itemid", "id"))
        title = str(self._first(
            values, "title", "productname", "name") or "").strip()
        price = self._money(self._first(
            values, "saleprice", "currentprice", "offerprice", "price"))
        currency = str(self._first(
            values, "currency", "currencycode") or "AUD").strip().upper()
        if not sku or not title or price is None or price <= 0:
            return None
        if currency not in ("AUD", "A$", "AUSTRALIAN DOLLAR", "AUSTRALIAN DOLLARS"):
            return None

        rrp = self._money(self._first(
            values, "wasprice", "rrp", "retailprice", "originalprice", "listprice"))
        if rrp is not None and rrp <= price:
            rrp = None

        stock = self._first(values, "instock", "availability", "stockstatus", "stocklevel")
        in_stock = None
        if stock is not None:
            normalized = str(stock).strip().lower().replace("_", " ")
            if normalized in ("true", "yes", "y", "1", "in stock", "instock", "available"):
                in_stock = True
            elif normalized in ("false", "no", "n", "0", "out of stock", "outofstock",
                                "unavailable", "sold out"):
                in_stock = False
            else:
                amount = self._money(stock)
                if amount is not None:
                    in_stock = amount > 0

        return ProductRecord(
            retailer=self.name,
            sku=sku,
            gtin=self._clean_identifier(self._first(
                values, "gtin", "ean", "ean13", "barcode", "upc")),
            title=title,
            brand=str(self._first(values, "brand", "manufacturer") or "").strip() or None,
            subcategory=str(self._first(
                values, "category", "productcategory", "categoryname") or "").strip() or None,
            url=self._direct_target_url(values, sku, title),
            image_url=str(self._first(
                values, "imageurl", "image", "largeimage", "productimage") or "").strip() or None,
            price=price,
            rrp=rrp,
            in_stock=in_stock,
            is_marketplace=False,
            is_clearance=str(self._first(values, "clearance") or "").strip().lower()
                         in ("true", "yes", "1"),
        )

    def _record_from_listing(self, p) -> ProductRecord | None:
        price = (p.get("price") or {})
        offer = price.get("offerPrice")
        if not offer:
            return None
        was = price.get("wasPrice") or 0
        rrp = price.get("recommendedRetailPrice") or 0
        ref = max(float(was), float(rrp))
        product_url = p.get("baseProductUrl") or ""
        if product_url.startswith("/"):
            product_url = "https://www.target.com.au" + product_url
        image = p.get("imageUrl") or p.get("productImageUrl") or p.get("image")
        if isinstance(image, dict):
            image = image.get("url") or image.get("src")
        return ProductRecord(
            retailer=self.name,
            sku=str(p.get("id") or ""),
            gtin=None,  # not exposed in listings; page crawl enriches it
            title=p.get("title") or "",
            brand=None,
            url=product_url,
            image_url=image if isinstance(image, str) else None,
            price=float(offer),
            rrp=ref if ref > float(offer) else None,
            in_stock=None,
            is_marketplace=False,
        )
