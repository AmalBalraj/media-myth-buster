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
  "$LOCAL_DIR/" "$HOST:$REMOTE_DIR/" \
  || { rc=$?; [ "$rc" = 24 ] || exit $rc; }   # 24 = source file vanished mid-sync

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

# Migrate before anything serves. A model change that never reached the database
# used to surface as a runtime insert failure on the first analysis; now it is a
# failed deploy instead.
#
# The </dev/null on each \`compose run\` is load-bearing. This script arrives on
# the remote's stdin (bash -s), and \`compose run\` attaches stdin to the
# container, swallowing every remaining line of the deploy. -T alone does not
# prevent it — it only disables the TTY. The symptom was brutal: the migration
# ran, then the container restart and every check after it silently never
# happened, and the deploy reported success while still serving the old image.
echo "migrating database…"
\$DC up -d postgres
\$DC run --rm --no-deps -T api alembic upgrade head </dev/null

# Refuse to ship models that have drifted from the migrations.
if ! \$DC run --rm --no-deps -T api alembic check </dev/null; then
  echo "FATAL: models have changes with no migration. Run:" >&2
  echo "  cd backend && alembic revision --autogenerate -m 'describe change'" >&2
  exit 1
fi

# --force-recreate is not optional: after a rebuild, plain \`up -d\` left the
# long-running containers on the previous image, so a deploy reported success
# while still serving the old code. A deploy is an explicit action; always
# replacing the containers costs a few seconds and removes the entire class of
# "I deployed but nothing changed".
\$DC up -d --force-recreate --remove-orphans \$services

echo
\$DC ps --format 'table {{.Service}}\t{{.Status}}\t{{.Ports}}'

# Prove the running containers are on the image we just built, rather than
# trusting that they are.
echo
for svc in api worker web; do
  case " \${services:-api worker web} " in *" \$svc "*) ;; *) continue ;; esac
  running=\$(sudo docker inspect "myth-buster-\${svc}-1" --format '{{.Image}}' 2>/dev/null || echo none)
  tagged=\$(sudo docker image inspect "myth-buster-\$svc" --format '{{.Id}}' 2>/dev/null || echo none)
  if [ "\$running" = "\$tagged" ]; then
    echo "  \$svc is running the current image"
  else
    echo "  FATAL: \$svc is running a stale image (\${running:0:19} != \${tagged:0:19})" >&2
    exit 1
  fi
done
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
