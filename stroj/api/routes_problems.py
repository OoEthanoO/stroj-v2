"""Public problem browsing."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request

from .. import db
from ..judge import languages
from .deps import current_user, get_problem, is_admin, problem_summary

router = APIRouter(prefix="/api", tags=["problems"])


@router.get("/languages")
def list_languages():
    return {"languages": languages.catalog(), "default": languages.DEFAULT_LANGUAGE}


@router.get("/problems")
def list_problems(request: Request):
    user = current_user(request)
    if is_admin(user):
        rows = db.query("SELECT * FROM problems ORDER BY id")
    else:
        rows = db.query("SELECT * FROM problems WHERE visible = 1 ORDER BY id")
    return {"problems": [problem_summary(p, user) for p in rows]}


@router.get("/problems/{slug}")
def get_problem_detail(slug: str, request: Request):
    user = current_user(request)
    problem = get_problem(slug, user)
    data = problem_summary(problem, user)
    data["statement"] = problem["statement"]
    data["float_eps"] = problem["float_eps"]

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
    data["limits"] = {
        lang.id: {
            "time_limit_ms": lang.effective_time_limit_ms(problem["time_limit_ms"]),
            "memory_limit_mb": lang.effective_memory_limit_mb(
                problem["memory_limit_mb"]
            ),
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
