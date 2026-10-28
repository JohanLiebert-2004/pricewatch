import unittest
from scrapers.bigw import BigWScraper


class BigWReferencePriceTests(unittest.TestCase):
    def record(self, fields=None, title='Example'):
        item = {'code': '123', 'information': {'name': title},
                'derived': {'priceRange': {'min': {'amount': 1000}}}}
        item['derived'].update(fields or {})
        return BigWScraper.__new__(BigWScraper)._record_from_listing(item)

    def test_scalar_was_price_is_cents(self):
        record = self.record({'wasPrice': 1500})
        self.assertEqual(record.price, 10)
        self.assertEqual(record.rrp, 15)

    def test_object_was_price_is_cents(self):
        self.assertEqual(self.record({'wasPrice': {'amount': 2500}}).rrp, 25)

    def test_absent_or_non_discounted_reference_stays_unknown(self):
        for fields in ({}, {'wasPrice': 1000}, {'wasPrice': 900}, {'wasPrice': True}):
            self.assertIsNone(self.record(fields).rrp)

    def test_title_is_not_a_price_source(self):
        self.assertIsNone(self.record(title='Was $900 novelty sign').rrp)


if __name__ == '__main__':
    unittest.main()
