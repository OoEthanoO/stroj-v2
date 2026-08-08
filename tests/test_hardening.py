"""Protections that only matter once the judge faces the public internet."""

from __future__ import annotations

import os

import pytest

from stroj import config, ratelimit
from stroj.judge import sandbox


class TestRateLimiter:
    def test_allows_up_to_the_limit(self):
        limiter = ratelimit.RateLimiter(3, 60)
        for _ in range(3):
            assert limiter.check("k") == 0.0
            limiter.hit("k")
        assert limiter.check("k") > 0

    def test_keys_are_independent(self):
        limiter = ratelimit.RateLimiter(1, 60)
        limiter.hit("a")
        assert limiter.check("a") > 0
        assert limiter.check("b") == 0.0

    def test_window_expires(self):
        limiter = ratelimit.RateLimiter(1, 0.05)
        limiter.hit("k")
        assert limiter.check("k") > 0
        import time

        time.sleep(0.08)
        assert limiter.check("k") == 0.0

    def test_reset_clears_one_key(self):
        limiter = ratelimit.RateLimiter(1, 60)
        limiter.hit("k")
        limiter.reset("k")
        assert limiter.check("k") == 0.0

    def test_check_does_not_consume(self):
        limiter = ratelimit.RateLimiter(1, 60)
        for _ in range(5):
            assert limiter.check("k") == 0.0

    def test_expired_keys_are_not_retained(self):
        """An attacker rotating keys must not grow the dict without bound."""
        limiter = ratelimit.RateLimiter(1, 0.01)
        for i in range(50):
            limiter.hit(f"key-{i}")
        import time

        time.sleep(0.05)
        for i in range(50):
            limiter.check(f"key-{i}")
        assert len(limiter._hits) == 0


class TestLoginThrottling:
    def _fail_login(self, client, username="victim"):
        return client.post(
            "/api/auth/login", json={"username": username, "password": "wrong-guess"}
        )

    def test_brute_force_is_throttled(self, client, monkeypatch):
        monkeypatch.setattr(config, "LOGIN_ATTEMPTS", 10)
        client.post(
            "/api/auth/register", json={"username": "victim", "password": "password123"}
        )
        client.post("/api/auth/logout")

        codes = [self._fail_login(client).status_code for _ in range(14)]
        assert 401 in codes, "early attempts should just be rejected"
        assert 429 in codes, "sustained guessing must start being refused"
        assert codes[-1] == 429

    def test_throttle_response_says_how_long(self, client):
        for _ in range(40):
            response = self._fail_login(client, "ghost")
            if response.status_code == 429:
                assert "Retry-After" in response.headers
                assert int(response.headers["Retry-After"]) > 0
                return
        pytest.fail("throttling never engaged")

    def test_a_correct_password_still_works_under_load(self, client):
        """Only failures count, so one user's mistakes cannot lock out another."""
        client.post(
            "/api/auth/register", json={"username": "steady", "password": "password123"}
        )
        client.post("/api/auth/logout")
        for _ in range(6):
            self._fail_login(client, "someone-else")
        response = client.post(
            "/api/auth/login", json={"username": "steady", "password": "password123"}
        )
        assert response.status_code == 200

    def test_success_clears_the_counter(self, client):
        client.post(
            "/api/auth/register", json={"username": "recover", "password": "password123"}
        )
        client.post("/api/auth/logout")
        for _ in range(5):
            self._fail_login(client, "recover")
        assert client.post(
            "/api/auth/login", json={"username": "recover", "password": "password123"}
        ).status_code == 200
        # The earlier failures must not still be held against them.
        client.post("/api/auth/logout")
        assert self._fail_login(client, "recover").status_code == 401


class TestRegistrationThrottling:
    def test_account_spam_is_capped(self, client, monkeypatch):
        monkeypatch.setattr(config, "REGISTRATION", "open")
        codes = []
        for i in range(config.REGISTER_LIMIT + 3):
            codes.append(client.post("/api/auth/register", json={
                "username": f"spam{i}", "password": "password123"}).status_code)
        assert 200 in codes
        assert codes[-1] == 429


