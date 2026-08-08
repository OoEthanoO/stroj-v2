"""Contest windows and the two scoring systems."""

from __future__ import annotations

from datetime import timedelta

import pytest

from stroj import contest, db


def iso(moment) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


@pytest.fixture
def fixtures():
    """A contest with problems A/B, three users, and a submission helper."""
    now = db.parse_time(db.utcnow())
    started = now - timedelta(hours=1)
    ends = now + timedelta(hours=1)

    contest_id = db.insert(
        "INSERT INTO contests (slug, title, description, starts_at, ends_at, scoring,"
        " penalty_minutes, created_at) VALUES ('c', 'C', '', ?, ?, 'icpc', 20, ?)",
        (iso(started), iso(ends), db.utcnow()),
    )
    problems = {}
    for label, slug in (("A", "alpha"), ("B", "beta")):
        problem_id = db.insert(
            "INSERT INTO problems (slug, title, created_at) VALUES (?, ?, ?)",
            (slug, slug.title(), db.utcnow()),
        )
        db.execute(
            "INSERT INTO contest_problems (contest_id, problem_id, label) VALUES (?, ?, ?)",
            (contest_id, problem_id, label),
        )
        problems[label] = problem_id
    users = {
        name: db.insert(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, 'x', ?)",
            (name, db.utcnow()),
        )
        for name in ("ann", "bob", "cid")
    }

    def submit(user, label, verdict, minute, score=0, max_score=10):
        return db.insert(
            "INSERT INTO submissions (user_id, problem_id, contest_id, language, source,"
            " verdict, score, max_score, created_at) VALUES (?, ?, ?, 'python3', '', ?, ?, ?, ?)",
            (users[user], problems[label], contest_id, verdict, score, max_score,
             iso(started + timedelta(minutes=minute))),
        )

    def board(scoring="icpc"):
        db.execute("UPDATE contests SET scoring = ? WHERE id = ?", (scoring, contest_id))
        return contest.scoreboard(db.one("SELECT * FROM contests WHERE id = ?", (contest_id,)))

    return {"submit": submit, "board": board, "contest_id": contest_id,
            "started": started, "ends": ends, "users": users}


class TestContestState:
    def test_running(self, fixtures):
        row = db.one("SELECT * FROM contests WHERE id = ?", (fixtures["contest_id"],))
        assert contest.state_of(row) == contest.RUNNING
        assert contest.is_running(row)

    def test_before_and_after(self, fixtures):
        now = db.parse_time(db.utcnow())
        db.execute(
            "UPDATE contests SET starts_at = ?, ends_at = ? WHERE id = ?",
            (iso(now + timedelta(hours=1)), iso(now + timedelta(hours=2)), fixtures["contest_id"]),
        )
        row = db.one("SELECT * FROM contests WHERE id = ?", (fixtures["contest_id"],))
        assert contest.state_of(row) == contest.BEFORE

        db.execute(
            "UPDATE contests SET starts_at = ?, ends_at = ? WHERE id = ?",
            (iso(now - timedelta(hours=2)), iso(now - timedelta(hours=1)), fixtures["contest_id"]),
        )
        row = db.one("SELECT * FROM contests WHERE id = ?", (fixtures["contest_id"],))
        assert contest.state_of(row) == contest.ENDED


