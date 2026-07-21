# Dealwatch on Oracle Cloud

Terraform describes the two production roles and one optional replacement:

- `pricewatch`: public nginx + SSR/image proxy, Telegram bot, optional
  embedding server, Kmart timer, and backup tooling.
- `pricewatch_db_x86`: PostgreSQL 17 + pgvector, PostgREST, nginx, fail2ban,
  and daily Object Storage backups. This is the current production database.
- `pricewatch_db`: optional A1.Flex database replacement. It is disabled by
  default because Sydney Always Free ARM capacity is unreliable.

The website remains a Vercel static deployment. GitHub Actions performs
hourly catalogue refresh/detection and separate three-hour product-page
enrichment. CI connects to PostgreSQL through an SSH tunnel via the web host;
port 5432 is restricted to the OCI VCN.

## Secret handling

Terraform never receives runtime passwords or tokens. Keep these files out of
Git and do not print them:

- Web role: `/opt/pricewatch.env`, root-owned mode `0600`.
- Database role: `/etc/pricewatch-db.env`, root-owned mode `0600`.
- Kmart override: `/etc/pricewatch-kmart.env`, root-owned mode `0600`.
- Local Terraform values: `terraform.tfvars` (gitignored).

The repository contains examples/placeholders only. Terraform state and the
local OCI configuration/private key are also sensitive and gitignored.

## Configure and validate

```powershell
Copy-Item infra\oci\terraform.tfvars.example infra\oci\terraform.tfvars
terraform -chdir=infra\oci init
terraform -chdir=infra\oci fmt -check
terraform -chdir=infra\oci validate
terraform -chdir=infra\oci plan
```

Set `ssh_allowed_cidr` to the operator's current public `/32` whenever
possible. Leave these safety defaults unchanged for routine operation:

```hcl
enable_arm_db        = false
database_allowed_cidr = "10.42.0.0/16"
```

Enabling ARM is a deliberate migration action, not a capacity retry loop:

```hcl
enable_arm_db = true
```

Review the plan before applying. The existing instances ignore metadata
changes because cloud-init is first-boot-only; editing a template must not
replace a live host.

## Role-specific bootstrap

`cloud-init-web.yaml.tftpl` installs the repository, Python environment,
nginx/TLS helper, service units, fail2ban, and an empty root-only environment
file. It enables only nginx, fail2ban, and the SSR service. Restore secrets
before enabling the bot, Kmart, embedding, or backup units.

`cloud-init-db.yaml.tftpl` installs PostgreSQL 17, pgvector, the pinned
PostgREST binary, nginx/TLS, fail2ban, service units, secure `pg_hba` rules,
and an empty root-only DB environment file. After restoring two generated
passwords, finish the idempotent setup with:

```bash
sudoedit /etc/pricewatch-db.env
sudo chmod 600 /etc/pricewatch-db.env
sudo pricewatch-finalize-db
```

The finalizer creates/updates least-privilege roles, applies `schema.sql` and
`views.sql`, writes the protected PostgREST configuration, and enables the API
and backup timer. Restore a database dump before applying `views.sql` when
recovering production data.

Both roles derive their `sslip.io` hostname from OCI instance metadata; no
instance IP is embedded in cloud-init or a committed systemd unit.

## GitHub Actions private database path

The local composite action `.github/actions/database-tunnel` requires:

- secrets `OCI_SSH_PRIVATE_KEY` and `OCI_SSH_KNOWN_HOSTS`;
- variables `OCI_SSH_HOST` and `OCI_DB_PRIVATE_HOST`.

It opens a pinned-host-key local forward and rewrites `DATABASE_URL` only for
subsequent job steps. The original database credential stays in GitHub
Secrets. Verify a workflow through the tunnel before restricting an existing
public 5432 rule.

## Production checks

Web role:

```bash
systemctl --no-pager status pricewatch-web pricewatch-bot
systemctl --no-pager status pricewatch-kmart.timer
journalctl -u pricewatch-kmart.service -n 100 --no-pager
```

Database role:

```bash
pg_isready
curl --fail http://127.0.0.1:3000/
systemctl --no-pager status postgresql pricewatch-postgrest nginx
systemctl --no-pager status pricewatch-backup.timer
journalctl -u pricewatch-backup.service -n 100 --no-pager
```

## Recovery and migration rules

1. Never destroy the x86 database during the first ARM migration pass.
2. Restore the newest checksummed Object Storage dump to the candidate.
3. Apply schema/views, compare counts and freshness, then test PostgREST.
4. Update private endpoint configuration and run a complete CI cycle.
5. Cut over public API routing with an explicit rollback target.
6. Retain the former database until a new backup from the replacement has
   completed and been verified.

Local configuration backups can accelerate recovery, but the committed
role-specific templates are the rebuild source of truth.
