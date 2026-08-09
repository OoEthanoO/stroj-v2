"""Compile a submission, run it against every test, produce a verdict."""

from __future__ import annotations

import os
import re
import threading
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .. import config
from . import checkers, languages
from .sandbox import RunStatus, privilege_drop_target, run, toolchain_temp_dir

# Verdicts. PENDING/JUDGING are queue states; the rest are terminal.
PENDING = "PENDING"
JUDGING = "JUDGING"
AC = "AC"          # accepted
WA = "WA"          # wrong answer
TLE = "TLE"        # time limit exceeded
MLE = "MLE"        # memory limit exceeded
RE = "RE"          # runtime error
CE = "CE"          # compile error
OLE = "OLE"        # output limit exceeded
IE = "IE"          # internal error — the judge's fault, not the submission's
AB = "AB"          # aborted — cancelled by the submitter or an admin

VERDICT_NAMES = {
    PENDING: "Pending",
    JUDGING: "Judging",
    AC: "Accepted",
    WA: "Wrong Answer",
    TLE: "Time Limit Exceeded",
    MLE: "Memory Limit Exceeded",
    RE: "Runtime Error",
    CE: "Compile Error",
    OLE: "Output Limit Exceeded",
    IE: "Internal Error",
    AB: "Aborted",
}

# Never load more than this much of a program's stdout into memory to compare.
OUTPUT_READ_CAP = 16 * 1024 * 1024

# Runtimes report running out of memory in their own way; RLIMIT_AS usually
# surfaces as a clean allocation failure rather than a kill, so peak RSS alone
# would misreport these as runtime errors.
_OOM_MARKERS = (
    "std::bad_alloc",
    "MemoryError",
    "OutOfMemoryError",
    "Cannot allocate memory",
    "cannot allocate memory",
    "bad_array_new_length",
    "GC overhead limit exceeded",
    "Java heap space",
)

# A C++ submission that #includes an absolute path or escapes its directory is
# reading the judge's filesystem at compile time, not solving the problem.
_BAD_INCLUDE = re.compile(
    r'^\s*#\s*include\s*[<"]\s*(/|\.\.)', re.MULTILINE
)


@dataclass
class TestOutcome:
    idx: int
    verdict: str
    time_ms: int
    memory_kb: int
    points: int
    message: str = ""


@dataclass
class JudgeOutcome:
    verdict: str
    score: int = 0
    max_score: int = 0
    time_ms: int = 0
    memory_kb: int = 0
    message: str = ""
    tests: list[TestOutcome] = field(default_factory=list)


@dataclass
class ProblemSpec:
    """Everything the runner needs to know about a problem."""

    time_limit_ms: int = 1000
    memory_limit_mb: int = 256
    checker: str = "token"
    float_eps: float = 1e-6
    partial: bool = False
    #: language id -> (time_limit_ms, memory_limit_mb), set from measured runs.
    #: A language absent here falls back to the scaled base limit.
    limits: dict[str, tuple[int, int]] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row, limits: dict[str, tuple[int, int]] | None = None) -> "ProblemSpec":
        return cls(
            time_limit_ms=row["time_limit_ms"],
            memory_limit_mb=row["memory_limit_mb"],
            checker=row["checker"],
            float_eps=row["float_eps"],
            partial=bool(row["partial"]),
            limits=dict(limits or {}),
        )

    def limits_for(self, lang: "languages.Language") -> tuple[int, int]:
        """The time and memory this language actually gets on this problem.

        An explicit limit wins outright — it was measured against the intended
        solution in that language, which is a better answer than any multiplier.
        """
        override = self.limits.get(lang.id)
        if override is not None:
            return override
        return (
            lang.effective_time_limit_ms(self.time_limit_ms),
            lang.effective_memory_limit_mb(self.memory_limit_mb),
        )


@dataclass
class TestSpec:
    idx: int
    input_path: str
    answer_path: str
    points: int = 1
    #: Sample tests are already public, so their diagnostics can be shown in
    #: full. Hidden tests must not echo anything back — see `check(reveal=...)`.
    is_sample: bool = False
    #: Scoring group this test belongs to; 0 means ungrouped.
    subtask: int = 0


