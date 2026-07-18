"""Telegram deal alerts.

Pings a Telegram chat for every new deal the anomaly engine records - any
row landing in `deals` (anomaly.py already gates on its own thresholds:
50%+ off RRP/history/cross-retailer and a $40+ reference price, so this
module doesn't re-filter). ALERT_MIN_SCORE/ALERT_MIN_REFERENCE opened up
from 80%/$100 on 2026-07-08 - that bar was stricter than any real deal
seen so far, so alerts were silently never firing; raise them again here
if the flood of alerts at every-deal volume turns out to be too noisy.

Setup (once):
  1. In Telegram, message @BotFather -> /newbot -> copy the bot token.
  2. Open your new bot and press Start (so it is allowed to message you).
  3. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (find the chat id with
     `python alerts.py whoami` after step 2).

Runs at the end of every `run.py detect`; without the env vars it is a no-op.
Each deal is stamped (deals.alerted_at) so it only ever fires once.
"""
import html
import os
import re
import sys
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from anomaly import BIG_DROP
from scrapers import REGISTRY
from scrapers.base import NotFound, verify_price
SITE_URL = os.environ.get("SITE_URL", "https://web-pi-blush-48.vercel.app").rstrip("/")


def _history_url(retailer: str, sku: object) -> str:
    """Stable product page for alert links; retailer listings can disappear."""
    return f"{SITE_URL}/p/{quote(retailer, safe='')}/{quote(str(sku), safe='')}"


def _confirm_live_price(retailer: str, url: str, claimed_price: float) -> bool:
    """Confirm an alert product still has a real storefront page and price.

    This is deliberately strict for the special RAM watch: a notification is
    useful only when a shopper can actually open and buy the item. A transient
    verification problem suppresses that one alert rather than risking a stale
    catalogue-feed price.
    """
    scraper_cls = REGISTRY.get(retailer)
    if not scraper_cls:
        return False
    try:
        scraper = scraper_cls()
        record = scraper.parse_product(url, scraper.get(url))
    except NotFound:
        return False
    except Exception:
        return False
    if record is None or record.price is None:
        return False
    return abs(float(record.price) - claimed_price) <= max(0.5, claimed_price * VERIFY_TOLERANCE)
RAM_RX = re.compile(r"\b(ram|ddr3|ddr4|ddr5|dimm|so-?dimm)\b", re.I)


ALERT_MIN_SCORE = 0.0         # any deal the anomaly engine records
ALERT_MIN_REFERENCE = 0.0     # anomaly.py already gates reference >= $40
ALERT_EXCLUDE_RETAILERS = {"supercheap", "sephora"}  # user opted out of
                                          # supercheap 2026-07-09 and sephora
                                          # 2026-07-10 - still shown on-site,
                                          # just no Telegram ping
VERIFY_TOLERANCE = 0.05       # live-checked price may drift this much and
                              # still count as confirming the alerted price

RETAILER_LABEL = {"kmart": "Kmart", "bigw": "Big W", "target": "Target",
                  "officeworks": "Officeworks", "jbhifi": "JB Hi-Fi",
                  "goodguys": "The Good Guys", "myer": "Myer",
                  "supercheap": "Supercheap Auto",
                  "sephora": "Sephora", "chemistwarehouse": "Chemist Warehouse", "booktopia": "Booktopia", "qbd": "QBD Books", "ikea": "IKEA"}


def _verify_live(retailer: str, url: str, claimed_price: float) -> bool:
    """Re-fetch the actual product page for an error-tier deal before
    alerting on it.

    Some retailers' bulk listing feeds (e.g. Kmart's Constructor.io search
    index) can go stale for individual SKUs for days at a time while the
    live storefront has already moved on - which otherwise fires a
    confident-looking "80%+ off" alert for a price nobody can actually get.
    Returns False only when the live page clearly disagrees; any fetch
    failure (blocked, no scraper, page unreadable) fails open so a real
    alert is never swallowed by a network hiccup.
    """
    scraper_cls = REGISTRY.get(retailer)
    if not scraper_cls:
        return True
    live = verify_price(scraper_cls(), url, claimed_price)
    if live is None:
        print(f"  ? couldn't verify {retailer} price live; alerting anyway")
        return True
    return abs(live - claimed_price) <= max(0.5, claimed_price * VERIFY_TOLERANCE)


