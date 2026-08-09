"""Supported submission languages.

Every toolchain path can be overridden with an environment variable, so you can
point the judge at ``g++`` instead of ``clang++`` or at a specific JDK without
touching this file.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

#: Ships a <bits/stdc++.h> shim so libc++ toolchains accept the usual
#: competitive-programming preamble.
INCLUDE_DIR = Path(__file__).resolve().parent / "include"

PYTHON = os.environ.get("STROJ_PYTHON", "python3")
CXX = os.environ.get("STROJ_CXX", "clang++")
JAVAC = os.environ.get("STROJ_JAVAC", "javac")
JAVA = os.environ.get("STROJ_JAVA", "java")


@dataclass(frozen=True)
class Language:
    id: str
    name: str
    source_file: str
    run_cmd: tuple[str, ...]
    compile_cmd: tuple[str, ...] | None = None
    #: RLIMIT_AS is the wrong tool for runtimes that reserve a large virtual
    #: arena up front (the JVM); those cap their heap with a flag instead and we
    #: fall back to comparing peak RSS against the limit plus `memory_overhead_mb`.
    limit_address_space: bool = True
    #: Slower runtimes get a proportionally larger share of the problem's limit.
    time_multiplier: float = 1.0
    time_overhead_ms: int = 0
    memory_overhead_mb: int = 0
    editor_mode: str = "text"
    comment_prefix: str = "//"
    binaries: tuple[str, ...] = field(default=())

    def compile_argv(self) -> list[str] | None:
        return list(self.compile_cmd) if self.compile_cmd else None

    def run_argv(self, memory_limit_mb: int) -> list[str]:
        """Build the run command for a given *enforced* memory ceiling.

        A runtime that caps its own heap with a flag must be told something
        below the ceiling it is held to, or it will happily fill the heap and
        then breach the limit with its own overhead on top. `memory_overhead_mb`
        is exactly the allowance made for that overhead, so taking it back off
        is what turns an enforced ceiling into a safe heap.

        This keeps a derived limit behaving as it always did — base plus the
        overhead, minus the overhead, is the base — while letting an explicit
        per-language limit move the heap with it.
        """
        heap = max(32, memory_limit_mb - self.memory_overhead_mb)
        return [arg.format(heap=heap) for arg in self.run_cmd]

    def effective_time_limit_ms(self, base_ms: int) -> int:
        return int(base_ms * self.time_multiplier) + self.time_overhead_ms

    def effective_memory_limit_mb(self, base_mb: int) -> int:
        return base_mb + self.memory_overhead_mb


LANGUAGES: dict[str, Language] = {
    "python3": Language(
        id="python3",
        name="Python 3",
        source_file="main.py",
        # py_compile turns a syntax error into a compile error instead of a
        # runtime error on every single test.
        compile_cmd=(PYTHON, "-B", "-m", "py_compile", "main.py"),
        run_cmd=(PYTHON, "-B", "main.py"),
        time_multiplier=3.0,
        time_overhead_ms=200,
        memory_overhead_mb=32,
        editor_mode="python",
        comment_prefix="#",
        binaries=(PYTHON,),
    ),
    "cpp": Language(
        id="cpp",
        name="C++20 (clang++)",
        source_file="main.cpp",
        compile_cmd=(
            CXX, "-O2", "-pipe", "-std=c++20",
            "-isystem", str(INCLUDE_DIR),
            "-o", "main", "main.cpp",
        ),
        run_cmd=("./main",),
        editor_mode="cpp",
        binaries=(CXX,),
    ),
    "java": Language(
        id="java",
        name="Java 21",
        source_file="Main.java",
        compile_cmd=(JAVAC, "-encoding", "UTF-8", "-d", ".", "Main.java"),
        run_cmd=(
            JAVA,
            "-XX:+UseSerialGC",
            "-XX:-UsePerfData",
            "-Xshare:auto",
            "-Xss64m",
            "-Xms16m",
            "-Xmx{heap}m",
            "-Dfile.encoding=UTF-8",
            "-cp",
            ".",
            "Main",
        ),
        limit_address_space=False,
        time_multiplier=2.0,
        time_overhead_ms=500,
        memory_overhead_mb=320,
        editor_mode="java",
        binaries=(JAVAC, JAVA),
    ),
}

DEFAULT_LANGUAGE = "cpp"

TEMPLATES: dict[str, str] = {
    "python3": "import sys\n\ndef main():\n    data = sys.stdin.read().split()\n    # your code here\n\nmain()\n",
    "cpp": '#include <bits/stdc++.h>\nusing namespace std;\n\nint main() {\n    ios::sync_with_stdio(false);\n    cin.tie(nullptr);\n    // your code here\n    return 0;\n}\n',
    "java": "import java.io.*;\nimport java.util.*;\n\npublic class Main {\n    public static void main(String[] args) throws IOException {\n        // your code here\n    }\n}\n",
}


def get(language_id: str) -> Language:
    try:
        return LANGUAGES[language_id]
    except KeyError:
        raise ValueError(f"unknown language: {language_id}") from None


@lru_cache(maxsize=None)
def is_available(language_id: str) -> bool:
    """True when every binary the language needs is on PATH."""
    lang = LANGUAGES.get(language_id)
    if lang is None:
        return False
    return all(shutil.which(b) is not None for b in lang.binaries)


@lru_cache(maxsize=None)
def version_string(language_id: str) -> str:
    """Best-effort toolchain version, shown on the submit form."""
    lang = LANGUAGES.get(language_id)
    if lang is None or not is_available(language_id):
        return "not installed"
    probes = {
        "python3": [PYTHON, "--version"],
        "cpp": [CXX, "--version"],
        "java": [JAVA, "-version"],
    }
    try:
        proc = subprocess.run(
            probes[language_id], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError, KeyError):
        return "unknown"
    output = (proc.stdout + proc.stderr).strip().splitlines()
    return output[0].strip() if output else "unknown"


def catalog() -> list[dict]:
    """Language list for the API, including per-language limit scaling."""
    return [
        {
            "id": lang.id,
            "name": lang.name,
            "available": is_available(lang.id),
            "version": version_string(lang.id),
            "time_multiplier": lang.time_multiplier,
            "time_overhead_ms": lang.time_overhead_ms,
            "memory_overhead_mb": lang.memory_overhead_mb,
            "editor_mode": lang.editor_mode,
            "template": TEMPLATES.get(lang.id, ""),
        }
        for lang in LANGUAGES.values()
    ]
