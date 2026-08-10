"""Rated contests: what gets rated, when, and what stays out of it."""

from __future__ import annotations

from datetime import timedelta

import pytest

from stroj import contest, db, rating
from stroj.judge import worker


def iso(moment) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


@pytest.fixture
def club():
    """Three competitors and a helper that runs a finished contest."""
    users = {
        name: db.insert(
            "INSERT INTO users (username, password_hash, created_at)"
            " VALUES (?, 'x', ?)",
            (name, db.utcnow()),
        )
        for name in ("ann", "bob", "cid")
    }
    problem = db.insert(
        "INSERT INTO problems (slug, title, points, created_at)"
        " VALUES ('p', 'P', 100, ?)",
        (db.utcnow(),),
    )

    def run(slug, results, *, rated=True, weeks_ago=1, scoring="ioi"):
        """`results` maps username -> earned percent. Higher places higher."""
        now = db.parse_time(db.utcnow())
        ends = now - timedelta(weeks=weeks_ago)
        starts = ends - timedelta(hours=2)
        contest_id = db.insert(
            "INSERT INTO contests (slug, title, description, starts_at, ends_at,"
            " scoring, penalty_minutes, freeze_minutes, rated, created_at)"
            " VALUES (?, ?, '', ?, ?, ?, 0, 0, ?, ?)",
            (slug, slug, iso(starts), iso(ends), scoring, int(rated), db.utcnow()),
        )
        db.execute(
            "INSERT INTO contest_problems (contest_id, problem_id, label)"
            " VALUES (?, ?, 'A')",
            (contest_id, problem),
        )
        for name, earned in results.items():
            db.insert(
                "INSERT INTO submissions (user_id, problem_id, contest_id, language,"
                " source, verdict, score, max_score, earned_percent, created_at)"
                " VALUES (?, ?, ?, 'python3', '', 'AC', ?, 100, ?, ?)",
                (users[name], problem, contest_id, earned, earned,
                 iso(starts + timedelta(minutes=5))),
            )
        return contest_id

    return {"users": users, "run": run}


def rating_of(club, name):
    row = db.one("SELECT * FROM users WHERE id = ?", (club["users"][name],))
    return row["rating"], row["rated_contests"]


class TestWhatCounts:
    def test_a_rated_contest_moves_everyone_who_entered(self, club):
        club["run"]("r1", {"ann": 100, "bob": 50, "cid": 0})
        contest.recompute_ratings()
        ann, bob, cid = (rating_of(club, n)[0] for n in ("ann", "bob", "cid"))
        assert ann > bob > cid

    def test_an_unrated_contest_moves_nobody(self, club):
        club["run"]("practice", {"ann": 100, "bob": 0}, rated=False)
        contest.recompute_ratings()
        for name in ("ann", "bob"):
            value, played = rating_of(club, name)
            assert (value, played) == (rating.START_RATING, 0)

    def test_a_running_contest_is_not_rated_yet(self, club):
        """Its standings can still change with the next submission."""
        now = db.parse_time(db.utcnow())
        cid = db.insert(
            "INSERT INTO contests (slug, title, description, starts_at, ends_at,"
            " scoring, penalty_minutes, freeze_minutes, rated, created_at)"
            " VALUES ('live','Live','',?,?,'ioi',0,0,1,?)",
            (iso(now - timedelta(minutes=5)), iso(now + timedelta(hours=1)), db.utcnow()),
        )
        assert cid not in [c["id"] for c in contest.rated_contests()]

    def test_only_people_who_submitted_are_rated(self, club):
        club["run"]("r1", {"ann": 100, "bob": 0})
        contest.recompute_ratings()
        assert rating_of(club, "cid") == (rating.START_RATING, 0)

    def test_entering_and_scoring_nothing_still_counts(self, club):
        """Turning up and failing is a result, not an absence."""
        club["run"]("r1", {"ann": 100, "bob": 0})
        contest.recompute_ratings()
        value, played = rating_of(club, "bob")
        assert played == 1 and value < rating.START_RATING

    def test_a_contest_nobody_entered_rates_nobody_and_still_settles(self, club):
        cid = club["run"]("empty", {})
        contest.recompute_ratings()
        row = db.one("SELECT rated_at FROM contests WHERE id = ?", (cid,))
        # Marked done, or the judge would try to rate it again forever.
        assert row["rated_at"] is not None


