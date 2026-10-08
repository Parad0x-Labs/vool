"""Agent names state the task and its importance, never a generic label.

A user reading "agent-2 is paused" learns nothing. A user reading "Login redirect fix · high is
paused" knows what stopped and how much it matters. So the display name is the task title (at
most five words) followed by `` · <importance>``, and a name that says nothing about the task —
``agent``, ``worker 3``, ``B``, ``subagent-1`` — is refused at contract validation rather than
rendered.

Internal ids still exist for the registry; they never reach a user-facing string.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

IMPORTANCE_LEVELS: tuple[str, ...] = ("low", "normal", "high", "critical")
MAX_TITLE_WORDS = 5
SEPARATOR = " · "


class NameRefused(ValueError):
    """A title that does not name a task, or an importance that is not a level."""


_GENERIC_WORDS = {
    "agent", "agents", "worker", "workers", "subagent", "sub-agent", "child", "helper", "bot",
    "task", "job", "assistant", "runner", "thread", "process", "proc", "unit", "node", "seat",
    "member", "instance", "delegate", "sub", "new", "my", "the", "a", "an", "number", "no",
}
_ORDINAL_RE = re.compile(r"^(?:[a-z]|\d+|#\d+|[ivx]+|one|two|three|four|five|alpha|beta|gamma|delta)$")

# Words that carry no task meaning when a title is derived from an objective.
_FILLER = {
    "please", "the", "a", "an", "just", "kindly", "can", "you", "could", "would", "we", "i",
    "need", "needs", "want", "to", "should", "must", "go", "and", "then", "also", "that",
    "this", "it", "so", "of", "our", "my", "some",
}


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[\s_]+", str(text or "").strip()) if w]


def is_generic(title: str) -> bool:
    """True when every word is a generic role/ordinal word: nothing names the task."""
    words = [w.lower().strip(".,:;!?()[]{}'\"-") for w in _words(title)]
    words = [w for w in words if w]
    if not words:
        return True
    for word in words:
        parts = [p for p in re.split(r"[-]", word) if p] or [word]
        for part in parts:
            if part in _GENERIC_WORDS or _ORDINAL_RE.match(part):
                continue
            # agent1, worker02, a1, b
            stem = re.sub(r"\d+$", "", part)
            if stem in _GENERIC_WORDS or (stem and _ORDINAL_RE.match(stem)):
                continue
            return False
    return True


def normalize_importance(value: str | None) -> str:
    clean = str(value or "normal").strip().lower()
    if clean not in IMPORTANCE_LEVELS:
        raise NameRefused(
            f"importance must be one of {', '.join(IMPORTANCE_LEVELS)}; got {value!r}"
        )
    return clean


def raise_importance(level: str, steps: int) -> str:
    """One level up per dependent agent, capped at critical."""
    index = IMPORTANCE_LEVELS.index(normalize_importance(level))
    return IMPORTANCE_LEVELS[min(len(IMPORTANCE_LEVELS) - 1, index + max(0, int(steps)))]


def importance_rank(level: str) -> int:
    return IMPORTANCE_LEVELS.index(normalize_importance(level))


def title_from_objective(objective: str) -> str:
    """A short task title from the objective's first clause: filler dropped, five words max."""
    first = re.split(r"(?<=[.!?;])\s|\n|:\s", str(objective or "").strip(), maxsplit=1)[0]
    kept = [w.strip(".,;:!?\"'()[]") for w in _words(first)]
    kept = [w for w in kept if w and w.lower() not in _FILLER]
    title = " ".join(kept[:MAX_TITLE_WORDS])
    return title[:1].upper() + title[1:]


def validate_title(title: str) -> str:
    clean = " ".join(_words(title))
    if not clean:
        raise NameRefused("an agent needs a task title; the user must be able to tell agents apart")
    if len(_words(clean)) > MAX_TITLE_WORDS:
        raise NameRefused(f"task title {clean!r} is longer than {MAX_TITLE_WORDS} words")
    if is_generic(clean):
        raise NameRefused(
            f"{clean!r} is a generic name; name the task (e.g. 'Login redirect fix') so the "
            "user knows which agent is which"
        )
    return clean


def display_name(title: str, importance: str) -> str:
    return f"{validate_title(title)}{SEPARATOR}{normalize_importance(importance)}"


def area_of(claims: Sequence[str]) -> str:
    """The claimed area used to tell two same-titled agents apart: the first claim, as a dir."""
    for claim in claims or ():
        clean = str(claim or "").strip().strip("/")
        if clean:
            return clean + "/"
    return ""


def assign_display_names(entries: Iterable[tuple[str, str, Sequence[str]]],
                         taken: Iterable[str] = ()) -> list[str]:
    """Display names for ``(title, importance, claims)`` rows, unique among themselves and
    ``taken``. A collision adds the claimed area: ``"Docs update (api/) · low"``; if that still
    collides, a short counter follows the area so names never silently merge."""
    rows = list(entries)
    used = {str(n) for n in taken}
    base = [display_name(t, i) for t, i, _ in rows]
    counts: dict[str, int] = {}
    for name in [*base, *used]:
        counts[name] = counts.get(name, 0) + 1
    result: list[str] = []
    for (title, importance, claims), name in zip(rows, base, strict=True):
        if counts.get(name, 0) > 1 or name in used:
            area = area_of(claims)
            clean_title = validate_title(title)
            candidate = f"{clean_title} ({area}){SEPARATOR}{importance}" if area else name
            n = 2
            while candidate in used:
                suffix = f"{area}, {n}" if area else str(n)
                candidate = f"{clean_title} ({suffix}){SEPARATOR}{importance}"
                n += 1
            name = candidate
        used.add(name)
        result.append(name)
    return result


__all__ = [
    "IMPORTANCE_LEVELS",
    "MAX_TITLE_WORDS",
    "NameRefused",
    "area_of",
    "assign_display_names",
    "display_name",
    "importance_rank",
    "is_generic",
    "normalize_importance",
    "raise_importance",
    "title_from_objective",
    "validate_title",
]
