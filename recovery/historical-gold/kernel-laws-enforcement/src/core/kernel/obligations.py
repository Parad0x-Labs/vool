"""Law 1 — a turn is a transaction over obligations; answers COMMIT, they don't trail off.

Motivating incident (Fable5 audit, 2026-08-19): a currency fast path was asked four
conversions in one turn (RUB→EUR, USD→GBP, gold, BTC), answered exactly one, and reported
the turn as a success. Nothing in the runtime represented the other three requests, so
nothing could notice they were dropped — "answered" was a vibe, not a checked state.

This module makes that failure class structural rather than merely tested-against: every
request the turn takes on becomes an ``Obligation``, and the turn can only ship through
``TurnTransaction.commit()``, which refuses (``CommitRefused``, naming exactly the open
ids) while any obligation is neither closed with evidence nor explicitly declared
unanswerable with a reason. Shipping with open obligations stays possible — real systems
degrade — but only through ``commit_partial()``, whose result is *labelled* partial and
names every open item by id and description, so "partial" can never masquerade as "done".

Three decisions worth defending:

- **Status words must match reality in both directions.** ``commit()`` with open
  obligations refuses (work understated as done), and ``commit_partial()`` with zero open
  obligations also refuses (done work overstated as partial). The same audit found refusal
  text contradicting the event store; a label allowed to drift from the record in either
  direction is that defect wearing a different word.
- **Finalization seals the obligations themselves**, not only the transaction's methods.
  A manifest computed at commit time over obligations that keep mutating afterwards goes
  stale silently; sealing turns any post-commit mutation into a loud ``RuntimeError``.
- **A refused commit leaves the transaction live.** Refusal is the start of the repair
  loop (close the rest, or declare them, or ship partial) — killing the transaction on
  refusal would force callers to route around the law instead of through it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

_OPEN = "open"
_CLOSED = "closed"
_DECLARED = "declared_unanswerable"
# Consensus 2026-08-20 (review-20260820-035058): crashes and cancellations are NOT
# "unanswerable" — a crash mapped to unanswerable makes Law 1 pass trivially (a turn-22
# AttributeError shipped as "declared unanswerable" over an answer the model had
# already produced), and a user cancellation is a different fact than a gap.
_FAILED = "failed_internal"
_CANCELLED = "cancelled"
# Consensus-2: a missing capability or an illegal terminal (clarify echoing the ask)
# is REFUSED with a named reason — distinct from unanswerable (a world gap) and from
# failed (the system broke). "Unanswerable" was measured being used as a landfill for
# dispatch failures, letting Law 1 pass trivially.
_REFUSED = "refused"


def _require_text(value: object, what: str) -> str:
    """Refuse empty/whitespace/non-str where the contract says 'non-empty'.

    Whitespace counts as empty on purpose: an evidence ref of ``" "`` satisfies a naive
    truthiness check while carrying exactly as much evidence as ``""`` — none.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{what} must be a non-empty string, got {value!r}")
    return value