class TestReplayIsTheDefinition:
    def test_rating_twice_changes_nothing(self, club):
        club["run"]("r1", {"ann": 100, "bob": 0})
        contest.recompute_ratings()
        first = rating_of(club, "ann")
        contest.recompute_ratings()
        assert rating_of(club, "ann") == first

    def test_contests_apply_in_the_order_they_finished(self, club):
        club["run"]("older", {"ann": 100, "bob": 0}, weeks_ago=8)
        club["run"]("newer", {"ann": 0, "bob": 100}, weeks_ago=1)
        contest.recompute_ratings()
        history = contest.rating_history(club["users"]["ann"])
        assert [h["contest_slug"] for h in history] == ["older", "newer"]
        assert history[0]["delta"] > 0 and history[1]["delta"] < 0

    def test_making_a_contest_unrated_undoes_it(self, club):
        cid = club["run"]("oops", {"ann": 100, "bob": 0})
        contest.recompute_ratings()
        assert rating_of(club, "ann")[0] != rating.START_RATING
        db.execute("UPDATE contests SET rated = 0 WHERE id = ?", (cid,))
        contest.recompute_ratings()
        assert rating_of(club, "ann") == (rating.START_RATING, 0)

    def test_a_correction_to_an_old_contest_reflows_everything_after_it(self, club):
        club["run"]("first", {"ann": 100, "bob": 0}, weeks_ago=6)
        club["run"]("second", {"ann": 100, "bob": 0}, weeks_ago=1)
        contest.recompute_ratings()
        before = rating_of(club, "ann")[0]
        # Bob's run is regraded: he actually won the first contest.
        db.execute(
            "UPDATE submissions SET earned_percent = 100 WHERE user_id = ?"
            " AND contest_id = (SELECT id FROM contests WHERE slug = 'first')",
            (club["users"]["bob"],),
        )
        db.execute(
            "UPDATE submissions SET earned_percent = 0 WHERE user_id = ?"
            " AND contest_id = (SELECT id FROM contests WHERE slug = 'first')",
            (club["users"]["ann"],),
        )
        contest.recompute_ratings()
        assert rating_of(club, "ann")[0] < before

    def test_history_is_rebuilt_not_appended(self, club):
        club["run"]("r1", {"ann": 100, "bob": 0})
        for _ in range(3):
            contest.recompute_ratings()
        assert len(contest.rating_history(club["users"]["ann"])) == 1


class TestTimingBetweenContests:
    def test_a_gap_makes_the_next_result_count_for_more(self, club):
        """Same finish, further apart: the rusty competitor moves further."""
        club["run"]("weekly-1", {"ann": 100, "bob": 0}, weeks_ago=30)
        club["run"]("weekly-2", {"ann": 100, "bob": 0}, weeks_ago=29)
        contest.recompute_ratings()
        prompt = contest.rating_history(club["users"]["ann"])[1]["delta"]

        db.execute("DELETE FROM contests WHERE slug = 'weekly-2'")
        club["run"]("much-later", {"ann": 100, "bob": 0}, weeks_ago=4)
        contest.recompute_ratings()
        delayed = contest.rating_history(club["users"]["ann"])[1]["delta"]

        assert delayed > prompt


class TestTheJudgeSettlesContestsOnItsOwn:
    def test_a_finished_rated_contest_is_picked_up(self, club):
        club["run"]("r1", {"ann": 100, "bob": 0})
        assert [c["slug"] for c in contest.awaiting_rating()] == ["r1"]
        assert worker.apply_finished_ratings() == 1
        assert rating_of(club, "ann")[1] == 1

    def test_it_does_not_run_again_once_settled(self, club):
        club["run"]("r1", {"ann": 100, "bob": 0})
        worker.apply_finished_ratings()
        assert contest.awaiting_rating() == []
        assert worker.apply_finished_ratings() == 0

    def test_a_contest_still_being_judged_waits(self, club):
        """Rating someone on a result that has not landed yet would be wrong."""
        cid = club["run"]("r1", {"ann": 100, "bob": 0})
        db.execute(
            "INSERT INTO submissions (user_id, problem_id, contest_id, language,"
            " source, verdict, created_at) SELECT ?, problem_id, ?, 'python3', '',"
            " 'PENDING', created_at FROM submissions WHERE contest_id = ? LIMIT 1",
            (club["users"]["cid"], cid, cid),
        )
        assert contest.awaiting_rating() == []
        assert worker.apply_finished_ratings() == 0

    def test_an_unrated_contest_is_never_picked_up(self, club):
        club["run"]("practice", {"ann": 100, "bob": 0}, rated=False)
        assert contest.awaiting_rating() == []
