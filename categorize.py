"""Title-based product categorisation.

Retailers don't expose category data in listings, so we bucket products by
keyword rules on the title. Mirrors the rules in web/index.html so the deal
feed and the catalogue agree.
"""
import re
import time

RULES = [
    ("clothing", re.compile(
        r"\b(t-?shirt|shirt|tee|top|trunks|shorts|jeans|pants?|trouser|"
        r"leggings?|dress|skirt|jacket|hoodie|jumper|sweater|coat|vest|"
        r"cardigan|blouse|polo|chino|sock|underwear|bra|briefs|pyjama|pajama|"
        r"sneaker|shoe|boot|sandal|thong|slipper|cap|beanie|hat|scarf|glove|"
        r"swimwear|bikini|rashie|wig|costume|racing suit)\b", re.I)),
    ("tech", re.compile(
        r"\b(laptop|notebook pc|monitor|headphone|earbud|ear ?pod|speaker|"
        r"soundbar|tablet|ipad|iphone|galaxy|pixel|phone|charger|power ?bank|"
        r"usb|hdmi|ssd|hard ?drive|mouse|keyboard|printer|ink cartridge|"
        r"ink refill|toner|camera|"
        r"webcam|smart ?watch|fitbit|garmin|television|tv|console|playstation|"
        r"xbox|nintendo|router|modem|drone|projector|chromebook|macbook|"
        r"airpod|kindle|e-?reader|gpu|cpu|ram)\b", re.I)),
    ("kitchen", re.compile(
        r"\b(air ?fryer|fryer|rice cooker|slow cooker|multi ?cooker|kettle|"
        r"toaster|blender|mixer|coffee|espresso|frypan|saucepan|pan set|"
        r"cookware|knife|knives|chopping|dinner ?set|plate|bowl set|mug|"
        r"glassware|jug|bakeware|oven tray|microwave|dishwasher|utensil|"
        r"cutlery|thermomix|food processor)\b", re.I)),
    ("toys", re.compile(
        r"\b(toy|lego|duplo|plush|doll|barbie|nerf|puzzle|board game|playset|"
        r"play set|action figure|hot wheels|paw patrol|bluey|pokemon|disney|"
        r"marvel|scooter|trampoline|baby|infant|nappy|nappies|pram|stroller|"
        r"cot|bassinet|nanobebe|teether|kids?|children)\b", re.I)),
    ("beauty", re.compile(
        r"\b(skincare|skin care|serum|toner|moisturis\w*|cleanser|sunscreen|"
        r"spf|makeup|make-up|mascara|lipstick|foundation|concealer|eyeshadow|"
        r"shampoo|conditioner|hair ?dryer|straightener|curler|perfume|"
        r"fragrance|cologne|nail polish|razor|shaver|trimmer|epilator|oxx)\b",
        re.I)),
    # Keep this deliberately narrow. Generic stationery words (paper, journal,
    # notebook, pen) created a misleading "Books" feed, including apparel
    # listings with incidental words in their titles. Specialist book retailers
    # set category="books" directly from their book-only catalogue paths.
    ("books", re.compile(
        r"\b(book|novel|paperback|hardback|hardcover|softcover|audiobook|"
        r"e-?book|textbook|omnibus|box ?set|anthology|memoir|biography|"
        r"dictionary|thesaurus|atlas|comic|manga|graphic novel|"
        r"colouring book|coloring book|activity book)\b", re.I)),
    ("home", re.compile(
        r"\b(chair|desk|table|shelf|shelving|bookcase|drawer|cabinet|sofa|"
        r"couch|lounge|bed|mattress|quilt|doona|duvet|sheet set|pillow|"
        r"cushion|towel|blanket|throw|rug|mat|curtain|blind|lamp|light\w*|"
        r"mirror|vase|candle|photo frame|clock|storage|organiser|organizer|"
        r"basket|hamper|heater|fan|air purifier|humidifier|vacuum|mop|broom|"
        r"laundry|ironing|garden|plant|pot|bbq|outdoor|gazebo|jar|container)\b",
        re.I)),
]


def categorize(title: str | None) -> str:
    t = title or ""
    for cat, rx in RULES:
        if rx.search(t):
            return cat
    return "other"


