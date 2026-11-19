"""Storing a problem's test data on disk and registering it in the database."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
import shutil
import zipfile
from pathlib import Path

import yaml

from . import config, db

INPUT_EXTENSIONS = {".in", ".input", ".dat"}
ANSWER_EXTENSIONS = {".out", ".ans", ".a", ".expected", ".sol"}

#: DMOJ's test data manifest. When an archive carries one with test cases, it
#: decides which files are tests and how they are grouped, not the file names.
DMOJ_MANIFEST_NAMES = ("init.yml", "init.yaml")


class TestDataError(Exception):
    """The uploaded test data could not be interpreted."""


def test_dir(slug: str) -> Path:
    return config.PROBLEM_DIR / slug / "tests"


def _natural_key(name: str) -> tuple:
    return tuple(
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", name)
    )


#: `subtask3` or `subtask3-25` — the optional suffix is that subtask's share of
#: the problem's points.
_SUBTASK_DIR = re.compile(r"^subtask[-_]?(\d+)(?:[-_](\d+))?$", re.IGNORECASE)


@dataclass
class ParsedTestData:
    tests: list[dict]
    #: subtask index -> percentage of the problem's points. Empty when the
    #: archive has no subtask directories.
    subtasks: dict[int, int]


def _subtask_of(path: Path) -> tuple[int, int | None]:
    """``(subtask index, declared percent)`` from the directory a file sits in."""
    for part in path.parts[:-1]:
        match = _SUBTASK_DIR.match(part)
        if match:
            declared = int(match.group(2)) if match.group(2) else None
            return int(match.group(1)), declared
    return 0, None


def _resolve_percentages(declared: dict[int, int | None]) -> dict[int, int]:
    """Turn declared subtask shares into percentages that add up to 100."""
    explicit = {k: v for k, v in declared.items() if v is not None}
    if explicit and len(explicit) != len(declared):
        missing = sorted(set(declared) - set(explicit))
        raise TestDataError(
            "Either every subtask directory names its percentage or none do; "
            f"missing on subtask {', '.join(map(str, missing))}."
        )

    if explicit:
        total = sum(explicit.values())
        if total != 100:
            raise TestDataError(
                f"Subtask percentages must add up to 100, but they add up to {total}."
            )
        return explicit

    # No percentages given: split evenly, with the remainder on the last
    # subtask so the total is exactly 100 rather than 99.
    order = sorted(declared)
    share = 100 // len(order)
    out = {idx: share for idx in order}
    out[order[-1]] += 100 - share * len(order)
    return out


def _dmoj_manifest(archive: zipfile.ZipFile, prefix: str) -> zipfile.ZipInfo | None:
    """The shallowest ``init.yml`` under ``prefix``, if the archive has one.

    Shallowest, because a zipped folder puts it one level down, and a stray
    copy deeper in should not win over it.
    """
    found = [
        info for info in archive.infolist()
        if not info.is_dir()
        and info.filename.startswith(prefix)
        and "__MACOSX" not in info.filename
        and Path(info.filename).name.lower() in DMOJ_MANIFEST_NAMES
    ]
    return min(found, key=lambda info: info.filename.count("/")) if found else None


def _is_sample_name(name: str) -> bool:
    return "sample" in Path(name).stem.lower()


def _point_shares(points: list[int]) -> list[int]:
    """Percentages proportional to ``points`` that total exactly 100.

    Largest remainder: 1 and 2 points become 33 and 67, not 33 and 66.
    """
    total = sum(points)
    if total <= 0:
        raise TestDataError("init.yml gives its tests no points, so nothing could be scored.")
    exact = [100 * value / total for value in points]
    shares = [int(value) for value in exact]
    by_remainder = sorted(range(len(exact)), key=lambda i: exact[i] - shares[i], reverse=True)
    for i in by_remainder[: 100 - sum(shares)]:
        shares[i] += 1
    return shares


def _parse_dmoj(archive: zipfile.ZipFile, manifest: zipfile.ZipInfo) -> ParsedTestData | None:
    """Tests and subtasks from a DMOJ ``init.yml``, or None if it lists no tests.

    Each ``batched`` group becomes a subtask worth its ``points`` as a share of
    the total. Unbatched cases all worth the same stay ungrouped, which scores
    the way DMOJ would; worth different amounts, each becomes a subtask of its
    own, since STROJ weights tests only through subtasks. A group worth no
    points whose files are all named ``sample`` becomes visible samples. Case
    files are named relative to init.yml, or within the zip its ``archive`` key
    names. Files init.yml does not mention are ignored.
    """
    try:
        spec = yaml.safe_load(archive.read(manifest).decode("utf-8", "replace"))
    except yaml.YAMLError as exc:
        raise TestDataError(f"init.yml is not valid YAML: {exc}") from None
    if not isinstance(spec, dict) or not spec.get("test_cases"):
        return None
    cases = spec["test_cases"]
    if not isinstance(cases, list):
        raise TestDataError("init.yml's test_cases must be a list.")

    base = manifest.filename.rpartition("/")[0]
    base = f"{base}/" if base else ""
    source, root = archive, base
    nested = spec.get("archive")
    if nested:
        try:
            inner = archive.read(base + str(nested).strip().removeprefix("./"))
            source, root = zipfile.ZipFile(io.BytesIO(inner)), ""
        except KeyError:
            raise TestDataError(f"init.yml names the archive {nested}, which is not in the zip.") from None
        except zipfile.BadZipFile:
            raise TestDataError(f"init.yml's archive {nested} is not a readable zip.") from None
    files = {info.filename: info for info in source.infolist() if not info.is_dir()}
    missing: list[str] = []

    def read(name) -> str:
        if not isinstance(name, str) or not name.strip():
            raise TestDataError("Every init.yml test case needs both an 'in' and an 'out' file.")
        info = files.get(root + name.strip().removeprefix("./"))
        if info is None:
            missing.append(name)
            return ""
        return source.read(info).decode("utf-8", "replace")

    # (batched, points, [(input name, input, answer)]), in init.yml's order.
    groups: list[tuple[bool, int, list[tuple[str, str, str]]]] = []
    for number, entry in enumerate(cases, start=1):
        if not isinstance(entry, dict):
            raise TestDataError(f"init.yml test case {number} is not a mapping.")
        points = entry.get("points", 1)
        if isinstance(points, bool) or not isinstance(points, int) or points < 0:
            raise TestDataError(
                f"init.yml test case {number} has points {points!r}; use a whole number."
            )
        batched = "batched" in entry
        members = entry["batched"] if batched else [entry]
        if not isinstance(members, list) or not members:
            raise TestDataError(f"init.yml batch {number} has no test cases.")
        tests = []
        for member in members:
            if not isinstance(member, dict):
                raise TestDataError(f"init.yml batch {number} lists something that is not a test case.")
            name = member.get("in")
            tests.append((name, read(name), read(member.get("out"))))
        groups.append((batched, points, tests))
    if missing:
        raise TestDataError("init.yml names files that are not in the zip: " + ", ".join(missing[:5]))

    def test(name: str, given: str, expected: str, subtask: int) -> dict:
        return {
            "input": given,
            "output": expected,
            "is_sample": _is_sample_name(name),
            "points": 0,
            "subtask": subtask,
        }

    unbatched_points = {points for batched, points, _ in groups if not batched}
    if not any(batched for batched, _, _ in groups) and len(unbatched_points) <= 1:
        tests = [test(*case, 0) for _, _, members in groups for case in members]
        # Samples first, as for any archive; otherwise init.yml's order (a stable sort).
        tests.sort(key=lambda t: 0 if t["is_sample"] else 1)
        return ParsedTestData(tests=tests, subtasks={})

    tests = []
    weighted: list[int] = []
    for _, points, members in groups:
        if points == 0 and all(_is_sample_name(name) for name, _, _ in members):
            tests.extend(test(*case, 0) for case in members)
            continue
        weighted.append(points)
        tests.extend(test(*case, len(weighted)) for case in members)
    subtasks = dict(enumerate(_point_shares(weighted), start=1))
    # Samples first, then subtask by subtask; init.yml's order within each.
    tests.sort(key=lambda t: (0 if t["subtask"] == 0 else 1, t["subtask"]))
    return ParsedTestData(tests=tests, subtasks=subtasks)


def parse_zip(data: bytes, prefix: str = "") -> ParsedTestData:
    """Pull ``name.in`` / ``name.out`` pairs out of a zip archive.

    Files are paired by stem and ordered naturally, so ``2.in`` sorts before
    ``10.in``. A directory called ``subtask1`` (optionally ``subtask1-30`` to
    declare its worth) groups its tests into a scoring subtask.

    ``prefix`` narrows the read to one directory and strips it, so an express
    package's ``tests/`` subtree parses exactly as a bare test archive would —
    subtask directories and sample names are then relative to it.

    An archive exported from DMOJ is read through its ``init.yml`` instead; see
    ``_parse_dmoj``.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise TestDataError("That file is not a readable zip archive.") from None

    manifest = _dmoj_manifest(archive, prefix)
    if manifest is not None:
        parsed = _parse_dmoj(archive, manifest)
        if parsed is not None:
            return parsed

    inputs: dict[str, str] = {}
    answers: dict[str, str] = {}
    subtask_of: dict[str, int] = {}
    declared: dict[int, int | None] = {}

    for info in archive.infolist():
        if info.is_dir():
            continue
        if prefix and not info.filename.startswith(prefix):
            continue
        path = Path(info.filename[len(prefix):])
        if path.name.startswith(".") or "__MACOSX" in info.filename:
            continue
        suffix = path.suffix.lower()
        # Two files in different subtasks may share a stem, so key on both.
        subtask, percent = _subtask_of(path)
        key = f"{subtask}/{path.stem}"
        if suffix in INPUT_EXTENSIONS:
            inputs[key] = archive.read(info).decode("utf-8", "replace")
            subtask_of[key] = subtask
            if subtask:
                declared.setdefault(subtask, percent)
                if percent is not None:
                    declared[subtask] = percent
        elif suffix in ANSWER_EXTENSIONS:
            answers[key] = archive.read(info).decode("utf-8", "replace")

    if not inputs:
        where = f"inside {prefix}" if prefix else "inside the zip"
        raise TestDataError(
            f"No input files found. Expected pairs like 1.in / 1.out {where}."
        )
    missing = sorted(set(inputs) - set(answers), key=_natural_key)
    if missing:
        raise TestDataError(
            "These inputs have no matching answer file: "
            + ", ".join(m.split("/", 1)[1] for m in missing[:5])
        )

    is_sample = {key: "sample" in key.split("/", 1)[1].lower() for key in inputs}

    # A half-grouped archive is almost always a mistake — an ungrouped test can
    # never be worth anything, so it would silently go unscored.
    if declared:
        stray = sorted(
            key for key in inputs if not subtask_of[key] and not is_sample[key]
        )
        if stray:
            raise TestDataError(
                "This archive uses subtasks, so every non-sample test must live "
                "in a subtask directory. These do not: "
                + ", ".join(s.split("/", 1)[1] for s in stray[:5])
            )

    subtasks = _resolve_percentages(declared) if declared else {}

    def ordering(key: str) -> tuple:
        # Samples first, then subtask by subtask. Judging stops at the first
        # failure on a non-partial problem, and only samples show diagnostics —
        # so samples running last would mean solvers rarely see them. Keeping
        # each subtask contiguous also makes a partial result readable.
        return (0 if is_sample[key] else 1, subtask_of[key], _natural_key(key))

    return ParsedTestData(
        tests=[
            {
                "input": inputs[key],
                "output": answers[key],
                "is_sample": is_sample[key],
                "points": 0,
                "subtask": subtask_of[key],
            }
            for key in sorted(inputs, key=ordering)
        ],
        subtasks=subtasks,
    )


