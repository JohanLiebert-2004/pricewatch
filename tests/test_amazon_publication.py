import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import hashlib
import subprocess
import threading

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
        self.assertEqual(snapshot['coverage']['tracked_products'],3)
        self.assertEqual(snapshot['coverage']['priced_products'],1)
        encoded = json.dumps(snapshot)
        for forbidden in ["DO NOT PUBLISH", "private host", "private job", "seller_key", "fleet", "last_run"]:
            self.assertNotIn(forbidden, encoded)

    def test_publication_without_target_only_writes_local_public_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            publish_snapshot({"generated_at": "2026-11-17T00:00:00+00:00", "products": []}, data)
            self.assertEqual(json.loads((data / "amazon-public.json").read_text())["currency"], "AUD")
            self.assertFalse((data / "publication.lock").exists())

    def test_one_ssh_connection_transmits_only_the_sanitized_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            data=Path(temporary)
            (data/'publish-target.json').write_text(json.dumps({'host':'ubuntu@example.com','key':'local-key-path',
                'remote_directory':'/var/lib/dealwatch-amazon/public'}))
            sent=[]
            def run(args,**kwargs):
                sent.append((args,kwargs['input']))
                return subprocess.CompletedProcess(args,0,hashlib.sha256(kwargs['input']).hexdigest().encode()+b'\n',b'')
            with patch('amazon_watch.publication.subprocess.run',side_effect=run):
                publish_snapshot({'generated_at':'2026-11-17T00:00:00+00:00','products':[],
                                  'fleet':{'private_key':'NEVER SEND'}},data)
            self.assertEqual(len(sent),1)
            self.assertEqual(sent[0][0][0],'ssh')
            self.assertNotIn(b'NEVER SEND',sent[0][1])
            self.assertIsNone(json.loads((data/'publication-status.json').read_text())['error'])

    def test_background_publication_does_not_hold_up_the_scanner(self):
        started,release,finished=threading.Event(),threading.Event(),threading.Event()
        def slow_upload(*args):
            started.set()
            release.wait(5)
            finished.set()
        with tempfile.TemporaryDirectory() as temporary,patch('amazon_watch.publication._push_snapshot',side_effect=slow_upload):
            try:
                publish_snapshot({'generated_at':'2026-11-17T00:00:00+00:00','products':[]},Path(temporary),background=True)
                self.assertTrue(started.wait(1))
                self.assertFalse(finished.is_set())
            finally:
                release.set()
                self.assertTrue(finished.wait(1))

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
