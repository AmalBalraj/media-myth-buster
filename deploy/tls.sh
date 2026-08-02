#!/usr/bin/env bash
# Issue (or renew) the Let's Encrypt certificate via certbot's nginx plugin,
# matching how the other sites on this box are configured. Run after the site is
# answering on port 80 — certbot validates over HTTP.
set -euo pipefail

HOST="${MYTH_HOST:-oracle-dev}"
DOMAIN="${MYTH_DOMAIN:-myth-buster.devmindset.in}"
EMAIL="${MYTH_EMAIL:-amalbalraj99@gmail.com}"

echo "▸ Checking $DOMAIN answers on port 80 before requesting a cert"
code=$(curl -s -o /dev/null -w '%{http_code}' "http://$DOMAIN/api/health" || true)
if [ "$code" != "200" ]; then
  echo "http://$DOMAIN/api/health returned ${code:-nothing}." >&2
  echo "Certbot's HTTP challenge will fail until the site is up. Run deploy.sh first." >&2
  exit 1
fi

ssh "$HOST" sudo certbot --nginx \
  -d "$DOMAIN" \
  --non-interactive --agree-tos -m "$EMAIL" \
  --redirect

echo "▸ Certificate installed. Verifying HTTPS…"
curl -sS "https://$DOMAIN/api/health" | python3 -m json.tool
ssh "$HOST" "sudo certbot certificates 2>/dev/null | grep -A2 '$DOMAIN'"
