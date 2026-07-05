"""Title-based product categorisation.

Retailers don't expose category data in listings, so we bucket products by
keyword rules on the title. Mirrors the rules in web/index.html so the deal
feed and the catalogue agree.
"""
import re

RULES = [
    ("tech", re.compile(
        r"\b(laptop|notebook pc|monitor|headphone|earbud|ear ?pod|speaker|"
        r"soundbar|tablet|ipad|iphone|galaxy|pixel|phone|charger|power ?bank|"
        r"usb|hdmi|ssd|hard ?drive|mouse|keyboard|printer|ink|toner|camera|"
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
    ("clothing", re.compile(
        r"\b(t-?shirt|shirt|tee|trunks|shorts|jeans|pants|leggings|dress|"
        r"skirt|jacket|hoodie|jumper|sweater|coat|sock|underwear|bra|briefs|"
        r"pyjama|pajama|sneaker|shoe|boot|sandal|thong|slipper|cap|beanie|"
        r"hat|scarf|glove|swimwear|bikini|rashie|wig|costume|racing suit)\b",
        re.I)),
    ("beauty", re.compile(
        r"\b(skincare|skin care|serum|toner|moisturis\w*|cleanser|sunscreen|"
        r"spf|makeup|make-up|mascara|lipstick|foundation|concealer|eyeshadow|"
        r"shampoo|conditioner|hair ?dryer|straightener|curler|perfume|"
        r"fragrance|cologne|nail polish|razor|shaver|trimmer|epilator|oxx)\b",
        re.I)),
    ("books", re.compile(
        r"\b(book|novel|paperback|hardcover|colouring|coloring|notebook|"
        r"journal|diary|planner|pen|pencil|marker|highlighter|stapler|tape|"
        r"post-?it|envelope|paper|folder|binder|calculator|whiteboard|easel)\b",
        re.I)),
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


def backfill(conn) -> int:
    """Categorise every product that doesn't have a category yet."""
    rows = conn.execute(
        "SELECT id, title FROM products "
        "WHERE category IS NULL OR category = ''").fetchall()
    if not rows:
        return 0
    updates = [(categorize(r["title"]), r["id"]) for r in rows]
    for i in range(0, len(updates), 1000):
        conn.executemany("UPDATE products SET category=? WHERE id=?",
                         updates[i:i + 1000])
        conn.commit()
    return len(updates)
