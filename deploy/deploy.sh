#!/usr/bin/env bash
# Push local code to oracle-dev and restart the stack.
#
#   ./deploy/deploy.sh              # sync, rebuild changed images, restart
#   ./deploy/deploy.sh --api        # backend only (skips the slow web build)
#   ./deploy/deploy.sh --web        # frontend only
#   ./deploy/deploy.sh --no-build   # sync + restart, no rebuild
#   ./deploy/deploy.sh --logs       # tail logs after deploying
#
# The server's .env is never overwritten — it holds the API keys and is the one
# file that must not be clobbered by a deploy.
set -euo pipefail

HOST="${MYTH_HOST:-oracle-dev}"
REMOTE_DIR="${MYTH_REMOTE_DIR:-/home/amal/myth-buster}"
LOCAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOMAIN="${MYTH_DOMAIN:-myth-buster.devmindset.in}"

SERVICES=()
BUILD=1
FOLLOW_LOGS=0

for arg in "$@"; do
  case "$arg" in
    --api)      SERVICES=(api worker) ;;
    --web)      SERVICES=(web) ;;
    --no-build) BUILD=0 ;;
    --logs)     FOLLOW_LOGS=1 ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }

say "Checking local build before shipping"
( cd "$LOCAL_DIR/backend" && .venv/bin/python -m pytest tests -q ) \
  || { echo "tests failed — not deploying"; exit 1; }

say "Syncing $LOCAL_DIR -> $HOST:$REMOTE_DIR"
rsync -az --delete \
  --exclude '.git/' \
  --exclude '.env' \
  --exclude 'data/' \
  --exclude '.venv/' \
  --exclude '__pycache__/' \
  --exclude '.ruff_cache/' \
  --exclude '.pytest_cache/' \
  --exclude 'node_modules/' \
  --exclude '.next/' \
  --exclude 'eval/runs/' \
  "$LOCAL_DIR/" "$HOST:$REMOTE_DIR/"

say "Restarting stack on $HOST"
ssh "$HOST" bash -s <<REMOTE
set -euo pipefail
cd "$REMOTE_DIR"

if [ ! -f .env ]; then
  echo "FATAL: $REMOTE_DIR/.env is missing. Run deploy/bootstrap.sh first." >&2
  exit 1
fi

# This account is not in the docker group; sudo is passwordless. Detect rather
# than assume, so adding the user to the group later just works.
if docker info >/dev/null 2>&1; then DC="docker compose"; else DC="sudo docker compose"; fi

services="${SERVICES[*]:-}"
if [ "$BUILD" = "1" ]; then
  echo "building \${services:-all services}…"
  \$DC build \$services
fi
\$DC up -d --remove-orphans \$services

echo
\$DC ps --format 'table {{.Service}}\t{{.Status}}\t{{.Ports}}'
REMOTE

say "Waiting for health"
# HTTPS first, falling back to HTTP so the very first deploy — before tls.sh has
# run — still reports honestly instead of failing on a missing certificate.
for i in $(seq 1 45); do
  for scheme in https http; do
    code=$(curl -s -o /dev/null -w '%{http_code}' "$scheme://$DOMAIN/api/health" || true)
    if [ "$code" = "200" ]; then base="$scheme://$DOMAIN"; break 2; fi
  done
  sleep 2
done

if [ "${code:-}" = "200" ]; then
  echo "$base is up"
  curl -s "$base/api/health" | python3 -m json.tool 2>/dev/null || true
  [ "${base%%:*}" = "http" ] && echo "(still plain HTTP — run ./deploy/tls.sh)"
else
  echo "health check did not return 200 (got ${code:-none}) — check the logs:" >&2
  ssh "$HOST" "cd $REMOTE_DIR && sudo docker compose logs --tail 40 api worker"
  exit 1
fi

if [ "$FOLLOW_LOGS" = "1" ]; then
  ssh "$HOST" "cd $REMOTE_DIR && sudo docker compose logs -f --tail 50"
fi
