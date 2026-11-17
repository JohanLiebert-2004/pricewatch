import json
from pathlib import Path
import tempfile
import unittest

from amazon_watch.publication import public_snapshot, publish_snapshot
from scripts.amazon_navigation import apply


class AmazonIntegrationTests(unittest.TestCase):
    def test_public_export_removes_runtime_details_and_wrong_region(self):
        product = {"asin": "B0DGWTGZQH", "title": "Camera", "cents": 10000,
                   "delivery_country": "AU", "delivery_postcode": "6000", "reference_cents": 20000,
                   "worker": "private host", "job_id": "private job", "seller_key": "private detail"}
        snapshot = public_snapshot({"generated_at": "2026-11-17T00:00:00+00:00", "products": [
            product, {**product, "asin": "B000000000", "delivery_country": "IN"},
            {**product, "asin": "B000000001", "excluded_from_ranking": True}],
            "fleet": {"private_key": "DO NOT PUBLISH"}, "last_run": {"error": "private infrastructure"}})
        self.assertEqual(len(snapshot["products"]), 1)
        encoded = json.dumps(snapshot)
        for forbidden in ["DO NOT PUBLISH", "private host", "private job", "seller_key", "fleet", "last_run"]:
            self.assertNotIn(forbidden, encoded)

    def test_publication_without_target_only_writes_local_public_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            publish_snapshot({"generated_at": "2026-11-17T00:00:00+00:00", "products": []}, data)
            self.assertEqual(json.loads((data / "amazon-public.json").read_text())["currency"], "AUD")
            self.assertFalse((data / "publication.lock").exists())

    def test_navigation_is_additive_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = '<body><div class="navlinks"><a href="/">Deals</a></div><p>Existing content</p></body>'
            (root / "index.html").write_text(original)
            (root / "sitemap-pages.xml").write_text('<urlset></urlset>')
            apply(root)
            first = (root / "index.html").read_text()
            apply(root)
            self.assertEqual(first, (root / "index.html").read_text())
            self.assertIn("<p>Existing content</p>", first)
            self.assertEqual(first.count('href="/amazon.html"'), 1)
            self.assertEqual((root / "sitemap-pages.xml").read_text().count('/amazon.html'), 1)

    def test_mobile_navigation_retains_existing_links_and_fits_new_item(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / 'index.html'
            path.write_text('<nav class="mobile-nav"><a href="/">Home</a><a href="/alerts.html">Alerts</a></nav>')
            apply(root)
            result = path.read_text()
            apply(root)
            self.assertEqual(path.read_text(), result)
            self.assertIn('href="/alerts.html"', result)
            self.assertIn('repeat(3,minmax(0,1fr))', result)
            self.assertEqual(result.count('href="/amazon.html"'), 1)


if __name__ == "__main__":
    unittest.main()
