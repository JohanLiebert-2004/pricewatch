# Handoff to ChatGPT Codex — Pricewatch

Continuing from Claude Code. Repo: `JohanLiebert-2004/pricewatch` (public,
GitHub Actions cron every 30 min). Live site: https://web-pi-blush-48.vercel.app
Read `CLAUDE.md` first — it's the project's own deployment/conventions doc
and is kept up to date; treat it as authoritative over this file for anything
that conflicts.

## What just happened (2026-07-10, this session)

1. **Fixed a delisting bug**: `run.py` now confirms 404s on previously-tracked
   SKUs after each bulk refresh (bounded to 15 checks/run) and NULLs the
   price + expires the deal, instead of leaving dead links live on the site.
2. **Added two retailers**: Sephora AU (`scrapers/sephora.py`, JSON API,
   needs `X-Platform: Web` + `X-Site-Country: AU` headers or it silently
   serves the wrong country's price book) and Chemist Warehouse
   (`scrapers/chemistwarehouse.py`, `__NEXT_DATA__` scrape, prescription
   items skipped for AU TGA compliance). Both live in production.
2b. Freed Supabase space: deleted ~500k stale `crawl_queue` rows +
    `VACUUM FULL` (320MB → 150MB of the 500MB free tier).
3. **Webshare residential proxy** (user bought the $3.50/mo 1GB AU plan,
   endpoint `p.webshare.io:80`, credentials in the `PROXY_URL` GitHub
   secret — do not print/log the raw value). Tested all three
   Akamai-fronted retailers today:
   - **Big W**: direct requests are blocked outright. The proxy + Chrome
     impersonation (`chrome99_android`) + a homepage warmup request
     **works** (~2/3 of attempts succeed; the rest get a plain 403, which
     the existing retry/Blocked-handling already tolerates). This is now
     wired up: `scrapers/bigw.py` has `use_proxy = True` and a
     **self-enforced monthly byte cap** (`PROXY_MONTHLY_BYTE_CAP`, currently
     700MB, tracked in the `bigw_cat_state` kv row) so the bulk category
     sweep physically cannot exceed the 1GB/month plan — it stops itself
     and resumes next month.
   - **Kmart / Target**: proxy does **not** help. Their block is a
     behavioral Akamai JS-challenge (an interstitial requiring JS
     execution) — residential IPs get challenged exactly like datacenter
     IPs. `use_proxy = False` on both. Don't re-enable without new evidence.
   - **Sephora**: already crawled cleanly direct in production before the
     proxy existed. `use_proxy = False` to leave the whole budget for Big W.
   - Just pushed (commit `16a862e`) and the `PROXY_URL` secret is set. A
     manual workflow run was kicked off to exercise it
     (https://github.com/JohanLiebert-2004/pricewatch/actions).
     **Verify next**: check that run's Big W step actually pulled more
     listings than usual and didn't error, and watch the `_proxy_bytes`
     figure in the `bigw_cat_state` kv row (or just note total Big W
     coverage growth on the growth.html page) over the next few days.
4. **Privacy pass on the public-facing site**: removed language from
   `web/catalogue.html` that named specific retailers and said "retailers
   block automated requests" — that was visible to anyone (including
   retailer staff) browsing the live site and directly described the
   scraping/evasion effort. Deployed to Vercel.

## Known open item — the repo itself leaks scraping method

This was raised by the user mid-session and **not yet resolved**: the
GitHub repo is **public** (intentionally, for free unlimited Actions
minutes — see `CLAUDE.md` section "GitHub (agents)"). That means
`scrapers/*.py`, `.github/workflows/crawl.yml`, and `CLAUDE.md` — which
contain the Akamai bypass techniques, exact impersonation profiles, delay
floors, and now the Big W proxy strategy in comments — are visible to
**anyone**, including the retailers being scraped. Website-copy fixes
(#4 above) only address what a *site visitor* sees; they don't touch this.

This is a real tradeoff, not a bug to silently fix:
- Keep public → free Actions minutes, but the scraping method (including
  which specific bypass works against which retailer) is fully readable
  by anyone who finds the repo.
- Make private → costs money once free Actions minutes run out, but hides
  the method.

**Do not flip repo visibility unilaterally.** Surface this tradeoff to the
user explicitly and let them decide (this is exactly the kind of thing
the user asked about — "i dont want website to show how i am scraping it
all" — the website fix in #4 is done, but the repo-level exposure is the
bigger piece of that ask and still needs a decision).

## Standing project conventions (see CLAUDE.md for full detail)

- Politeness floors are non-negotiable: ≥1.75s delay on Akamai retailers,
  one request at a time per retailer, no parallelization.
- Never commit `pricewatch.db` or any `.env` file.
- `web/` deploys are auto-committed/pushed/deployed to Vercel without
  asking first (established user preference, 2026-07-09) — but always
  `cd web && vercel --prod --yes` (not repo root).
- Domain `dealwatch.com.au` is ordered via VentraIP, still in review;
  Resend email setup is deferred until that domain lands.
- Telegram alerts fire for every deal above anomaly.py's own thresholds
  except Supercheap Auto (user opted out 2026-07-09).

## Suggested next steps (not yet done, no explicit go-ahead from user)

- Confirm the Big W proxy fix is actually improving coverage in production
  over the next few days (was "limited coverage" before today).
- Resolve the public-repo scraping-method exposure question above.
- User previously asked for more women-focused retailer suggestions
  (Mecca, Adore Beauty, Priceline) beyond Sephora/Chemist Warehouse —
  never followed up on.
- Nightly Postgres backup workflow — discussed, no go-ahead yet.
