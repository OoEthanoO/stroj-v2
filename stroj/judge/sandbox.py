"""Run one untrusted program under time, memory and output limits.

We fork/exec by hand rather than going through :mod:`subprocess` for one reason:
``os.wait4`` hands back the child's own ``rusage``, which is the only way to get
a per-submission peak-RSS and CPU number when several judge threads are running
at once. Everything the child needs is computed *before* the fork so the code
between fork and exec stays to plain syscalls.

Isolation on macOS is ``sandbox-exec`` (no network, no writes outside the box)
plus ``setrlimit``. That stops accidents and casual mischief; it is not a
container and not a defence against a determined attacker. See README.
"""

from __future__ import annotations

import ctypes
import math
import os
import pwd
import resource
import shutil
import signal
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache

# getrusage reports ru_maxrss in bytes on the BSDs (macOS) and kilobytes on Linux.
RSS_UNIT_BYTES = 1 if sys.platform == "darwin" else 1024

SANDBOX_EXEC = "/usr/bin/sandbox-exec"

#: How often the memory monitor samples a running child.
POLL_INTERVAL_S = 0.02

#: For this long after exec, sample far more often. Short submissions would
#: otherwise be reaped between two slow samples and report no memory at all.
FAST_SAMPLE_WINDOW_S = 0.05

# Start from `allow default` and subtract: a deny-by-default profile breaks
# clang, the JVM and CPython in a dozen small ways that are not worth chasing
# for a local judge. What actually matters is that submissions cannot reach the
# network or write anywhere but their own scratch directory.
_PROFILE_TEMPLATE = """(version 1)
(allow default)
(deny network*)
(deny file-write*)
(allow file-write*
{write_paths}
    (literal "/dev/null")
    (literal "/dev/zero")
    (literal "/dev/random")
    (literal "/dev/urandom")
    (literal "/dev/dtracehelper")
    (literal "/dev/stdout")
    (literal "/dev/stderr"))
"""


class RunStatus(str, Enum):
    OK = "OK"
    TIMEOUT = "TIMEOUT"
    MEMORY = "MEMORY"
    OUTPUT = "OUTPUT"
    RUNTIME = "RUNTIME"
    INTERNAL = "INTERNAL"


@dataclass
class RunResult:
    status: RunStatus
    exit_code: int | None
    term_signal: int | None
    wall_ms: int
    cpu_ms: int
    max_rss_bytes: int
    detail: str = ""

    @property
    def memory_kb(self) -> int:
        return self.max_rss_bytes // 1024


def sandbox_exec_available() -> bool:
    """macOS: full isolation — no network, no writes outside the box."""
    return sys.platform == "darwin" and os.path.exists(SANDBOX_EXEC)