# -- per-store subcategories (site's per-retailer chips) ----------------------
# Retailers with native category data (kmart / bigw / jbhifi / supercheap /
# chemistwarehouse) tag products.subcategory in their scrapers; the ones below
# have no per-product category anywhere in their data source, so their chips
# are derived from titles. First match wins - order from specific to broad.
def _r(pat):
    return re.compile(rf"\b(?:{pat})\b", re.I)


SUBCAT_RULES = {
    "myer": [
        ("Shoes", _r(r"sneaker|shoe|boot|heel|sandal|loafer|slipper|thong|flat|pump|trainer")),
        ("Bags & Accessories", _r(r"bag|tote|clutch|wallet|purse|backpack|belt|scarf|hat|cap|sunglass|watch|jewell?ery|earring|necklace|bracelet|ring")),
        ("Beauty", _r(r"serum|moisturis\w*|cleanser|mascara|lipstick|foundation|perfume|fragrance|eau de|shampoo|conditioner|skincare|makeup")),
        ("Kids & Toys", _r(r"kids?|baby|infant|toddler|toy|lego|plush|doll")),
        ("Home & Kitchen", _r(r"quilt|sheet|pillow|towel|cushion|candle|vase|dinner|plate|mug|glass|pan|cookware|knife|kettle|blender|coffee")),
        ("Tech", _r(r"headphone|speaker|tablet|watch smart|smart ?watch|earbud")),
        ("Clothing", _r(r"dress|top|tee|t-?shirt|shirt|knit|jumper|sweater|hoodie|jacket|coat|blazer|pant|jean|short|skirt|cami|blouse|polo|suit|swim|bra|brief|sock|pyjama|robe|cardigan|vest|parka|trench|legging")),
    ],
    "goodguys": [
        ("Fridges & Freezers", _r(r"fridge|refrigerator|freezer")),
        ("Laundry", _r(r"washer|washing machine|dryer|laundry")),
        ("Kitchen Appliances", _r(r"oven|cooktop|rangehood|dishwasher|microwave|air fryer|fryer|kettle|toaster|blender|mixer|coffee|espresso|rice cooker|slow cooker|food processor")),
        ("Heating & Cooling", _r(r"air con\w*|aircon\w*|heater|fan|purifier|humidifier|dehumidifier|split system")),
        ("Floorcare", _r(r"vacuum|mop|steam cleaner|carpet")),
        ("TVs & Home Cinema", _r(r"tv|television|soundbar|projector|home theatre")),
        ("Audio", _r(r"headphone|earbud|speaker|turntable|radio")),
        ("Computers & Phones", _r(r"laptop|notebook|desktop|monitor|iphone|galaxy|pixel|phone|tablet|ipad|printer|router")),
        ("Personal Care", _r(r"shaver|trimmer|straightener|hair dryer|toothbrush|epilator")),
        ("Gaming", _r(r"playstation|xbox|nintendo|console|gaming")),
    ],
    "officeworks": [
        ("Ink & Toner", _r(r"ink|toner|cartridge")),
        ("Paper & Notebooks", _r(r"paper|notebook|notepad|envelope|label|card ?stock|copy")),
        ("Pens & Stationery", _r(r"pen|pencil|marker|highlighter|stapler|tape|glue|scissors|eraser|sharpener|post-?it|binder|folder|clip")),
        ("Tech", _r(r"laptop|monitor|keyboard|mouse|usb|ssd|hard ?drive|webcam|headset|printer|router|charger|cable|tablet|ipad")),
        ("Furniture", _r(r"chair|desk|table|shelf|shelving|cabinet|drawer|stool|whiteboard")),
        ("Education & Art", _r(r"crayon|paint|canvas|easel|colouring|craft|chalk|book cover|calculator")),
    ],
    "target": [
        ("Women", _r(r"women'?s?|ladies|maternity")),
        ("Men", _r(r"men'?s?")),
        ("Kids & Baby", _r(r"kids?|baby|infant|toddler|girls?|boys?|nappy|nappies")),
        ("Toys", _r(r"toy|lego|doll|plush|puzzle|board game|nerf")),
        ("Home", _r(r"quilt|sheet|pillow|towel|cushion|candle|frame|lamp|storage|rug")),
        ("Tech & Entertainment", _r(r"headphone|speaker|console|playstation|xbox|nintendo|tablet|tv|book")),
        ("Beauty", _r(r"makeup|mascara|lipstick|skincare|shampoo|fragrance|perfume")),
        ("Clothing", _r(r"dress|top|tee|t-?shirt|shirt|jumper|hoodie|jacket|pant|jean|short|skirt|sock|pyjama|swim|bra|brief|shoe|sneaker|boot")),
    ],
    "sephora": [
        ("Skincare", _r(r"serum|moisturis\w*|cleanser|toner|mask|spf|sunscreen|eye cream|exfoli\w*|essence|retinol|hyaluronic")),
        ("Makeup", _r(r"mascara|lipstick|lip |foundation|concealer|eyeshadow|eyeliner|blush|bronzer|highlighter|primer|palette|brow|setting spray|tint")),
        ("Hair", _r(r"shampoo|conditioner|hair|scalp")),
        ("Fragrance", _r(r"perfume|fragrance|eau de|cologne|parfum|body mist")),
        ("Bath & Body", _r(r"body|hand cream|soap|bath|shower|scrub|deodorant")),
        ("Tools & Brushes", _r(r"brush|sponge|tool|roller|gua sha|mirror|tweezer|curler")),
    ],
}


