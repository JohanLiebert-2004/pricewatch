#!/usr/bin/env bash
# Daily PostgreSQL backup to OCI Object Storage. Runs as root from systemd.
set -Eeuo pipefail
umask 077

source /opt/pricewatch.env
source /etc/pricewatch-backup.env

: "${DATABASE_URL:?DATABASE_URL is required}"
: "${OCI_BACKUP_BUCKET:?OCI_BACKUP_BUCKET is required}"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
object="postgres/pricewatch-${stamp}.dump"
workdir="$(mktemp -d /var/tmp/pricewatch-backup.XXXXXX)"
dump_file="${workdir}/pricewatch.dump"
checksum_file="${workdir}/pricewatch.dump.sha256"
trap 'rm -rf "${workdir}"' EXIT

pg_dump_bin="${PG_DUMP:-/usr/lib/postgresql/17/bin/pg_dump}"
if [ ! -x "${pg_dump_bin}" ]; then
  pg_dump_bin="$(command -v pg_dump)"
fi

"${pg_dump_bin}" --version
"${pg_dump_bin}" --dbname="${DATABASE_URL}" --format=custom --no-owner --no-privileges \
  --file="${dump_file}"
sha256sum "${dump_file}" > "${checksum_file}"

oci --auth instance_principal os object put \
  --bucket-name "${OCI_BACKUP_BUCKET}" --name "${object}" \
  --file "${dump_file}" --force
oci --auth instance_principal os object put \
  --bucket-name "${OCI_BACKUP_BUCKET}" --name "${object}.sha256" \
  --file "${checksum_file}" --force
oci --auth instance_principal os object head \
  --bucket-name "${OCI_BACKUP_BUCKET}" --name "${object}" >/dev/null

echo "Uploaded ${object} ($(du -h "${dump_file}" | cut -f1))"
