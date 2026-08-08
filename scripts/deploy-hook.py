#!/usr/bin/env python3
"""Redeploy the judge the moment `main` moves, driven by a GitHub webhook.

Runs **on the host**, not inside the judge container. That is deliberate: this
process launches a deploy, and a container that can make the host run commands
is an escape hatch with extra steps.

Authentication is GitHub's HMAC signature over the request body. That is the
whole security boundary, so it is checked before the payload is even parsed,
compared in constant time, and a request without a signature is rejected rather
than treated as unsigned-but-fine.

Stdlib only — this sits outside the judge's virtualenv.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

log = logging.getLogger("deploy-hook")

#: Bodies are small; anything larger is not a push event we care about.
MAX_BODY = 1 << 20


def valid_signature(body: bytes, header: str | None, secret: bytes) -> bool:
    """Check GitHub's ``X-Hub-Signature-256`` over the raw body.

    Takes the secret as an argument rather than reading global state so the
    check — the only thing standing between the internet and a deploy — can be
    tested directly.
    """
    if not secret:
        return False
    if not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


def should_deploy(event: str | None, payload: dict, branch: str) -> tuple[bool, str]:
    """Decide whether this delivery means "main moved"."""
    if event == "ping":
        return False, "pong"
    if event != "push":
        return False, f"ignoring {event!r} event"
    ref = payload.get("ref")
    if ref != branch:
        return False, f"ignoring push to {ref!r}"
    if payload.get("deleted"):
        return False, "ignoring branch deletion"
    return True, "deploying"


class Handler(BaseHTTPRequestHandler):
    secret = b""
    updater = ""
    domain = ""
    branch = "refs/heads/main"

    def _reply(self, code: int, message: str) -> None:
        body = message.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's interface
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._reply(400, "bad length")
            return
        if length > MAX_BODY:
            self._reply(413, "too large")
            return
        body = self.rfile.read(length)

        if not valid_signature(body, self.headers.get("X-Hub-Signature-256"), self.secret):
            # Deliberately terse: a precise error would help someone guessing.
            log.warning("rejected a delivery with a bad or missing signature")
            self._reply(401, "bad signature")
            return

        try:
            payload = json.loads(body or b"{}")
        except json.JSONDecodeError:
            self._reply(400, "bad json")
            return

        go, reason = should_deploy(
            self.headers.get("X-GitHub-Event"), payload, self.branch
        )
        log.info("%s", reason)
        if not go:
            self._reply(200, reason)
            return

        # Answer straight away: a deploy takes minutes and GitHub times out in
        # seconds. The updater is detached so it outlives this request, and it
        # takes its own lock, so overlapping pushes queue rather than collide.
        subprocess.Popen(
            [self.updater, self.domain],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
        )
        self._reply(202, "deploying")

    def do_GET(self) -> None:  # noqa: N802
        self._reply(200, "stroj deploy hook")

    def log_message(self, fmt: str, *args) -> None:
        log.info("%s - %s", self.address_string(), fmt % args)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    secret = os.environ.get("STROJ_WEBHOOK_SECRET", "").strip()
    if not secret:
        print("error: STROJ_WEBHOOK_SECRET is not set", file=sys.stderr)
        return 2
    domain = os.environ.get("STROJ_DOMAIN", "").strip()
    if not domain:
        print("error: STROJ_DOMAIN is not set", file=sys.stderr)
        return 2

    Handler.secret = secret.encode()
    Handler.domain = domain
    Handler.updater = os.environ.get(
        "STROJ_UPDATER", "/root/stroj-v2/scripts/auto-update.sh"
    )
    Handler.branch = os.environ.get("STROJ_BRANCH", "refs/heads/main")

    port = int(os.environ.get("STROJ_HOOK_PORT", "8787"))
    # Loopback only. Caddy terminates TLS and forwards; nothing should reach
    # this directly from the network.
    server = HTTPServer(("127.0.0.1", port), Handler)
    log.info("listening on 127.0.0.1:%d, deploying %s on %s", port, domain, Handler.branch)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
