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


def _solved_points_by_user() -> dict[int, tuple[str, str, list[int]]]:
    """Every user's solved problems' point values, keyed by user id.

    One row per (user, problem) even if they solved it repeatedly — a problem
    must count once however many times it is submitted.
    """
    rows = db.query(
        "SELECT u.id AS user_id, u.username, u.role, p.points"
        "  FROM submissions s"
        "  JOIN users u    ON u.id = s.user_id"
        "  JOIN problems p ON p.id = s.problem_id"
        " WHERE s.verdict = 'AC'"
        " GROUP BY u.id, p.id"
    )
    out: dict[int, tuple[str, str, list[int]]] = {}
    for row in rows:
        entry = out.setdefault(row["user_id"], (row["username"], row["role"], []))
        entry[2].append(row["points"])
    return out


def leaderboard(limit: int | None = None) -> list[dict]:
    """Everyone who has solved something, best first."""
    standings = [
        Standing(
            user_id=user_id,
            username=username,
            role=role,
            score=weighted_score(points),
            solved=len(points),
            hardest=max(points) if points else 0,
        )
        for user_id, (username, role, points) in _solved_points_by_user().items()
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
        "SELECT p.slug, p.title, p.points, MIN(s.created_at) AS first_solved"
        "  FROM submissions s"
        "  JOIN problems p ON p.id = s.problem_id"
        " WHERE s.user_id = ? AND s.verdict = 'AC'"
        " GROUP BY p.id"
        " ORDER BY p.points DESC, p.slug",
        (user_id,),
    )
    return [
        {
            "slug": row["slug"],
            "title": row["title"],
            "points": row["points"],
            "first_solved": row["first_solved"],
            "weight": round(DECAY ** rank, 4),
            "contribution": round(row["points"] * (DECAY ** rank), 1),
        }
        for rank, row in enumerate(rows)
    ]


def user_score(user_id: int) -> float:
    rows = db.query(
        "SELECT p.points FROM submissions s"
        "  JOIN problems p ON p.id = s.problem_id"
        " WHERE s.user_id = ? AND s.verdict = 'AC'"
        " GROUP BY p.id",
        (user_id,),
    )
    return weighted_score([r["points"] for r in rows])
