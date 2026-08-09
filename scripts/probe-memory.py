#!/usr/bin/env python3
"""Which measurement can see a three-millisecond program?

    ssh stroj-judge 'docker exec -i stroj-judge python -' < scripts/probe-memory.py

Standalone on purpose: imports nothing from `stroj`, so it measures the machine
rather than whatever version is deployed on it.

Sampling from outside has now failed three ways — the sampler waiting on a
close-on-exec pipe, busy-looping with no sleep, and watching for the `VmHWM`
reset. Each time a C++ A+B got zero reads, because creating the thread and
opening /proc costs more than the program's whole life. So this compares:

  A  a sampler thread started *before* the fork, already spinning, so the only
     cost left is the read itself
  B  a small C helper that forks the program and reports its `ru_maxrss` —
     clean, because the helper holds a megabyte rather than the judge's fourteen
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

HELPER_C = r"""
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/resource.h>
#include <sys/wait.h>
#include <unistd.h>

/* fork, exec argv[1..], wait, print the child's peak RSS in KiB on stderr. */
int main(int argc, char **argv) {
    if (argc < 2) return 2;
    pid_t pid = fork();
    if (pid < 0) return 3;
    if (pid == 0) { execv(argv[1], argv + 1); _exit(127); }
    int status; struct rusage ru;
    if (wait4(pid, &status, 0, &ru) < 0) return 4;
    fprintf(stderr, "STROJ_MAXRSS %ld\n", (long)ru.ru_maxrss);
    if (WIFSIGNALED(status)) raise(WTERMSIG(status));
    return WIFEXITED(status) ? WEXITSTATUS(status) : 1;
}
"""


def runner_ids():
    try:
        e = pwd.getpwnam("stroj-runner")
        return e.pw_uid, e.pw_gid
    except KeyError:
        return None


def vmhwm(pid: int) -> int:
    try:
        with open(f"/proc/{pid}/status", "rb") as fh:
            for line in fh:
                if line.startswith(b"VmHWM:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return 0


def approach_a(argv, stdin_path, run_as):
    """Sampler already spinning before the fork happens."""
    box = {"pid": 0, "peak": 0, "reads": 0, "stop": False}

    def spin():
        while not box["stop"]:
            pid = box["pid"]
            if not pid:
                continue
            value = vmhwm(pid)
            if value:
                box["reads"] += 1
                box["peak"] = max(box["peak"], value)

    thread = threading.Thread(target=spin, daemon=True)
    thread.start()
    time.sleep(0.05)                      # let it get hot before forking

    pid = os.fork()
    if pid == 0:
        try:
            fd = os.open(stdin_path, os.O_RDONLY); os.dup2(fd, 0)
            null = os.open(os.devnull, os.O_WRONLY); os.dup2(null, 1); os.dup2(null, 2)
            if run_as:
                os.setgroups([]); os.setgid(run_as[1]); os.setuid(run_as[0])
            os.execv(argv[0], argv)
        except BaseException:
            os._exit(127)
    box["pid"] = pid
    _, status, _ = os.wait4(pid, 0)
    box["stop"] = True
    thread.join(timeout=1)
    ran = not os.WIFSIGNALED(status) and os.waitstatus_to_exitcode(status) == 0
    return box["peak"], box["reads"], ran


def approach_b(helper, argv, stdin_path, run_as):
    """The helper reports the program's own ru_maxrss. Returns (bytes, note)."""
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        try:
            os.close(read_fd)
            fd = os.open(stdin_path, os.O_RDONLY); os.dup2(fd, 0)
            null = os.open(os.devnull, os.O_WRONLY); os.dup2(null, 1)
            os.dup2(write_fd, 2)
            if run_as:
                os.setgroups([]); os.setgid(run_as[1]); os.setuid(run_as[0])
            os.execv(helper, [helper] + argv)
        except BaseException:
            os._exit(126)
    os.close(write_fd)
    chunks = []
    while True:
        block = os.read(read_fd, 4096)
        if not block:
            break
        chunks.append(block)
    os.close(read_fd)
    _, status, _ = os.wait4(pid, 0)
    text = b"".join(chunks)
    for line in text.split(b"\n"):
        if line.startswith(b"STROJ_MAXRSS"):
            return int(line.split()[1]) * 1024, "ok"
    code = os.waitstatus_to_exitcode(status) if not os.WIFSIGNALED(status) else -os.WTERMSIG(status)
    return 0, f"exit {code}, stderr {text[:120]!r}"


