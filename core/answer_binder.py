"""Check that an answer only claims what the tools actually produced.

Deterministic and model-free. It runs after the tools, never before, and it can only ever demote
an answer — it cannot withhold a tool or change what the model was offered. That direction matters:
a check that decides capability from text ahead of the model is the bug class this codebase already
paid for once.

The failure it exists to catch, measured 2026-07-28. Folder `~/Desktop/vool-w5x1` held exactly
`iota.rb`, `tau.sql`, `upsilon.md`. `machine.list_directory` ran and returned all three. The answer:

    "The directory ~/Desktop/vool-w5x1 contains a collection of files and folders related to
     VOOL and OpenClaw runtime behavior. These include configurati..."

Right folder, real tool output, entirely invented contents. Note what that shape defeats: it names
no wrong filename, so a check that only hunts for invented names finds nothing, and it shares plenty
of vocabulary with the request, so `core/model_output_guard.is_ungrounded` — a bag-of-words
disjointness test — passes it. What catches it is the opposite question: the tool returned three
names and the answer quotes none of them while describing the contents.

**Fails open, deliberately.** No records, no target, nothing extractable — it passes. A verification
layer that cannot tell must not block; the cost of a false positive here is a correct answer
withheld from the user, which is worse than the fabrication it would have prevented.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from core import execution_records

# A filename as a human writes one: a stem, a dot, a short alphanumeric extension. Deliberately
# narrow — "e.g." and "3.5" must not read as filenames, or ordinary prose trips the check.
_FILENAME_RE = re.compile(r"\b([A-Za-z0-9_][\w\-.]{0,63}\.[A-Za-z][A-Za-z0-9]{0,5})\b")

# A path the answer asserts. Absolute, home-relative, or an explicit ./ — a bare word is not a path.
_PATH_RE = re.compile(r"(?:(?<=\s)|^|[`'\"(])((?:~|\.{1,2})?/[\w\-./ ]{1,200}?)(?=[`'\")\s,;:.]|$)")

# Extensions that appear in prose often enough to be noise rather than a claimed file.
_PROSE_EXTENSIONS = frozenset({"e.g", "i.e", "etc", "vs", "no", "com", "org", "net", "io", "ai"})

# Verbs that turn a sentence into a claim ABOUT the contents, rather than a plan or a question.
_CONTENT_CLAIM_MARKERS = (
    "contains",
    "contain ",
    "holds",
    "includes",
    "consists of",
    "there are",
    "there is",
    "you have",
    "i found",
    "i see",
    "the files",
    "these include",
)


@dataclass(frozen=True)
class BindingIssue:
    kind: str
    detail: str


@dataclass(frozen=True)
class BindingResult:
    ok: bool = True
    checked: bool = False
    issues: tuple[BindingIssue, ...] = ()
    targets: tuple[str, ...] = field(default_factory=tuple)
    item_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checked": self.checked,
            "issues": [{"kind": i.kind, "detail": i.detail} for i in self.issues],
            "targets": list(self.targets),
            "item_count": self.item_count,
        }


def _normalise_path(value: str) -> str:
    text = str(value or "").strip().strip("`'\"").rstrip("/")
    # Tools label targets home-relative ("~/Desktop/x") while an answer may spell the same place
    # absolutely. Compare on the tail so the two forms agree without resolving anything on disk.
    if text.startswith("~"):
        text = text[1:]
    return text


def _segments(value: str) -> tuple[str, ...]:
    return tuple(p for p in _normalise_path(value).split("/") if p)


def _is_related(answer_path: str, target: str) -> bool:
    """Whether a path in the answer refers to the same place a tool touched, or its neighbourhood.

    Related means one is an ancestor of the other, compared on path segments. Both directions are
    legitimate: an answer may name a descendant it saw in a listing, or the parent it was found
    under ("Under ~/Desktop, the folder vool-w5x1 holds ..."). Flagging a parent was a real false
    positive — the kind that makes a checker useless because correct answers get blocked.

    Segments are compared from the tail, so the home-relative label a tool reports
    (`~/Desktop/x`) matches the absolute spelling an answer may use (`/Users/me/Desktop/x`).
    """

    left, right = _segments(answer_path), _segments(target)
    if not left or not right:
        return False
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    return longer[-len(shorter):] == shorter or longer[: len(shorter)] == shorter


def _paths_in(text: str) -> tuple[str, ...]:
    found = {m.group(1).strip() for m in _PATH_RE.finditer(text)}
    return tuple(p for p in found if len(_normalise_path(p).strip("/")) > 1)


def _filenames_in(text: str) -> tuple[str, ...]:
    names = set()
    for match in _FILENAME_RE.finditer(text):
        candidate = match.group(1)
        stem, _, extension = candidate.rpartition(".")
        if not stem or extension.lower() in _PROSE_EXTENSIONS:
            continue
        if candidate.lower() in _PROSE_EXTENSIONS:
            continue
        names.add(candidate)
    return tuple(names)


def _makes_a_content_claim(answer: str) -> bool:
    lowered = answer.lower()
    return any(marker in lowered for marker in _CONTENT_CLAIM_MARKERS)


def check(
    answer: str,
    *,
    session_id: str,
    user_input: str = "",
) -> BindingResult:
    """Compare an answer against what this session's tools actually returned."""

    text = str(answer or "").strip()
    records = execution_records.records_for(session_id)
    if not text or not records:
        return BindingResult(ok=True, checked=False)

    targets = tuple(r.resolved_target for r in records if r.has_target)
    every_item = {name for r in records for name in r.items}
    # Anything the user named themselves is theirs to name; the answer echoing it is not a claim
    # the tools have to support. Without this, "did you find notes.md?" makes every reply suspect.
    user_names = set(_filenames_in(user_input)) | set(_paths_in(user_input))
    issues: list[BindingIssue] = []

    if targets:
        for path in _paths_in(text):
            if path in user_names:
                continue
            if not _segments(path):
                continue
            if any(_is_related(path, target) for target in targets):
                continue
            issues.append(
                BindingIssue("unbound_target", f"answer names {path!r}; tools ran on {sorted(targets)!r}")
            )

    if every_item:
        for name in _filenames_in(text):
            if name in every_item or name in user_names:
                continue
            if any(name in item or item.endswith("/" + name) for item in every_item):
                continue
            issues.append(
                BindingIssue("unbound_item", f"answer names {name!r}, which no tool returned")
            )

    # The check the measured fabrication needs. A tool returned names; the answer describes the
    # contents and quotes none of them. Only fires when there is something to quote and the answer
    # actually asserts what is there — a summary that stays general is not a false claim.
    if every_item and _makes_a_content_claim(text):
        quoted = [n for n in every_item if n in text]
        if not quoted:
            issues.append(
                BindingIssue(
                    "uncited_contents",
                    f"answer describes contents but names none of the {len(every_item)} returned "
                    f"item(s); e.g. {sorted(every_item)[:3]!r}",
                )
            )

    return BindingResult(
        ok=not issues,
        checked=True,
        issues=tuple(issues),
        targets=targets,
        item_count=len(every_item),
    )


def deterministic_rendering(session_id: str) -> str:
    """What the tools actually found, rendered without a model.

    The fallback when a regenerated answer fails binding twice: a plain listing is a worse answer
    than a good prose one and a far better answer than a confident invention.
    """

    lines: list[str] = []
    for entry in execution_records.records_for(session_id):
        if not entry.ok or not entry.items:
            continue
        where = entry.resolved_target or entry.intent
        lines.append(f"{where}:")
        lines.extend(f"- {name}" for name in entry.items)
    return "\n".join(lines)


__all__ = ["BindingIssue", "BindingResult", "check", "deterministic_rendering"]
