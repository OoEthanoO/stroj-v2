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


@pytest.mark.skipif(not sandbox.sandbox_available(), reason="needs macOS sandbox-exec")
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
