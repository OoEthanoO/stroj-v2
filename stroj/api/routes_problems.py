"""Public problem browsing."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request

from .. import db
from ..judge import languages
from ..judge.runner import ProblemSpec
# One loader, in the layer that owns a problem's judging inputs.
from ..judge.worker import load_limits as problem_limits
from .deps import (
    contest_appearances,
    current_user,
    get_problem,
    is_admin,
    problem_summary,
)

router = APIRouter(prefix="/api", tags=["problems"])


@router.get("/languages")
def list_languages():
    return {"languages": languages.catalog(), "default": languages.DEFAULT_LANGUAGE}


@router.get("/problems")
def list_problems(request: Request):
    user = current_user(request)
    admin = is_admin(user)
    if admin:
        rows = db.query("SELECT * FROM problems ORDER BY id")
    else:
        rows = db.query("SELECT * FROM problems WHERE visible = 1 ORDER BY id")
    problems = [problem_summary(p, user) for p in rows]
    if admin:
        # For the admin page, which reads this list: a problem with no test
        # data judges every submission as IE, and nothing else on the list
        # says which ones those are. One grouped count, not one per row.
        counts = {
            r["problem_id"]: r["n"]
            for r in db.query(
                "SELECT problem_id, COUNT(*) AS n FROM testcases GROUP BY problem_id"
            )
        }
        for row, data in zip(rows, problems):
            data["test_count"] = counts.get(row["id"], 0)
    return {"problems": problems}


@router.get("/problems/{slug}")
def get_problem_detail(slug: str, request: Request):
    user = current_user(request)
    problem = get_problem(slug, user)
    data = problem_summary(problem, user)
    data["statement"] = problem["statement"]
    data["float_eps"] = problem["float_eps"]
    # Which rounds this problem is in, and as which letter. Anything but
    # the one that is running right now.
    data["contests"] = contest_appearances(problem, user)

    samples = []
    for row in db.query(
        "SELECT idx, input_path, answer_path FROM testcases"
        " WHERE problem_id = ? AND is_sample = 1 ORDER BY idx",
        (problem["id"],),
    ):
        samples.append(
            {
                "idx": row["idx"],
                "input": _read_sample(row["input_path"]),
                "output": _read_sample(row["answer_path"]),
            }
        )
    data["samples"] = samples
    data["subtasks"] = [
        {
            "idx": r["idx"],
            "percent": r["percent"],
            "tests": db.one(
                "SELECT COUNT(*) AS n FROM testcases"
                " WHERE problem_id = ? AND subtask = ?",
                (problem["id"], r["idx"]),
            )["n"],
        }
        for r in db.query(
            "SELECT idx, percent FROM problem_subtasks WHERE problem_id = ?"
            " ORDER BY idx",
            (problem["id"],),
        )
    ]
    data["test_count"] = db.one(
        "SELECT COUNT(*) AS n FROM testcases WHERE problem_id = ?", (problem["id"],)
    )["n"]
    # What each language actually gets, override or fallback — this is the
    # number a solver is judged against, so it is the number to show them.
    spec = ProblemSpec.from_row(problem, problem_limits(problem["id"]))
    overrides = spec.limits
    data["limits"] = {
        lang.id: {
            "time_limit_ms": spec.limits_for(lang)[0],
            "memory_limit_mb": spec.limits_for(lang)[1],
            "measured": lang.id in overrides,
        }
        for lang in languages.LANGUAGES.values()
    }
    return data


def _read_sample(path: str, cap: int = 8 * 1024) -> str:
    try:
        with Path(path).open("rb") as fh:
            data = fh.read(cap + 1)
    except OSError:
        return ""
    text = data[:cap].decode("utf-8", "replace")
    return text + "\n…(truncated)" if len(data) > cap else text
