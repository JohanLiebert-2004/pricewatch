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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response

SITE_URL = os.environ.get("SITE_URL", "https://dealwatch.com.au").rstrip("/")
# PostgREST is reachable through the public site's same-origin /rest rewrite.
# PRICEWATCH_API_URL remains available for a private/internal origin, but no
# infrastructure IP is baked into application code.
SUPABASE_URL = os.environ.get("PRICEWATCH_API_URL", SITE_URL).rstrip("/")
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
    "www.booktopia.com.au", "www.qbd.com.au",           # book retailers
    "www.ikea.com",                                       # IKEA
}

RETAILER_LABEL = {"kmart": "Kmart", "bigw": "Big W", "target": "Target",
                  "officeworks": "Officeworks", "jbhifi": "JB Hi-Fi",
                  "goodguys": "The Good Guys", "myer": "Myer",
                  "supercheap": "Supercheap Auto", "sephora": "Sephora",
                  "chemistwarehouse": "Chemist Warehouse", "booktopia": "Booktopia", "qbd": "QBD Books", "ikea": "IKEA"}

app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
client = httpx.AsyncClient(timeout=20, follow_redirects=True)


@app.get("/healthz", include_in_schema=False)
async def healthz():
    """Cheap process health check; does not turn a DB outage into a restart loop."""
    return Response("ok\n", media_type="text/plain")


async def fetch_product(retailer: str, sku: str) -> dict | None:
    r = await client.get(
        f"{SUPABASE_URL}/rest/v1/product_search",
        params={"retailer": f"eq.{retailer}", "sku": f"eq.{sku}", "limit": 1})
    if r.status_code != 200:
        # A temporary API failure is not the same thing as a missing product.
        # Returning 404 here teaches crawlers to discard a valid URL during a
        # database outage; 503 explicitly asks them to retry it later.
        raise HTTPException(503, "product data temporarily unavailable")
    try:
        rows = r.json()
    except ValueError as exc:
        raise HTTPException(503, "product data temporarily unavailable") from exc
    return rows[0] if rows else None


CATEGORY_LABEL = {
    "tech": "Tech", "home": "Home", "kitchen": "Kitchen",
    "toys": "Toys & Baby", "clothing": "Clothing", "beauty": "Beauty",
    "books": "Books", "other": "Other",
}


def _product_path(retailer: str, sku: str) -> str:
    return f"/p/{quote(retailer, safe='')}/{quote(str(sku), safe='')}"


def _parsed_time(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)
    except (TypeError, ValueError):
        return None


def _is_recent(value, hours: int = 36) -> bool:
    parsed = _parsed_time(value)
    return bool(parsed and parsed >= datetime.now(timezone.utc) - timedelta(hours=hours))


def _display_date(value) -> str:
    parsed = _parsed_time(value)
    return f"{parsed.day} {parsed.strftime('%B %Y')}" if parsed else ""


def _safe_http_url(value, fallback: str) -> str:
    value = str(value or "")
    return value if re.match(r"^https?://", value, re.I) else fallback


def _product_ssr_body(p: dict, retailer: str, sku: str, canonical: str,
                      image: str) -> str:
    """Meaningful initial HTML for crawlers and visitors before JS hydrates."""
    e = html.escape
    label = RETAILER_LABEL[retailer]
    title = str(p.get("title") or "Price history")
    category = str(p.get("category") or "other")
    category_label = CATEGORY_LABEL.get(category, category.title())
    price = float(p["current_price"]) if p.get("current_price") else None
    rrp = float(p["current_rrp"]) if p.get("current_rrp") else None
    retailer_url = _safe_http_url(p.get("url"), canonical)
    checked = _display_date(p.get("last_seen"))
    freshness = (f"Last checked {checked}." if checked else
                 "The latest recorded price is shown below.")
    media = (f'<img src="{e(image, quote=True)}" width="320" height="400" alt="{e(title, quote=True)}" '
             'loading="eager" fetchpriority="high">' if image else
             f'<span class="mono">{e((label or "?")[0])}</span>')
    price_html = f'<div class="now">${price:.2f}</div>' if price is not None else ""
    was_html = (f'<div class="was">${rrp:.2f}</div>'
                if price is not None and rrp is not None and rrp > price else "")
    category_link = (f'<a href="/deals/{quote(category, safe="")}">{e(category_label)}</a>'
                     if category in CATEGORY_LABEL else e(category_label))
    return f'''<nav class="landing-links" aria-label="Breadcrumb">
  <a href="/">Deals</a><a href="/retailers/{quote(retailer, safe='')}">{e(label)}</a>{category_link}
</nav>
<article class="pblock">
  <div class="pthumb">{media}</div>
  <div class="pinfo"><div class="ptitle">{e(title)}</div>
    <div class="pmeta"><span class="pill">{e(label)}</span><span class="pill">{e(category_label)}</span><span class="pill">SKU {e(str(sku))}</span></div>
    <p class="hint">{e(freshness)} Dealwatch records changes so you can compare this price with its history.</p>
  </div>
  <div class="pprice">{price_html}{was_html}<a href="{e(retailer_url, quote=True)}" target="_blank" rel="noopener">View at {e(label)}</a></div>
</article>'''


