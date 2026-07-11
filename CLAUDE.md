# Pricewatch — deployment instructions for Claude Code

This project is an AU retail price-anomaly tracker. Your job when asked to
"deploy" or "set this up" is to automate the full free-tier deployment:
GitHub (repo + Actions agents) -> Supabase (Postgres) -> Vercel (website).
## Shared agent coordination

Codex and Claude do not share private chat history. The repository is the handoff mechanism:

1. Read `AGENT_STATE.md` and `AGENT_PROTOCOL.md` immediately after this file.
2. Check `git status -sb` and claim an unowned task in `AGENT_STATE.md` before editing.
3. Do not touch a task/files claimed by the other agent; use focused commits and record the result before handing off.
4. `HANDOFF_CODEX.md` is historical. Use `AGENT_STATE.md` for current state.

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

## Crawler infra (2026-07-10): GitHub Actions is primary, not OCI
- `.github/workflows/crawl.yml` (cron every 30 min) is the **sole active**
  crawl-and-detect pipeline. The repo is public specifically for unlimited
  free Actions minutes, so there's no capacity reason to move off it.
- An OCI VM exists (`infra/oci/`, Terraform-managed, IP via
  `terraform -chdir=infra/oci output public_ip`) from an earlier
  experiment to run the crawler on a systemd timer instead. Its
  `pricewatch-cycle.timer` has been **stopped and disabled** — do not
  re-enable it without fixing, first: (a) `PROXY_URL` is blank in its
  `/opt/pricewatch.env`, so Big W is skipped there; (b) a full cycle takes
  ~110 min, right at the systemd unit's `TimeoutStartSec=6600`, so it can
  get killed before reaching `python run.py detect`; (c) running it
  alongside GitHub Actions on the same 30-min schedule caused
  `psycopg.errors.DeadlockDetected` from both hitting Postgres schema DDL
  concurrently (fixed in commit `81c6250`, but only matters if both run
  at once again). The VM itself is left provisioned (not destroyed) in
  case OCI is revisited later.

## Project conventions
- Politeness is non-negotiable: do not lower scraper delays below 1.75s for
  Akamai-protected retailers (owner-approved floor, July 2026; revert to 2s+
  if blocks increase); do not parallelize requests to one retailer.
- **Proxy policy (updated 2026-07-10):** residential/rotating proxies are
  permitted for Akamai-fronted retailers to reduce datacenter-IP flags.
  Config: `scrapers/base.py` reads `PROXY_URL` (format
  `http://user:pass@host:port`) and routes any scraper with `use_proxy =
  True` through it via curl_cffi; unset, everything runs exactly as before
  (direct GitHub Actions runner IP). A Webshare residential plan (1GB/month,
  AU-geo rotating endpoint) is live as of 2026-07-10, scoped to **bigw
  only** — tested findings:
  - **Big W**: direct requests are blocked outright (403 on request #1).
    Residential proxy + `chrome99_android` impersonation + homepage warmup
    passes cleanly (~2/3 of attempts; the rest get a plain 403, handled by
    the existing Blocked-mid-refresh fallback). `bigw.py` self-tracks
    cumulative bytes sent through the proxy per calendar month
    (`PROXY_MONTHLY_BYTE_CAP`, stored in the `bigw_cat_state` kv row) and
    stops the bulk sweep once spent, so it can never exceed the plan.
  - **Kmart / Target**: the proxy does *not* help. Their block is a
    behavioral Akamai JS-challenge (curl_cffi can't execute JS), not an
    IP-reputation block — residential IPs get the same challenge
    interstitial as datacenter IPs. `use_proxy = False` on both; don't
    re-enable without new evidence it works.
  - **Sephora**: already crawls cleanly direct (not Akamai-gated the same
    way) — `use_proxy = False` to leave the full budget for Big W.
  The human still needs to manage the Webshare account/billing directly —
  do not attempt signup or plan changes yourself. This does NOT relax the
  delay floors or concurrency rule above. Bunnings' Cloudflare
  fingerprinting stays ruled out unless the human decides otherwise.
- SQLite is the local dev DB; setting DATABASE_URL switches everything to
  Postgres. Never commit pricewatch.db or any .env file (see .gitignore).
- Retailers block datacenter IPs intermittently; a Blocked exception is
  expected behaviour, not a bug — batches resume on the next run.
