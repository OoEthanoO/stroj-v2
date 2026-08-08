"""Limits and isolation. These run real processes, so they are the slow tests."""

from __future__ import annotations

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