def _base_origin(request: Request) -> str:
    """The origin that actually served this request, for the <base> tag.

    Must NOT be hardcoded to SITE_URL: the page's CSP sends
    `base-uri 'self'`, so a <base href> pointing at a different origin
    than the one the browser thinks it's on gets silently blocked,
    breaking every relative resource (style.css, etc.) on the page. This
    bit a real user 17 July when a www->apex redirect briefly lapsed and
    www-served pages got a base tag pointing at the bare apex - a
    different origin under CSP. Deriving it from the actual request
    means it's correct regardless of which host (apex, www, or a future
    alias) the request came in on.
    """
    proto = request.headers.get("x-forwarded-proto", "https")
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    return f"{proto}://{host}" if host else SITE_URL


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
        image = f"{SITE_URL}/img?u={quote(str(image), safe='')}"
        media = (f'<img src="{html.escape(image, quote=True)}" width="320" height="320" loading="lazy" '
                 f'alt="{html.escape(title, quote=True)}">')
    else:
        media = f'<span class="mono">{html.escape((label or "?")[0])}</span>'
    was = (f'<span class="cwas">${reference:.2f}</span>' if reference > price
           else "")
    save = (f'<span class="save">Save ${reference - price:.2f}</span>'
            if reference > price else "")
    lowest = '<span class="badge lowest">Lowest Price!</span>' if row.get("is_30d_low") else ""
    href = _product_path(retailer, sku)
    return f'''<article class="card">
  <a class="card-link" href="{html.escape(href, quote=True)}" aria-label="View price history for {html.escape(title, quote=True)}"></a>
  <div class="ph"><span class="badge">-{pct_off}%</span>{lowest}{media}</div>
  <div class="cbody"><div class="cname">{html.escape(title)}</div>
    <div class="cprices"><span class="cnow">${price:.2f}</span>{was}</div>
    <div class="cfoot"><span>{html.escape(label)}</span>{save}</div>
    <a class="track-btn" href="{html.escape(href, quote=True)}">View price history</a>
  </div>
</article>'''


async def _fetch_landing_deals(field: str, value: str) -> list[dict]:
    r = await client.get(
        f"{SUPABASE_URL}/rest/v1/discount_feed",
        params={"select": "retailer,sku,title,category,image_url,price,reference_price,pct_off,price_updated_at,is_30d_low",
                field: f"eq.{value}", "order": "pct_off.desc,reference_price.desc",
                "limit": 36})
    if r.status_code != 200:
        raise HTTPException(503, "deal feed unavailable")
    return r.json()


def _landing_insight(kind: str, rows: list[dict]) -> str:
    """A compact, data-backed summary instead of generic SEO filler."""
    if not rows:
        return ("No recently verified discounts are available here right now. "
                "Dealwatch will update this page when fresh prices arrive.")
    prices = [float(row["price"]) for row in rows if row.get("price") is not None]
    discounts = [float(row["pct_off"]) for row in rows if row.get("pct_off") is not None]
    if kind == "category":
        dimensions = {row.get("retailer") for row in rows if row.get("retailer")}
        scope = f"across {len(dimensions)} retailer{'s' if len(dimensions) != 1 else ''}"
    else:
        dimensions = {row.get("category") for row in rows if row.get("category")}
        scope = f"across {len(dimensions)} categor{'ies' if len(dimensions) != 1 else 'y'}"
    facts = [f"This page highlights {len(rows)} recently checked deal{'s' if len(rows) != 1 else ''} {scope}"]
    if prices:
        facts.append(f"current prices start at ${min(prices):.2f}")
    if discounts:
        facts.append(f"the largest displayed discount is {round(max(discounts))}%")
    return "; ".join(facts) + ". Open a product to verify its recorded price history."