@lru_cache(maxsize=1)
def unshare_net_prefix() -> tuple[str, ...] | None:
    """Linux: an argv prefix that puts the child in an empty network namespace.

    There is no `sandbox-exec` outside macOS, so network isolation on Linux
    comes from `unshare`. Whether it works depends on the kernel and on the
    container's capabilities, so rather than guess we actually run it once and
    keep the first form that succeeds. Returns None when neither works, and the
    caller degrades to rlimits alone.
    """
    if sys.platform != "linux":
        return None
    unshare = shutil.which("unshare")
    if unshare is None:
        return None
    candidates = (
        ("--net",),                            # needs CAP_SYS_ADMIN
        ("--user", "--map-root-user", "--net"),  # unprivileged user namespace
    )
    for args in candidates:
        try:
            probe = subprocess.run(
                [unshare, *args, "true"], capture_output=True, timeout=15
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            return (unshare, *args)
    return None


#: Account submissions are dropped to. It must not be the account the judge
#: itself runs as, or a submission inherits the judge's access to the database.
RUNNER_USER = os.environ.get("STROJ_RUNNER_USER", "stroj-runner")


@lru_cache(maxsize=1)
def privilege_drop_target() -> tuple[int, int] | None:
    """``(uid, gid)`` to run submissions as, or None if we cannot separate.

    Only root can change uid, so a judge running unprivileged has no way to put
    submissions on a different account — and they then share its read/write
    access to ``/data``, including the database. The caller must treat None as
    a serious degradation, not a detail.
    """
    if os.geteuid() != 0:
        return None
    try:
        entry = pwd.getpwnam(RUNNER_USER)
    except KeyError:
        return None
    if entry.pw_uid == 0:
        return None
    return (entry.pw_uid, entry.pw_gid)


def isolation_mode() -> str:
    """Which isolation is actually in force here: the honest answer, not the
    configured one."""
    if sandbox_exec_available():
        return "sandbox-exec"
    if unshare_net_prefix() is not None:
        return "unshare-net"
    return "none"


def sandbox_available() -> bool:
    """True when *some* isolation beyond rlimits is available."""
    return isolation_mode() != "none"


def protection_summary() -> str:
    """One word for how well a submission is actually contained.

    ``isolation_mode()`` names only the in-process sandbox mechanism, and there
    isn't one inside a Linux container. Reporting that alone read as "nothing
    is protecting you", which was wrong and alarming: privilege separation is
    the load-bearing control there, and it is invisible to that field.

    Deliberately reports only what this process can verify about itself. The
    host's egress rule does real work but lives outside the container, so it is
    never counted here — see DEPLOY.md.
    """
    separated = privilege_drop_target() is not None
    if sandbox_exec_available():
        return "full"
    if separated and isolation_mode() == "unshare-net":
        return "separated+netns"
    if separated:
        return "separated"
    if isolation_mode() == "unshare-net":
        return "network-only"
    return "none"


def toolchain_temp_dir() -> str | None:
    """The per-user temp directory Apple's toolchain caches into.

    ``clang`` writes its ``xcrun`` lookup cache here through
    ``confstr(_CS_DARWIN_USER_TEMP_DIR)``, ignoring ``$TMPDIR``, so compiles
    have to be allowed to write to it. Only the compile step gets this.
    """
    if sys.platform != "darwin":
        return None
    # _CS_DARWIN_USER_TEMP_DIR; Python's confstr name table has no entry for it.
    try:
        value = os.confstr(65537)
    except (ValueError, OSError):
        value = None
    return value or os.environ.get("TMPDIR") or None


def _sbpl_quote(path: str) -> str:
    return path.replace("\\", "\\\\").replace('"', '\\"')


def build_profile(write_dirs: list[str]) -> str:
    lines = "\n".join(
        f'    (subpath "{_sbpl_quote(os.path.realpath(d))}")' for d in write_dirs
    )
    return _PROFILE_TEMPLATE.format(write_paths=lines)


def _apply_limits(
    cpu_seconds: int, address_space: int | None, file_size: int | None
) -> None:
    """Set rlimits in the freshly forked child. Never raises."""
    def _set(what, soft, hard):
        try:
            resource.setrlimit(what, (soft, hard))
        except (ValueError, OSError):
            pass

    _set(resource.RLIMIT_CPU, cpu_seconds, cpu_seconds + 1)
    _set(resource.RLIMIT_CORE, 0, 0)
    _set(resource.RLIMIT_NOFILE, 256, 256)
    if address_space is not None:
        _set(resource.RLIMIT_AS, address_space, address_space)
    if file_size is not None:
        _set(resource.RLIMIT_FSIZE, file_size, file_size)


def _make_rss_reader():
    """Return ``pid -> resident bytes`` (0 when unknown) for this platform.

    macOS accepts ``RLIMIT_AS`` and then ignores it — a submission can happily
    allocate gigabytes past its limit — so the memory ceiling has to be policed
    from the outside by sampling the child instead.
    """
    if sys.platform == "darwin":
        try:
            libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        except OSError:  # pragma: no cover - macOS always has it
            return lambda pid: 0
        libproc.proc_pidinfo.restype = ctypes.c_int
        libproc.proc_pidinfo.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int
        ]
        PROC_PIDTASKINFO, SIZE = 4, 96

        def read_rss(pid: int) -> int:
            buffer = ctypes.create_string_buffer(SIZE)
            if libproc.proc_pidinfo(pid, PROC_PIDTASKINFO, 0, buffer, SIZE) < SIZE:
                return 0
            # struct proc_taskinfo: virtual size, then resident size.
            return struct.unpack_from("=QQ", buffer.raw, 0)[1]

        return read_rss

    page_size = os.sysconf("SC_PAGE_SIZE")

    def read_rss_proc(pid: int) -> int:
        try:
            with open(f"/proc/{pid}/statm", "rb") as fh:
                return int(fh.read().split()[1]) * page_size
        except (OSError, IndexError, ValueError):
            return 0

    return read_rss_proc


_read_rss = _make_rss_reader()


def _self_rss() -> int:
    """This process's resident size, used as a pollution floor — see below."""
    return _read_rss(os.getpid())


