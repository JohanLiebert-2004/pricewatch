import io
import subprocess
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

import local_sephora_sweep as sweep


class SephoraSweepTests(unittest.TestCase):
    def run_sweep(self, result=None, error=None):
        connect = MagicMock()
        with patch.dict(sys.modules, {'psycopg': types.SimpleNamespace(connect=connect)}), \
                patch.object(sweep.subprocess, 'run', return_value=result, side_effect=error):
            success = sweep._run_sweep({'DATABASE_URL': 'test'}, io.StringIO())
        sql = [call.args[0] for call in connect.return_value.__enter__.return_value.execute.call_args_list]
        self.assertTrue(any('sephora_local_running' in query and 'DELETE' in query for query in sql))
        return success, any('sephora_local_heartbeat' in query for query in sql)

    def test_only_complete_committed_feed_writes_success_heartbeat(self):
        result = subprocess.CompletedProcess([], 0,
            'Sephora catalogue window complete: 2 pages\n-> 1000 listings seen', '')
        self.assertEqual(self.run_sweep(result), (True, True))

    def test_partial_or_failed_runs_release_lease_without_success_heartbeat(self):
        for code, output in ((1, '-> 500 listings seen'),
                             (0, '-> 500 listings seen'),
                             (0, 'Sephora catalogue window complete: 1 pages\n-> 0 listings seen')):
            with self.subTest(code=code, output=output):
                self.assertEqual(self.run_sweep(subprocess.CompletedProcess([], code, output, '')),
                                 (False, False))

    def test_timeout_releases_lease_without_success_heartbeat(self):
        self.assertEqual(self.run_sweep(error=subprocess.TimeoutExpired('refresh', 3600)),
                         (False, False))


if __name__ == '__main__':
    unittest.main()
