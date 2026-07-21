#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "run as root" >&2
  exit 1
fi
config=/etc/pricewatch-db.env
if [[ ! -r "$config" ]]; then
  echo "$config is missing" >&2
  exit 1
fi
set -a
source "$config"
set +a
: "${PRICEWATCH_DB_PASSWORD:?set PRICEWATCH_DB_PASSWORD in $config}"
: "${POSTGREST_AUTH_PASSWORD:?set POSTGREST_AUTH_PASSWORD in $config}"

sudo -u postgres psql --set=ON_ERROR_STOP=1 \
  --set=db_password="$PRICEWATCH_DB_PASSWORD" <<'SQL'
SELECT 'CREATE DATABASE pricewatch'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'pricewatch')\gexec
SELECT format('CREATE ROLE pricewatch LOGIN PASSWORD %L', :'db_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'pricewatch')\gexec
ALTER ROLE pricewatch LOGIN BYPASSRLS;
SELECT format('ALTER ROLE pricewatch PASSWORD %L', :'db_password')\gexec
SQL

sudo -u postgres psql --dbname pricewatch --set=ON_ERROR_STOP=1 \
  --set=auth_password="$POSTGREST_AUTH_PASSWORD" <<'SQL'
DO $$ BEGIN CREATE ROLE anon NOLOGIN NOINHERIT; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE ROLE authenticated NOLOGIN NOINHERIT; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
SELECT format('CREATE ROLE authenticator NOINHERIT LOGIN PASSWORD %L', :'auth_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'authenticator')\gexec
SELECT format('ALTER ROLE authenticator PASSWORD %L', :'auth_password')\gexec
GRANT anon, authenticated TO authenticator;
SQL

install -d -m 0750 -o postgrest -g postgrest /etc/postgrest
umask 077
encoded_auth_password=$(python3 -c \
  'import os, urllib.parse; print(urllib.parse.quote(os.environ["POSTGREST_AUTH_PASSWORD"], safe=""))')
cat > /etc/postgrest/postgrest.conf <<EOF
db-uri = "postgres://authenticator:${encoded_auth_password}@127.0.0.1:5432/pricewatch"
db-schemas = "public"
db-anon-role = "anon"
server-host = "127.0.0.1"
server-port = 3000
db-pool = 10
db-pool-timeout = 10
EOF
chown postgrest:postgrest /etc/postgrest/postgrest.conf

sudo -u postgres psql --dbname pricewatch --set=ON_ERROR_STOP=1 \
  --file /opt/pricewatch/schema.sql
sudo -u postgres psql --dbname pricewatch --set=ON_ERROR_STOP=1 \
  --file /opt/pricewatch/views.sql
systemctl enable --now pricewatch-postgrest.service pricewatch-backup.timer
systemctl reload nginx
echo "Database roles, schema, API, and backup timer are ready."
