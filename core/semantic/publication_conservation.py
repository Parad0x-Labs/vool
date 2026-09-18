"""Publication conservation: every terminal SlotID is VISIBLE in the rendered answer.

Slot reconciliation (``slot_reconciliation``) proves every frozen SlotID reached a terminal
disposition. That is necessary but not sufficient: the Astra review showed a slot can be terminally
``indeterminate``/``blocked``/``unknown`` and then vanish from the published answer — structural
closure coexisting with a missing visible answer. This layer closes that gap at the render boundary.

The law: for every SlotID the reconciliation terminated, the published answer must contain a VISIBLE
representation of its disposition — an answer for ANSWERED, and an explicit "unavailable / blocked /
needs input / …" line for the rest. This must hold for prose, tables, JSON, literal output and
artifacts alike: a structured-output request may not hide a missing slot. The renderer reports which
SlotIDs it actually surfaced (``PublishedSlot``); this refuses to call the answer complete if any
terminal slot was dropped or rendered invisibly.

Nothing here renders — the renderer owns layout and may not add or drop slots. This only verifies
that what was rendered accounts, visibly, for every terminal obligation.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.semantic.request_graph import SlotId
from core.semantic.slot_reconciliation import Reconciliation


class PublicationConservationError(ValueError):
    """A rendered answer was declared complete while a terminal SlotID was omitted or hidden."""


@dataclass(frozen=True)
class PublishedSlot:
    """How one SlotID appears in the rendered answer, as the renderer reports it. ``visible`` is
    False when the slot was emitted structurally but not shown (e.g. a JSON key suppressed, a table
    row dropped) — which conservation treats the same as omission."""

    slot_id: SlotId
    visible: bool = True
    rendering: str = ""          # the surfaced text/cell/field, for evidence


@dataclass(frozen=True)
class PublicationVerdict:
    conserved: bool
    omitted: tuple[SlotId, ...] = field(default_factory=tuple)   # terminal slots with no rendering
    hidden: tuple[SlotId, ...] = field(default_factory=tuple)    # rendered but visible=False / empty
    invented: tuple[SlotId, ...] = field(default_factory=tuple)  # published ids no terminal slot owns

    def violations(self) -> tuple[str, ...]:
        out: list[str] = []
        if self.omitted:
            out.append(f"terminal slots omitted from the published answer: {list(self.omitted)}")
        if self.hidden:
            out.append(f"terminal slots rendered but not visible: {list(self.hidden)}")
        if self.invented:
            out.append(f"published slots no terminal result owns: {list(self.invented)}")
        return tuple(out)


def verify_publication(
    reconciliation: Reconciliation, published: tuple[PublishedSlot, ...]
) -> PublicationVerdict:
    """Report whether every terminal SlotID is visibly published. Never raises.

    Requires a CONSERVED reconciliation as its input universe — publication conservation is only
    meaningful once the terminal set is itself conserved. If the reconciliation was not conserved,
    that is surfaced as the primary violation (there is no valid terminal set to publish)."""
    terminal = frozenset(r.slot_id for r in reconciliation.results)
    # A row that claims visibility must SHOW something: an empty rendering is a label, not a
    # published answer, and counts as hidden. (A visible flag alone certified nothing.)
    visible = {p.slot_id for p in published if p.visible and str(p.rendering or "").strip()}
    present_any = {p.slot_id for p in published}
    omitted = tuple(sorted(terminal - present_any))
    hidden = tuple(sorted(terminal - visible - set(omitted)))
    invented = tuple(sorted(present_any - terminal))
    conserved = reconciliation.conserved and not omitted and not hidden and not invented
    return PublicationVerdict(conserved=conserved, omitted=omitted, hidden=hidden, invented=invented)


def require_published(
    reconciliation: Reconciliation, published: tuple[PublishedSlot, ...]
) -> PublicationVerdict:
    """Fail-closed gate: raise unless every terminal SlotID is visibly published (and the terminal
    set was itself conserved). A finalization/publication boundary calls this before shipping."""
    if not reconciliation.conserved:
        raise PublicationConservationError(
            "cannot publish an unconserved terminal set: " + "; ".join(reconciliation.violations())
        )
    verdict = verify_publication(reconciliation, published)
    if not verdict.conserved:
        raise PublicationConservationError("; ".join(verdict.violations()))
    return verdict


__all__ = [
    "PublicationConservationError",
    "PublicationVerdict",
    "PublishedSlot",
    "require_published",
    "verify_publication",
]