def earned_percent(
    tests: list[TestSpec],
    results: list[TestOutcome],
    subtasks: dict[int, int] | None,
    partial: bool,
) -> int:
    """Share of a problem's points this run earned, 0-100.

    Three regimes, in order of how deliberate the problem author was:

    * **Subtasks.** All-or-nothing per group — a subtask pays its percentage
      only if every test in it passed. This is the point of subtasks: they
      correspond to a weaker version of the problem someone actually solved,
      not to how many tests happened to pass.
    * **Partial, no subtasks.** The share of tests passed, so a beginner who
      handles the small cases still gets something.
    * **Neither.** All or nothing.
    """
    passed = {r.idx for r in results if r.verdict == AC}
    if not tests:
        return 0

    if subtasks:
        total = 0
        for group, percent in subtasks.items():
            in_group = [t.idx for t in tests if t.subtask == group]
            if in_group and all(idx in passed for idx in in_group):
                total += percent
        return min(100, total)

    scored = [t for t in tests if not t.is_sample] or tests
    if partial:
        return round(100 * sum(1 for t in scored if t.idx in passed) / len(scored))
    return 100 if all(t.idx in passed for t in scored) else 0


def validate_source(language_id: str, source: str) -> str | None:
    """Return a rejection reason, or ``None`` if the source may be compiled."""
    if not source.strip():
        return "Source file is empty."
    if len(source.encode("utf-8", "replace")) > config.MAX_SOURCE_BYTES:
        return f"Source exceeds {config.MAX_SOURCE_BYTES // 1024} KiB."
    if language_id == "cpp" and _BAD_INCLUDE.search(source):
        return "#include may not reference an absolute path or a parent directory."
    if language_id == "java" and not re.search(
        r"\bpublic\s+(final\s+|abstract\s+)?class\s+Main\b", source
    ):
        return "Java submissions must declare `public class Main`."
    return None


def _read_clipped(path: Path, cap: int) -> tuple[str, bool]:
    """Read at most ``cap`` bytes. Returns ``(text, was_truncated)``."""
    try:
        size = path.stat().st_size
    except OSError:
        return "", False
    with path.open("rb") as fh:
        data = fh.read(cap)
    return data.decode("utf-8", "replace"), size > cap


def _looks_like_oom(text: str) -> bool:
    return any(marker in text for marker in _OOM_MARKERS)


def _hand_box_to_runner(box: Path, run_as: tuple[int, int] | None) -> None:
    """Give the submission's scratch directory to the account it will run as.

    Everything else the judge owns — the database, other submissions' boxes,
    the problems' answer files — stays unreachable to it.
    """
    if run_as is None:
        return
    uid, gid = run_as
    os.chown(box, uid, gid)
    for child in box.rglob("*"):
        os.chown(child, uid, gid)


def _compile(
    lang: languages.Language,
    box: Path,
    use_sandbox: bool,
    run_as: tuple[int, int] | None = None,
    abort: "threading.Event | None" = None,
) -> tuple[bool, str]:
    argv = lang.compile_argv()
    if argv is None:
        return True, ""

    stderr_path = box / "compile.err"
    stdout_path = box / "compile.out"
    toolchain_temp = toolchain_temp_dir()
    result = run(
        argv,
        cwd=str(box),
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
        wall_limit_s=config.COMPILE_TIME_LIMIT_S,
        cpu_limit_s=config.COMPILE_TIME_LIMIT_S,
        memory_limit_bytes=config.COMPILE_MEMORY_MB * 1024 * 1024,
        address_space_rlimit=lang.limit_address_space,
        output_limit_bytes=config.OUTPUT_LIMIT_BYTES,
        use_sandbox=use_sandbox,
        extra_write_dirs=(toolchain_temp,) if toolchain_temp else (),
        # The compiler is fed attacker-controlled source, so it is untrusted
        # too and runs under the same reduced account.
        run_as=run_as,
        abort=abort,
    )
    if result.status is RunStatus.OK:
        return True, ""
    if result.status is RunStatus.ABORTED:
        return False, "\x00cancelled"

    diagnostics, _ = _read_clipped(stderr_path, config.MESSAGE_CLIP_BYTES)
    if not diagnostics.strip():
        diagnostics, _ = _read_clipped(stdout_path, config.MESSAGE_CLIP_BYTES)
    if result.status is RunStatus.TIMEOUT:
        return False, "Compilation timed out.\n\n" + diagnostics
    if result.status is RunStatus.INTERNAL:
        return False, f"Could not run the compiler: {result.detail}"
    return False, diagnostics.strip() or f"Compilation failed ({result.detail})."


#: A first run pays for faulting the executable in from disk and for the
#: dynamic linker resolving its libraries, and none of that is the submission.
#: Measured on a freshly compiled C++ binary: 175 ms, then 15, 14, 12, 12, 12 —
#: with CPU steady at 9 ms throughout, so it is waiting, not working. The
#: judge reports the slowest test, so that one-off cost became the answer.
#: Cap the throwaway run so a slow submission cannot pay a full limit for it.
WARM_UP_LIMIT_S = 1.0


