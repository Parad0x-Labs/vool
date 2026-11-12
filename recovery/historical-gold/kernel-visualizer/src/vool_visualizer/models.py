"""Typed row models derived 1:1 from the kernel journal + terminal_state.

Kept deliberately thin: every field maps to a raw journal key so the visualizer
never invents facts that are not already durable bytes.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Accepted lane names from the kernel's _LANES (repl.py) — for coloring only.
LANES = frozenset({
    "web_lookup", "arithmetic", "machine", "recall", "intake", "chat",
    "clarify", "knowledge", "compose", "cancelled",
})

_TERMINAL_LABELS = {
    "closed": "closed",
    "open": "open",
    "declared_unanswerable": "declared unanswerable",
    "failed_internal": "FAILED internal",
    "cancelled": "cancelled",
    "refused": "refused",
}


@dataclass(frozen=True)
class Effect:
    """One tape entry. `error` is set (non-None) only for recorded failures."""
    step: int
    effect_id: str
    args_hash: str
    result: object | None = None
    error: dict | None = None
    ts: str = ""

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class Obligation:
    id: str
    lane: str
    status: str
    description: str = ""

    @property
    def label(self) -> str:
        return _TERMINAL_LABELS.get(self.status, self.status)


@dataclass
class Turn:
    turn_index: int
    question: str
    answer: str
    answer_present: bool
    answer_sha256: str
    build: str
    model: str
    run_id: str
    ts: str
    stamps: dict
    effects: list[Effect]
    terminal_state: list[Obligation]
    transcript: str = ""
    crashed: bool = False
    crash_exception: str | None = None
    _commit_line: str = field(default="", init=False, repr=False)

    @property
    def commit(self) -> str:
        """The COMMIT disposition, parsed once from the transcript (raw bytes)."""
        if not self._commit_line:
            self._commit_line = _commit_from_transcript(self.transcript)
        return self._commit_line

    @property
    def web_torn(self) -> bool:
        return not bool(self.stamps.get("web", True))

    @property
    def machine_torn(self) -> bool:
        return not bool(self.stamps.get("machine", True))


def _commit_from_transcript(transcript: str) -> str:
    if not transcript:
        return ""
    for line in transcript.splitlines():
        s = line.strip()
        low = s.lower()
        if "kernel refused commit" in low:
            return "REFUSED COMMIT (partial)"
        if low.startswith("commit:"):
            return s
        if low.startswith("shipped as:"):
            return "SHIPPED AS: " + s.split(":", 1)[1].strip()
    return ""
