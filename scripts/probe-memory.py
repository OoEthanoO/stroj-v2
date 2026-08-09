#!/usr/bin/env python3
"""Answer, on the judge itself, how a short-lived program should be measured.

    ssh stroj-judge 'docker exec -i stroj-judge python -' < scripts/probe-memory.py

Deliberately standalone: it imports nothing from `stroj`, so it measures this
machine rather than whatever version happens to be deployed on it. Everything
here mirrors what the sandbox does — fork, drop to the runner account, exec —
and then reports what each candidate source of truth actually returns.
"""
from __future__ import annotations

import os
import pwd
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

RUNNER = "stroj-runner"


def runner_ids():
    try:
        entry = pwd.getpwnam(RUNNER)
        return entry.pw_uid, entry.pw_gid
    except KeyError:
        return None


def read_field(pid: int, key: bytes) -> int:
    """A line out of /proc/<pid>/status, in bytes."""
    try:
        with open(f"/proc/{pid}/status", "rb") as fh:
            for line in fh:
                if line.startswith(key):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return 0


def read_statm(pid: int) -> int:
    try:
        with open(f"/proc/{pid}/statm", "rb") as fh:
            return int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, IndexError, ValueError):
        return 0


def measure(argv, stdin_path, run_as):
    """Fork/exec exactly as the sandbox does, sampling every source at once."""
    seen = {"hwm": 0, "statm": 0, "exe_readable": None, "samples": 0}
    exec_r, exec_w = os.pipe()

    pid = os.fork()
    if pid == 0:
        try:
            os.close(exec_r)
            fd = os.open(stdin_path, os.O_RDONLY)
            os.dup2(fd, 0)
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, 1)
            os.dup2(devnull, 2)
            if run_as:
                os.setgroups([])
                os.setgid(run_as[1])
                os.setuid(run_as[0])
            os.execv(argv[0], argv)
        except BaseException:
            os._exit(127)

    os.close(exec_w)

    def sample():
        # Block until execve closes the child's end of the pipe.
        try:
            while os.read(exec_r, 1):
                pass
        except OSError:
            pass
        # Can we see what it is running? This is what the old gate needed.
        try:
            os.readlink(f"/proc/{pid}/exe")
            seen["exe_readable"] = True
        except OSError as exc:
            seen["exe_readable"] = f"no ({exc.errno})"
        while not done.is_set():
            hwm, cur = read_field(pid, b"VmHWM:"), read_statm(pid)
            if hwm or cur:
                seen["samples"] += 1
            seen["hwm"] = max(seen["hwm"], hwm)
            seen["statm"] = max(seen["statm"], cur)
            done.wait(0.0005)

    done = threading.Event()
    thread = threading.Thread(target=sample, daemon=True)
    thread.start()
    start = time.monotonic()
    _, _, usage = os.wait4(pid, 0)
    wall = (time.monotonic() - start) * 1000
    done.set()
    thread.join(timeout=1)
    os.close(exec_r)
    return seen, int(usage.ru_maxrss) * 1024, wall


def main() -> int:
    ids = runner_ids()
    floor = read_field(os.getpid(), b"VmRSS:")
    print(f"judge RSS now      {floor / 1048576:.1f} MiB")
    print(f"runner account     {ids}\n")

    box = Path(tempfile.mkdtemp())
    os.chmod(box, 0o777)
    (box / "in").write_text("2 3\n")
    (box / "m.cpp").write_text(
        "#include <iostream>\n"
        "int main(){long long a,b;std::cin>>a>>b;std::cout<<a+b<<'\\n';}")
    subprocess.run(["g++", "-O2", "-o", str(box / "ab"), str(box / "m.cpp")],
                   check=True, capture_output=True)
    os.chmod(box / "ab", 0o755)

    programs = [
        ("C++ A+B (about 2 ms)", [str(box / "ab")]),
        ("python3, does nothing", [sys.executable, "-c", "pass"]),
        ("python3, holds 50 MiB", [sys.executable, "-c",
                                   "x=bytearray(50*1024*1024)\nx[::4096]=b'1'*len(x[::4096])"]),
    ]
    print(f"{'program':24} {'VmHWM':>9} {'statm':>9} {'ru-floor':>9} {'wall':>7} {'samples':>8}")
    for label, argv in programs:
        seen, rusage, wall = measure(argv, str(box / "in"), ids)
        print(f"  {label:22} {seen['hwm']/1048576:>8.2f} {seen['statm']/1048576:>8.2f} "
              f"{max(0, rusage - floor)/1048576:>8.2f} {wall:>6.0f}ms {seen['samples']:>8}")
        print(f"    /proc/<pid>/exe readable while it ran: {seen['exe_readable']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
