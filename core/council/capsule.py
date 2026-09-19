"""Per-seat context capsules — the context-isolation boundary.

A seat is never handed "the conversation" or another seat's output. It is
handed a frozen :class:`ContextCapsule` whose contents were snapshotted when
the capsule was built, and the capsule renderer can only ever emit fields the
capsule itself carries. There is no API by which a seat's prompt could come to
contain a peer's answer: the runtime holds peer answers in structures the
capsule builder does not read, and R1 capsules are built BEFORE any model is
invoked (snapshot semantics), so even a sequentially-later seat's capsule
cannot contain an earlier seat's answer — it was frozen before that answer
existed.

This mirrors core/context_namespace.py: access to material outside one's own
scope requires an explicit grant carried IN the capsule as a typed material,
never ambient visibility.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.council.seats import Role


@dataclass(frozen=True)
class KernelFact:
    """A mechanically proven fact (tool receipt / arithmetic). Sealed content.

    Kernel facts are rendered VERBATIM into every capsule and into the committed
    answer. No role — judge included — may restate them differently; the commit
    guard enforces byte-identity. This is the VOOL kernel Law 2 boundary applied
    to council adjudication.
    """

    fact_id: str
    text: str
    source: str  # e.g. "tool:machine.list_directory#r-1"

    def __post_init__(self) -> None:
        if not str(self.fact_id or "").strip() or not str(self.text or "").strip():
            raise ValueError("KernelFact needs non-empty fact_id and text")


@dataclass(frozen=True)
class TaskInput:
    """The user task plus its provenance-carrying grounding."""

    task_text: str
    kernel_facts: tuple[KernelFact, ...] = ()
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class CapsuleMaterial:
    """One explicitly-granted item of shared material.

    ``kind`` is a closed vocabulary so tests and audits can assert exactly what
    leaked where. Author identity is deliberately absent: debate/adjudication
    materials carry ANONYMIZED candidate labels, so no judge or voter knows
    which model produced which claim (blindness extends past R1).
    """

    kind: str          # "candidate" | "dispute_brief" | "ballot_sheet" | "challenge_record" | "advisory_note"
    ref: str           # stable id (cand-N / dispute id / record id)
    body: str


@dataclass(frozen=True)
class ContextCapsule:
    """Everything ONE seat sees for ONE phase. Frozen at build time."""

    council_name: str
    seat_id: str
    role: Role
    phase: str
    task_text: str
    kernel_facts: tuple[KernelFact, ...] = ()
    materials: tuple[CapsuleMaterial, ...] = ()

    def render_prompt(self) -> str:
        """Deterministic rendering. Only capsule fields can appear here."""
        lines = [
            f"[council:{self.council_name} phase:{self.phase}]",
            f"You are seat {self.seat_id} (role: {self.role.value}).",
            "",
            "TASK:",
            self.task_text,
        ]
        if self.kernel_facts:
            lines += ["", "VERIFIED FACTS (cite verbatim, never alter):"]
            lines += [f"- {f.text}" for f in self.kernel_facts]
        for m in self.materials:
            lines += ["", f"[{m.kind} {m.ref}]", m.body]
        return "\n".join(lines)


__all__ = ["CapsuleMaterial", "ContextCapsule", "KernelFact", "TaskInput"]
