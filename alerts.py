"""Telegram deal alerts.

Pings a Telegram chat the moment the anomaly engine records a monster deal:
ALERT_MIN_SCORE off an item whose normal price is ALERT_MIN_REFERENCE or more.

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
import sys
from datetime import datetime, timezone

import httpx

ALERT_MIN_SCORE = 0.80        # 80%+ off
ALERT_MIN_REFERENCE = 100.0   # items that normally cost $100+

RETAILER_LABEL = {"kmart": "Kmart", "bigw": "Big W", "target": "Target",
                  "officeworks": "Officeworks", "jbhifi": "JB Hi-Fi"}


def _config():
    return (os.environ.get("TELEGRAM_BOT_TOKEN"),
            os.environ.get("TELEGRAM_CHAT_ID"))


def send_alerts(conn) -> int:
    """Alert on not-yet-alerted qualifying deals. Returns alerts sent."""
    token, chat_id = _config()
    if not token or not chat_id:
        return 0
    rows = conn.execute(
        """SELECT d.id, d.price, d.reference_price, d.score,
                  p.title, p.retailer, p.url
           FROM deals d JOIN products p ON p.id = d.product_id
           WHERE d.alerted_at IS NULL AND d.status <> 'expired'
             AND d.score >= ? AND COALESCE(d.reference_price, 0) >= ?
           ORDER BY d.score DESC LIMIT 20""",
        (ALERT_MIN_SCORE, ALERT_MIN_REFERENCE)).fetchall()
    if not rows:
        return 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    sent = 0
    with httpx.Client(timeout=15) as client:
        for r in rows:
            store = RETAILER_LABEL.get(r["retailer"], r["retailer"])
            text = (f"\U0001F6A8 <b>{round(r['score'] * 100)}% OFF</b> "
                    f"at {store}\n"
                    f"{html.escape(r['title'] or '')}\n"
                    f"<b>${r['price']:.2f}</b> — normally "
                    f"${r['reference_price']:.2f}\n"
                    f"{r['url']}")
            resp = client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"})
            if resp.status_code == 200 and resp.json().get("ok"):
                conn.execute("UPDATE deals SET alerted_at=? WHERE id=?",
                             (now, r["id"]))
                conn.commit()
                sent += 1
            else:
                print(f"  ! telegram send failed ({resp.status_code}): "
                      f"{resp.text[:200]}")
                break   # bad token/chat id — don't hammer the API
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
                      "here for every deal 80%+ off an item normally $100+."},
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