@dataclass
class Obligation:
    """One request the turn has taken on.

    Born open; leaves ``open`` only through :meth:`close` (with an evidence ref) or
    :meth:`declare_unanswerable` (with a reason). Both transitions are one-way and
    mutually exclusive — an obligation whose status could be rewritten after the fact
    would let a manifest disagree with what actually happened.
    """

    id: str
    description: str
    #: e.g. "answer" | "lookup" | "action" — an open set, but never blank.
    kind: str
    _status: str = field(default=_OPEN, init=False, repr=False)
    _evidence_ref: str | None = field(default=None, init=False, repr=False)
    _reason: str | None = field(default=None, init=False, repr=False)
    _sealed: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        # A blank id would make CommitRefused name nothing; a blank description would
        # make a partial manifest list nothing. Refuse at birth, not at ship time.
        _require_text(self.id, "obligation id")
        _require_text(self.description, "obligation description")
        _require_text(self.kind, "obligation kind")

    @property
    def status(self) -> str:
        return self._status

    @property
    def evidence_ref(self) -> str | None:
        return self._evidence_ref

    @property
    def reason(self) -> str | None:
        return self._reason

    def close(self, evidence_ref: str) -> None:
        """Close with evidence. Refused without a real ref, on any non-open status,
        and after the owning transaction has finalized.

        ROUND-014 ROOT 2 (commit evidence law): an obligation of kind "action" —
        an external side-effect request — may close ONLY on typed execution
        evidence (an ``exec:``-prefixed ref minted by a genuine capability/adapter
        execution). Payload provenance (``user``), authored text (``claims:<id>``),
        model verdicts, and registration are categorically insufficient: generated
        bytes are never proof that a side effect happened."""
        self._refuse_if_sealed("close")
        _require_text(evidence_ref, f"evidence ref for obligation {self.id!r}")
        if self._status != _OPEN:
            raise RuntimeError(
                f"obligation {self.id!r} is {self._status!r}, not open; close() refused"
            )
        if self.kind == "action" and not evidence_ref.startswith("exec:"):
            raise ValueError(
                f"obligation {self.id!r} is an external action and may only close on typed "
                f"execution evidence (exec:...), got {evidence_ref!r} — generated text or "
                "payload provenance is not action proof"
            )
        self._status = _CLOSED
        self._evidence_ref = evidence_ref

    def declare_unanswerable(self, reason: str) -> None:
        """Give up explicitly, with a reason the manifest will carry. Refused without a
        reason, on any non-open status, and after the owning transaction has finalized."""
        self._refuse_if_sealed("declare_unanswerable")
        _require_text(reason, f"unanswerable reason for obligation {self.id!r}")
        if self._status != _OPEN:
            raise RuntimeError(
                f"obligation {self.id!r} is {self._status!r}, not open; "
                "declare_unanswerable() refused"
            )
        self._status = _DECLARED
        self._reason = reason

    def declare_failed(self, reason: str) -> None:
        """An INTERNAL failure — the system broke, the question did not. Distinct from
        unanswerable by consensus: the manifest must say the kernel failed."""
        self._refuse_if_sealed("declare_failed")
        _require_text(reason, f"failure reason for obligation {self.id!r}")
        if self._status != _OPEN:
            raise RuntimeError(
                f"obligation {self.id!r} is {self._status!r}, not open; declare_failed() refused"
            )
        self._status = _FAILED
        self._reason = reason

    def declare_cancelled(self, reason: str) -> None:
        """Retracted by the user — the latest instruction wins. Not a gap, not a failure."""
        self._refuse_if_sealed("declare_cancelled")
        _require_text(reason, f"cancellation reason for obligation {self.id!r}")
        if self._status != _OPEN:
            raise RuntimeError(
                f"obligation {self.id!r} is {self._status!r}, not open; declare_cancelled() refused"
            )
        self._status = _CANCELLED
        self._reason = reason

    def declare_refused(self, reason: str) -> None:
        """Refused with a named reason — a capability gap or an illegal terminal,
        never a world gap and never a crash."""
        self._refuse_if_sealed("declare_refused")
        _require_text(reason, f"refusal reason for obligation {self.id!r}")
        if self._status != _OPEN:
            raise RuntimeError(
                f"obligation {self.id!r} is {self._status!r}, not open; declare_refused() refused"
            )
        self._status = _REFUSED
        self._reason = reason

    def reopen(self, reason: str) -> None:
        """Undo a close when the POST-RENDER check finds nothing shipped for it
        (consensus-4 fix 7). Legal only from closed, only before the turn seals:
        the ledger must be able to correct itself against the rendered truth, and
        this is the one transition that does it — never a softening of a declared
        or refused state."""
        self._refuse_if_sealed("reopen")
        _require_text(reason, f"reopen reason for obligation {self.id!r}")
        if self._status not in (_CLOSED, _DECLARED):
            # Also legal from declared-unanswerable: the render can prove a
            # declaration premature (consensus-4 fix 7 — the verifier reported full
            # coverage while a sibling had already been written off). Refused, and
            # ONLY refused, from failed/cancelled/refused: those are decisions about
            # what happened, not guesses about what could be answered.
            raise RuntimeError(
                f"obligation {self.id!r} is {self._status!r}, not closed or declared;"
                " reopen() refused"
            )
        self._status = _OPEN
        self._evidence_ref = ""
        self._reason = reason

    def _seal(self) -> None:
        self._sealed = True

    def _refuse_if_sealed(self, verb: str) -> None:
        if self._sealed:
            raise RuntimeError(
                f"obligation {self.id!r} belongs to a finalized turn; {verb}() refused"
            )


