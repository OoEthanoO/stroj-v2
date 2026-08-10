"""Submitting code and reading back verdicts."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .. import __version__, config, contest as contest_mod, db
from ..ratelimit import RateLimiter
from ..judge import cancel, languages, worker
from ..judge.runner import AB, JUDGING, PENDING, VERDICT_NAMES, validate_source
from ..judge.sandbox import (
    isolation_mode,
    privilege_drop_target,
    protection_summary,
    sandbox_available,
)
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

# The in-flight cap alone does not stop a fast loop of tiny submissions that
# each judge in milliseconds; this bounds sustained volume per account.
_submit_limiter = RateLimiter(config.SUBMIT_LIMIT, config.SUBMIT_WINDOW_S)

#: When this process came up — distinguishes "redeployed" from "restarted".
STARTED_AT = db.utcnow()

_JOINED = """
SELECT s.*, u.username AS username, u.role AS user_role,
       p.slug AS problem_slug, p.title AS problem_title,
       p.visible AS problem_visible,
       c.slug AS contest_slug
  FROM submissions s
  JOIN users u    ON u.id = s.user_id
  JOIN problems p ON p.id = s.problem_id
  LEFT JOIN contests c ON c.id = s.contest_id
"""

#: The same rule `list_submissions` applies in SQL, for one already-loaded row.
#:
#: The list has to express this as a WHERE clause because it paginates; reading
#: one submission by id does not. Keeping the two in sync matters more than the
#: duplication costs: fetching by id used to bypass the list's filters
#: completely, which handed out live contest results the frozen scoreboard was
#: busy withholding. `TestBothSubmissionRoutesAgree` pins them together.
def may_read_submission(row: sqlite3.Row, user: sqlite3.Row | None) -> bool:
    if is_admin(user) or (user is not None and user["id"] == row["user_id"]):
        return True
    # A practice run on an unpublished problem is the setter's own working.
    if not row["problem_visible"] and row["contest_id"] is None:
        return False
    if row["contest_id"] is not None:
        contest_row = db.one(
            "SELECT * FROM contests WHERE id = ?", (row["contest_id"],)
        )
        if contest_row is not None and contest_mod.is_running(contest_row):
            return False
    return True


class SubmitBody(BaseModel):
    problem: str
    language: str
    source: str = Field(max_length=4 * 1024 * 1024)
    contest: str | None = None


@router.post("/submissions")
def submit(body: SubmitBody, request: Request):
    user = require_user(request)

    wait = _submit_limiter.check(str(user["id"]))
    if wait > 0:
        raise HTTPException(
            status_code=429,
            detail=f"You are submitting too quickly. Try again in {int(wait) + 1}s.",
            headers={"Retry-After": str(int(wait) + 1)},
        )

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
    _submit_limiter.hit(str(user["id"]))
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
        # A live contest's results stay between the contestant and the judge.
        # Listing them here hands out precisely what a frozen scoreboard is
        # withholding, and the list is one click from the contest page.
        live = contest_mod.live_ids()
        if live:
            marks = ", ".join("?" * len(live))
            where.append(
                f"(s.contest_id IS NULL OR s.contest_id NOT IN ({marks})"
                f" OR s.user_id = ?)"
            )
            params.extend(live)
            params.append(user["id"] if user else -1)

    sql = _JOINED
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY s.id DESC LIMIT ?"
    params.append(limit)

    rows = db.query(sql, tuple(params))
    return {"submissions": [submission_public(r, user) for r in rows]}


@router.get("/problems/{slug}/ranking")
def problem_ranking(
    slug: str,
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
):
    """Every judged submission for a problem, best first.

    Best means: most of the problem earned, then fastest, then smallest, then
    earliest — correctness before speed, and an earlier submission wins a tie
    it drew with a later one.

    Submissions still in the queue are left out; they have no result to rank.
    So are ones made inside a contest that is still running, because listing
    them here would hand out exactly what a frozen scoreboard is withholding.
    """
    problem = get_problem(slug, current_user(request))
    user = current_user(request)

    # Mirrors `contest._earned` and the leaderboard: submissions judged before
    # `earned_percent` existed still carry a score, and ranking them last
    # because of a schema change would be wrong.
    earned = (
        "CASE WHEN s.earned_percent > 0 THEN s.earned_percent"
        "     WHEN s.max_score > 0"
        "     THEN CAST(ROUND(100.0 * s.score / s.max_score) AS INTEGER)"
        "     ELSE 0 END"
    )

    where = ["s.problem_id = ?", "s.verdict NOT IN (?, ?, ?)"]
    params: list = [problem["id"], PENDING, JUDGING, AB]

    if not is_admin(user):
        # A live contest's submissions stay out until it finishes. Your own are
        # always yours to see.
        live = contest_mod.live_ids()
        if live:
            marks = ", ".join("?" * len(live))
            where.append(
                f"(s.contest_id IS NULL OR s.contest_id NOT IN ({marks})"
                f" OR s.user_id = ?)"
            )
            params.extend(live)
            params.append(user["id"] if user else -1)

    sql = (
        f"{_JOINED} WHERE " + " AND ".join(where)
        + f" ORDER BY {earned} DESC, s.time_ms ASC, s.memory_kb ASC, s.id ASC"
        " LIMIT ?"
    )
    params.append(limit)

    rows = db.query(sql, tuple(params))
    ranked = []
    for position, row in enumerate(rows, start=1):
        entry = submission_public(row, user)
        entry["rank"] = position
        entry["earned_percent"] = (
            row["earned_percent"] or
            (round(100 * row["score"] / row["max_score"]) if row["max_score"] else 0)
        )
        ranked.append(entry)
    return {
        "problem": {"slug": problem["slug"], "title": problem["title"],
                    "points": problem["points"]},
        "submissions": ranked,
    }


@router.post("/submissions/{submission_id}/abort")
def abort_submission(submission_id: int, request: Request):
    """Stop a submission that is queued or already running.

    Yours to cancel, or anyone's if you are an admin — a runaway submission
    holds a judge worker, so an organiser has to be able to clear it during a
    contest without waiting out its time limit on every remaining test.
    """
    user = require_user(request)
    row = db.one("SELECT id, user_id, verdict FROM submissions WHERE id = ?",
                 (submission_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="No such submission.")
    if row["user_id"] != user["id"] and not is_admin(user):
        raise HTTPException(status_code=403, detail="That is not your submission.")
    if row["verdict"] not in (PENDING, JUDGING):
        raise HTTPException(
            status_code=409,
            detail=f"That submission has already finished ({row['verdict']}).",
        )

    # Ask first, then try to settle it in the queue. Doing it in this order
    # closes the race with a worker claiming the row in between: either the
    # UPDATE wins and no worker ever starts it, or the worker started and picks
    # up the request that is already waiting for it.
    cancel.request(submission_id)
    claimed_first = db.execute(
        "UPDATE submissions SET verdict = ?, message = 'Cancelled.', judged_at = ?"
        " WHERE id = ? AND verdict = ?",
        (AB, db.utcnow(), submission_id, PENDING),
    ).rowcount
    if claimed_first:
        # It never started, so nothing is watching the event.
        cancel.release(submission_id)
        return {"id": submission_id, "verdict": AB, "stopped": "queued"}
    return {"id": submission_id, "verdict": JUDGING, "stopped": "running"}


@router.get("/submissions/{submission_id}")
def get_submission(submission_id: int, request: Request):
    user = current_user(request)
    row = db.one(_JOINED + " WHERE s.id = ?", (submission_id,))
    if row is None or not may_read_submission(row, user):
        raise HTTPException(status_code=404, detail="No such submission.")

    data = submission_public(row, user, with_source=True)
    own = user is not None and user["id"] == row["user_id"]
    admin = is_admin(user)
    if own or admin:
        # How many tests the problem has, so a page watching a live judge can
        # show "4 of 18" rather than just a growing list of unknown length.
        data["test_count"] = db.one(
            "SELECT COUNT(*) AS n FROM testcases WHERE problem_id = ?",
            (row["problem_id"],),
        )["n"]
        # Which group each test belongs to lives on the problem, not on the
        # result, so join rather than duplicating it per submission.
        tests = db.query(
            "SELECT st.*, tc.subtask, tc.is_sample FROM submission_tests st"
            "  LEFT JOIN testcases tc"
            "    ON tc.problem_id = ? AND tc.idx = st.idx"
            " WHERE st.submission_id = ? ORDER BY st.idx",
            (row["problem_id"], submission_id),
        )
        data["tests"] = [
            {
                "idx": t["idx"],
                "verdict": t["verdict"],
                "verdict_name": VERDICT_NAMES.get(t["verdict"], t["verdict"]),
                "time_ms": t["time_ms"],
                "memory_kb": t["memory_kb"],
                "points": t["points"],
                # Same rule as the submission's own message: a per-test
                # detail names which hidden test broke and roughly how. Sample
                # tests are printed in the statement, so their diagnostics —
                # usually the program's own stderr — give nothing away.
                "message": t["message"] if (admin or t["is_sample"]) else "",
                # NULL when the test data was replaced after this run, so the
                # row no longer matches a live test case.
                "subtask": t["subtask"] or 0,
                "is_sample": bool(t["is_sample"]),
            }
            for t in tests
        ]
        data["subtasks"] = [
            {"idx": s["idx"], "percent": s["percent"],
             "tests": s["n"]}
            for s in db.query(
                "SELECT ps.idx, ps.percent,"
                "       (SELECT COUNT(*) FROM testcases tc"
                "         WHERE tc.problem_id = ps.problem_id AND tc.subtask = ps.idx)"
                "       AS n"
                "  FROM problem_subtasks ps WHERE ps.problem_id = ? ORDER BY ps.idx",
                (row["problem_id"],),
            )
        ]
    return data


@router.get("/version")
def version():
    """What this judge is actually running.

    The frontend is deployed separately and can drift ahead of or behind the
    backend, so both halves report their commit and a check can compare them.
    """
    return {
        "commit": config.commit(),
        "short": config.commit()[:7],
        "version": __version__,
        "started_at": STARTED_AT,
    }


@router.get("/config")
def judge_config():
    # Report the isolation actually in force, not merely the one requested:
    # sandbox-exec is macOS-only, and the Linux fallback depends on kernel and
    # container capabilities, so both are probed rather than assumed.
    active = config.USE_SANDBOX and sandbox_available()
    return {
        "max_source_bytes": config.MAX_SOURCE_BYTES,
        "sandbox": active,
        # Overall posture — what the UI shows. `isolation` below is only the
        # in-process sandbox mechanism and is absent in a Linux container.
        "protection": protection_summary(),
        "isolation": isolation_mode() if config.USE_SANDBOX else "none",
        "sandbox_requested": config.USE_SANDBOX,
        # Whether submissions run under an account separate from the judge's.
        # Without it they share the judge's access to the database.
        "privilege_separation": privilege_drop_target() is not None,
        "workers": config.JUDGE_WORKERS,
        "registration": config.registration_mode(),
        "verdicts": VERDICT_NAMES,
    }
