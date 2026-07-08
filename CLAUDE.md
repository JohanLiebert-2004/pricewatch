# Pricewatch — deployment instructions for Claude Code

This project is an AU retail price-anomaly tracker. Your job when asked to
"deploy" or "set this up" is to automate the full free-tier deployment:
GitHub (repo + Actions agents) -> Supabase (Postgres) -> Vercel (website).

## What the human must do themselves (do not attempt these)
- Log in to accounts: `gh auth login`, `vercel login`, and creating a
  Supabase access token at https://supabase.com/dashboard/account/tokens
- Never ask for or handle raw passwords. Pause and ask them to complete
  each login in the browser, then continue.
- Price-watch emails (watch_alerts.py / product.html's "watch this price"
  form): sign up at resend.com, verify a sending domain (their shared
  onboarding.resend.dev sender only delivers to the account owner's own
  address, not to real visitors — a real domain is required), create an
  API key. Then `gh secret set RESEND_API_KEY` and `gh secret set
  RESEND_FROM` (e.g. alerts@yourdomain.com). Until both secrets are set,
  watch_alerts.send_watch_alerts() is a no-op — watches still get created,
  they just never fire, exactly like Telegram alerts before its token was
  set.

## Deployment steps (automate all of this)

### 0. Preflight
- Check tools: `gh --version`, `vercel --version`, `python --version`.
  Install missing ones (winget/brew/npm i -g vercel). Supabase can be driven
  via its CLI (`npx supabase`) or Management API with the access token.
- `pip install -r requirements.txt` and run the test suite below before deploying.

### 1. GitHub (agents)
- `git init` if needed, commit everything.
- `gh repo create pricewatch --public --source . --push`
  (public = unlimited free Actions minutes; confirm the human is OK with
  the code being public first).
- After Supabase step: `gh secret set DATABASE_URL` with the connection string.
- Enable and kick the workflow: `gh workflow run crawl-and-detect`.
- IMPORTANT first run: temporarily uncomment the `python run.py index all`
  line in .github/workflows/crawl.yml, push, run once, then re-comment and push.

### 2. Supabase (database)
- With the human's access token (env SUPABASE_ACCESS_TOKEN):
  create a project via `npx supabase projects create pricewatch --region ap-southeast-2`
  (Sydney region — closest to AU retailers' audience).
- Apply `schema.sql`, then create the website's read-only view:
  see DEPLOY.md section 1 step 3 for the exact `create view deal_feed` SQL.
- Retrieve the connection string and the anon key; store the connection string
  as the GitHub secret, and inject the URL + anon key into web/index.html
  (constants SUPABASE_URL / SUPABASE_ANON_KEY at the top of the script tag).
- Enable RLS so the anon role can only SELECT from the deal_feed view.

### 3. Vercel (website)
- `cd web && vercel --prod --yes` (project root is the static `web/` folder,
  no build step). Report the resulting URL to the human.

### 4. Verify end-to-end
- `python run.py url "https://www.bigw.com.au/product/sheridan-luxury-otis-herringbone-cotton-linen-quilt-cover-set-white-size-queen/p/9903273875"`
  with DATABASE_URL set -> confirm a row lands in Supabase.
- `python run.py detect` -> confirm deals view populates.
- Open the Vercel URL -> the feed should render.

## Local test suite (run before any deploy)
```
python -c "import ast;[ast.parse(open(f).read()) for f in ['db.py','anomaly.py','matching.py','run.py','api_deals.py']]"
python run.py scrape officeworks --limit 5   # live scrape smoke test
python run.py detect
```

## Project conventions
- Politeness is non-negotiable: do not lower scraper delays below 1.75s for
  Akamai-protected retailers (owner-approved floor, July 2026; revert to 2s+
  if blocks increase); do not parallelize requests to one retailer.
- **Proxy policy (updated 2026-07-08):** residential/rotating proxies are
  permitted for Akamai-fronted retailers (Big W, Kmart, Target) to reduce
  datacenter-IP flags, now that the site is meant to run as a real
  production service rather than a hobby Vercel deployment. Config is
  already wired: `scrapers/base.py` reads `PROXY_URL` (format
  `http://user:pass@host:port`) and routes any scraper with `use_proxy =
  True` through it via curl_cffi; unset, everything runs exactly as before
  (direct GitHub Actions runner IP). The human still needs to sign up with
  a provider (e.g. Bright Data, Oxylabs, Smartproxy, IPRoyal) and run
  `gh secret set PROXY_URL` — do not attempt that signup yourself. This
  does NOT relax the delay floors or concurrency rule above; it only adds
  a proxy option on top of the existing politeness budget. Scope is
  intentionally limited to the three Akamai retailers, not the whole
  no-evasion stance (Bunnings' Cloudflare fingerprinting stays ruled out
  unless the human decides otherwise).
- SQLite is the local dev DB; setting DATABASE_URL switches everything to
  Postgres. Never commit pricewatch.db or any .env file (see .gitignore).
- Retailers block datacenter IPs intermittently; a Blocked exception is
  expected behaviour, not a bug — batches resume on the next run.