async def _landing_page(request: Request, kind: str, value: str):
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
    rendered = (template.replace("{{base_url}}", html.escape(_base_origin(request), quote=True))
                .replace("{{title}}", html.escape(title, quote=True))
                .replace("{{description}}", html.escape(description, quote=True))
                .replace("{{canonical}}", html.escape(canonical, quote=True))
                .replace("{{heading}}", html.escape(heading))
                .replace("{{jsonld}}", json.dumps(jsonld).replace("</", "<\\/"))
                .replace("{{insight}}", html.escape(_landing_insight(kind, rows)))
                .replace("{{cards}}", "\n".join(_landing_card(row) for row in rows)
                 or '<p class="empty">No current deals are available for this page yet.</p>'))
    return Response(rendered, media_type="text/html",
                    headers={"Cache-Control": "public, max-age=600"})


@app.get("/deals/{category}")
async def deals_landing(request: Request, category: str):
    return await _landing_page(request, "category", category)


@app.get("/retailers/{retailer}")
async def retailer_landing(request: Request, retailer: str):
    return await _landing_page(request, "retailer", retailer)

@app.get("/p/{retailer}/{sku}")
async def preview(request: Request, retailer: str, sku: str):
    if retailer not in RETAILER_LABEL or not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", sku):
        raise HTTPException(404)
    p = await fetch_product(retailer, sku)
    if p is None:
        raise HTTPException(404)
    template = TEMPLATE_PATH.read_text(encoding="utf-8")

    product_name = str(p.get("title") or "Product")
    retailer_label = RETAILER_LABEL[retailer]
    title = f"{product_name} — {retailer_label} price | Dealwatch"
    active_price = (float(p["current_price"])
                    if p.get("current_price") and float(p["current_price"]) > 0
                    else None)
    if active_price is not None:
        desc = (f"${active_price:.2f} at {retailer_label}. Price history and "
                f"drop alerts for {product_name} on Dealwatch, the Australian "
                f"price tracker.")
    else:
        desc = (f"Recorded {retailer_label} price history and drop alerts for "
                f"{product_name} on Dealwatch, the Australian price tracker.")
    canonical = f"{SITE_URL}/p/{retailer}/{sku}"
    image = _safe_http_url(p.get("image_url"), "")
    if image:
        image = f"{SITE_URL}/img?u={quote(image, safe='')}"

    e = html.escape
    meta = "\n".join(filter(None, [
        f'<base href="{_base_origin(request)}/">',
        f'<link rel="canonical" href="{e(canonical)}">',
        f'<meta property="og:type" content="product">',
        f'<meta property="og:title" content="{e(title)}">',
        f'<meta property="og:description" content="{e(desc)}">',
        f'<meta property="og:url" content="{e(canonical)}">',
        f'<meta property="og:image" content="{e(image)}">' if image else "",
        f'<meta name="twitter:card" content="summary_large_image">' if image
        else '<meta name="twitter:card" content="summary">',
        f'<meta property="product:price:amount" content="{active_price:.2f}">'
        if active_price is not None else "",
        '<meta property="product:price:currency" content="AUD">',
    ]))

    category = str(p.get("category") or "other")
    category_label = CATEGORY_LABEL.get(category, category.title())
    breadcrumbs = {
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Deals", "item": f"{SITE_URL}/"},
            {"@type": "ListItem", "position": 2, "name": retailer_label,
             "item": f"{SITE_URL}/retailers/{quote(retailer, safe='')}"},
            {"@type": "ListItem", "position": 3, "name": category_label,
             "item": f"{SITE_URL}/deals/{quote(category, safe='')}"},
            {"@type": "ListItem", "position": 4, "name": product_name,
             "item": canonical},
        ],
    }
    graph = [breadcrumbs]

    # Google flags 'image' as a critical missing field for Product rich
    # results. Rather than fake one, only claim Product/Offer eligibility for
    # listings we can actually back with a real product photo - image-less
    # products (a chunk of Big W's bulk-listing lane, see P9) just don't get
    # the JSON-LD block, same as if they had no price.
    if active_price is not None and image:
        product = {
            "@type": "Product",
            "name": product_name,
            "description": desc,
            "sku": str(sku),
            "url": canonical,
            "image": image,
            "offers": {
                "@type": "Offer",
                "price": f"{active_price:.2f}",
                "priceCurrency": "AUD",
                "url": _safe_http_url(p.get("url"), canonical),
                "seller": {"@type": "Organization", "name": retailer_label},
            },
        }
        # A non-zero price alone is not proof of current stock. Only publish
        # InStock when the crawler has confirmed the listing recently; omit
        # availability for older rows instead of making an unsupported claim.
        if _is_recent(p.get("last_seen")):
            product["offers"]["availability"] = "https://schema.org/InStock"
        if p.get("brand"):
            product["brand"] = {"@type": "Brand", "name": p["brand"]}
        graph.insert(0, product)
        # Deliberately NOT adding hasMerchantReturnPolicy, shippingDetails or
        # review/aggregateRating: we don't hold verified per-retailer return/
        # shipping terms, and we have no real review data to report -
        # fabricating any of these would be inaccurate structured data (and
        # fake reviews specifically risk a Google manual action). Google
        # lists all three as non-critical suggestions, not blockers.
    jsonld = {"@context": "https://schema.org", "@graph": graph}
    meta += (f'\n<script type="application/ld+json">'
             f'{json.dumps(jsonld).replace("</", "<\\/")}</script>')

    out = template.replace("<title>", f"{meta}\n<title>", 1)
    out = re.sub(r"<title>.*?</title>", f"<title>{e(title)}</title>", out,
                 count=1, flags=re.S)
    out = re.sub(r'<meta name="description" content="[^"]*">',
                 f'<meta name="description" content="{e(desc, quote=True)}">',
                 out, count=1)
    out = out.replace('<h1 class="display">Price <em>history</em></h1>',
                      f'<h1 class="display">{e(product_name)}</h1>', 1)
    product_sub = (f"Current {retailer_label} price, recorded history and an "
                   "optional alert when the price drops.")
    out = re.sub(r'<p class="sub">.*?</p>',
                 f'<p class="sub">{e(product_sub)}</p>', out,
                 count=1, flags=re.S)
    ssr_body = _product_ssr_body(p, retailer, sku, canonical, image)
    out = re.sub(r'(<main class="wrap" id="main">).*?(</main>)',
                 lambda match: f"{match.group(1)}\n{ssr_body}\n{match.group(2)}",
                 out, count=1, flags=re.S)
    # The response itself contains no token-specific or user-specific data;
    # watch tokens remain in the URL and are handled client-side. A short CDN
    # cache reduces crawler load on the 1GB SSR host without serving stale
    # prices for long.
    return Response(out, media_type="text/html",
                    headers={"Cache-Control":
                             "public, max-age=300, s-maxage=300, stale-while-revalidate=60"})


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


