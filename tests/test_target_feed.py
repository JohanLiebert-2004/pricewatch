import os
import unittest
from unittest import mock

import httpx

from scrapers.base import Blocked
from scrapers.kmart_group import TargetScraper


class TargetFeedTests(unittest.TestCase):
    def setUp(self):
        # Feed parsing does not need BaseScraper's network session.
        self.scraper = TargetScraper.__new__(TargetScraper)

    def test_csv_feed_maps_product_fields_and_deduplicates_skus(self):
        feed = """SKU,Product Name,Deeplink URL,Currency,Price,Was Price,Image URL,Brand,Category,EAN,Availability
71130170,Rainbocorns Axolotlcorn,https://t.cfjump.com/example,AUD,$15.00,$25.00,https://example.test/a.jpg,ZURU,Toys,9312345678901.0,In Stock
71130170,Rainbocorns Axolotlcorn,https://www.target.com.au/p/rainbocorns/71130170,AUD,$14.00,$25.00,https://example.test/b.jpg,ZURU,Toys,9312345678901.0,5
"""

        records = self.scraper._parse_feed(feed, "text/csv")

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.retailer, "target")
        self.assertEqual(record.sku, "71130170")
        self.assertEqual(record.price, 14.0)
        self.assertEqual(record.rrp, 25.0)
        self.assertEqual(record.gtin, "9312345678901")
        self.assertEqual(record.brand, "ZURU")
        self.assertEqual(record.subcategory, "Toys")
        self.assertTrue(record.in_stock)
        self.assertEqual(
            record.url,
            "https://www.target.com.au/p/rainbocorns/71130170",
        )

    def test_json_feed_rejects_non_aud_and_builds_direct_target_url(self):
        feed = """{
          "data": {"products": [
            {"Product ID": "123.0", "Title": "Blue Mug", "Price": "9.95",
             "Currency": "AUD", "URL": "https://t.cfjump.com/tracked"},
            {"Product ID": "999", "Title": "US Item", "Price": "8",
             "Currency": "USD", "URL": "https://www.target.com.au/p/us/999"}
          ]}
        }"""

        records = self.scraper._parse_feed(feed, "application/json")

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].sku, "123")
        self.assertEqual(
            records[0].url,
            "https://www.target.com.au/p/blue-mug/123",
        )

    def test_feed_destination_is_unwrapped_only_for_target_https(self):
        values = self.scraper._feed_values({
            "Deeplink URL": (
                "https://t.cfjump.com/x?url="
                "https%3A%2F%2Fwww.target.com.au%2Fp%2Fsafe%2F42"
            )
        })
        self.assertEqual(
            self.scraper._direct_target_url(values, "42", "Safe"),
            "https://www.target.com.au/p/safe/42",
        )

        insecure = self.scraper._feed_values({
            "Product URL": "http://www.target.com.au/p/unsafe/42"
        })
        self.assertEqual(
            self.scraper._direct_target_url(insecure, "42", "Safe"),
            "https://www.target.com.au/p/safe/42",
        )

    def test_configured_feed_takes_precedence_over_blocked_storefront(self):
        expected = self.scraper._record_from_feed_row({
            "SKU": "42", "Title": "Safe", "Price": "12", "Currency": "AUD"
        })
        with mock.patch.dict(os.environ, {"TARGET_PRODUCT_FEED_URL": "https://feed.test/x"}), \
                mock.patch.object(self.scraper, "_feed_records", return_value=[expected]) as feed, \
                mock.patch.object(self.scraper, "_storefront_records") as storefront:
            records = list(self.scraper.refresh_listings())

        self.assertEqual(records, [expected])
        feed.assert_called_once_with("https://feed.test/x")
        storefront.assert_not_called()

    def test_invalid_json_shape_fails_visibly(self):
        with self.assertRaises(Blocked):
            self.scraper._parse_feed('{"status": "ok"}', "application/json")

    def test_feed_fetch_error_does_not_leak_secret_url(self):
        secret_url = "https://feed.test/export?token=super-secret"
        request = httpx.Request("GET", secret_url)
        response = httpx.Response(403, request=request)
        client = mock.MagicMock()
        client.__enter__.return_value.get.return_value = response

        with mock.patch("scrapers.kmart_group.httpx.Client", return_value=client), \
                self.assertRaises(Blocked) as raised:
            list(self.scraper._feed_records(secret_url))

        self.assertNotIn("super-secret", str(raised.exception))

    def test_feed_url_requires_https(self):
        with self.assertRaisesRegex(Blocked, "must use HTTPS"):
            list(self.scraper._feed_records("http://feed.test/export"))

    def test_storefront_listing_normalizes_relative_url_and_image(self):
        record = self.scraper._record_from_listing({
            "id": "7",
            "title": "Sample",
            "baseProductUrl": "/p/sample/7",
            "image": {"url": "https://example.test/sample.jpg"},
            "price": {"offerPrice": 10, "wasPrice": 15},
        })

        self.assertEqual(record.url, "https://www.target.com.au/p/sample/7")
        self.assertEqual(record.image_url, "https://example.test/sample.jpg")
        self.assertEqual(record.rrp, 15.0)


if __name__ == "__main__":
    unittest.main()
