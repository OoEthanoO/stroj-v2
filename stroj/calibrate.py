"""Measure whether this host can produce trustworthy verdicts.

A judge's time limits only mean something if the same submission takes the same
time on every run. Two things break that:

* **contention** — other work on the box stealing cycles;
* **thermal throttling** — sustained load dropping the clock, so a submission
  that passes on a cold machine fails on a hot one.

This runs a fixed CPU-bound workload through the real sandbox, many times in a
row, and reports the spread *and the drift* between the first and last quarter
of the run. Drift is the one that catches throttling: a laptop will look fine
for ten seconds and then sag.
"""

from __future__ import annotations

import shutil
import statistics
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import config
from .judge import languages
from .judge.sandbox import RunStatus, run

#: Each repetition aims for roughly this long, so the numbers are comparable
#: across fast and slow hosts.
TARGET_MS = 400

BENCH_CPP = """\
#include <cstdio>
#include <cstdlib>
int main(int argc, char** argv) {
    unsigned long long n = strtoull(argv[1], nullptr, 10);
    volatile unsigned long long x = 88172645463325252ULL;
    for (unsigned long long i = 0; i < n; i++) {
        x ^= x << 13; x ^= x >> 7; x ^= x << 17; x += i;
    }
    printf("%llu\\n", (unsigned long long) x);
    return 0;
}
"""

BENCH_PY = """\
import sys
n = int(sys.argv[1])
x = 88172645463325252
for i in range(n):
    x ^= (x << 13) & 0xFFFFFFFFFFFFFFFF
    x ^= x >> 7
    x ^= (x << 17) & 0xFFFFFFFFFFFFFFFF
    x = (x + i) & 0xFFFFFFFFFFFFFFFF
print(x)
"""


@dataclass
class Sample:
    wall_ms: int
    cpu_ms: int


