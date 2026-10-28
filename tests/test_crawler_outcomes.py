import argparse
import json
import sqlite3
import unittest
from unittest.mock import patch

import db
import run
from scrapers.base import Blocked


class CrawlerOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(db.SCHEMA)
        self.addCleanup(self.conn.close)
        for name in ('send_item_watch', 'send_ram_watch'):
            mock = patch.object(run.alerts, name)
            mock.start()
            self.addCleanup(mock.stop)
        mock = patch.object(run.db, 'connect', return_value=self.conn)
        mock.start()
        self.addCleanup(mock.stop)
        mock = patch.object(db, 'DATABASE_URL', None)
        mock.start()
        self.addCleanup(mock.stop)

    def health(self):
        return json.loads(self.conn.execute("SELECT v FROM kv WHERE k='scraper_health:kmart'").fetchone()[0])

    def refresh(self, items, error=None):
        class Scraper:
            def refresh_listings(self, **kwargs):
                yield from items
                if error:
                    raise error
        with patch.dict(run.REGISTRY, {'kmart': Scraper}):
            run.cmd_refresh(argparse.Namespace(retailer='kmart', budget=1))

    def test_block_preserves_partial_products_and_exits_failure(self):
        record = db.ProductRecord('kmart', '1', 'Product', 'https://example.org', 50)
        with self.assertRaisesRegex(RuntimeError, 'Refresh incomplete'):
            self.refresh([record], Blocked('HTTP 403'))
        self.assertEqual(self.health()['status'], 'blocked')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM products').fetchone()[0], 1)

    def test_network_failure_is_not_marked_success(self):
        with self.assertRaises(RuntimeError):
            self.refresh([], ConnectionError('unavailable'))
        self.assertEqual(self.health()['status'], 'error')

    def test_empty_listing_feed_fails(self):
        with self.assertRaises(RuntimeError):
            self.refresh([])
        self.assertEqual(self.health()['status'], 'error')

    def test_success_records_products(self):
        self.refresh([db.ProductRecord('kmart', '1', 'Product', 'https://example.org', 50)])
        self.assertEqual(self.health()['status'], 'ok')

    def test_existing_cheap_product_refreshes_without_adding_new_cheap_items(self):
        self.conn.execute("INSERT INTO products(retailer,sku,title,url,current_price,region) "
                          "VALUES ('kmart','tracked','Existing','https://example.org',20,'')")
        self.refresh([
            db.ProductRecord('kmart', 'tracked', 'Existing', 'https://example.org', 5),
            db.ProductRecord('kmart', 'new', 'New cheap item', 'https://example.org', 5),
        ])
        rows = self.conn.execute('SELECT sku,current_price FROM products').fetchall()
        self.assertEqual([(row['sku'], row['current_price']) for row in rows], [('tracked', 5)])

    def test_blocked_crawl_exits_failure(self):
        self.conn.execute("INSERT INTO crawl_queue(retailer,url) VALUES ('kmart','https://example.org')")
        class Scraper:
            def parse_product(self, url, raw):
                return None
            def get(self, url):
                raise Blocked('HTTP 403')
        with patch.dict(run.REGISTRY, {'kmart': Scraper}), self.assertRaisesRegex(RuntimeError, 'crawl blocked'):
            run.cmd_crawl(argparse.Namespace(retailer='kmart', batch=1))
        self.assertEqual(self.health()['status'], 'blocked')
        queue = self.conn.execute('SELECT fails,last_scraped FROM crawl_queue').fetchone()
        self.assertEqual(queue['fails'], 0)
        self.assertIsNotNone(queue['last_scraped'])
