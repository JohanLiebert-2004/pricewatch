"""Amazon AU deal discovery, conservative anomaly detection, and local reporting."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html import escape, unescape
import json
import os
from pathlib import Path
import re
import sqlite3
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("AMAZON_WATCH_DATA_DIR", str(ROOT / "data")))
UA = "BlackFridayWatch/0.1 (personal deal research)"
ASIN_RE = re.compile(r'(?:/dp/|/gp/product/|["\']asin["\']\s*:\s*["\']|[?&]asin=)([A-Z0-9]{10})\b')


def now():
    return datetime.now(timezone.utc)


def iso(value=None):
    return (value or now()).isoformat()


def date(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except (ValueError, AttributeError, TypeError):
        return None


def money(text):
    """Accept AUD prices only; return integer cents, never binary floats."""
    if re.search(r'\b(?:US|USD|NZ|NZD|EUR|GBP)\b|US\$|NZ\$|[£€]', text, re.I):
        return None
    match = re.search(r'(?:A\$|AU\$|AUD\s*|\$)\s*(\d[\d,]*(?:\.\d{1,2})?)\b', text)
    if not match:
        return None
    try:
        amount = Decimal(match[1].replace(",", ""))
        return int(amount * 100) if amount > 0 else None
    except InvalidOperation:
        return None


class FetchError(Exception):
    pass


class Fetcher:
    def __init__(self, delay=3):
        self.delay = max(3, delay)
        self.last = {}
        self.robots = {}
        self.blocked = set()
        self.requests = 0

    def raw(self, url):
        parsed = urllib.parse.urlsplit(url)
        host = parsed.netloc
        # All configured and discovered network destinations are fixed public hosts.
        if parsed.scheme != "https" or host not in {"www.amazon.com.au", "www.ozbargain.com.au"}:
            raise FetchError("URL outside the configured public source hosts")
        if host in self.blocked:
            raise FetchError(f"{host}: paused for this scan after access denial")
        time.sleep(max(0, self.delay - (time.monotonic() - self.last.get(host, 0))))
        self.last[host] = time.monotonic()
        self.requests += 1
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-AU,en;q=0.9"})
        try:
            with urllib.request.urlopen(req, timeout=25) as response:
                data = response.read(6_000_001)
                if len(data) > 6_000_000:
                    raise FetchError("Response exceeds 6 MB limit")
                return data.decode(response.headers.get_content_charset() or "utf-8", "replace")
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 429, 503):
                self.blocked.add(host)
            raise FetchError(f"{host}: HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise FetchError(f"{host}: {type(exc).__name__}") from None

    def get(self, url):
        parsed = urllib.parse.urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self.robots:
            policy = urllib.robotparser.RobotFileParser()
            policy.parse(self.raw(origin + "/robots.txt").splitlines())
            self.robots[origin] = policy
        if not self.robots[origin].can_fetch(UA, url):
            raise FetchError("Source robots policy disallows this URL")
        text = self.raw(url)
        lower = text.lower()
        if any(marker in lower for marker in ("validatecaptcha", "enter the characters you see below", "sorry, we just need to make sure you're not a robot")):
            self.blocked.add(parsed.netloc)
            raise FetchError("Source returned a verification challenge; paused for this scan")
        return text


def discover_asins(html):
    decoded = unescape(html).replace('\\"', '"')
    return list(dict.fromkeys(ASIN_RE.findall(decoded)))


def parse_feed(xml):
    results = []
    for item in ET.fromstring(xml).findall("./channel/item"):
        title = item.findtext("title", "")
        description = BeautifulSoup(item.findtext("description", ""), "html.parser").get_text(" ", strip=True)
        meta = item.find("{https://www.ozbargain.com.au}meta")
        attrs = meta.attrib if meta is not None else {}
        url = attrs.get("url", "")
        parsed_url = urllib.parse.urlsplit(url)
        if parsed_url.hostname not in {"amazon.com.au", "www.amazon.com.au"}:
            continue
        asins = discover_asins(url)
        if not asins:
            continue  # No guessing product identity from the title.
        expired = bool(re.search(r'\b(?:expired|out of stock)\b', title, re.I))
        expiry = date(attrs.get("expiry"))
        if expiry and expiry <= now():
            expired = True
        # Multi-product titles cannot be mapped safely to a single advertised price.
        amounts = re.findall(r'(?:A\$|AU\$|\$)\s*\d[\d,]*(?:\.\d{1,2})?', title.split(" @ ")[0])
        quoted = money(amounts[0]) if len(amounts) == 1 else None
        results.append({
            "asin": asins[0], "title": title, "source_url": item.findtext("link", ""),
            "amazon_url": f"https://www.amazon.com.au/dp/{asins[0]}",
            "quoted_cents": quoted, "expiry": iso(expiry) if expiry else None,
            "expired": expired,
            "reported_error": bool(re.search(r'\b(?:price|pricing)\s*(?:error|mistake)|\bmispric', title, re.I)),
            "limited": bool(re.search(r'lightning|limited time|flash sale|ends (?:soon|today)', title + " " + description, re.I)),
            "published": item.findtext("pubDate", ""),
            "conditional": bool(re.search(r'coupon|subscribe|\bS&S\b|business|targeted|checkout', title, re.I)),
        })
    return results


def parse_community_listing(html):
    """Read public listing cards, without treating community quotes as Amazon observations."""
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for node in soup.select("div.node-ozbdeal"):
        heading = node.select_one("h2.title")
        link = heading.select_one('a[href^="/node/"]') if heading else None
        destination = node.select_one('a[title^="Go to https://www.amazon.com.au/"]')
        if not link or not destination:
            continue
        asins = discover_asins(destination.get("title", ""))
        if not asins:
            continue
        title = heading.get("data-title") or link.get_text(" ", strip=True)
        content = node.select_one(".content")
        description = content.get_text(" ", strip=True) if content else ""
        # Delivery thresholds and a parenthesised RRP are not additional product prices.
        price_text = re.split(r'\bDelivered\b|\+\s*Delivery|\(RRP|\(Normally|\s@\s', title, maxsplit=1, flags=re.I)[0]
        prices = re.findall(r'(?:A\$|AU\$|\$)\s*\d[\d,]*(?:\.\d{1,2})?', price_text)
        reference_match = re.search(r'(?:RRP|Normally)\s*((?:A\$|\$)[\d,.]+)', title, re.I)
        quoted = money(prices[0]) if len(prices) == 1 else None
        reference = money(reference_match[1]) if reference_match else None
        submitted = node.select_one(".submitted")
        submitted_text = submitted.get_text(" ", strip=True) if submitted else ""
        published_match = re.search(r'(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}:\d{2})', submitted_text)
        published = None
        if published_match:
            published = datetime.strptime(" ".join(published_match.groups()), "%d/%m/%Y %H:%M").replace(tzinfo=timezone(timedelta(hours=11))).isoformat()
        results.append({"asin": asins[0], "title": title, "source_url": "https://www.ozbargain.com.au" + link["href"],
                        "amazon_url": f"https://www.amazon.com.au/dp/{asins[0]}", "quoted_cents": quoted,
                        "reference_cents": reference, "expiry": None, "expired": "expired" in node.get("class", []),
                        "reported_error": bool(re.search(r'\b(?:price|pricing)\s*(?:error|mistake)|\bmispric', title, re.I)),
                        "limited": bool(re.search(r'lightning|limited time|flash sale|ends (?:soon|today)', title + " " + description, re.I)),
                        "conditional": bool(re.search(r'coupon|subscribe|\bS&S\b|business|targeted|checkout', title, re.I)),
                        "published": published, "description": description, "collected": iso()})
    return results


def parse_product(html, asin):
    soup = BeautifulSoup(html, "html.parser")
    title_node = soup.select_one("#productTitle")
    identity = soup.select_one("input#ASIN")
    if not title_node or not identity or identity.get("value", "").upper() != asin:
        raise FetchError("Product identity not confirmed; no price recorded")
    current = None
    for selector in (
        "#corePriceDisplay_desktop_feature_div .priceToPay .a-offscreen",
        "#corePriceDisplay_desktop_feature_div .apex-pricetopay-value .a-offscreen",
        "#corePrice_feature_div .apex-pricetopay-value .a-offscreen",
        "#corePriceDisplay_desktop_feature_div .a-price:not(.a-text-price) .a-offscreen",
        "#corePrice_feature_div .a-price:not(.a-text-price) .a-offscreen",
        "#priceblock_dealprice", "#priceblock_ourprice",
    ):
        node = soup.select_one(selector)
        if node and (current := money(node.get_text(" ", strip=True))):
            break
    reference = None
    for node in soup.select("#corePriceDisplay_desktop_feature_div .a-text-price .a-offscreen"):
        reference = money(node.get_text(" ", strip=True))
        if reference:
            break
    availability = soup.select_one("#availability")
    available_text = availability.get_text(" ", strip=True) if availability else "Unknown"
    unavailable = bool(re.search(r'currently unavailable|out of stock|unavailable for purchase', available_text, re.I))
    seller_node = soup.select_one("#sellerProfileTriggerId")
    seller = seller_node.get_text(" ", strip=True) if seller_node else "Unknown seller"
    seller_id = None
    if seller_node:
        seller_id = urllib.parse.parse_qs(urllib.parse.urlsplit(seller_node.get("href", "")).query).get("seller", [None])[0]
    seller_key = seller_id or (seller if seller != "Unknown seller" else "unknown")
    buying_area = soup.select_one("#apex_desktop") or soup.select_one("#desktop_buybox")
    buying_text = buying_area.get_text(" ", strip=True) if buying_area else ""
    condition = "used" if re.search(r'condition:\s*(?:used|renewed|refurbished)', buying_text, re.I) else "unspecified"
    return {
        "asin": asin, "title": title_node.get_text(" ", strip=True),
        "cents": None if unavailable else current, "reference_cents": reference,
        "seller": seller, "seller_key": seller_key, "condition": condition,
        "availability": available_text, "unavailable": unavailable,
        "limited": bool(re.search(r'lightning deal|limited time deal', buying_text, re.I)),
        "price_scope": "Displayed item price; shipping, coupons and account-specific discounts excluded",
    }


def connect(path=None):
    path = path or DATA / "watch.sqlite3"
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS products(asin TEXT PRIMARY KEY, title TEXT, discovered TEXT);
        CREATE TABLE IF NOT EXISTS leads(asin TEXT, source_url TEXT, payload TEXT, seen TEXT,
          PRIMARY KEY(asin, source_url));
        CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY, asin TEXT, observed TEXT,
          cents INTEGER, seller_key TEXT, payload TEXT);
        CREATE INDEX IF NOT EXISTS observation_lookup ON observations(asin, observed);
        CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY, started TEXT, finished TEXT, payload TEXT);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, detected TEXT, asin TEXT, payload TEXT);
        CREATE TABLE IF NOT EXISTS alert_state(asin TEXT PRIMARY KEY, signature TEXT);
        CREATE TABLE IF NOT EXISTS attempts(job_id TEXT, asin TEXT, attempted TEXT, status TEXT,
          retry_after TEXT, error TEXT, PRIMARY KEY(job_id,asin));
        CREATE INDEX IF NOT EXISTS attempt_lookup ON attempts(asin,attempted);
    """)
    return db


