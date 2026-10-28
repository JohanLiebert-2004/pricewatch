import os
import unittest
from unittest.mock import MagicMock, patch

from scrapers.base import BaseScraper, Blocked
from scrapers.bigw import BigWScraper
from scrapers.firefox import FirefoxSession


URL = 'https://www.bigw.com.au/product/example/p/123'


class FirefoxTransportTests(unittest.TestCase):
    def session(self, status=200, text='<html>product</html>'):
        session = FirefoxSession.__new__(FirefoxSession)
        session._session = 'test'
        session._host = 'www.bigw.com.au'
        session._process = None
        session._command = MagicMock(side_effect=[None, URL, text, status])
        return session

    def test_preserves_page_and_actual_status(self):
        response = self.session().get(URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, '<html>product</html>')

    def test_invalid_status_is_not_assumed_success(self):
        for status in (None, 0):
            with self.assertRaisesRegex(RuntimeError, 'valid navigation HTTP status'):
                self.session(status=status).get(URL)

    def test_refusal_stops_without_retry(self):
        session = self.session(403, '<h1>Access Denied</h1>')
        scraper = BigWScraper.__new__(BigWScraper)
        scraper.session = session
        scraper._cffi = False
        scraper._warmed = True
        with patch('scrapers.base.time.sleep'), self.assertRaisesRegex(Blocked, 'HTTP 403'):
            scraper.get(URL)
        navigations = [call for call in session._command.call_args_list
                       if call.args[:2] == ('POST', '/url')]
        self.assertEqual(len(navigations), 1)

    def test_rejects_other_hosts_before_navigation(self):
        session = self.session()
        for url in ('http://www.bigw.com.au/', 'https://example.org/',
                    'https://user:secret@www.bigw.com.au/', 'https://www.bigw.com.au:444/'):
            with self.assertRaises(ValueError):
                session.get(url)
        session._command.assert_not_called()

    def test_close_is_idempotent(self):
        session = self.session()
        session._command = MagicMock()
        process = MagicMock()
        process.poll.return_value = None
        session._process = process
        session.close()
        session.close()
        process.terminate.assert_called_once()
        session._command.assert_called_once_with('DELETE', '', timeout=10)

    def test_existing_client_remains_default(self):
        with patch.dict(os.environ, {'BIGW_BROWSER': ''}), \
                patch.object(BaseScraper, '__init__') as init:
            BigWScraper()
        init.assert_called_once()

    def test_browser_mode_is_explicit_and_direct_only(self):
        with patch.dict(os.environ, {'BIGW_BROWSER': 'firefox', 'PROXY_URL': ''}), \
                patch('scrapers.firefox.FirefoxSession') as factory:
            self.assertIs(BigWScraper().session, factory.return_value)
        with patch.dict(os.environ, {'BIGW_BROWSER': 'firefox', 'PROXY_URL': 'configured'}):
            with self.assertRaises(ValueError):
                BigWScraper()
        with patch.dict(os.environ, {'BIGW_BROWSER': 'unknown'}):
            with self.assertRaises(ValueError):
                BigWScraper()


if __name__ == '__main__':
    unittest.main()
