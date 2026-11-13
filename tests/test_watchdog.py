import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    'pricewatch_watchdog', Path(__file__).resolve().parents[1] / 'scripts' / 'watchdog.py')
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)


class WatchdogTests(unittest.TestCase):
    def run_check(self, problems, state, delivered, check_only=False):
        with tempfile.TemporaryDirectory() as directory:
            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.multiple(
                    watchdog, STATE_DIR=directory, LOG=str(Path(directory) / 'watchdog.log'),
                    STATE_FILE=str(Path(directory) / 'state.json')))
                stack.enter_context(patch.object(watchdog, 'check_github_actions', return_value=None))
                stack.enter_context(patch.object(watchdog, 'check_heartbeats', return_value=problems))
                stack.enter_context(patch.object(watchdog, 'check_oci_hosts', return_value=[]))
                stack.enter_context(patch.object(watchdog, 'check_website', return_value=[]))
                stack.enter_context(patch.object(watchdog, '_load_state', return_value=state))
                send = stack.enter_context(patch.object(watchdog, '_send_telegram', return_value=delivered))
                save = stack.enter_context(patch.object(watchdog, '_save_state'))
                output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                self.assertEqual(watchdog.main(['--check-only'] if check_only else []), 0)
                return send, save, output.getvalue()

    def test_check_only_cannot_notify_or_acknowledge(self):
        send, save, output = self.run_check(['stale'], {}, True, check_only=True)
        send.assert_not_called()
        save.assert_not_called()
        self.assertFalse(json.loads(output)['healthy'])
        self.assertFalse(json.loads(output)['notifications_sent'])

    def test_failed_alert_is_not_acknowledged(self):
        send, save, _ = self.run_check(['stale'], {}, False)
        send.assert_called_once()
        self.assertFalse(save.call_args.args[0]['notified_bad'])

    def test_recovery_delivery_is_retried(self):
        send, save, _ = self.run_check([], {'bad': True, 'notified_bad': True}, False)
        send.assert_called_once()
        self.assertTrue(save.call_args.args[0]['notified_bad'])

    def test_delivered_problem_is_debounced(self):
        send, save, _ = self.run_check(['stale'], {'bad': True, 'notified_bad': True}, True)
        send.assert_not_called()
        self.assertTrue(save.call_args.args[0]['notified_bad'])

    def test_old_unacknowledged_state_does_not_hide_problem(self):
        send, save, _ = self.run_check(['stale'], {'bad': True}, True)
        send.assert_called_once()
        self.assertTrue(save.call_args.args[0]['notified_bad'])

    def test_github_check_selects_collector_workflow(self):
        with patch.object(watchdog.httpx, 'get') as get:
            get.return_value.json.return_value = {'workflow_runs': []}
            self.assertIsNotNone(watchdog.check_github_actions(io.StringIO()))
        self.assertTrue(get.call_args.args[0].endswith('/actions/workflows/crawl.yml/runs'))


if __name__ == '__main__':
    unittest.main()
