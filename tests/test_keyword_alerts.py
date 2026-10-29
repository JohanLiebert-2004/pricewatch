import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import db
import keyword_alerts as alerts


class KeywordAlertsTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(db.SCHEMA)
        alerts.sqlite_schema(self.conn)
        self.now = datetime.now(timezone.utc).isoformat()
        self.client = Mock()
        self.client.post.return_value.status_code = 200
        self.conn.execute('''INSERT INTO keyword_alerts(id,email,keywords,excluded_words,retailers,
            min_price,max_price,min_discount,token,confirm_token,created_at,confirmed_at)
            VALUES(1,'test@example.invalid','lego star','keyring','kmart,bigw',10,50,20,
            'manage-secret','email-only-secret',?,?)''', (self.now,self.now))

    def tearDown(self):
        self.conn.close()

    def product(self, title='Star Wars LEGO set', retailer='kmart', price=40, rrp=100, market=False, age=0):
        now = (datetime.now(timezone.utc)-timedelta(days=age)).isoformat()
        cur = self.conn.execute('''INSERT INTO products(retailer,sku,title,current_price,current_rrp,
            is_marketplace,price_updated_at) VALUES(?,?,?,?,?,?,?)''',
            (retailer,title,title,price,rrp,market,now))
        return cur.lastrowid

    def alert(self):
        return self.conn.execute('SELECT * FROM keyword_alerts WHERE id=1').fetchone()

    def run_worker(self):
        return alerts.process(self.conn,self.client,'test-key','sender@example.invalid')

    def test_keyword_order_stores_exclusions_prices_discount_and_marketplace(self):
        good=self.product()
        self.product(title='LEGO Star keyring')
        self.product(title='LEGO Star set',retailer='myer')
        self.product(title='LEGO Star marketplace',market=True)
        self.product(title='LEGO Star expensive',price=60)
        self.product(title='LEGO Star little discount',rrp=45)
        self.product(title='LEGO other set')
        self.product(title='LEGO Star old',age=8)
        self.assertEqual([r['id'] for r in alerts.matching_products(self.conn,self.alert())],[good])

    def test_send_deduplicates_unchanged_or_higher_prices_but_sends_lower(self):
        pid=self.product()
        self.assertEqual(self.run_worker(),(0,1))
        self.assertEqual(self.run_worker(),(0,0))
        self.conn.execute('UPDATE products SET current_price=45 WHERE id=?',(pid,))
        self.assertEqual(self.run_worker(),(0,0))
        self.conn.execute('UPDATE products SET current_price=30 WHERE id=?',(pid,))
        self.assertEqual(self.run_worker(),(0,1))
        self.assertEqual(self.client.post.call_count,2)
        calls=self.client.post.call_args_list
        self.assertNotEqual(calls[0].kwargs['headers']['Idempotency-Key'],calls[1].kwargs['headers']['Idempotency-Key'])

    def test_unconfirmed_gets_only_confirmation_and_management_cannot_confirm(self):
        self.product()
        self.conn.execute('UPDATE keyword_alerts SET confirmed_at=NULL')
        self.assertEqual(self.run_worker(),(1,0))
        payload=self.client.post.call_args.kwargs['json']
        self.assertIn('keyword_confirm=email-only-secret',payload['html'])
        self.assertNotIn('keyword_confirm=manage-secret',payload['html'])
        self.assertEqual(self.run_worker(),(0,0))

    def test_cancelled_or_expired_pending_gets_no_emails(self):
        self.product()
        self.conn.execute('UPDATE keyword_alerts SET cancelled_at=?',(self.now,))
        self.assertEqual(self.run_worker(),(0,0))
        self.conn.execute('UPDATE keyword_alerts SET cancelled_at=NULL,confirmed_at=NULL,created_at=?',
                          ((datetime.now(timezone.utc)-timedelta(days=8)).isoformat(),))
        self.assertEqual(self.run_worker(),(0,0))

    def test_provider_failure_does_not_consume_match_and_retry_key_is_stable(self):
        self.product()
        self.client.post.return_value.status_code=503
        with self.assertRaisesRegex(RuntimeError,'HTTP 503'):
            self.run_worker()
        first=self.client.post.call_args.kwargs['headers']['Idempotency-Key']
        self.assertEqual(self.conn.execute('SELECT count(*) FROM keyword_alert_deliveries').fetchone()[0],0)
        self.client.post.return_value.status_code=200
        self.assertEqual(self.run_worker(),(0,1))
        self.assertEqual(first,self.client.post.call_args.kwargs['headers']['Idempotency-Key'])

    def test_literal_wildcards_and_html_escaping(self):
        self.conn.execute("UPDATE keyword_alerts SET keywords='100% lego',excluded_words='',min_discount=0")
        self.product(title='100X LEGO')
        self.product(title='100% LEGO <script>')
        self.assertEqual(self.run_worker(),(0,1))
        body=self.client.post.call_args.kwargs['json']['html']
        self.assertIn('&lt;script&gt;',body)
        self.assertNotIn('<script>',body)

    def test_no_reference_price_allowed_only_at_zero_minimum_discount(self):
        self.product(rrp=None)
        self.assertEqual(alerts.matching_products(self.conn,self.alert()),[])
        self.conn.execute('UPDATE keyword_alerts SET min_discount=0')
        self.assertEqual(len(alerts.matching_products(self.conn,self.alert())),1)


if __name__=='__main__':
    unittest.main()
