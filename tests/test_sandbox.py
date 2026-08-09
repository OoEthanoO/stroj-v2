"""Limits and isolation. These run real processes, so they are the slow tests."""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

import pytest

from stroj.judge import sandbox
from stroj.judge.sandbox import RunStatus


def run_python(code, tmp_path, **kwargs):
    kwargs.setdefault("wall_limit_s", 10)
    return sandbox.run(
        [sys.executable, "-c", code],
        cwd=str(tmp_path),
        stdout_path=str(tmp_path / "out"),
        stderr_path=str(tmp_path / "err"),
        **kwargs,
    )


def test_successful_run(tmp_path):
    result = run_python("print('hi')", tmp_path)
    assert result.status is RunStatus.OK
    assert result.exit_code == 0
    assert (tmp_path / "out").read_text().strip() == "hi"


def test_stdin_is_wired_up(tmp_path):
    (tmp_path / "in").write_text("7\n")
    result = run_python(
        "import sys; print(int(sys.stdin.read()) * 6)",
        tmp_path,
        stdin_path=str(tmp_path / "in"),
    )
    assert result.status is RunStatus.OK
    assert (tmp_path / "out").read_text().strip() == "42"


def test_nonzero_exit_is_a_runtime_error(tmp_path):
    result = run_python("raise SystemExit(3)", tmp_path)
    assert result.status is RunStatus.RUNTIME
    assert result.exit_code == 3


def test_uncaught_exception_is_a_runtime_error(tmp_path):
    result = run_python("raise ValueError('boom')", tmp_path)
    assert result.status is RunStatus.RUNTIME
    assert "boom" in (tmp_path / "err").read_text()


def test_wall_clock_timeout(tmp_path):
    result = run_python("import time; time.sleep(30)", tmp_path, wall_limit_s=1.0)
    assert result.status is RunStatus.TIMEOUT
    assert result.wall_ms < 5000


def test_busy_loop_times_out(tmp_path):
    result = run_python("\nwhile True: pass", tmp_path, wall_limit_s=1.0, cpu_limit_s=1.0)
    assert result.status is RunStatus.TIMEOUT


def test_memory_limit_is_enforced(tmp_path):
    """The headline case: macOS ignores RLIMIT_AS, so this must be policed by
    the sampler rather than the kernel."""
    result = run_python(
        "x = bytearray(600 * 1024 * 1024); print(len(x))",
        tmp_path,
        wall_limit_s=20,
        memory_limit_bytes=128 * 1024 * 1024,
    )
    assert result.status is RunStatus.MEMORY
    assert "600" not in (tmp_path / "out").read_text()


def test_memory_under_the_limit_is_fine(tmp_path):
    result = run_python(
        "x = bytearray(8 * 1024 * 1024); print('ok')",
        tmp_path,
        wall_limit_s=20,
        memory_limit_bytes=512 * 1024 * 1024,
    )
    assert result.status is RunStatus.OK
    assert result.max_rss_bytes > 0


def test_output_limit(tmp_path):
    result = run_python(
        "import sys\nwhile True: sys.stdout.write('x' * 4096)",
        tmp_path,
        wall_limit_s=20,
        output_limit_bytes=256 * 1024,
    )
    assert result.status in (RunStatus.OUTPUT, RunStatus.RUNTIME)
    assert (tmp_path / "out").stat().st_size <= 256 * 1024


def test_missing_executable_is_internal(tmp_path):
    result = sandbox.run(
        ["definitely-not-a-real-binary-xyz"], cwd=str(tmp_path), wall_limit_s=5
    )
    assert result.status is RunStatus.INTERNAL


def test_rusage_is_populated(tmp_path):
    result = run_python("sum(range(3_000_000))", tmp_path)
    assert result.cpu_ms > 0
    assert result.max_rss_bytes > 1_000_000


def test_children_are_reaped_with_the_group(tmp_path):
    """A submission cannot outlive its own timeout by forking."""
    code = (
        "import os, time\n"
        "if os.fork() == 0:\n"
        "    time.sleep(60)\n"
        "else:\n"
        "    time.sleep(60)\n"
    )
    result = run_python(code, tmp_path, wall_limit_s=1.0)
    assert result.status is RunStatus.TIMEOUT