def main() -> int:
    ids = runner_ids()
    print(f"judge RSS now      {vmhwm(os.getpid()) / 1048576:.1f} MiB (peak)")
    print(f"runner account     {ids}\n")

    # /tmp is mounted noexec in the judge's container, so a binary built there
    # cannot run at all — and a program that never starts looks exactly like a
    # program too fast to sample. Find somewhere that actually permits exec,
    # and prove it before measuring anything.
    box = None
    for candidate in ("/data/work", "/data", os.environ.get("STROJ_DATA", ""), "/tmp"):
        if not candidate or not os.path.isdir(candidate):
            continue
        try:
            trial = Path(tempfile.mkdtemp(dir=candidate))
        except OSError:
            continue
        probe_bin = trial / "t.sh"
        probe_bin.write_text("#!/bin/sh\nexit 7\n")
        os.chmod(probe_bin, 0o755)
        try:
            if subprocess.run([str(probe_bin)]).returncode == 7:
                box = trial
                print(f"work directory      {candidate} (exec allowed)")
                break
        except OSError as exc:
            print(f"  {candidate}: cannot exec ({exc})")
    if box is None:
        print("no directory on this machine allows exec; cannot measure")
        return 1
    os.chmod(box, 0o777)
    (box / "in").write_text("2 3\n")
    (box / "m.cpp").write_text(
        "#include <iostream>\n"
        "int main(){long long a,b;std::cin>>a>>b;std::cout<<a+b<<'\\n';}")
    subprocess.run(["g++", "-O2", "-o", str(box / "ab"), str(box / "m.cpp")],
                   check=True, capture_output=True)
    (box / "h.c").write_text(HELPER_C)
    helper = str(box / "helper")
    try:
        subprocess.run(["gcc", "-O2", "-o", helper, str(box / "h.c")],
                       check=True, capture_output=True)
    except Exception as exc:                                  # noqa: BLE001
        print(f"could not build the helper: {exc}")
        helper = None
    for path in ("ab", "helper"):
        target = box / path
        if target.exists():
            os.chmod(target, 0o755)

    programs = [
        ("C++ A+B (about 2 ms)", [str(box / "ab")], "2-5"),
        ("python3, does nothing", [sys.executable, "-c", "pass"], "8-15"),
        ("python3, holds 50 MiB", [sys.executable, "-c",
            "x=bytearray(50*1024*1024)\nx[::4096]=b'1'*len(x[::4096])"], "58-70"),
    ]
    if helper:
        plain = subprocess.run([helper, str(box / "ab")], input=b"2 3\n",
                               capture_output=True)
        print(f"helper on its own: exit {plain.returncode}, "
              f"stderr {plain.stderr[:80]!r}\n")

    print(f"{'program':24} {'A: spin':>9} {'reads':>7} {'B: helper':>11}   expected")
    notes = []
    for label, argv, want in programs:
        peak, reads, ran = approach_a(argv, str(box / "in"), ids)
        via, note = approach_b(helper, argv, str(box / "in"), ids) if helper else (0, "no helper")
        print(f"  {label:22} {peak/1048576:>8.2f} {reads:>7} {via/1048576:>10.2f}   "
              f"{want} MiB{'' if ran else '   <-- DID NOT RUN'}")
        if note != "ok":
            notes.append(f"  {label}: {note}")
    if notes:
        print("\nwhy the helper returned nothing:")
        print("\n".join(notes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