def assess(observation, history, leads, config, at=None):
    at = at or now()
    price = observation.get("cents")
    checked = date(observation.get("observed"))
    fresh = bool(checked and at - checked <= timedelta(minutes=config["freshness_minutes"]))
    result = {"score": 0, "signals": [], "historical_drop_percent": None, "baseline_cents": None,
              "fresh": fresh, "short_lived_observed": False}
    live = bool(fresh and price and not observation.get("unavailable"))
    comparable = [x for x in history if x.get("cents") and x.get("seller_key") == observation.get("seller_key")
                  and x.get("delivery_country") == observation.get("delivery_country")
                  and x.get("delivery_postcode") == observation.get("delivery_postcode")
                  and x.get("condition") == observation.get("condition") and x.get("seller_key") != "unknown"
                  and date(x.get("observed")) and at - timedelta(days=90) <= date(x["observed"]) < at - timedelta(hours=1)]
    # Hourly buckets prevent repeated five-minute polls from overwhelming the baseline.
    buckets = defaultdict(list)
    for x in comparable:
        buckets[date(x["observed"]).strftime("%Y-%m-%dT%H")].append(x["cents"])
    sufficient = len(buckets) >= config["history_minimum_samples"] and comparable and (
        at - min(date(x["observed"]) for x in comparable)).total_seconds() >= config["history_minimum_hours"] * 3600
    if sufficient and live:
        baseline = int(statistics.median(statistics.median(v) for v in buckets.values()))
        drop = round(100 * (baseline - price) / baseline, 1)
        result.update(baseline_cents=baseline, historical_drop_percent=drop)
        if drop >= config["historical_drop_percent"] and baseline - price >= config["minimum_saving_aud"] * 100:
            result["score"] += 80
            result["signals"].append("Possible pricing error: large drop versus this seller's observed history")
    active_leads = [x for x in leads if not x.get("expired") and (not date(x.get("expiry")) or date(x["expiry"]) > at)
                    and price and x.get("quoted_cents") and abs(price - x["quoted_cents"]) <= 1
                    and not x.get("conditional")]
    if live and any(x.get("reported_error") for x in active_leads):
        result["score"] += 35
        result["signals"].append("Community title reports a price error; intent unconfirmed")
    soon = [x for x in active_leads if date(x.get("expiry")) and timedelta(0) < date(x["expiry"]) - at <= timedelta(hours=config["short_lived_hours"])]
    if live and soon:
        result["score"] += 40
        result["signals"].append("Community-reported expiry within six hours")
    if live and (observation.get("limited") or any(x.get("limited") for x in active_leads)):
        result["score"] += 25
        result["signals"].append("Limited-time offer reported; exact end time may be unknown")
    ref = observation.get("reference_cents")
    if live and ref and ref > price:
        advertised = round(100 * (ref - price) / ref, 1)
        result["advertised_discount_percent"] = advertised
        if advertised >= 50:
            result["score"] += min(20, int(advertised / 4))
            result["signals"].append(f"{advertised}% below Amazon's displayed reference price (not price history)")
    # A short-lived observation needs surrounding, same-offer checks. Fetch failures never count as an end.
    ordered = sorted([x for x in history if x.get("seller_key") == observation.get("seller_key")
                      and x.get("condition") == observation.get("condition") and x.get("seller_key") != "unknown"],
                     key=lambda x: x["observed"])
    if fresh and ordered:
        low = ordered[-1]
        low_price = low.get("cents")
        low_at = date(low.get("observed"))
        if low_price and low_at and (observation.get("unavailable") or (price and price >= low_price * 1.5)):
            # Walk back through the entire low-price streak, rather than just the last poll.
            start = low_at
            prior_high = None
            for entry in reversed(ordered[:-1]):
                if not entry.get("cents"):
                    break
                if entry["cents"] >= low_price * 1.5:
                    prior_high = date(entry["observed"])
                    break
                start = date(entry["observed"])
            if prior_high and at - prior_high <= timedelta(hours=config["short_lived_hours"]):
                result["short_lived_observed"] = True
                result["signals"].append("Short-lived drop observed between successful price checks; offer has ended")
                result["observed_low_cents"] = low_price
                result["duration_upper_minutes"] = round((at - prior_high).total_seconds() / 60)
    if not fresh:
        result["signals"].append("No recent successful product check")
    if not price:
        result["signals"].append("No purchasable item price confirmed")
    return result


