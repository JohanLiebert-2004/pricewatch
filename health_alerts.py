"""Email operational alerts when a retailer feed is empty, stale or blocked.

Run after every crawler matrix pass. Alert state is stored in ``kv`` so the
same fault sends one alert, then a single recovery email once it clears.
"""
import html
import json
import os
from datetime import datetime, timezone

import httpx

import db

RESEND_ENDPOINT = "https://api.resend.com/emails"
ADMIN_EMAIL = os.environ.get("ADMIN_ALERT_EMAIL", "admin@dealwatch.com.au")
DEFAULT_MAX_AGE_HOURS = 36
MAX_AGE_HOURS = {"chemistwarehouse": 24}
RETAILERS = ("bigw", "booktopia", "chemistwarehouse", "goodguys", "ikea", "jbhifi", "kmart",
             "myer", "qbd", "officeworks", "sephora", "supercheap", "target")
LABELS = {"bigw": "Big W", "booktopia": "Booktopia", "chemistwarehouse": "Chemist Warehouse",
          "goodguys": "The Good Guys", "ikea": "IKEA", "jbhifi": "JB Hi-Fi", "kmart": "Kmart",
          "myer": "Myer", "officeworks": "Officeworks", "sephora": "Sephora",
          "supercheap": "Supercheap Auto", "target": "Target", "qbd": "QBD Books"}


def _now():
    return datetime.now(timezone.utc)


def _as_utc(value):
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _kv_get(conn, key):
    row = conn.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["v"])
    except (TypeError, ValueError):
        return None


def _kv_set(conn, key, value):
    encoded = json.dumps(value)
    conn.execute("INSERT OR IGNORE INTO kv (k, v) VALUES (?, ?)", (key, encoded))
    conn.execute("UPDATE kv SET v=? WHERE k=?", (encoded, key))
    conn.commit()


def _kv_delete(conn, key):
    conn.execute("DELETE FROM kv WHERE k=?", (key,))
    conn.commit()


def _send(subject, body):
    api_key, from_addr = os.environ.get("RESEND_API_KEY"), os.environ.get("RESEND_FROM")
    if not api_key or not from_addr:
        print("health alerts disabled: RESEND_API_KEY or RESEND_FROM is unset")
        return False
    response = httpx.post(
        RESEND_ENDPOINT, headers={"Authorization": f"Bearer {api_key}"},
        json={"from": from_addr, "to": [ADMIN_EMAIL], "subject": subject, "html": body},
        timeout=20)
    if response.status_code not in (200, 201):
        print(f"health email failed ({response.status_code}): {response.text[:200]}")
        return False
    return True


def _retailer_rows(conn):
    rows = conn.execute(
        """SELECT retailer, count(*) filter (where current_price is not null) AS listings,
                  max(last_seen) AS last_seen
           FROM products GROUP BY retailer""").fetchall()
    return {r["retailer"]: r for r in rows}


def _problem(conn, retailer, row):
    health = _kv_get(conn, f"scraper_health:{retailer}") or {}
    if health.get("status") == "blocked":
        return "blocked", html.escape(health.get("detail") or "retailer bot protection blocked the crawler")
    if not row or not int(row["listings"] or 0):
        return "empty", "no products with a current price are available"
    try:
        age_hours = (_now() - _as_utc(row["last_seen"])).total_seconds() / 3600
    except (TypeError, ValueError):
        return "stale", "the most recent successful listing time is invalid"
    limit = MAX_AGE_HOURS.get(retailer, DEFAULT_MAX_AGE_HOURS)
    if age_hours > limit:
        return "stale", f"latest successful listing is {age_hours:.1f} hours old (limit {limit}h)"
    return None, ""


def run():
    conn = db.connect()
    rows = _retailer_rows(conn)
    sent = 0
    for retailer in RETAILERS:
        code, detail = _problem(conn, retailer, rows.get(retailer))
        key = f"health_alert:{retailer}"
        prior = _kv_get(conn, key)
        label = LABELS[retailer]
        if code:
            fingerprint = code
            if not prior or prior.get("fingerprint") != fingerprint:
                body = (f"<p><b>{html.escape(label)} needs attention.</b></p>"
                        f"<p>{detail}</p><p>Dealwatch is withholding neither data nor alerts "
                        "automatically; inspect the crawler log and retailer feed.</p>")
                if _send(f"Dealwatch crawler alert: {label}", body):
                    _kv_set(conn, key, {"fingerprint": fingerprint, "at": _now().isoformat()})
                    sent += 1
            continue
        if prior:
            body = (f"<p><b>{html.escape(label)} has recovered.</b></p>"
                    "<p>Dealwatch has fresh listings again.</p>")
            if _send(f"Dealwatch crawler recovered: {label}", body):
                _kv_delete(conn, key)
                sent += 1
    print(f"health alerts: {sent} email(s) sent")


if __name__ == "__main__":
    run()