class TestIcpcScoring:
    def test_first_try_penalty_is_just_the_minute(self, fixtures):
        fixtures["submit"]("ann", "A", "AC", minute=17)
        row = fixtures["board"]()["rows"][0]
        assert row["solved"] == 1
        assert row["penalty"] == 17
        assert row["cells"]["A"]["attempts"] == 1

    def test_rejected_attempts_add_twenty_minutes_each(self, fixtures):
        fixtures["submit"]("ann", "A", "WA", minute=5)
        fixtures["submit"]("ann", "A", "TLE", minute=8)
        fixtures["submit"]("ann", "A", "AC", minute=30)
        row = fixtures["board"]()["rows"][0]
        assert row["penalty"] == 30 + 2 * 20

    def test_attempts_after_the_solve_are_free(self, fixtures):
        fixtures["submit"]("ann", "A", "AC", minute=10)
        fixtures["submit"]("ann", "A", "WA", minute=40)
        row = fixtures["board"]()["rows"][0]
        assert row["penalty"] == 10
        assert row["cells"]["A"]["attempts"] == 1

    def test_unsolved_problems_cost_nothing(self, fixtures):
        fixtures["submit"]("ann", "A", "WA", minute=5)
        fixtures["submit"]("ann", "A", "WA", minute=6)
        row = fixtures["board"]()["rows"][0]
        assert row["solved"] == 0
        assert row["penalty"] == 0

    def test_more_solved_beats_lower_penalty(self, fixtures):
        fixtures["submit"]("ann", "A", "AC", minute=1)
        fixtures["submit"]("bob", "A", "AC", minute=50)
        fixtures["submit"]("bob", "B", "AC", minute=55)
        rows = fixtures["board"]()["rows"]
        assert [r["username"] for r in rows] == ["bob", "ann"]

    def test_equal_solves_break_on_penalty(self, fixtures):
        fixtures["submit"]("ann", "A", "AC", minute=40)
        fixtures["submit"]("bob", "A", "AC", minute=10)
        rows = fixtures["board"]()["rows"]
        assert [r["username"] for r in rows] == ["bob", "ann"]

    def test_ties_share_a_rank(self, fixtures):
        fixtures["submit"]("ann", "A", "AC", minute=10)
        fixtures["submit"]("bob", "A", "AC", minute=10)
        rows = fixtures["board"]()["rows"]
        assert [r["rank"] for r in rows] == [1, 1]

    def test_pending_submissions_are_flagged_not_counted(self, fixtures):
        fixtures["submit"]("ann", "A", "PENDING", minute=5)
        row = fixtures["board"]()["rows"][0]
        assert row["cells"]["A"]["pending"] is True
        assert row["cells"]["A"]["attempts"] == 0
        assert row["solved"] == 0

    def test_per_problem_solve_counts(self, fixtures):
        fixtures["submit"]("ann", "A", "AC", minute=5)
        fixtures["submit"]("bob", "A", "AC", minute=6)
        fixtures["submit"]("cid", "B", "WA", minute=7)
        board = fixtures["board"]()
        counts = {p["label"]: p["solved_by"] for p in board["problems"]}
        assert counts == {"A": 2, "B": 0}


class TestIoiScoring:
    def test_best_score_per_problem_counts(self, fixtures):
        fixtures["submit"]("ann", "A", "WA", minute=5, score=3, max_score=10)
        fixtures["submit"]("ann", "A", "WA", minute=9, score=7, max_score=10)
        fixtures["submit"]("ann", "A", "WA", minute=12, score=5, max_score=10)
        row = fixtures["board"]("ioi")["rows"][0]
        assert row["cells"]["A"]["score"] == 70
        assert row["total_score"] == 70

    def test_full_marks_count_as_solved(self, fixtures):
        fixtures["submit"]("ann", "A", "AC", minute=5, score=10, max_score=10)
        row = fixtures["board"]("ioi")["rows"][0]
        assert row["cells"]["A"]["solved"] is True
        assert row["solved"] == 1

    def test_scores_sum_across_problems(self, fixtures):
        fixtures["submit"]("ann", "A", "WA", minute=5, score=5, max_score=10)
        fixtures["submit"]("ann", "B", "AC", minute=9, score=10, max_score=10)
        row = fixtures["board"]("ioi")["rows"][0]
        assert row["total_score"] == 150

    def test_ranking_is_by_total_score(self, fixtures):
        fixtures["submit"]("ann", "A", "WA", minute=5, score=5, max_score=10)
        fixtures["submit"]("bob", "A", "WA", minute=5, score=9, max_score=10)
        rows = fixtures["board"]("ioi")["rows"]
        assert [r["username"] for r in rows] == ["bob", "ann"]

    def test_a_zero_max_score_does_not_divide_by_zero(self, fixtures):
        fixtures["submit"]("ann", "A", "CE", minute=5, score=0, max_score=0)
        row = fixtures["board"]("ioi")["rows"][0]
        assert row["total_score"] == 0


def test_submissions_outside_the_window_are_ignored(fixtures):
    fixtures["submit"]("ann", "A", "AC", minute=-30)   # before the start
    fixtures["submit"]("bob", "A", "AC", minute=500)   # after the end
    assert fixtures["board"]()["rows"] == []


def test_practice_submissions_do_not_reach_the_board(fixtures):
    """A submission with no contest_id is practice, not a contest entry."""
    problem_id = db.one("SELECT id FROM problems WHERE slug = 'alpha'")["id"]
    db.execute(
        "INSERT INTO submissions (user_id, problem_id, contest_id, language, source,"
        " verdict, created_at) VALUES (?, ?, NULL, 'python3', '', 'AC', ?)",
        (fixtures["users"]["ann"], problem_id, db.utcnow()),
    )
    assert fixtures["board"]()["rows"] == []