SITEMAP_PAGE_SIZE = 1_000  # PostgREST is configured with a 1000-row limit
                            # db-max-rows cap regardless of a larger `limit`
                            # param - requesting more here would silently
                            # leave gaps between pages instead of increasing
                            # rows returned. Confirmed live 17 July: a
                            # limit=5000 request still came back as 1000 rows.
# A hardcoded page count (previously 25, i.e. 25,000 URLs) went stale the
# moment the catalogue grew past it: with ~330k+ fresh rows and the sitemap
# ordered by last_seen desc, the 25k cap was silently 100% Kmart (the most
# continuously-swept retailer) - every other retailer, including Myer's
# ~118k-item catalogue, had near-zero sitemap presence and effectively no
# organic discovery path. Compute the real page count from the live row
# count instead, so coverage tracks the catalogue automatically. Clamped to
# a sane ceiling as a runaway-growth safety valve, not an expected limit.
SITEMAP_PAGE_COUNT_CEILING = 3_000
SITEMAP_FRESH_DAYS = 30  # Exclude abandoned catalogue rows from discovery.
SITEMAP_TTL = 24 * 3600  # cache once daily: enough freshness without egress churn
SITEMAP_CACHE_VERSION = 2  # Do not serve pre-freshness-filter cache files.

_sitemap_page_count_cache = {"value": None, "at": 0.0}


