"""Express authoring: one zip in, one paste back.

Setting a problem up by hand is a form, a test-data upload, three solutions
pasted into the editor one at a time, and then — once the calibration runs come
back — a second visit to the form to write the measured limits down. An express
package collapses the first half into a single file; the limits DSL collapses
the second into a single paste.

The package is a zip:

    problem.json    the metadata form, as JSON
    statement.md    the statement (markdown, `$…$` for maths)
    tests/          the test archive, laid out exactly as a normal upload
    solutions/      the intended solutions, one per language

Nothing here writes to the database: every function either returns a validated
plan or raises `ExpressError`, so the route can check the whole package before
it creates anything. A rejected upload is recoverable; a half-created problem
with no tests and three failed submissions against it is not.
"""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass, field

from .judge import checkers, languages

MANIFEST = "problem.json"
STATEMENT = "statement.md"
TESTS_DIR = "tests/"
SOLUTIONS_DIR = "solutions/"

#: The same bounds the problem form enforces. They live here because the DSL
#: has to reject a bad paste before it reaches the database, and duplicating
#: them as literals in two places is how the two drift apart.
TIME_MS_RANGE = (100, 60_000)
MEMORY_MB_RANGE = (16, 4096)
POINTS_RANGE = (1, 10_000)

MANIFEST_KEYS = {
    "slug", "title", "statement", "points", "types", "checker", "float_eps",
    "partial", "time_limit_ms", "memory_limit_mb", "author",
}


class ExpressError(Exception):
    """The package or the paste could not be used. The message goes to the
    author verbatim, so it says what to fix rather than what went wrong."""


@dataclass
class ExpressPackage:
    """A validated package, ready to be turned into a problem."""

    data: bytes
    bundle: zipfile.ZipFile
    slug: str
    title: str
    statement: str
    points: int
    types: list[str]
    checker: str
    float_eps: float
    partial: bool
    time_limit_ms: int
    memory_limit_mb: int
    author: str | None
    #: Zip entries under `solutions/` that a language claims, in name order.
    solutions: list[zipfile.ZipInfo] = field(default_factory=list)


# ------------------------------------------------------------------ package


