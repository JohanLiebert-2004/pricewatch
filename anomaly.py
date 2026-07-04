"""Anomaly engine: scores prices against three signals and writes to deals.

Signals:
  rrp_gap        price is >= RRP_GAP_MIN off the listed RRP/was price
  history_drop   price is far below the product's own recent median
  cross_retailer price is far below the median for the same GTIN elsewhere
"""
import statistics

from matching import build_match_groups
from datetime import datetime, timezone

RRP_GAP_MIN = 0.50          # 50%+ off RRP gets recorded
HISTORY_DROP_MIN = 0.50     # 50%+ below own recent median (needs >= 3 snapshots)
CROSS_RETAILER_MIN = 0.50   # 50%+ below cross-retailer median (needs >= 2 others)
BIG_DROP = 0.80             # 80%+ = "error-tier" deal, worth an instant alert
MIN_PRICE = 1.0             # ignore sub-$1 noise
MIN_HISTORY = 3             # snapshots needed before history_drop can fire


def run(conn) -> list[dict]:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    found = []
    cross_peers = _cross_peers(conn)
    products = conn.execute("SELECT * FROM products").fetchall()
    for p in products:
        snaps = conn.execute(
            "SELECT price, rrp FROM price_snapshots WHERE product_id=? "
            "ORDER BY scraped_at DESC LIMIT 90", (p["id"],),
        ).fetchall()
        if not snaps:
            continue
        price = snaps[0]["price"]
        if price < MIN_PRICE:
            continue

        checks = []
        rrp = snaps[0]["rrp"]
        if rrp and rrp > 0:
            gap = 1 - price / rrp
            if gap >= RRP_GAP_MIN:
                checks.append(("rrp_gap", rrp, gap))

        history = [s["price"] for s in snaps[1:]]
        if len(history) >= MIN_HISTORY:
            med = statistics.median(history)
            if med > 0 and 1 - price / med >= HISTORY_DROP_MIN:
                checks.append(("history_drop", med, 1 - price / med))

        peers = cross_peers.get(p["id"])
        if peers:
            others = [pr for pid, pr in peers["prices"] if pr >= MIN_PRICE]
            # high-confidence (GTIN) needs 2+ peers for a stable median;
            # low-confidence (fuzzy) allows a single peer but is flagged _fuzzy
            need = 2 if peers["confidence"] == "high" else 1
            if len(others) >= need:
                med = statistics.median(others)
                if med > 0 and 1 - price / med >= CROSS_RETAILER_MIN:
                    sig = "cross_retailer" if peers["confidence"] == "high" \
                          else "cross_retailer_fuzzy"
                    checks.append((sig, med, 1 - price / med))

        for signal, ref, score in checks:
            cur = conn.execute(
                """INSERT OR IGNORE INTO deals
                   (product_id, price, reference_price, signal, score, detected_at)
                   VALUES (?,?,?,?,?,?)""",
                (p["id"], price, ref, signal, round(score, 4), now),
            )
            if cur.rowcount:
                found.append({
                    "retailer": p["retailer"], "title": p["title"], "url": p["url"],
                    "price": price, "reference": ref, "signal": signal,
                    "off": f"{score:.0%}", "marketplace": bool(p["is_marketplace"]),
                    "tier": "ERROR-TIER" if score >= BIG_DROP else "deal",
                })
    conn.commit()
    return found


def _cross_peers(conn) -> dict:
    """Map each product_id -> {confidence, prices:[(peer_id, latest_price)]}
    for OTHER retailers in the same match group."""
    latest = {}
    for r in conn.execute(
        """SELECT product_id, price FROM price_snapshots ps WHERE id = (
             SELECT id FROM price_snapshots WHERE product_id=ps.product_id
             ORDER BY scraped_at DESC LIMIT 1)"""):
        latest[r["product_id"]] = r["price"]
    retailer_of = {p["id"]: p["retailer"]
                   for p in conn.execute("SELECT id, retailer FROM products")}
    out = {}
    for g in build_match_groups(conn).values():
        ids = g["product_ids"]
        for pid in ids:
            peers = [(o, latest[o]) for o in ids
                     if o != pid and o in latest
                     and retailer_of[o] != retailer_of.get(pid)]
            if peers:
                out[pid] = {"confidence": g["confidence"], "prices": peers}
    return out
