import json
import os
import unittest
from unittest.mock import MagicMock, patch

from scrapers.base import Blocked
from scrapers.firefox import BrowserResponse, FirefoxSession
from scrapers.sephora import API, SephoraScraper


class SephoraFirefoxTests(unittest.TestCase):
    def scraper(self):
        scraper = SephoraScraper.__new__(SephoraScraper)
        scraper._firefox = True
        scraper._cffi = False
        scraper._warmed = False
        scraper.session = MagicMock()
        return scraper

    def test_api_fetch_preserves_au_country_headers(self):
        scraper = self.scraper()
        scraper.session.fetch.return_value = BrowserResponse(200, '{}')
        url = API + '?page[size]=500'
        self.assertEqual(scraper._request(url), (200, '{}'))
        scraper.session.fetch.assert_called_once_with(
            url, headers={'X-Platform': 'Web', 'X-Site-Country': 'AU'})
        scraper.session.get.assert_not_called()

    def test_warmup_navigates_before_api_and_stops_on_refusal(self):
        scraper = self.scraper()
        scraper.session.get.return_value = BrowserResponse(200, '<html>home</html>')
        scraper.session.fetch.return_value = BrowserResponse(403, 'Access Denied')
        with patch('scrapers.base.time.sleep'), self.assertRaisesRegex(Blocked, 'HTTP 403'):
            scraper.get(API + '?page[size]=500')
        scraper.session.get.assert_called_once_with(scraper.warmup_url)
        self.assertEqual(scraper.session.fetch.call_count, 1)

    def test_firefox_is_opt_in_and_scoped_to_sephora(self):
        with patch.dict(os.environ, {'SEPHORA_BROWSER': 'firefox', 'PROXY_URL': ''}), \
                patch('scrapers.firefox.FirefoxSession') as factory:
            SephoraScraper()
        factory.assert_called_once_with(host='www.sephora.com.au')

    def test_browser_api_requires_current_same_origin(self):
        session = FirefoxSession.__new__(FirefoxSession)
        session._host = 'www.sephora.com.au'
        session._session = 'test'
        session._command = MagicMock(return_value='https://www.bigw.com.au/')
        with self.assertRaises(ValueError):
            session.fetch(API, {'X-Site-Country': 'AU'})
        self.assertEqual(session._command.call_count, 1)

    def test_browser_api_returns_actual_status_without_retry(self):
        session = FirefoxSession.__new__(FirefoxSession)
        session._host = 'www.sephora.com.au'
        session._session = 'test'
        session._command = MagicMock(side_effect=[
            'https://www.sephora.com.au/', {'status': 429, 'text': 'refused',
                                           'headers': {'retry-after': '60'}}])
        response = session.fetch(API, {'X-Site-Country': 'AU'})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers['retry-after'], '60')
        self.assertEqual(session._command.call_count, 2)

    def page(self, number, total=2, empty=False):
        return json.dumps({'data': [] if empty else [{
            'id': str(number), 'attributes': {'price': 11700, 'slug-url': 'example',
                                            'name': 'Example'}}],
            'meta': {'total-pages': total}})

    def test_complete_feed_and_incomplete_budget_are_distinct(self):
        scraper = self.scraper()
        scraper.get = MagicMock(side_effect=[self.page(1), self.page(2)])
        self.assertEqual(len(list(scraper.refresh_listings(budget=2))), 2)
        scraper.get = MagicMock(return_value=self.page(1))
        with self.assertRaisesRegex(RuntimeError, 'budget exhausted'):
            list(scraper.refresh_listings(budget=1))

    def test_empty_page_does_not_claim_complete(self):
        scraper = self.scraper()
        scraper.get = MagicMock(return_value=self.page(1, empty=True))
        with self.assertRaisesRegex(RuntimeError, 'empty'):
            list(scraper.refresh_listings(budget=2))


if __name__ == '__main__':
    unittest.main()
