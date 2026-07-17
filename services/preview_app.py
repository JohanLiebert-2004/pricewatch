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
            "url": canonical,
            "offers": {
                "@type": "Offer",
                "price": f"{float(p['current_price']):.2f}",
                "priceCurrency": "AUD",
                "availability": "https://schema.org/InStock",
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


SITEMAP_CACHE = CACHE_DIR / "sitemap-products.xml"
SITEMAP_TTL = 6 * 3600  # matches the hourly crawl cadence with headroom; keeps
                         # this off the Supabase egress budget on repeat hits


@app.get("/sitemap-products.xml")
async def sitemap_products():
    if SITEMAP_CACHE.exists() and time.time() - SITEMAP_CACHE.stat().st_mtime < SITEMAP_TTL:
        return Response(SITEMAP_CACHE.read_bytes(), media_type="application/xml")
    r = await client.get(
        f"{SUPABASE_URL}/rest/v1/product_search",
        params={"select": "retailer,sku,price_updated_at",
                "order": "price_updated_at.desc", "limit": 5000},
        headers={"apikey": SUPABASE_ANON_KEY,
                 "Authorization": f"Bearer {SUPABASE_ANON_KEY}"})
    if r.status_code != 200:
        # Don't cache a transient upstream failure - serve the last good
        # sitemap if we have one rather than baking in an empty file for
        # SITEMAP_TTL, which would silently de-index the whole catalogue.
        if SITEMAP_CACHE.exists():
            return Response(SITEMAP_CACHE.read_bytes(), media_type="application/xml")
        raise HTTPException(502, "sitemap source unavailable")
    rows = r.json()
    e = html.escape
    urls = "\n".join(
        f"  <url><loc>{e(SITE_URL)}/p/{e(row['retailer'])}/{e(row['sku'])}</loc>"
        f"<lastmod>{e(row['price_updated_at'][:10])}</lastmod></url>"
        for row in rows if row.get("retailer") and row.get("sku"))
    body = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"{urls}\n</urlset>\n").encode()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SITEMAP_CACHE.with_suffix(".tmp")
    tmp.write_bytes(body)
    tmp.replace(SITEMAP_CACHE)
    return Response(body, media_type="application/xml")


@app.get("/healthz")
async def healthz():
    return {"ok": True}
