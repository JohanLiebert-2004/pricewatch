#!/usr/bin/env bash
set -Eeuo pipefail

role="${1:?usage: configure_nginx_sslip.sh web|db}"
case "$role" in
  web) template=/opt/pricewatch/infra/oci/services/nginx-pricewatch-web.conf.template ;;
  db)  template=/opt/pricewatch/infra/oci/services/nginx-pricewatch-db.conf.template ;;
  *) echo "unknown role: $role" >&2; exit 2 ;;
esac

metadata=http://169.254.169.254/opc/v2/vnics/
public_ip=$(curl --fail --silent --show-error --connect-timeout 5 \
  -H 'Authorization: Bearer Oracle' "$metadata" | jq -r '.[0].publicIp // empty')
test -n "$public_ip"
host="${public_ip//./-}.sslip.io"

sed "s/REPLACE_WITH_HOST/$host/g; s/REPLACE_WITH_DASHED_IP/${public_ip//./-}/g" \
  "$template" > /etc/nginx/sites-available/pricewatch
ln -sfn /etc/nginx/sites-available/pricewatch /etc/nginx/sites-enabled/pricewatch
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

# A first boot remains usable over HTTP if certificate issuance is briefly
# delayed by DNS propagation. Re-running this script is safe and completes TLS.
certbot --nginx --non-interactive --agree-tos --register-unsafely-without-email \
  --redirect -d "$host" || true
