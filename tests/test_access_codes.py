"""Rated contests are sat in the room: entering one takes the code given out there."""

from __future__ import annotations

from datetime import timedelta

import pytest

from stroj import contest, db
from tests.conftest import _admin_password
from tests.test_api import make_problem, register


def as_admin(client):
    client.post("/api/auth/logout")
    client.post(
        "/api/auth/login", json={"username": "admin", "password": _admin_password()}
    ).raise_for_status()


def as_member(client, username):
    client.post("/api/auth/logout")
    client.post(
        "/api/auth/login", json={"username": username, "password": "password123"}
    ).raise_for_status()


def make_contest(client, slug, *, rated=True, starts_in=timedelta(minutes=-5),
                 lasts=timedelta(hours=2), problems=("secret", "open")):
    now = db.parse_time(db.utcnow())
    client.post("/api/admin/contests", json={
        "slug": slug, "title": slug.title(), "scoring": "ioi", "rated": rated,
        "starts_at": (now + starts_in).isoformat(),
        "ends_at": (now + starts_in + lasts).isoformat(),
    }).raise_for_status()
    client.put(f"/api/admin/contests/{slug}/problems",
               json={"problems": [{"slug": s} for s in problems]}).raise_for_status()
    return slug


def new_code(client, slug):
    return client.post(f"/api/admin/contests/{slug}/access-code").json()["code"]


@pytest.fixture
def room(admin_client):
    """A rated contest that is running, one hidden problem written for it and
    one public problem reused in it, and two members. Leaves the client signed
    in as the admin."""
    make_problem(admin_client, slug="secret", visible=False)
    make_problem(admin_client, slug="open")
    make_contest(admin_client, "final")
    admin_client.post("/api/auth/logout")
    register(admin_client, "ann")
    admin_client.post("/api/auth/logout")
    register(admin_client, "bob")
    as_admin(admin_client)
    return admin_client


def detail(client, slug="final"):
    return client.get(f"/api/contests/{slug}").json()


def enter(client, code, slug="final"):
    return client.post(f"/api/contests/{slug}/enter", json={"code": code})


def submit(client, problem, slug="final"):
    return client.post("/api/submissions", json={
        "problem": problem, "language": "python3", "contest": slug,
        "source": "print(1)"})


class TestKeptOutUntilEntered:
    def test_the_problem_set_is_sealed(self, room):
        as_member(room, "ann")
        data = detail(room)
        assert data["state"] == "running"
        assert data["sealed"] is True
        assert data["problems"] == []
        assert data["access"]["needs_code"] is True

    def test_a_hidden_problem_in_it_stays_hidden(self, room):
        """The problem page must not hand out what the contest page withholds."""
        as_member(room, "ann")
        assert room.get("/api/problems/secret").status_code == 404

    def test_submitting_into_it_is_refused(self, room):
        as_member(room, "ann")
        response = submit(room, "open")
        assert response.status_code == 403
        assert "access code" in response.json()["detail"]
        assert db.one("SELECT COUNT(*) AS n FROM submissions")["n"] == 0

    def test_the_scoreboard_withholds_the_problem_set_too(self, room):
        as_member(room, "ann")
        board = room.get("/api/contests/final/scoreboard").json()
        assert board["problems"] == []

    def test_a_signed_out_visitor_is_kept_out(self, room):
        room.post("/api/auth/logout")
        data = detail(room)
        assert data["sealed"] is True and data["access"]["needs_code"] is True
        assert room.get("/api/problems/secret").status_code == 404


