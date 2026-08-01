"""Operational alerts when a retailer feed is empty, stale or blocked.

Run after every crawler matrix pass. Alert state is stored in ``kv`` so the
same fault sends one alert, then a single recovery notification once it
clears.

Telegram (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID, same owner chat as deal
alerts) is the primary channel - it's the one actually watched day to day.
The Resend email to ADMIN_ALERT_EMAIL is kept as a secondary/durable record
but should not be relied on alone: a 3.5-day-dead Myer scraper (22-26 July
2026) sent exactly one correctly-detected email here and nobody saw it
because that inbox isn't checked. Found and fixed the same session as the
underlying Myer bug - see AGENT_STATE.md.
"""
import html
import json
import os
from datetime import datetime, timezone

import httpx

import db

RESEND_ENDPOINT = "https://api.resend.com/emails"
TELEGRAM_ENDPOINT = "https://api.telegram.org/bot{token}/sendMessage"
ADMIN_EMAIL = os.environ.get("ADMIN_ALERT_EMAIL", "admin@dealwatch.com.au")
DEFAULT_MAX_AGE_HOURS = 36
MAX_AGE_HOURS = {"chemistwarehouse": 24}
# A batch this size or larger storing exactly 0 products is a much stronger,
# faster signal than waiting out the last_seen age window above - it means
# the scraper ran to completion without a single successful parse (a site
# markup change, not a block - Blocked already short-circuits separately).
# 20 is comfortably above any retailer's legitimate near-empty-queue tail.
MIN_ATTEMPTED_FOR_EMPTY_BATCH_ALERT = 20
RETAILERS = ("bigw", "booktopia", "chemistwarehouse", "goodguys", "ikea", "jbhifi", "kmart",
             "myer", "qbd", "officeworks", "sephora", "supercheap", "target")
LABELS = {"bigw": "Big W", "booktopia": "Booktopia", "chemistwarehouse": "Chemist Warehouse",
          "goodguys": "The Good Guys", "ikea": "IKEA", "jbhifi": "JB Hi-Fi", "kmart": "Kmart",
          "myer": "Myer", "officeworks": "Officeworks", "sephora": "Sephora",
          "supercheap": "Supercheap Auto", "target": "Target", "qbd": "QBD Books"}
# Big W, Chemist Warehouse and JB Hi-Fi run their real crawl lane on the
# owner's always-on Ubuntu laptop (see local_*_sweep.py), not in CI, because
# their storefronts reject data-centre IPs; crawl.yml/enrich.yml read these
# same kv rows to decide whether to skip their own weaker fallback.
# scripts/watchdog.py already watches these rows too, but it *runs on that
# same laptop* - a full laptop outage silently takes its own monitor down
# with it. Confirmed live 2026-08-01: the laptop went offline, Big W quietly
# ran on a byte-capped proxy trickle for over a day instead of its real
# sweep, and nothing alerted - the owner only found out by asking. These
# checks run in CI instead (this script), which stays up regardless of
# laptop state, so a laptop outage is never silent again. Thresholds match
# watchdog.py's (timer cadence + the CI-gate window + slack).
HEARTBEATS = {
    "bigw_local_heartbeat": (30, "Big W home-IP sweep"),
    "chemistwarehouse_local_heartbeat": (8, "Chemist Warehouse home-IP sweep"),
    "jbhifi_local_heartbeat": (2.5, "JB Hi-Fi home-IP sweep"),
}
# Webshare's 1GB/month plan is Big W's only path to Akamai-protected pages
# (see scrapers/bigw.py PROXY_CYCLE_BYTE_CAP) - once it's spent, Big W's CI
# fallback goes from "degraded trickle" to "nothing at all" until the cycle
# renews on the 10th. Warn well before that self-throttle bites, not after.
PROXY_BUDGET_WARN_FRACTION = 0.85


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


def _kv_get_raw(conn, key):
    # Heartbeat rows store a plain ISO timestamp string, not JSON - _kv_get
    # would return None for them (json.loads fails on a bare timestamp).
    row = conn.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
    return row["v"] if row else None


def _send_telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("health alerts: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID unset, skipping")
        return False
    response = httpx.post(
        TELEGRAM_ENDPOINT.format(token=token),
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=15)
    if response.status_code != 200 or not response.json().get("ok"):
        print(f"health telegram send failed ({response.status_code}): {response.text[:200]}")
        return False
    return True


def _send_email(subject, body):
    api_key, from_addr = os.environ.get("RESEND_API_KEY"), os.environ.get("RESEND_FROM")
    if not api_key or not from_addr:
        print("health email disabled: RESEND_API_KEY or RESEND_FROM is unset")
        return False
    response = httpx.post(
        RESEND_ENDPOINT, headers={"Authorization": f"Bearer {api_key}"},
        json={"from": from_addr, "to": [ADMIN_EMAIL], "subject": subject, "html": body},
        timeout=20)
    if response.status_code not in (200, 201):
        print(f"health email failed ({response.status_code}): {response.text[:200]}")
        return False
    return True


