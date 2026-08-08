"""Every test gets its own data directory and database."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def fresh_rate_limits():
    """Limiter state is process-global, so it would otherwise leak between
    cases and start rejecting unrelated tests part-way through the run."""
    from stroj import ratelimit

    ratelimit.reset_all()
    yield
    ratelimit.reset_all()


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    """Point stroj at a throwaway data directory for the duration of a test."""
    from stroj import config, db

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "stroj.db")
    monkeypatch.setattr(config, "PROBLEM_DIR", tmp_path / "problems")
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "work")
    config.ensure_dirs()
    db.close()
    db.init_db()
    yield tmp_path
    db.close()


@pytest.fixture
def make_tests(tmp_path):
    """Write ``(input, answer)`` pairs to disk and return TestSpec objects."""
    from stroj.judge.runner import TestSpec

    def build(pairs, points=None, samples=0):
        """`samples` marks the first N tests as public samples."""
        directory = tmp_path / "cases"
        directory.mkdir(exist_ok=True)
        specs = []
        for idx, (stdin, answer) in enumerate(pairs, start=1):
            input_path = directory / f"{idx}.in"
            answer_path = directory / f"{idx}.ans"
            input_path.write_text(stdin)
            answer_path.write_text(answer)
            specs.append(
                TestSpec(idx, str(input_path), str(answer_path),
                         points[idx - 1] if points else 1,
                         is_sample=idx <= samples)
            )
        return specs

    return build


@pytest.fixture
def client(monkeypatch):
    """A TestClient with the judge pool disabled — tests drain the queue."""
    from fastapi.testclient import TestClient

    from stroj import config, main

    monkeypatch.setattr(config, "START_WORKERS", False)
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture
def admin_client(client):
    client.post(
        "/api/auth/login", json={"username": "admin", "password": _admin_password()}
    ).raise_for_status()
    return client


def _admin_password() -> str:
    return os.environ["STROJ_ADMIN_PASSWORD"]


def pytest_configure(config):
    # `ensure_admin` generates a random password unless we pin one.
    os.environ.setdefault("STROJ_ADMIN_PASSWORD", "test-admin-password")
    os.environ.setdefault("STROJ_START_WORKERS", "0")
