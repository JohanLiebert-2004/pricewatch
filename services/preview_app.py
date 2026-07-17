"""SSR preview pages + image proxy - runs behind nginx on the OCI VM.

Two jobs the static Vercel site can't do itself:

  GET /p/{retailer}/{sku}
      The share/crawler-friendly product URL. Serves web/product.html with
      real <title>/description/Open Graph/Twitter meta injected server-side,
      so Google and link previews (WhatsApp/Telegram/Twitter) see actual
      product content instead of an empty JS shell. Humans get the exact
      same page as product.html - its JS reads the /p/<retailer>/<sku> path.
      Reached via a Vercel rewrite so it lives on the site's own origin.

  GET /img?u=<retailer-cdn-url>
      Caching image proxy (host-whitelisted, size-capped). Cuts repeat page
      loads' dependence on eight different retailer CDNs and stops sending
      visitor referers to retailers. Cache is content-addressed on disk;
      nothing expires it (product images are immutable in practice, and the
      45GB boot volume dwarfs the working set).

Reads product data through the same public PostgREST anon endpoints as the
website (read-only by RLS) - no DB credentials needed in this process.
"""
import hashlib
import html
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import FastAPI, HTTPException, Query, Response

SUPABASE_URL = "https://eklfgwalyfugpeieeqwz.supabase.co"
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
SITE_URL = os.environ.get("SITE_URL", "https://web-pi-blush-48.vercel.app").rstrip("/")
SELF_URL = os.environ.get("SELF_URL", "https://159-13-59-184.sslip.io").rstrip("/")
TEMPLATE_PATH = Path(os.environ.get(
    "PRODUCT_TEMPLATE", "/opt/pricewatch/web/product.html"))
LANDING_TEMPLATE_PATH = Path(os.environ.get(
    "LANDING_TEMPLATE", "/opt/pricewatch/web/landing.html"))
CACHE_DIR = Path(os.environ.get("IMG_CACHE_DIR", "/var/cache/pricewatch-img"))
MAX_IMG_BYTES = 8 * 1024 * 1024

ALLOWED_IMG_HOSTS = {
    "cdn.shopify.com",                                   # JB Hi-Fi, Good Guys
    "assets.kmart.com.au", "kmartau.mo.cloudinary.net",  # Kmart
    "s3-ap-southeast-2.amazonaws.com",                   # Officeworks PIM
    "www.supercheapauto.com.au",                         # Supercheap (demandware)
    "static.chemistwarehouse.com.au",                    # Chemist Warehouse
    "image-optimizer-reg.production.sephora-asia.net",   # Sephora
    "myer-media.com.au",                                 # Myer
}

RETAILER_LABEL = {"kmart": "Kmart", "bigw": "Big W", "target": "Target",
                  "officeworks": "Officeworks", "jbhifi": "JB Hi-Fi",
                  "goodguys": "The Good Guys", "myer": "Myer",
                  "supercheap": "Supercheap Auto", "sephora": "Sephora",
                  "chemistwarehouse": "Chemist Warehouse"}

app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
client = httpx.AsyncClient(timeout=20, follow_redirects=True)


async def fetch_product(retailer: str, sku: str) -> dict | None:
    r = await client.get(
        f"{SUPABASE_URL}/rest/v1/product_search",
        params={"retailer": f"eq.{retailer}", "sku": f"eq.{sku}", "limit": 1},
        headers={"apikey": SUPABASE_ANON_KEY,
                 "Authorization": f"Bearer {SUPABASE_ANON_KEY}"})
    rows = r.json() if r.status_code == 200 else []
    return rows[0] if rows else None


CATEGORY_LABEL = {
    "tech": "Tech", "home": "Home", "kitchen": "Kitchen",
    "toys": "Toys & Baby", "clothing": "Clothing", "beauty": "Beauty",
    "books": "Books & Stationery", "other": "Other",
}


def _product_path(retailer: str, sku: str) -> str:
    return f"/p/{quote(retailer, safe='')}/{quote(str(sku), safe='')}"


def _landing_card(row: dict) -> str:
    retailer = str(row.get("retailer") or "")
    sku = str(row.get("sku") or "")
    label = RETAILER_LABEL.get(retailer, retailer.title())
    title = str(row.get("title") or "Price history")
    price = float(row.get("price") or 0)
    reference = float(row.get("reference_price") or 0)
    pct_off = int(round(float(row.get("pct_off") or 0)))
    image = row.get("image_url") or ""
    if image:
        image = f"{SELF_URL}/img?u={quote(str(image), safe='')}"
        media = (f'<img src="{html.escape(image, quote=True)}" loading="lazy" '
                 f'alt="{html.escape(title, quote=True)}">')
    else:
        media = f'<span class="mono">{html.escape((label or "?")[0])}</span>'
    was = (f'<span class="cwas">${reference:.2f}</span>' if reference > price
           else "")
    save = (f'<span class="save">Save ${reference - price:.2f}</span>'
            if reference > price else "")
    href = _product_path(retailer, sku)
    return f'''<article class="card">
  <a class="card-link" href="{html.escape(href, quote=True)}" aria-label="View price history for {html.escape(title, quote=True)}"></a>
  <div class="ph"><span class="badge">-{pct_off}%</span>{media}</div>
  <div class="cbody"><div class="cname">{html.escape(title)}</div>
    <div class="cprices"><span class="cnow">${price:.2f}</span>{was}</div>
    <div class="cfoot"><span>{html.escape(label)}</span>{save}</div>
    <a class="track-btn" href="{html.escape(href, quote=True)}">View price history</a>
  </div>
</article>'''


