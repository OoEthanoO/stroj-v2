"""Protections that only matter once the judge faces the public internet."""

from __future__ import annotations

import os
import pathlib

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


class TestProtectionSummary:
    """`isolation` alone reported "none" inside a container even with
    privilege separation active, which read as "nothing is protecting you"."""

    def test_privilege_separation_alone_is_not_none(self, monkeypatch):
        monkeypatch.setattr(sandbox, "sandbox_exec_available", lambda: False)
        monkeypatch.setattr(sandbox, "isolation_mode", lambda: "none")
        monkeypatch.setattr(sandbox, "privilege_drop_target", lambda: (10002, 10002))
        assert sandbox.protection_summary() == "separated"

    def test_netns_and_separation_combine(self, monkeypatch):
        monkeypatch.setattr(sandbox, "sandbox_exec_available", lambda: False)
        monkeypatch.setattr(sandbox, "isolation_mode", lambda: "unshare-net")
        monkeypatch.setattr(sandbox, "privilege_drop_target", lambda: (10002, 10002))
        assert sandbox.protection_summary() == "separated+netns"

    def test_sandbox_exec_wins(self, monkeypatch):
        monkeypatch.setattr(sandbox, "sandbox_exec_available", lambda: True)
        assert sandbox.protection_summary() == "full"

    def test_nothing_available_is_still_none(self, monkeypatch):
        monkeypatch.setattr(sandbox, "sandbox_exec_available", lambda: False)
        monkeypatch.setattr(sandbox, "isolation_mode", lambda: "none")
        monkeypatch.setattr(sandbox, "privilege_drop_target", lambda: None)
        assert sandbox.protection_summary() == "none"

    def test_config_exposes_it(self, client):
        body = client.get("/api/config").json()
        assert body["protection"] == sandbox.protection_summary()
        # The narrower field stays, for anyone who needs the mechanism itself.
        assert "isolation" in body


class TestSchemaMigration:
    """`CREATE TABLE IF NOT EXISTS` does nothing to a table that already
    exists, so every column added after the first release has to be retrofitted
    by hand — against a live database, on a deploy nobody is watching."""

    def _old_schema(self, path):
        import sqlite3

        conn = sqlite3.connect(path)
        conn.executescript(
            "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,"
            " role TEXT NOT NULL DEFAULT 'user', created_at TEXT NOT NULL);"
            "CREATE TABLE problems (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " slug TEXT NOT NULL UNIQUE, title TEXT NOT NULL,"
            " statement TEXT NOT NULL DEFAULT '', time_limit_ms INTEGER NOT NULL DEFAULT 1000,"
            " memory_limit_mb INTEGER NOT NULL DEFAULT 256, checker TEXT NOT NULL DEFAULT 'token',"
            " float_eps REAL NOT NULL DEFAULT 1e-6, partial INTEGER NOT NULL DEFAULT 0,"
            " visible INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);"
            "CREATE TABLE contests (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " slug TEXT NOT NULL UNIQUE, title TEXT NOT NULL,"
            " description TEXT NOT NULL DEFAULT '', starts_at TEXT NOT NULL,"
            " ends_at TEXT NOT NULL, scoring TEXT NOT NULL DEFAULT 'icpc',"
            " penalty_minutes INTEGER NOT NULL DEFAULT 20, created_at TEXT NOT NULL);"
        )
        conn.execute(
            "INSERT INTO users (username, password_hash, role, created_at)"
            " VALUES ('existing', 'hash', 'admin', 't')"
        )
        conn.execute(
            "INSERT INTO problems (slug, title, created_at) VALUES ('old', 'Old', 't')"
        )
        conn.commit()
        conn.close()

    def test_upgrades_a_pre_release_database(self, tmp_path, monkeypatch):
        from stroj import config, db

        path = tmp_path / "old.db"
        self._old_schema(path)
        monkeypatch.setattr(config, "DB_PATH", path)
        db.close()

        applied = db.init_db()
        assert "users.bio" in applied
        assert "problems.points" in applied
        assert "problems.author_id" in applied
        assert "contests.freeze_minutes" in applied
        db.close()

    def test_existing_rows_survive_with_defaults(self, tmp_path, monkeypatch):
        from stroj import config, db

        path = tmp_path / "old.db"
        self._old_schema(path)
        monkeypatch.setattr(config, "DB_PATH", path)
        db.close()
        db.init_db()

        user = db.one("SELECT username, role, bio FROM users")
        assert user["username"] == "existing" and user["role"] == "admin"
        assert user["bio"] == ""
        problem = db.one("SELECT slug, points, author_id FROM problems")
        assert problem["slug"] == "old"
        assert problem["points"] == 100      # not NULL — the column default applied
        assert problem["author_id"] is None  # unattributed, and nullable
        db.close()

    def test_is_idempotent(self, tmp_path, monkeypatch):
        """Every restart calls this. The second run must be a no-op, not an error."""
        from stroj import config, db

        path = tmp_path / "old.db"
        self._old_schema(path)
        monkeypatch.setattr(config, "DB_PATH", path)
        db.close()

        assert db.init_db() != []
        assert db.init_db() == []
        assert db.init_db() == []
        db.close()

    def test_every_declared_column_actually_exists_after_init(self, isolated_data):
        """Guards the reverse mistake: a column added to schema.sql but never
        listed for migration would work on a fresh database and fail on a live
        one — the failure you would only see in production."""
        from stroj import db

        db.init_db()
        for table, column, _ in db._ADDED_COLUMNS:
            names = {r["name"] for r in db.query(f"PRAGMA table_info({table})")}
            assert column in names, f"{table}.{column} missing"


class TestDataDirectoryModes:
    """The runner must be able to reach its own box while still being locked
    out of everything else. Getting `/data` wrong breaks Python submissions and
    only Python submissions, which is a miserable thing to debug."""

    def _modes(self):
        from stroj import config

        return dict(config.data_dir_modes())

    def test_data_dir_is_traversable_by_others(self):
        from stroj import config

        mode = self._modes()[config.DATA_DIR]
        assert mode & 0o001, "runner needs execute on /data to reach its box"
        assert not mode & 0o004, "but must not be able to list it"

    def test_work_dir_is_traversable_by_others(self):
        from stroj import config

        mode = self._modes()[config.WORK_DIR]
        assert mode & 0o001
        assert not mode & 0o004

    def test_problem_dir_is_not_reachable_at_all(self):
        """Answer files: traversal itself is the thing to deny."""
        from stroj import config

        assert self._modes()[config.PROBLEM_DIR] & 0o007 == 0

    def test_database_and_its_sidecars_are_owner_only(self):
        from stroj import config

        modes = self._modes()
        for suffix in ("", "-wal", "-shm", "-journal"):
            path = pathlib.Path(str(config.DB_PATH) + suffix)
            assert path in modes, f"{suffix or 'db'} not protected"
            assert modes[path] & 0o077 == 0, f"{suffix or 'db'} is group/other readable"

    def test_nothing_is_group_or_other_writable(self):
        for path, mode in self._modes().items():
            assert mode & 0o022 == 0, f"{path} is writable by someone else"