class TestSubmissionThrottling:
    def test_sustained_submitting_is_capped(self, client, admin_client, monkeypatch):
        from tests.test_api import make_problem

        monkeypatch.setattr(config, "SUBMIT_LIMIT", 4)
        # Rebuild the limiter so the patched limit takes effect.
        from stroj.api import routes_submissions

        monkeypatch.setattr(
            routes_submissions, "_submit_limiter", ratelimit.RateLimiter(4, 300)
        )
        make_problem(admin_client)

        codes = []
        for _ in range(7):
            codes.append(admin_client.post("/api/submissions", json={
                "problem": "a-plus-b", "language": "python3",
                "source": "print(1)"}).status_code)
        assert codes.count(200) == 4
        assert codes[-1] == 429


class TestPrivilegeSeparation:
    def test_reports_honestly_when_unavailable(self):
        """Running unprivileged, there is no separate account to drop to — and
        the judge must say so rather than imply isolation it does not have."""
        sandbox.privilege_drop_target.cache_clear()
        target = sandbox.privilege_drop_target()
        if os.geteuid() != 0:
            assert target is None
        else:
            assert target is None or target[0] != 0

    def test_config_exposes_the_state(self, client):
        body = client.get("/api/config").json()
        assert "privilege_separation" in body
        assert body["privilege_separation"] is (
            sandbox.privilege_drop_target() is not None
        )

    def test_never_drops_to_root(self, monkeypatch):
        """A misconfigured runner account pointing at uid 0 must be refused,
        not silently honoured."""
        import pwd

        sandbox.privilege_drop_target.cache_clear()
        monkeypatch.setattr(os, "geteuid", lambda: 0)
        fake = pwd.struct_passwd(("root", "x", 0, 0, "", "/root", "/bin/sh"))
        monkeypatch.setattr(pwd, "getpwnam", lambda name: fake)
        assert sandbox.privilege_drop_target() is None
        sandbox.privilege_drop_target.cache_clear()

    def test_missing_runner_account_is_refused(self, monkeypatch):
        import pwd

        sandbox.privilege_drop_target.cache_clear()
        monkeypatch.setattr(os, "geteuid", lambda: 0)

        def missing(name):
            raise KeyError(name)

        monkeypatch.setattr(pwd, "getpwnam", missing)
        assert sandbox.privilege_drop_target() is None
        sandbox.privilege_drop_target.cache_clear()


class TestDataDirectoryProtection:
    def test_is_a_no_op_when_unprivileged(self, isolated_data):
        """Must not throw on a developer machine, where there is no root."""
        config.protect_data_dir()
        assert isolated_data.exists()


class TestVersionReporting:
    """The frontend and backend deploy independently, so each must be able to
    say which commit it is."""

    def test_version_endpoint(self, client):
        body = client.get("/api/version").json()
        assert set(body) >= {"commit", "short", "version", "started_at"}
        assert body["short"] == body["commit"][:7]

    def test_reports_the_baked_in_commit(self, client, monkeypatch):
        from stroj import config

        monkeypatch.setattr(config, "COMMIT", "a" * 40)
        assert client.get("/api/version").json()["commit"] == "a" * 40

    def test_falls_back_to_the_checkout(self, monkeypatch):
        """Running from source there is no stamp, so it should read git rather
        than reporting 'unknown' for the common local case."""
        from stroj import config

        monkeypatch.setattr(config, "COMMIT", "unknown")
        resolved = config.commit()
        assert resolved == "unknown" or len(resolved) == 40

    def test_unknown_when_there_is_no_git_either(self, monkeypatch):
        import subprocess

        from stroj import config

        monkeypatch.setattr(config, "COMMIT", "unknown")

        def no_git(*args, **kwargs):
            raise OSError("git missing")

        monkeypatch.setattr(subprocess, "run", no_git)
        assert config.commit() == "unknown"
