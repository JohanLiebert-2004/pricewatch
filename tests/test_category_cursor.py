import sqlite3
import unittest
from unittest.mock import patch

import categorize


class CategoryCursorTests(unittest.TestCase):
    def test_unmatched_titles_do_not_starve_later_products(self):
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.executescript('CREATE TABLE products(id INTEGER PRIMARY KEY,retailer TEXT,title TEXT,subcategory TEXT); CREATE TABLE kv(k TEXT PRIMARY KEY,v TEXT);')
        conn.executemany("INSERT INTO products VALUES (?,'example','unmatched',NULL)", [(i,) for i in range(1, 1002)])
        with patch.object(categorize, 'SUBCAT_RULES', {'example': []}):
            self.assertEqual(categorize.backfill_subcategories(conn), 0)
            self.assertEqual(conn.execute('SELECT v FROM kv').fetchone()[0], '1000')
            categorize.backfill_subcategories(conn)
            self.assertEqual(conn.execute('SELECT v FROM kv').fetchone()[0], '1001')
            categorize.backfill_subcategories(conn)
            self.assertEqual(conn.execute('SELECT v FROM kv').fetchone()[0], '0')
        conn.close()