class TestEntering:
    def test_nobody_can_enter_before_a_code_exists(self, room):
        as_member(room, "ann")
        assert detail(room)["access"]["code_ready"] is False
        response = enter(room, "ABCDEF")
        assert response.status_code == 403
        assert "not opened" in response.json()["detail"]

    def test_the_right_code_opens_everything(self, room):
        code = new_code(room, "final")
        as_member(room, "ann")
        assert enter(room, code).json() == {"entered": True}

        data = detail(room)
        assert data["sealed"] is False
        assert [p["slug"] for p in data["problems"]] == ["secret", "open"]
        assert data["access"] == {"needs_code": False, "entered": True, "code_ready": True}
        assert room.get("/api/problems/secret").status_code == 200
        assert submit(room, "open").status_code == 200
        assert submit(room, "secret").status_code == 200
        board = room.get("/api/contests/final/scoreboard").json()
        assert [p["slug"] for p in board["problems"]] == ["secret", "open"]

    def test_the_code_is_forgiving_about_case_spaces_and_dashes(self, room):
        code = new_code(room, "final")        # e.g. "K7Q-XM4"
        as_member(room, "ann")
        sloppy = " " + code.lower().replace("-", " ") + " "
        assert enter(room, sloppy).status_code == 200

    def test_a_wrong_code_is_refused(self, room):
        code = new_code(room, "final")
        as_member(room, "ann")
        wrong = "".join("A" if ch != "A" else "B" for ch in code if ch != "-")
        response = enter(room, wrong)
        assert response.status_code == 403
        assert detail(room)["sealed"] is True

    def test_entry_is_per_member(self, room):
        code = new_code(room, "final")
        as_member(room, "ann")
        enter(room, code).raise_for_status()
        as_member(room, "bob")
        assert detail(room)["sealed"] is True
        assert submit(room, "open").status_code == 403

    def test_entering_twice_is_harmless(self, room):
        code = new_code(room, "final")
        as_member(room, "ann")
        enter(room, code).raise_for_status()
        assert enter(room, code).json() == {"entered": True}
        assert db.one("SELECT COUNT(*) AS n FROM contest_entries")["n"] == 1

    def test_signing_in_is_required(self, room):
        new_code(room, "final")
        room.post("/api/auth/logout")
        assert enter(room, "ABCDEF").status_code == 401

    def test_guessing_is_rate_limited(self, room):
        code = new_code(room, "final")
        as_member(room, "ann")
        wrong = "".join("A" if ch != "A" else "B" for ch in code if ch != "-")
        for _ in range(10):
            assert enter(room, wrong).status_code == 403
        # Even the right code waits now: otherwise the limit only slows the
        # guesses that are wrong, which is all of them but the last.
        assert enter(room, code).status_code == 429

    def test_a_room_can_enter_before_the_start(self, room):
        make_contest(room, "later", starts_in=timedelta(hours=1), problems=("open",))
        code = new_code(room, "later")
        as_member(room, "ann")
        assert enter(room, code, "later").status_code == 200
        data = detail(room, "later")
        # In, but the paper stays face down until the clock starts.
        assert data["access"]["entered"] is True
        assert data["sealed"] is True and data["problems"] == []


class TestANewCode:
    def test_it_stops_the_old_one_but_keeps_the_room_in(self, room):
        first = new_code(room, "final")
        as_member(room, "ann")
        enter(room, first).raise_for_status()

        as_admin(room)
        second = new_code(room, "final")
        assert second != first

        as_member(room, "bob")
        assert enter(room, first).status_code == 403
        assert enter(room, second).status_code == 200
        as_member(room, "ann")
        assert detail(room)["sealed"] is False


class TestWhoIsNeverAsked:
    def test_an_unrated_contest(self, room):
        make_contest(room, "practice", rated=False)
        as_member(room, "ann")
        data = detail(room, "practice")
        assert data["sealed"] is False and data["access"]["needs_code"] is False
        assert room.get("/api/problems/secret").status_code == 200
        assert submit(room, "open", "practice").status_code == 200
        assert enter(room, "ABCDEF", "practice").status_code == 400

    def test_an_admin(self, room):
        data = detail(room)
        assert data["sealed"] is False
        assert len(data["problems"]) == 2
        assert submit(room, "secret").status_code == 200

    def test_anyone_once_it_is_over(self, admin_client):
        make_problem(admin_client, slug="secret", visible=False)
        make_contest(admin_client, "done", starts_in=timedelta(hours=-3),
                     lasts=timedelta(hours=2), problems=("secret",))
        admin_client.post("/api/auth/logout")
        register(admin_client, "late")
        data = detail(admin_client, "done")
        assert data["sealed"] is False and data["access"]["needs_code"] is False
        assert admin_client.get("/api/problems/secret").status_code == 200
        assert enter(admin_client, "ABCDEF", "done").status_code == 400


class TestTheAdminSide:
    def test_the_code_and_the_entrants_are_listed(self, room):
        empty = room.get("/api/admin/contests/final/access").json()
        assert empty["code"] is None and empty["entrants"] == []

        code = new_code(room, "final")
        as_member(room, "ann")
        enter(room, code).raise_for_status()
        as_admin(room)
        view = room.get("/api/admin/contests/final/access").json()
        assert view["code"] == code
        assert [e["username"] for e in view["entrants"]] == ["ann"]

    def test_members_cannot_make_or_read_codes(self, room):
        as_member(room, "ann")
        assert room.post("/api/admin/contests/final/access-code").status_code == 403
        assert room.get("/api/admin/contests/final/access").status_code == 403

    def test_the_code_never_reaches_a_member(self, room):
        code = new_code(room, "final")
        bare = code.replace("-", "")
        as_member(room, "ann")
        for path in ("/api/contests", "/api/contests/final",
                     "/api/contests/final/scoreboard"):
            text = room.get(path).text
            assert code not in text and bare not in text


class TestCodes:
    def test_codes_avoid_characters_that_read_as_others(self):
        for _ in range(200):
            code = contest.new_access_code()
            assert len(code) == contest.CODE_LENGTH
            assert set(code) <= set(contest.CODE_ALPHABET)
        assert not set("0O1IL") & set(contest.CODE_ALPHABET)

    def test_formatting_and_normalising_round_trip(self):
        assert contest.format_code("K7QXM4") == "K7Q-XM4"
        assert contest.normalise_code(" k7q-xm4 ") == "K7QXM4"
        assert contest.format_code(None) is None
