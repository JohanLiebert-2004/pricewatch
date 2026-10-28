import unittest
from unittest.mock import Mock, patch
from scrapers.base import BaseScraper, Blocked


class BlockDiagnosticsTests(unittest.TestCase):
    def scraper(self, warmup=None):
        scraper = BaseScraper.__new__(BaseScraper)
        scraper.name = 'example'
        scraper.delay = 0
        scraper.warmup_url = warmup
        scraper._warmed = False
        scraper._cffi = True
        return scraper

    def test_blocked_warmup_does_not_request_product(self):
        scraper = self.scraper('https://example.org/')
        with patch.object(scraper, '_request', return_value=(403, 'Access Denied')) as request:
            with self.assertRaisesRegex(Blocked, 'phase=warm-up; response=access-denied page'):
                scraper.get('https://example.org/product')
        request.assert_called_once_with('https://example.org/')

    def test_blocked_page_is_not_retried_in_fresh_session(self):
        scraper = self.scraper()
        with patch.object(scraper, '_request', return_value=(429, 'Too many requests')) as request:
            with self.assertRaisesRegex(Blocked, 'HTTP 429'):
                scraper.get('https://example.org/product')
        self.assertEqual(request.call_count, 1)

    def test_diagnostics_include_safe_headers_and_exclude_cookies(self):
        scraper = self.scraper()
        response = Mock(status_code=403, text='Access Denied',
                        headers={'server':'ExampleEdge','retry-after':'600','set-cookie':'private-cookie'})
        scraper.session = Mock()
        scraper.session.get.return_value = response
        with self.assertRaises(Blocked) as caught:
            scraper.get('https://example.org/product')
        self.assertIn('server=ExampleEdge', str(caught.exception))
        self.assertIn('Retry-After=600', str(caught.exception))
        self.assertNotIn('private-cookie', str(caught.exception))

    def test_challenge_with_http_200_is_not_product_content(self):
        scraper = self.scraper()
        with patch.object(scraper,'_request',return_value=(200,'<div class="sec-if-cpt-container">')):
            with self.assertRaisesRegex(Blocked, 'browser-challenge page'):
                scraper.get('https://example.org/product')

    def test_successful_warmup_and_product_still_work(self):
        scraper = self.scraper('https://example.org/')
        with patch.object(scraper,'_request',side_effect=[(200,'home'),(200,'product')]) as request:
            self.assertEqual(scraper.get('https://example.org/product'),'product')
        self.assertEqual(request.call_count,2)
