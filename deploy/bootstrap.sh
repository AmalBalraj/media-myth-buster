#!/usr/bin/env bash
# One-time server setup for myth-buster.devmindset.in. Safe to re-run.
#
#   ./deploy/bootstrap.sh
#
# Creates the remote directory and .env, installs the nginx site, then hands off
# to deploy.sh. TLS is issued separately at the end (certbot needs the site
# answering on port 80 first).
set -euo pipefail

HOST="${MYTH_HOST:-oracle-dev}"
REMOTE_DIR="${MYTH_REMOTE_DIR:-/home/amal/myth-buster}"
DOMAIN="${MYTH_DOMAIN:-myth-buster.devmindset.in}"
LOCAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

say() { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }

say "Preparing $HOST:$REMOTE_DIR"
ssh "$HOST" bash -s <<REMOTE
set -euo pipefail
mkdir -p "$REMOTE_DIR"/{data/media,infra/searxng}
command -v docker >/dev/null || { echo "docker missing on host" >&2; exit 1; }
if docker info >/dev/null 2>&1; then DC="docker compose"; else DC="sudo docker compose"; fi
\$DC version >/dev/null || { echo "docker compose plugin missing" >&2; exit 1; }
echo "using: \$DC"
REMOTE

say "Seeding .env (only if absent — an existing one keeps its keys)"
scp -q "$LOCAL_DIR/.env.example" "$HOST:$REMOTE_DIR/.env.example"
ssh "$HOST" bash -s <<REMOTE
set -euo pipefail
cd "$REMOTE_DIR"
if [ -f .env ]; then
  echo ".env already exists — untouched"
else
  cp .env.example .env
  sed -i 's|^APP_ENV=.*|APP_ENV=prod|'                          .env
  sed -i 's|^PUBLIC_BASE_URL=.*|PUBLIC_BASE_URL=https://$DOMAIN|' .env
  sed -i 's|^WEB_BASE_URL=.*|WEB_BASE_URL=https://$DOMAIN|'     .env
  sed -i "s|^META_WEBHOOK_VERIFY_TOKEN=.*|META_WEBHOOK_VERIFY_TOKEN=\$(openssl rand -hex 16)|" .env
  echo "created .env — ADD YOUR API KEYS THERE (DEEPSEEK/GROQ/GEMINI)"
fi
REMOTE

say "Installing nginx site for $DOMAIN"
scp -q "$LOCAL_DIR/deploy/nginx/myth-buster.conf" "$HOST:/tmp/myth-buster.conf"
ssh "$HOST" bash -s <<'REMOTE'
set -euo pipefail
sudo mv /tmp/myth-buster.conf /etc/nginx/sites-available/myth-buster
sudo ln -sfn /etc/nginx/sites-available/myth-buster /etc/nginx/sites-enabled/myth-buster
sudo nginx -t
sudo systemctl reload nginx
echo "nginx reloaded"
REMOTE

say "Bootstrap done. Next:"
cat <<EOF

  1. Add your API keys:      ssh $HOST 'nano $REMOTE_DIR/.env'
  2. Deploy the stack:       ./deploy/deploy.sh
  3. Issue the certificate:  ./deploy/tls.sh

EOF
