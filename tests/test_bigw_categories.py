import unittest
from unittest.mock import MagicMock

from scrapers.base import Blocked, NotFound
from scrapers.bigw import BigWScraper


class BigWCategoryTests(unittest.TestCase):
    def scraper(self):
        scraper = BigWScraper.__new__(BigWScraper)
        scraper._category_paths = MagicMock(return_value=[('/old/c/1', 'Home'), ('/live/c/2', 'Home')])
        scraper._next_data = MagicMock(side_effect=[NotFound('retired'), {
            'props': {'pageProps': {'results': {'organic': {
                'results': [{'code': '42', 'derived': {'priceRange': {'min': {'amount': 4500}}}}],
                'pageCount': 1}}}}}])
        return scraper

    def test_category_404_does_not_starve_live_categories(self):
        scraper = self.scraper()
        state = {}
        records = list(scraper.refresh_listings(budget=3, state=state))
        self.assertEqual([r.sku for r in records], ['42'])
        self.assertIn('/old/c/1', state)
        self.assertIn('/live/c/2', state)

    def test_category_404_consumes_request_budget(self):
        scraper = self.scraper()
        self.assertEqual(list(scraper.refresh_listings(budget=2)), [])
        self.assertEqual(scraper._next_data.call_count, 1)

    def test_refusal_stops_without_next_category(self):
        scraper = self.scraper()
        scraper._next_data.side_effect = Blocked('HTTP 429')
        with self.assertRaises(Blocked):
            list(scraper.refresh_listings(budget=3))
        self.assertEqual(scraper._next_data.call_count, 1)


if __name__ == '__main__':
    unittest.main()