@dataclass(frozen=True)
class CommitResult:
    """The shippable record of how a turn ended. Frozen: the manifest a caller shows the
    user must be the manifest the transaction produced, not an edited copy."""

    #: "committed" (nothing open; declared items allowed) or "partial" (open items named).
    status: str
    #: Ids of obligations still open — non-empty only for "partial".
    open: tuple[str, ...]
    #: (id, reason) for every obligation explicitly declared unanswerable.
    declared: tuple[tuple[str, str], ...]
    #: (id, reason) for internal failures — the system broke, not the question.
    failed: tuple[tuple[str, str], ...] = ()
    #: (id, reason) for user-cancelled requests — latest instruction wins.
    cancelled: tuple[tuple[str, str], ...] = ()
    #: (id, reason) for refused items — capability gaps and illegal terminals.
    refused: tuple[tuple[str, str], ...] = ()
    #: Descriptions aligned index-for-index with ``open``, so a partial manifest names
    #: what was dropped in words, not just ids.
    open_descriptions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in ("committed", "settled", "partial"):
            raise ValueError(
                f"CommitResult status must be 'committed', 'settled' or 'partial', got {self.status!r}"
            )
        if self.status == "committed" and self.open:
            raise ValueError("a 'committed' result cannot carry open obligations")
        if self.status == "committed" and (self.failed or self.cancelled or self.refused):
            raise ValueError("a result with failed/cancelled/refused items is 'settled', not 'committed'")
        if self.status == "committed" and self.declared:
            # Measured 2026-08-20: a turn whose every obligation was declared unanswerable
            # printed "committed", reading as success. Declared items are honestly
            # accounted for, but a turn that answered nothing did not COMMIT an answer —
            # that is 'settled', a distinct outcome with its own name.
            raise ValueError("a result with declared-unanswerable items is 'settled', not 'committed'")
        if self.status == "settled" and self.open:
            raise ValueError("a 'settled' result cannot carry open obligations")
        if self.status == "settled" and not (self.declared or self.failed or self.cancelled or self.refused):
            raise ValueError("a 'settled' result must carry at least one declared/failed/cancelled/refused item")
        if self.status == "partial" and not self.open:
            # The mirror drift: commit_partial() refuses to label a fully-closed turn
            # "partial", but direct construction allowed it (finding D5). Status and
            # contents must agree in both directions, wherever the object is made.
            raise ValueError("a 'partial' result must name at least one open obligation")
        if self.open_descriptions and len(self.open_descriptions) != len(self.open):
            raise ValueError("open_descriptions must align one-to-one with open")

    def manifest(self) -> str:
        """One line naming every open and declared item — the sentence a caller may
        truthfully relay, and the whole point of the law: no summary softer than this."""
        bits: list[str] = []
        if self.open:
            descriptions = self.open_descriptions or ("",) * len(self.open)
            named = ", ".join(
                f"{oid} ({desc})" if desc else oid
                for oid, desc in zip(self.open, descriptions, strict=True)
            )
            bits.append(f"open: {named}")
        if self.declared:
            named = ", ".join(f"{oid} ({reason})" for oid, reason in self.declared)
            bits.append(f"declared unanswerable: {named}")
        if self.failed:
            named = ", ".join(f"{oid} ({reason})" for oid, reason in self.failed)
            bits.append(f"FAILED internally: {named}")
        if self.cancelled:
            named = ", ".join(f"{oid} ({reason})" for oid, reason in self.cancelled)
            bits.append(f"cancelled: {named}")
        if self.refused:
            named = ", ".join(f"{oid} ({reason})" for oid, reason in self.refused)
            bits.append(f"REFUSED: {named}")
        if not bits:
            bits.append("all obligations closed")
        return f"{self.status}: " + "; ".join(bits)


class CommitRefused(RuntimeError):  # noqa: N818 — name is the kernel contract (core/kernel/__init__.py)
    """commit() found open obligations.

    Carries and names exactly the open ids so the refusal cannot be summarized into
    something softer than the record — the audited defect was precisely a success report
    standing in front of three unanswered requests.
    """

    def __init__(self, open_ids: tuple[str, ...]) -> None:
        self.open_ids = open_ids
        noun = "obligation" if len(open_ids) == 1 else "obligations"
        super().__init__(
            f"commit refused: {len(open_ids)} open {noun}: {', '.join(open_ids)}"
        )


@dataclass(frozen=True)
class CompensationEntry:
    """One journaled effect and how to undo it. Frozen: an undo plan that can be edited
    after the effect ran is not a record of what ran."""

    tool: str
    description: str
    compensation: str