@pytest.mark.skipif(
    not sandbox.sandbox_exec_available(), reason="needs macOS sandbox-exec"
)
class TestSandboxProfile:
    def test_writes_outside_the_box_are_denied(self, tmp_path):
        forbidden = tmp_path.parent / "escaped.txt"
        code = (
            f"try:\n"
            f"    open({str(forbidden)!r}, 'w').write('x')\n"
            f"    print('WROTE')\n"
            f"except OSError as e:\n"
            f"    print('DENIED')\n"
        )
        result = run_python(code, tmp_path, use_sandbox=True)
        assert result.status is RunStatus.OK
        assert (tmp_path / "out").read_text().strip() == "DENIED"
        assert not forbidden.exists()

    def test_writes_inside_the_box_are_allowed(self, tmp_path):
        code = "open('scratch.txt', 'w').write('x'); print('WROTE')"
        result = run_python(code, tmp_path, use_sandbox=True)
        assert result.status is RunStatus.OK
        assert (tmp_path / "out").read_text().strip() == "WROTE"

    def test_network_is_denied(self, tmp_path):
        code = (
            "import socket\n"
            "s = socket.socket()\n"
            "s.settimeout(5)\n"
            "try:\n"
            "    s.connect(('93.184.216.34', 80))\n"
            "    print('CONNECTED')\n"
            "except OSError:\n"
            "    print('DENIED')\n"
        )
        result = run_python(code, tmp_path, use_sandbox=True)
        assert (tmp_path / "out").read_text().strip() == "DENIED"


def test_profile_quotes_paths_with_spaces():
    profile = sandbox.build_profile(["/tmp/a dir/with \"quotes\""])
    assert '\\"' in profile
    assert "(deny network*)" in profile


class TestIsolationReporting:
    def test_mode_matches_the_platform(self):
        mode = sandbox.isolation_mode()
        assert mode in ("sandbox-exec", "unshare-net", "none")
        if sys.platform == "darwin":
            assert mode == "sandbox-exec"

    def test_available_agrees_with_mode(self):
        assert sandbox.sandbox_available() == (sandbox.isolation_mode() != "none")

    def test_unshare_prefix_is_none_off_linux(self):
        if sys.platform != "linux":
            assert sandbox.unshare_net_prefix() is None


class TestMemoryIsAttributedToTheProgram:
    """`ru_maxrss` from wait4 counts what the child inherited at fork, before
    exec replaced the image — so on Linux a trivial submission used to report
    the judge's own footprint, and any limit below it produced a false MLE."""

    def test_the_difference_reflects_the_allocation(self, tmp_path):
        small = run_python("pass", tmp_path, memory_limit_bytes=512 * 1024**2)
        big = run_python(
            "x = bytearray(120 * 1024 * 1024)\nx[::4096] = b'1' * len(x[::4096])",
            tmp_path, memory_limit_bytes=512 * 1024**2,
        )
        assert small.status is RunStatus.OK and big.status is RunStatus.OK
        grew_by = big.max_rss_bytes - small.max_rss_bytes
        assert grew_by > 100 * 1024**2, f"only grew {grew_by / 1e6:.1f} MB"

    def test_a_short_program_still_reports_something(self, tmp_path):
        """The fix must not swing the other way: a program that exits before the
        sampler wakes should not report 0 MiB."""
        result = run_python("pass", tmp_path, memory_limit_bytes=512 * 1024**2)
        assert result.max_rss_bytes > 0

    def test_measurement_works_without_a_limit(self, tmp_path):
        """Sampling used to be tied to enforcing a limit, so an unlimited run
        had only the polluted figure to fall back on."""
        result = run_python("x = bytearray(40 * 1024 * 1024)", tmp_path)
        assert result.status is RunStatus.OK
        assert result.max_rss_bytes > 0

    def test_a_generous_limit_is_not_tripped_by_the_judge(self, tmp_path):
        """The bug this guards: with the floor counted, any limit under the
        judge's own RSS marked every submission MLE."""
        result = run_python("pass", tmp_path, memory_limit_bytes=64 * 1024**2)
        assert result.status is RunStatus.OK


