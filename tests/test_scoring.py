"""Geometric-decay ranking: the incentive shape, not just the arithmetic."""

from __future__ import annotations

import pytest

from stroj import db, scoring


class TestWeightedScore:
    def test_empty(self):
        assert scoring.weighted_score([]) == 0

    def test_single_problem_counts_in_full(self):
        assert scoring.weighted_score([300]) == pytest.approx(300)

    def test_later_solves_count_for_less(self):
        d = scoring.DECAY
        assert scoring.weighted_score([100, 100]) == pytest.approx(100 + 100 * d)

    def test_order_of_input_is_irrelevant(self):
        assert scoring.weighted_score([50, 300, 25]) == pytest.approx(
            scoring.weighted_score([25, 50, 300])
        )

    def test_hardest_always_takes_rank_zero(self):
        """A hard solve counts in full however many easy ones came first."""
        easy = [10] * 30
        gain = scoring.weighted_score(easy + [500]) - scoring.weighted_score(easy)
        assert gain > 400

    def test_one_hard_problem_beats_many_easy_ones(self):
        """The whole point: grinding must not out-earn challenging yourself."""
        grinder = scoring.weighted_score([50] * 25)
        challenger = scoring.weighted_score([600, 500, 400])
        assert challenger > grinder

    def test_grinding_hits_a_ceiling(self):
        """Repeating one tier converges to points / (1 - decay)."""
        ceiling = 100 / (1 - scoring.DECAY)
        assert scoring.weighted_score([100] * 500) < ceiling
        assert scoring.weighted_score([100] * 500) == pytest.approx(ceiling, rel=0.01)

    def test_score_never_decreases(self):
        """Inserting a solve pushes weaker ones down a rank; the new term must
        always more than cover that loss, at any insertion point."""
        import random

        rng = random.Random(7)
        for _ in range(200):
            existing = [rng.randint(1, 1000) for _ in range(rng.randint(0, 20))]
            before = scoring.weighted_score(existing)
            after = scoring.weighted_score(existing + [rng.randint(1, 1000)])
            assert after >= before - 1e-9

    def test_marginal_gain_shrinks_for_repeated_difficulty(self):
        """Each additional problem of the same value is worth less than the last."""
        solved = []
        gains = []
        for _ in range(6):
            gains.append(scoring.marginal_gain(solved, 100))
            solved.append(100)
        assert gains == sorted(gains, reverse=True)
        assert gains[-1] < gains[0]

    def test_a_harder_problem_gains_more_than_an_easy_one(self):
        solved = [100, 100, 100]
        assert scoring.marginal_gain(solved, 400) > scoring.marginal_gain(solved, 100)

    @pytest.mark.parametrize("decay", [0.5, 0.8, 0.95, 0.99])
    def test_monotonic_for_any_decay(self, decay):
        base = [500, 300, 100, 40]
        for value in (1, 50, 400, 900):
            assert scoring.weighted_score(base + [value], decay) >= scoring.weighted_score(
                base, decay
            )


class TestLeaderboard:
    @pytest.fixture
    def people(self):
        users = {
            name: db.insert(
                "INSERT INTO users (username, password_hash, role, created_at)"
                " VALUES (?, 'x', ?, ?)",
                (name, "admin" if name == "boss" else "user", db.utcnow()),
            )
            for name in ("grinder", "challenger", "boss")
        }
        problems = {}
        for slug, points in (("easy", 50), ("medium", 200), ("hard", 600)):
            problems[slug] = db.insert(
                "INSERT INTO problems (slug, title, points, created_at)"
                " VALUES (?, ?, ?, ?)",
                (slug, slug.title(), points, db.utcnow()),
            )

        def solve(user, slug, verdict="AC"):
            db.insert(
                "INSERT INTO submissions (user_id, problem_id, language, source,"
                " verdict, created_at) VALUES (?, ?, 'python3', '', ?, ?)",
                (users[user], problems[slug], verdict, db.utcnow()),
            )

        return {"solve": solve, "users": users}

    def test_ranks_by_score(self, people):
        people["solve"]("challenger", "hard")
        people["solve"]("grinder", "easy")
        people["solve"]("grinder", "medium")
        board = scoring.leaderboard()
        assert [r["username"] for r in board] == ["challenger", "grinder"]
        assert board[0]["rank"] == 1

    def test_only_accepted_submissions_count(self, people):
        people["solve"]("grinder", "hard", verdict="WA")
        assert scoring.leaderboard() == []

    def test_a_problem_counts_once_however_often_solved(self, people):
        for _ in range(5):
            people["solve"]("grinder", "medium")
        board = scoring.leaderboard()
        assert board[0]["solved"] == 1
        assert board[0]["score"] == pytest.approx(200)

    def test_role_is_exposed_so_admins_can_be_marked(self, people):
        people["solve"]("boss", "easy")
        assert scoring.leaderboard()[0]["role"] == "admin"

    def test_ties_share_a_rank(self, people):
        people["solve"]("grinder", "medium")
        people["solve"]("challenger", "medium")
        assert [r["rank"] for r in scoring.leaderboard()] == [1, 1]

    def test_solved_problems_show_their_contribution(self, people):
        people["solve"]("grinder", "hard")
        people["solve"]("grinder", "easy")
        rows = scoring.solved_problems(people["users"]["grinder"])
        assert [r["slug"] for r in rows] == ["hard", "easy"]
        assert rows[0]["weight"] == 1.0
        assert rows[1]["weight"] == pytest.approx(scoring.DECAY)
        assert sum(r["contribution"] for r in rows) == pytest.approx(
            scoring.user_score(people["users"]["grinder"]), abs=0.2
        )