def test_minutes_since_start_never_goes_negative(fixtures):
    row = db.one("SELECT * FROM contests WHERE id = ?", (fixtures["contest_id"],))
    early = iso(fixtures["started"] - timedelta(minutes=5))
    assert contest.minutes_since_start(row, early) == 0


class TestScoreboardFreeze:
    """Near the end the board stops resolving submissions, so nobody can tell
    whether the team above them just solved something."""

    def freeze(self, fixtures, minutes):
        db.execute(
            "UPDATE contests SET freeze_minutes = ? WHERE id = ?",
            (minutes, fixtures["contest_id"]),
        )

    def test_no_freeze_by_default(self, fixtures):
        fixtures["submit"]("ann", "A", "AC", minute=115)
        board = fixtures["board"]()
        assert board["frozen"] is False
        assert board["rows"][0]["solved"] == 1

    def test_submissions_before_the_freeze_still_resolve(self, fixtures):
        # Contest runs 60 min either side of now; freeze covers the last 30.
        self.freeze(fixtures, 30)
        fixtures["submit"]("ann", "A", "AC", minute=10)
        board = fixtures["board"]()
        assert board["frozen"] is True
        assert board["rows"][0]["solved"] == 1
        assert board["rows"][0]["cells"]["A"]["frozen"] == 0

    def test_submissions_after_the_freeze_are_hidden(self, fixtures):
        self.freeze(fixtures, 45)
        # The contest ends 60 minutes from its start + 60; a submission at
        # minute 100 lands inside the last 45 minutes.
        fixtures["submit"]("ann", "A", "AC", minute=100)
        board = fixtures["board"]()
        row = board["rows"][0]
        assert row["solved"] == 0, "a frozen solve must not show as solved"
        assert row["cells"]["A"]["frozen"] == 1
        assert row["cells"]["A"]["attempts"] == 0

    def test_frozen_attempts_do_not_change_the_ranking(self, fixtures):
        self.freeze(fixtures, 45)
        fixtures["submit"]("ann", "A", "AC", minute=5)
        fixtures["submit"]("bob", "A", "AC", minute=100)   # frozen
        fixtures["submit"]("bob", "B", "AC", minute=101)   # frozen
        rows = fixtures["board"]()["rows"]
        # bob has more solves in reality, but the visible board must not say so.
        assert [r["username"] for r in rows] == ["ann", "bob"]
        assert rows[0]["solved"] == 1 and rows[1]["solved"] == 0

    def test_admins_can_see_through_the_freeze(self, fixtures):
        self.freeze(fixtures, 45)
        fixtures["submit"]("ann", "A", "AC", minute=100)
        row = db.one("SELECT * FROM contests WHERE id = ?", (fixtures["contest_id"],))
        revealed = contest.scoreboard(row, reveal=True)
        assert revealed["frozen"] is False
        assert revealed["rows"][0]["solved"] == 1

    def test_freeze_lifts_once_the_contest_ends(self, fixtures):
        self.freeze(fixtures, 45)
        now = db.parse_time(db.utcnow())
        # Move the whole window into the past. The submission has to stay
        # inside it, or the scoreboard filters it out for unrelated reasons —
        # so it lands 30 min before the end, well within the frozen tail.
        db.execute(
            "UPDATE contests SET starts_at = ?, ends_at = ? WHERE id = ?",
            (iso(now - timedelta(hours=3)), iso(now - timedelta(hours=1)),
             fixtures["contest_id"]),
        )
        fixtures["submit"]("ann", "A", "AC", minute=-30)  # now - 1h30m

        board = fixtures["board"]()
        assert board["state"] == contest.ENDED
        assert board["frozen"] is False
        assert board["rows"][0]["solved"] == 1

    def test_freeze_moment_is_reported(self, fixtures):
        self.freeze(fixtures, 30)
        row = db.one("SELECT * FROM contests WHERE id = ?", (fixtures["contest_id"],))
        moment = contest.freeze_at(row)
        assert moment is not None
        delta = db.parse_time(row["ends_at"]) - db.parse_time(moment)
        assert abs(delta.total_seconds() - 30 * 60) < 1