def generate_report(db, config):
    rows = []
    for product in db.execute("SELECT * FROM products"):
        observations = [json.loads(r["payload"]) for r in db.execute(
            "SELECT payload FROM observations WHERE asin=? ORDER BY observed,id", (product["asin"],))]
        observations = [x for x in observations if not x.get("excluded_from_ranking")]
        leads = [json.loads(r["payload"]) for r in db.execute("SELECT payload FROM leads WHERE asin=?", (product["asin"],))]
        latest = observations[-1] if observations else {"asin": product["asin"], "title": product["title"]}
        matching_workers = sorted({x.get("worker", "Windows laptop") for x in observations
                                   if x.get("cents") and x.get("cents") == latest.get("cents")
                                   and x.get("seller_key") == latest.get("seller_key")
                                   and x.get("delivery_country") == latest.get("delivery_country")
                                   and x.get("delivery_postcode") == latest.get("delivery_postcode")
                                   and x.get("seller_key") != "unknown" and date(x.get("observed"))
                                   and now() - date(x["observed"]) <= timedelta(minutes=config["freshness_minutes"])})
        rating = assess(latest, observations[:-1], leads, config)
        rows.append({**latest, **rating, "asin": product["asin"], "leads": leads,
                     "amazon_url": f'https://www.amazon.com.au/dp/{product["asin"]}', "observations": len(observations),
                     "matching_workers": matching_workers, "double_checked": len(matching_workers) >= 2})
        signature = json.dumps([latest.get("cents"), rating["signals"]], sort_keys=True) if rating["score"] >= 35 or rating["short_lived_observed"] else ""
        prior = db.execute("SELECT signature FROM alert_state WHERE asin=?", (product["asin"],)).fetchone()
        if signature and (not prior or prior[0] != signature):
            db.execute("INSERT INTO events(detected,asin,payload) VALUES(?,?,?)", (iso(), product["asin"], json.dumps(rows[-1])))
        db.execute("INSERT OR REPLACE INTO alert_state VALUES(?,?)", (product["asin"], signature))
    db.commit()
    rows.sort(key=lambda x: (x["score"], bool(x.get("cents")), x.get("advertised_discount_percent", 0)), reverse=True)
    run = db.execute("SELECT payload FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    run = json.loads(run[0]) if run else {}
    events = [{"detected": r["detected"], **json.loads(r["payload"])} for r in db.execute("SELECT * FROM events ORDER BY id DESC LIMIT 100")]
    fleet_path = DATA / "fleet_status.json"
    fleet = json.loads(fleet_path.read_text(encoding="utf-8")) if fleet_path.exists() else {"workers": {}}
    community_leads = [json.loads(r["payload"]) for r in db.execute("SELECT payload FROM leads")]
    payload = {"generated_at": iso(), "marketplace": "Amazon Australia", "currency": "AUD", "last_run": run, "products": rows, "events": events, "fleet": fleet, "community_leads": community_leads}
    if __package__:
        from .publication import public_snapshot, publish_snapshot
    else:
        from publication import public_snapshot, publish_snapshot
    public = public_snapshot(payload)
    payload.update(catalogue=public["catalogue"], coverage=public["coverage"])
    (DATA / "latest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    cards = []
    for row in rows:
        price = f'A${row["cents"] / 100:,.2f}' if row.get("cents") else "Price unconfirmed"
        signals = " ".join(row["signals"]) or "Tracking baseline; no anomaly established."
        source_links = " · ".join(f'<a href="{escape(x["source_url"], quote=True)}" target="_blank" rel="noopener">Community source</a>' for x in row["leads"] if x.get("source_url", "").startswith("https://www.ozbargain.com.au/node/"))
        cards.append(f'''<article><div class="top"><span class="badge">Priority {row['score']}</span><span data-checked="{escape(row.get('observed', ''))}">{'Recently checked' if row['fresh'] else 'Needs fresh check'}</span></div>
<h2><a href="{row['amazon_url']}" target="_blank" rel="noopener">{escape(row.get('title', row['asin']))}</a></h2>
<div class="price">{price}</div><p>{escape(signals)}</p><small>{escape(row.get('seller', 'Seller unconfirmed'))} · {row['observations']} price checks · {escape(row.get('availability', 'Availability unknown'))}<br>Checked: {escape(row.get('observed', 'Never'))}</small><p>{source_links}</p></article>''')
    errors = "<br>".join(escape(x) for x in run.get("errors", []))
    event_html = "".join(f'<li>{escape(x["detected"])} · <a href="{x["amazon_url"]}">{escape(x.get("title", x["asin"]))}</a> · {escape(" ".join(x["signals"]))}</li>' for x in events[:20])
    report = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="60"><title>Black Friday Watch · AU</title>
<style>body{{margin:0;background:#101722;color:#eef3fa;font:16px/1.5 system-ui}}main{{max-width:1200px;margin:auto;padding:32px}}h1{{font-size:36px;margin-bottom:4px}}h2{{font-size:18px}}a{{color:#80d5fc;text-decoration:none}}.muted,small{{color:#b2bfd0}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:18px}}article,.notice{{background:#1b2635;border:1px solid #35465c;border-radius:14px;padding:20px}}.price{{font-size:30px;font-weight:700}}.top{{display:flex;justify-content:space-between;font-size:12px;color:#b2bfd0}}.badge{{color:#b6f4d0}}.notice{{margin:24px 0}}button{{background:#80d5fc;border:0;padding:10px 16px;border-radius:8px;cursor:pointer}}[hidden]{{display:none!important}}</style>
<main><div class="muted">AMAZON AUSTRALIA · AUD · LOCAL PRICE MONITOR</div><h1>Black Friday Watch</h1><p>Find unusual drops. Check the evidence. Act while an offer is available.</p>
<div class="notice">{len(rows)} products discovered · {sum(bool(x.get('cents')) and x['fresh'] for x in rows)} recent prices at generation · {sum(x['score'] >= 35 for x in rows)} priority leads at generation<br>
Generated {escape(payload['generated_at'])}. This report refreshes every minute; the scanner updates it separately.<br>Displayed item prices exclude shipping and account-specific offers. Reference discounts are not historical savings. Coverage is limited to discovered products; no pricing error is guaranteed.</div>
<p><button onclick="document.querySelectorAll('article').forEach(x=>x.hidden=!x.textContent.toLowerCase().includes(document.getElementById('q').value.toLowerCase()))">Search</button> <input id="q" aria-label="Search products" placeholder="Product, seller or signal" style="padding:10px;width:240px;max-width:60%"></p>
<div class="grid">{''.join(cards) or '<p>No products yet. Run a scan to discover offers.</p>'}</div>
<div class="notice"><h2>Detected events</h2><ul>{event_html or '<li>No pricing anomaly or short-lived event established yet.</li>'}</ul>Events remain here after an offer ends.</div>
<div class="notice">Last scan: {escape(str(run.get('checked', 0)))} successful product checks / {escape(str(run.get('attempted', 0)))} attempted.<br>{errors or 'No source errors recorded.'}</div></main>
<script>function freshness(){{document.querySelectorAll('[data-checked]').forEach(x=>{{const age=Date.now()-Date.parse(x.dataset.checked);x.textContent=Number.isFinite(age)&&age<={config['freshness_minutes']}*60000?'Recently checked':'Needs fresh check'}})}}freshness();setInterval(freshness,30000);</script></html>'''
    template = ROOT / "dashboard.html"
    if template.exists():
        snapshot = json.dumps(payload).replace("</", "<\\/")
        report = template.read_text(encoding="utf-8").replace("</main><script>", '</main><script id="snapshot" type="application/json">' + snapshot + '</script><script>')
    (ROOT / "report.html").write_text(report, encoding="utf-8")
    publish_snapshot(payload, DATA, background=True)
    return payload


def scan(config):
    db = connect()
    fetcher = Fetcher(config["request_delay_seconds"])
    started = iso()
    summary = {"started": started, "errors": [], "checked": 0, "attempted": 0}
    candidates = list(config.get("watch_asins", []))
    try:
        for lead in parse_feed(fetcher.get(config["community_feed"])):
            db.execute("INSERT OR IGNORE INTO products VALUES(?,?,?)", (lead["asin"], lead["title"], started))
            db.execute("INSERT OR REPLACE INTO leads VALUES(?,?,?,?)", (lead["asin"], lead["source_url"], json.dumps(lead), started))
            if not lead["expired"]:
                candidates.append(lead["asin"])
    except (FetchError, ET.ParseError) as exc:
        summary["errors"].append(f"Community feed: {exc}")
    for seed in config["amazon_seeds"]:
        try:
            candidates.extend(discover_asins(fetcher.get(seed)))
        except FetchError as exc:
            summary["errors"].append(f"Amazon discovery: {exc}")
    candidates = list(dict.fromkeys(x for x in candidates if re.fullmatch(r"[A-Z0-9]{10}", x)))
    for asin in candidates:
        db.execute("INSERT OR IGNORE INTO products VALUES(?,?,?)", (asin, asin, started))
    db.commit()
    # Round robin by last successful check so newly discovered ASINs don't starve older ones.
    known = {r["asin"]: r["last"] or "" for r in db.execute("SELECT p.asin, MAX(o.observed) AS last FROM products p LEFT JOIN observations o ON p.asin=o.asin GROUP BY p.asin")}
    watched = set(config.get("watch_asins", []))
    urgent = set()
    for row in db.execute("SELECT asin, payload FROM leads"):
        lead = json.loads(row["payload"])
        expiry = date(lead.get("expiry"))
        if not lead.get("expired") and (not expiry or expiry > now()) and (
            lead.get("reported_error") or lead.get("limited") or
            (expiry and expiry - now() <= timedelta(hours=config["short_lived_hours"]))):
            urgent.add(row["asin"])
    latest_path = DATA / "latest.json"
    if latest_path.exists():
        for product in json.loads(latest_path.read_text(encoding="utf-8")).get("products", []):
            if product.get("historical_drop_percent", 0) is not None and product.get("historical_drop_percent", 0) >= config["historical_drop_percent"]:
                urgent.add(product["asin"])
    budget = max(1, int(config["max_products_per_scan"]))
    explicit_queue = sorted(watched & known.keys(), key=lambda x: known[x])[:budget]
    # Reserve half the remaining budget for coverage even when many urgent leads exist.
    remaining = budget - len(explicit_queue)
    urgent_queue = sorted((urgent & known.keys()) - set(explicit_queue), key=lambda x: known[x])[:max(0, remaining // 2)]
    priority = explicit_queue + urgent_queue
    queue = priority + sorted(set(known) - set(priority), key=lambda x: (known[x], x))[:budget - len(priority)]
    for asin in queue:
        if "www.amazon.com.au" in fetcher.blocked:
            break
        summary["attempted"] += 1
        try:
            observation = parse_product(fetcher.get(f"https://www.amazon.com.au/dp/{asin}"), asin)
            observation["observed"] = iso()
            db.execute("INSERT INTO observations(asin,observed,cents,seller_key,payload) VALUES(?,?,?,?,?)",
                       (asin, observation["observed"], observation["cents"], observation["seller_key"], json.dumps(observation)))
            db.execute("UPDATE products SET title=? WHERE asin=?", (observation["title"], asin))
            summary["checked"] += 1
            db.commit()
            print(f'{asin}  {observation["cents"]} cents  {observation["title"][:85]}', flush=True)
        except FetchError as exc:
            summary["errors"].append(f"{asin}: {exc}")
            print(f"{asin}: {exc}", flush=True)
    summary.update(finished=iso(), requests=fetcher.requests, discovered=len(candidates), total_products=len(known))
    db.execute("INSERT INTO runs(started,finished,payload) VALUES(?,?,?)", (started, summary["finished"], json.dumps(summary)))
    db.commit()
    report = generate_report(db, config)
    db.close()
    print(json.dumps(summary, indent=2), flush=True)
    return report


def main():
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["scan", "watch", "report", "status"])
    args = parser.parse_args()
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    if args.command == "scan":
        scan(config)
    elif args.command == "watch":
        DATA.mkdir(exist_ok=True)
        lock = DATA / "watch.lock"
        import os
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise SystemExit("watch.lock exists; stop the running watcher or remove a stale lock first")
        with os.fdopen(fd, "w") as handle:
            handle.write(str(os.getpid()))
        try:
            while True:
                began = time.monotonic()
                try:
                    scan(config)
                except Exception as exc:
                    print(f"Scan failed: {type(exc).__name__}: {exc}", flush=True)
                time.sleep(max(30, config["scan_interval_seconds"] - (time.monotonic() - began)))
        finally:
            lock.unlink(missing_ok=True)
    elif args.command == "report":
        with connect() as db:
            generate_report(db, config)
        print(ROOT / "report.html")
    else:
        path = DATA / "latest.json"
        if not path.exists():
            raise SystemExit("No scan completed yet")
        payload = json.loads(path.read_text(encoding="utf-8"))
        print(json.dumps({key: value for key, value in payload.items() if key != "products"}, indent=2))


if __name__ == "__main__":
    main()
