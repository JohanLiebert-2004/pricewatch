# Deploying Pricewatch for free (test site)

Three free services, all $0, no credit card strictly required:

    GitHub Actions  ->  runs the crawler + detector on a schedule (the "agents")
    Supabase        ->  Postgres database that stores products, prices, deals
    Vercel / Pages  ->  hosts the deal-feed website

## 1. Database — Supabase (free Postgres)
1. Create a project at supabase.com (free tier: 500 MB, ~500k+ snapshots).
2. SQL Editor -> paste and run `schema.sql` (Postgres version).
3. Also create the read-only view the website uses:
   ```sql
   create view deal_feed as
     select d.price, d.reference_price, d.signal, d.score, d.status,
            d.detected_at, p.title, p.retailer, p.url, p.is_marketplace,
            round(d.score*100) as pct_off, (d.score >= 0.80) as error_tier
     from deals d join products p on p.id = d.product_id
     where d.status <> 'expired'
     order by d.score desc;
   ```
4. Settings -> Database -> Connection string (URI). Copy it; that's DATABASE_URL.
5. Settings -> API -> copy the Project URL and the `anon` public key (for the site).

Note: the free project pauses after 7 days of NO database activity. Your
scheduled crawler writes daily, which keeps it awake — no extra keep-alive needed.

## 2. Agents — GitHub Actions
1. Push this folder to a **public** GitHub repo (public = unlimited free Actions
   minutes; private repos only get 2,000 min/month).
2. Repo -> Settings -> Secrets and variables -> Actions -> New secret:
   name `DATABASE_URL`, value the Supabase connection string.
3. First run: Actions tab -> "crawl-and-detect" -> Run workflow, but first
   uncomment the `python run.py index all` line in
   `.github/workflows/crawl.yml` so the queue gets populated once. Re-comment
   it afterwards so later runs just crawl + detect.
4. It now runs every 6 hours automatically. Adjust the cron in the workflow.

Because a full catalogue is large, each run crawls a polite batch and the queue
rolls forward — coverage builds over successive runs. Run one workflow per
retailer in parallel if you want faster coverage.

## 3. Website — Vercel (or Cloudflare Pages)
The site is a single static file (`web/index.html`) — no build step.

Easiest (static, zero config):
1. `python api_deals.py --export` locally to refresh `web/deals.json`, OR let the
   site read Supabase live (next option).
2. Drag the `web/` folder onto vercel.com/new (or connect the repo, root = `web`).
3. You get a URL like `pricewatch.vercel.app`. Done.

Live data (no manual export): open `web/index.html`, set `SUPABASE_URL` and
`SUPABASE_ANON_KEY` at the top of the script. The site then reads the
`deal_feed` view directly. Keep Row Level Security ON and grant only SELECT on
that view to the anon role, so the public key can read deals and nothing else.

## Cost ceiling
Everything above is free at prototype scale. You'll pay only if you (a) make the
scraper repo private and blow past 2,000 Action-minutes, (b) exceed Supabase's
500 MB, or (c) get real traffic beyond Vercel's hobby limits. All three are far
past "test site".

## What still needs care before going live
- Kmart/Target FIRST-PARTY prices load client-side; use their price APIs or an
  affiliate feed (marketplace items already work via scraping).
- Amazon: use the Product Advertising API (affiliate), not scraping.
- Respect robots.txt and keep the polite delays; consider affiliate product
  feeds (Commission Factory / Impact) as the sanctioned bulk-data route.
- Add a visible disclaimer that retailers may cancel pricing-error orders (the
  site already includes one).