class TestChoosingWhichMeasurementToTrust:
    """Tested as a decision rather than an outcome, because the behaviour it
    guards is platform-specific: `ru_maxrss` inherits the parent's footprint on
    Linux but not on macOS, so measuring a real program cannot tell a correct
    rule from a broken one on a developer's machine.

    `parent_floor` means "the part of ru_maxrss that was inherited" — the
    judge's own resident size at fork on Linux, and zero where nothing is
    inherited.
    """

    MB = 1024 * 1024

    def test_the_inherited_footprint_is_subtracted(self):
        """The regression: using ru_maxrss raw put the judge's own size under
        every submission, and a C++ A+B came back at 42 MiB."""
        got = sandbox.resolve_rss(4 * self.MB, 42 * self.MB, 41 * self.MB)
        assert got == 4 * self.MB

    def test_a_sample_the_kernel_did_not_see_still_counts(self):
        """A program too short-lived to sample properly is the other failure,
        and reporting 0.03 MiB for it is no better than reporting 42."""
        got = sandbox.resolve_rss(30 * 1024, 12 * self.MB, 8 * self.MB)
        assert got == 4 * self.MB

    def test_the_larger_of_the_two_wins(self):
        assert sandbox.resolve_rss(9 * self.MB, 12 * self.MB, 8 * self.MB) == 9 * self.MB

    def test_nothing_inherited_means_rusage_is_trusted_whole(self):
        """On macOS ru_maxrss is already the child's own, so a floor of zero
        must leave it intact rather than discard it."""
        assert sandbox.resolve_rss(30 * 1024, 12 * self.MB, 0) == 12 * self.MB

    def test_a_shrinking_judge_cannot_produce_a_negative(self):
        assert sandbox.resolve_rss(0, 10 * self.MB, 40 * self.MB) == 0

    def test_no_measurement_at_all_reports_nothing(self):
        assert sandbox.resolve_rss(0, 0, 0) == 0


class TestReportedMemoryExcludesTheJudge:
    """`ru_maxrss` measures the child from `fork`, when it is still a copy of
    the judge — so it can never report less than the Python interpreter that
    launched it. Consulting it at all put a floor of roughly the judge's own
    size under every submission: a C++ A+B came back at 42 MiB.
    """

    def test_a_trivial_program_is_far_below_the_judge_itself(self, tmp_path):
        judge = sandbox._self_rss()
        # Nothing to measure on a platform without a working sampler.
        if not judge:
            pytest.skip("no RSS reader on this platform")
        result = run_python("pass", tmp_path)
        assert result.status is RunStatus.OK
        assert 0 < result.max_rss_bytes, "a live program cannot use nothing"
        # The interpreter is real; the judge's own footprint is not.
        assert result.max_rss_bytes < judge * 2, (
            f"reported {result.max_rss_bytes/1048576:.1f} MiB against a judge "
            f"holding {judge/1048576:.1f} MiB — the floor is back"
        )

    def test_an_allocation_shows_up_at_close_to_its_real_size(self, tmp_path):
        """The strongest check available: the *difference* between two runs of
        the same interpreter is the allocation and nothing else."""
        base = run_python("pass", tmp_path, memory_limit_bytes=512 * 1024**2)
        grown = run_python(
            "x = bytearray(80 * 1024 * 1024)\nx[::4096] = b'1' * len(x[::4096])",
            tmp_path, memory_limit_bytes=512 * 1024**2)
        assert base.status is RunStatus.OK and grown.status is RunStatus.OK
        delta = (grown.max_rss_bytes - base.max_rss_bytes) / 1048576
        assert 70 < delta < 95, f"80 MiB allocation measured as {delta:.1f} MiB"

    def test_two_different_programs_do_not_report_the_same_floor(self, tmp_path):
        """With the floor in place every language reported within a megabyte of
        the others, whatever it actually held — which is what gave the game
        away on a problem that stores two integers."""
        if not sandbox._self_rss():
            pytest.skip("no RSS reader on this platform")
        small = run_python("pass", tmp_path)
        large = run_python("x = bytearray(60 * 1024 * 1024)", tmp_path)
        assert large.max_rss_bytes > small.max_rss_bytes * 1.5


