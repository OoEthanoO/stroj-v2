"""HTTP surface: auth, permissions, submitting, admin authoring."""

from __future__ import annotations

import io
import zipfile
from datetime import timedelta

import pytest

from stroj import db
from stroj.judge import worker


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
        make_problem(admin_client)
        admin_client.post("/api/auth/logout")
        register(client, "coder")
        response = client.post("/api/submissions", json={
            "problem": "a-plus-b", "language": "python3",
            "source": "a,b=map(int,input().split())\nprint(a*b)"})
        worker.drain()
        detail = client.get(f"/api/submissions/{response.json()['id']}").json()
        assert detail["verdict"] == "WA"
        assert "Test 1" in detail["message"]

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
