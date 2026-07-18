"""Resend double-opt-in price-watch alerts.

A visitor must open a confirmation link before a watch can receive a price-drop
email. The confirmation is queued by the hourly detect job; an unconfirmed
watch never receives a price alert.
"""
import html
import os
import sys
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

RESEND_ENDPOINT = "https://api.resend.com/emails"
SITE_URL = os.environ.get("SITE_URL", "https://dealwatch.com.au").rstrip("/")

RETAILER_LABEL = {"kmart": "Kmart", "bigw": "Big W", "target": "Target",
                  "officeworks": "Officeworks", "jbhifi": "JB Hi-Fi",
                  "goodguys": "The Good Guys", "myer": "Myer",
                  "supercheap": "Supercheap Auto", "sephora": "Sephora",
                  "chemistwarehouse": "Chemist Warehouse", "booktopia": "Booktopia", "qbd": "QBD Books", "ikea": "IKEA"}


def _config():
    return os.environ.get("RESEND_API_KEY"), os.environ.get("RESEND_FROM")


def _product_url(retailer: str, sku: str, token: str | None = None) -> str:
    url = f"{SITE_URL}/p/{quote(retailer, safe='')}/{quote(str(sku), safe='')}"
    return f"{url}?confirm={quote(token, safe='')}" if token else url


def send_watch_confirmations(conn) -> int:
    """Send confirmation links for unconfirmed watches; retry only if unsent."""
    api_key, from_addr = _config()
    if not api_key or not from_addr:
        return 0
    rows = conn.execute(
        """SELECT w.id, w.email, w.target_price, w.token, p.title, p.retailer, p.sku
           FROM watches w JOIN products p ON p.id = w.product_id
           WHERE w.confirmed_at IS NULL AND w.confirmation_sent_at IS NULL
             AND w.cancelled_at IS NULL AND w.fired_at IS NULL
           ORDER BY w.created_at LIMIT 20""").fetchall()
    if not rows:
        return 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    sent = 0
    with httpx.Client(timeout=15) as client:
        for r in rows:
            title = html.escape(r["title"] or "this product")
            store = html.escape(RETAILER_LABEL.get(r["retailer"], r["retailer"]))
            confirm_url = _product_url(r["retailer"], r["sku"], r["token"])
            body = (
                f"<p>Confirm your Dealwatch price alert for <b>{title}</b> at {store}. "
                f"We'll email you when it reaches <b>${float(r['target_price']):.2f}</b> or less.</p>"
                f"<p><a href='{html.escape(confirm_url, quote=True)}'>Confirm this alert</a></p>"
                "<p style='color:#888;font-size:12px'>If you didn't request this, ignore this email. "
                "No alert will be activated.</p>")
            response = client.post(
                RESEND_ENDPOINT, headers={"Authorization": f"Bearer {api_key}"},
                json={"from": from_addr, "to": [r["email"]],
                      "subject": "Confirm your Dealwatch price alert", "html": body})
            if response.status_code in (200, 201):
                conn.execute("UPDATE watches SET confirmation_sent_at=? WHERE id=?",
                             (now, r["id"]))
                conn.commit()
                sent += 1
            else:
                print(f"  ! resend confirmation failed ({response.status_code}): "
                      f"{response.text[:200]}")
                break
    return sent


def send_watch_alerts(conn) -> int:
    """Email confirmed, not-yet-fired watches whose target price has been hit."""
    api_key, from_addr = _config()
    if not api_key or not from_addr:
        return 0
    confirmations = send_watch_confirmations(conn)
    if confirmations:
        print(f"resend: {confirmations} confirmation email(s) sent")
    rows = conn.execute(
        """SELECT w.id, w.email, w.target_price, w.token,
                  p.title, p.retailer, p.sku, p.url, p.current_price
           FROM watches w JOIN products p ON p.id = w.product_id
           WHERE w.confirmed_at IS NOT NULL AND w.fired_at IS NULL
             AND w.cancelled_at IS NULL AND p.current_price IS NOT NULL
             AND p.current_price <= w.target_price
           ORDER BY w.created_at LIMIT 20""").fetchall()
    if not rows:
        return 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    sent = 0
    with httpx.Client(timeout=15) as client:
        for r in rows:
            store = RETAILER_LABEL.get(r["retailer"], r["retailer"])
            title = r["title"] or "your watched item"
            cancel_url = f"{_product_url(r['retailer'], r['sku'])}?cancel={quote(r['token'], safe='')}"
            html_body = (
                f"<p><b>{html.escape(title)}</b> at {html.escape(store)} just dropped to "
                f"<b>${float(r['current_price']):.2f}</b> — your target was "
                f"${float(r['target_price']):.2f}.</p>"
                f"<p><a href='{html.escape(r['url'] or '', quote=True)}'>View at {html.escape(store)}</a></p>"
                f"<p style='color:#888;font-size:12px'><a href='{html.escape(cancel_url, quote=True)}'>"
                "Unsubscribe from this watch</a></p>")
            response = client.post(
                RESEND_ENDPOINT, headers={"Authorization": f"Bearer {api_key}"},
                json={"from": from_addr, "to": [r["email"]],
                      "subject": f"Price drop: {title[:60]}", "html": html_body})
            if response.status_code in (200, 201):
                conn.execute("UPDATE watches SET fired_at=? WHERE id=?", (now, r["id"]))
                conn.commit()
                sent += 1
            else:
                print(f"  ! resend send failed ({response.status_code}): {response.text[:200]}")
                break
    return sent


def test_message(to_addr: str):
    """Send a test email so the user can confirm the pipe works."""
    api_key, from_addr = _config()
    if not api_key or not from_addr:
        sys.exit("set RESEND_API_KEY and RESEND_FROM first")
    response = httpx.post(
        RESEND_ENDPOINT, headers={"Authorization": f"Bearer {api_key}"},
        json={"from": from_addr, "to": [to_addr],
              "subject": "Dealwatch watch alerts are live",
              "html": "<p>Dealwatch email alerts are wired up correctly.</p>"}, timeout=15)
    print(response.status_code, response.text[:200])


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "test":
        test_message(sys.argv[2])
    else:
        sys.exit("usage: python watch_alerts.py test <email>")