class _MemoryMonitor(threading.Thread):
    """Sample a child's RSS; kill its process group if it crosses the limit.

    Sampling only starts once the child has actually ``execve``'d. Between
    ``fork`` and ``exec`` the child is a copy-on-write clone of the judge and
    reports the judge's own footprint, so an early sample would attribute tens
    of megabytes of Python interpreter to the submission.
    """

    def __init__(self, pid: int, limit_bytes: int | None, exec_path: str | None) -> None:
        super().__init__(name=f"rss-{pid}", daemon=True)
        self.pid = pid
        self.limit = limit_bytes
        self.exec_path = exec_path
        self.peak = 0
        self.exceeded = threading.Event()
        self._done = threading.Event()
        self._running_yet = exec_path is None

    def _has_exec(self) -> bool:
        """True once /proc says the child is running the program we launched."""
        if self._running_yet:
            return True
        try:
            if os.readlink(f"/proc/{self.pid}/exe") == self.exec_path:
                self._running_yet = True
        except OSError:
            pass
        return self._running_yet

    def run(self) -> None:
        started = None
        while not self._done.is_set():
            if not self._has_exec():
                # Spin tightly: exec lands in well under a millisecond, and
                # sleeping the full interval would lose real measurements.
                self._done.wait(0.0005)
                continue
            if started is None:
                started = time.monotonic()
            rss = _read_rss(self.pid)
            if rss > self.peak:
                self.peak = rss
            if self.limit is not None and rss >= self.limit:
                self.exceeded.set()
                _killpg(self.pid)
                return
            # A submission that finishes in a few milliseconds would otherwise
            # be reaped before the first slow sample, and report nothing at all.
            elapsed = time.monotonic() - started
            self._done.wait(0.0005 if elapsed < FAST_SAMPLE_WINDOW_S else POLL_INTERVAL_S)

    def stop(self) -> None:
        self._done.set()


def _signal_name(sig: int) -> str:
    try:
        return signal.Signals(sig).name
    except ValueError:
        return "unknown"