@dataclass
class Report:
    language: str
    reps: int
    iterations: int
    total_seconds: float
    wall: list[int]
    cpu: list[int]
    #: Fixed process-startup cost, excluded from the throughput figure.
    baseline_ms: int = 0

    @property
    def throughput(self) -> float:
        """Million workload iterations per second — a machine-speed index.

        Comparable across hosts, which the raw millisecond figures are not:
        the workload size is auto-scaled per machine. The ratio of two hosts'
        throughput is the factor by which their time limits differ.
        """
        working_ms = max(self.median - self.baseline_ms, 1)
        return self.iterations / (working_ms / 1000.0) / 1e6

    @property
    def median(self) -> float:
        return statistics.median(self.wall)

    @property
    def spread_pct(self) -> float:
        """How far the typical worst case sits above the median.

        Deliberately p95 rather than max: one scheduling hiccup in a hundred
        runs should not condemn an otherwise steady machine, and max-minus-min
        is entirely at the mercy of that single sample.
        """
        if not self.median:
            return 0.0
        return 100.0 * (self.percentile(self.wall, 95) - self.median) / self.median

    @property
    def outlier_pct(self) -> float:
        """The worst single run, over the median — reported, not rated on."""
        if not self.median:
            return 0.0
        return 100.0 * (max(self.wall) - self.median) / self.median

    @property
    def drift_pct(self) -> float:
        """How much slower the last quarter is than the first — throttling."""
        quarter = max(1, len(self.wall) // 4)
        early = statistics.median(self.wall[:quarter])
        late = statistics.median(self.wall[-quarter:])
        if not early:
            return 0.0
        return 100.0 * (late - early) / early

    def percentile(self, values: list[int], pct: float) -> int:
        ordered = sorted(values)
        index = min(len(ordered) - 1, int(round(pct / 100 * (len(ordered) - 1))))
        return ordered[index]

    @property
    def rating(self) -> tuple[str, str]:
        """(verdict, advice) for a human."""
        if self.spread_pct < 10 and self.drift_pct < 5:
            return ("STABLE", "Time limits at ~2x a reference solution will be reliable.")
        if self.spread_pct < 25 and self.drift_pct < 15:
            return (
                "USABLE",
                "Set time limits at 3x a reference solution; borderline "
                "submissions will occasionally flip between AC and TLE.",
            )
        return (
            "UNRELIABLE",
            "This host cannot produce trustworthy verdicts under sustained "
            "load. Expect identical submissions to get different results.",
        )


def _pick_language() -> tuple[str, str, str]:
    """(language_id, source_filename, source) — prefer C++, it is what tight
    time limits are actually calibrated against."""
    if languages.is_available("cpp"):
        return "cpp", "bench.cpp", BENCH_CPP
    if languages.is_available("python3"):
        return "python3", "bench.py", BENCH_PY
    raise RuntimeError("no usable language: install a C++ compiler or python3")


def calibrate(reps: int = 60, seconds: float | None = None, progress=None) -> Report:
    """Run the workload `reps` times (or for `seconds`) and report the spread."""
    language_id, filename, source = _pick_language()
    lang = languages.get(language_id)

    config.ensure_dirs()
    box = Path(tempfile.mkdtemp(prefix="calib-", dir=config.WORK_DIR))
    try:
        (box / filename).write_text(source)

        if language_id == "cpp":
            compile_result = run(
                [languages.CXX, "-O2", "-std=c++20", "-o", "bench", filename],
                cwd=str(box),
                stderr_path=str(box / "cc.err"),
                wall_limit_s=60,
                memory_limit_bytes=2 * 1024**3,
            )
            if compile_result.status is not RunStatus.OK:
                raise RuntimeError(
                    f"benchmark failed to compile: {(box / 'cc.err').read_text()[:400]}"
                )
            argv_for = lambda n: ["./bench", str(n)]
        else:
            argv_for = lambda n: [languages.PYTHON, "-B", filename, str(n)]

        def once(iterations: int) -> Sample:
            result = run(
                argv_for(iterations),
                cwd=str(box),
                stdout_path=str(box / "out"),
                wall_limit_s=120,
                memory_limit_bytes=512 * 1024**2,
                address_space_rlimit=lang.limit_address_space,
                use_sandbox=config.USE_SANDBOX,
            )
            if result.status is not RunStatus.OK:
                raise RuntimeError(f"benchmark run failed: {result.status} {result.detail}")
            return Sample(result.wall_ms, result.cpu_ms)

        # Scale the workload so one repetition lands near TARGET_MS on this host.
        #
        # Naively dividing time by iterations does not work: a short run is
        # almost entirely process startup (fork, sandbox-exec, dynamic linking),
        # which does not scale with the workload. Measure that floor first and
        # calibrate against the *marginal* cost, growing the probe until the
        # difference is big enough to measure.
        baseline = min(once(1).wall_ms for _ in range(3))
        probe_n = 1_000_000 if language_id == "cpp" else 50_000
        while True:
            marginal = once(probe_n).wall_ms - baseline
            if marginal >= 50 or probe_n > 2_000_000_000:
                break
            probe_n *= 4
        per_iteration_ms = max(marginal, 1) / probe_n
        iterations = max(1000, int(TARGET_MS / per_iteration_ms))

        wall: list[int] = []
        cpu: list[int] = []
        elapsed = 0.0
        index = 0
        while (seconds is not None and elapsed < seconds) or (
            seconds is None and index < reps
        ):
            sample = once(iterations)
            wall.append(sample.wall_ms)
            cpu.append(sample.cpu_ms)
            elapsed += sample.wall_ms / 1000.0
            index += 1
            if progress:
                progress(index, sample)

        return Report(
            language=lang.name,
            reps=len(wall),
            iterations=iterations,
            total_seconds=elapsed,
            wall=wall,
            cpu=cpu,
            baseline_ms=baseline,
        )
    finally:
        shutil.rmtree(box, ignore_errors=True)


def format_report(report: Report) -> str:
    verdict, advice = report.rating
    lines = [
        f"workload  : {report.language}, {report.iterations:,} iterations "
        f"x {report.reps} reps over {report.total_seconds:.0f}s",
        "",
        f"wall ms   : min {min(report.wall)}  median {report.median:.0f}  "
        f"p95 {report.percentile(report.wall, 95)}  max {max(report.wall)}",
        f"cpu  ms   : min {min(report.cpu)}  median {statistics.median(report.cpu):.0f}  "
        f"p95 {report.percentile(report.cpu, 95)}  max {max(report.cpu)}",
        "",
        f"spread    : {report.spread_pct:.1f}%  (p95 over median — the typical worst case)",
        f"outlier   : {report.outlier_pct:.1f}%  (worst single run; one hiccup is normal)",
        f"drift     : {report.drift_pct:+.1f}%  (last quarter vs first — "
        f"this is what thermal throttling looks like)",
        "",
        f"speed     : {report.throughput:.1f} M iterations/s",
        "            Compare this between hosts to convert time limits. A host",
        "            with half the throughput needs double the limits, and every",
        "            problem must be rejudged after moving.",
        "",
        f"verdict   : {verdict}",
        f"            {advice}",
    ]
    return "\n".join(lines)
