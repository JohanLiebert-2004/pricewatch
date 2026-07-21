import json
import re
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from starlette.requests import Request

from services import preview_app


ROOT = Path(__file__).resolve().parents[1]


def request_for(path: str) -> Request:
    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(b"host", b"dealwatch.com.au"),
                    (b"x-forwarded-proto", b"https")],
        "client": ("127.0.0.1", 12345),
        "server": ("dealwatch.com.au", 443),
    })


def sample_product(last_seen: str) -> dict:
    return {
        "retailer": "kmart",
        "sku": "ABC123",
        "title": "Test & Product",
        "brand": "Example",
        "category": "home",
        "url": "https://www.kmart.com.au/product/abc123",
        "image_url": "https://assets.kmart.com.au/example.jpg",
        "current_price": 19.0,
        "current_rrp": 29.0,
        "price_updated_at": last_seen,
        "last_seen": last_seen,
    }


class ProductSeoTests(unittest.IsolatedAsyncioTestCase):
    async def render_product(self, last_seen: str):
        product = sample_product(last_seen)
        with patch.object(preview_app, "TEMPLATE_PATH", ROOT / "web" / "product.html"), \
                patch.object(preview_app, "fetch_product", AsyncMock(return_value=product)):
            return await preview_app.preview(
                request_for("/p/kmart/ABC123"), "kmart", "ABC123")

    async def test_product_response_has_one_description_and_real_initial_content(self):
        response = await self.render_product(datetime.now(timezone.utc).isoformat())
        body = response.body.decode()

        self.assertEqual(body.count('<meta name="description"'), 1)
        self.assertNotIn('content="noindex,follow"', body)
        self.assertIn('<h1 class="display">Test &amp; Product</h1>', body)
        self.assertIn('href="/retailers/kmart"', body)
        self.assertIn('href="/deals/home"', body)
        self.assertIn('$19.00', body)
        self.assertIn("s-maxage=300", response.headers["cache-control"])

        scripts = re.findall(
            r'<script type="application/ld\+json">(.*?)</script>', body, re.S)
        self.assertEqual(len(scripts), 1)
        graph = json.loads(scripts[0])["@graph"]
        product = next(item for item in graph if item["@type"] == "Product")
        self.assertEqual(product["offers"]["availability"],
                         "https://schema.org/InStock")
        self.assertTrue(any(item["@type"] == "BreadcrumbList" for item in graph))

    async def test_stale_product_does_not_claim_to_be_in_stock(self):
        response = await self.render_product("2020-01-01T00:00:00+00:00")
        self.assertNotIn("https://schema.org/InStock", response.body.decode())

    async def test_api_failure_is_retryable_not_a_false_404(self):
        response = type("UpstreamResponse", (), {"status_code": 500})()
        fake_client = type("Client", (), {"get": AsyncMock(return_value=response)})()
        with patch.object(preview_app, "client", fake_client):
            with self.assertRaises(HTTPException) as caught:
                await preview_app.fetch_product("kmart", "ABC123")
        self.assertEqual(caught.exception.status_code, 503)


class SitemapSeoTests(unittest.IsolatedAsyncioTestCase):
    async def test_sitemap_requests_only_recent_positive_price_rows(self):
        now = datetime.now(timezone.utc).isoformat()
        rows = [{"retailer": "kmart", "sku": "ABC123",
                 "price_updated_at": now, "last_seen": now}]
        upstream = type("UpstreamResponse", (), {
            "status_code": 200,
            "json": lambda self: rows,
        })()
        fake_client = type("Client", (), {"get": AsyncMock(return_value=upstream)})()

        with patch.object(preview_app, "client", fake_client), \
                patch.object(Path, "exists", return_value=False), \
                patch.object(Path, "mkdir"), \
                patch.object(Path, "write_bytes"), \
                patch.object(Path, "replace"):
            response = await preview_app._sitemap_products(1)

        params = fake_client.get.await_args.kwargs["params"]
        self.assertEqual(params["current_price"], "gt.0")
        self.assertTrue(params["last_seen"].startswith("gte."))
        self.assertTrue(params["order"].startswith("last_seen.desc"))
        root = ET.fromstring(response.body)
        loc = root.find("{http://www.sitemaps.org/schemas/sitemap/0.9}url/"
                        "{http://www.sitemaps.org/schemas/sitemap/0.9}loc")
        self.assertEqual(loc.text, "https://dealwatch.com.au/p/kmart/ABC123")


if __name__ == "__main__":
    unittest.main()
