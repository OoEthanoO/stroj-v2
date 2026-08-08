"""Submitting code and reading back verdicts."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .. import config, contest as contest_mod, db
from ..judge import languages, worker
from ..judge.runner import JUDGING, PENDING, VERDICT_NAMES, validate_source
from ..judge.sandbox import isolation_mode, sandbox_available
from .deps import (
    current_user,
    get_problem,
    is_admin,
    require_user,
    submission_public,
)

router = APIRouter(prefix="/api", tags=["submissions"])

#: Refuse a new submission while this many of the user's are still in flight.
MAX_IN_FLIGHT = 5

_JOINED = """
SELECT s.*, u.username AS username,
       p.slug AS problem_slug, p.title AS problem_title,
       c.slug AS contest_slug
  FROM submissions s
  JOIN users u    ON u.id = s.user_id
  JOIN problems p ON p.id = s.problem_id
  LEFT JOIN contests c ON c.id = s.contest_id
"""


class SubmitBody(BaseModel):
    problem: str
    language: str
    source: str = Field(max_length=4 * 1024 * 1024)
    contest: str | None = None


@router.post("/submissions")
def submit(body: SubmitBody, request: Request):
    user = require_user(request)
    problem = get_problem(body.problem, user)

    if body.language not in languages.LANGUAGES:
        raise HTTPException(status_code=400, detail="Unknown language.")
    if not languages.is_available(body.language):
        raise HTTPException(
            status_code=400,
            detail=f"{languages.get(body.language).name} is not installed on this judge.",
        )
    rejection = validate_source(body.language, body.source)
    if rejection:
        raise HTTPException(status_code=400, detail=rejection)

    contest_id = None
    if body.contest:
        contest_row = db.one(
            "SELECT * FROM contests WHERE slug = ?", (body.contest,)
        )
        if contest_row is None:
            raise HTTPException(status_code=404, detail="No such contest.")
        state = contest_mod.state_of(contest_row)
        if state != contest_mod.RUNNING and not is_admin(user):
            raise HTTPException(
                status_code=403,
                detail="That contest has not started yet."
                if state == contest_mod.BEFORE
                else "That contest is over.",
            )
        in_contest = db.one(
            "SELECT 1 FROM contest_problems WHERE contest_id = ? AND problem_id = ?",
            (contest_row["id"], problem["id"]),
        )
        if in_contest is None:
            raise HTTPException(
                status_code=400, detail="That problem is not in that contest."
            )
        contest_id = contest_row["id"]

    in_flight = db.one(
        "SELECT COUNT(*) AS n FROM submissions WHERE user_id = ? AND verdict IN (?, ?)",
        (user["id"], PENDING, JUDGING),
    )["n"]
    if in_flight >= MAX_IN_FLIGHT:
        raise HTTPException(
            status_code=429,
            detail="You already have submissions waiting to be judged.",
        )

    submission_id = db.insert(
        "INSERT INTO submissions (user_id, problem_id, contest_id, language, source,"
        " verdict, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            user["id"],
            problem["id"],
            contest_id,
            body.language,
            body.source,
            PENDING,
            db.utcnow(),
        ),
    )
    worker.notify()
    return {"id": submission_id, "verdict": PENDING}


@router.get("/submissions")
def list_submissions(
    request: Request,
    problem: str | None = None,
    contest: str | None = None,
    username: str | None = None,
    mine: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    before: int | None = Query(default=None, description="Return ids below this one."),
):
    user = current_user(request)
    where: list[str] = []
    params: list = []

    if mine:
        if user is None:
            raise HTTPException(status_code=401, detail="Sign in to do that.")
        where.append("s.user_id = ?")
        params.append(user["id"])
    if username:
        where.append("u.username = ?")
        params.append(username)
    if problem:
        where.append("p.slug = ?")
        params.append(problem)
    if contest:
        where.append("c.slug = ?")
        params.append(contest)
    if before:
        where.append("s.id < ?")
        params.append(before)
    if not is_admin(user):
        where.append("(p.visible = 1 OR s.contest_id IS NOT NULL)")

    sql = _JOINED
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY s.id DESC LIMIT ?"
    params.append(limit)

    rows = db.query(sql, tuple(params))
    return {"submissions": [submission_public(r, user) for r in rows]}


@router.get("/submissions/{submission_id}")
def get_submission(submission_id: int, request: Request):
    user = current_user(request)
    row = db.one(_JOINED + " WHERE s.id = ?", (submission_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="No such submission.")

    data = submission_public(row, user, with_source=True)
    own = user is not None and user["id"] == row["user_id"]
    if own or is_admin(user):
        tests = db.query(
            "SELECT * FROM submission_tests WHERE submission_id = ? ORDER BY idx",
            (submission_id,),
        )
        data["tests"] = [
            {
                "idx": t["idx"],
                "verdict": t["verdict"],
                "verdict_name": VERDICT_NAMES.get(t["verdict"], t["verdict"]),
                "time_ms": t["time_ms"],
                "memory_kb": t["memory_kb"],
                "points": t["points"],
                "message": t["message"],
            }
            for t in tests
        ]
    return data


@router.get("/config")
def judge_config():
    # Report the isolation actually in force, not merely the one requested:
    # sandbox-exec is macOS-only, and the Linux fallback depends on kernel and
    # container capabilities, so both are probed rather than assumed.
    active = config.USE_SANDBOX and sandbox_available()
    return {
        "max_source_bytes": config.MAX_SOURCE_BYTES,
        "sandbox": active,
        "isolation": isolation_mode() if config.USE_SANDBOX else "none",
        "sandbox_requested": config.USE_SANDBOX,
        "workers": config.JUDGE_WORKERS,
        "verdicts": VERDICT_NAMES,
    }