def _config():
    return (os.environ.get("TELEGRAM_BOT_TOKEN"),
            os.environ.get("TELEGRAM_CHAT_ID"))


def _retailer_subs(conn) -> dict:
    """retailer -> [chat_ids] subscribed to that store's deals via the bot."""
    try:
        rows = conn.execute(
            "SELECT chat_id, retailer FROM telegram_subs WHERE sku IS NULL"
        ).fetchall()
    except Exception:
        conn.rollback()   # table not present (fresh local SQLite dev DB)
        return {}
    out = {}
    for r in rows:
        out.setdefault(r["retailer"], []).append(r["chat_id"])
    return out


def _item_subs(conn) -> dict:
    """(retailer, sku) -> [chat_ids] watching that specific product."""
    try:
        rows = conn.execute(
            "SELECT chat_id, retailer, sku FROM telegram_subs "
            "WHERE sku IS NOT NULL").fetchall()
    except Exception:
        conn.rollback()
        return {}
    out = {}
    for r in rows:
        out.setdefault((r["retailer"], r["sku"]), []).append(r["chat_id"])
    return out


def send_alerts(conn) -> int:
    """Alert on not-yet-alerted qualifying deals. Returns alerts sent.

    Two audiences per deal: the owner's personal chat (TELEGRAM_CHAT_ID,
    skipping ALERT_EXCLUDE_RETAILERS), and any bot subscribers watching
    that retailer (services/telegram_bot.py writes telegram_subs) - the
    owner's retailer opt-outs deliberately do NOT apply to subscribers,
    who asked for that store explicitly.
    """
    token, chat_id = _config()
    if not token:
        return 0
    subs = _retailer_subs(conn)
    rows = conn.execute(
        """SELECT d.id, d.price, d.reference_price, d.score,
                  p.title, p.retailer, p.sku, p.url
           FROM deals d JOIN products p ON p.id = d.product_id
           WHERE d.alerted_at IS NULL AND d.status <> 'expired'
             AND d.score >= ? AND COALESCE(d.reference_price, 0) >= ?
           ORDER BY d.score DESC LIMIT 30""",
        (ALERT_MIN_SCORE, ALERT_MIN_REFERENCE)).fetchall()
    if not rows:
        return 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    sent = 0
    with httpx.Client(timeout=15) as client:
        for r in rows:
            recipients = []
            if chat_id and r["retailer"] not in ALERT_EXCLUDE_RETAILERS:
                recipients.append(chat_id)
            recipients += subs.get(r["retailer"], [])
            if not recipients:
                # nobody wants this one - stamp it seen without the cost of
                # a live verification fetch
                conn.execute("UPDATE deals SET alerted_at=? WHERE id=?",
                             (now, r["id"]))
                conn.commit()
                continue
            if r["score"] >= BIG_DROP and not _verify_live(
                    r["retailer"], r["url"], float(r["price"])):
                conn.execute("UPDATE deals SET alerted_at=? WHERE id=?", (now, r["id"]))
                conn.commit()
                print(f"  - skipped stale error-tier deal (live price disagrees): "
                      f"{r['title']}")
                continue
            store = RETAILER_LABEL.get(r["retailer"], r["retailer"])
            text = (f"\U0001F6A8 <b>{round(r['score'] * 100)}% OFF</b> "
                    f"at {store}\n"
                    f"{html.escape(r['title'] or '')}\n"
                    f"<b>${r['price']:.2f}</b> — normally "
                    f"${r['reference_price']:.2f}\n"
                    f"<a href=\"{html.escape(_history_url(r['retailer'], r['sku']), quote=True)}\">"
                    f"Open price history</a>")
            delivered = 0
            for rcpt in recipients:
                resp = client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": rcpt, "text": text, "parse_mode": "HTML"})
                if resp.status_code == 200 and resp.json().get("ok"):
                    delivered += 1
                else:
                    # a blocked/deleted subscriber chat must not stall the
                    # owner's alerts or other subscribers
                    print(f"  ! telegram send failed for {rcpt} "
                          f"({resp.status_code}): {resp.text[:200]}")
            conn.execute("UPDATE deals SET alerted_at=? WHERE id=?",
                         (now, r["id"]))
            conn.commit()
            sent += delivered
    return sent


