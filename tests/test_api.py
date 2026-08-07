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
        assert [t["is_sample"] for t in tests] == [False, False, True]
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
