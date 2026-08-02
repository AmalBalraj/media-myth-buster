#!/usr/bin/env bash
# Ship an Instagram cookie export to the server and point the workers at it.
#
#   ./deploy/push-cookies.sh ~/Downloads/instagram-cookies.txt
#
# Generate the file on YOUR machine, never on the server: Instagram scores
# datacenter IPs as high-risk, and logging in from one is a fast route to a
# checkpointed account. See README §2 Path A.
#
# The file lands in data/, which is excluded from rsync, so deploys never
# clobber it. Inside the containers it appears at /data/instagram-cookies.txt.
set -euo pipefail

LOCAL_FILE="${1:-}"
HOST="${MYTH_HOST:-oracle-dev}"
REMOTE_DIR="${MYTH_REMOTE_DIR:-/home/amal/myth-buster}"
CONTAINER_PATH="/data/instagram-cookies.txt"
REMOTE_FILE="$REMOTE_DIR/data/instagram-cookies.txt"

say() { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

[ -n "$LOCAL_FILE" ] || die "usage: $0 <path-to-cookies.txt>"
[ -f "$LOCAL_FILE" ] || die "no such file: $LOCAL_FILE"

say "Validating $LOCAL_FILE"

head -1 "$LOCAL_FILE" | grep -qi "netscape\|^#" \
  || die "Not a Netscape-format cookie file (first line should be a '# Netscape...' comment).
Re-export with a 'cookies.txt' browser extension, not a JSON export."

grep -q "instagram\.com" "$LOCAL_FILE" \
  || die "No instagram.com cookies in this file — wrong site exported?"

grep -qE "^\.?instagram\.com.*[[:space:]]sessionid[[:space:]]" "$LOCAL_FILE" \
  || die "No 'sessionid' cookie found. You are not logged in, or the export was
filtered. Log in, reload instagram.com, then export again."

# A sessionid expiring within a week means a re-export is imminent; better to
# know now than when a reel silently fails to resolve.
expiry=$(awk '$6 == "sessionid" && $1 ~ /instagram\.com/ {print $5; exit}' "$LOCAL_FILE")
if [ -n "${expiry:-}" ] && [ "$expiry" -gt 0 ] 2>/dev/null; then
  now=$(date +%s)
  days=$(( (expiry - now) / 86400 ))
  if [ "$days" -lt 0 ]; then
    die "sessionid already expired. Log in again and re-export."
  elif [ "$days" -lt 7 ]; then
    echo "  warning: sessionid expires in $days day(s)"
  else
    echo "  sessionid valid for ~$days days"
  fi
fi

n=$(grep -c "instagram\.com" "$LOCAL_FILE")
echo "  $n instagram.com cookies, format OK"

say "Copying to $HOST"
scp -q "$LOCAL_FILE" "$HOST:$REMOTE_FILE"
ssh "$HOST" "chmod 600 '$REMOTE_FILE'"
echo "  $REMOTE_FILE (mode 600)"

say "Pointing .env at $CONTAINER_PATH"
ssh "$HOST" bash -s <<REMOTE
set -euo pipefail
cd "$REMOTE_DIR"
if grep -q '^YTDLP_COOKIES_FILE=' .env; then
  sed -i "s|^YTDLP_COOKIES_FILE=.*|YTDLP_COOKIES_FILE=$CONTAINER_PATH|" .env
else
  echo "YTDLP_COOKIES_FILE=$CONTAINER_PATH" >> .env
fi
sed -i 's|^ENABLE_YTDLP_FALLBACK=.*|ENABLE_YTDLP_FALLBACK=true|' .env
grep -E '^(ENABLE_YTDLP_FALLBACK|YTDLP_COOKIES_FILE)=' .env
REMOTE

say "Restarting workers"
ssh "$HOST" bash -s <<REMOTE
set -euo pipefail
cd "$REMOTE_DIR"
if docker info >/dev/null 2>&1; then DC="docker compose"; else DC="sudo docker compose"; fi
\$DC up -d api worker
\$DC exec -T worker test -r "$CONTAINER_PATH" \
  && echo "worker can read $CONTAINER_PATH" \
  || echo "WARNING: worker cannot read $CONTAINER_PATH"
REMOTE

say "Smoke test"
cat <<EOF
Resolve a real public reel end to end:

  ssh $HOST "cd $REMOTE_DIR && sudo docker compose exec -T worker \\
    yt-dlp --cookies $CONTAINER_PATH --skip-download --print title \\
    'https://www.instagram.com/reel/<SHORTCODE>/'"

A title means it works. "empty media response" means the session was rejected —
usually the account hit a checkpoint; open it in a browser and clear the prompt.
EOF