class TurnTransaction:
    """The transaction a turn ships through. Exactly three exits — ``commit()`` (nothing
    open), ``commit_partial()`` (open items named), ``abort()`` (undo plan returned) —
    and every exit is final."""

    def __init__(self, turn_id: str, obligations: list[Obligation]) -> None:
        _require_text(turn_id, "turn_id")
        ids = [ob.id for ob in obligations]
        duplicates = sorted({oid for oid in ids if ids.count(oid) > 1})
        if duplicates:
            # Two obligations sharing an id would make close-by-id silently pick one and
            # leave the other open-but-invisible — the dropped-request defect reborn.
            raise ValueError(f"duplicate obligation ids: {', '.join(duplicates)}")
        self.turn_id = turn_id
        self._obligations: dict[str, Obligation] = {ob.id: ob for ob in obligations}
        self._journal: list[CompensationEntry] = []
        self._state = "live"  # -> "committed" | "partial" | "aborted"

    # -- introspection ---------------------------------------------------------------

    def open_obligations(self) -> tuple[Obligation, ...]:
        self._refuse_if_finalized("open_obligations")
        return tuple(ob for ob in self._obligations.values() if ob.status == _OPEN)

    # -- obligation transitions by id ------------------------------------------------

    def close(self, obligation_id: str, evidence_ref: str) -> None:
        self._refuse_if_finalized("close")
        self._lookup(obligation_id).close(evidence_ref)

    def declare_unanswerable(self, obligation_id: str, reason: str) -> None:
        self._refuse_if_finalized("declare_unanswerable")
        self._lookup(obligation_id).declare_unanswerable(reason)

    def declare_failed(self, obligation_id: str, reason: str) -> None:
        self._refuse_if_finalized("declare_failed")
        self._lookup(obligation_id).declare_failed(reason)

    def declare_cancelled(self, obligation_id: str, reason: str) -> None:
        self._refuse_if_finalized("declare_cancelled")
        self._lookup(obligation_id).declare_cancelled(reason)

    def declare_refused(self, obligation_id: str, reason: str) -> None:
        self._refuse_if_finalized("declare_refused")
        self._lookup(obligation_id).declare_refused(reason)

    def reopen(self, obligation_id: str, reason: str) -> None:
        self._refuse_if_finalized("reopen")
        self._lookup(obligation_id).reopen(reason)

    # -- effects ----------------------------------------------------------------------

    def journal_effect(self, tool: str, description: str, compensation: str) -> None:
        """Record a nondeterministic effect and its undo, before or as it runs. An
        effect journaled with no compensation text is an effect abort() cannot walk
        back, so all three fields are mandatory."""
        self._refuse_if_finalized("journal_effect")
        self._journal.append(
            CompensationEntry(
                tool=_require_text(tool, "journal_effect tool"),
                description=_require_text(description, "journal_effect description"),
                compensation=_require_text(compensation, "journal_effect compensation"),
            )
        )

    # -- exits ------------------------------------------------------------------------

    def commit(self) -> CommitResult:
        """Ship as done. Refuses — naming exactly the open ids — unless every obligation
        is closed or declared. A refusal does NOT finalize: it starts the repair loop."""
        self._refuse_if_finalized("commit")
        open_ids = tuple(ob.id for ob in self._obligations.values() if ob.status == _OPEN)
        if open_ids:
            raise CommitRefused(open_ids)
        declared = self._pairs(_DECLARED)
        failed = self._pairs(_FAILED)
        cancelled = self._pairs(_CANCELLED)
        refused = self._pairs(_REFUSED)
        status = "settled" if (declared or failed or cancelled or refused) else "committed"
        self._finalize(status)
        return CommitResult(status=status, open=(), declared=declared, failed=failed,
                            cancelled=cancelled, refused=refused)

    def commit_partial(self) -> CommitResult:
        """Ship with open obligations — the only legal way — naming each by id and
        description. Refused when nothing is open: labelling a fully-closed turn
        'partial' is the same status-drift defect in the opposite direction."""
        self._refuse_if_finalized("commit_partial")
        open_obs = tuple(ob for ob in self._obligations.values() if ob.status == _OPEN)
        if not open_obs:
            raise RuntimeError(
                f"turn {self.turn_id!r} has no open obligations; commit_partial() refused"
                " — a fully-closed turn must commit()"
            )
        self._finalize("partial")
        return CommitResult(
            status="partial",
            open=tuple(ob.id for ob in open_obs),
            declared=self._pairs(_DECLARED),
            failed=self._pairs(_FAILED),
            cancelled=self._pairs(_CANCELLED),
            refused=self._pairs(_REFUSED),
            open_descriptions=tuple(ob.description for ob in open_obs),
        )

    def abort(self) -> tuple[CompensationEntry, ...]:
        """Kill the turn and return the undo plan: journaled compensations in reverse
        order, because effects are walked back opposite to how they were applied."""
        self._refuse_if_finalized("abort")
        undo = tuple(reversed(self._journal))
        self._finalize("aborted")
        return undo

    # -- internals ---------------------------------------------------------------------

    def _lookup(self, obligation_id: str) -> Obligation:
        try:
            return self._obligations[obligation_id]
        except KeyError:
            raise ValueError(
                f"turn {self.turn_id!r} has no obligation {obligation_id!r}"
            ) from None

    def _pairs(self, status: str) -> tuple[tuple[str, str], ...]:
        return tuple(
            (ob.id, ob.reason or "")
            for ob in self._obligations.values()
            if ob.status == status
        )

    def _finalize(self, state: str) -> None:
        self._state = state
        for ob in self._obligations.values():
            ob._seal()

    def _refuse_if_finalized(self, verb: str) -> None:
        if self._state != "live":
            raise RuntimeError(
                f"turn {self.turn_id!r} is already {self._state}; {verb}() refused"
            )