async def _fetch_landing_deals(field: str, value: str) -> list[dict]:
    r = await client.get(
        f"{SUPABASE_URL}/rest/v1/discount_feed",
        params={"select": "retailer,sku,title,category,image_url,price,reference_price,pct_off,price_updated_at",
                field: f"eq.{value}", "order": "pct_off.desc,reference_price.desc",
                "limit": 36},
        headers={"apikey": SUPABASE_ANON_KEY,
                 "Authorization": f"Bearer {SUPABASE_ANON_KEY}"})
    if r.status_code != 200:
        raise HTTPException(503, "deal feed unavailable")
    return r.json()


async def _landing_page(kind: str, value: str):
    if kind == "category":
        label = CATEGORY_LABEL.get(value)
        field = "category"
        if not label:
            raise HTTPException(404)
        heading = f"{label} deals in Australia"
        description = (f"Compare current {label.lower()} deals from major Australian retailers. "
                       "See tracked price history before you buy.")
        canonical = f"{SITE_URL}/deals/{quote(value, safe='')}"
    else:
        label = RETAILER_LABEL.get(value)
        field = "retailer"
        if not label:
            raise HTTPException(404)
        heading = f"{label} deals and price history"
        description = (f"See current {label} price drops, compare discounts, and review "
                       "tracked price history on Dealwatch.")
        canonical = f"{SITE_URL}/retailers/{quote(value, safe='')}"

    rows = await _fetch_landing_deals(field, value)
    title = f"{heading} | Dealwatch"
    items = [{"@type": "ListItem", "position": i, "url": f"{SITE_URL}{_product_path(row['retailer'], row['sku'])}"}
             for i, row in enumerate(rows, 1) if row.get("retailer") and row.get("sku")]
    jsonld = {"@context": "https://schema.org", "@type": "CollectionPage",
              "name": title, "url": canonical, "description": description,
              "mainEntity": {"@type": "ItemList", "itemListElement": items}}
    template = LANDING_TEMPLATE_PATH.read_text(encoding="utf-8")
    rendered = (template.replace("{{base_url}}", html.escape(SITE_URL, quote=True))
                .replace("{{title}}", html.escape(title, quote=True))
                .replace("{{description}}", html.escape(description, quote=True))
                .replace("{{canonical}}", html.escape(canonical, quote=True))
                .replace("{{heading}}", html.escape(heading))
                .replace("{{jsonld}}", json.dumps(jsonld).replace("</", "<\\/"))
                .replace("{{cards}}", "\n".join(_landing_card(row) for row in rows)
                 or '<p class="empty">No current deals are available for this page yet.</p>'))
    return Response(rendered, media_type="text/html",
                    headers={"Cache-Control": "public, max-age=600"})


@app.get("/deals/{category}")
async def deals_landing(category: str):
    return await _landing_page("category", category)


@app.get("/retailers/{retailer}")
async def retailer_landing(retailer: str):
    return await _landing_page("retailer", retailer)

@app.get("/p/{retailer}/{sku}")
async def preview(retailer: str, sku: str):
    if retailer not in RETAILER_LABEL or not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", sku):
        raise HTTPException(404)
    p = await fetch_product(retailer, sku)
    if p is None:
        raise HTTPException(404)
    template = TEMPLATE_PATH.read_text(encoding="utf-8")

    title = f"{p['title']} — {RETAILER_LABEL[retailer]} price | Dealwatch"
    price = f"${float(p['current_price']):.2f}" if p.get("current_price") else ""
    desc = (f"{price} at {RETAILER_LABEL[retailer]}. Price history and "
            f"drop alerts for {p['title']} on Dealwatch, the Australian "
            f"price tracker.")
    canonical = f"{SITE_URL}/p/{retailer}/{sku}"
    image = p.get("image_url") or ""
    if image:
        image = f"{SELF_URL}/img?u={quote(image, safe='')}"

    e = html.escape
    meta = "\n".join(filter(None, [
        f'<base href="{SITE_URL}/">',
        f'<link rel="canonical" href="{e(canonical)}">',
        f'<meta name="description" content="{e(desc)}">',
        f'<meta property="og:type" content="product">',
        f'<meta property="og:title" content="{e(title)}">',
        f'<meta property="og:description" content="{e(desc)}">',
        f'<meta property="og:url" content="{e(canonical)}">',
        f'<meta property="og:image" content="{e(image)}">' if image else "",
        f'<meta name="twitter:card" content="summary_large_image">' if image
        else '<meta name="twitter:card" content="summary">',
        f'<meta property="product:price:amount" content="{float(p["current_price"]):.2f}">'
        if p.get("current_price") else "",
        '<meta property="product:price:currency" content="AUD">',
    ]))

    if p.get("current_price"):
        jsonld = {
            "@context": "https://schema.org",
            "@type": "Product",
            "name": p["title"],
            "description": desc,
            "sku": str(sku),
            "url": canonical,
            "offers": {
                "@type": "Offer",
                "price": f"{float(p['current_price']):.2f}",
                "priceCurrency": "AUD",
                "url": p.get("url") or canonical,
                "seller": {"@type": "Organization", "name": RETAILER_LABEL[retailer]},
            },
        }
        if image:
            jsonld["image"] = image
        if p.get("brand"):
            jsonld["brand"] = {"@type": "Brand", "name": p["brand"]}
        meta += (f'\n<script type="application/ld+json">'
                 f'{json.dumps(jsonld).replace("</", "<\\/")}</script>')

    out = template.replace("<title>", f"{meta}\n<title>", 1)
    out = re.sub(r"<title>.*?</title>", f"<title>{e(title)}</title>", out,
                 count=1, flags=re.S)
    return Response(out, media_type="text/html",
                    headers={"Cache-Control": "public, max-age=600"})


