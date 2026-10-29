"""Confirmed keyword watches. Run hourly; email only new matches or lower prices."""
import hashlib
import html
import json
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx
import db
from watch_alerts import RESEND_ENDPOINT, SITE_URL, RETAILER_LABEL


def sqlite_schema(conn):
    """Local test/development equivalent of scripts/shopping_features.sql."""
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS keyword_alerts (
      id INTEGER PRIMARY KEY, email TEXT NOT NULL, keywords TEXT NOT NULL,
      excluded_words TEXT NOT NULL DEFAULT '', retailers TEXT NOT NULL DEFAULT '',
      min_price REAL NOT NULL DEFAULT 0, max_price REAL NOT NULL, min_discount INTEGER NOT NULL DEFAULT 0,
      token TEXT NOT NULL UNIQUE, confirm_token TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
      confirmed_at TEXT, confirmation_sent_at TEXT, cancelled_at TEXT, checked_at TEXT);
    CREATE TABLE IF NOT EXISTS keyword_alert_deliveries (
      alert_id INTEGER NOT NULL, product_id INTEGER NOT NULL, price REAL NOT NULL,
      sent_at TEXT NOT NULL, PRIMARY KEY(alert_id,product_id));
    ''')


def _pattern(word):
    return '%' + word.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'


def matching_products(conn, alert):
    clauses = ['p.current_price >= ?', 'p.current_price <= ?',
               'COALESCE(p.is_marketplace, false) = false',
               '(d.price IS NULL OR p.current_price < d.price)']
    values = [alert['min_price'], alert['max_price']]
    for word in alert['keywords'].lower().split():
        clauses.append("lower(p.title) LIKE ? ESCAPE '\\'")
        values.append(_pattern(word))
    for word in alert['excluded_words'].lower().split():
        clauses.append("lower(p.title) NOT LIKE ? ESCAPE '\\'")
        values.append(_pattern(word))
    stores = alert['retailers'].split(',') if alert['retailers'] else []
    if stores:
        clauses.append('p.retailer IN (' + ','.join('?' for _ in stores) + ')')
        values.extend(stores)
    if alert['min_discount']:
        clauses.append('p.current_rrp > 0 AND p.current_price * 100 <= p.current_rrp * (100 - ?)')
        values.append(alert['min_discount'])
    # Bounded to observations made after creation (or during the last week for
    # a newly created alert). Historical, untouched records do not trigger mail.
    recent = (datetime.now(timezone.utc)-timedelta(days=7)).isoformat()
    clauses.append('p.price_updated_at >= ?')
    values.append(recent)
    return conn.execute('''SELECT p.id,p.title,p.retailer,p.sku,p.current_price
        FROM products p LEFT JOIN keyword_alert_deliveries d ON d.product_id=p.id AND d.alert_id=?
        WHERE ''' + ' AND '.join(clauses) + ' ORDER BY p.current_price,p.id LIMIT 10',
        (alert['id'], *values)).fetchall()


def _send(client, api_key, from_addr, email, subject, body, key):
    response = client.post(RESEND_ENDPOINT,
        headers={'Authorization': f'Bearer {api_key}', 'Idempotency-Key': key},
        json={'from': from_addr, 'to': [email], 'subject': subject, 'html': body})
    if response.status_code not in (200, 201):
        # Provider responses can contain recipient details; log only status.
        raise RuntimeError(f'Keyword email provider returned HTTP {response.status_code}')


def process(conn, client, api_key, from_addr):
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    cutoff = (datetime.now(timezone.utc)-timedelta(days=7)).isoformat()
    pending = conn.execute('''SELECT * FROM keyword_alerts WHERE confirmed_at IS NULL
        AND confirmation_sent_at IS NULL AND cancelled_at IS NULL AND created_at>=?
        ORDER BY created_at LIMIT 20''', (cutoff,)).fetchall()
    confirmed = 0
    for alert in pending:
        url = f"{SITE_URL}/alerts.html?keyword_confirm={quote(alert['confirm_token'], safe='')}"
        cancel = f"{SITE_URL}/alerts.html?keyword_cancel={quote(alert['token'], safe='')}"
        body = (f"<p>Confirm your Dealwatch keyword alert for <b>{html.escape(alert['keywords'])}</b>, "
                f"${float(alert['min_price']):.2f} to ${float(alert['max_price']):.2f}.</p>"
                f"<p><a href='{html.escape(url, quote=True)}'>Confirm keyword alert</a></p>"
                f"<p>If you did not request this, ignore it. No deal emails will be sent without confirmation.</p>"
                f"<p><a href='{html.escape(cancel, quote=True)}'>Cancel this request</a></p>")
        _send(client, api_key, from_addr, alert['email'], 'Confirm your Dealwatch keyword alert',
              body, f"keyword-confirm-{alert['id']}")
        conn.execute('UPDATE keyword_alerts SET confirmation_sent_at=? WHERE id=?', (now,alert['id']))
        conn.commit()
        confirmed += 1
    alerts = conn.execute('''SELECT * FROM keyword_alerts WHERE confirmed_at IS NOT NULL
        AND cancelled_at IS NULL ORDER BY COALESCE(checked_at,created_at),id LIMIT 100''').fetchall()
    sent = 0
    for alert in alerts:
        matches = matching_products(conn, alert)
        if matches:
            cancel = f"{SITE_URL}/alerts.html?keyword_cancel={quote(alert['token'], safe='')}"
            items = []
            for product in matches:
                url = f"{SITE_URL}/p/{quote(product['retailer'],safe='')}/{quote(product['sku'],safe='')}"
                store = RETAILER_LABEL.get(product['retailer'], product['retailer'])
                items.append(f"<li><a href='{html.escape(url, quote=True)}'>{html.escape(product['title'])}</a>"
                             f" — ${float(product['current_price']):.2f} at {html.escape(store)}</li>")
            body = (f"<p>New matches or lower prices for <b>{html.escape(alert['keywords'])}</b>:</p>"
                    f"<ul>{''.join(items)}</ul><p>Check price, stock and delivery at the retailer.</p>"
                    f"<p><a href='{html.escape(cancel, quote=True)}'>Unsubscribe from this keyword alert</a></p>")
            fingerprint = json.dumps([(p['id'],str(p['current_price'])) for p in matches])
            key = hashlib.sha256(f"{alert['id']}:{fingerprint}".encode()).hexdigest()
            # Recheck cancellation immediately before sending.
            state = conn.execute('SELECT cancelled_at FROM keyword_alerts WHERE id=?',(alert['id'],)).fetchone()
            if state['cancelled_at'] is not None:
                continue
            _send(client,api_key,from_addr,alert['email'],f"Dealwatch matches: {alert['keywords'][:60]}",body,'keyword-'+key)
            for p in matches:
                conn.execute('''INSERT INTO keyword_alert_deliveries(alert_id,product_id,price,sent_at)
                    VALUES(?,?,?,?) ON CONFLICT(alert_id,product_id) DO UPDATE
                    SET price=excluded.price,sent_at=excluded.sent_at''',
                    (alert['id'],p['id'],p['current_price'],now))
            sent += 1
        conn.execute('UPDATE keyword_alerts SET checked_at=? WHERE id=?',(now,alert['id']))
        conn.commit()
    return confirmed, sent


def send_keyword_alerts(conn):
    api_key, from_addr = os.getenv('RESEND_API_KEY'),os.getenv('RESEND_FROM')
    if not api_key or not from_addr:
        return 0, 0
    locked = False
    try:
        if db.DATABASE_URL:
            locked = conn.execute('SELECT pg_try_advisory_lock(64029102) AS locked').fetchone()['locked']
            conn.commit()
            if not locked:
                return 0, 0
        else:
            sqlite_schema(conn)
        with httpx.Client(timeout=20) as client:
            confirmations, sent = process(conn,client,api_key,from_addr)
        print(f'Keyword alerts: {confirmations} confirmations, {sent} match emails')
        return confirmations, sent
    finally:
        conn.rollback()
        if locked:
            conn.execute('SELECT pg_advisory_unlock(64029102)')
            conn.commit()


def main():
    conn = db.connect()
    try:
        send_keyword_alerts(conn)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