def _warm_up(
    lang: languages.Language,
    problem: ProblemSpec,
    test: TestSpec,
    box: Path,
    use_sandbox: bool,
    run_as: tuple[int, int] | None,
    abort: "threading.Event | None",
) -> None:
    """Run the program once and throw the result away.

    Nothing here is scored or reported; the point is only that the pages are
    resident and the libraries are bound before the first test is timed. A
    failure is ignored on purpose — if the program is broken, the real tests
    are where that gets decided.
    """
    if abort is not None and abort.is_set():
        return
    time_limit_ms, memory_limit_mb = problem.limits_for(lang)
    limit = min(WARM_UP_LIMIT_S, time_limit_ms / 1000.0)
    try:
        run(
            lang.run_argv(memory_limit_mb),
            cwd=str(box),
            stdin_path=test.input_path,
            stdout_path=str(box / "warmup.out"),
            stderr_path=str(box / "warmup.err"),
            wall_limit_s=max(0.2, limit),
            memory_limit_bytes=memory_limit_mb * 1024 * 1024,
            address_space_rlimit=lang.limit_address_space,
            output_limit_bytes=config.OUTPUT_LIMIT_BYTES,
            use_sandbox=use_sandbox,
            run_as=run_as,
            abort=abort,
        )
    except Exception:                       # pragma: no cover - defensive
        pass
    for leftover in ("warmup.out", "warmup.err"):
        (box / leftover).unlink(missing_ok=True)


def _run_one_test(
    lang: languages.Language,
    problem: ProblemSpec,
    test: TestSpec,
    box: Path,
    use_sandbox: bool,
    run_as: tuple[int, int] | None = None,
    abort: "threading.Event | None" = None,
) -> TestOutcome:
    time_limit_ms, memory_limit_mb = problem.limits_for(lang)
    limit_s = time_limit_ms / 1000.0

    out_path = box / "stdout.txt"
    err_path = box / "stderr.txt"
    for stale in (out_path, err_path):
        stale.unlink(missing_ok=True)

    result = run(
        # The resolved limit, not the base one: a runtime that caps its heap
        # with a flag has to be told the ceiling it is actually being held to,
        # or a per-language limit raises the allowance while leaving the heap
        # sized for the base and the program dies well inside it.
        lang.run_argv(memory_limit_mb),
        cwd=str(box),
        stdin_path=test.input_path,
        stdout_path=str(out_path),
        stderr_path=str(err_path),
        # Give an over-running program a bit of rope so we can measure it,
        # then compare against the real limit below.
        wall_limit_s=limit_s * 2 + 2.0,
        cpu_limit_s=limit_s + 1.0,
        memory_limit_bytes=memory_limit_mb * 1024 * 1024,
        address_space_rlimit=lang.limit_address_space,
        output_limit_bytes=config.OUTPUT_LIMIT_BYTES,
        use_sandbox=use_sandbox,
        run_as=run_as,
        abort=abort,
        # Only here. Compiling is not the submission running, and the warm-up
        # is thrown away — measuring either is pointless, and putting an extra
        # process in front of the compiler broke every language that has one.
        measure=True,
    )

    memory_kb = result.memory_kb
    time_ms = result.wall_ms
    stderr_text, _ = _read_clipped(err_path, config.MESSAGE_CLIP_BYTES)

    def outcome(verdict: str, message: str = "") -> TestOutcome:
        points = test.points if verdict == AC else 0
        return TestOutcome(test.idx, verdict, time_ms, memory_kb, points, message)

    if result.status is RunStatus.ABORTED:
        return outcome(AB, "cancelled")
    if result.status is RunStatus.INTERNAL:
        return outcome(IE, result.detail)
    if result.status is RunStatus.TIMEOUT or time_ms > time_limit_ms:
        return outcome(TLE, f"exceeded {time_limit_ms} ms")
    if result.status is RunStatus.OUTPUT:
        return outcome(OLE, "wrote more output than allowed")
    if result.status is RunStatus.MEMORY:
        return outcome(MLE, f"exceeded {memory_limit_mb} MiB")
    if result.status is RunStatus.RUNTIME:
        if _looks_like_oom(stderr_text) or memory_kb >= memory_limit_mb * 1024:
            return outcome(MLE, f"exceeded {memory_limit_mb} MiB")
        detail = result.detail
        # stderr is echoed back to the submitter, so on a hidden test it is a
        # 4 KiB read primitive: print a secret, exit non-zero, read the verdict.
        # Sample tests are public, so their diagnostics stay useful.
        if test.is_sample and stderr_text.strip():
            detail = f"{detail}\n{stderr_text.strip()}"
        return outcome(RE, detail)

    actual, truncated = _read_clipped(out_path, OUTPUT_READ_CAP)
    if truncated:
        return outcome(OLE, "output too large to check")
    expected, _ = _read_clipped(Path(test.answer_path), OUTPUT_READ_CAP)

    verdict_check = checkers.check(
        expected, actual, problem.checker, problem.float_eps, reveal=test.is_sample
    )
    if not verdict_check.ok:
        return outcome(WA, verdict_check.message)
    return outcome(AC)