def _killpg(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _resolve(program: str, path: str | None) -> str | None:
    if program.startswith(("/", "./", "../")):
        return program
    return shutil.which(program, path=path)


def run(
    argv: list[str],
    *,
    cwd: str,
    stdin_path: str | None = None,
    stdout_path: str | None = None,
    stderr_path: str | None = None,
    wall_limit_s: float,
    cpu_limit_s: float | None = None,
    memory_limit_bytes: int | None = None,
    address_space_rlimit: bool = True,
    output_limit_bytes: int | None = None,
    use_sandbox: bool = False,
    extra_write_dirs: tuple[str, ...] = (),
    env: dict[str, str] | None = None,
    run_as: tuple[int, int] | None = None,
) -> RunResult:
    """Execute ``argv`` in ``cwd`` under the given limits and report how it went.

    ``memory_limit_bytes`` is enforced by sampling the child's resident size and
    killing it on overage. It is *additionally* set as ``RLIMIT_AS`` unless
    ``address_space_rlimit`` is false — pass false for runtimes that reserve a
    large virtual arena up front and would die on startup (the JVM).
    """
    cwd = os.path.realpath(cwd)
    if cpu_limit_s is None:
        cpu_limit_s = wall_limit_s

    child_env = dict(
        env
        or {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
            "HOME": cwd,
            "TMPDIR": cwd,
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        }
    )

    argv = list(argv)
    if use_sandbox:
        if sandbox_exec_available():
            profile = build_profile([cwd, *extra_write_dirs])
            argv = [SANDBOX_EXEC, "-p", profile, *argv]
        else:
            # Linux: no filesystem confinement here — that is the container's
            # job — but the network can still be taken away.
            prefix = unshare_net_prefix()
            if prefix is not None:
                argv = [*prefix, *argv]

    exe = _resolve(argv[0], child_env.get("PATH"))
    if exe is None:
        return RunResult(
            RunStatus.INTERNAL, None, None, 0, 0, 0, f"executable not found: {argv[0]}"
        )

    # Everything below is precomputed so the post-fork child only makes syscalls.
    in_path = stdin_path or os.devnull
    out_path = stdout_path or os.devnull
    err_path = stderr_path or os.devnull
    cpu_rlimit = max(1, math.ceil(cpu_limit_s))
    write_flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC

    # Captured before the fork: the child inherits this footprint, and
    # ru_maxrss cannot tell it apart from memory the submission really used.
    parent_floor = _self_rss() if sys.platform == "linux" else 0
    exec_target = os.path.realpath(exe) if sys.platform == "linux" else None

    start = time.monotonic()
    try:
        pid = os.fork()
    except OSError as exc:
        return RunResult(RunStatus.INTERNAL, None, None, 0, 0, 0, f"fork failed: {exc}")

    if pid == 0:  # ---- child ----
        try:
            os.setpgid(0, 0)
            os.chdir(cwd)
            fd_in = os.open(in_path, os.O_RDONLY)
            fd_out = os.open(out_path, write_flags, 0o644)
            fd_err = os.open(err_path, write_flags, 0o644)
            os.dup2(fd_in, 0)
            os.dup2(fd_out, 1)
            os.dup2(fd_err, 2)
            for fd in (fd_in, fd_out, fd_err):
                if fd > 2:
                    os.close(fd)
            _apply_limits(
                cpu_rlimit,
                memory_limit_bytes if address_space_rlimit else None,
                output_limit_bytes,
            )
            # Drop privileges last, and only after the standard descriptors are
            # already open: the test input lives under a directory the runner
            # account deliberately cannot reach, and an inherited fd sidesteps
            # that. Supplementary groups first, then gid, then uid — reversing
            # the last two would leave the process unable to change group.
            if run_as is not None:
                target_uid, target_gid = run_as
                os.setgroups([])
                os.setgid(target_gid)
                os.setuid(target_uid)
            os.execve(exe, argv, child_env)
        except BaseException:
            os._exit(127)
        os._exit(127)  # unreachable

    # ---- parent ----
    # Both sides set the process group so a kill can never race the exec.
    try:
        os.setpgid(pid, pid)
    except OSError:
        pass

    timed_out = threading.Event()

    def _watchdog() -> None:
        timed_out.set()
        _killpg(pid)

    timer = threading.Timer(wall_limit_s, _watchdog)
    timer.start()
    # Always sample, even with no limit to enforce: it is the only measurement
    # that reflects the submission rather than the judge that launched it.
    monitor = _MemoryMonitor(pid, memory_limit_bytes, exec_target)
    monitor.start()
    try:
        _, status, usage = os.wait4(pid, 0)
    finally:
        timer.cancel()
        monitor.stop()
    wall_ms = int((time.monotonic() - start) * 1000)
    # Sweep up anything the submission spawned and left behind.
    _killpg(pid)

    if os.WIFSIGNALED(status):
        term_signal: int | None = os.WTERMSIG(status)
        exit_code: int | None = None
    else:
        term_signal = None
        exit_code = os.WEXITSTATUS(status)

    cpu_ms = int((usage.ru_utime + usage.ru_stime) * 1000)

    # ru_maxrss is exact but has a floor at whatever the judge itself was using
    # when it forked, because the pre-exec child is a copy of it. Above that
    # floor the number is real and beats sampling, which can miss a short spike;
    # at or below it, the figure says nothing about the submission and only the
    # sampled peak is meaningful.
    reported_rusage = int(usage.ru_maxrss) * RSS_UNIT_BYTES
    if monitor.peak:
        # Sampled after exec, so it describes the submission and nothing else.
        max_rss = max(monitor.peak, reported_rusage if reported_rusage > parent_floor else 0)
    else:
        # Gone before a single sample landed. All that is left is the polluted
        # figure — an overestimate, but reporting 0 MiB would be worse.
        max_rss = reported_rusage

    result = RunResult(
        RunStatus.OK, exit_code, term_signal, wall_ms, cpu_ms, max_rss
    )

    if monitor.exceeded.is_set():
        result.status = RunStatus.MEMORY
        result.detail = "memory limit exceeded"
    elif timed_out.is_set() or term_signal == signal.SIGXCPU:
        result.status = RunStatus.TIMEOUT
        result.detail = "wall-clock limit exceeded" if timed_out.is_set() else "CPU limit exceeded"
    elif term_signal == signal.SIGXFSZ:
        result.status = RunStatus.OUTPUT
        result.detail = "output limit exceeded"
    elif memory_limit_bytes is not None and max_rss >= memory_limit_bytes:
        result.status = RunStatus.MEMORY
        result.detail = "memory limit exceeded"
    elif term_signal is not None:
        result.status = RunStatus.RUNTIME
        result.detail = f"killed by signal {term_signal} ({_signal_name(term_signal)})"
    elif exit_code == 127:
        result.status = RunStatus.INTERNAL
        result.detail = "could not start the program"
    elif exit_code != 0:
        result.status = RunStatus.RUNTIME
        result.detail = f"exited with code {exit_code}"

    return result
