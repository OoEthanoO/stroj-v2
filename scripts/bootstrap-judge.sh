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
say "Installing docker and caddy"
sudo apt-get update -qq
sudo apt-get install -y -qq docker.io caddy iptables-persistent
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
docker build -t "$IMAGE" .
docker volume create "$VOLUME" >/dev/null

# ---------------------------------------------------------------- container
say "Starting the judge"
ADMIN_PASSWORD="${STROJ_ADMIN_PASSWORD:-$(head -c 18 /dev/urandom | base64 | tr -d '/+=')}"
WORKERS="${STROJ_WORKERS:-$(( $(nproc) > 2 ? $(nproc) - 1 : 1 ))}"

# A backstop, not a working limit. The judge's own per-submission RSS monitor
# should always bind first; sizing this tightly risks the cgroup OOM killer
# taking out the judge instead of the submission that misbehaved.
TOTAL_MB=$(free -m | awk '/^Mem:/{print $2}')
MEM_LIMIT_MB=$(( TOTAL_MB * 3 / 4 ))

docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
docker run -d --name "$CONTAINER" \
    --network "$NETWORK" \
    --publish "127.0.0.1:$PORT:$PORT" \
    --volume "$VOLUME:/data" \
    --env "STROJ_ADMIN_PASSWORD=$ADMIN_PASSWORD" \
    --env "STROJ_WORKERS=$WORKERS" \
    --restart unless-stopped \
    --memory "${MEM_LIMIT_MB}m" \
    --pids-limit 512 \
    "$IMAGE" >/dev/null

# Bound to 127.0.0.1 above, so the container is only reachable through Caddy,
# which owns TLS.
say "Configuring Caddy for $DOMAIN"
echo "$DOMAIN {
    reverse_proxy 127.0.0.1:$PORT
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
docker exec "$CONTAINER" /usr/bin/python3 - <<'PY' || true
import socket
try:
    socket.setdefaulttimeout(5)
    socket.create_connection(("1.1.1.1", 53))
    print("  FAIL: the container reached the internet. Egress is NOT blocked.")
    print("        Do not open registration until this is fixed.")
except OSError as exc:
    print(f"  ok: egress blocked ({type(exc).__name__})")
PY

say "Health"
curl -fsS "http://127.0.0.1:$PORT/healthz" && echo

cat <<EOF

------------------------------------------------------------------
Judge is up on https://$DOMAIN (once DNS points here and Caddy has
issued a certificate).

  admin password: $ADMIN_PASSWORD

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
