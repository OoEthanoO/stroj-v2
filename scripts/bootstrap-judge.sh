#!/usr/bin/env bash
#
# Provision the judge backend on a fresh Ubuntu box (Oracle A1, Hetzner, any VPS).
#
#   ./scripts/bootstrap-judge.sh judge.ethanyanxu.com
#
# Idempotent: safe to re-run. Verifies rather than assumes — every security
# control it sets up is tested at the end and the results printed.
set -euo pipefail

DOMAIN="${1:-}"
if [ -z "$DOMAIN" ]; then
    echo "usage: $0 <judge-hostname>    e.g. $0 judge.ethanyanxu.com" >&2
    exit 2
fi

CONTAINER=stroj-judge
IMAGE=stroj-judge
VOLUME=stroj-data
BRIDGE=stroj0
NETWORK=stroj-net
PORT=8000

cd "$(dirname "$0")/.."

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

# ---------------------------------------------------------------- packages
# iptables-persistent asks, interactively, whether to save current rules — which
# would hang this script forever on a box nobody is watching.
export DEBIAN_FRONTEND=noninteractive

say "Installing docker and caddy"
sudo -E apt-get update -qq
sudo -E apt-get install -y -qq docker.io iptables-persistent

# Caddy is not in every Ubuntu release's archive. Use it if it is there, and
# fall back to the project's own repo rather than failing halfway through.
if ! apt-cache show caddy >/dev/null 2>&1; then
    say "Adding the Caddy repository"
    sudo -E apt-get install -y -qq debian-keyring debian-archive-keyring \
        apt-transport-https curl gnupg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
        | sudo gpg --batch --yes --dearmor \
              -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
        | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
    sudo -E apt-get update -qq
fi
sudo -E apt-get install -y -qq caddy

sudo systemctl enable --now docker

if ! docker info >/dev/null 2>&1; then
    sudo usermod -aG docker "$USER"
    echo "Added $USER to the docker group. Log out and back in, then re-run."
    exit 1
fi

# ------------------------------------------------------------------ network
# A dedicated bridge with a predictable name, so the egress rule below can
# target exactly this container and nothing else on the host.
say "Creating the container network"
if ! docker network inspect "$NETWORK" >/dev/null 2>&1; then
    docker network create "$NETWORK" \
        --opt "com.docker.network.bridge.name=$BRIDGE"
fi

# The judge needs no outbound internet at runtime. Dropping egress here is what
# actually denies submissions the network: inside a default container `unshare`
# cannot create a network namespace (no CAP_SYS_ADMIN, and seccomp blocks
# CLONE_NEWUSER), and granting SYS_ADMIN to get it back would undermine the
# container boundary that is doing the real work on Linux.
#
# DOCKER-USER is consulted before Docker's own rules and survives daemon
# restarts. Matching on -i (traffic *from* the bridge) leaves published-port
# ingress, which arrives with -o, untouched.
say "Blocking container egress"
if ! sudo iptables -C DOCKER-USER -i "$BRIDGE" -j DROP 2>/dev/null; then
    sudo iptables -I DOCKER-USER -i "$BRIDGE" -j DROP
fi
sudo netfilter-persistent save >/dev/null

# -------------------------------------------------------------------- image
say "Building the image"
# Stamp the image with the commit it was built from, so the running judge can
# say what it is rather than leaving you to guess.
BUILD_COMMIT="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
docker build --build-arg "STROJ_COMMIT=$BUILD_COMMIT" -t "$IMAGE" .
docker volume create "$VOLUME" >/dev/null

# ---------------------------------------------------------------- container
say "Starting the judge"
ADMIN_PASSWORD="${STROJ_ADMIN_PASSWORD:-$(head -c 18 /dev/urandom | base64 | tr -d '/+=')}"
WORKERS="${STROJ_WORKERS:-$(( $(nproc) > 2 ? $(nproc) - 1 : 1 ))}"

# Default to invite-only. A judge on a public URL that anyone can register on
# is a free code-execution service for the whole internet; a club wants one
# shared code instead. Override with STROJ_REGISTRATION=open.
REGISTRATION="${STROJ_REGISTRATION:-invite}"
INVITE_CODE="${STROJ_INVITE_CODE:-$(head -c 9 /dev/urandom | base64 | tr -d '/+=')}"

# A backstop, not a working limit. The judge's own per-submission RSS monitor
# should always bind first; sizing this tightly risks the cgroup OOM killer
# taking out the judge instead of the submission that misbehaved.
TOTAL_MB=$(free -m | awk '/^Mem:/{print $2}')
MEM_LIMIT_MB=$(( TOTAL_MB * 3 / 4 ))

docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
docker run -d --name "$CONTAINER" \
    --network "$NETWORK" \
    --publish "127.0.0.1:$PORT:$PORT" \
    `# Point DNS at the container itself so lookups fail. The judge resolves
     # nothing at runtime, and Docker's embedded resolver bypasses the host
     # egress rule entirely — leaving a lookup-based exfiltration channel.` \
    --dns 127.0.0.1 \
    --volume "$VOLUME:/data" \
    --env "STROJ_ADMIN_PASSWORD=$ADMIN_PASSWORD" \
    --env "STROJ_WORKERS=$WORKERS" \
    --env "STROJ_REGISTRATION=$REGISTRATION" \
    --env "STROJ_INVITE_CODE=$INVITE_CODE" \
    --restart unless-stopped \
    --memory "${MEM_LIMIT_MB}m" \
    --pids-limit 512 \
    `# Everything outside /data is immutable, so a submission cannot rewrite
     # the judge's own code or the compilers it will be judged by. /tmp is a
     # small tmpfs for the toolchains' scratch files (not noexec: some
     # compilers do execute out of their temporary directory).` \
    --read-only \
    --tmpfs /tmp:rw,nosuid,nodev,size=256m \
    `# The judge keeps root *inside* the container purely to drop each
     # submission to stroj-runner. SETUID/SETGID do the dropping, CHOWN hands
     # over the box, DAC_OVERRIDE and KILL let it then clean up and time out
     # processes it no longer owns. Everything else goes.` \
    --cap-drop ALL \
    --cap-add SETUID --cap-add SETGID --cap-add CHOWN \
    --cap-add DAC_OVERRIDE --cap-add KILL \
    --security-opt no-new-privileges \
    "$IMAGE" >/dev/null

# Bound to 127.0.0.1 above, so the container is only reachable through Caddy,
# which owns TLS.
say "Configuring Caddy for $DOMAIN"
# /_deploy/* goes to the webhook receiver on the host, everything else to the
# judge container. The receiver is a separate process precisely so the
# container never gains the ability to make the host run anything.
echo "$DOMAIN {
    handle /_deploy/* {
        reverse_proxy 127.0.0.1:8787
    }
    handle {
        reverse_proxy 127.0.0.1:$PORT
    }
}" | sudo tee /etc/caddy/Caddyfile >/dev/null
sudo systemctl restart caddy

# ------------------------------------------------------------ verification
say "Waiting for the judge to come up"
for _ in $(seq 30); do
    if curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then break; fi
    sleep 1
done

say "Toolchains and isolation"
docker exec "$CONTAINER" python -m stroj doctor || true

say "Egress test (this MUST fail closed)"
# `docker exec` without -i does not attach stdin, so a heredoc script silently
# never runs and this check printed nothing at all.
docker exec -i "$CONTAINER" /usr/bin/python3 - <<'PY' || true
import socket

socket.setdefaulttimeout(6)
failures = 0

try:
    socket.create_connection(("1.1.1.1", 53))
    print("  FAIL: the container reached the internet over TCP.")
    failures += 1
except OSError as exc:
    print(f"  ok: TCP egress blocked ({type(exc).__name__})")

# DNS leaves through Docker's embedded resolver, which is a host-local path and
# so never traverses the FORWARD chain the DOCKER-USER rule lives on. Blocking
# TCP is not enough: name lookups alone are an exfiltration channel.
try:
    address = socket.gethostbyname("cloudflare.com")
    print(f"  FAIL: DNS resolved ({address}) — submissions can exfiltrate by lookup.")
    failures += 1
except OSError as exc:
    print(f"  ok: DNS blocked ({type(exc).__name__})")

if failures:
    print("  *** Submissions are NOT fully isolated. ***")
PY

say "Health"
curl -fsS "http://127.0.0.1:$PORT/healthz" && echo

cat <<EOF

------------------------------------------------------------------
Judge is up on https://$DOMAIN (once DNS points here and Caddy has
issued a certificate).

  admin password: $ADMIN_PASSWORD
  registration  : $REGISTRATION
  invite code   : $INVITE_CODE   (share this with club members)
  judge workers : $WORKERS

Next:
  1. Point an A record for $DOMAIN at this machine's public IP.
     On Oracle, also open 80/443 in the VCN security list.
  2. docker exec $CONTAINER python -m stroj seed     # sample content
  3. docker exec $CONTAINER python -m stroj passwd admin
  4. Set that hostname as JUDGE_ORIGIN in vercel.json, drop
     STROJ_FRONTEND_ONLY from the Vercel project, and push.

Re-read the egress test above before opening registration.
------------------------------------------------------------------
EOF
