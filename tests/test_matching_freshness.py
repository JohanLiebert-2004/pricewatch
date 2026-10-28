import datetime as dt
import sqlite3
import unittest
import anomaly
import db


class MatchingFreshnessTests(unittest.TestCase):
    def test_changed_barcode_lookup_rejects_stale_peer_prices(self):
        conn = sqlite3.connect(':memory:');conn.row_factory = sqlite3.Row;conn.executescript(db.SCHEMA)
        now = dt.datetime.now(dt.timezone.utc)
        for retailer, price, days in [('kmart',50,0),('target',60,0),('bigw',500,7)]:
            conn.execute('INSERT INTO products(retailer,sku,title,url,gtin,current_price,last_seen) VALUES (?,?,?,?,?,?,?)',
                         (retailer,'1','Example','https://example.org','4006381333931',price,(now-dt.timedelta(days=days)).isoformat()))
        product = conn.execute('SELECT * FROM products WHERE retailer=?',('kmart',)).fetchone()
        peers = anomaly._cross_peers(conn,[product])
        self.assertEqual(peers[1]['prices'],[(2,60)])
        conn.close()

    def test_old_retailer_prices_cannot_create_cross_store_savings(self):
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA)
        now = dt.datetime.now(dt.timezone.utc)
        for retailer, price, days in [('kmart', 50, 0), ('target', 60, 0), ('bigw', 500, 7)]:
            conn.execute('INSERT INTO products(retailer,sku,title,url,gtin,current_price,last_seen) VALUES (?,?,?,?,?,?,?)',
                         (retailer, '1', 'Example', 'https://example.org', '12345', price,
                          (now-dt.timedelta(days=days)).isoformat()))
        groups = anomaly._cross_peers(conn)
        self.assertEqual(set(groups), {1, 2})
        self.assertEqual(groups[1]['prices'], [(2, 60)])
        self.assertNotIn(500, [price for group in groups.values() for _, price in group['prices']])
        conn.close()