def _entries(bundle: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """Real files, with the noise a desktop zipper leaves behind removed."""
    out = []
    for info in bundle.infolist():
        name = info.filename
        if info.is_dir() or "__MACOSX" in name or name.split("/")[-1].startswith("."):
            continue
        out.append(info)
    return out


def _read_manifest(bundle: zipfile.ZipFile) -> dict:
    try:
        raw = bundle.read(MANIFEST)
    except KeyError:
        raise ExpressError(
            f"The package has no {MANIFEST} at its root. It holds the fields the"
            " problem form would ask for: slug, title, points, types, checker,"
            " partial."
        ) from None
    try:
        manifest = json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError as exc:
        raise ExpressError(f"{MANIFEST} is not valid JSON: {exc}") from None
    if not isinstance(manifest, dict):
        raise ExpressError(f"{MANIFEST} must be a JSON object.")
    return manifest


def _string(manifest: dict, key: str, *, required: bool = False) -> str:
    value = manifest.get(key)
    if value is None:
        if required:
            raise ExpressError(f"{MANIFEST} is missing {key!r}.")
        return ""
    if not isinstance(value, str) or not value.strip():
        raise ExpressError(f"{MANIFEST}: {key} must be a non-empty string.")
    return value.strip()


def _integer(manifest: dict, key: str, default: int, bounds: tuple[int, int]) -> int:
    value = manifest.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ExpressError(f"{MANIFEST}: {key} must be a whole number.")
    low, high = bounds
    if not low <= value <= high:
        raise ExpressError(f"{MANIFEST}: {key} must be between {low} and {high}.")
    return value


def _flag(manifest: dict, key: str) -> bool:
    value = manifest.get(key, False)
    if not isinstance(value, bool):
        raise ExpressError(f"{MANIFEST}: {key} must be true or false.")
    return value


def parse_package(data: bytes) -> ExpressPackage:
    """Validate an express zip and return what it describes.

    Raises `ExpressError` with something an author can act on. The zip stays
    open on the returned package: the caller still needs it for the solutions,
    and the test data is read straight out of `data` by `testdata.parse_zip`.
    """
    try:
        bundle = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ExpressError("That file is not a readable zip archive.") from None

    manifest = _read_manifest(bundle)
    if "visible" in manifest:
        # Not an oversight: an express problem is created hidden so that the
        # calibration submissions, and any mistake the package carries, happen
        # out of sight. Publishing is a separate, deliberate click.
        raise ExpressError(
            f"{MANIFEST}: remove `visible`. An express problem is always created"
            " hidden — show it from the admin page once the limits are set."
        )
    unknown = sorted(set(manifest) - MANIFEST_KEYS)
    if unknown:
        # Nearly always a typo — `type` for `types`, `time_limit` for
        # `time_limit_ms` — and silently ignoring it ships the default.
        raise ExpressError(
            f"{MANIFEST} has field(s) nothing reads: {', '.join(unknown)}. "
            f"Known fields: {', '.join(sorted(MANIFEST_KEYS))}."
        )

    slug = _string(manifest, "slug", required=True)
    title = _string(manifest, "title", required=True)

    statement = ""
    if STATEMENT in bundle.namelist():
        statement = bundle.read(STATEMENT).decode("utf-8", "replace")
    elif manifest.get("statement"):
        statement = _string(manifest, "statement")
    if not statement.strip():
        raise ExpressError(
            f"The package has no {STATEMENT}, and {MANIFEST} sets no statement."
        )

    checker = manifest.get("checker", "token")
    if checker not in checkers.CHECKERS:
        raise ExpressError(
            f"{MANIFEST}: unknown checker {checker!r}."
            f" Use one of: {', '.join(checkers.CHECKERS)}."
        )
    if checker == "float" and "float_eps" not in manifest:
        # The default would be 1e-6, which is right often enough that a problem
        # needing 1e-9 would pass its own calibration run and fail solvers.
        raise ExpressError(
            f"{MANIFEST}: the float checker needs an explicit float_eps."
        )
    float_eps = manifest.get("float_eps", 1e-6)
    if isinstance(float_eps, bool) or not isinstance(float_eps, (int, float)):
        raise ExpressError(f"{MANIFEST}: float_eps must be a number.")
    if not 0 < float(float_eps) <= 1:
        raise ExpressError(f"{MANIFEST}: float_eps must be greater than 0 and at most 1.")

    types = manifest.get("types", [])
    if not isinstance(types, list) or any(not isinstance(t, str) for t in types):
        raise ExpressError(f"{MANIFEST}: types must be a list of strings.")

    author = manifest.get("author")
    if author is not None and not isinstance(author, str):
        raise ExpressError(f"{MANIFEST}: author must be a username.")

    entries = _entries(bundle)
    if not any(e.filename.startswith(TESTS_DIR) for e in entries):
        raise ExpressError(
            f"The package has no {TESTS_DIR} directory. Put the test archive's"
            " contents there — samples and subtask directories included."
        )

    solutions = sorted(
        (e for e in entries if e.filename.startswith(SOLUTIONS_DIR)),
        key=lambda e: e.filename,
    )
    if not solutions:
        raise ExpressError(
            f"The package has no {SOLUTIONS_DIR} directory. Express creation"
            " submits the intended solutions to measure the limits, so it needs"
            " at least one."
        )

    return ExpressPackage(
        data=data,
        bundle=bundle,
        slug=slug,
        title=title,
        statement=statement,
        points=_integer(manifest, "points", 100, POINTS_RANGE),
        types=types,
        checker=checker,
        float_eps=float(float_eps),
        partial=_flag(manifest, "partial"),
        time_limit_ms=_integer(manifest, "time_limit_ms", 1000, TIME_MS_RANGE),
        memory_limit_mb=_integer(manifest, "memory_limit_mb", 256, MEMORY_MB_RANGE),
        author=author,
        solutions=solutions,
    )


# ---------------------------------------------------------------- limits DSL


@dataclass
class LimitPlan:
    """What a pasted limits block asks for."""

    slug: str
    #: `(time_ms, memory_mb)` for the problem itself, or None to leave it alone.
    base: tuple[int, int] | None
    #: language id -> `(time_ms, memory_mb)`, or None to clear the override.
    languages: dict[str, tuple[int, int] | None]


def _time_ms(token: str, line: str) -> int:
    text = token.lower().rstrip()
    scale = 1
    if text.endswith("ms"):
        text = text[:-2]
    elif text.endswith("s"):
        text, scale = text[:-1], 1000
    try:
        value = int(round(float(text) * scale))
    except ValueError:
        raise ExpressError(f"{line!r}: {token!r} is not a time.") from None
    low, high = TIME_MS_RANGE
    if not low <= value <= high:
        raise ExpressError(f"{line!r}: time must be between {low} and {high} ms.")
    return value


def _memory_mb(token: str, line: str) -> int:
    text = token.lower().rstrip()
    scale = 1
    for suffix, factor in (("gib", 1024), ("gb", 1024), ("g", 1024),
                           ("mib", 1), ("mb", 1), ("m", 1)):
        if text.endswith(suffix):
            text, scale = text[: -len(suffix)], factor
            break
    try:
        value = int(round(float(text) * scale))
    except ValueError:
        raise ExpressError(f"{line!r}: {token!r} is not a memory size.") from None
    low, high = MEMORY_MB_RANGE
    if not low <= value <= high:
        raise ExpressError(f"{line!r}: memory must be between {low} and {high} MiB.")
    return value


def parse_limits(text: str) -> LimitPlan:
    """Read a pasted limits block.

        limits repair-shop
        base 1500ms 256MiB
        cpp 1500ms 256MiB
        python3 4000ms 320MiB
        java clear

    The header names the problem on purpose: these numbers arrive by paste,
    often minutes after the run they came from, and applying them to whichever
    problem happens to be open is the mistake worth making impossible.
    """
    lines = [line.strip() for line in text.strip().splitlines()]
    lines = [line for line in lines if line and not line.startswith("#")]
    if not lines:
        raise ExpressError("Nothing to apply.")

    header = lines[0].split()
    if len(header) != 2 or header[0].lower() not in ("limits", "express-limits"):
        raise ExpressError(
            "The first line must name the problem, like `limits my-problem`."
        )
    slug = header[1]

    base: tuple[int, int] | None = None
    per_language: dict[str, tuple[int, int] | None] = {}
    for line in lines[1:]:
        parts = line.split()
        target = parts[0].lower()
        if target != "base" and target not in languages.LANGUAGES:
            raise ExpressError(
                f"{line!r}: {parts[0]!r} is not a language."
                f" Use `base` or one of: {', '.join(sorted(languages.LANGUAGES))}."
            )
        if len(parts) == 2 and parts[1].lower() == "clear":
            if target == "base":
                raise ExpressError("The base limit cannot be cleared, only set.")
            per_language[target] = None
            continue
        if len(parts) != 3:
            raise ExpressError(
                f"{line!r}: expected `<language> <time> <memory>`, e.g."
                " `python3 4000ms 320MiB`."
            )
        pair = (_time_ms(parts[1], line), _memory_mb(parts[2], line))
        if target == "base":
            base = pair
        else:
            per_language[target] = pair

    if base is None and not per_language:
        raise ExpressError("That block sets no limits.")
    return LimitPlan(slug=slug, base=base, languages=per_language)


# -------------------------------------------------------------- the report


def _memory(kb: int) -> str:
    return f"{kb / 1024:.1f} MiB" if kb else "—"


def _row(cells: list[str], widths: list[int]) -> str:
    return "  ".join(cell.ljust(width) for cell, width in zip(cells, widths)).rstrip()


def format_report(summary: dict, rows: list[dict]) -> str:
    """The calibration result, as text meant to be copied out and pasted back.

    Every number needed to choose a limit is here — what each language used,
    what it is currently allowed, and how the problem is priced — because the
    person reading it is somewhere else, without the judge in front of them.
    """
    head = [
        "stroj express calibration",
        f"problem: {summary['slug']} — {summary['title']}",
        f"points: {summary['points']}"
        + (" (partial scoring)" if summary.get("partial") else ""),
        f"tests: {summary['tests']} ({summary['samples']} sample"
        f"{'' if summary['samples'] == 1 else 's'}"
        + (f", {summary['subtasks']} subtasks" if summary.get("subtasks") else "")
        + ")",
        f"base limits: {summary['time_limit_ms']} ms / "
        f"{summary['memory_limit_mb']} MiB",
        "",
    ]

    header = ["language", "verdict", "score", "time", "memory", "allowed now"]
    body = []
    for row in rows:
        allowed = (
            f"{row['limit_time_ms']} ms / {row['limit_memory_mb']} MiB"
            f" ({'measured' if row['measured'] else 'derived'})"
        )
        body.append([
            row["language"],
            row["verdict"],
            f"{row['score_percent']}%",
            f"{row['time_ms']} ms" if row["judged"] else "—",
            _memory(row["memory_kb"]) if row["judged"] else "—",
            allowed,
        ])

    widths = [max(len(r[i]) for r in [header, *body]) for i in range(len(header))]
    table = [_row(header, widths), *(_row(r, widths) for r in body)]
    return "\n".join([*head, *table, ""])
