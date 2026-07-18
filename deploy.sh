#!/usr/bin/env bash
# one-shot deploy: commit + push local work, then pull + restart on the droplet.
#
#   ./deploy.sh "your commit message"
#
# the SINGLE argument is the commit message. everything else has a default that
# matches DEPLOY.md and can be overridden with an env var:
#   RARE_EARTH_SERVER_IP    default 167.99.234.25   (the droplet)
#   RARE_EARTH_SSH_USER     default root
#   RARE_EARTH_REMOTE_DIR   default /root/rare-earth (repo path on the droplet)
#   RARE_EARTH_SERVICE      default rare-earth       (systemd unit)
#   RARE_EARTH_RESTART_CMD  default: systemctl restart + status (override for
#                                    tmux/nohup setups)
#
# needs working SSH to the droplet (key-based is smoothest; a passphrase prompt
# is fine since you run this in a terminal). the droplet only ever pulls, and
# saves/ + settings.json are gitignored, so the remote tree stays fast-forwardable.
set -euo pipefail

cd "$(dirname "$0")"

if [ "$#" -lt 1 ] || [ -z "$1" ]; then
    echo "usage: ./deploy.sh \"commit message\"" >&2
    exit 1
fi
MSG="$1"

SERVER_IP="${RARE_EARTH_SERVER_IP:-167.99.234.25}"
SSH_USER="${RARE_EARTH_SSH_USER:-root}"
REMOTE_DIR="${RARE_EARTH_REMOTE_DIR:-/root/rare-earth}"
SERVICE="${RARE_EARTH_SERVICE:-rare-earth}"
RESTART_CMD="${RARE_EARTH_RESTART_CMD:-systemctl restart ${SERVICE} && systemctl --no-pager --lines=0 status ${SERVICE} | head -n 5}"

# --- 1. commit + push locally ---
echo "[deploy] staging changes..."
git add -A
if git diff --cached --quiet; then
    echo "[deploy] nothing to commit — redeploying current HEAD"
else
    git commit -m "$MSG"
fi

echo "[deploy] pushing to origin..."
git push

# --- 2. pull + restart on the droplet ---
echo "[deploy] ${SSH_USER}@${SERVER_IP}: pulling + restarting '${SERVICE}'..."
ssh "${SSH_USER}@${SERVER_IP}" "
    set -e
    cd '${REMOTE_DIR}'
    echo '[remote] pulling latest...'
    git pull --ff-only
    echo '[remote] restarting server...'
    ${RESTART_CMD}
"

echo
echo "[deploy] done -> ${SERVER_IP} updated and '${SERVICE}' restarted."
