# Dealwatch — current instructions for Claude Code

Dealwatch is an Australian retail price tracker. The production stack is:

- Vercel serves the static site at `https://dealwatch.com.au`.
- OCI runs the SSR/image-proxy service and the crawler-owned timers.
- A separate OCI x86 VM runs PostgreSQL 17, pgvector, PostgREST, nginx, and
  the daily database backup.
- GitHub Actions runs hourly catalogue refresh/detection and a separate,
  slower enrichment workflow. Actions reaches PostgreSQL through a restricted
  SSH local-forward account on the OCI web host; PostgreSQL is not public.

Supabase is retired. Do not deploy schema, views, crawlers, or configuration
to the old Supabase project.

## Shared-agent coordination

Codex and Claude do not share private chat history. The repository is the
handoff mechanism:

1. Read `AGENT_STATE.md` and `AGENT_PROTOCOL.md` immediately after this file.
2. Check `git status -sb` and claim an unowned task in `AGENT_STATE.md`
   before editing.
3. Preserve unrelated user or agent changes and use focused commits.
4. Update `AGENT_STATE.md` with test and production evidence before handing
   work back.
5. `HANDOFF_CODEX.md` and `PROJECT_NOTES.md` are historical. Current state
   in this file, `README.md`, and `AGENT_STATE.md` wins.

Never print, copy into documentation, or commit runtime secrets, private keys,
`.env` files, Terraform state, or database dumps.

## Production ownership

- `.github/workflows/crawl.yml`: hourly bulk refresh, embeddings, then deal
  detection. Detection does not hide crawler failures.
- `.github/workflows/enrich.yml`: slower product-page/sitemap enrichment
  every three hours, capped at three parallel retailers.
- `.github/actions/database-tunnel/action.yml`: creates the private database
  tunnel used by Actions. Keep the CI key restricted to forwarding only.
- Kmart has an OCI systemd sweep and a heartbeat gate that prevents CI from
  duplicating a healthy VM-owned run.
- Big W and Chemist Warehouse can use owner-PC sweeps because their storefronts
  reject data-centre runner IPs. Their database heartbeats decide whether CI
  should skip its fallback attempt.
- Do not parallelise requests within one retailer or lower the configured
  politeness delays.

## Safe deployment flow

Run the relevant local checks before every deployment:

```powershell
python -m unittest discover -s .\tests -p 'test_*.py'
python -m py_compile db.py run.py services\preview_app.py
terraform -chdir=infra/oci fmt -check
terraform -chdir=infra/oci validate
```

Also parse changed workflow/cloud-init YAML and changed inline browser scripts.
`terraform validate` does not validate rendered cloud-init YAML, so render
and parse both role templates when changing them.

### Website

```powershell
vercel --prod --yes web
```

Then verify the apex, `/rest/v1/rpc/homepage_bootstrap`, `/img`, one
`/p/:retailer/:sku` product page, and the sitemap. Static frontend requests
must use same-origin `/rest/v1` and `/img`, not embedded OCI addresses.

### Database schema and views

Apply `schema.sql` before `views.sql` as the PostgreSQL administrator on the
database host. Restart PostgREST only when its schema cache or service config
needs it. Smoke-test the affected RPC through the public same-origin route.
Do not expose port 5432 to run migrations.

### OCI application host

A push does not deploy OCI. Pull the intended commit in
`/opt/pricewatch`, then restart only the affected service and verify its
health/logs. Preserve host-local files and configuration.

### Terraform

`infra/oci/` models two roles:

- `cloud-init-web.yaml.tftpl` for the web/worker host.
- `cloud-init-db.yaml.tftpl` for PostgreSQL/PostgREST.

The current x86 database is production. `enable_arm_db = false` is the safe
default because Sydney Always Free ARM capacity has repeatedly been
unavailable. Do not enable ARM, replace an instance, or destroy a fallback
without explicit owner approval and a tested restore/cutover/rollback plan.
Review every plan for replacement or destroy actions before applying it.

The shared security list deliberately allows public HTTP/HTTPS and SSH.
PostgreSQL ingress must remain VCN-only. GitHub runner addresses are dynamic,
so SSH remains public with keys-only authentication, fail2ban, and a
forwarding-only `ci-tunnel` identity.

## Project conventions

- SQLite is the local development default; `DATABASE_URL` selects PostgreSQL.
- Never commit `pricewatch.db`, `.env`, `terraform.tfvars`, `tfplan`,
  state files, keys, or dumps.
- Retailer blocks are expected and should fail visibly or resume from a saved
  cursor; never add browser-stealth or challenge-bypass behaviour.
- Use official feeds/APIs where available, retain serial retailer access, and
  keep existing rate limits.
- A green workflow is not enough evidence of freshness. Check the applicable
  heartbeat/freshness row and crawler output before declaring recovery.