def _send(subject, body, plain):
    # Telegram is the channel actually watched; email is a secondary record.
    # Either succeeding counts as "sent" - don't let a Resend hiccup mask a
    # Telegram alert that got through, or vice versa.
    tg = _send_telegram(f"<b>{html.escape(subject)}</b>\n{plain}")
    em = _send_email(subject, body)
    return tg or em


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
    attempted, stored = health.get("attempted") or 0, health.get("stored") or 0
    if attempted >= MIN_ATTEMPTED_FOR_EMPTY_BATCH_ALERT and stored == 0:
        # Not Blocked (that's handled above) and not a small tail batch -
        # the scraper ran to completion without a single successful parse.
        # Waiting for the last_seen age check below to also catch this can
        # take up to DEFAULT_MAX_AGE_HOURS; this fires on the very next run.
        return ("empty_batch",
                f"the last crawl attempted {attempted} pages and stored 0 products - "
                "the scraper likely needs a code fix (e.g. the site's page markup "
                "changed), this isn't a routine bot block")
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


def _heartbeat_problem(conn, key, max_age_hours):
    raw = _kv_get_raw(conn, key)
    if raw is None:
        return "has never reported in"
    try:
        age_hours = (_now() - _as_utc(raw)).total_seconds() / 3600
    except (TypeError, ValueError):
        return "reported an invalid timestamp"
    if age_hours > max_age_hours:
        return f"last checked in {age_hours:.1f} hours ago (limit {max_age_hours}h)"
    return None


def _proxy_budget_problem(conn):
    from scrapers.bigw import PROXY_CYCLE_BYTE_CAP, proxy_cycle
    state = _kv_get(conn, "bigw_cat_state") or {}
    if state.get("_proxy_cycle") != proxy_cycle():
        return None   # stale/absent state for the current cycle == nothing spent yet
    spent = state.get("_proxy_bytes", 0)
    fraction = spent / PROXY_CYCLE_BYTE_CAP
    if fraction >= PROXY_BUDGET_WARN_FRACTION:
        return (f"{spent // 1024 // 1024}MB of the "
                f"{PROXY_CYCLE_BYTE_CAP // 1024 // 1024}MB Webshare cycle budget spent "
                f"({fraction:.0%}) - Big W's CI fallback will go dark once it's exhausted")
    return None


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
                subject = f"Dealwatch crawler alert: {label}"
                body = (f"<p><b>{html.escape(label)} needs attention.</b></p>"
                        f"<p>{detail}</p><p>Dealwatch is withholding neither data nor alerts "
                        "automatically; inspect the crawler log and retailer feed.</p>")
                plain = f"\U0001F6A8 {label} needs attention.\n{detail}"
                if _send(subject, body, plain):
                    _kv_set(conn, key, {"fingerprint": fingerprint, "at": _now().isoformat()})
                    sent += 1
            continue
        if prior:
            subject = f"Dealwatch crawler recovered: {label}"
            body = (f"<p><b>{html.escape(label)} has recovered.</b></p>"
                    "<p>Dealwatch has fresh listings again.</p>")
            plain = f"✅ {label} has recovered - fresh listings again."
            if _send(subject, body, plain):
                _kv_delete(conn, key)
                sent += 1

    for hb_key, (max_age_hours, label) in HEARTBEATS.items():
        detail = _heartbeat_problem(conn, hb_key, max_age_hours)
        alert_key = f"health_alert:{hb_key}"
        prior = _kv_get(conn, alert_key)
        if detail:
            if not prior or prior.get("fingerprint") != "stale":
                subject = f"Dealwatch crawler alert: {label}"
                body = (f"<p><b>{html.escape(label)} {detail}.</b></p>"
                        "<p>CI is likely running a degraded fallback (proxy-limited or "
                        "rate-limited) instead of the real sweep - check the laptop.</p>")
                plain = (f"\U0001F6A8 {label} {detail}.\n"
                         "CI is likely running a degraded fallback instead - check the laptop.")
                if _send(subject, body, plain):
                    _kv_set(conn, alert_key, {"fingerprint": "stale", "at": _now().isoformat()})
                    sent += 1
            continue
        if prior:
            subject = f"Dealwatch crawler recovered: {label}"
            body = f"<p><b>{html.escape(label)} is checking in again.</b></p>"
            plain = f"✅ {label} is checking in again."
            if _send(subject, body, plain):
                _kv_delete(conn, alert_key)
                sent += 1

    proxy_detail = _proxy_budget_problem(conn)
    proxy_key = "health_alert:bigw_proxy_budget"
    prior = _kv_get(conn, proxy_key)
    if proxy_detail:
        if not prior or prior.get("fingerprint") != "budget_high":
            subject = "Dealwatch crawler alert: Big W proxy budget"
            body = f"<p><b>Big W proxy budget running low.</b></p><p>{proxy_detail}.</p>"
            plain = f"\U0001F6A8 Big W proxy budget running low.\n{proxy_detail}."
            if _send(subject, body, plain):
                _kv_set(conn, proxy_key, {"fingerprint": "budget_high", "at": _now().isoformat()})
                sent += 1
    elif prior:
        # No recovery notice here - the cap just resets silently on the 10th,
        # nothing "recovered" that needs announcing the way a fixed scraper does.
        _kv_delete(conn, proxy_key)

    print(f"health alerts: {sent} notification(s) sent")


if __name__ == "__main__":
    run()