@app.get("/img")
async def img(u: str = Query(..., max_length=1000)):
    m = re.match(r"^https://([a-z0-9.-]+)/", u)
    if not m or m.group(1) not in ALLOWED_IMG_HOSTS:
        raise HTTPException(403, "host not allowed")
    key = hashlib.sha256(u.encode()).hexdigest()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    body_f, type_f = CACHE_DIR / key, CACHE_DIR / (key + ".type")
    if body_f.exists():
        ctype = type_f.read_text() if type_f.exists() else "image/jpeg"
        return Response(body_f.read_bytes(), media_type=ctype,
                        headers={"Cache-Control": "public, max-age=2592000, immutable"})
    try:
        r = await client.get(u)
    except httpx.HTTPError:
        raise HTTPException(502, "upstream fetch failed")
    ctype = r.headers.get("content-type", "")
    if r.status_code != 200 or not ctype.startswith("image/") \
            or len(r.content) > MAX_IMG_BYTES:
        raise HTTPException(502, "not a cacheable image")
    tmp = body_f.with_suffix(".tmp")
    tmp.write_bytes(r.content)
    tmp.replace(body_f)                       # atomic under concurrent hits
    type_f.write_text(ctype)
    return Response(r.content, media_type=ctype,
                    headers={"Cache-Control": "public, max-age=2592000, immutable"})


SITEMAP_PAGE_SIZE = 1_000  # Supabase/PostgREST enforces a hard 1000-row
                            # db-max-rows cap regardless of a larger `limit`
                            # param - requesting more here would silently
                            # leave gaps between pages instead of increasing
                            # rows returned. Confirmed live 17 July: a
                            # limit=5000 request still came back as 1000 rows.
SITEMAP_PAGE_COUNT = 5
SITEMAP_TTL = 24 * 3600  # cache once daily: enough freshness without egress churn


async def _sitemap_products(page: int):
    if page < 1 or page > SITEMAP_PAGE_COUNT:
        raise HTTPException(404)
    cache_file = CACHE_DIR / f"sitemap-products-{page}.xml"
    if cache_file.exists() and time.time() - cache_file.stat().st_mtime < SITEMAP_TTL:
        return Response(cache_file.read_bytes(), media_type="application/xml")

    r = await client.get(
        f"{SUPABASE_URL}/rest/v1/product_search",
        params={"select": "retailer,sku,price_updated_at",
                "order": "price_updated_at.desc",
                "limit": SITEMAP_PAGE_SIZE,
                "offset": (page - 1) * SITEMAP_PAGE_SIZE},
        headers={"apikey": SUPABASE_ANON_KEY,
                 "Authorization": f"Bearer {SUPABASE_ANON_KEY}"})
    if r.status_code != 200:
        raise HTTPException(503, "product sitemap unavailable")
    rows = r.json()
    urls = "\n".join(
        f"  <url><loc>{html.escape(SITE_URL)}/p/{html.escape(quote(str(row['retailer']), safe=''))}/{html.escape(quote(str(row['sku']), safe=''))}</loc>"
        f"<lastmod>{html.escape(str(row['price_updated_at'])[:10])}</lastmod></url>"
        for row in rows if row.get("retailer") and row.get("sku") and row.get("price_updated_at"))
    body = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"{urls}\n</urlset>\n").encode()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = cache_file.with_suffix(".tmp")
    tmp.write_bytes(body)
    tmp.replace(cache_file)
    return Response(body, media_type="application/xml")


@app.get("/sitemap-products.xml")
async def sitemap_products():
    """Legacy first-page endpoint retained for previously discovered URLs."""
    return await _sitemap_products(1)


@app.get("/sitemap-products-{page}.xml")
async def sitemap_products_page(page: int):
    return await _sitemap_products(page)

@app.get("/healthz")
async def healthz():
    return {"ok": True}