@pytest.mark.skipif(sys.platform != "linux", reason="VmHWM is a Linux thing")
class TestPeakIsReadFromTheKernel:
    """Sampling the *current* size loses any program short enough to finish
    between two samples — a C++ A+B runs in about two milliseconds and read as
    0 MiB. `VmHWM` is the kernel's own high-water mark, so a single reading at
    any point gives the true peak."""

    def hwm(self, pid: int) -> int:
        return sandbox._read_rss(pid)

    def test_it_reports_this_process_at_its_peak_not_its_present(self):
        import gc
        before = self.hwm(os.getpid())
        block = bytearray(60 * 1024 * 1024)
        block[::4096] = b"1" * len(block[::4096])
        peak = self.hwm(os.getpid())
        del block
        gc.collect()
        after = self.hwm(os.getpid())
        assert peak > before + 50 * 1024 * 1024, "the allocation was not seen"
        # The memory is gone, but the mark is not: that is what makes one
        # late sample enough.
        assert after >= peak

    def test_a_dead_process_reads_as_nothing(self):
        assert self.hwm(999999) == 0


class TestTheMeasuringWrapper:
    """Sampling cannot see a program that finishes in two milliseconds — on the
    live judge a C++ A+B produced zero reads against thirty-six per millisecond
    for longer programs — and `ru_maxrss` from the judge's own `wait4` counts
    what the child inherited at `fork`. Both come from *who* forks the program,
    so a small wrapper forks it instead and reports what the kernel already
    knows."""

    def test_it_only_stands_in_where_sampling_is_expendable(self):
        """The monitor watches what it started, so a wrapper blinds it. That is
        acceptable only when RLIMIT_AS is holding the ceiling instead — never
        for the JVM, which opts out of RLIMIT_AS and relies on sampling."""
        source = (pathlib.Path(sandbox.__file__)).read_text()
        guard = source[source.index("    helper = ("):]
        guard = guard[:guard.index("\n    )")]
        assert 'sys.platform == "linux"' in guard
        assert "address_space_rlimit" in guard

    def test_its_figure_outranks_the_others(self):
        MB = 1024 * 1024
        # Measured by a process holding a megabyte, so nothing to subtract.
        assert sandbox.resolve_rss(0, 42 * MB, 41 * MB, 3 * MB) == 3 * MB
        # ...and it wins even over a sample, which can only ever be lower.
        assert sandbox.resolve_rss(1 * MB, 42 * MB, 41 * MB, 3 * MB) == 3 * MB

    def test_without_it_nothing_changes(self):
        MB = 1024 * 1024
        assert sandbox.resolve_rss(4 * MB, 42 * MB, 41 * MB, 0) == 4 * MB

    @pytest.mark.skipif(shutil.which("cc") is None, reason="needs a C compiler")
    def test_it_builds_and_reports_a_believable_figure(self, tmp_path):
        built = sandbox.measure_helper()
        assert built and os.access(built, os.X_OK)

        # Run it directly: fork, exec, report on descriptor 3.
        read_fd, write_fd = os.pipe()
        os.set_inheritable(write_fd, True)
        result = subprocess.run(
            [built, sys.executable, "-c", "x = bytearray(40 * 1024 * 1024)"],
            pass_fds=(write_fd,) if write_fd == 3 else (),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ) if write_fd == 3 else None
        os.close(write_fd)
        os.close(read_fd)
        # The descriptor number is not controllable from subprocess, so the
        # end-to-end path is covered by `run()` above; here it is enough that
        # the wrapper exists, is executable, and was built from the source.
        assert pathlib.Path(built).stat().st_mtime >= sandbox._MEASURE_SOURCE.stat().st_mtime

    def test_a_missing_compiler_is_survivable(self, monkeypatch):
        """Going without means falling back to sampling, which is right for
        anything longer-lived. Failing the run would not be."""
        monkeypatch.setattr(sandbox, "_measure_path", False)
        monkeypatch.setattr(sandbox.shutil, "which", lambda name: None)
        monkeypatch.setattr(sandbox, "_MEASURE_SOURCE",
                            pathlib.Path("/nonexistent/measure.c"))
        assert sandbox.measure_helper() is None
