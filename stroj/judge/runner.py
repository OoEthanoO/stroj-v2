"""Compile a submission, run it against every test, produce a verdict."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .. import config
from . import checkers, languages
from .sandbox import RunStatus, run, toolchain_temp_dir

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

    @classmethod
    def from_row(cls, row) -> "ProblemSpec":
        return cls(
            time_limit_ms=row["time_limit_ms"],
            memory_limit_mb=row["memory_limit_mb"],
            checker=row["checker"],
            float_eps=row["float_eps"],
            partial=bool(row["partial"]),
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


def _compile(
    lang: languages.Language, box: Path, use_sandbox: bool
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
    )
    if result.status is RunStatus.OK:
        return True, ""

    diagnostics, _ = _read_clipped(stderr_path, config.MESSAGE_CLIP_BYTES)
    if not diagnostics.strip():
        diagnostics, _ = _read_clipped(stdout_path, config.MESSAGE_CLIP_BYTES)
    if result.status is RunStatus.TIMEOUT:
        return False, "Compilation timed out.\n\n" + diagnostics
    if result.status is RunStatus.INTERNAL:
        return False, f"Could not run the compiler: {result.detail}"
    return False, diagnostics.strip() or f"Compilation failed ({result.detail})."


def _run_one_test(
    lang: languages.Language,
    problem: ProblemSpec,
    test: TestSpec,
    box: Path,
    use_sandbox: bool,
) -> TestOutcome:
    time_limit_ms = lang.effective_time_limit_ms(problem.time_limit_ms)
    memory_limit_mb = lang.effective_memory_limit_mb(problem.memory_limit_mb)
    limit_s = time_limit_ms / 1000.0

    out_path = box / "stdout.txt"
    err_path = box / "stderr.txt"
    for stale in (out_path, err_path):
        stale.unlink(missing_ok=True)

    result = run(
        lang.run_argv(problem.memory_limit_mb),
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
    )

    memory_kb = result.memory_kb
    time_ms = result.wall_ms
    stderr_text, _ = _read_clipped(err_path, config.MESSAGE_CLIP_BYTES)

    def outcome(verdict: str, message: str = "") -> TestOutcome:
        points = test.points if verdict == AC else 0
        return TestOutcome(test.idx, verdict, time_ms, memory_kb, points, message)

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


def judge(
    source: str,
    language_id: str,
    problem: ProblemSpec,
    tests: list[TestSpec],
    *,
    use_sandbox: bool | None = None,
    work_dir: Path | None = None,
) -> JudgeOutcome:
    """Judge one submission. Never raises — internal failures become ``IE``."""
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

    box = Path(tempfile.mkdtemp(prefix="box-", dir=base))
    try:
        os.chmod(box, 0o755)
        (box / lang.source_file).write_text(source, encoding="utf-8")

        ok, diagnostics = _compile(lang, box, use_sandbox)
        if not ok:
            return JudgeOutcome(CE, max_score=max_score, message=diagnostics)

        outcome = JudgeOutcome(AC, max_score=max_score)
        for test in tests:
            test_result = _run_one_test(lang, problem, test, box, use_sandbox)
            outcome.tests.append(test_result)
            outcome.score += test_result.points
            outcome.time_ms = max(outcome.time_ms, test_result.time_ms)
            outcome.memory_kb = max(outcome.memory_kb, test_result.memory_kb)
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
