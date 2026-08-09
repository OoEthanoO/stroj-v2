"""Sample content so a fresh install has something to judge.

Three problems chosen to exercise different parts of the judge: a trivial one, a
big-input one where the naive solution should time out, and one that needs the
floating-point checker. Plus a contest containing all three.
"""

from __future__ import annotations

import random
from datetime import timedelta

from . import db, testdata

A_PLUS_B = {
    "slug": "a-plus-b",
    "title": "A + B",
    "points": 25,
    "time_limit_ms": 1000,
    "memory_limit_mb": 256,
    "checker": "token",
    "types": ["implementation"],
    "statement": """\
Read two integers and print their sum.

## Input

A single line with two integers `a` and `b` (`-10^9 <= a, b <= 10^9`).

## Output

One line: the value of `a + b`.
""",
}

MAX_SUBARRAY = {
    "slug": "max-subarray",
    "title": "Maximum Subarray Sum",
    "points": 300,
    "time_limit_ms": 2000,
    "memory_limit_mb": 256,
    "checker": "token",
    "partial": 1,
    "types": ["dp", "arrays"],
    "statement": """\
Given an array, find the largest sum of any non-empty contiguous subarray.

## Input

The first line contains `n` (`1 <= n <= 200000`).
The second line contains `n` integers `a_1 … a_n` (`-10^9 <= a_i <= 10^9`).

## Output

One line: the maximum subarray sum.

## Note

An `O(n^2)` scan will not finish inside the time limit on the larger tests.
""",
}

CIRCLE = {
    "slug": "circle-area",
    "title": "Circle Area",
    "points": 50,
    "time_limit_ms": 1000,
    "memory_limit_mb": 256,
    "checker": "float",
    "float_eps": 1e-6,
    "types": ["math", "geometry"],
    "statement": """\
Print the area of a circle of radius `r`.

## Input

One line with a real number `r` (`0 < r <= 10^4`).

## Output

The area, `pi * r^2`. Answers within `1e-6` relative or absolute error are accepted.
""",
}


def _kadane(values: list[int]) -> int:
    best = current = values[0]
    for value in values[1:]:
        current = max(value, current + value)
        best = max(best, current)
    return best


def _a_plus_b_tests() -> list[dict]:
    pairs = [(2, 3), (-1, 1), (1_000_000_000, 1_000_000_000), (0, 0), (-7, -13)]
    tests = []
    for i, (a, b) in enumerate(pairs):
        tests.append(
            {
                "input": f"{a} {b}\n",
                "output": f"{a + b}\n",
                "is_sample": i < 2,
                "points": 1,
            }
        )
    return tests


def _max_subarray_tests(rng: random.Random) -> list[dict]:
    tests = [
        {"input": "5\n-2 1 -3 4 -1\n", "output": "4\n", "is_sample": 1, "points": 1},
        {"input": "3\n-5 -2 -9\n", "output": "-2\n", "is_sample": 1, "points": 1},
    ]
    for size, points in ((1000, 2), (50_000, 3), (200_000, 5)):
        values = [rng.randint(-10**6, 10**6) for _ in range(size)]
        tests.append(
            {
                "input": f"{size}\n{' '.join(map(str, values))}\n",
                "output": f"{_kadane(values)}\n",
                "is_sample": 0,
                "points": points,
            }
        )
    return tests


def _circle_tests() -> list[dict]:
    import math

    radii = ["1", "2.5", "0.0001", "10000", "3.14159"]
    return [
        {
            "input": f"{r}\n",
            "output": f"{math.pi * float(r) ** 2:.9f}\n",
            "is_sample": i < 2,
            "points": 1,
        }
        for i, r in enumerate(radii)
    ]


def _insert_problem(spec: dict) -> int:
    existing = db.one("SELECT id FROM problems WHERE slug = ?", (spec["slug"],))
    if existing is not None:
        return existing["id"]
    problem_id = db.insert(
        "INSERT INTO problems (slug, title, statement, time_limit_ms, memory_limit_mb,"
        " checker, float_eps, partial, visible, points, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
        (
            spec["slug"],
            spec["title"],
            spec["statement"],
            spec["time_limit_ms"],
            spec["memory_limit_mb"],
            spec["checker"],
            spec.get("float_eps", 1e-6),
            spec.get("partial", 0),
            spec.get("points", 100),
            db.utcnow(),
        ),
    )
    for name in spec.get("types", []):
        db.execute(
            "INSERT INTO problem_types (problem_id, type) VALUES (?, ?)",
            (problem_id, name),
        )
    return problem_id


def seed(with_contest: bool = True) -> dict:
    """Insert the sample problems and contest. Safe to run more than once."""
    rng = random.Random(20260807)
    created = []

    for spec, tests in (
        (A_PLUS_B, _a_plus_b_tests()),
        (MAX_SUBARRAY, _max_subarray_tests(rng)),
        (CIRCLE, _circle_tests()),
    ):
        problem_id = _insert_problem(spec)
        if not db.one(
            "SELECT 1 FROM testcases WHERE problem_id = ? LIMIT 1", (problem_id,)
        ):
            testdata.replace_testcases(problem_id, spec["slug"], tests)
        created.append(spec["slug"])

    contest_slug = None
    if with_contest and not db.one("SELECT 1 FROM contests LIMIT 1"):
        contest_slug = "open-round-1"
        now = db.parse_time(db.utcnow())
        starts = now - timedelta(minutes=1)
        ends = now + timedelta(hours=3)

        def iso(value) -> str:
            return value.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

        contest_id = db.insert(
            "INSERT INTO contests (slug, title, description, starts_at, ends_at,"
            " scoring, penalty_minutes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                contest_slug,
                "stroj Open Round 1",
                "A three-problem warm-up. ICPC scoring, 20 minute penalty.",
                iso(starts),
                iso(ends),
                "icpc",
                20,
                db.utcnow(),
            ),
        )
        for label, slug in zip("ABC", created):
            problem = db.one("SELECT id FROM problems WHERE slug = ?", (slug,))
            db.execute(
                "INSERT OR IGNORE INTO contest_problems (contest_id, problem_id, label)"
                " VALUES (?, ?, ?)",
                (contest_id, problem["id"], label),
            )

    return {"problems": created, "contest": contest_slug}
