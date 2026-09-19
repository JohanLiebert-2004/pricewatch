import sqlite3
import unittest
from unittest.mock import patch

import anomaly
import db


class DetectionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(db.SCHEMA)
        self.addCleanup(self.conn.close)
        peers = patch.object(anomaly, "_cross_peers", return_value={})
        peers.start()
        self.addCleanup(peers.stop)

    def add_products(self, count, date="2026-01-02", start=1):
        self.conn.executemany(
            "INSERT INTO products (id, retailer, sku, title, current_price, current_rrp) "
            "VALUES (?, 'kmart', ?, 'Example', 40, 100)",
            [(i, str(i)) for i in range(start, start + count)])
        self.conn.executemany(
            "INSERT INTO price_snapshots (product_id, price, rrp, scraped_at) "
            "VALUES (?, 40, 100, ?)",
            [(i, date) for i in range(start, start + count)])
        self.conn.commit()

    def set_watermark(self):
        self.conn.execute("INSERT INTO kv VALUES ('anomaly_last_detect_at', '2026-01-01')")
        self.conn.commit()

    def test_backlog_larger_than_bind_limit_keeps_incremental_scope(self):
        # Reproduce the same failure at a small limit, without a production DB.
        self.conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)
        self.add_products(1100)
        self.add_products(1, date="2025-12-31", start=1101)
        self.set_watermark()
        found = anomaly.run(self.conn)
        self.assertEqual(len(found), 1100)
        self.assertEqual(self.conn.execute("SELECT max(product_id) FROM deals").fetchone()[0], 1100)
        self.assertEqual(anomaly.run(self.conn), [])

    def test_changed_product_retains_history_before_watermark(self):
        self.add_products(1)
        self.conn.executemany(
            "INSERT INTO price_snapshots (product_id, price, scraped_at) VALUES (1, 200, ?)",
            [(f"2025-12-{day}",) for day in (27, 28, 29)])
        self.set_watermark()
        found = anomaly.run(self.conn)
        self.assertEqual({d['signal'] for d in found}, {'rrp_gap', 'history_drop'})
        self.assertEqual(next(d['reference'] for d in found if d['signal'] == 'history_drop'), 200)

    def test_first_run_then_unchanged_run(self):
        self.add_products(2)
        self.assertEqual(len(anomaly.run(self.conn)), 2)
        self.assertEqual(anomaly.run(self.conn), [])

    def test_failed_write_does_not_advance_watermark(self):
        self.add_products(1)
        self.set_watermark()
        self.conn.execute("CREATE TRIGGER fail_deal BEFORE INSERT ON deals "
                          "BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            anomaly.run(self.conn)
        self.conn.rollback()
        self.assertEqual(self.conn.execute(
            "SELECT v FROM kv WHERE k='anomaly_last_detect_at'").fetchone()[0], '2026-01-01')


if __name__ == "__main__":
    unittest.main()
