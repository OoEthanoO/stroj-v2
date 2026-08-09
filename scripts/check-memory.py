#!/usr/bin/env python3
"""Report what the memory sampler actually sees, on this machine.

    docker exec stroj-judge python /app/scripts/check-memory.py

Memory is measured by sampling the child's resident size after it has
`execve`'d. Whether that works depends on the kernel, the container's
capabilities, and the account the submission runs as — none of which can be
checked from a developer's laptop. This prints the raw numbers so the answer
comes from the judge itself rather than from a guess.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stroj.judge import sandbox  # noqa: E402


def main() -> int:
    box = Path(tempfile.mkdtemp())
    (box / "in").write_text("2 3\n")

    print(f"platform          {sys.platform}")
    print(f"judge's own RSS   {sandbox._self_rss() / 1048576:.1f} MiB")
    print(f"privilege drop    {sandbox.privilege_drop_target()}")
    print(f"isolation         {sandbox.isolation_mode()}\n")

    programs: list[tuple[str, list[str]]] = [
        ("python3, does nothing", [sys.executable, "-c", "pass"]),
        ("python3, holds 50 MiB", [sys.executable, "-c",
                                   "x=bytearray(50*1024*1024)\nx[::4096]=b'1'*len(x[::4096])"]),
        ("python3, holds 150 MiB", [sys.executable, "-c",
                                    "x=bytearray(150*1024*1024)\nx[::4096]=b'1'*len(x[::4096])"]),
    ]
    cxx = None
    try:
        (box / "m.cpp").write_text(
            "#include <bits/stdc++.h>\nusing namespace std;\n"
            "int main(){long long a,b;cin>>a>>b;cout<<a+b<<'\\n';}")
        subprocess.run(["g++", "-O2", "-std=c++20", "-o", str(box / "ab"),
                        str(box / "m.cpp")], check=True, capture_output=True)
        cxx = [str(box / "ab")]
        programs.insert(0, ("C++ A+B", cxx))
    except Exception as exc:                        # noqa: BLE001
        print(f"(skipping C++: {exc})\n")

    print(f"{'program':26} {'reported':>10}   what it should be about")
    expected = {
        "C++ A+B": "2-5 MiB",
        "python3, does nothing": "8-15 MiB",
        "python3, holds 50 MiB": "60-70 MiB",
        "python3, holds 150 MiB": "160-175 MiB",
    }
    worked = True
    for label, argv in programs:
        result = sandbox.run(
            argv, cwd=str(box), stdin_path=str(box / "in"),
            stdout_path=str(box / "o"), stderr_path=str(box / "e"),
            wall_limit_s=30, run_as=sandbox.privilege_drop_target(),
        )
        mib = result.max_rss_bytes / 1048576
        print(f"  {label:24} {mib:>7.1f} MiB   {expected.get(label, '')}")
        if mib <= 0.5:
            worked = False

    print()
    if worked:
        print("Sampling is working: the figures track what each program holds.")
    else:
        print("Something read as ~0 MiB, so sampling is NOT working here.")
        print("Send this output back and it will say why.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
