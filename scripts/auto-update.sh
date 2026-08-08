#!/usr/bin/env bash
#
# Redeploy the judge when main moves.
#
#   ./scripts/auto-update.sh --install judge.ethanyanxu.com   # set up the timer
#   ./scripts/auto-update.sh judge.ethanyanxu.com             # one check (the timer calls this)
#
# The server polls GitHub rather than GitHub pushing to the server. That means
# no deploy key with root access sitting in a CI secret store, and nothing new
# listening on the network — which matters on a box that runs untrusted code.
set -euo pipefail

INSTALL=0
if [ "${1:-}" = "--install" ]; then INSTALL=1; shift; fi

DOMAIN="${1:-}"
if [ -z "$DOMAIN" ]; then
    echo "usage: $0 [--install] <judge-hostname>" >&2
    exit 2
fi

CONTAINER=stroj-judge
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

log() { printf '%s  %s\n' "$(date -u +%FT%TZ)" "$*"; }

# ------------------------------------------------------------------ install
if [ "$INSTALL" -eq 1 ]; then
    cat >/etc/systemd/system/stroj-update.service <<UNIT
[Unit]
Description=Redeploy stroj when main moves
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$REPO_DIR
ExecStart=$REPO_DIR/scripts/auto-update.sh $DOMAIN
UNIT

    cat >/etc/systemd/system/stroj-update.timer <<'UNIT'
[Unit]
Description=Check for stroj updates

[Timer]
OnBootSec=3min
OnUnitActiveSec=5min
# Stops every judge on the planet hammering GitHub on the same second.
RandomizedDelaySec=60
Unit=stroj-update.service

[Install]
WantedBy=timers.target
UNIT

    systemctl daemon-reload
    systemctl enable --now stroj-update.timer
    log "timer installed; checking every 5 minutes"
    systemctl list-timers stroj-update.timer --no-pager | head -3
    exit 0
fi

# ------------------------------------------------------------------- check
git fetch -q origin main
local_rev="$(git rev-parse HEAD)"
remote_rev="$(git rev-parse origin/main)"

if [ "$local_rev" = "$remote_rev" ]; then
    exit 0
fi
log "update available: ${local_rev:0:7} -> ${remote_rev:0:7}"

# A redeploy rebuilds the image and restarts the container. In-flight
# submissions get requeued rather than lost, but the site is down for a minute
# and verdicts stall — which is the worst possible time to do it. Wait.
if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    if docker exec -i "$CONTAINER" python - <<'PY'
import sys
sys.path.insert(0, "/app")
from stroj import contest, db

rows = db.query("SELECT * FROM contests")
running = [r for r in rows if contest.state_of(r) == contest.RUNNING]
for row in running:
    print(f"contest '{row['slug']}' is live until {row['ends_at']}")
sys.exit(0 if running else 1)
PY
    then
        log "deferring: a contest is running"
        exit 0
    fi
fi

# Carry the running container's configuration across the redeploy. Without
# this, bootstrap would fall back to its defaults and silently flip
# registration back to invite-only.
carry() {
    local value
    value="$(docker inspect "$CONTAINER" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
        | grep "^$1=" | head -1 | cut -d= -f2- || true)"
    if [ -n "$value" ]; then
        export "$1=$value"
        log "carrying over $1"
    fi
}
carry STROJ_REGISTRATION
carry STROJ_INVITE_CODE
carry STROJ_ADMIN_PASSWORD
carry STROJ_WORKERS

# --ff-only so a dirty or diverged checkout fails loudly instead of merging.
git merge --ff-only "$remote_rev"

log "redeploying"
./scripts/bootstrap-judge.sh "$DOMAIN"
log "now at $(git rev-parse --short HEAD)"