def subcategorize(retailer: str, title: str | None) -> str | None:
    t = title or ""
    for label, rx in SUBCAT_RULES.get(retailer, ()):
        if rx.search(t):
            return label
    return None


def backfill(conn) -> int:
    """Categorise every product that doesn't have a category yet.

    The `embed` CI job runs concurrently with this one (both write to
    `products`; see embed_products.py's _update_batch_with_retry docstring)
    and updates rows in whatever order its own budget/cursor picks. Without
    a consistent lock order here, two concurrent per-row UPDATE sequences
    hitting overlapping rows in different orders is a textbook Postgres
    deadlock - seen live 22 July 2026, which crashed the whole hourly
    detect job (and with it, that cycle's price-drop alerts) over a single
    unlucky race. `ORDER BY id` makes this job's own lock order
    deterministic, and the retry loop (same shape as embed_products.py's)
    means the rare remaining collision just costs one retry instead of the
    entire job.
    """
    rows = conn.execute(
        "SELECT id, title FROM products "
        "WHERE category IS NULL OR category = '' ORDER BY id").fetchall()
    if not rows:
        return 0
    updates = [(categorize(r["title"]), r["id"]) for r in rows]
    for i in range(0, len(updates), 1000):
        _update_batch_with_retry(conn, updates[i:i + 1000])
    return len(updates)


def _update_batch_with_retry(conn, batch, attempts=3):
    for attempt in range(attempts):
        try:
            conn.executemany("UPDATE products SET category=? WHERE id=?", batch)
            conn.commit()
            return
        except Exception as e:
            if type(e).__name__ != "DeadlockDetected":
                raise
            conn.rollback()
            if attempt == attempts - 1:
                print(f"categorize.backfill: giving up on a batch of "
                      f"{len(batch)} products after {attempts} deadlock "
                      "retries, will retry next run")
                return
            time.sleep(0.5 * (attempt + 1))



def repair_misclassified_books(conn) -> int:
    """Correct prior broad book tagging without rewriting the full catalogue.

    Only records already tagged as books are reconsidered. This is safe to run
    every detect cycle and promptly removes clothing/stationery false positives
    created by the older broad rule.
    """
    rows = conn.execute(
        "SELECT id, title FROM products WHERE category='books'").fetchall()
    updates = [(category, row["id"]) for row in rows
               if (category := categorize(row["title"])) != "books"]
    for i in range(0, len(updates), 1000):
        conn.executemany("UPDATE products SET category=? WHERE id=?",
                         updates[i:i + 1000])
        conn.commit()
    return len(updates)
def backfill_subcategories(conn) -> int:
    """Tag subcategory from titles for retailers with no native category data."""
    total = 0
    for retailer in SUBCAT_RULES:
        rows = conn.execute(
            "SELECT id, title FROM products "
            "WHERE retailer=? AND subcategory IS NULL", (retailer,)).fetchall()
        updates = [(sub, r["id"]) for r in rows
                   if (sub := subcategorize(retailer, r["title"]))]
        for i in range(0, len(updates), 1000):
            conn.executemany("UPDATE products SET subcategory=? WHERE id=?",
                             updates[i:i + 1000])
            conn.commit()
        total += len(updates)
    return total
