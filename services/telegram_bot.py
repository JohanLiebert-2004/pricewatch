"""Telegram subscription bot - runs as a systemd service on the OCI VM.

Lets site visitors subscribe to alerts themselves (the owner's personal
alerts in alerts.py are separate and unchanged):

  - item watch:  t.me/underp22bot?start=i_<retailer>_<sku>
                 ping on every price change for that product
  - store watch: t.me/underp22bot?start=s_<retailer>
                 ping for every new deal the anomaly engine records there

This process only handles registration (long-polls getUpdates; no inbound
webhook, so no port needs to be open for it). The actual alert fan-out
happens where prices/deals are detected - alerts.send_item_watch() in the
refresh/crawl lanes and the subscriber loop in alerts.send_alerts() - all
running from GitHub Actions with the same DATABASE_URL.

NOTE: this is the only getUpdates consumer allowed; `python alerts.py
whoami` (a setup helper that also calls getUpdates) will conflict while
this service runs - stop the service first if you ever need whoami again.

Env (from /opt/pricewatch.env): TELEGRAM_BOT_TOKEN, DATABASE_URL.
"""
import html
import os
import re
import time

import httpx
import psycopg
from psycopg.rows import dict_row

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
DATABASE_URL = os.environ["DATABASE_URL"]
API = f"https://api.telegram.org/bot{TOKEN}"

RETAILER_LABEL = {"kmart": "Kmart", "bigw": "Big W", "target": "Target",
                  "officeworks": "Officeworks", "jbhifi": "JB Hi-Fi",
                  "goodguys": "The Good Guys", "myer": "Myer",
                  "supercheap": "Supercheap Auto", "sephora": "Sephora",
                  "chemistwarehouse": "Chemist Warehouse", "booktopia": "Booktopia", "qbd": "QBD Books", "ikea": "IKEA"}

PAYLOAD_RX = re.compile(r"^(i_([a-z]+)_([A-Za-z0-9_-]{1,48})|s_([a-z]+))$")

HELP = (
    "👋 <b>Dealwatch alerts</b>\n\n"
    "Subscribe from the website: open any product's price-history page and "
    "tap <i>Telegram alerts</i> — you'll land back here with the watch "
    "pre-filled.\n\n"
    "Commands:\n"
    "/list — your current watches\n"
    "/stop N — remove watch number N (from /list)\n"
    "/stop all — remove every watch"
)


def db():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True)


def send(chat_id: int, text: str):
    try:
        httpx.post(f"{API}/sendMessage", timeout=15,
                   json={"chat_id": chat_id, "text": text,
                         "parse_mode": "HTML",
                         "disable_web_page_preview": True})
    except httpx.HTTPError as e:
        print(f"send failed for {chat_id}: {e}", flush=True)


def product_title(conn, retailer: str, sku: str) -> str | None:
    row = conn.execute(
        "SELECT title FROM products WHERE retailer=%s AND sku=%s LIMIT 1",
        (retailer, sku)).fetchone()
    return row["title"] if row else None


def handle_start(conn, chat_id: int, payload: str):
    m = PAYLOAD_RX.match(payload or "")
    if not m:
        send(chat_id, HELP)
        return
    if m.group(1).startswith("i_"):
        retailer, sku = m.group(2), m.group(3)
        if retailer not in RETAILER_LABEL:
            send(chat_id, HELP)
            return
        title = product_title(conn, retailer, sku)
        if title is None:
            send(chat_id, "That product isn't in the catalogue (it may have "
                          "been delisted). Nothing was subscribed.")
            return
        conn.execute(
            """INSERT INTO telegram_subs (chat_id, retailer, sku, title)
               VALUES (%s,%s,%s,%s)
               ON CONFLICT (chat_id, retailer, COALESCE(sku, ''))
               DO NOTHING""",
            (chat_id, retailer, sku, title))
        send(chat_id,
             f"🔔 Watching <b>{html.escape(title)}</b> at "
             f"{RETAILER_LABEL[retailer]}.\nYou'll get a message here on "
             f"every price change. /list to manage.")
    else:
        retailer = m.group(4)
        if retailer not in RETAILER_LABEL:
            send(chat_id, HELP)
            return
        conn.execute(
            """INSERT INTO telegram_subs (chat_id, retailer, sku, title)
               VALUES (%s,%s,NULL,NULL)
               ON CONFLICT (chat_id, retailer, COALESCE(sku, ''))
               DO NOTHING""",
            (chat_id, retailer))
        send(chat_id,
             f"🔔 Watching all new <b>{RETAILER_LABEL[retailer]}</b> deals "
             f"(50%+ off). /list to manage.")


def handle_list(conn, chat_id: int):
    rows = conn.execute(
        "SELECT id, retailer, sku, title FROM telegram_subs "
        "WHERE chat_id=%s ORDER BY id", (chat_id,)).fetchall()
    if not rows:
        send(chat_id, "No watches yet. Subscribe from the website's "
                      "product pages.")
        return
    lines = []
    for i, r in enumerate(rows, 1):
        store = RETAILER_LABEL.get(r["retailer"], r["retailer"])
        what = (html.escape(r["title"] or r["sku"])
                if r["sku"] else "all deals")
        lines.append(f"{i}. {store} — {what}")
    send(chat_id, "<b>Your watches</b>\n" + "\n".join(lines) +
                  "\n\n/stop N to remove one, /stop all for all")


def handle_stop(conn, chat_id: int, arg: str):
    if arg.strip().lower() == "all":
        conn.execute("DELETE FROM telegram_subs WHERE chat_id=%s", (chat_id,))
        send(chat_id, "All watches removed.")
        return
    try:
        n = int(arg.strip())
    except ValueError:
        send(chat_id, "Use /stop N (a number from /list) or /stop all.")
        return
    rows = conn.execute(
        "SELECT id FROM telegram_subs WHERE chat_id=%s ORDER BY id",
        (chat_id,)).fetchall()
    if not 1 <= n <= len(rows):
        send(chat_id, f"No watch number {n} — check /list.")
        return
    conn.execute("DELETE FROM telegram_subs WHERE id=%s", (rows[n - 1]["id"],))
    send(chat_id, f"Watch {n} removed.")


def handle_update(conn, upd: dict):
    msg = upd.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    text = (msg.get("text") or "").strip()
    if not chat_id or not text:
        return
    if text.startswith("/start"):
        handle_start(conn, chat_id, text.partition(" ")[2].strip())
    elif text.startswith("/list"):
        handle_list(conn, chat_id)
    elif text.startswith("/stop"):
        handle_stop(conn, chat_id, text.partition(" ")[2])
    else:
        send(chat_id, HELP)


def main():
    print("bot polling started", flush=True)
    offset = 0
    conn = db()
    while True:
        try:
            r = httpx.get(f"{API}/getUpdates", timeout=70,
                          params={"timeout": 60, "offset": offset,
                                  "allowed_updates": '["message"]'})
            for upd in r.json().get("result", []):
                offset = upd["update_id"] + 1
                try:
                    handle_update(conn, upd)
                except psycopg.OperationalError:
                    conn = db()   # pooled connection dropped - reconnect once
                    handle_update(conn, upd)
        except Exception as e:
            print(f"poll error: {type(e).__name__}: {e}", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
