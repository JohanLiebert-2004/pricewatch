"""Resend price-watch alerts.

Emails a watcher once when a tracked product's price drops to/below the
target price they set on product.html. Runs at the end of every
`run.py detect`; without RESEND_API_KEY / RESEND_FROM it is a no-op.

Setup (once):
  1. Buy/verify a sending domain in Resend (needs the site's own domain —
     the shared onboarding.resend.dev sender only delivers to the account's
     own address, not to real visitors).
  2. Create an API key, set RESEND_API_KEY and RESEND_FROM (e.g.
     alerts@yourdomain.com).

Each watch is stamped (watches.fired_at) so it only ever fires once — this
is also the only outbound email a watch ever generates, so there's no
separate confirmation/double opt-in step; the alert email itself carries an
unsubscribe link back to product.html.
"""
import os
import sys
from datetime import datetime, timezone

import httpx

RESEND_ENDPOINT = "https://api.resend.com/emails"
SITE_URL = os.environ.get("SITE_URL", "https://web-pi-blush-48.vercel.app")

RETAILER_LABEL = {"kmart": "Kmart", "bigw": "Big W", "target": "Target",
                  "officeworks": "Officeworks", "jbhifi": "JB Hi-Fi",
                  "goodguys": "The Good Guys"}


def _config():
    return os.environ.get("RESEND_API_KEY"), os.environ.get("RESEND_FROM")


def send_watch_alerts(conn) -> int:
    """Email not-yet-fired watches whose target price has been hit."""
    api_key, from_addr = _config()
    if not api_key or not from_addr:
        return 0
    rows = conn.execute(
        """SELECT w.id, w.email, w.target_price, w.token,
                  p.title, p.retailer, p.sku, p.url, p.current_price
           FROM watches w JOIN products p ON p.id = w.product_id
           WHERE w.fired_at IS NULL AND w.cancelled_at IS NULL
             AND p.current_price IS NOT NULL
             AND p.current_price <= w.target_price
           ORDER BY w.created_at LIMIT 20""").fetchall()
    if not rows:
        return 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    sent = 0
    with httpx.Client(timeout=15) as client:
        for r in rows:
            store = RETAILER_LABEL.get(r["retailer"], r["retailer"])
            cancel_url = (f"{SITE_URL}/product.html?retailer={r['retailer']}"
                          f"&sku={r['sku']}&cancel={r['token']}")
            html_body = (
                f"<p><b>{r['title'] or ''}</b> at {store} just dropped to "
                f"<b>${r['current_price']:.2f}</b> — your target was "
                f"${r['target_price']:.2f}.</p>"
                f"<p><a href='{r['url']}'>View at {store}</a></p>"
                f"<p style='color:#888;font-size:12px'>"
                f"<a href='{cancel_url}'>Unsubscribe from this watch</a></p>")
            resp = client.post(
                RESEND_ENDPOINT,
                headers={"Authorization": f"Bearer {api_key}"},
                json={"from": from_addr, "to": [r["email"]],
                      "subject": f"Price drop: {(r['title'] or 'your watched item')[:60]}",
                      "html": html_body})
            if resp.status_code in (200, 201):
                conn.execute("UPDATE watches SET fired_at=? WHERE id=?",
                             (now, r["id"]))
                conn.commit()
                sent += 1
            else:
                print(f"  ! resend send failed ({resp.status_code}): "
                      f"{resp.text[:200]}")
                break   # bad key/domain — don't hammer the API
    return sent


def test_message(to_addr: str):
    """Send a test email so the user can confirm the pipe works."""
    api_key, from_addr = _config()
    if not api_key or not from_addr:
        sys.exit("set RESEND_API_KEY and RESEND_FROM first")
    r = httpx.post(
        RESEND_ENDPOINT,
        headers={"Authorization": f"Bearer {api_key}"},
        json={"from": from_addr, "to": [to_addr],
              "subject": "Underpriced watch alerts are live",
              "html": "<p>Price-watch emails are wired up correctly.</p>"},
        timeout=15)
    print(r.status_code, r.text[:200])


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "test":
        test_message(sys.argv[2])
    else:
        sys.exit("usage: python watch_alerts.py test <email>")
