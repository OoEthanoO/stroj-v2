"""Ranking users by what they have solved.

The scheme you described has a name in the wild: **geometric decay by rank**,
the same shape Kattis uses. Sort someone's solved problems by point value,
hardest first, and let the k-th one contribute ``points * decay**k``.

Why this and not a plain sum: a plain sum makes fifty easy problems worth more
than five hard ones, so the optimal strategy is grinding. Here the fiftieth
easiest solve is multiplied by ``0.95**49 ≈ 0.08``, so it adds almost nothing —
while a problem harder than anything you have solved lands at rank 0 and
contributes its full value.

Two properties worth knowing, both proven out in the tests:

* **Score never goes down.** Inserting a problem at rank k pushes everything
  below it down one rank, shrinking those terms — but the new term always more
  than covers that loss, for any decay in (0, 1).
* **Ceiling.** With decay d, repeating a problem worth p forever converges to
  ``p / (1 - d)`` — 20x at d = 0.95. So grinding one difficulty tier has a hard
  limit, and the only way past it is a harder problem.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from . import db

#: Contribution multiplier per rank. Lower punishes grinding harder; higher
#: makes breadth count for more. 0.95 gives a 20x ceiling per difficulty tier.
DECAY = float(os.environ.get("STROJ_SCORE_DECAY", "0.95"))


def weighted_score(points: list[int], decay: float = DECAY) -> float:
    """Score for someone whose solved problems are worth `points` each."""
    return sum(
        value * (decay ** rank)
        for rank, value in enumerate(sorted(points, reverse=True))
    )


def marginal_gain(points: list[int], new_value: int, decay: float = DECAY) -> float:
    """What solving one more problem worth `new_value` would add.

    Shown on a problem page so the incentive is legible rather than folklore.
    """
    return weighted_score(points + [new_value], decay) - weighted_score(points, decay)


@dataclass
class Standing:
    user_id: int
    username: str
    role: str
    score: float
    solved: int
    hardest: int

    def as_dict(self, rank: int) -> dict:
        return {
            "rank": rank,
            "user_id": self.user_id,
            "username": self.username,
            "role": self.role,
            "score": round(self.score, 1),
            "solved": self.solved,
            "hardest": self.hardest,
        }


#: Below this, a partial result is treated as noise rather than an achievement.
#: Without a floor, one accidentally-passing sample would put a problem on
#: someone's profile and nudge every other solve down a rank.
MIN_EARNED_PERCENT = 1

#: What it takes to have *solved* a problem, as opposed to scored on it.
#:
#: Partial credit earns its share of the points, so it belongs in the score and
#: on the profile list. It is not a solve, though: the "Solved" count answers
#: "how many problems did you finish", and letting a 5% scrape answer it the
#: same way a completed problem does makes the column meaningless.
FULL_SOLVE_PERCENT = 100

#: Hidden problems are worth nothing until they are published.
#:
#: A problem is hidden while it is being written and while it is a contest's
#: unreleased problem set, and in both states its solves are not achievements:
#: the setter's own calibration runs, and contestants' work on a paper nobody
#: else can see yet. Counting either would put points on the board that no one
#: else could go and earn, and list a problem on a profile that 404s for every
#: visitor. Publishing the problem grants the points to everyone who solved it,
#: all at once, which is the right moment for them to appear.
#:
#: Contest standings are computed in `contest.py` from the contest's own
#: problem set and never come through here, so a live scoreboard is unaffected.
PUBLISHED_ONLY = "p.visible = 1"


@dataclass
class _Solves:
    username: str
    role: str
    #: Earned value of each problem this user has scored on.
    points: list[int]
    #: How many of those they solved outright.
    full: int = 0


def _solved_points_by_user() -> dict[int, _Solves]:
    """Every user's earned value per problem, keyed by user id.

    A problem counts once however many times it is submitted, at the *best*
    result they ever achieved on it — so a later worse attempt never costs
    someone points, and partial credit counts for its share.
    """
    rows = db.query(
        "SELECT u.id AS user_id, u.username, u.role, p.points,"
        "       MAX(s.earned_percent) AS best"
        "  FROM submissions s"
        "  JOIN users u    ON u.id = s.user_id"
        "  JOIN problems p ON p.id = s.problem_id"
        f" WHERE s.earned_percent >= ? AND {PUBLISHED_ONLY}"
        " GROUP BY u.id, p.id",
        (MIN_EARNED_PERCENT,),
    )
    out: dict[int, _Solves] = {}
    for row in rows:
        entry = out.setdefault(
            row["user_id"], _Solves(row["username"], row["role"], [])
        )
        entry.points.append(round(row["points"] * row["best"] / 100))
        if row["best"] >= FULL_SOLVE_PERCENT:
            entry.full += 1
    return out


def leaderboard(limit: int | None = None) -> list[dict]:
    """Everyone who has solved something, best first."""
    standings = [
        Standing(
            user_id=user_id,
            username=entry.username,
            role=entry.role,
            score=weighted_score(entry.points),
            solved=entry.full,
            hardest=max(entry.points) if entry.points else 0,
        )
        for user_id, entry in _solved_points_by_user().items()
    ]
    standings.sort(key=lambda s: (-s.score, -s.hardest, s.username))

    result = []
    rank = 0
    previous = None
    for position, standing in enumerate(standings, start=1):
        key = round(standing.score, 6)
        if key != previous:
            rank = position
            previous = key
        result.append(standing.as_dict(rank))
        if limit is not None and len(result) >= limit:
            break
    return result


def solved_problems(user_id: int) -> list[dict]:
    """Problems this user has solved, hardest first, with each one's
    contribution at its actual rank — so a profile can show *why* the total is
    what it is."""
    rows = db.query(
        "SELECT p.slug, p.title, p.points, MAX(s.earned_percent) AS best,"
        "       MIN(s.created_at) AS first_solved"
        "  FROM submissions s"
        "  JOIN problems p ON p.id = s.problem_id"
        f" WHERE s.user_id = ? AND s.earned_percent >= ? AND {PUBLISHED_ONLY}"
        " GROUP BY p.id",
        (user_id, MIN_EARNED_PERCENT),
    )
    # Rank by what each one is actually worth to this user, not by the problem's
    # headline value — a 40% result on a 500-point problem sits where its 200
    # earned points put it.
    ordered = sorted(
        rows, key=lambda r: (-round(r["points"] * r["best"] / 100), r["slug"])
    )
    return [
        {
            "slug": row["slug"],
            "title": row["title"],
            "points": row["points"],
            "earned_percent": row["best"],
            "earned": round(row["points"] * row["best"] / 100),
            "first_solved": row["first_solved"],
            "weight": round(DECAY ** rank, 4),
            "contribution": round(
                round(row["points"] * row["best"] / 100) * (DECAY ** rank), 1
            ),
        }
        for rank, row in enumerate(ordered)
    ]


def full_solve_count(solved: list[dict]) -> int:
    """How many rows of `solved_problems` are full solves rather than partials.

    Takes the list a caller already has so a profile does not re-run the query
    just to count it, and so the leaderboard's "Solved" column and the profile's
    "Solved" stat can never disagree about what a solve is.
    """
    return sum(1 for row in solved if row["earned_percent"] >= FULL_SOLVE_PERCENT)


def user_score(user_id: int) -> float:
    rows = db.query(
        "SELECT p.points, MAX(s.earned_percent) AS best FROM submissions s"
        "  JOIN problems p ON p.id = s.problem_id"
        f" WHERE s.user_id = ? AND s.earned_percent >= ? AND {PUBLISHED_ONLY}"
        " GROUP BY p.id",
        (user_id, MIN_EARNED_PERCENT),
    )
    return weighted_score([round(r["points"] * r["best"] / 100) for r in rows])
