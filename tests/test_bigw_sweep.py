import io
import subprocess
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

import local_bigw_sweep as sweep


class BigWSweepTests(unittest.TestCase):
    def test_failed_refresh_does_not_start_product_crawl(self):
        result = subprocess.CompletedProcess([], 1, 'BLOCKED: HTTP 403', '')
        with patch.object(sweep.subprocess, 'run', return_value=result) as run:
            self.assertFalse(sweep._run_sweep({}, io.StringIO()))
        self.assertEqual(run.call_count, 1)

    def test_refresh_timeout_does_not_start_product_crawl(self):
        with patch.object(sweep.subprocess, 'run',
                          side_effect=subprocess.TimeoutExpired('refresh', 5400)) as run:
            self.assertFalse(sweep._run_sweep({}, io.StringIO()))
        self.assertEqual(run.call_count, 1)

    def test_legacy_partial_block_does_not_claim_success(self):
        result = subprocess.CompletedProcess(
            [], 0, 'BLOCKED mid-refresh: HTTP 403\n-> 595 listings seen', '')
        with patch.object(sweep.subprocess, 'run', return_value=result) as run:
            self.assertFalse(sweep._run_sweep({}, io.StringIO()))
        self.assertEqual(run.call_count, 1)

    def test_insufficient_refresh_does_not_start_product_crawl(self):
        result = subprocess.CompletedProcess([], 0, '-> 0 listings seen', '')
        with patch.object(sweep.subprocess, 'run', return_value=result) as run:
            self.assertFalse(sweep._run_sweep({}, io.StringIO()))
        self.assertEqual(run.call_count, 1)

    def test_success_runs_both_lanes_and_records_heartbeat(self):
        connect = MagicMock()
        results = [subprocess.CompletedProcess([], 0, '-> 144 listings seen', ''),
                   subprocess.CompletedProcess([], 0, '1 product stored', '')]
        with patch.object(sweep.subprocess, 'run', side_effect=results) as run, \
                patch.dict(sys.modules, {'psycopg': types.SimpleNamespace(connect=connect)}):
            self.assertTrue(sweep._run_sweep({'DATABASE_URL': 'test'}, io.StringIO()))
        self.assertEqual(run.call_count, 2)
        connect.return_value.execute.assert_called_once()
        connect.return_value.commit.assert_called_once()


if __name__ == '__main__':
    unittest.main()
