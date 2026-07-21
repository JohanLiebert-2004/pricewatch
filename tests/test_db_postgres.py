import unittest

import db
from db import ProductRecord


class _Result:
    def fetchall(self):
        return []


class _Connection:
    def __init__(self):
        self.sql = ""
        self.params = ()
        self.committed = False

    def execute(self, sql, params):
        self.sql = sql
        self.params = params
        return _Result()

    def commit(self):
        self.committed = True


class PostgresBulkUpsertTests(unittest.TestCase):
    def test_values_cells_are_explicitly_typed(self):
        conn = _Connection()
        rows = [
            ProductRecord("kmart", "1", "First", "/1", 10, brand=42),
            ProductRecord("kmart", "2", "Second", "/2", 12,
                          brand="Formula 10.0.6"),
        ]

        changed = db._upsert_chunk_pg(conn, rows, "2026-07-21T00:00:00+00:00")

        self.assertEqual(changed, [])
        self.assertTrue(conn.committed)
        first_values_row = conn.sql.split("VALUES ", 1)[1].split("\n", 1)[0]
        self.assertIn("%s::text", first_values_row)
        self.assertIn("%s::boolean", first_values_row)
        self.assertIn("%s::numeric(10,2)", first_values_row)
        self.assertEqual(conn.params[4], 42)
        self.assertEqual(conn.params[19], "Formula 10.0.6")


if __name__ == "__main__":
    unittest.main()
