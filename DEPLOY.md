# Dealwatch deployment

Production is Vercel plus two OCI roles. Supabase is retired. Read
`AGENT_STATE.md` before operating production. Never print or commit secrets,
keys, Terraform state, database dumps, or `.env` files.

## Components

- `web/`: static Vercel project at `https://dealwatch.com.au`.
- OCI web role: SSR, sitemaps, `/img`, health checks, and selected workers.
- OCI DB role: PostgreSQL 17, pgvector, PostgREST, nginx, and backups.
- GitHub Actions: hourly refresh/detect plus separate three-hour enrichment.
  Actions reaches PostgreSQL through the restricted SSH tunnel action.

## Validate

```powershell
python -m unittest discover -s .\tests -p 'test_*.py'
python -m py_compile db.py run.py services\preview_app.py
terraform -chdir=infra/oci fmt -check
terraform -chdir=infra/oci validate
```

Also parse changed workflow/cloud-init YAML and browser scripts. Render and
parse cloud-init separately because Terraform does not validate its YAML.

## Database

Apply `schema.sql` before `views.sql` as the PostgreSQL administrator on the
OCI DB host. Keep port 5432 private; use the OCI network or authenticated SSH
tunnel. Restart PostgREST only when needed, then verify the affected RPC/view
through the public same-origin `/rest/v1` route.

## Website

```powershell
vercel --prod --yes web
```

Verify the apex, `homepage_bootstrap`, `/img`, one real `/p/:retailer/:sku`
page, `robots.txt`, and the sitemap.

## OCI application code

Git pushes do not deploy OCI. Pull the intended commit in `/opt/pricewatch`,
preserve host-local changes, restart only the affected service, and check its
health and logs. Database SQL is applied explicitly on the DB role.

## GitHub Actions

`DATABASE_URL`, the SSH private key, and known hosts are repository secrets.
SSH host/user and the private DB address are repository variables. Keep values
only in GitHub/host secret stores. After workflow edits, manually verify
checkout, tunnel creation, jobs, detection, and resulting freshness.

## Terraform

`infra/oci/` has separate web and DB cloud-init templates. The x86 DB is
production and `enable_arm_db = false` is the safe default. Review every plan
for replacement/destroy actions. Do not enable ARM or replace/migrate a host
without explicit approval and a tested backup, cutover, rollback, and data
comparison plan.

The intended network posture is public HTTP/HTTPS, keys-only SSH with
fail2ban, and PostgreSQL ingress restricted to the OCI VCN.
