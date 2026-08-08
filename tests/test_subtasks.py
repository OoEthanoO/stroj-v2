"""Subtasks: partial credit that corresponds to solving a weaker version of the
problem, rather than to how many tests happened to pass."""

from __future__ import annotations

import io
import zipfile

import pytest

from stroj import db, scoring, testdata
from stroj.judge import runner
# Imported under other names: pytest tries to collect anything called Test*.
from stroj.judge.runner import AC, WA
from stroj.judge.runner import TestOutcome as Outcome
from stroj.judge.runner import TestSpec as Spec


def archive(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, content in entries:
            zf.writestr(name, content)
    return buffer.getvalue()


def pair(name, value="1"):
    return [(f"{name}.in", f"{value}\n"), (f"{name}.out", f"{value}\n")]


class TestParsingSubtasks:
    def test_directories_become_subtasks(self):
        parsed = testdata.parse_zip(archive(
            pair("subtask1/01") + pair("subtask1/02") + pair("subtask2/01")))
        assert parsed.subtasks == {1: 50, 2: 50}
        assert [t["subtask"] for t in parsed.tests] == [1, 1, 2]

    def test_declared_percentages_are_honoured(self):
        parsed = testdata.parse_zip(archive(
            pair("subtask1-30/01") + pair("subtask2-70/01")))
        assert parsed.subtasks == {1: 30, 2: 70}

    def test_percentages_must_total_one_hundred(self):
        with pytest.raises(testdata.TestDataError, match="add up to 100"):
            testdata.parse_zip(archive(pair("subtask1-30/01") + pair("subtask2-30/01")))

    def test_percentages_are_all_or_nothing(self):
        with pytest.raises(testdata.TestDataError, match="every subtask"):
            testdata.parse_zip(archive(pair("subtask1-30/01") + pair("subtask2/01")))

    def test_an_even_split_still_totals_one_hundred(self):
        """Three subtasks at 33 each would silently lose a point."""
        parsed = testdata.parse_zip(archive(
            pair("subtask1/01") + pair("subtask2/01") + pair("subtask3/01")))
        assert sum(parsed.subtasks.values()) == 100

    def test_samples_may_sit_outside_a_subtask(self):
        parsed = testdata.parse_zip(archive(pair("sample1") + pair("subtask1/01")))
        assert parsed.subtasks == {1: 100}
        assert parsed.tests[0]["is_sample"] and parsed.tests[0]["subtask"] == 0

    def test_a_stray_hidden_test_is_rejected(self):
        """Half-grouping is nearly always a mistake, and an ungrouped test could
        never be worth anything — so it would go silently unscored."""
        with pytest.raises(testdata.TestDataError, match="must live"):
            testdata.parse_zip(archive(pair("subtask1/01") + pair("loose")))

    def test_same_stem_in_two_subtasks_does_not_collide(self):
        parsed = testdata.parse_zip(archive(pair("subtask1/01") + pair("subtask2/01")))
        assert len(parsed.tests) == 2

    def test_tests_are_grouped_contiguously(self):
        parsed = testdata.parse_zip(archive(
            pair("subtask2/01") + pair("subtask1/01") + pair("subtask1/02")))
        assert [t["subtask"] for t in parsed.tests] == [1, 1, 2]

    def test_an_archive_without_subtasks_still_parses(self):
        parsed = testdata.parse_zip(archive(pair("01") + pair("02")))
        assert parsed.subtasks == {}
        assert all(t["subtask"] == 0 for t in parsed.tests)


class TestEarnedPercent:
    def specs(self, layout):
        return [Spec(i, "", "", 1, subtask=s) for i, s in enumerate(layout, 1)]

    def results(self, verdicts):
        return [Outcome(i, v, 0, 0, 0) for i, v in enumerate(verdicts, 1)]

    def test_a_fully_solved_subtask_pays_out(self):
        earned = runner.earned_percent(
            self.specs([1, 1, 2, 2]), self.results([AC, AC, WA, WA]),
            {1: 40, 2: 60}, True)
        assert earned == 40

    def test_a_partly_solved_subtask_pays_nothing(self):
        """All-or-nothing per group is the point: half a subtask does not mean
        you solved a weaker version of the problem."""
        earned = runner.earned_percent(
            self.specs([1, 1, 2, 2]), self.results([AC, WA, AC, AC]),
            {1: 40, 2: 60}, True)
        assert earned == 60

    def test_everything_solved_is_one_hundred(self):
        earned = runner.earned_percent(
            self.specs([1, 2]), self.results([AC, AC]), {1: 30, 2: 70}, True)
        assert earned == 100

    def test_nothing_solved_is_zero(self):
        earned = runner.earned_percent(
            self.specs([1, 2]), self.results([WA, WA]), {1: 30, 2: 70}, True)
        assert earned == 0

    def test_without_subtasks_partial_counts_tests(self):
        """The documented fallback: 10 of 20 tests is 50%."""
        specs = self.specs([0] * 20)
        results = self.results([AC] * 10 + [WA] * 10)
        assert runner.earned_percent(specs, results, {}, True) == 50

    def test_without_subtasks_and_without_partial_it_is_all_or_nothing(self):
        specs = self.specs([0, 0, 0, 0])
        assert runner.earned_percent(specs, self.results([AC, AC, AC, WA]), {}, False) == 0
        assert runner.earned_percent(specs, self.results([AC] * 4), {}, False) == 100

    def test_samples_do_not_dilute_the_fraction(self):
        """A free sample must not be worth as much as a real test."""
        specs = [
            Spec(1, "", "", 1, is_sample=True),
            Spec(2, "", "", 1),
            Spec(3, "", "", 1),
        ]
        earned = runner.earned_percent(specs, self.results([AC, AC, WA]), {}, True)
        assert earned == 50   # one of two real tests, not two of three

    def test_a_run_that_stopped_early_scores_what_it_reached(self):
        specs = self.specs([0, 0, 0, 0])
        assert runner.earned_percent(specs, self.results([AC, WA]), {}, True) == 25


class TestPartialCreditReachesTheLeaderboard:
    @pytest.fixture
    def setup(self):
        user = db.insert(
            "INSERT INTO users (username, password_hash, created_at)"
            " VALUES ('learner', 'x', ?)", (db.utcnow(),))
        problem = db.insert(
            "INSERT INTO problems (slug, title, points, partial, created_at)"
            " VALUES ('hard', 'Hard', 500, 1, ?)", (db.utcnow(),))

        def submit(percent, verdict="WA"):
            db.insert(
                "INSERT INTO submissions (user_id, problem_id, language, source,"
                " verdict, earned_percent, created_at)"
                " VALUES (?, ?, 'python3', '', ?, ?, ?)",
                (user, problem, verdict, percent, db.utcnow()))

        return {"user": user, "submit": submit}

    def test_a_partial_result_earns_points(self, setup):
        """The whole reason for this feature: a beginner who solves the easy
        version of a hard problem should get something for it."""
        setup["submit"](40)
        assert scoring.user_score(setup["user"]) == pytest.approx(200)  # 40% of 500

    def test_the_best_attempt_counts_not_the_last(self, setup):
        setup["submit"](80)
        setup["submit"](20)
        assert scoring.user_score(setup["user"]) == pytest.approx(400)

    def test_a_later_full_solve_upgrades_the_score(self, setup):
        setup["submit"](40)
        assert scoring.user_score(setup["user"]) == pytest.approx(200)
        setup["submit"](100, verdict="AC")
        assert scoring.user_score(setup["user"]) == pytest.approx(500)

    def test_zero_percent_does_not_appear_at_all(self, setup):
        setup["submit"](0)
        assert scoring.user_score(setup["user"]) == 0
        assert scoring.solved_problems(setup["user"]) == []

    def test_the_profile_shows_what_was_earned(self, setup):
        setup["submit"](40)
        row = scoring.solved_problems(setup["user"])[0]
        assert row["points"] == 500
        assert row["earned_percent"] == 40
        assert row["earned"] == 200

    def test_repricing_a_problem_updates_standings(self, setup):
        """Storing a percentage rather than an absolute is what makes this work."""
        setup["submit"](50)
        assert scoring.user_score(setup["user"]) == pytest.approx(250)
        db.execute("UPDATE problems SET points = 1000 WHERE slug = 'hard'")
        assert scoring.user_score(setup["user"]) == pytest.approx(500)


class TestSubmissionGrouping:
    """A submission that passed one subtask and failed another is unreadable as
    a flat list, so the detail view groups it."""

    def _problem_with_subtasks(self, admin_client):
        admin_client.post("/api/admin/problems", json={
            "slug": "tiered", "title": "Tiered", "partial": True, "points": 300})
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("sample1.in", "1\n"); zf.writestr("sample1.out", "1\n")
            for i in (1, 2):
                zf.writestr(f"subtask1-40/{i}.in", "1\n")
                zf.writestr(f"subtask1-40/{i}.out", "1\n")
            zf.writestr("subtask2-60/1.in", "2\n")
            zf.writestr("subtask2-60/1.out", "2\n")
        admin_client.post(
            "/api/admin/problems/tiered/tests/upload",
            files={"archive": ("t.zip", buffer.getvalue(), "application/zip")},
        ).raise_for_status()

    def test_each_test_reports_its_subtask(self, admin_client):
        from stroj.judge import worker

        self._problem_with_subtasks(admin_client)
        # Echoes the input, so it passes on "1" and fails on "2".
        response = admin_client.post("/api/submissions", json={
            "problem": "tiered", "language": "python3", "source": "print(1)"})
        worker.drain()

        body = admin_client.get(f"/api/submissions/{response.json()['id']}").json()
        by_subtask = {}
        for test in body["tests"]:
            by_subtask.setdefault(test["subtask"], []).append(test["verdict"])
        assert by_subtask[0] == ["AC"]          # the sample
        assert by_subtask[1] == ["AC", "AC"]    # subtask 1 fully passed
        assert by_subtask[2] == ["WA"]          # subtask 2 failed
        assert body["earned_percent"] == 40

    def test_the_detail_carries_the_subtask_weights(self, admin_client):
        self._problem_with_subtasks(admin_client)
        response = admin_client.post("/api/submissions", json={
            "problem": "tiered", "language": "python3", "source": "print(1)"})
        body = admin_client.get(f"/api/submissions/{response.json()['id']}").json()
        assert body["subtasks"] == [
            {"idx": 1, "percent": 40, "tests": 2},
            {"idx": 2, "percent": 60, "tests": 1},
        ]

    def test_samples_are_reported_as_samples(self, admin_client):
        from stroj.judge import worker

        self._problem_with_subtasks(admin_client)
        response = admin_client.post("/api/submissions", json={
            "problem": "tiered", "language": "python3", "source": "print(1)"})
        worker.drain()
        body = admin_client.get(f"/api/submissions/{response.json()['id']}").json()
        assert [t["is_sample"] for t in body["tests"]] == [True, False, False, False]

    def test_a_problem_without_subtasks_reports_none(self, admin_client):
        from tests.test_api import make_problem

        make_problem(admin_client)
        response = admin_client.post("/api/submissions", json={
            "problem": "a-plus-b", "language": "python3", "source": "print(1)"})
        body = admin_client.get(f"/api/submissions/{response.json()['id']}").json()
        assert body["subtasks"] == []
        assert all(t["subtask"] == 0 for t in body["tests"])
