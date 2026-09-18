"""Strict semver for the signed-manifest update lane.

`core.self_update_check.parse_version` is best-effort display logic for GitHub tags; the
signed lane needs real ordering rules with failure that is loud, not a silent (0,0,0):

  MAJOR.MINOR.PATCH[-prerelease][+build]

Prerelease ordering follows semver 2.0.0 §11: a prerelease sorts BELOW the release with
the same triple; dot-separated identifiers compare numerically when both are numeric and
lexically otherwise; numeric identifiers sort below lexical ones. Build metadata never
affects ordering.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_VERSION_RE = re.compile(
    r"^v?(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<pre>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


def _prerelease_key(prerelease: tuple[str | int, ...]) -> tuple:
    # semver §11: numeric identifiers have LOWER precedence than lexical ones; encode that
    # with a (0, int) vs (1, str) tag so mixed tuples stay comparable.
    return tuple((0, part) if isinstance(part, int) else (1, part) for part in prerelease)


@dataclass(frozen=True, eq=False)
class Semver:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str | int, ...] = ()
    build: str = ""

    def __str__(self) -> str:  # pragma: no cover - display only
        base = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            base += "-" + ".".join(str(p) for p in self.prerelease)
        if self.build:
            base += "+" + self.build
        return base

    def _comparable(self) -> tuple:
        # A release (empty prerelease) sorts ABOVE any prerelease of the same triple.
        # Build metadata is deliberately absent: semver precedence ignores it.
        release_rank = 1 if not self.prerelease else 0
        return (self.major, self.minor, self.patch, release_rank, _prerelease_key(self.prerelease))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Semver):
            return NotImplemented
        return self._comparable() == other._comparable()

    def __hash__(self) -> int:
        return hash(self._comparable())

    def __lt__(self, other: Semver) -> bool:
        return self._comparable() < other._comparable()

    def __le__(self, other: Semver) -> bool:
        return self._comparable() <= other._comparable()

    def __gt__(self, other: Semver) -> bool:
        return self._comparable() > other._comparable()

    def __ge__(self, other: Semver) -> bool:
        return self._comparable() >= other._comparable()

    @property
    def is_prerelease(self) -> bool:
        return bool(self.prerelease)


def parse_semver(text: str) -> Semver:
    """Parse a strict semver string (a leading `v` is tolerated for release tags; surrounding
    whitespace is NOT). Raises ValueError — an unparsable release version is a hard defect in
    the manifest, never a quiet (0,0,0)."""
    match = _VERSION_RE.match(str(text or ""))
    if not match:
        raise ValueError(f"not a valid semver string: {text!r}")
    prerelease: tuple[str | int, ...] = ()
    raw_pre = match.group("pre")
    if raw_pre is not None:
        parts: list[str | int] = []
        for part in raw_pre.split("."):
            if part.isdigit():
                parts.append(int(part))
            else:
                parts.append(part)
        prerelease = tuple(parts)
    return Semver(
        major=int(match.group("major")),
        minor=int(match.group("minor")),
        patch=int(match.group("patch")),
        prerelease=prerelease,
        build=match.group("build") or "",
    )


def is_strictly_newer(candidate: str, installed: str) -> bool:
    return parse_semver(candidate) > parse_semver(installed)


__all__ = ["Semver", "is_strictly_newer", "parse_semver"]
