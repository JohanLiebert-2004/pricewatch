#!/usr/bin/env bash
# Install only the Amazon additions on top of the exact current public release.
set -euo pipefail
stage=${1:?staging directory required}
release_id=${2:?release ID required}
[[ "$stage" == /home/ubuntu/.local/share/blackfriday-watch/deploy-* ]]
[[ "$release_id" =~ ^amazon-[0-9]{8}T[0-9]{6}Z$ ]]
base=/var/www/dealwatch
previous=$(readlink -f "$base/current")
[[ "$previous" == "$base/releases/"* && -d "$previous" && -L "$base/current" ]]
release="$base/releases/$release_id"
[[ ! -e "$release" ]]
mkdir -m 0755 "$release"
cp -a "$previous/." "$release/"
for asset in amazon.html amazon.js amazon.css; do
    install -m 0644 "$stage/$asset" "$release/$asset"
done
python3 "$stage/amazon_navigation.py" "$release"
python3 - "$stage/amazon-public.json" <<'PY'
import json,sys
data=json.load(open(sys.argv[1]))
assert data['schema_version']==1 and data['currency']=='AUD'
assert set(data)=={'schema_version','generated_at','currency','freshness_minutes','products','community_leads','coverage'}
assert all('worker' not in item and 'job_id' not in item for item in data['products'])
PY
public=/var/lib/dealwatch-amazon/public
install -d -o ubuntu -g www-data -m 0755 /var/lib/dealwatch-amazon "$public"
install -o ubuntu -g www-data -m 0644 "$stage/amazon-public.json" "$public/.initial.json"
mv -f "$public/.initial.json" "$public/amazon-deals.json"
if [[ -L "$release/amazon-deals.json" ]]; then
    [[ "$(readlink "$release/amazon-deals.json")" == "$public/amazon-deals.json" ]]
else
    [[ ! -e "$release/amazon-deals.json" ]]
    ln -s "$public/amazon-deals.json" "$release/amazon-deals.json"
fi
find "$release" -type d -exec chmod 0755 {} +
find "$release" -type f -exec chmod 0644 {} +
printf '%s\n' "$previous" > "$stage/previous-release.txt"
switched=0
rollback() {
    code=$?
    if [[ $code -ne 0 && $switched -eq 1 ]]; then
        ln -s "$previous" "$base/.rollback-$release_id"
        mv -Tf "$base/.rollback-$release_id" "$base/current"
    fi
    exit "$code"
}
trap rollback EXIT
ln -s "$release" "$base/.next-$release_id"
mv -Tf "$base/.next-$release_id" "$base/current"
switched=1
curl -fsS --max-time 25 https://dealwatch.com.au/amazon.html -o /dev/null
curl -fsS --max-time 25 https://dealwatch.com.au/amazon-deals.json -o /dev/null
printf 'Amazon release: %s\nPrevious release: %s\n' "$release" "$previous"
