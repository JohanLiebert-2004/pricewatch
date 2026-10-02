import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

with patch.dict(os.environ, {
    "TELEGRAM_BOT_TOKEN": "test-token",
    "DATABASE_URL": "postgresql://localhost/pricewatch",
}):
    from services import telegram_bot as bot


class TelegramTests(unittest.TestCase):
    def test_retired_database_is_rejected_before_connecting(self):
        for url in ("", "postgresql://example.supabase.co/postgres",
                    "postgresql://example.pooler.supabase.com/postgres"):
            with self.subTest(url=url), patch.object(bot, "DATABASE_URL", url), \
                    patch.object(bot.psycopg, "connect") as connect:
                with self.assertRaisesRegex(RuntimeError, "current OCI"):
                    bot.db()
                connect.assert_not_called()

    def test_service_uses_current_database_override(self):
        unit = (Path(__file__).resolve().parents[1] /
                "infra/oci/services/pricewatch-bot.service").read_text()
        files = [line for line in unit.splitlines() if line.startswith("EnvironmentFile=")]
        self.assertEqual(files, ["EnvironmentFile=/opt/pricewatch.env",
                                 "EnvironmentFile=/etc/pricewatch-kmart.env"])

    def test_existing_product_is_subscribed_with_exact_sku(self):
        conn = Mock()
        conn.execute.return_value.fetchone.return_value = {"title": "Book & toy"}
        with patch.object(bot, "send") as send:
            bot.handle_start(conn, 123, "i_myer_001_A-B")
        self.assertEqual(conn.execute.call_args_list[0].args[1], ("myer", "001_A-B"))
        self.assertEqual(conn.execute.call_args_list[1].args[1],
                         (123, "myer", "001_A-B", "Book & toy"))
        self.assertIn("Watching", send.call_args.args[1])
        self.assertIn("Book &amp; toy", send.call_args.args[1])

    def test_missing_product_is_not_subscribed(self):
        conn = Mock()
        conn.execute.return_value.fetchone.return_value = None
        with patch.object(bot, "send") as send:
            bot.handle_start(conn, 123, "i_myer_missing")
        self.assertEqual(conn.execute.call_count, 1)
        self.assertIn("Nothing was subscribed", send.call_args.args[1])

    def test_invalid_or_oversize_payload_does_not_query_database(self):
        for payload in ("i_chemistwarehouse_" + "A" * 48, "i_myer_a\n", "i_unknown_a"):
            with self.subTest(payload=payload), patch.object(bot, "send"):
                conn = Mock()
                bot.handle_start(conn, 123, payload)
                conn.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
