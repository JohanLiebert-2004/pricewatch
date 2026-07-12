"""Cross-retailer product matching.

SKUs are retailer-internal and never match across stores. The only reliable
universal key is the GTIN/EAN barcode. Where GTIN is missing, we fall back to a
normalized brand+title signature. Matches made without a GTIN are LOWER
CONFIDENCE and flagged as such so the anomaly engine can weight them.
"""
import re

_STOP = {"the", "a", "an", "and", "with", "for", "size", "pack", "pk", "set",
         "new", "genuine", "official", "au", "australia", "cm", "mm", "ml", "l"}


def normalize_title(title: str) -> str:
    t = (title or "").lower()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    tokens = [w for w in t.split() if w not in _STOP and len(w) > 1]
    return " ".join(sorted(set(tokens)))


def signature(brand: str | None, title: str) -> str:
    b = (brand or "").lower().strip()
    return f"{b}|{normalize_title(title)}" if b else normalize_title(title)


def build_match_groups(conn) -> dict:
    """Return {group_key: [product_ids]} clustering the same item across retailers.

    Primary key: GTIN (high confidence). Fallback: brand+title signature
    (low confidence) only when 2+ retailers share it, to avoid false clusters.
    """
    groups, gtin_map, sig_map = {}, {}, {}
    # delisted rows (current_price NULL) can't be deal candidates and never
    # contribute peer prices (_cross_peers only maps non-NULL prices), so
    # skip shipping them out of the DB at all
    for p in conn.execute("SELECT id, retailer, gtin, brand, title "
                          "FROM products WHERE current_price IS NOT NULL"):
        if p["gtin"]:
            gtin_map.setdefault(f"gtin:{p['gtin']}", []).append(p)
        else:
            sig_map.setdefault(f"sig:{signature(p['brand'], p['title'])}", []).append(p)

    for key, rows in gtin_map.items():
        groups[key] = {"confidence": "high",
                       "product_ids": [r["id"] for r in rows],
                       "retailers": sorted({r["retailer"] for r in rows})}
    for key, rows in sig_map.items():
        retailers = {r["retailer"] for r in rows}
        if len(retailers) >= 2:  # only trust a fuzzy match across DIFFERENT stores
            groups[key] = {"confidence": "low",
                           "product_ids": [r["id"] for r in rows],
                           "retailers": sorted(retailers)}
    return groups
