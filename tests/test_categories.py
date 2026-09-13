import sqlite3
import unittest

from categorize import classify_product, is_book_identifier, repair_misclassified_books
from scripts.repair_categories import repairs


class CategoryTests(unittest.TestCase):
    def test_identifiers_protect_books_with_ambiguous_titles(self):
        self.assertTrue(is_book_identifier('9780747532743'))
        self.assertFalse(is_book_identifier('9780747532744'))
        self.assertFalse(is_book_identifier('4893156045881'))
        self.assertEqual(classify_product('Eagle Strike', 'qbd', '9780747532743'), 'books')
        self.assertEqual(classify_product('Science toy kit', 'qbd', '4893156045881'), 'toys')

    def test_taxonomy_wins_over_incidental_title_words(self):
        self.assertEqual(classify_product('Doll dress', 'kmart', None, 'Toys'), 'toys')
        self.assertEqual(classify_product('Toner', 'kmart', None, 'Beauty'), 'beauty')
        self.assertEqual(classify_product('Apple Magic Trackpad (2021)'), 'tech')
        self.assertEqual(classify_product('Hydrating toner'), 'beauty')
        self.assertEqual(classify_product('Laser toner cartridge'), 'tech')

    def test_cleanup_retains_isbn_book_and_repairs_false_positive(self):
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute('CREATE TABLE products (id INT, title TEXT, retailer TEXT, gtin TEXT, subcategory TEXT, category TEXT)')
        conn.executemany('INSERT INTO products VALUES (?,?,?,?,?,?)', [
            (1,'Eagle Strike','qbd','9780747532743',None,'books'),
            (2,'Cotton shirt','myer',None,None,'books'),
        ])
        self.assertEqual(repair_misclassified_books(conn), 1)
        self.assertEqual(conn.execute('SELECT category FROM products WHERE id=1').fetchone()[0], 'books')
        self.assertEqual(conn.execute('SELECT category FROM products WHERE id=2').fetchone()[0], 'clothing')
        conn.close()

    def test_repair_does_not_override_existing_category_with_title_guess(self):
        rows=[dict(id=1,retailer='jbhifi',gtin=None,subcategory=None,title='Speaker stand',category='home'),
              dict(id=2,retailer='qbd',gtin='9780747532743',subcategory=None,title='Eagle Strike',category='other')]
        self.assertEqual(list(repairs(rows)), [('books',2,'other')])
