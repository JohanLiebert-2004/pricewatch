import argparse
from contextlib import nullcontext
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

from collector_lock import collector_lock
import run


class CollectorLockTests(unittest.TestCase):
    def test_busy_lock_does_not_call_collector(self):
        fn = MagicMock()
        args = argparse.Namespace(cmd='refresh', retailer='bigw', fn=fn)
        with patch.object(run, 'collector_lock', return_value=nullcontext(False)):
            run.execute(args)
        fn.assert_not_called()

    def test_connection_failure_stops_before_collection(self):
        fn = MagicMock()
        args = argparse.Namespace(cmd='crawl', retailer='chemistwarehouse', fn=fn)
        with patch.object(run, 'collector_lock', side_effect=RuntimeError('database unavailable')):
            with self.assertRaises(RuntimeError):
                run.execute(args)
        fn.assert_not_called()

    def test_session_released_after_collector_error(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = (True,)
        with patch.dict(sys.modules, {'psycopg': types.SimpleNamespace(connect=lambda *a, **k: conn)}):
            with self.assertRaisesRegex(ValueError, 'collector failed'):
                with collector_lock('postgresql://test', 'sephora') as acquired:
                    self.assertTrue(acquired)
                    raise ValueError('collector failed')
        conn.close.assert_called_once()

    def test_busy_session_is_closed(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = (False,)
        with patch.dict(sys.modules, {'psycopg': types.SimpleNamespace(connect=lambda *a, **k: conn)}):
            with collector_lock('postgresql://test', 'jbhifi') as acquired:
                self.assertFalse(acquired)
        conn.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
