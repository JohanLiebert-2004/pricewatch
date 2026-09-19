import unittest
from unittest.mock import Mock, patch

import publication
import run


class PublicationTests(unittest.TestCase):
    def test_failed_view_does_not_prevent_other_views_publishing(self):
        conn = Mock()

        def execute(sql):
            if sql.endswith("discount_feed"):
                raise RuntimeError("injected timeout")

        conn.execute.side_effect = execute
        with self.assertRaisesRegex(RuntimeError, "Website publication failed: discount_feed"):
            publication.refresh_public_views(conn)
        attempts = [call.args[0] for call in conn.execute.call_args_list
                    if call.args[0].startswith("REFRESH")]
        self.assertEqual(len(attempts), len(publication.PUBLIC_VIEWS))
        self.assertTrue(attempts[-1].endswith("subcategory_stats"))
        self.assertEqual(conn.commit.call_count, len(publication.PUBLIC_VIEWS) - 1)
        conn.rollback.assert_called_once()

    def test_success_commits_each_view(self):
        conn = Mock()
        publication.refresh_public_views(conn)
        self.assertEqual(conn.commit.call_count, len(publication.PUBLIC_VIEWS))
        conn.rollback.assert_not_called()

    def test_publish_refuses_missing_or_retired_database(self):
        for url in (None, "postgresql://example.pooler.supabase.com/postgres"):
            with self.subTest(url=url), patch.object(run.db, 'DATABASE_URL', url), \
                    patch.object(run.db, 'connect') as connect:
                with self.assertRaisesRegex(RuntimeError, 'current OCI'):
                    run.cmd_publish(None)
                connect.assert_not_called()

    def test_publish_closes_connection_after_failure_and_does_not_send_alerts(self):
        conn = Mock()
        with patch.object(run.db, 'DATABASE_URL', 'postgresql://localhost/postgres'), \
                patch.object(run.db, 'connect', return_value=conn), \
                patch.object(run, 'refresh_public_views', side_effect=RuntimeError('failed')), \
                patch.object(run.alerts, 'send_alerts') as alerts, \
                patch.object(run.watch_alerts, 'send_watch_alerts') as watches:
            with self.assertRaises(RuntimeError):
                run.cmd_publish(None)
            conn.close.assert_called_once()
            alerts.assert_not_called()
            watches.assert_not_called()


if __name__ == "__main__":
    unittest.main()
