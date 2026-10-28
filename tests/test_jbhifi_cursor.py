import json
import unittest
from unittest.mock import patch
from scrapers.jbhifi import JBHiFiScraper, MAX_PAGES
from scrapers.base import Blocked


class JBHiFiCursorTests(unittest.TestCase):
    def test_success_resumes_next_page_and_catalogue_end_wraps(self):
        scraper = JBHiFiScraper.__new__(JBHiFiScraper)
        state = {'page': MAX_PAGES - 1}
        payload = json.dumps({'products':[{'id':1}] * 250})
        with patch.object(scraper,'get',return_value=payload) as get, patch.object(scraper,'_record_from_listing',side_effect=lambda item:item):
            self.assertEqual(len(list(scraper.refresh_listings(budget=1,state=state))),250)
            self.assertIn(f'page={MAX_PAGES-1}',get.call_args.args[0])
            self.assertEqual(state['page'],MAX_PAGES)
            list(scraper.refresh_listings(budget=1,state=state))
            self.assertIn(f'page={MAX_PAGES}',get.call_args.args[0])
            self.assertEqual(state['page'],1)

    def test_blocked_page_does_not_advance_cursor(self):
        scraper = JBHiFiScraper.__new__(JBHiFiScraper);state={'page':12}
        with patch.object(scraper,'get',side_effect=Blocked('HTTP 403')):
            with self.assertRaises(Blocked):list(scraper.refresh_listings(budget=1,state=state))
        self.assertEqual(state['page'],12)