def replace_testcases(
    problem_id: int,
    slug: str,
    tests: list[dict],
    subtasks: dict[int, int] | None = None,
) -> int:
    """Write ``tests`` to disk and make them the problem's complete test set."""
    if not tests:
        raise TestDataError("A problem needs at least one test case.")

    directory = test_dir(slug)
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True, exist_ok=True)

    rows = []
    for idx, test in enumerate(tests, start=1):
        input_path = directory / f"{idx:03d}.in"
        answer_path = directory / f"{idx:03d}.ans"
        input_path.write_text(str(test.get("input", "")), encoding="utf-8")
        answer_path.write_text(str(test.get("output", "")), encoding="utf-8")
        rows.append(
            (
                problem_id,
                idx,
                str(input_path),
                str(answer_path),
                1 if test.get("is_sample") else 0,
                int(test.get("points") or 0),
                int(test.get("subtask") or 0),
            )
        )

    with db.transaction() as conn:
        conn.execute("DELETE FROM testcases WHERE problem_id = ?", (problem_id,))
        conn.executemany(
            "INSERT INTO testcases"
            " (problem_id, idx, input_path, answer_path, is_sample, points, subtask)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.execute(
            "DELETE FROM problem_subtasks WHERE problem_id = ?", (problem_id,)
        )
        if subtasks:
            conn.executemany(
                "INSERT INTO problem_subtasks (problem_id, idx, percent)"
                " VALUES (?, ?, ?)",
                [(problem_id, idx, percent) for idx, percent in sorted(subtasks.items())],
            )
    return len(rows)


def delete_testdata(slug: str) -> None:
    directory = config.PROBLEM_DIR / slug
    if directory.exists():
        shutil.rmtree(directory, ignore_errors=True)
