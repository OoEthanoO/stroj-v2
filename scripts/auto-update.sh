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

take_deploy_lock() {
    # Two things can start a deploy now — the webhook and the fallback timer —
    # and two concurrent bootstraps would fight over the image and container.
    # Hold the lock on a file descriptor for the life of the script; a second
    # caller gives up rather than queueing, since whatever it wanted deployed
    # the running one is already picking up.
    command -v flock >/dev/null 2>&1 || return 0
    exec 9>/var/lock/stroj-update.lock || return 0
    if ! flock --nonblock 9; then
        log "another deploy is already running; nothing to do"
        exit 0
    fi
}

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

    # The webhook is what makes a deploy immediate. The timer stays as a safety
    # net for the deliveries it misses — GitHub gives up after a few retries,
    # and a webhook that silently stops is the classic way CD rots — so it runs
    # rarely rather than often.
    cat >/etc/systemd/system/stroj-update.timer <<'UNIT'
[Unit]
Description=Fallback check for stroj updates

[Timer]
OnBootSec=3min
OnUnitActiveSec=30min
RandomizedDelaySec=120
Unit=stroj-update.service

[Install]
WantedBy=timers.target
UNIT

    # --- webhook receiver ---------------------------------------------------
    SECRET_FILE=/etc/stroj-webhook.secret
    if [ ! -s "$SECRET_FILE" ]; then
        head -c 32 /dev/urandom | base64 | tr -d '/+=' > "$SECRET_FILE"
        chmod 600 "$SECRET_FILE"
    fi
    WEBHOOK_SECRET="$(cat "$SECRET_FILE")"

    cat >/etc/systemd/system/stroj-deploy-hook.service <<UNIT
[Unit]
Description=GitHub webhook receiver that redeploys stroj
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/python3 $REPO_DIR/scripts/deploy-hook.py
Environment=STROJ_DOMAIN=$DOMAIN
Environment=STROJ_UPDATER=$REPO_DIR/scripts/auto-update.sh
EnvironmentFile=-/etc/stroj-hook.env
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
    printf 'STROJ_WEBHOOK_SECRET=%s\n' "$WEBHOOK_SECRET" > /etc/stroj-hook.env
    chmod 600 /etc/stroj-hook.env

    systemctl daemon-reload
    systemctl enable --now stroj-update.timer
    systemctl enable --now stroj-deploy-hook.service

    log "installed"
    cat <<EOF

------------------------------------------------------------------
Add this webhook at
https://github.com/OoEthanoO/stroj-v2/settings/hooks/new

  Payload URL   https://$DOMAIN/_deploy/hook
  Content type  application/json
  Secret        $WEBHOOK_SECRET
  Events        Just the push event

Pushes then deploy within seconds. The timer still runs every 30
minutes as a fallback for deliveries GitHub fails to make.

  journalctl -u stroj-deploy-hook -f    # what the receiver sees
  journalctl -u stroj-update -f         # what the deploy does
------------------------------------------------------------------
EOF
    exit 0
fi

# ------------------------------------------------------------------- check
take_deploy_lock

git fetch -q origin main
remote_rev="$(git rev-parse origin/main)"

# Compare what is actually RUNNING against the remote, not what is checked out.
# The commit is baked into the image at build time, so a `git pull` on the box
# moves the checkout without rebuilding anything — and a checkout-to-remote
# comparison would then report "up to date" forever while the container stayed
# on an old commit. Ask the container what it is.
deployed="$(docker inspect "$CONTAINER" \
    --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
    | sed -n 's/^STROJ_COMMIT=//p' | head -1)"

if [ "$deployed" = "$remote_rev" ]; then
    exit 0
fi
log "running ${deployed:0:7}${deployed:+ }-> deploying ${remote_rev:0:7}"

# A redeploy used to be deferred while a contest was live. It no longer is: a
# patch pushed mid-contest is almost always the fix for something breaking that
# contest, so making it wait until the contest ends is exactly backwards. The
# cost of restarting is bounded instead — in-flight submissions are requeued
# and judged again, and the site refuses to load against a half-updated
# backend rather than showing a broken one.

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
# Mail. Losing these across a redeploy would not fail loudly — the judge falls
# back to writing confirmation links to its log, so signups would keep
# "working" while nobody could finish one.
carry STROJ_BASE_URL
carry STROJ_SITE_NAME
carry STROJ_SMTP_HOST
carry STROJ_SMTP_PORT
carry STROJ_SMTP_USER
carry STROJ_SMTP_PASSWORD
carry STROJ_SMTP_STARTTLS
carry STROJ_SMTP_SSL
carry STROJ_MAIL_FROM

# --ff-only so a dirty or diverged checkout fails loudly instead of merging.
git merge --ff-only "$remote_rev"

log "redeploying"
./scripts/bootstrap-judge.sh "$DOMAIN"
log "now at $(git rev-parse --short HEAD)"
