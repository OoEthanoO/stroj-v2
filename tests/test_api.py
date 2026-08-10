"""HTTP surface: auth, permissions, submitting, admin authoring."""

from __future__ import annotations

import io
import re
import zipfile
from datetime import timedelta

import pytest

from stroj import db
from stroj.api import routes_admin, routes_users
from stroj.judge import worker
from tests.conftest import _admin_password as _admin_pw


def register(client, username="user1", password="password123"):
    response = client.post(
        "/api/auth/register", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()["user"]


def make_problem(admin_client, slug="a-plus-b", **overrides):
    body = {"slug": slug, "title": "A + B", "statement": "Add them.", **overrides}
    response = admin_client.post("/api/admin/problems", json=body)
    assert response.status_code == 200, response.text
    tests = {
        "tests": [
            {"input": "2 3\n", "output": "5\n", "is_sample": True, "points": 1},
            {"input": "-1 1\n", "output": "0\n", "points": 1},
        ]
    }
    admin_client.put(f"/api/admin/problems/{slug}/tests", json=tests).raise_for_status()
    return slug


class TestAuth:
    def test_register_and_whoami(self, client):
        register(client)
        assert client.get("/api/auth/me").json()["user"]["username"] == "user1"

    def test_anonymous_whoami_is_null(self, client):
        assert client.get("/api/auth/me").json()["user"] is None

    def test_duplicate_username_is_rejected(self, client):
        register(client)
        client.post("/api/auth/logout")
        response = client.post(
            "/api/auth/register", json={"username": "user1", "password": "password123"}
        )
        assert response.status_code == 400
        assert "taken" in response.json()["detail"]

    def test_short_password_is_rejected(self, client):
        response = client.post(
            "/api/auth/register", json={"username": "shorty", "password": "abc"}
        )
        assert response.status_code == 400

    @pytest.mark.parametrize("username", ["ab", "has space", "sym!bol", "x" * 40])
    def test_invalid_usernames(self, client, username):
        response = client.post(
            "/api/auth/register", json={"username": username, "password": "password123"}
        )
        assert response.status_code == 400

    def test_login_logout_round_trip(self, client):
        register(client, "loginer")
        client.post("/api/auth/logout")
        assert client.get("/api/auth/me").json()["user"] is None
        response = client.post(
            "/api/auth/login", json={"username": "loginer", "password": "password123"}
        )
        assert response.status_code == 200
        assert client.get("/api/auth/me").json()["user"]["username"] == "loginer"

    def test_wrong_password_is_rejected(self, client):
        register(client, "victim")
        client.post("/api/auth/logout")
        response = client.post(
            "/api/auth/login", json={"username": "victim", "password": "not-the-one"}
        )
        assert response.status_code == 401

    def test_unknown_user_gets_the_same_message(self, client):
        response = client.post(
            "/api/auth/login", json={"username": "ghost", "password": "password123"}
        )
        assert response.status_code == 401
        assert "Incorrect username or password" in response.json()["detail"]

    def test_passwords_are_not_stored_in_the_clear(self, client):
        register(client, "hashme")
        row = db.one("SELECT password_hash FROM users WHERE username = 'hashme'")
        assert "password123" not in row["password_hash"]
        assert row["password_hash"].startswith("pbkdf2_sha256$")


class TestPermissions:
    def test_admin_endpoints_reject_anonymous(self, client):
        assert client.get("/api/admin/users").status_code == 401

    def test_admin_endpoints_reject_plain_users(self, client):
        register(client)
        assert client.get("/api/admin/users").status_code == 403

    def test_admin_can_get_in(self, admin_client):
        assert admin_client.get("/api/admin/users").status_code == 200

    def test_submitting_requires_an_account(self, client, admin_client):
        slug = make_problem(admin_client)
        admin_client.post("/api/auth/logout")
        response = client.post(
            "/api/submissions", json={"problem": slug, "language": "python3", "source": "x=1"}
        )
        assert response.status_code == 401

    def test_hidden_problems_are_invisible_to_users(self, client, admin_client):
        make_problem(admin_client, slug="secret", visible=False)
        admin_client.post("/api/auth/logout")
        register(client, "nosy")
        assert client.get("/api/problems").json()["problems"] == []
        assert client.get("/api/problems/secret").status_code == 404

    def test_hidden_problems_are_visible_to_admins(self, admin_client):
        make_problem(admin_client, slug="secret", visible=False)
        slugs = [p["slug"] for p in admin_client.get("/api/problems").json()["problems"]]
        assert "secret" in slugs


class TestProblems:
    def test_listing_and_detail(self, client, admin_client):
        make_problem(admin_client)
        problems = client.get("/api/problems").json()["problems"]
        assert [p["slug"] for p in problems] == ["a-plus-b"]

        detail = client.get("/api/problems/a-plus-b").json()
        assert detail["title"] == "A + B"
        assert detail["test_count"] == 2
        assert len(detail["samples"]) == 1
        assert detail["samples"][0]["input"] == "2 3\n"

    def test_non_sample_tests_are_not_leaked(self, client, admin_client):
        make_problem(admin_client)
        detail = client.get("/api/problems/a-plus-b").json()
        assert all(s["input"] != "-1 1\n" for s in detail["samples"])

    def test_per_language_limits_are_reported(self, client, admin_client):
        make_problem(admin_client, time_limit_ms=1000)
        limits = client.get("/api/problems/a-plus-b").json()["limits"]
        assert limits["cpp"]["time_limit_ms"] == 1000
        # Slower runtimes get a scaled allowance.
        assert limits["java"]["time_limit_ms"] > limits["cpp"]["time_limit_ms"]

    def test_missing_problem_is_a_404(self, client):
        assert client.get("/api/problems/nope").status_code == 404

    def test_solved_status_is_tracked(self, client, admin_client):
        make_problem(admin_client)
        admin_client.post("/api/auth/logout")
        register(client, "solver")
        assert client.get("/api/problems").json()["problems"][0]["status"] == "untouched"

        client.post("/api/submissions", json={
            "problem": "a-plus-b", "language": "python3",
            "source": "a,b=map(int,input().split())\nprint(a+b)"})
        worker.drain()
        assert client.get("/api/problems").json()["problems"][0]["status"] == "solved"


class TestSubmissions:
    def test_submit_and_judge(self, client, admin_client):
        make_problem(admin_client)
        admin_client.post("/api/auth/logout")
        register(client, "coder")

        response = client.post("/api/submissions", json={
            "problem": "a-plus-b", "language": "python3",
            "source": "a,b=map(int,input().split())\nprint(a+b)"})
        assert response.status_code == 200
        submission_id = response.json()["id"]
        assert response.json()["verdict"] == "PENDING"

        assert worker.drain() == 1
        detail = client.get(f"/api/submissions/{submission_id}").json()
        assert detail["verdict"] == "AC"
        assert detail["score"] == detail["max_score"] == 2
        assert len(detail["tests"]) == 2

    def test_wrong_answer_reports_the_failing_test(self, client, admin_client):
        """Which test failed is named for an admin only — to the solver it is a
        hint about hidden data, so they get the verdict and nothing more."""
        make_problem(admin_client)
        admin_client.post("/api/auth/logout")
        register(client, "coder")
        response = client.post("/api/submissions", json={
            "problem": "a-plus-b", "language": "python3",
            "source": "a,b=map(int,input().split())\nprint(a*b)"})
        worker.drain()
        submission_id = response.json()["id"]

        detail = client.get(f"/api/submissions/{submission_id}").json()
        assert detail["verdict"] == "WA"
        assert detail["message"] == ""

        client.post("/api/auth/logout")
        client.post("/api/auth/login",
                    json={"username": "admin", "password": "test-admin-password"})
        seen_by_admin = client.get(f"/api/submissions/{submission_id}").json()
        assert "Test 1" in seen_by_admin["message"]

    def test_unknown_language_is_rejected(self, client, admin_client):
        make_problem(admin_client)
        response = admin_client.post("/api/submissions", json={
            "problem": "a-plus-b", "language": "cobol", "source": "x"})
        assert response.status_code == 400

    def test_empty_source_is_rejected(self, client, admin_client):
        make_problem(admin_client)
        response = admin_client.post("/api/submissions", json={
            "problem": "a-plus-b", "language": "python3", "source": "   "})
        assert response.status_code == 400

    def test_in_flight_submissions_are_capped(self, client, admin_client):
        make_problem(admin_client)
        admin_client.post("/api/auth/logout")
        register(client, "spammer")
        body = {"problem": "a-plus-b", "language": "python3", "source": "print(1)"}
        for _ in range(5):
            assert client.post("/api/submissions", json=body).status_code == 200
        assert client.post("/api/submissions", json=body).status_code == 429

    def test_source_is_private_to_its_author(self, client, admin_client):
        make_problem(admin_client)
        admin_client.post("/api/auth/logout")
        register(client, "author")
        response = client.post("/api/submissions", json={
            "problem": "a-plus-b", "language": "python3", "source": "print('secret')"})
        submission_id = response.json()["id"]
        assert "source" in client.get(f"/api/submissions/{submission_id}").json()

        client.post("/api/auth/logout")
        register(client, "snooper")
        other = client.get(f"/api/submissions/{submission_id}").json()
        assert "source" not in other
        assert "tests" not in other

    def test_admins_can_read_any_source(self, client, admin_client):
        make_problem(admin_client)
        admin_client.post("/api/auth/logout")
        register(client, "author")
        submission_id = client.post("/api/submissions", json={
            "problem": "a-plus-b", "language": "python3", "source": "print('x')"}).json()["id"]
        client.post("/api/auth/logout")
        client.post("/api/auth/login",
                    json={"username": "admin", "password": "test-admin-password"})
        assert "source" in client.get(f"/api/submissions/{submission_id}").json()

    def test_filtering_by_mine(self, client, admin_client):
        make_problem(admin_client)
        admin_client.post("/api/submissions", json={
            "problem": "a-plus-b", "language": "python3", "source": "print(1)"})
        admin_client.post("/api/auth/logout")
        register(client, "other")
        client.post("/api/submissions", json={
            "problem": "a-plus-b", "language": "python3", "source": "print(2)"})

        assert len(client.get("/api/submissions").json()["submissions"]) == 2
        mine = client.get("/api/submissions?mine=true").json()["submissions"]
        assert len(mine) == 1
        assert mine[0]["username"] == "other"

    def test_rejudging_requeues(self, client, admin_client):
        make_problem(admin_client)
        admin_client.post("/api/submissions", json={
            "problem": "a-plus-b", "language": "python3", "source": "print(1)"})
        worker.drain()
        response = admin_client.post("/api/admin/rejudge?problem=a-plus-b")
        assert response.json()["requeued"] == 1
        assert db.one("SELECT verdict FROM submissions LIMIT 1")["verdict"] == "PENDING"


class TestContestApi:
    @pytest.fixture
    def contest_slug(self, admin_client):
        make_problem(admin_client)
        now = db.parse_time(db.utcnow())
        admin_client.post("/api/admin/contests", json={
            "slug": "round-1", "title": "Round 1",
            "starts_at": (now - timedelta(minutes=5)).isoformat(),
            "ends_at": (now + timedelta(hours=2)).isoformat(),
        }).raise_for_status()
        admin_client.put("/api/admin/contests/round-1/problems",
                         json={"problems": [{"slug": "a-plus-b"}]}).raise_for_status()
        return "round-1"

    def test_labels_are_assigned_in_order(self, admin_client, contest_slug):
        detail = admin_client.get(f"/api/contests/{contest_slug}").json()
        assert [p["label"] for p in detail["problems"]] == ["A"]

    def test_contest_submission_lands_on_the_scoreboard(self, client, admin_client, contest_slug):
        admin_client.post("/api/auth/logout")
        register(client, "racer")
        client.post("/api/submissions", json={
            "problem": "a-plus-b", "language": "python3", "contest": contest_slug,
            "source": "a,b=map(int,input().split())\nprint(a+b)"})
        worker.drain()

        board = client.get(f"/api/contests/{contest_slug}/scoreboard").json()
        assert board["rows"][0]["username"] == "racer"
        assert board["rows"][0]["solved"] == 1

    def test_a_future_contest_seals_its_problems(self, client, admin_client):
        now = db.parse_time(db.utcnow())
        admin_client.post("/api/admin/contests", json={
            "slug": "later", "title": "Later",
            "starts_at": (now + timedelta(hours=1)).isoformat(),
            "ends_at": (now + timedelta(hours=3)).isoformat(),
        }).raise_for_status()
        admin_client.post("/api/auth/logout")
        register(client, "eager")
        detail = client.get("/api/contests/later").json()
        assert detail["sealed"] is True
        assert detail["problems"] == []

    def test_cannot_submit_before_the_start(self, client, admin_client):
        make_problem(admin_client)
        now = db.parse_time(db.utcnow())
        admin_client.post("/api/admin/contests", json={
            "slug": "later", "title": "Later",
            "starts_at": (now + timedelta(hours=1)).isoformat(),
            "ends_at": (now + timedelta(hours=3)).isoformat(),
        })
        admin_client.put("/api/admin/contests/later/problems",
                         json={"problems": [{"slug": "a-plus-b"}]})
        admin_client.post("/api/auth/logout")
        register(client, "eager")
        response = client.post("/api/submissions", json={
            "problem": "a-plus-b", "language": "python3",
            "source": "print(1)", "contest": "later"})
        assert response.status_code == 403

    def test_problem_must_belong_to_the_contest(self, client, admin_client, contest_slug):
        make_problem(admin_client, slug="unrelated")
        response = admin_client.post("/api/submissions", json={
            "problem": "unrelated", "language": "python3",
            "source": "print(1)", "contest": contest_slug})
        assert response.status_code == 400

    def test_end_before_start_is_rejected(self, admin_client):
        now = db.parse_time(db.utcnow())
        response = admin_client.post("/api/admin/contests", json={
            "slug": "backwards", "title": "Backwards",
            "starts_at": (now + timedelta(hours=2)).isoformat(),
            "ends_at": now.isoformat(),
        })
        assert response.status_code == 400


class TestAdminAuthoring:
    def test_duplicate_slug_conflicts(self, admin_client):
        make_problem(admin_client)
        response = admin_client.post(
            "/api/admin/problems", json={"slug": "a-plus-b", "title": "Again"})
        assert response.status_code == 409

    @pytest.mark.parametrize("slug", ["Bad-Slug", "with space", "a", "under_score!"])
    def test_bad_slugs_rejected(self, admin_client, slug):
        response = admin_client.post("/api/admin/problems", json={"slug": slug, "title": "x"})
        assert response.status_code == 400

    def test_unknown_checker_rejected(self, admin_client):
        response = admin_client.post(
            "/api/admin/problems", json={"slug": "ok-slug", "title": "x", "checker": "magic"})
        assert response.status_code == 400

    def test_patching_a_problem(self, admin_client):
        make_problem(admin_client)
        admin_client.patch("/api/admin/problems/a-plus-b",
                           json={"time_limit_ms": 5000, "visible": False})
        detail = admin_client.get("/api/problems/a-plus-b").json()
        assert detail["time_limit_ms"] == 5000
        assert detail["visible"] is False

    def test_types_are_normalised_and_replaced_wholesale(self, admin_client):
        admin_client.post("/api/admin/problems", json={
            "slug": "typed", "title": "Typed", "types": ["Graphs", " dp ", "graphs"]})
        assert admin_client.get("/api/problems/typed").json()["types"] == ["dp", "graphs"]

        admin_client.patch("/api/admin/problems/typed", json={"types": ["greedy"]})
        assert admin_client.get("/api/problems/typed").json()["types"] == ["greedy"]

    def test_bad_type_rejected(self, admin_client):
        response = admin_client.post(
            "/api/admin/problems", json={"slug": "typed", "title": "x", "types": ["d&p"]})
        assert response.status_code == 400

    def test_deleting_takes_the_test_data_with_it(self, admin_client, isolated_data):
        make_problem(admin_client)
        directory = isolated_data / "problems" / "a-plus-b"
        assert directory.exists()
        admin_client.delete("/api/admin/problems/a-plus-b")
        assert not directory.exists()
        assert admin_client.get("/api/problems/a-plus-b").status_code == 404

    def test_zip_upload(self, admin_client):
        admin_client.post("/api/admin/problems", json={"slug": "zipped", "title": "Zipped"})
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("tests/sample1.in", "1 1\n")
            archive.writestr("tests/sample1.out", "2\n")
            archive.writestr("tests/2.in", "3 4\n")
            archive.writestr("tests/2.out", "7\n")
            archive.writestr("tests/10.in", "5 5\n")
            archive.writestr("tests/10.out", "10\n")
        response = admin_client.post(
            "/api/admin/problems/zipped/tests/upload",
            files={"archive": ("tests.zip", buffer.getvalue(), "application/zip")},
        )
        assert response.status_code == 200, response.text
        assert response.json()["tests"] == 3

        tests = admin_client.get("/api/admin/problems/zipped/tests").json()["tests"]
        # Natural ordering: 2 before 10, and the "sample" file is flagged.
        # Samples lead, then the hidden cases in natural order (2 before 10).
        assert [t["is_sample"] for t in tests] == [True, False, False]
        detail = admin_client.get("/api/problems/zipped").json()
        assert detail["samples"][0]["input"] == "1 1\n"

    def test_zip_with_a_missing_answer_is_rejected(self, admin_client):
        admin_client.post("/api/admin/problems", json={"slug": "broken", "title": "Broken"})
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("1.in", "x")
        response = admin_client.post(
            "/api/admin/problems/broken/tests/upload",
            files={"archive": ("t.zip", buffer.getvalue(), "application/zip")},
        )
        assert response.status_code == 400
        assert "answer file" in response.json()["detail"]

    def test_non_zip_upload_is_rejected(self, admin_client):
        admin_client.post("/api/admin/problems", json={"slug": "nz", "title": "NZ"})
        response = admin_client.post(
            "/api/admin/problems/nz/tests/upload",
            files={"archive": ("t.zip", b"not a zip at all", "application/zip")},
        )
        assert response.status_code == 400

    def test_promoting_a_user(self, client, admin_client):
        admin_client.post("/api/auth/logout")
        register(client, "future-admin")
        client.post("/api/auth/logout")
        client.post("/api/auth/login",
                    json={"username": "admin", "password": "test-admin-password"})
        client.post("/api/admin/users/future-admin/role?role=admin").raise_for_status()
        client.post("/api/auth/logout")
        client.post("/api/auth/login",
                    json={"username": "future-admin", "password": "password123"})
        assert client.get("/api/auth/me").json()["user"]["is_admin"] is True


def test_healthz(client):
    body = client.get("/healthz").json()
    assert body["ok"] is True
    assert body["queue"] == 0


def test_index_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "stroj" in response.text


def test_languages_endpoint(client):
    body = client.get("/api/languages").json()
    ids = {lang["id"] for lang in body["languages"]}
    assert {"python3", "cpp", "java"} <= ids
    assert body["default"] in ids


class TestRegistrationModes:
    """A judge on a public URL should not let the whole internet in."""

    def test_open_is_the_default(self, client):
        assert client.get("/api/config").json()["registration"] == "open"
        register(client, "anyone")

    def test_closed_refuses_everyone(self, client, monkeypatch):
        from stroj import config

        monkeypatch.setattr(config, "REGISTRATION", "closed")
        assert client.get("/api/config").json()["registration"] == "closed"
        response = client.post(
            "/api/auth/register", json={"username": "sneaky", "password": "password123"}
        )
        assert response.status_code == 403
        assert "closed" in response.json()["detail"].lower()

    def test_invite_mode_requires_the_code(self, client, monkeypatch):
        from stroj import config

        monkeypatch.setattr(config, "REGISTRATION", "invite")
        monkeypatch.setattr(config, "INVITE_CODE", "chess-club-2026")
        assert client.get("/api/config").json()["registration"] == "invite"

        missing = client.post(
            "/api/auth/register", json={"username": "student", "password": "password123"}
        )
        assert missing.status_code == 403

        wrong = client.post("/api/auth/register", json={
            "username": "student", "password": "password123", "invite": "guess"})
        assert wrong.status_code == 403

        right = client.post("/api/auth/register", json={
            "username": "student", "password": "password123", "invite": "chess-club-2026"})
        assert right.status_code == 200
        assert right.json()["user"]["username"] == "student"

    def test_invite_mode_without_a_code_fails_closed(self, client, monkeypatch):
        """Misconfiguration must not silently mean 'let everyone in'."""
        from stroj import config

        monkeypatch.setattr(config, "REGISTRATION", "invite")
        monkeypatch.setattr(config, "INVITE_CODE", "")
        assert config.registration_mode() == "closed"
        response = client.post(
            "/api/auth/register", json={"username": "student", "password": "password123"}
        )
        assert response.status_code == 403

    def test_admins_can_still_create_accounts_when_closed(self, client, monkeypatch):
        from stroj import auth, config

        monkeypatch.setattr(config, "REGISTRATION", "closed")
        auth.create_user("added-by-organiser", "password123")
        response = client.post("/api/auth/login", json={
            "username": "added-by-organiser", "password": "password123"})
        assert response.status_code == 200

    def test_unknown_mode_falls_back_to_open(self, monkeypatch):
        from stroj import config

        monkeypatch.setattr(config, "REGISTRATION", "banana")
        assert config.registration_mode() == "open"


class TestEditingProblems:
    """Admins author statements in the browser, so the round trip matters."""

    def test_statement_round_trips(self, client, admin_client):
        make_problem(admin_client)
        markdown = "# Heading\n\nA **bold** claim with `code` and `n <= 10^9`.\n\n- one\n- two\n"
        response = admin_client.patch(
            "/api/admin/problems/a-plus-b", json={"statement": markdown}
        )
        assert response.status_code == 200
        assert client.get("/api/problems/a-plus-b").json()["statement"] == markdown

    def test_statement_can_be_emptied(self, admin_client):
        """`exclude_none` must not swallow a deliberate empty string."""
        make_problem(admin_client)
        admin_client.patch("/api/admin/problems/a-plus-b", json={"statement": ""})
        assert admin_client.get("/api/problems/a-plus-b").json()["statement"] == ""

    def test_editing_does_not_disturb_test_data(self, admin_client):
        """Rewriting a statement must not invalidate the problem's tests."""
        make_problem(admin_client)
        before = admin_client.get("/api/admin/problems/a-plus-b/tests").json()["tests"]
        admin_client.patch(
            "/api/admin/problems/a-plus-b", json={"statement": "rewritten", "title": "Renamed"}
        )
        after = admin_client.get("/api/admin/problems/a-plus-b/tests").json()["tests"]
        assert before == after
        assert admin_client.get("/api/problems/a-plus-b").json()["title"] == "Renamed"

    def test_a_plain_user_cannot_edit(self, client, admin_client):
        make_problem(admin_client)
        admin_client.post("/api/auth/logout")
        register(client, "meddler")
        response = client.patch(
            "/api/admin/problems/a-plus-b", json={"statement": "defaced"}
        )
        assert response.status_code == 403
        assert client.get("/api/problems/a-plus-b").json()["statement"] == "Add them."

    def test_editing_a_missing_problem_is_a_404(self, admin_client):
        assert admin_client.patch(
            "/api/admin/problems/ghost", json={"statement": "x"}
        ).status_code == 404


class TestInspectTestData:
    """Authoring is test-data-first: validate the archive before the problem
    exists, so a bad zip never strands a problem with no tests."""

    def _zip(self, entries):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, content in entries:
                archive.writestr(name, content)
        return buffer.getvalue()

    def test_reports_counts_and_a_preview(self, admin_client):
        data = self._zip([
            ("sample1.in", "2 3\n"), ("sample1.out", "5\n"),
            ("2.in", "4 5\n"), ("2.out", "9\n"),
            ("10.in", "6 7\n"), ("10.out", "13\n"),
        ])
        response = admin_client.post(
            "/api/admin/testdata/inspect",
            files={"archive": ("t.zip", data, "application/zip")},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["tests"] == 3
        assert body["samples"] == 1
        assert body["preview"]["input"] == "2 3\n"
        assert body["preview"]["output"] == "5\n"

    def test_creates_nothing(self, admin_client):
        """The whole point: inspection must not touch the database."""
        before = admin_client.get("/api/problems").json()["problems"]
        data = self._zip([("1.in", "x\n"), ("1.out", "y\n")])
        admin_client.post(
            "/api/admin/testdata/inspect",
            files={"archive": ("t.zip", data, "application/zip")},
        )
        assert admin_client.get("/api/problems").json()["problems"] == before

    def test_unpaired_input_is_rejected(self, admin_client):
        data = self._zip([("1.in", "x\n")])
        response = admin_client.post(
            "/api/admin/testdata/inspect",
            files={"archive": ("t.zip", data, "application/zip")},
        )
        assert response.status_code == 400
        assert "answer file" in response.json()["detail"]

    def test_garbage_is_rejected(self, admin_client):
        response = admin_client.post(
            "/api/admin/testdata/inspect",
            files={"archive": ("t.zip", b"not a zip", "application/zip")},
        )
        assert response.status_code == 400

    def test_requires_admin(self, client):
        register(client, "outsider")
        data = self._zip([("1.in", "x\n"), ("1.out", "y\n")])
        response = client.post(
            "/api/admin/testdata/inspect",
            files={"archive": ("t.zip", data, "application/zip")},
        )
        assert response.status_code == 403


def test_samples_are_ordered_first(admin_client):
    """Numeric stems sort before the word 'sample', so without an explicit
    rule the samples would land at the end of the run."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in [
            ("3.in", "c\n"), ("3.out", "C\n"),
            ("sample2.in", "b\n"), ("sample2.out", "B\n"),
            ("1.in", "a\n"), ("1.out", "A\n"),
            ("sample1.in", "s\n"), ("sample1.out", "S\n"),
        ]:
            archive.writestr(name, content)

    admin_client.post("/api/admin/problems", json={"slug": "ordered", "title": "Ordered"})
    admin_client.post(
        "/api/admin/problems/ordered/tests/upload",
        files={"archive": ("t.zip", buffer.getvalue(), "application/zip")},
    ).raise_for_status()

    tests = admin_client.get("/api/admin/problems/ordered/tests").json()["tests"]
    assert [t["is_sample"] for t in tests] == [True, True, False, False]
    samples = admin_client.get("/api/problems/ordered").json()["samples"]
    assert [s["input"] for s in samples] == ["s\n", "b\n"]


class TestProfilesAndAuthorship:
    def test_profile_shows_solved_and_score(self, client, admin_client):
        admin_client.post("/api/admin/problems", json={
            "slug": "p1", "title": "P1", "points": 400})
        admin_client.put("/api/admin/problems/p1/tests", json={"tests": [
            {"input": "1\n", "output": "1\n"}]})
        admin_client.post("/api/auth/logout")
        register(client, "solver")
        client.post("/api/submissions", json={
            "problem": "p1", "language": "python3", "source": "print(input())"})
        worker.drain()

        body = client.get("/api/users/solver").json()
        assert body["solved_count"] == 1
        assert body["score"] == 400
        assert body["rank"] == 1
        assert body["solved"][0]["slug"] == "p1"

    def test_bio_round_trips_and_is_own_only(self, client, admin_client):
        admin_client.post("/api/auth/logout")
        register(client, "writer")
        client.patch("/api/users/me", json={"bio": "# Hi\n\nI like **graphs**."})
        assert "graphs" in client.get("/api/users/writer").json()["bio"]
        assert client.get("/api/users/writer").json()["editable"] is True

        client.post("/api/auth/logout")
        register(client, "stranger")
        other = client.get("/api/users/writer").json()
        assert other["editable"] is False
        assert "graphs" in other["bio"]      # bios are public

    def test_bio_requires_sign_in(self, client):
        assert client.patch("/api/users/me", json={"bio": "x"}).status_code == 401

    def test_author_defaults_to_the_creating_admin(self, admin_client):
        admin_client.post("/api/admin/problems", json={"slug": "mine", "title": "Mine"})
        assert admin_client.get("/api/problems/mine").json()["author"] == "admin"

    def test_author_can_be_someone_else(self, client, admin_client):
        admin_client.post("/api/auth/logout")
        register(client, "guest-author")
        client.post("/api/auth/logout")
        client.post("/api/auth/login",
                    json={"username": "admin", "password": "test-admin-password"})
        client.post("/api/admin/problems", json={
            "slug": "theirs", "title": "Theirs", "author": "guest-author"})
        assert client.get("/api/problems/theirs").json()["author"] == "guest-author"
        assert client.get("/api/users/guest-author").json()["authored"][0]["slug"] == "theirs"

    def test_unknown_author_is_rejected(self, admin_client):
        response = admin_client.post("/api/admin/problems", json={
            "slug": "ghosted", "title": "Ghosted", "author": "nobody"})
        assert response.status_code == 404

    def test_points_default_and_are_editable(self, admin_client):
        admin_client.post("/api/admin/problems", json={"slug": "pts", "title": "Pts"})
        assert admin_client.get("/api/problems/pts").json()["points"] == 100
        admin_client.patch("/api/admin/problems/pts", json={"points": 750})
        assert admin_client.get("/api/problems/pts").json()["points"] == 750

    def test_missing_profile_is_a_404(self, client):
        assert client.get("/api/users/nobody").status_code == 404

    def test_leaderboard_endpoint(self, client):
        body = client.get("/api/leaderboard").json()
        assert "standings" in body and "decay" in body


class TestSubmissionActivity:
    """The profile's calendar of submissions per day."""

    def submit(self, client, source="a,b=map(int,input().split())\nprint(a+b)"):
        client.post("/api/submissions", json={
            "problem": "a-plus-b", "language": "python3", "source": source})
        worker.drain()

    def activity(self, client, username="user1"):
        return client.get(f"/api/users/{username}").json()["activity"]

    def test_a_new_user_has_an_empty_year(self, client, admin_client):
        admin_client.post("/api/auth/logout")
        register(client, "quiet")
        activity = self.activity(client, "quiet")
        assert activity["counts"] == []
        assert activity["total"] == 0
        assert activity["days"] == routes_users.ACTIVITY_DAYS
        assert activity["since"] < activity["until"]

    def test_a_day_counts_every_submission_and_its_accepteds(
            self, client, admin_client):
        make_problem(admin_client)
        admin_client.post("/api/auth/logout")
        register(client)
        self.submit(client)
        self.submit(client, source="print(0)")

        activity = self.activity(client)
        assert activity["total"] == 2
        assert len(activity["counts"]) == 1
        assert activity["counts"][0]["count"] == 2
        assert activity["counts"][0]["accepted"] == 1
        assert activity["counts"][0]["date"] == db.utcnow()[:10]

    def test_days_are_separate_and_older_ones_fall_out_of_the_window(
            self, client, admin_client):
        make_problem(admin_client)
        admin_client.post("/api/auth/logout")
        register(client)
        self.submit(client)
        self.submit(client, source="print(0)")
        # Push one back a week and one clean out of the year.
        ids = [r["id"] for r in db.query("SELECT id FROM submissions ORDER BY id")]
        for sub_id, days in zip(ids, (7, 400)):
            stamp = db.parse_time(db.utcnow()) - timedelta(days=days)
            db.execute("UPDATE submissions SET created_at = ? WHERE id = ?",
                       (stamp.strftime("%Y-%m-%dT%H:%M:%S.000Z"), sub_id))

        activity = self.activity(client)
        assert activity["total"] == 1
        assert [d["count"] for d in activity["counts"]] == [1]
        assert activity["counts"][0]["date"] >= activity["since"]

    def test_the_calendar_is_public(self, client, admin_client):
        make_problem(admin_client)
        admin_client.post("/api/auth/logout")
        register(client, "watched")
        self.submit(client)
        client.post("/api/auth/logout")
        assert self.activity(client, "watched")["total"] == 1


class TestLiveSubmissionView:
    def test_rows_are_visible_before_judging_finishes(self, client, admin_client):
        """Persist each test as it lands, checked from inside the judge itself:
        by the time test 2 finishes, test 1 must already be readable."""
        from stroj.judge import runner as runner_mod

        make_problem(admin_client)
        response = admin_client.post("/api/submissions", json={
            "problem": "a-plus-b", "language": "python3",
            "source": "a,b=map(int,input().split())\nprint(a+b)"})
        submission_id = response.json()["id"]

        visible_during = []
        original = runner_mod.judge

        def spy(*args, **kwargs):
            caller = kwargs.get("on_test")

            def wrapped(test):
                caller(test)
                rows = db.query(
                    "SELECT idx FROM submission_tests WHERE submission_id = ?",
                    (submission_id,),
                )
                visible_during.append(len(rows))

            kwargs["on_test"] = wrapped
            return original(*args, **kwargs)

        runner_mod.judge = spy
        try:
            worker.drain()
        finally:
            runner_mod.judge = original

        # One row visible after the first test, two after the second, and so on.
        assert visible_during == [1, 2]

    def test_detail_reports_how_many_tests_to_expect(self, admin_client):
        make_problem(admin_client)
        response = admin_client.post("/api/submissions", json={
            "problem": "a-plus-b", "language": "python3", "source": "print(1)"})
        body = admin_client.get(f"/api/submissions/{response.json()['id']}").json()
        assert body["test_count"] == 2

    def test_a_rejudge_clears_the_previous_run(self, admin_client):
        """Stale rows from the last run must not linger on screen while the new
        one is still working through the tests."""
        make_problem(admin_client)
        response = admin_client.post("/api/submissions", json={
            "problem": "a-plus-b", "language": "python3",
            "source": "a,b=map(int,input().split())\nprint(a+b)"})
        submission_id = response.json()["id"]
        worker.drain()
        assert len(admin_client.get(f"/api/submissions/{submission_id}").json()["tests"]) == 2

        admin_client.post("/api/admin/rejudge?problem=a-plus-b")
        cleared = []
        from stroj.judge import worker as worker_mod

        original = worker_mod.store_outcome

        def spy(sid, *args, **kwargs):
            # Sampled at the moment judging finishes but before the final write:
            # the count must reflect this run, not the previous one plus it.
            cleared.append(len(db.query(
                "SELECT idx FROM submission_tests WHERE submission_id = ?", (sid,))))
            return original(sid, *args, **kwargs)

        worker_mod.store_outcome = spy
        try:
            worker.drain()
        finally:
            worker_mod.store_outcome = original
        assert cleared == [2], "rows accumulated across runs"

    def test_score_climbs_as_tests_pass(self, admin_client):
        make_problem(admin_client)
        response = admin_client.post("/api/submissions", json={
            "problem": "a-plus-b", "language": "python3",
            "source": "a,b=map(int,input().split())\nprint(a+b)"})
        submission_id = response.json()["id"]
        assert admin_client.get(f"/api/submissions/{submission_id}").json()["score"] == 0
        worker.drain()
        assert admin_client.get(f"/api/submissions/{submission_id}").json()["score"] == 2


class TestDeletionImpact:
    """Deleting a problem cascades to every submission against it, so the
    confirm dialog should say how much that is rather than warn generically."""

    def test_counts_submissions_and_users(self, client, admin_client):
        make_problem(admin_client)
        admin_client.post("/api/submissions", json={
            "problem": "a-plus-b", "language": "python3", "source": "print(1)"})
        admin_client.post("/api/auth/logout")
        register(client, "someone-else")
        client.post("/api/submissions", json={
            "problem": "a-plus-b", "language": "python3", "source": "print(2)"})
        client.post("/api/auth/logout")
        client.post("/api/auth/login",
                    json={"username": "admin", "password": "test-admin-password"})

        impact = client.get("/api/admin/problems/a-plus-b/impact").json()
        assert impact["submissions"] == 2
        assert impact["users"] == 2

    def test_names_the_contests_it_would_disturb(self, admin_client):
        make_problem(admin_client)
        now = db.parse_time(db.utcnow())
        admin_client.post("/api/admin/contests", json={
            "slug": "round-9", "title": "Round 9",
            "starts_at": now.isoformat(),
            "ends_at": (now + timedelta(hours=2)).isoformat()})
        admin_client.put("/api/admin/contests/round-9/problems",
                         json={"problems": [{"slug": "a-plus-b"}]})
        impact = admin_client.get("/api/admin/problems/a-plus-b/impact").json()
        assert impact["contests"] == [{"slug": "round-9", "title": "Round 9"}]

    def test_an_untouched_problem_reports_nothing(self, admin_client):
        make_problem(admin_client)
        impact = admin_client.get("/api/admin/problems/a-plus-b/impact").json()
        assert impact["submissions"] == 0 and impact["contests"] == []

    def test_requires_admin(self, client, admin_client):
        make_problem(admin_client)
        admin_client.post("/api/auth/logout")
        register(client, "curious")
        assert client.get("/api/admin/problems/a-plus-b/impact").status_code == 403


class TestEditingAContest:
    """Deleting a contest NULLs `contest_id` on every submission made during
    it, so the scoreboard cannot be rebuilt. Editing is the only safe way to
    fix a mistake, which makes its validation load-bearing."""

    def contest(self, admin_client, **overrides):
        now = db.parse_time(db.utcnow())
        body = {
            "slug": "round-1", "title": "Round 1",
            "starts_at": now.isoformat(),
            "ends_at": (now + timedelta(hours=3)).isoformat(),
            **overrides,
        }
        admin_client.post("/api/admin/contests", json=body).raise_for_status()
        return body["slug"]

    def stored(self, slug="round-1"):
        return db.one("SELECT * FROM contests WHERE slug = ?", (slug,))

    def test_changes_are_applied(self, admin_client):
        slug = self.contest(admin_client)
        response = admin_client.patch(f"/api/admin/contests/{slug}",
                                      json={"title": "Renamed", "penalty_minutes": 5})
        assert response.status_code == 200
        row = self.stored()
        assert row["title"] == "Renamed" and row["penalty_minutes"] == 5

    def test_untouched_fields_survive(self, admin_client):
        slug = self.contest(admin_client, description="the original blurb")
        admin_client.patch(f"/api/admin/contests/{slug}", json={"title": "Renamed"})
        assert self.stored()["description"] == "the original blurb"

    def test_an_empty_patch_changes_nothing(self, admin_client):
        slug = self.contest(admin_client)
        before = dict(self.stored())
        assert admin_client.patch(f"/api/admin/contests/{slug}", json={}).json()["updated"] == 0
        assert dict(self.stored()) == before

    def test_times_are_stored_in_the_one_canonical_format(self, admin_client):
        """The scoreboard filters submissions by string comparison against
        these columns, so a different ISO spelling would drop rows silently."""
        slug = self.contest(admin_client)
        response = admin_client.patch(f"/api/admin/contests/{slug}", json={
            "starts_at": "2026-09-01T10:00:00+00:00",
            "ends_at": "2026-09-01T13:00:00+00:00",
        })
        assert response.status_code == 200, response.text
        row = self.stored()
        assert row["starts_at"] == "2026-09-01T10:00:00.000Z"
        assert row["ends_at"] == "2026-09-01T13:00:00.000Z"

    def test_a_half_patch_is_checked_against_the_stored_half(self, admin_client):
        """Sending only `ends_at` must still be compared with the existing
        `starts_at`, or a contest can be edited into ending before it begins."""
        slug = self.contest(admin_client)
        earlier = (db.parse_time(db.utcnow()) - timedelta(hours=5)).isoformat()
        response = admin_client.patch(f"/api/admin/contests/{slug}", json={"ends_at": earlier})
        assert response.status_code == 400
        assert "end after it starts" in response.json()["detail"]

    def test_a_freeze_longer_than_the_contest_is_refused(self, admin_client):
        slug = self.contest(admin_client)
        response = admin_client.patch(f"/api/admin/contests/{slug}",
                                      json={"freeze_minutes": 600})
        assert response.status_code == 400
        assert self.stored()["freeze_minutes"] == 0

    def test_shortening_a_contest_rechecks_an_existing_freeze(self, admin_client):
        """The freeze was legal against the old window; it need not be against
        the new one."""
        slug = self.contest(admin_client, freeze_minutes=120)
        now = db.parse_time(db.utcnow())
        response = admin_client.patch(
            f"/api/admin/contests/{slug}",
            json={"ends_at": (now + timedelta(minutes=30)).isoformat()})
        assert response.status_code == 400

    def test_unknown_scoring_is_refused(self, admin_client):
        slug = self.contest(admin_client)
        assert admin_client.patch(f"/api/admin/contests/{slug}",
                                  json={"scoring": "codeforces"}).status_code == 400

    def test_missing_contest_is_a_404(self, admin_client):
        assert admin_client.patch("/api/admin/contests/ghost",
                                  json={"title": "x"}).status_code == 404

    def test_requires_admin(self, client, admin_client):
        slug = self.contest(admin_client)
        admin_client.post("/api/auth/logout")
        register(client, "meddler")
        assert client.patch(f"/api/admin/contests/{slug}",
                            json={"title": "mine now"}).status_code == 403


class TestLastAdminIsProtected:
    """Every admin route rejects non-admins, so demoting the final admin locks
    the judge's owner out of their own instance with no way back in short of
    editing the database on the server."""

    def test_the_only_admin_cannot_be_demoted(self, admin_client):
        response = admin_client.post("/api/admin/users/admin/role?role=user")
        assert response.status_code == 400
        assert "only admin" in response.json()["detail"]
        assert db.one("SELECT role FROM users WHERE username = 'admin'")["role"] == "admin"

    def test_demotion_works_once_a_replacement_exists(self, client, admin_client):
        admin_client.post("/api/auth/logout")
        register(client, "successor")
        client.post("/api/auth/logout")
        admin_client.post("/api/auth/login",
                          json={"username": "admin", "password": "test-admin-password"})
        admin_client.post("/api/admin/users/successor/role?role=admin")

        assert admin_client.post("/api/admin/users/admin/role?role=user").status_code == 200
        assert db.one("SELECT role FROM users WHERE username = 'admin'")["role"] == "user"

    def test_promoting_is_never_blocked(self, client, admin_client):
        admin_client.post("/api/auth/logout")
        register(client, "newbie")
        client.post("/api/auth/logout")
        admin_client.post("/api/auth/login",
                          json={"username": "admin", "password": "test-admin-password"})
        assert admin_client.post("/api/admin/users/newbie/role?role=admin").status_code == 200

    def test_unknown_user_is_a_404(self, admin_client):
        assert admin_client.post("/api/admin/users/ghost/role?role=admin").status_code == 404


class TestPosts:
    def make_post(self, admin_client, slug="hello", **overrides):
        body = {"slug": slug, "title": "Hello", "body": "# Hi\n\nWelcome.", **overrides}
        response = admin_client.post("/api/admin/posts", json=body)
        assert response.status_code == 200, response.text
        return slug

    def test_posting_and_reading(self, client, admin_client):
        self.make_post(admin_client)
        admin_client.post("/api/auth/logout")
        posts = client.get("/api/posts").json()["posts"]
        assert [p["slug"] for p in posts] == ["hello"]
        assert posts[0]["author"] == "admin"
        assert client.get("/api/posts/hello").json()["body"] == "# Hi\n\nWelcome."

    def test_users_cannot_post(self, client):
        register(client)
        assert client.post(
            "/api/admin/posts", json={"slug": "sneaky", "title": "x"}).status_code == 403

    def test_drafts_are_admin_only(self, client, admin_client):
        self.make_post(admin_client, slug="draft", published=False)
        assert len(admin_client.get("/api/posts").json()["posts"]) == 1
        admin_client.post("/api/auth/logout")
        assert client.get("/api/posts").json()["posts"] == []
        assert client.get("/api/posts/draft").status_code == 404

    def test_pinned_posts_come_first(self, admin_client):
        self.make_post(admin_client, slug="older")
        self.make_post(admin_client, slug="newer")
        admin_client.patch("/api/admin/posts/older", json={"pinned": True})
        posts = admin_client.get("/api/posts").json()["posts"]
        assert [p["slug"] for p in posts] == ["older", "newer"]

    def test_editing_stamps_updated_at(self, admin_client):
        self.make_post(admin_client)
        before = admin_client.get("/api/posts/hello").json()
        admin_client.patch("/api/admin/posts/hello", json={"title": "Hello again"})
        after = admin_client.get("/api/posts/hello").json()
        assert after["title"] == "Hello again"
        assert after["updated_at"] > before["created_at"]

    def test_deleting(self, admin_client):
        self.make_post(admin_client)
        assert admin_client.delete("/api/admin/posts/hello").status_code == 200
        assert admin_client.get("/api/posts/hello").status_code == 404

    def test_duplicate_slug_conflicts(self, admin_client):
        self.make_post(admin_client)
        assert admin_client.post(
            "/api/admin/posts", json={"slug": "hello", "title": "x"}).status_code == 409


class TestPatchIsAllOrNothing:
    """Types live in their own table, so they are written by a different
    statement than the rest of a patch. Applying them before the request was
    fully validated let a rejected patch still change the problem."""

    def test_a_rejected_patch_leaves_types_alone(self, admin_client):
        make_problem(admin_client, types=["implementation"])
        response = admin_client.patch("/api/admin/problems/a-plus-b",
                                      json={"types": ["sorting"], "checker": "nonsense"})
        assert response.status_code == 400
        assert admin_client.get("/api/problems/a-plus-b").json()["types"] == ["implementation"]

    def test_an_unknown_author_also_leaves_types_alone(self, admin_client):
        make_problem(admin_client, types=["implementation"])
        response = admin_client.patch("/api/admin/problems/a-plus-b",
                                      json={"types": ["sorting"], "author": "nobody-here"})
        assert response.status_code == 404
        assert admin_client.get("/api/problems/a-plus-b").json()["types"] == ["implementation"]

    def test_a_bad_type_leaves_the_other_fields_alone(self, admin_client):
        make_problem(admin_client, points=100)
        response = admin_client.patch("/api/admin/problems/a-plus-b",
                                      json={"types": ["d&p"], "points": 250})
        assert response.status_code == 400
        assert admin_client.get("/api/problems/a-plus-b").json()["points"] == 100

    def test_a_types_only_patch_reports_that_it_changed_something(self, admin_client):
        make_problem(admin_client)
        response = admin_client.patch("/api/admin/problems/a-plus-b", json={"types": ["dp"]})
        assert response.json()["updated"] == 1
        assert admin_client.get("/api/problems/a-plus-b").json()["types"] == ["dp"]

    def test_an_empty_patch_still_reports_nothing(self, admin_client):
        make_problem(admin_client)
        assert admin_client.patch("/api/admin/problems/a-plus-b", json={}).json()["updated"] == 0

    def test_types_count_alongside_column_changes(self, admin_client):
        make_problem(admin_client)
        response = admin_client.patch("/api/admin/problems/a-plus-b",
                                      json={"types": ["dp"], "points": 250})
        assert response.json()["updated"] == 2


class TestSchemaReachesExistingDatabases:
    """New tables arrive through `CREATE TABLE IF NOT EXISTS` in schema.sql
    rather than the `_ADDED_COLUMNS` list, which only retrofits columns. A
    deploy against the live database depends on that actually working."""

    def test_new_tables_appear_on_a_database_that_predates_them(self, isolated_data):
        db.init_db()
        db.execute("DROP TABLE posts")
        db.execute("DROP TABLE problem_types")
        names = lambda: {r["name"] for r in db.query(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert "posts" not in names() and "problem_types" not in names()

        db.init_db()
        assert {"posts", "problem_types"} <= names()

    def test_existing_rows_survive_the_upgrade(self, isolated_data):
        db.init_db()
        db.insert("INSERT INTO users (username, password_hash, created_at)"
                  " VALUES ('keeper', 'x', ?)", (db.utcnow(),))
        db.execute("DROP TABLE posts")
        db.init_db()
        assert db.one("SELECT 1 FROM users WHERE username = 'keeper'") is not None


class TestAbortingSubmissions:
    """A submission still queued is settled in the database; one already
    running has to be told to stop, and the worker has to notice."""

    def queued(self, client, admin_client, source="print(1)"):
        make_problem(admin_client)
        admin_client.post("/api/auth/logout")
        register(client, "runner1")
        response = client.post("/api/submissions", json={
            "problem": "a-plus-b", "language": "python3", "source": source})
        return response.json()["id"]

    def verdict(self, submission_id):
        return db.one("SELECT verdict FROM submissions WHERE id = ?",
                      (submission_id,))["verdict"]

    def test_owner_can_abort_their_own(self, client, admin_client):
        sid = self.queued(client, admin_client)
        response = client.post(f"/api/submissions/{sid}/abort")
        assert response.status_code == 200, response.text
        assert response.json()["stopped"] == "queued"
        assert self.verdict(sid) == "AB"

    def test_an_aborted_submission_is_never_judged(self, client, admin_client):
        sid = self.queued(client, admin_client)
        client.post(f"/api/submissions/{sid}/abort")
        # The worker only claims PENDING rows, so there is nothing left to take.
        assert worker.drain() == 0
        assert self.verdict(sid) == "AB"

    def test_someone_else_cannot_abort_it(self, client, admin_client):
        sid = self.queued(client, admin_client)
        client.post("/api/auth/logout")
        register(client, "meddler")
        assert client.post(f"/api/submissions/{sid}/abort").status_code == 403
        assert self.verdict(sid) == "PENDING"

    def test_an_admin_can_abort_anyones(self, client, admin_client):
        sid = self.queued(client, admin_client)
        client.post("/api/auth/logout")
        admin_client.post("/api/auth/login",
                          json={"username": "admin", "password": "test-admin-password"})
        assert admin_client.post(f"/api/submissions/{sid}/abort").status_code == 200
        assert self.verdict(sid) == "AB"

    def test_anonymous_cannot_abort(self, client, admin_client):
        sid = self.queued(client, admin_client)
        client.post("/api/auth/logout")
        assert client.post(f"/api/submissions/{sid}/abort").status_code == 401

    def test_a_finished_submission_cannot_be_aborted(self, client, admin_client):
        sid = self.queued(client, admin_client,
                          source="a,b=map(int,input().split())\nprint(a+b)")
        worker.drain()
        assert self.verdict(sid) == "AC"
        response = client.post(f"/api/submissions/{sid}/abort")
        assert response.status_code == 409
        assert self.verdict(sid) == "AC"

    def test_unknown_submission_is_a_404(self, client, admin_client):
        self.queued(client, admin_client)
        assert client.post("/api/submissions/999999/abort").status_code == 404

    def test_it_earns_nothing(self, client, admin_client):
        sid = self.queued(client, admin_client)
        client.post(f"/api/submissions/{sid}/abort")
        row = db.one("SELECT earned_percent, score FROM submissions WHERE id = ?", (sid,))
        assert row["earned_percent"] == 0 and row["score"] == 0

    def test_the_page_reports_it(self, client, admin_client):
        sid = self.queued(client, admin_client)
        client.post(f"/api/submissions/{sid}/abort")
        data = client.get(f"/api/submissions/{sid}").json()
        assert data["verdict"] == "AB"
        assert data["verdict_name"] == "Aborted"


class TestPerLanguageLimits:
    """A fixed multiplier assumes a ratio between runtimes that is really a
    property of the problem. On a loop-heavy problem the measured gap can be
    28x where the multiplier assumes 3x, which fails a correct solution."""

    def test_defaults_are_the_derived_limits(self, admin_client):
        make_problem(admin_client, time_limit_ms=1000, memory_limit_mb=256)
        limits = admin_client.get("/api/admin/problems/a-plus-b/limits").json()["limits"]
        assert limits["cpp"]["time_limit_ms"] == 1000
        assert limits["python3"]["time_limit_ms"] == 3200   # 1000 * 3.0 + 200
        assert all(not l["measured"] for l in limits.values())

    def test_setting_one_language_leaves_the_others_derived(self, admin_client):
        make_problem(admin_client, time_limit_ms=1000)
        admin_client.put("/api/admin/problems/a-plus-b/limits", json={
            "limits": {"python3": {"time_limit_ms": 6500, "memory_limit_mb": 256}}})
        limits = admin_client.get("/api/admin/problems/a-plus-b/limits").json()["limits"]
        assert limits["python3"] == {
            "name": "Python 3", "time_limit_ms": 6500,
            "memory_limit_mb": 256, "measured": True}
        assert limits["cpp"]["time_limit_ms"] == 1000 and not limits["cpp"]["measured"]

    def test_clearing_returns_a_language_to_the_fallback(self, admin_client):
        make_problem(admin_client, time_limit_ms=1000)
        admin_client.put("/api/admin/problems/a-plus-b/limits", json={
            "limits": {"python3": {"time_limit_ms": 6500, "memory_limit_mb": 256}}})
        admin_client.put("/api/admin/problems/a-plus-b/limits",
                         json={"limits": {"python3": None}})
        limits = admin_client.get("/api/admin/problems/a-plus-b/limits").json()["limits"]
        assert limits["python3"]["time_limit_ms"] == 3200
        assert not limits["python3"]["measured"]

    def test_the_public_page_shows_what_will_be_enforced(self, client, admin_client):
        make_problem(admin_client, time_limit_ms=1000)
        admin_client.put("/api/admin/problems/a-plus-b/limits", json={
            "limits": {"python3": {"time_limit_ms": 6500, "memory_limit_mb": 512}}})
        limits = client.get("/api/problems/a-plus-b").json()["limits"]
        assert limits["python3"]["time_limit_ms"] == 6500
        assert limits["python3"]["memory_limit_mb"] == 512
        assert limits["python3"]["measured"] is True

    def test_the_judge_enforces_the_override(self, admin_client):
        """The number shown has to be the number applied, or the page lies."""
        from stroj.judge.runner import ProblemSpec
        from stroj.judge import languages, worker
        make_problem(admin_client, time_limit_ms=1000)
        admin_client.put("/api/admin/problems/a-plus-b/limits", json={
            "limits": {"python3": {"time_limit_ms": 6500, "memory_limit_mb": 512}}})

        row = db.one("SELECT * FROM problems WHERE slug = 'a-plus-b'")
        spec = ProblemSpec.from_row(row, worker.load_limits(row["id"]))
        assert spec.limits_for(languages.get("python3")) == (6500, 512)
        # Untouched languages keep the derived value.
        assert spec.limits_for(languages.get("cpp")) == (1000, 256)

    @pytest.mark.parametrize("bad", [
        {"time_limit_ms": 50, "memory_limit_mb": 256},        # under the floor
        {"time_limit_ms": 999999, "memory_limit_mb": 256},    # over the ceiling
        {"time_limit_ms": 1000, "memory_limit_mb": 8},        # under the floor
        {"time_limit_ms": 1000},                              # incomplete
    ])
    def test_out_of_range_values_are_refused(self, admin_client, bad):
        make_problem(admin_client)
        response = admin_client.put("/api/admin/problems/a-plus-b/limits",
                                    json={"limits": {"python3": bad}})
        assert response.status_code == 400
        stored = db.one("SELECT COUNT(*) AS n FROM problem_limits")["n"]
        assert stored == 0, "a rejected request must not write anything"

    def test_unknown_language_is_refused(self, admin_client):
        make_problem(admin_client)
        response = admin_client.put("/api/admin/problems/a-plus-b/limits",
                                    json={"limits": {"cobol": {"time_limit_ms": 1000,
                                                               "memory_limit_mb": 256}}})
        assert response.status_code == 400
        assert "cobol" in response.json()["detail"]

    def test_requires_admin(self, client, admin_client):
        make_problem(admin_client)
        admin_client.post("/api/auth/logout")
        register(client, "nosy2")
        assert client.get("/api/admin/problems/a-plus-b/limits").status_code == 403
        assert client.put("/api/admin/problems/a-plus-b/limits",
                          json={"limits": {}}).status_code == 403

    def test_limits_die_with_the_problem(self, admin_client, isolated_data):
        make_problem(admin_client)
        admin_client.put("/api/admin/problems/a-plus-b/limits", json={
            "limits": {"cpp": {"time_limit_ms": 200, "memory_limit_mb": 256}}})
        admin_client.delete("/api/admin/problems/a-plus-b")
        assert db.one("SELECT COUNT(*) AS n FROM problem_limits")["n"] == 0


class TestJudgeOutputIsAdminOnly:
    """Judge output names which hidden test failed and roughly how, which is a
    hint about data the solver is not meant to see. Admins keep it, or a broken
    checker or bad test file cannot be diagnosed from the site."""

    def solve(self, client, admin_client, source, slug="a-plus-b"):
        make_problem(admin_client, slug=slug)
        admin_client.post("/api/auth/logout")
        register(client, "solver9")
        sid = client.post("/api/submissions", json={
            "problem": slug, "language": "python3", "source": source}).json()["id"]
        worker.drain()
        return sid

    def as_admin(self, client):
        client.post("/api/auth/logout")
        client.post("/api/auth/login",
                    json={"username": "admin", "password": "test-admin-password"})

    def test_the_owner_does_not_see_the_failure_message(self, client, admin_client):
        sid = self.solve(client, admin_client, "print(999)")
        data = client.get(f"/api/submissions/{sid}").json()
        assert data["verdict"] == "WA"
        assert data["message"] == ""

    def test_an_admin_does_see_it(self, client, admin_client):
        sid = self.solve(client, admin_client, "print(999)")
        self.as_admin(client)
        data = client.get(f"/api/submissions/{sid}").json()
        assert data["message"], "an admin must still be able to troubleshoot"

    def test_hidden_test_details_are_withheld(self, client, admin_client):
        # Judging stops at the first failure, so this must clear the sample in
        # order for a hidden test to run at all.
        sid = self.solve(client, admin_client, "a,b=map(int,input().split())\nprint(abs(a)+abs(b))")
        tests = client.get(f"/api/submissions/{sid}").json()["tests"]
        hidden = [t for t in tests if not t["is_sample"]]
        assert hidden, "the fixture should have a hidden test"
        assert all(t["message"] == "" for t in hidden)
        # The verdict itself is not a hint — they still learn which test broke.
        assert any(t["verdict"] != "AC" for t in tests)

    def test_hidden_test_details_reach_an_admin(self, client, admin_client):
        sid = self.solve(client, admin_client, "a,b=map(int,input().split())\nprint(abs(a)+abs(b))")
        self.as_admin(client)
        tests = client.get(f"/api/submissions/{sid}").json()["tests"]
        hidden = [t for t in tests if not t["is_sample"]]
        assert any(t["message"] for t in hidden)

    def test_sample_diagnostics_stay_with_the_solver(self, client, admin_client):
        """Sample input and answer are printed in the statement, so a message
        about one gives nothing away — and it is usually the program's own
        stderr, which is exactly what a beginner needs."""
        sid = self.solve(client, admin_client, "raise ValueError('my own bug')")
        tests = client.get(f"/api/submissions/{sid}").json()["tests"]
        samples = [t for t in tests if t["is_sample"]]
        assert samples and any("my own bug" in t["message"] for t in samples)

    def test_compile_errors_stay_with_the_solver(self, client, admin_client):
        """Produced from their own source, quoting nothing from the problem.
        Withheld, a CE is a verdict the solver cannot act on."""
        sid = self.solve(client, admin_client, "def broken(:\n    pass")
        data = client.get(f"/api/submissions/{sid}").json()
        assert data["verdict"] == "CE"
        assert data["message"], "a compile error must reach whoever wrote the code"

    def test_the_list_view_never_carried_it(self, client, admin_client):
        sid = self.solve(client, admin_client, "print(999)")
        rows = client.get("/api/submissions").json()["submissions"]
        assert rows and all("message" not in r for r in rows)
        assert sid == rows[0]["id"]


class TestAuthorCarriesItsRole:
    """A name styled as an admin on one page and plain on another reads as two
    different people, so the role travels with the name everywhere."""

    def test_the_list_reports_an_admin_author(self, admin_client):
        make_problem(admin_client)          # authored by `admin`
        row = admin_client.get("/api/problems").json()["problems"][0]
        assert row["author"] == "admin"
        assert row["author_role"] == "admin"

    def test_the_detail_reports_it_too(self, admin_client):
        make_problem(admin_client)
        detail = admin_client.get("/api/problems/a-plus-b").json()
        assert detail["author"] == "admin" and detail["author_role"] == "admin"

    def test_a_plain_author_is_marked_plain(self, client, admin_client):
        admin_client.post("/api/auth/logout")
        register(client, "writer")
        client.post("/api/auth/logout")
        admin_client.post("/api/auth/login",
                          json={"username": "admin", "password": "test-admin-password"})
        make_problem(admin_client, author="writer")
        row = admin_client.get("/api/problems").json()["problems"][0]
        assert row["author"] == "writer" and row["author_role"] == "user"

    def test_an_unattributed_problem_reports_neither(self, admin_client):
        make_problem(admin_client)
        db.execute("UPDATE problems SET author_id = NULL WHERE slug = 'a-plus-b'")
        row = admin_client.get("/api/problems").json()["problems"][0]
        assert row["author"] is None and row["author_role"] is None

    def test_a_dangling_author_id_reports_neither(self, isolated_data):
        """On a database created from schema.sql the foreign key forbids this,
        but the migrated `problems.author_id` has no REFERENCES clause — SQLite
        cannot add one to an existing table — so a live database can hold an id
        whose user is gone. Resolving it must not raise."""
        from stroj.api.deps import _author_of
        assert _author_of({"author_id": 99999}) == (None, None)
        assert _author_of({"author_id": None}) == (None, None)
        assert _author_of({}) == (None, None)


class TestMentionRoster:
    """`@name` has to render identically in a bio, a post, a statement and in
    the live preview of each. The previews have no server round trip to attach
    a resolved map to, so the whole roster is sent once and reused."""

    def test_it_lists_every_user_with_their_role(self, client, admin_client):
        admin_client.post("/api/auth/logout")
        register(client, "plainuser")
        users = client.get("/api/mentions").json()["users"]
        assert users["admin"] == "admin"
        assert users["plainuser"] == "user"

    def test_it_is_readable_without_signing_in(self, client, admin_client):
        """Statements and posts are public, so their mentions must render for
        a signed-out reader too."""
        admin_client.post("/api/auth/logout")
        response = client.get("/api/mentions")
        assert response.status_code == 200
        assert "admin" in response.json()["users"]

    def test_a_new_user_appears(self, client, admin_client):
        admin_client.post("/api/auth/logout")
        before = client.get("/api/mentions").json()["users"]
        register(client, "latecomer")
        after = client.get("/api/mentions").json()["users"]
        assert "latecomer" not in before and after["latecomer"] == "user"

    def test_a_promotion_is_reflected(self, client, admin_client):
        admin_client.post("/api/auth/logout")
        register(client, "risingstar")
        client.post("/api/auth/logout")
        admin_client.post("/api/auth/login",
                          json={"username": "admin", "password": "test-admin-password"})
        admin_client.post("/api/admin/users/risingstar/role?role=admin")
        assert admin_client.get("/api/mentions").json()["users"]["risingstar"] == "admin"

    def test_the_profile_no_longer_carries_its_own_map(self, client, admin_client):
        """Two sources of truth for the same thing is how they drift apart."""
        admin_client.post("/api/auth/logout")
        register(client, "writer9")
        assert "mentions" not in client.get("/api/users/writer9").json()


class TestBioSaving:
    """`PATCH /api/users/me` takes its bio in the request body. With the model
    missing, `from __future__ import annotations` leaves the annotation a lazy
    string, so the module still imports and FastAPI quietly demotes it to a
    query parameter — every save then 422s at runtime with nothing at import
    time to warn you."""

    def test_a_bio_can_be_saved_and_read_back(self, client, admin_client):
        admin_client.post("/api/auth/logout")
        register(client, "biosaver")
        response = client.patch("/api/users/me", json={"bio": "hello @admin"})
        assert response.status_code == 200, response.text
        assert client.get("/api/users/biosaver").json()["bio"] == "hello @admin"

    def test_the_bio_arrives_in_the_body_not_the_query(self, client, admin_client):
        admin_client.post("/api/auth/logout")
        register(client, "biosaver2")
        # A query-parameter endpoint would accept this and ignore the body.
        assert client.patch("/api/users/me").status_code == 422

    def test_an_oversized_bio_is_refused(self, client, admin_client):
        admin_client.post("/api/auth/logout")
        register(client, "biosaver3")
        big = "x" * 4001
        assert client.patch("/api/users/me", json={"bio": big}).status_code == 422

    def test_saving_requires_signing_in(self, client, admin_client):
        admin_client.post("/api/auth/logout")
        assert client.patch("/api/users/me", json={"bio": "hi"}).status_code == 401


class TestAnInterruptedJudgeRecovers:
    """A patch mid-contest now deploys immediately, so a submission being
    judged when the container goes down is an ordinary event rather than an
    accident. It has to come back as if it had never started."""

    def mid_judge(self, client, admin_client):
        """A submission left exactly as a killed worker would leave it."""
        make_problem(admin_client)
        admin_client.post("/api/auth/logout")
        register(client, "interrupted")
        sid = client.post("/api/submissions", json={
            "problem": "a-plus-b", "language": "python3",
            "source": "a,b=map(int,input().split())\nprint(a+b)"}).json()["id"]
        # Claimed, one test published, then the process dies.
        db.execute("UPDATE submissions SET verdict = 'JUDGING', score = 1,"
                   " time_ms = 42, memory_kb = 900, message = 'half done'"
                   " WHERE id = ?", (sid,))
        db.execute("INSERT INTO submission_tests"
                   " (submission_id, idx, verdict, time_ms, memory_kb, points, message)"
                   " VALUES (?, 1, 'AC', 42, 900, 1, '')", (sid,))
        return sid

    def test_it_goes_back_on_the_queue(self, client, admin_client):
        sid = self.mid_judge(client, admin_client)
        assert worker.requeue_stuck() == 1
        assert db.one("SELECT verdict FROM submissions WHERE id = ?",
                      (sid,))["verdict"] == "PENDING"

    def test_the_partial_run_is_cleared(self, client, admin_client):
        """Left behind, the submitter sees a half-finished result stuck at
        'pending' that never becomes what they actually get."""
        sid = self.mid_judge(client, admin_client)
        worker.requeue_stuck()
        row = db.one("SELECT score, time_ms, memory_kb, message, earned_percent,"
                     " judged_at FROM submissions WHERE id = ?", (sid,))
        assert row["score"] == 0 and row["time_ms"] == 0 and row["memory_kb"] == 0
        assert row["message"] == "" and row["earned_percent"] == 0
        assert row["judged_at"] is None
        left = db.one("SELECT COUNT(*) AS n FROM submission_tests"
                      " WHERE submission_id = ?", (sid,))["n"]
        assert left == 0

    def test_it_then_judges_normally(self, client, admin_client):
        sid = self.mid_judge(client, admin_client)
        worker.requeue_stuck()
        assert worker.drain() == 1
        detail = client.get(f"/api/submissions/{sid}").json()
        assert detail["verdict"] == "AC"
        assert detail["score"] == detail["max_score"]
        assert len(detail["tests"]) == 2

    def test_finished_submissions_are_untouched(self, client, admin_client):
        make_problem(admin_client)
        admin_client.post("/api/auth/logout")
        register(client, "finished")
        sid = client.post("/api/submissions", json={
            "problem": "a-plus-b", "language": "python3",
            "source": "a,b=map(int,input().split())\nprint(a+b)"}).json()["id"]
        worker.drain()
        before = dict(db.one("SELECT * FROM submissions WHERE id = ?", (sid,)))
        assert worker.requeue_stuck() == 0
        assert dict(db.one("SELECT * FROM submissions WHERE id = ?", (sid,))) == before

    def test_nothing_stuck_is_a_no_op(self, admin_client):
        assert worker.requeue_stuck() == 0


class TestProblemRanking:
    """Best first: most of the problem earned, then fastest, then smallest,
    then earliest. Correctness has to outrank speed, or a fast wrong answer
    beats a slow correct one."""

    def seed(self, admin_client, rows):
        """Insert judged submissions directly; the ordering is what is on trial."""
        make_problem(admin_client)
        pid = db.one("SELECT id FROM problems WHERE slug='a-plus-b'")["id"]
        ids = {}
        for name, verdict, earned, time_ms, memory_kb in rows:
            uid = db.one("SELECT id FROM users WHERE username = ?", (name,))
            if uid is None:
                uid = db.insert("INSERT INTO users (username, password_hash, created_at)"
                                " VALUES (?, 'x', ?)", (name, db.utcnow()))
            else:
                uid = uid["id"]
            ids[name] = db.insert(
                "INSERT INTO submissions (user_id, problem_id, language, source,"
                " verdict, score, max_score, earned_percent, time_ms, memory_kb,"
                " created_at, judged_at) VALUES (?,?,'python3','x',?,?,?,?,?,?,?,?)",
                (uid, pid, verdict, 0, 0, earned, time_ms, memory_kb,
                 db.utcnow(), db.utcnow()))
        return ids

    def order(self, client, slug="a-plus-b"):
        data = client.get(f"/api/problems/{slug}/ranking").json()
        return [s["username"] for s in data["submissions"]]

    def test_more_earned_ranks_higher(self, client, admin_client):
        self.seed(admin_client, [
            ("partial", "WA", 40, 10, 100),
            ("full", "AC", 100, 900, 9000),
        ])
        assert self.order(client) == ["full", "partial"]

    def test_faster_breaks_a_tie_on_score(self, client, admin_client):
        self.seed(admin_client, [
            ("slow", "AC", 100, 900, 100),
            ("quick", "AC", 100, 50, 100),
        ])
        assert self.order(client) == ["quick", "slow"]

    def test_smaller_breaks_a_tie_on_time(self, client, admin_client):
        self.seed(admin_client, [
            ("fat", "AC", 100, 100, 90000),
            ("lean", "AC", 100, 100, 1000),
        ])
        assert self.order(client) == ["lean", "fat"]

    def test_earlier_wins_an_exact_tie(self, client, admin_client):
        ids = self.seed(admin_client, [
            ("first", "AC", 100, 100, 1000),
            ("second", "AC", 100, 100, 1000),
        ])
        assert ids["first"] < ids["second"]
        assert self.order(client) == ["first", "second"]

    def test_ranks_are_numbered_from_one(self, client, admin_client):
        self.seed(admin_client, [("a", "AC", 100, 10, 10), ("b", "WA", 0, 10, 10)])
        data = client.get("/api/problems/a-plus-b/ranking").json()
        assert [s["rank"] for s in data["submissions"]] == [1, 2]

    def test_unjudged_submissions_are_left_out(self, client, admin_client):
        self.seed(admin_client, [
            ("done", "AC", 100, 10, 10),
            ("queued", "PENDING", 0, 0, 0),
            ("running", "JUDGING", 0, 0, 0),
            ("cancelled", "AB", 0, 0, 0),
        ])
        assert self.order(client) == ["done"]

    def test_a_pre_migration_accept_still_ranks_first(self, client, admin_client):
        """Submissions judged before `earned_percent` existed carry 0 there but
        a real score; ranking them last because of a schema change is wrong."""
        make_problem(admin_client)
        pid = db.one("SELECT id FROM problems WHERE slug='a-plus-b'")["id"]
        for name, verdict, score, earned in (("old", "AC", 2, 0), ("new", "WA", 0, 30)):
            uid = db.insert("INSERT INTO users (username, password_hash, created_at)"
                            " VALUES (?, 'x', ?)", (name, db.utcnow()))
            db.insert(
                "INSERT INTO submissions (user_id, problem_id, language, source,"
                " verdict, score, max_score, earned_percent, time_ms, memory_kb,"
                " created_at) VALUES (?,?,'python3','x',?,?,2,?,10,10,?)",
                (uid, pid, verdict, score, earned, db.utcnow()))
        assert self.order(client) == ["old", "new"]

    def test_a_hidden_problem_has_no_public_ranking(self, client, admin_client):
        make_problem(admin_client, slug="secret", visible=False)
        admin_client.post("/api/auth/logout")
        register(client, "nosy3")
        assert client.get("/api/problems/secret/ranking").status_code == 404

    def test_source_is_not_exposed(self, client, admin_client):
        self.seed(admin_client, [("someone", "AC", 100, 10, 10)])
        admin_client.post("/api/auth/logout")
        register(client, "reader")
        rows = client.get("/api/problems/a-plus-b/ranking").json()["submissions"]
        assert rows and all("source" not in r for r in rows)


class TestRankingRespectsAContestFreeze:
    """Listing a live contest's submissions here would hand out exactly what a
    frozen scoreboard is withholding."""

    def setup_contest(self, admin_client, running: bool):
        make_problem(admin_client)
        now = db.parse_time(db.utcnow())
        span = ((now - timedelta(hours=1), now + timedelta(hours=1)) if running
                else (now - timedelta(hours=3), now - timedelta(hours=1)))
        iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        cid = db.insert(
            "INSERT INTO contests (slug, title, description, starts_at, ends_at,"
            " scoring, penalty_minutes, created_at)"
            " VALUES ('live','Live','',?,?,'icpc',20,?)",
            (iso(span[0]), iso(span[1]), db.utcnow()))
        pid = db.one("SELECT id FROM problems WHERE slug='a-plus-b'")["id"]
        rival = db.insert("INSERT INTO users (username, password_hash, created_at)"
                          " VALUES ('rival','x',?)", (db.utcnow(),))
        db.insert(
            "INSERT INTO submissions (user_id, problem_id, contest_id, language,"
            " source, verdict, score, max_score, earned_percent, time_ms,"
            " memory_kb, created_at) VALUES (?,?,?,'python3','x','AC',2,2,100,5,5,?)",
            (rival, pid, cid, db.utcnow()))

    def names(self, client):
        return [s["username"] for s in
                client.get("/api/problems/a-plus-b/ranking").json()["submissions"]]

    def test_a_live_contest_entry_is_hidden(self, client, admin_client):
        self.setup_contest(admin_client, running=True)
        admin_client.post("/api/auth/logout")
        register(client, "onlooker")
        assert "rival" not in self.names(client)

    def test_it_appears_once_the_contest_ends(self, client, admin_client):
        self.setup_contest(admin_client, running=False)
        admin_client.post("/api/auth/logout")
        register(client, "onlooker2")
        assert "rival" in self.names(client)

    def test_an_admin_sees_it_during_the_contest(self, admin_client):
        self.setup_contest(admin_client, running=True)
        assert "rival" in self.names(admin_client)

    def test_you_always_see_your_own(self, client, admin_client):
        """Hiding a contestant's own result from them would be pointless."""
        self.setup_contest(admin_client, running=True)
        admin_client.post("/api/auth/logout")
        # Log in as the rival themselves.
        db.execute("UPDATE users SET password_hash = ? WHERE username = 'rival'",
                   (__import__("stroj.auth", fromlist=["auth"]).hash_password("password123"),))
        client.post("/api/auth/login",
                    json={"username": "rival", "password": "password123"})
        assert "rival" in self.names(client)


class TestTheSubmissionListRespectsALiveContest:
    """The scoreboard can be frozen, but the submissions list sat one click
    away showing every verdict as it landed."""

    def during(self, admin_client, running: bool):
        make_problem(admin_client)
        now = db.parse_time(db.utcnow())
        span = ((now - timedelta(hours=1), now + timedelta(hours=1)) if running
                else (now - timedelta(hours=3), now - timedelta(hours=1)))
        iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        cid = db.insert(
            "INSERT INTO contests (slug, title, description, starts_at, ends_at,"
            " scoring, penalty_minutes, created_at)"
            " VALUES ('live','Live','',?,?,'icpc',20,?)",
            (iso(span[0]), iso(span[1]), db.utcnow()))
        pid = db.one("SELECT id FROM problems WHERE slug='a-plus-b'")["id"]
        rival = db.insert("INSERT INTO users (username, password_hash, created_at)"
                          " VALUES ('rival2','x',?)", (db.utcnow(),))
        db.insert(
            "INSERT INTO submissions (user_id, problem_id, contest_id, language,"
            " source, verdict, score, max_score, earned_percent, time_ms,"
            " memory_kb, created_at) VALUES (?,?,?,'python3','x','AC',2,2,100,5,5,?)",
            (rival, pid, cid, db.utcnow()))

    def names(self, client):
        return [s["username"] for s in
                client.get("/api/submissions").json()["submissions"]]

    def test_a_rivals_live_result_is_hidden(self, client, admin_client):
        self.during(admin_client, running=True)
        admin_client.post("/api/auth/logout")
        register(client, "watcher")
        assert "rival2" not in self.names(client)

    def test_it_appears_once_the_contest_is_over(self, client, admin_client):
        self.during(admin_client, running=False)
        admin_client.post("/api/auth/logout")
        register(client, "watcher2")
        assert "rival2" in self.names(client)

    def test_an_admin_still_sees_everything(self, admin_client):
        self.during(admin_client, running=True)
        assert "rival2" in self.names(admin_client)

    def test_practice_submissions_are_unaffected(self, client, admin_client):
        """Only contest entries are withheld; ordinary practice is public."""
        self.during(admin_client, running=True)
        admin_client.post("/api/auth/logout")
        register(client, "practiser")
        client.post("/api/submissions", json={
            "problem": "a-plus-b", "language": "python3", "source": "print(1)"})
        assert "practiser" in self.names(client)


class TestAPerLanguageLimitReachesTheRuntime:
    """A runtime that caps its own heap with a flag — the JVM — is told the
    ceiling through that flag. Sizing it from the base limit while enforcing
    the per-language one means a raised allowance the program cannot use."""

    def test_the_jvm_heap_follows_the_java_limit(self, admin_client):
        from stroj.judge import languages, worker
        from stroj.judge.runner import ProblemSpec
        make_problem(admin_client, memory_limit_mb=64)
        admin_client.put("/api/admin/problems/a-plus-b/limits", json={
            "limits": {"java": {"time_limit_ms": 2000, "memory_limit_mb": 512}}})

        row = db.one("SELECT * FROM problems WHERE slug = 'a-plus-b'")
        spec = ProblemSpec.from_row(row, worker.load_limits(row["id"]))
        java = languages.get("java")
        _, resolved_mb = spec.limits_for(java)
        assert resolved_mb == 512

        argv = java.run_argv(resolved_mb)
        # 512 enforced, less the 320 MiB allowed for JVM overhead.
        assert any("-Xmx192m" == part for part in argv), argv
        # The base must not be what reaches it...
        assert not any("-Xmx64m" == part for part in argv), argv
        # ...and neither may the whole ceiling: a heap equal to the limit lets
        # the JVM fill it and then breach the limit with its own overhead.
        assert not any("-Xmx512m" == part for part in argv), argv

    def test_a_derived_limit_still_sizes_the_heap_as_it_always_did(self):
        """base -> base + overhead enforced -> heap back at base."""
        from stroj.judge import languages
        java = languages.get("java")
        for base in (64, 256, 512):
            argv = java.run_argv(java.effective_memory_limit_mb(base))
            assert f"-Xmx{base}m" in argv, (base, argv)

    def test_the_heap_never_goes_below_a_usable_floor(self):
        from stroj.judge import languages
        argv = languages.get("java").run_argv(16)
        assert "-Xmx32m" in argv, argv

    def test_the_source_uses_the_resolved_value(self):
        """Guard the wiring itself: the base limit is in scope right beside it
        and is the easy thing to reach for."""
        import pathlib
        from stroj.judge import runner
        source = pathlib.Path(runner.__file__).read_text()
        assert "lang.run_argv(problem.memory_limit_mb)" not in source
        assert "lang.run_argv(memory_limit_mb)" in source


class TestAContestHidesPointsAndTypes:
    """A problem's point value rates it against the archive and its type tags
    name the technique. Both are hints, so a running contest withholds them."""

    def setup_contest(self, admin_client, *, running: bool, visible: bool = False):
        make_problem(admin_client, slug="secret", visible=visible)
        admin_client.patch("/api/admin/problems/secret",
                           json={"types": ["binary search", "greedy"],
                                 "points": 175}).raise_for_status()
        now = db.parse_time(db.utcnow())
        span = ((now - timedelta(hours=1), now + timedelta(hours=1)) if running
                else (now - timedelta(hours=3), now - timedelta(hours=1)))
        iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        cid = db.insert(
            "INSERT INTO contests (slug, title, description, starts_at, ends_at,"
            " scoring, penalty_minutes, created_at)"
            " VALUES ('diag','Diagnostic','',?,?,'ioi',0,?)",
            (iso(span[0]), iso(span[1]), db.utcnow()))
        pid = db.one("SELECT id FROM problems WHERE slug='secret'")["id"]
        db.execute("INSERT INTO contest_problems (contest_id, problem_id, label)"
                   " VALUES (?, ?, 'A')", (cid, pid))

    def detail(self, client):
        response = client.get("/api/problems/secret")
        assert response.status_code == 200, response.text
        return response.json()

    def test_points_are_withheld_while_it_runs(self, client, admin_client):
        self.setup_contest(admin_client, running=True)
        admin_client.post("/api/auth/logout")
        register(client, "contestant")
        assert self.detail(client)["points"] is None

    def test_types_are_withheld_while_it_runs(self, client, admin_client):
        self.setup_contest(admin_client, running=True)
        admin_client.post("/api/auth/logout")
        register(client, "contestant2")
        assert self.detail(client)["types"] == []

    def test_the_seal_is_announced_so_the_page_can_adapt(self, client, admin_client):
        self.setup_contest(admin_client, running=True)
        admin_client.post("/api/auth/logout")
        register(client, "contestant3")
        assert self.detail(client)["metadata_sealed"] is True

    def test_subtask_weights_still_come_through(self, client, admin_client):
        """Percentages say what to attempt without rating the problem."""
        self.setup_contest(admin_client, running=True)
        admin_client.put("/api/admin/problems/secret/tests", json={"tests": [
            {"input": "2 3\n", "output": "5\n", "is_sample": True, "points": 1},
            {"input": "-1 1\n", "output": "0\n", "points": 1, "subtask": 1},
        ]}).raise_for_status()
        admin_client.post("/api/auth/logout")
        register(client, "contestant4")
        detail = self.detail(client)
        assert detail["points"] is None
        assert detail["samples"], "samples are not a hint"

    def test_an_admin_still_sees_everything(self, admin_client):
        self.setup_contest(admin_client, running=True)
        detail = self.detail(admin_client)
        assert detail["points"] == 175
        assert detail["types"] == ["binary search", "greedy"]
        assert detail["metadata_sealed"] is False

    def test_they_are_revealed_once_the_contest_ends(self, client, admin_client):
        self.setup_contest(admin_client, running=False)
        admin_client.post("/api/auth/logout")
        register(client, "afterwards")
        detail = self.detail(client)
        assert detail["points"] == 175
        assert detail["types"] == ["binary search", "greedy"]

    def test_an_already_public_problem_keeps_its_rating(self, client, admin_client):
        """Hiding an archived problem's points mid-contest protects nothing —
        anyone could have read them yesterday."""
        self.setup_contest(admin_client, running=True, visible=True)
        admin_client.post("/api/auth/logout")
        register(client, "browser")
        detail = self.detail(client)
        assert detail["points"] == 175
        assert detail["types"] == ["binary search", "greedy"]


class TestBulkSubmit:
    """Calibrating a problem's limits means running the intended solution in
    every language. One upload replaces three rounds of copy-paste."""

    CPP = "#include <cstdio>\nint main(){int a,b;scanf(\"%d %d\",&a,&b);printf(\"%d\\n\",a+b);}\n"
    PY = "a, b = input().split()\nprint(int(a) + int(b))\n"
    JAVA = ("public class Main { public static void main(String[] a) {"
            " java.util.Scanner s = new java.util.Scanner(System.in);"
            " System.out.println(s.nextInt() + s.nextInt()); } }\n")

    def bundle(self, files: dict) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, text in files.items():
                archive.writestr(name, text)
        return buffer.getvalue()

    def post(self, client, slug, files):
        return client.post(
            f"/api/admin/problems/{slug}/bulk-submit",
            files={"archive": ("solutions.zip", self.bundle(files), "application/zip")},
        )

    def test_every_source_file_becomes_a_submission(self, admin_client):
        make_problem(admin_client)
        response = self.post(admin_client, "a-plus-b", {
            "intended.cpp": self.CPP, "intended.py": self.PY, "Main.java": self.JAVA})
        assert response.status_code == 200, response.text
        submitted = response.json()["submitted"]
        assert len(submitted) == 3
        assert {s["language"] for s in submitted} == {"cpp", "python3", "java"}

    def test_the_language_comes_from_the_extension(self, admin_client):
        make_problem(admin_client)
        body = self.post(admin_client, "a-plus-b", {
            "a.cpp": self.CPP, "b.py": self.PY}).json()
        by_file = {s["file"]: s["language"] for s in body["submitted"]}
        assert by_file == {"a.cpp": "cpp", "b.py": "python3"}

    def test_nested_directories_are_flattened(self, admin_client):
        """Zipping a folder is the normal way to make one of these."""
        make_problem(admin_client)
        body = self.post(admin_client, "a-plus-b",
                         {"solutions/intended.cpp": self.CPP}).json()
        assert [s["file"] for s in body["submitted"]] == ["intended.cpp"]

    def test_readmes_and_junk_are_skipped_not_fatal(self, admin_client):
        make_problem(admin_client)
        body = self.post(admin_client, "a-plus-b", {
            "intended.cpp": self.CPP, "README.md": "notes",
            "Main.class": "\x00\x01", ".DS_Store": "junk",
            "__MACOSX/._x.cpp": "junk"}).json()
        assert [s["file"] for s in body["submitted"]] == ["intended.cpp"]
        assert {s["file"] for s in body["skipped"]} == {"README.md", "Main.class"}

    def test_a_bad_java_class_name_is_reported_per_file(self, admin_client):
        """One unusable file must not sink the files beside it."""
        make_problem(admin_client)
        body = self.post(admin_client, "a-plus-b", {
            "intended.cpp": self.CPP,
            "Solution.java": "public class Solution {}\n"}).json()
        assert [s["file"] for s in body["submitted"]] == ["intended.cpp"]
        assert body["skipped"][0]["file"] == "Solution.java"
        assert "Main" in body["skipped"][0]["reason"]

    def test_an_archive_with_nothing_to_run_is_an_error(self, admin_client):
        make_problem(admin_client)
        response = self.post(admin_client, "a-plus-b", {"README.md": "notes"})
        assert response.status_code == 400
        assert "README.md" in response.json()["detail"]

    def test_a_non_zip_is_rejected(self, admin_client):
        make_problem(admin_client)
        response = admin_client.post(
            "/api/admin/problems/a-plus-b/bulk-submit",
            files={"archive": ("x.zip", b"not a zip", "application/zip")})
        assert response.status_code == 400

    def test_it_ignores_the_in_flight_cap(self, admin_client):
        """The cap stops one account flooding the queue. An archive of eight
        solutions is not a flood, and truncating it silently would be worse."""
        make_problem(admin_client)
        files = {f"s{i}.py": f"# {i}\n{self.PY}" for i in range(8)}
        body = self.post(admin_client, "a-plus-b", files).json()
        assert len(body["submitted"]) == 8

    def test_runs_are_practice_never_contest_entries(self, admin_client):
        make_problem(admin_client)
        self.post(admin_client, "a-plus-b", {"intended.py": self.PY})
        rows = db.query("SELECT contest_id FROM submissions")
        assert rows and all(r["contest_id"] is None for r in rows)

    def test_a_hidden_problem_still_works(self, admin_client):
        """Every problem is hidden while it is being calibrated."""
        make_problem(admin_client, slug="secret", visible=False)
        response = self.post(admin_client, "secret", {"intended.py": self.PY})
        assert response.status_code == 200, response.text

    def test_only_admins_may_do_this(self, client, admin_client):
        make_problem(admin_client)
        admin_client.post("/api/auth/logout")
        register(client, "ordinary")
        response = self.post(client, "a-plus-b", {"intended.py": self.PY})
        assert response.status_code == 403

    def test_an_unknown_problem_is_a_404(self, admin_client):
        response = self.post(admin_client, "nope", {"intended.py": self.PY})
        assert response.status_code == 404


class TestTheScoreboardKeepsTheSealBeforeTheStart:
    """`contest_detail` withholds the problem set until the clock starts. The
    scoreboard sits one click away and was handing out the same labels, slugs
    and titles — enough to name five problems before anyone had seen them."""

    def setup_future(self, admin_client, *, minutes_away: int):
        make_problem(admin_client, slug="secret-a", visible=False)
        now = db.parse_time(db.utcnow())
        iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        start = now + timedelta(minutes=minutes_away)
        admin_client.post("/api/admin/contests", json={
            "slug": "diag", "title": "Diagnostic", "description": "",
            "starts_at": iso(start), "ends_at": iso(start + timedelta(minutes=30)),
            "scoring": "ioi", "penalty_minutes": 0, "freeze_minutes": 0,
        }).raise_for_status()
        admin_client.put("/api/admin/contests/diag/problems",
                         json={"problems": [{"slug": "secret-a", "label": "A"}]}
                         ).raise_for_status()

    def board(self, client):
        response = client.get("/api/contests/diag/scoreboard")
        assert response.status_code == 200, response.text
        return response.json()

    def test_the_problem_set_is_withheld(self, client, admin_client):
        self.setup_future(admin_client, minutes_away=60)
        admin_client.post("/api/auth/logout")
        register(client, "peeker")
        assert self.board(client)["problems"] == []

    def test_not_even_the_count_gets_out(self, client, admin_client):
        """Knowing there are five is itself information about the paper."""
        self.setup_future(admin_client, minutes_away=60)
        admin_client.post("/api/auth/logout")
        register(client, "peeker2")
        assert len(self.board(client)["problems"]) == 0

    def test_an_organiser_still_sees_it(self, admin_client):
        self.setup_future(admin_client, minutes_away=60)
        assert [p["label"] for p in self.board(admin_client)["problems"]] == ["A"]

    def test_it_opens_up_once_the_contest_starts(self, client, admin_client):
        self.setup_future(admin_client, minutes_away=-5)
        admin_client.post("/api/auth/logout")
        register(client, "contestant")
        board = self.board(client)
        assert [p["label"] for p in board["problems"]] == ["A"]
        assert board["problems"][0]["title"] == "A + B"

    def test_the_two_endpoints_now_agree(self, client, admin_client):
        """The bug was that they disagreed; this is what pins them together."""
        self.setup_future(admin_client, minutes_away=60)
        admin_client.post("/api/auth/logout")
        register(client, "peeker3")
        detail = client.get("/api/contests/diag").json()
        assert detail["sealed"] is True
        assert detail["problems"] == self.board(client)["problems"] == []


class TestBothSubmissionRoutesAgree:
    """The submissions list filters hidden problems and live contests in SQL.
    Fetching one submission by id bypassed all of it — the id is a small
    integer, so the whole list was readable by counting upwards.

    Every case here asserts the list and the by-id route give the same answer,
    because the bug was that they did not.
    """

    def visible_to(self, client, sid):
        listed = sid in [s["id"] for s in
                         client.get("/api/submissions?limit=200").json()["submissions"]]
        direct = client.get(f"/api/submissions/{sid}").status_code == 200
        assert listed == direct, (
            f"list says {listed} but by-id says {direct} for submission {sid}")
        return direct

    def contest_submission(self, admin_client, *, running: bool, visible: bool = False):
        make_problem(admin_client, slug="cprob", visible=visible)
        now = db.parse_time(db.utcnow())
        iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        span = ((now - timedelta(minutes=5), now + timedelta(minutes=25)) if running
                else (now - timedelta(hours=3), now - timedelta(hours=1)))
        cid = db.insert(
            "INSERT INTO contests (slug, title, description, starts_at, ends_at,"
            " scoring, penalty_minutes, created_at)"
            " VALUES ('diag','Diagnostic','',?,?,'ioi',0,?)",
            (iso(span[0]), iso(span[1]), db.utcnow()))
        pid = db.one("SELECT id FROM problems WHERE slug='cprob'")["id"]
        db.execute("INSERT INTO contest_problems (contest_id, problem_id, label)"
                   " VALUES (?,?, 'A')", (cid, pid))
        rival = db.insert("INSERT INTO users (username, password_hash, created_at)"
                          " VALUES ('rival','x',?)", (db.utcnow(),))
        return db.insert(
            "INSERT INTO submissions (user_id, problem_id, contest_id, language,"
            " source, verdict, score, max_score, created_at)"
            " VALUES (?,?,?,'python3','print(1)','AC',9,9,?)",
            (rival, pid, cid, iso(span[0] + timedelta(minutes=1))))

    def test_a_practice_run_on_a_hidden_problem_is_not_readable(self, client, admin_client):
        """Calibration runs are the setter's own working."""
        make_problem(admin_client, slug="secret", visible=False)
        sid = admin_client.post("/api/submissions", json={
            "problem": "secret", "language": "python3", "source": "print(1)"}).json()["id"]
        admin_client.post("/api/auth/logout")
        register(client, "nosy")
        assert self.visible_to(client, sid) is False

    def test_a_live_contest_entry_is_not_readable(self, client, admin_client):
        """Otherwise the frozen scoreboard is decoration: poll the ids instead."""
        sid = self.contest_submission(admin_client, running=True)
        admin_client.post("/api/auth/logout")
        register(client, "opponent")
        assert self.visible_to(client, sid) is False

    def test_it_opens_up_once_the_contest_ends(self, client, admin_client):
        sid = self.contest_submission(admin_client, running=False)
        admin_client.post("/api/auth/logout")
        register(client, "afterwards")
        assert self.visible_to(client, sid) is True

    def test_a_published_problem_is_readable(self, client, admin_client):
        make_problem(admin_client)
        sid = admin_client.post("/api/submissions", json={
            "problem": "a-plus-b", "language": "python3", "source": "print(1)"}).json()["id"]
        admin_client.post("/api/auth/logout")
        register(client, "browser")
        assert self.visible_to(client, sid) is True

    def test_you_can_always_read_your_own(self, client, admin_client):
        """Hiding a contestant's own result from them would be pointless."""
        make_problem(admin_client, slug="secret", visible=False)
        admin_client.post("/api/auth/logout")
        register(client, "author")
        db.execute("UPDATE problems SET visible = 1 WHERE slug = 'secret'")
        sid = client.post("/api/submissions", json={
            "problem": "secret", "language": "python3", "source": "print(1)"}).json()["id"]
        db.execute("UPDATE problems SET visible = 0 WHERE slug = 'secret'")
        assert client.get(f"/api/submissions/{sid}").status_code == 200

    def test_an_admin_reads_everything(self, admin_client):
        sid = self.contest_submission(admin_client, running=True)
        assert admin_client.get(f"/api/submissions/{sid}").status_code == 200

    def test_the_source_stays_private_either_way(self, client, admin_client):
        sid = self.contest_submission(admin_client, running=False)
        admin_client.post("/api/auth/logout")
        register(client, "reader")
        assert "source" not in client.get(f"/api/submissions/{sid}").json()


class TestTestDataIsUploadedOnce:
    """Inspecting an archive and then creating the problem used to send the
    whole thing twice. A real test set runs to tens of megabytes, so the second
    copy was the slowest step in authoring a problem."""

    def archive(self) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as z:
            z.writestr("sample1.in", "1 1\n")
            z.writestr("sample1.out", "2\n")
            z.writestr("2.in", "3 4\n")
            z.writestr("2.out", "7\n")
        return buffer.getvalue()

    def inspect(self, admin_client):
        response = admin_client.post(
            "/api/admin/testdata/inspect",
            files={"archive": ("t.zip", self.archive(), "application/zip")})
        assert response.status_code == 200, response.text
        return response.json()

    def test_inspect_hands_back_a_token(self, admin_client):
        assert re.fullmatch(r"[0-9a-f]{32}", self.inspect(admin_client)["token"])

    def test_the_token_stands_in_for_the_archive(self, admin_client):
        token = self.inspect(admin_client)["token"]
        admin_client.post("/api/admin/problems", json={"slug": "staged", "title": "S"})
        response = admin_client.post("/api/admin/problems/staged/tests/upload",
                                     data={"token": token, "samples": "1"})
        assert response.status_code == 200, response.text
        assert response.json()["tests"] == 2
        tests = admin_client.get("/api/admin/problems/staged/tests").json()["tests"]
        assert [t["is_sample"] for t in tests] == [True, False]

    def test_the_staged_copy_is_dropped_once_used(self, admin_client):
        """Otherwise every authored problem leaves its archive on disk."""
        token = self.inspect(admin_client)["token"]
        admin_client.post("/api/admin/problems", json={"slug": "staged", "title": "S"})
        admin_client.post("/api/admin/problems/staged/tests/upload",
                          json=None, data={"token": token})
        assert not routes_admin._staged_path(token).exists()

    def test_a_spent_token_reports_410_so_the_client_can_resend(self, admin_client):
        token = self.inspect(admin_client)["token"]
        routes_admin._staged_path(token).unlink()
        admin_client.post("/api/admin/problems", json={"slug": "staged", "title": "S"})
        response = admin_client.post("/api/admin/problems/staged/tests/upload",
                                     data={"token": token})
        assert response.status_code == 410

    def test_a_token_cannot_name_a_file_outside_staging(self, admin_client):
        """The token reaches the server from the client, so it is a path until
        proven otherwise."""
        admin_client.post("/api/admin/problems", json={"slug": "staged", "title": "S"})
        for evil in ("../../etc/passwd", "..", "a/b", "ZZZZ" * 8):
            response = admin_client.post("/api/admin/problems/staged/tests/upload",
                                         data={"token": evil})
            assert response.status_code == 400, f"{evil} -> {response.status_code}"

    def test_sending_the_archive_still_works(self, admin_client):
        """The problem editor uploads directly, with no inspect step."""
        admin_client.post("/api/admin/problems", json={"slug": "direct", "title": "D"})
        response = admin_client.post(
            "/api/admin/problems/direct/tests/upload",
            files={"archive": ("t.zip", self.archive(), "application/zip")},
            data={"samples": "1"})
        assert response.status_code == 200, response.text
        assert response.json()["tests"] == 2

    def test_neither_archive_nor_token_is_a_clean_400(self, admin_client):
        admin_client.post("/api/admin/problems", json={"slug": "empty", "title": "E"})
        assert admin_client.post(
            "/api/admin/problems/empty/tests/upload").status_code == 400


class TestTheActivityCalendarRespectsVisibility:
    """A per-day count looks like an anonymous statistic, but during a contest
    it says how many attempts a rival has made and how many landed — the thing
    a frozen scoreboard exists to withhold. The calendar counts only what the
    submissions list would show the same viewer."""

    def contest_submissions(self, admin_client, *, running: bool, count: int = 3):
        make_problem(admin_client, slug="cprob", visible=False)
        now = db.parse_time(db.utcnow())
        iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        span = ((now - timedelta(minutes=5), now + timedelta(minutes=25)) if running
                else (now - timedelta(hours=3), now - timedelta(hours=1)))
        cid = db.insert(
            "INSERT INTO contests (slug, title, description, starts_at, ends_at,"
            " scoring, penalty_minutes, created_at)"
            " VALUES ('diag','Diagnostic','',?,?,'ioi',0,?)",
            (iso(span[0]), iso(span[1]), db.utcnow()))
        pid = db.one("SELECT id FROM problems WHERE slug='cprob'")["id"]
        db.execute("INSERT INTO contest_problems (contest_id, problem_id, label)"
                   " VALUES (?,?, 'A')", (cid, pid))
        rival = db.insert("INSERT INTO users (username, password_hash, created_at)"
                          " VALUES ('rival','x',?)", (db.utcnow(),))
        for _ in range(count):
            db.insert(
                "INSERT INTO submissions (user_id, problem_id, contest_id, language,"
                " source, verdict, created_at) VALUES (?,?,?,'python3','','AC',?)",
                (rival, pid, cid, db.utcnow()))
        return rival

    def totals(self, client, username="rival"):
        """The calendar total and the number of rows the list shows, which must
        agree — disagreeing is the whole bug."""
        activity = client.get(f"/api/users/{username}").json()["activity"]
        listed = client.get(
            f"/api/submissions?username={username}&limit=200").json()["submissions"]
        return activity["total"], len(listed)

    def test_a_live_contest_is_invisible_to_a_rival(self, client, admin_client):
        self.contest_submissions(admin_client, running=True)
        admin_client.post("/api/auth/logout")
        register(client, "opponent")
        assert self.totals(client) == (0, 0)

    def test_it_appears_once_the_contest_ends(self, client, admin_client):
        self.contest_submissions(admin_client, running=False)
        admin_client.post("/api/auth/logout")
        register(client, "afterwards")
        assert self.totals(client) == (3, 3)

    def test_an_organiser_sees_it_all_along(self, admin_client):
        self.contest_submissions(admin_client, running=True)
        assert self.totals(admin_client) == (3, 3)

    def test_practice_runs_on_an_unpublished_problem_are_hidden(
            self, client, admin_client):
        """The setter's own calibration runs, which is every problem before it
        is published."""
        make_problem(admin_client, slug="secret", visible=False)
        admin_client.post("/api/submissions", json={
            "problem": "secret", "language": "python3", "source": "print(1)"})
        admin_client.post("/api/auth/logout")
        register(client, "nosy")
        assert client.get("/api/users/admin").json()["activity"]["total"] == 0

    def test_you_always_see_your_own_calendar(self, client, admin_client):
        """Hiding a contestant's own attempts from them would be pointless."""
        self.contest_submissions(admin_client, running=True)
        admin_client.post("/api/auth/logout")
        db.execute("UPDATE users SET password_hash = ? WHERE username = 'rival'",
                   (__import__("stroj.auth", fromlist=["auth"]).hash_password("password123"),))
        client.post("/api/auth/login",
                    json={"username": "rival", "password": "password123"})
        assert client.get("/api/users/rival").json()["activity"]["total"] == 3

    def test_published_practice_still_counts_for_everyone(self, client, admin_client):
        make_problem(admin_client)
        admin_client.post("/api/submissions", json={
            "problem": "a-plus-b", "language": "python3", "source": "print(1)"})
        admin_client.post("/api/auth/logout")
        register(client, "browser")
        assert client.get("/api/users/admin").json()["activity"]["total"] == 1


class TestRatedContestsOverTheApi:
    """The rating feature as the site sees it."""

    def make_contest(self, admin_client, slug="rnd", *, rated=True, ended=True):
        now = db.parse_time(db.utcnow())
        iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        span = ((now - timedelta(hours=3), now - timedelta(hours=1)) if ended
                else (now - timedelta(minutes=5), now + timedelta(hours=1)))
        response = admin_client.post("/api/admin/contests", json={
            "slug": slug, "title": slug.title(), "description": "",
            "starts_at": iso(span[0]), "ends_at": iso(span[1]),
            "scoring": "ioi", "penalty_minutes": 0, "freeze_minutes": 0,
            "rated": rated})
        assert response.status_code == 200, response.text
        return slug

    def enter(self, slug, username, earned):
        """Someone competes and finishes with `earned` percent."""
        user = db.one("SELECT id FROM users WHERE username = ?", (username,))
        contest_row = db.one("SELECT * FROM contests WHERE slug = ?", (slug,))
        problem = db.one("SELECT id FROM problems LIMIT 1")
        db.execute("INSERT OR IGNORE INTO contest_problems (contest_id, problem_id,"
                   " label) VALUES (?, ?, 'A')", (contest_row["id"], problem["id"]))
        db.insert(
            "INSERT INTO submissions (user_id, problem_id, contest_id, language,"
            " source, verdict, score, max_score, earned_percent, created_at)"
            " VALUES (?, ?, ?, 'python3', '', 'AC', ?, 100, ?, ?)",
            (user["id"], problem["id"], contest_row["id"], earned, earned,
             contest_row["starts_at"]))

    def test_a_contest_says_whether_it_counts(self, client, admin_client):
        self.make_contest(admin_client, "rated-one", rated=True)
        self.make_contest(admin_client, "practice", rated=False)
        by_slug = {c["slug"]: c for c in client.get("/api/contests").json()["contests"]}
        assert by_slug["rated-one"]["rated"] is True
        assert by_slug["practice"]["rated"] is False

    def test_rating_is_applied_and_shown_on_the_board(self, client, admin_client):
        make_problem(admin_client)
        register(client, "winner")
        register(client, "runnerup")
        admin_client.post("/api/auth/login", json={
            "username": "admin", "password": _admin_pw()})
        slug = self.make_contest(admin_client, "rnd")
        self.enter(slug, "winner", 100)
        self.enter(slug, "runnerup", 10)
        assert admin_client.post("/api/admin/ratings/recompute").json()["changes"] == 2

        board = client.get(f"/api/contests/{slug}/scoreboard").json()
        by_name = {r["username"]: r for r in board["rows"]}
        assert by_name["winner"]["rating"]["delta"] > 0
        assert by_name["runnerup"]["rating"]["delta"] < 0
        assert by_name["winner"]["rating"]["rank"]["name"]

    def test_an_unrated_contest_carries_no_rating_column(self, client, admin_client):
        make_problem(admin_client)
        register(client, "someone")
        admin_client.post("/api/auth/login", json={
            "username": "admin", "password": _admin_pw()})
        slug = self.make_contest(admin_client, "practice", rated=False)
        self.enter(slug, "someone", 100)
        admin_client.post("/api/admin/ratings/recompute")
        board = client.get(f"/api/contests/{slug}/scoreboard").json()
        assert all("rating" not in row for row in board["rows"])

    def test_a_profile_carries_rating_rank_and_history(self, client, admin_client):
        make_problem(admin_client)
        register(client, "climber")
        register(client, "other")
        admin_client.post("/api/auth/login", json={
            "username": "admin", "password": _admin_pw()})
        slug = self.make_contest(admin_client, "rnd")
        self.enter(slug, "climber", 100)
        self.enter(slug, "other", 0)
        admin_client.post("/api/admin/ratings/recompute")

        profile = client.get("/api/users/climber").json()
        assert profile["rated_contests"] == 1
        assert profile["rating"] > 1000
        assert profile["rating_rank"]["name"]
        assert [h["contest_slug"] for h in profile["rating_history"]] == [slug]
        assert profile["rating_history"][0]["delta"] > 0

    def test_someone_who_never_competed_is_unranked(self, client, admin_client):
        admin_client.post("/api/auth/logout")
        register(client, "newcomer")
        profile = client.get("/api/users/newcomer").json()
        assert profile["rated_contests"] == 0
        assert profile["rating_rank"] is None
        assert profile["rating_history"] == []

    def test_rank_does_not_shadow_leaderboard_position(self, client, admin_client):
        """`rank` is the position on the users page; the ladder rank has its
        own name. They were briefly the same key, which silently replaced one
        with the other everywhere both appear."""
        make_problem(admin_client)
        register(client, "solver")
        client.post("/api/submissions", json={
            "problem": "a-plus-b", "language": "python3",
            "source": "a, b = input().split()\nprint(int(a) + int(b))"})
        worker.drain()
        board = client.get("/api/leaderboard").json()["standings"]
        assert board and isinstance(board[0]["rank"], int)
        assert "rating_rank" in board[0]

    def test_the_ladder_is_published_for_the_legend(self, client):
        body = client.get("/api/ranks").json()
        assert len(body["ladder"]) == 25
        assert body["ladder"][0]["name"] == "Iron 1"
        assert body["ladder"][-1]["name"] == "Radiant"
        assert body["radiant_at"] > body["start"]

    def test_only_admins_may_force_a_rebuild(self, client, admin_client):
        admin_client.post("/api/auth/logout")
        register(client, "meddler")
        assert client.post("/api/admin/ratings/recompute").status_code == 403

    def test_turning_rating_off_rebuilds_immediately(self, client, admin_client):
        make_problem(admin_client)
        register(client, "alpha")
        register(client, "beta")
        admin_client.post("/api/auth/login", json={
            "username": "admin", "password": _admin_pw()})
        slug = self.make_contest(admin_client, "rnd")
        self.enter(slug, "alpha", 100)
        self.enter(slug, "beta", 0)
        admin_client.post("/api/admin/ratings/recompute")
        assert client.get("/api/users/alpha").json()["rated_contests"] == 1

        admin_client.patch(f"/api/admin/contests/{slug}", json={"rated": False})
        assert client.get("/api/users/alpha").json()["rated_contests"] == 0