def _cancelled(outcome: "JudgeOutcome") -> "JudgeOutcome":
    """Report a run that was stopped part-way.

    The tests that did finish are kept: they are real results, and a solver who
    cancelled after watching three tests fail should still see those three.
    Scores are cleared, because a partial run must never look like a partial
    score — that is what `earned_percent` would otherwise read.
    """
    outcome.verdict = AB
    outcome.score = 0
    outcome.message = "Cancelled."
    return outcome


def judge(
    source: str,
    language_id: str,
    problem: ProblemSpec,
    tests: list[TestSpec],
    *,
    use_sandbox: bool | None = None,
    work_dir: Path | None = None,
    on_test: Callable[[TestOutcome], None] | None = None,
    abort: "threading.Event | None" = None,
) -> JudgeOutcome:
    """Judge one submission. Never raises — internal failures become ``IE``.

    ``on_test`` is invoked as each test finishes, so a caller can publish
    results while the rest are still running rather than at the end.
    """
    if use_sandbox is None:
        use_sandbox = config.USE_SANDBOX

    try:
        lang = languages.get(language_id)
    except ValueError as exc:
        return JudgeOutcome(IE, message=str(exc))

    if not languages.is_available(language_id):
        return JudgeOutcome(
            IE, message=f"{lang.name} is not installed on this judge."
        )

    if not tests:
        return JudgeOutcome(IE, message="This problem has no test cases yet.")

    max_score = sum(t.points for t in tests)

    rejection = validate_source(language_id, source)
    if rejection:
        return JudgeOutcome(CE, max_score=max_score, message=rejection)

    base = work_dir or config.WORK_DIR
    base.mkdir(parents=True, exist_ok=True)

    run_as = privilege_drop_target()
    box = Path(tempfile.mkdtemp(prefix="box-", dir=base))
    try:
        os.chmod(box, 0o755)
        (box / lang.source_file).write_text(source, encoding="utf-8")
        _hand_box_to_runner(box, run_as)

        if abort is not None and abort.is_set():
            return JudgeOutcome(AB, max_score=max_score, message="Cancelled.")

        ok, diagnostics = _compile(lang, box, use_sandbox, run_as, abort)
        if not ok:
            if diagnostics == "\x00cancelled":
                return JudgeOutcome(AB, max_score=max_score, message="Cancelled.")
            return JudgeOutcome(CE, max_score=max_score, message=diagnostics)
        # The compiler just created new files (the binary, __pycache__, class
        # files) owned by the runner already — but re-assert, since a language
        # with no compile step never went through that path.
        _hand_box_to_runner(box, run_as)

        _warm_up(lang, problem, tests[0], box, use_sandbox, run_as, abort)

        outcome = JudgeOutcome(AC, max_score=max_score)
        for test in tests:
            # Checked before each test as well as inside the sandbox, so a
            # request that lands between tests is not held until the next one
            # finishes.
            if abort is not None and abort.is_set():
                return _cancelled(outcome)
            test_result = _run_one_test(
                lang, problem, test, box, use_sandbox, run_as, abort
            )
            if test_result.verdict == AB:
                return _cancelled(outcome)
            outcome.tests.append(test_result)
            outcome.score += test_result.points
            outcome.time_ms = max(outcome.time_ms, test_result.time_ms)
            outcome.memory_kb = max(outcome.memory_kb, test_result.memory_kb)
            if on_test is not None:
                try:
                    on_test(test_result)
                except Exception:  # pragma: no cover - defensive
                    # Publishing progress is a convenience; a failure there must
                    # never cost the submission its verdict.
                    pass
            if test_result.verdict != AC:
                if outcome.verdict == AC:
                    outcome.verdict = test_result.verdict
                    outcome.message = (
                        f"Test {test_result.idx}: {test_result.message}"
                        if test_result.message
                        else f"Failed on test {test_result.idx}"
                    )
                if not problem.partial:
                    break
        return outcome
    except Exception as exc:  # pragma: no cover - defensive
        return JudgeOutcome(IE, max_score=max_score, message=f"{type(exc).__name__}: {exc}")
    finally:
        shutil.rmtree(box, ignore_errors=True)
