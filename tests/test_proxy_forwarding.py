"""Exercise the real home -> judge Caddy chain and sign-in rate limits.

Requires Caddy on PATH (or CADDY_BIN); all traffic and data stay local.
"""

from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from stroj import auth, ratelimit
from stroj.api import routes_auth


ROOT = Path(__file__).resolve().parent.parent


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def proxy_chain(client, tmp_path):
    caddy = os.environ.get("CADDY_BIN") or shutil.which("caddy")
    if not caddy:
        pytest.skip("Install Caddy or set CADDY_BIN to test proxy forwarding")

    class Backend(BaseHTTPRequestHandler):
        def handle_request(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if self.path in ("/api/_inspect", "/_deploy/inspect"):
                status, payload = 200, json.dumps(dict(self.headers)).encode()
                headers = {}
            else:
                result = client.request(
                    self.command, self.path, content=body,
                    headers=dict(self.headers),
                )
                status, payload, headers = result.status_code, result.content, result.headers
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            if "retry-after" in headers:
                self.send_header("Retry-After", headers["retry-after"])
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_POST = handle_request

        def log_message(self, *args):
            pass

    backend = ThreadingHTTPServer(("127.0.0.1", 0), Backend)
    thread = threading.Thread(target=backend.serve_forever, daemon=True)
    thread.start()
    home_port, judge_port, check_port = free_port(), free_port(), free_port()
    key = secrets.token_hex(32)
    private = tmp_path / "private"
    private.mkdir()
    home_key = private / "proxy-auth.caddy"
    home_key.write_text(f"header_up X-Stroj-Proxy-Key {key}\n")
    (private / "home.caddy").write_text(f"import stroj_frontend {key}\n")

    home = (ROOT / "deploy/home/Caddyfile").read_text()
    home = home.replace("https://judge.ethanyanxu.com", f"http://127.0.0.1:{judge_port}")
    home = home.replace("header_up Host judge.ethanyanxu.com", f"header_up Host 127.0.0.1:{judge_port}")
    home = home.replace("C:/Users/ethan/stroj/private/proxy-auth.caddy", home_key.as_posix())
    home = home.replace("C:/Users/ethan/stroj/current", tmp_path.as_posix())
    home = home.replace("C:/Users/ethan/stroj/logs/access.log", (tmp_path / "access.log").as_posix())
    home = home.replace("stroj.ethanyanxu.com, oj.ethanyanxu.com", f"http://127.0.0.1:{home_port}")
    home = home.replace("http://127.0.0.1:8097", f"http://127.0.0.1:{check_port}")

    judge = (ROOT / "deploy/judge/Caddyfile").read_text()
    judge = judge.replace("{$STROJ_JUDGE_DOMAIN}", f"http://127.0.0.1:{judge_port}")
    judge = judge.replace("127.0.0.1:8000", f"127.0.0.1:{backend.server_port}")
    judge = judge.replace("127.0.0.1:8787", f"127.0.0.1:{backend.server_port}")
    judge = judge.replace("/etc/caddy/stroj-frontend/*.caddy", (private / "home.caddy").as_posix())
    config = tmp_path / "Caddyfile"
    config.write_text("{\n admin off\n auto_https off\n}\n" + home + "\n" + judge)

    def request(port, source="127.0.0.2", path="/api/_inspect", headers=None, body=None):
        connection = http.client.HTTPConnection(
            "127.0.0.1", port, timeout=10, source_address=(source, 0),
        )
        request_headers = {"Content-Type": "application/json", **(headers or {})}
        try:
            connection.request("POST" if body is not None else "GET", path,
                               json.dumps(body) if body is not None else None, request_headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read()), dict(response.getheaders())
        finally:
            connection.close()

    with (tmp_path / "caddy.log").open("w+") as log:
        proc = subprocess.Popen(
            [caddy, "run", "--config", str(config), "--adapter", "caddyfile"],
            stdout=log, stderr=log,
            env={**os.environ, "XDG_CONFIG_HOME": str(tmp_path), "XDG_DATA_HOME": str(tmp_path)},
        )
        try:
            for _ in range(100):
                if proc.poll() is not None:
                    log.seek(0)
                    pytest.fail(log.read())
                try:
                    if request(home_port)[0] == 200:
                        break
                except OSError:
                    pass
                time.sleep(0.05)
            else:
                pytest.fail("Caddy did not become ready")
            yield request, home_port, judge_port, key
        finally:
            proc.terminate()
            proc.wait(timeout=10)
            backend.shutdown()
            backend.server_close()
            thread.join(timeout=5)


@pytest.mark.parametrize("source", ["127.0.0.2", "127.0.0.3"])
def test_home_preserves_socket_address_and_strips_key(proxy_chain, source):
    request, home, _, _ = proxy_chain
    status, headers, _ = request(home, source=source, headers={
        "X-Forwarded-For": "198.51.100.99, 2001:db8::1",
        "X-Stroj-Proxy-Key": "visitor-supplied-key",
    })
    assert status == 200
    headers = {k.lower(): v for k, v in headers.items()}
    assert headers["x-forwarded-for"] == source
    assert "x-stroj-proxy-key" not in headers


@pytest.mark.parametrize("key", [None, "wrong-key"])
def test_direct_judge_access_cannot_spoof_an_address(proxy_chain, key):
    request, _, judge, _ = proxy_chain
    headers = {"X-Forwarded-For": "198.51.100.99"}
    if key:
        headers["X-Stroj-Proxy-Key"] = key
    status, received, _ = request(judge, source="127.0.0.4", headers=headers)
    assert status == 200
    received = {k.lower(): v for k, v in received.items()}
    assert received["x-forwarded-for"] == "127.0.0.4"
    assert "x-stroj-proxy-key" not in received


def test_authenticated_ipv6_address_is_preserved(proxy_chain):
    request, _, judge, key = proxy_chain
    status, received, _ = request(judge, headers={
        "X-Forwarded-For": "2001:db8::42", "X-Stroj-Proxy-Key": key,
    })
    assert status == 200
    received = {k.lower(): v for k, v in received.items()}
    assert received["x-forwarded-for"] == "2001:db8::42"
    assert "x-stroj-proxy-key" not in received


def test_flooding_one_visitor_does_not_block_another(proxy_chain, monkeypatch):
    request, home, _, _ = proxy_chain
    monkeypatch.setattr(routes_auth, "_login_by_client", ratelimit.RateLimiter(3, 300))
    auth.create_user("healthy", "password123", email="healthy@example.test")
    for i in range(4):
        status, _, headers = request(home, path="/api/auth/login", headers={
            "X-Forwarded-For": f"198.51.100.{i + 1}",
        }, body={"username": f"unknown{i}", "password": "wrong"})
        assert status == (401 if i < 3 else 429)
    assert int(headers["Retry-After"]) > 0
    status, _, _ = request(home, source="127.0.0.3", path="/api/auth/login", body={
        "username": "healthy", "password": "password123",
    })
    assert status == 200


def test_account_limit_still_applies_across_addresses(proxy_chain, monkeypatch):
    request, home, _, _ = proxy_chain
    monkeypatch.setattr(routes_auth, "_login_by_account", ratelimit.RateLimiter(2, 300))
    for i in range(3):
        status, _, _ = request(home, source=f"127.0.0.{i + 2}", path="/api/auth/login",
                               body={"username": "same-account", "password": "wrong"})
        assert status == (401 if i < 2 else 429)


def test_proxy_key_never_reaches_the_deploy_hook(proxy_chain):
    request, _, judge, key = proxy_chain
    status, received, _ = request(judge, path="/_deploy/inspect", headers={"X-Stroj-Proxy-Key": key})
    assert status == 200
    assert "x-stroj-proxy-key" not in {k.lower() for k in received}
