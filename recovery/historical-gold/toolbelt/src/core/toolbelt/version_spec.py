"""Minimal version-constraint evaluation: ">=3.12,<3.13", "==22", ">2.40" etc."""
from __future__ import annotations

import re

_CLAUSE = re.compile(r"^\s*(>=|<=|==|!=|>|<)\s*([0-9]+(?:\.[0-9]+)*)\s*$")


def _parse(version: str) -> tuple[int, ...]:
    parts = []
    for chunk in str(version or "").strip().split("."):
        digits = re.match(r"^(\d+)", chunk)
        if not digits:
            break
        parts.append(int(digits.group(1)))
    return tuple(parts or [0])


def _cmp(a: tuple[int, ...], b: tuple[int, ...]) -> int:
    width = max(len(a), len(b))
    a += (0,) * (width - len(a))
    b += (0,) * (width - len(b))
    return (a > b) - (a < b)


def satisfies(version: str | None, spec: str) -> bool:
    """True iff ``version`` meets every comma-separated clause of ``spec``.

    A missing/unparsable version fails closed (False).
    """
    if version is None:
        return False
    v = _parse(version)
    if v == (0,):
        return False
    for clause in str(spec or "").split(","):
        clause = clause.strip()
        if not clause:
            continue
        m = _CLAUSE.match(clause)
        if not m:
            return False  # unparsable constraint fails closed
        op, ref = m.group(1), _parse(m.group(2))
        c = _cmp(v, ref)
        ok = {
            ">=": c >= 0,
            "<=": c <= 0,
            "==": c == 0,
            "!=": c != 0,
            ">": c > 0,
            "<": c < 0,
        }[op]
        if not ok:
            return False
    return True


__all__ = ["satisfies"]