def send_item_watch(conn, recs) -> int:
    """Ping bot subscribers watching a specific product, on any price change.

    `recs` are ProductRecords that actually got a new price snapshot (from
    bulk_upsert's changed-list in the refresh lane, or the crawl lane's own
    old-vs-new comparison), so this fires on genuine moves only.
    """
    token, _ = _config()
    if not token or not recs:
        return 0
    subs = _item_subs(conn)
    if not subs:
        return 0
    sent = 0
    with httpx.Client(timeout=15) as client:
        for r in recs:
            for rcpt in subs.get((r.retailer, str(r.sku)), []):
                store = RETAILER_LABEL.get(r.retailer, r.retailer)
                rrp_line = f" (RRP ${r.rrp:.2f})" if r.rrp else ""
                text = (f"\U0001F514 <b>Price update</b> at {store}\n"
                        f"{html.escape(r.title or '')}\n"
                        f"<b>${r.price:.2f}</b>{rrp_line}\n"
                        f"<a href=\"{html.escape(_history_url(r.retailer, r.sku), quote=True)}\">"
                        f"Open price history</a>")
                resp = client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": rcpt, "text": text, "parse_mode": "HTML"})
                if resp.status_code == 200 and resp.json().get("ok"):
                    sent += 1
                else:
                    print(f"  ! telegram send failed for {rcpt} "
                          f"({resp.status_code}): {resp.text[:200]}")
    return sent


def send_ram_watch(recs) -> int:
    """Ping Telegram for every JB Hi-Fi RAM price change, deal or not.

    User-requested watch (2026-07-10) separate from the deal-detection
    pipeline above: anomaly.py only records a "deal" at 50%+ off a $40+
    reference price, which would miss ordinary RAM price moves. `recs` are
    the ProductRecords db.bulk_upsert actually wrote a new price_snapshots
    row for, so this only fires on a genuine price change, not every poll.
    """
    token, chat_id = _config()
    if not token or not chat_id:
        return 0
    matches = [r for r in recs
               if r.retailer == "jbhifi" and RAM_RX.search(r.title or "")]
    if not matches:
        return 0
    sent = 0
    with httpx.Client(timeout=15) as client:
        for r in matches:
            if not _confirm_live_price(r.retailer, r.url, float(r.price)):
                print(f"  - skipped stale RAM alert: {r.title}")
                continue

            rrp_line = f" (RRP ${r.rrp:.2f})" if r.rrp else ""
            text = (f"\U0001F4E6 <b>RAM price update</b> at JB Hi-Fi\n"
                    f"{html.escape(r.title or '')}\n"
                    f"<b>${r.price:.2f}</b>{rrp_line}\n"
                    f"<a href=\"{html.escape(_history_url(r.retailer, r.sku), quote=True)}\">"
                    f"Open price history</a>")
            resp = client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"})
            if resp.status_code == 200 and resp.json().get("ok"):
                sent += 1
            else:
                print(f"  ! telegram send failed ({resp.status_code}): "
                      f"{resp.text[:200]}")
                break
    return sent


def whoami():
    """Print the chat id of whoever messaged the bot last (setup helper)."""
    token, _ = _config()
    if not token:
        sys.exit("set TELEGRAM_BOT_TOKEN first")
    r = httpx.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=15)
    updates = r.json().get("result", [])
    if not updates:
        sys.exit("no messages yet - open your bot in Telegram, press Start, "
                 "send it any message, then rerun")
    for u in updates[-5:]:
        msg = u.get("message") or u.get("my_chat_member") or {}
        chat = msg.get("chat") or {}
        if chat.get("id"):
            print(f"chat_id: {chat['id']}  "
                  f"({chat.get('first_name') or chat.get('title') or ''})")


def test_message():
    """Send a test ping so the user can confirm the pipe works."""
    token, chat_id = _config()
    if not token or not chat_id:
        sys.exit("set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID first")
    r = httpx.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "parse_mode": "HTML",
              "text": "✅ Pricewatch alerts are live — you'll get a ping "
                      "here for every new deal the site detects."},
        timeout=15)
    print(r.status_code, r.text[:200])


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "whoami":
        whoami()
    elif cmd == "test":
        test_message()
    else:
        sys.exit("usage: python alerts.py whoami|test")