def _sitemap_fresh_after() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=SITEMAP_FRESH_DAYS)).isoformat()


async def _sitemap_page_count() -> int:
    now = time.time()
    if (_sitemap_page_count_cache["value"] is not None
            and now - _sitemap_page_count_cache["at"] < SITEMAP_TTL):
        return _sitemap_page_count_cache["value"]
    r = await client.get(
        f"{SUPABASE_URL}/rest/v1/product_search",
        params={"select": "retailer", "current_price": "gt.0",
                "last_seen": f"gte.{_sitemap_fresh_after()}", "limit": 1},
        headers={"Prefer": "count=exact", "Range": "0-0"})
    total = None
    content_range = r.headers.get("content-range", "")
    if "/" in content_range:
        tail = content_range.split("/", 1)[1]
        if tail != "*":
            total = int(tail)
    if total is None:
        # Count unavailable (transient error) - fall back to the last known
        # good value, or a conservative single page if there isn't one yet.
        return _sitemap_page_count_cache["value"] or 1
    pages = max(1, min(SITEMAP_PAGE_COUNT_CEILING,
                        -(-total // SITEMAP_PAGE_SIZE)))  # ceil div
    _sitemap_page_count_cache.update(value=pages, at=now)
    return pages


async def _sitemap_products(page: int):
    page_count = await _sitemap_page_count()
    if page < 1 or page > page_count:
        raise HTTPException(404)
    cache_file = CACHE_DIR / f"sitemap-products-v{SITEMAP_CACHE_VERSION}-{page}.xml"
    if cache_file.exists() and time.time() - cache_file.stat().st_mtime < SITEMAP_TTL:
        return Response(cache_file.read_bytes(), media_type="application/xml")

    r = await client.get(
        f"{SUPABASE_URL}/rest/v1/product_search",
        params={"select": "retailer,sku,price_updated_at,last_seen",
                "current_price": "gt.0",
                "last_seen": f"gte.{_sitemap_fresh_after()}",
                "order": "last_seen.desc,retailer.asc,sku.asc",
                "limit": SITEMAP_PAGE_SIZE,
                "offset": (page - 1) * SITEMAP_PAGE_SIZE})
    if r.status_code != 200:
        raise HTTPException(503, "product sitemap unavailable")
    rows = r.json()
    urls = "\n".join(
        f"  <url><loc>{html.escape(SITE_URL)}/p/{html.escape(quote(str(row['retailer']), safe=''))}/{html.escape(quote(str(row['sku']), safe=''))}</loc>"
        f"<lastmod>{html.escape(str(row.get('price_updated_at') or row['last_seen'])[:10])}</lastmod></url>"
        for row in rows if row.get("retailer") and row.get("sku") and
        (row.get("price_updated_at") or row.get("last_seen")))
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


@app.get("/sitemap.xml")
async def sitemap_index():
    """Sitemap index. Product page count is computed live (see
    _sitemap_page_count) instead of hand-maintained, so this stays complete
    as the catalogue grows - a static list previously went stale at 25
    pages, silently freezing sitemap coverage at ~7% of the catalogue."""
    page_count = await _sitemap_page_count()
    entries = [f"{SITE_URL}/sitemap-pages.xml"] + [
        f"{SITE_URL}/sitemap-products-{i}.xml" for i in range(1, page_count + 1)]
    body = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "\n".join(f"  <sitemap><loc>{html.escape(u)}</loc></sitemap>" for u in entries)
            + "\n</sitemapindex>\n").encode()
    return Response(body, media_type="application/xml",
                     headers={"Cache-Control": f"public, max-age={SITEMAP_TTL}"})


@app.get("/healthz")
async def healthz():
    return {"ok": True}
