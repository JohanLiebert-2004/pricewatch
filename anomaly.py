"""Anomaly engine: scores prices against three signals and writes to deals.

Signals:
  rrp_gap        price is >= RRP_GAP_MIN off the listed RRP/was price
  history_drop   price is far below the product's own recent median
  cross_retailer price is far below the median for the same GTIN elsewhere
"""
import statistics
import db

from matching import build_match_groups
from datetime import datetime, timedelta, timezone

RRP_GAP_MIN = 0.50          # 50%+ off RRP gets recorded
HISTORY_DROP_MIN = 0.50     # 50%+ below own recent median (needs >= 3 snapshots)
CROSS_RETAILER_MIN = 0.50   # 50%+ below cross-retailer median (needs >= 2 others)
BIG_DROP = 0.80             # 80%+ = "error-tier" deal, worth an instant alert
MIN_PRICE = 1.0             # ignore sub-$1 noise
MIN_REFERENCE = 40.0        # a deal only counts if the item NORMALLY costs
                            # this much - nobody cares about cheap items on
                            # sale. (Gates the reference price, not the sale
                            # price: a $500 item at $20 must still fire.)
MIN_HISTORY = 3             # snapshots needed before history_drop can fire


def run(conn) -> list[dict]:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    found = []
    # Incremental: a product whose price didn't move since the last detect
    # run can't newly trip rrp_gap/history_drop (both compare the CURRENT
    # price against a reference that also hasn't moved), so only products
    # with a fresh price_snapshots row since last time need rescoring.
    # Rescanning the full catalogue every hour regardless - explicit columns
    # or not - grew into the single biggest line in Supabase's egress bill
    # as the catalogue passed a quarter million rows (~65MB/run x 24/day).
    # price_snapshots only gets a new row on a real change (db.py's
    # bulk_upsert), so this stays cheap however large the table gets.
    cutoff_row = conn.execute(
        "SELECT v FROM kv WHERE k = 'anomaly_last_detect_at'").fetchone()
    cutoff = cutoff_row["v"] if cutoff_row else None
    # Keep the changed-product set in the database. Expanding a multi-day
    # backlog into one bound parameter per ID exceeds PostgreSQL's protocol
    # limit and prevents the detector from ever advancing its watermark.
    through = now
    if cutoff:
        # Finish every timestamp group at the boundary so rows sharing a
        # bulk-ingest timestamp cannot be skipped while draining a backlog.
        window = conn.execute("SELECT scraped_at FROM price_snapshots WHERE scraped_at > ? "
                              "AND scraped_at <= ? ORDER BY scraped_at LIMIT 1000", (cutoff, now)).fetchall()
        if len(window) == 1000:
            value = window[-1]['scraped_at']
            through = value.isoformat() if hasattr(value, 'isoformat') else str(value)
    changed_sql = "SELECT product_id FROM price_snapshots WHERE scraped_at > ? AND scraped_at <= ?"
    scope = (cutoff, through)
    if cutoff:
        fields = 'id,retailer,title,url,is_marketplace,current_price,current_rrp,gtin,brand'
        if isinstance(conn, db._PgShim):
            products = conn.execute(
                f"SELECT p.* FROM (SELECT DISTINCT product_id FROM price_snapshots "
                f"WHERE scraped_at > ? AND scraped_at <= ?) changed CROSS JOIN LATERAL "
                f"(SELECT {fields} FROM products WHERE id=changed.product_id OFFSET 0) p", scope).fetchall()
        else:
            products = conn.execute(f"SELECT {fields} FROM products WHERE id IN ({changed_sql})", scope).fetchall()
    else:
        # no marker yet (first run after this change) - one full pass, then
        # every run after this becomes incremental
        products = conn.execute(
            "SELECT id, retailer, title, url, is_marketplace, "
            "current_price, current_rrp,gtin,brand FROM products").fetchall()

    cross_peers = _cross_peers(conn, products) if datetime.now(timezone.utc).hour % 6 == 0 and products else {}

    # one bulk pass instead of a per-product query: with change-only
    # snapshots the whole table stays small enough to group in memory
    history_by_pid = {}
    if cutoff and isinstance(conn, db._PgShim):
        hist_rows = conn.execute(
            f"SELECT h.product_id,h.price,h.rrp FROM "
            f"(SELECT DISTINCT product_id FROM price_snapshots WHERE scraped_at > ? AND scraped_at <= ?) changed "
            "CROSS JOIN LATERAL (SELECT product_id,price,rrp FROM price_snapshots "
            "WHERE product_id=changed.product_id ORDER BY scraped_at DESC LIMIT 90) h",
            scope)
    elif cutoff:
        hist_rows = conn.execute(
            f"SELECT product_id, price, rrp FROM price_snapshots "
            f"WHERE product_id IN ({changed_sql}) ORDER BY scraped_at DESC", scope)
    else:
        hist_rows = conn.execute(
            "SELECT product_id, price, rrp FROM price_snapshots "
            "ORDER BY scraped_at DESC")
    for s in hist_rows:
        h = history_by_pid.setdefault(s["product_id"], [])
        if len(h) < 90:
            h.append(s)
    # Known deal keys, matching the table's UNIQUE (product_id, price, signal).
    # Dedup is decided here instead of via per-row INSERT OR IGNORE rowcounts:
    # one insert round trip per deal inside a single long transaction held the
    # unique-index locks for the entire scoring pass (minutes from a US runner
    # to Sydney), starving any concurrent detect into Supabase's 2-minute
    # statement timeout. The constraint still backstops races.
    existing = {(d["product_id"], round(float(d["price"]), 2), d["signal"])
                for d in conn.execute(
                    "SELECT product_id, price, signal FROM deals")}
    # Close the read transaction: the loop below is minutes of pure python,
    # and a job killed mid-loop would otherwise leave an orphaned
    # idle-in-transaction session blocking the next run's detect.
    conn.commit()
    new_rows = []
    for p in products:
        snaps = history_by_pid.get(p["id"])
        if not snaps:
            continue
        price = float(p["current_price"] if p["current_price"] is not None
                      else snaps[0]["price"])
        if price < MIN_PRICE:
            continue

        checks = []
        rrp = (p["current_rrp"] if p["current_rrp"] is not None
               else snaps[0]["rrp"])
        rrp = float(rrp) if rrp is not None else None
        if rrp and rrp >= MIN_REFERENCE:
            gap = 1 - price / rrp
            if gap >= RRP_GAP_MIN:
                checks.append(("rrp_gap", rrp, gap))

        history = [float(s["price"]) for s in snaps[1:]]
        if len(history) >= MIN_HISTORY:
            med = statistics.median(history)
            if med >= MIN_REFERENCE and 1 - price / med >= HISTORY_DROP_MIN:
                checks.append(("history_drop", med, 1 - price / med))

        peers = cross_peers.get(p["id"])
        if peers:
            others = [pr for pid, pr in peers["prices"] if pr >= MIN_PRICE]
            # high-confidence (GTIN) needs 2+ peers for a stable median;
            # low-confidence (fuzzy) allows a single peer but is flagged _fuzzy
            need = 2 if peers["confidence"] == "high" else 1
            if len(others) >= need:
                med = statistics.median(others)
                if med >= MIN_REFERENCE and 1 - price / med >= CROSS_RETAILER_MIN:
                    sig = "cross_retailer" if peers["confidence"] == "high" \
                          else "cross_retailer_fuzzy"
                    checks.append((sig, med, 1 - price / med))

        for signal, ref, score in checks:
            key = (p["id"], round(price, 2), signal)
            if key in existing:
                continue
            existing.add(key)
            new_rows.append(
                (p["id"], price, ref, signal, round(score, 4), now))
            found.append({
                "retailer": p["retailer"], "title": p["title"], "url": p["url"],
                "price": price, "reference": ref, "signal": signal,
                "off": f"{score:.0%}", "marketplace": bool(p["is_marketplace"]),
                "tier": "ERROR-TIER" if score >= BIG_DROP else "deal",
            })
    if new_rows:
        conn.executemany(
            """INSERT OR IGNORE INTO deals
               (product_id, price, reference_price, signal, score, detected_at)
               VALUES (?,?,?,?,?,?)""", new_rows)
    # captured before this run's reads, so a snapshot written mid-run is
    # simply picked up next cycle instead of risking a gap
    conn.execute(
        "INSERT INTO kv (k, v) VALUES ('anomaly_last_detect_at', ?) "
        "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (through,))
    conn.commit()
    return found


def _cross_peers(conn, candidates=None) -> dict:
    """Map each product_id -> {confidence, prices:[(peer_id, latest_price)]}
    for OTHER retailers in the same match group."""
    if candidates is not None:
        candidates = [dict(p) for p in candidates]
        # Anomaly alerts use exact barcode peers of changed products. Fuzzy
        # product suggestions remain available separately on product pages.
        gtins = sorted({str(p['gtin']) for p in candidates if p.get('gtin')
                        and str(p['gtin']).isdigit() and len(str(p['gtin'])) in (8,12,13,14)
                        and set(str(p['gtin'])) != {'0'}})
        if not gtins:
            return {}
        cutoff = (datetime.now(timezone.utc)-timedelta(hours=36)).isoformat()
        rows = conn.execute('SELECT id,retailer,gtin,current_price FROM products WHERE gtin IN (' +
                            ','.join('?' for _ in gtins) + ') AND current_price>0 AND last_seen>?',
                            (*gtins, cutoff)).fetchall()
        groups = {}
        for row in rows:
            groups.setdefault(row['gtin'], []).append(row)
        out = {}
        for product in candidates:
            peers = [(p['id'],float(p['current_price'])) for p in groups.get(product.get('gtin'), [])
                     if p['id'] != product['id'] and p['retailer'] != product['retailer']]
            if peers:
                out[product['id']] = {'confidence':'high','prices':peers}
        return out
    latest = {}
    retailer_of = {}
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=36)).isoformat()
    for p in conn.execute(
            "SELECT id, retailer, current_price FROM products "
            "WHERE current_price > 0 AND last_seen > ?", (cutoff,)):
        retailer_of[p["id"]] = p["retailer"]
        latest[p["id"]] = float(p["current_price"])
    if not latest:
        return {}
    out = {}
    for g in build_match_groups(conn, fresh_since=cutoff).values():
        ids = g["product_ids"]
        for pid in ids:
            peers = [(o, latest[o]) for o in ids
                     if o != pid and o in latest
                     and retailer_of[o] != retailer_of.get(pid)]
            if peers:
                out[pid] = {"confidence": g["confidence"], "prices": peers}
    return out
