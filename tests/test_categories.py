import sqlite3
import unittest
from unittest import mock

from categorize import classify_product, is_book_identifier, repair_misclassified_books
from scripts.repair_categories import repairs
from scripts import repair_categories


class CategoryTests(unittest.TestCase):
    def test_repair_refuses_retired_database_before_connecting(self):
        with mock.patch.object(repair_categories.db, 'DATABASE_URL',
                               'postgresql://example.pooler.supabase.com/postgres'), \
                mock.patch.object(repair_categories.db, 'connect') as connect, \
                mock.patch('sys.argv', ['repair_categories.py', '--apply']), \
                mock.patch('sys.stderr'):
            with self.assertRaises(SystemExit):
                repair_categories.main()
            connect.assert_not_called()

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
        conn.execute('CREATE TABLE products (id INT, title TEXT, retailer TEXT, gtin TEXT, subcategory TEXT, category TEXT, is_marketplace BOOLEAN)')
        conn.executemany('INSERT INTO products VALUES (?,?,?,?,?,?,?)', [
            (1,'Eagle Strike','qbd','9780747532743',None,'books',False),
            (2,'Cotton shirt','myer',None,None,'books',False),
        ])
        self.assertEqual(repair_misclassified_books(conn), 1)
        self.assertEqual(conn.execute('SELECT category FROM products WHERE id=1').fetchone()[0], 'books')
        self.assertEqual(conn.execute('SELECT category FROM products WHERE id=2').fetchone()[0], 'clothing')
        conn.close()

    def test_repair_does_not_override_existing_category_with_title_guess(self):
        rows=[dict(id=1,retailer='jbhifi',gtin=None,subcategory=None,title='Speaker stand',category='home',is_marketplace=False),
              dict(id=2,retailer='qbd',gtin='9780747532743',subcategory=None,title='Eagle Strike',category='other',is_marketplace=False)]
        self.assertEqual(list(repairs(rows)), [('books',2,'other')])

    def test_marketplace_department_does_not_override_product(self):
        self.assertEqual(classify_product('Folding sofa bed', 'kmart', None, 'Beauty', True), 'home')
        row=dict(id=1,retailer='kmart',gtin=None,subcategory='Tech & Gaming',title='Mattress',category='home',is_marketplace=True)
        self.assertEqual(list(repairs([row])), [])
        row.update(is_marketplace=False,subcategory='Toys',title='LEGO Cement Mixer',category='kitchen')
        self.assertEqual(list(repairs([row])), [('toys',1,'kitchen')])
