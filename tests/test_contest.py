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
