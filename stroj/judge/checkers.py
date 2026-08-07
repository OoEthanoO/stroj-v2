"""Comparing a submission's output against the expected answer.

Three modes:

``token``  whitespace-insensitive token comparison — the sane default.
``exact``  byte-for-byte, ignoring trailing whitespace on each line and
           trailing blank lines (an editor's final newline should never be a WA).
``float``  like ``token``, but numeric tokens match within an absolute *or*
           relative epsilon.
"""

from __future__ import annotations

_CLIP = 60


class CheckResult:
    __slots__ = ("ok", "message")

    def __init__(self, ok: bool, message: str = "") -> None:
        self.ok = ok
        self.message = message

    def __bool__(self) -> bool:
        return self.ok

    def __repr__(self) -> str:
        return f"CheckResult(ok={self.ok!r}, message={self.message!r})"


def _clip(text: str, limit: int = _CLIP) -> str:
    text = text.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return text if len(text) <= limit else text[:limit] + "…"


def _normalize_exact(text: str) -> list[str]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines = [line.rstrip() for line in lines]
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def _as_float(token: str) -> float | None:
    try:
        value = float(token)
    except ValueError:
        return None
    return value


def _floats_match(expected: float, actual: float, eps: float) -> bool:
    if expected != expected or actual != actual:  # NaN never matches
        return False
    if expected == actual:
        return True
    diff = abs(expected - actual)
    if diff <= eps:
        return True
    scale = max(abs(expected), abs(actual))
    return scale > 0 and diff / scale <= eps


def check_tokens(expected: str, actual: str, eps: float | None = None) -> CheckResult:
    exp = expected.split()
    got = actual.split()
    for i in range(min(len(exp), len(got))):
        a, b = exp[i], got[i]
        if a == b:
            continue
        if eps is not None:
            fa, fb = _as_float(a), _as_float(b)
            if fa is not None and fb is not None and _floats_match(fa, fb, eps):
                continue
        return CheckResult(
            False,
            f"token {i + 1}: expected '{_clip(a)}', got '{_clip(b)}'",
        )
    if len(got) < len(exp):
        return CheckResult(
            False,
            f"output is too short: expected {len(exp)} tokens, got {len(got)}",
        )
    if len(got) > len(exp):
        return CheckResult(
            False,
            f"output is too long: expected {len(exp)} tokens, got {len(got)}"
            f" (first extra: '{_clip(got[len(exp)])}')",
        )
    return CheckResult(True)


def check_exact(expected: str, actual: str) -> CheckResult:
    exp = _normalize_exact(expected)
    got = _normalize_exact(actual)
    for i in range(min(len(exp), len(got))):
        if exp[i] != got[i]:
            return CheckResult(
                False,
                f"line {i + 1}: expected '{_clip(exp[i])}', got '{_clip(got[i])}'",
            )
    if len(got) < len(exp):
        return CheckResult(
            False, f"output is too short: expected {len(exp)} lines, got {len(got)}"
        )
    if len(got) > len(exp):
        return CheckResult(
            False, f"output is too long: expected {len(exp)} lines, got {len(got)}"
        )
    return CheckResult(True)


def check(expected: str, actual: str, mode: str = "token", eps: float = 1e-6) -> CheckResult:
    if mode == "exact":
        return check_exact(expected, actual)
    if mode == "float":
        return check_tokens(expected, actual, eps=eps)
    if mode == "token":
        return check_tokens(expected, actual)
    raise ValueError(f"unknown checker: {mode}")


CHECKERS = ("token", "exact", "float")
