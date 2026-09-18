"""Turn Continuation Bundles — a half-run turn is a portable fact, not a corpse.

Today (2026-08-25) a turn that dies mid-flight — provider transport failure, crash —
is journaled as evidence and then abandoned: the partial tape is filed, the obligations
 evaporate, and a retry starts the whole turn over, re-running every side effect the
dead attempt already performed. Law 4 made the tape a faithful record; nothing made
the record LOAD-BEARING for recovery.

This module composes three existing primitives into a capability none of them provides
alone:

- **Law 4** (`core.kernel.effects`): the partial tape is already an ordered,
  args-hash-keyed record of exactly what the dead attempt settled. It becomes the
  continuation's *prefix* — replayed, never re-executed.
- **Law 1** (`core.kernel.obligations`): the transaction's obligation states are
  snapshotted at seal time and restored after the seam through the Obligations' own
  legal transitions only. A closed action obligation crosses the seam still closed on
  its ``exec:`` evidence; a declared-unanswerable item stays honestly declared.
- **Failover** (`core.model_failover`, wired by callers — this kernel module never
  imports upward): the resuming lane is selected by excluding the providers already
  recorded in the bundle's segments.

The one new authority, stated narrowly: **the seam** — the single legal place where a
turn switches from serving its own tape to recording new effects. Everything else is
inherited: divergence checking (order + args hash) stays inside ``EffectRunner``; the
tape's validation door stays ``EffectJournal.record``; obligation mutation stays inside
the Obligation transition methods. Nothing here re-implements another law's authority.

Sealing rules, all fail-closed:

- Only TRAILING **error** outcomes may be dropped from the tape — exactly the failed
  provider call that failover replaces. A dropped entry is named in the bundle.
- A trailing UNKNOWN outcome refuses to seal. An ambiguous external effect may already
  have happened; dropping it and re-running could double-fire. Reconcile first.
- Successes are never droppable; the prefix digest covers the exact prefix bytes and is
  re-verified at open time, so a tampered or truncated tape refuses loudly.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from core.kernel.effects import DivergenceError, EffectJournal, EffectRunner
from core.kernel.obligations import Obligation, TurnTransaction

__all__ = [
    "ContinuationBundle",
    "ContinuationReceipt",
    "ContinuationRunner",
    "DroppedOutcome",
    "ObligationState",
    "TamperedBundleError",
    "open_continuation",
    "seal_bundle",
]

_BUNDLE_SCHEMA = "vool.continuation_bundle.v1"
_RECEIPT_SCHEMA = "vool.continuation_receipt.v1"


class TamperedBundleError(RuntimeError):
    """The bundle's prefix does not match its integrity digest, or is malformed."""


def _canonical(entries: list[dict[str, Any]]) -> str:
    return json.dumps(entries, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class DroppedOutcome:
    """One trailing error outcome excluded from the prefix — named, never silent."""

    index: int
    effect_id: str
    error_type: str
    message: str


@dataclass(frozen=True)
class ObligationState:
    """A point-in-time snapshot of one obligation, restored via legal transitions."""

    id: str
    description: str
    kind: str
    status: str
    evidence_ref: str = ""
    reason: str = ""

    @classmethod
    def of(cls, ob: Obligation) -> "ObligationState":
        return cls(
            id=ob.id,
            description=ob.description,
            kind=ob.kind,
            status=ob.status,
            evidence_ref=ob.evidence_ref or "",
            reason=ob.reason or "",
        )

    def restore(self) -> Obligation:
        """Rebuild the obligation through its public, one-way transitions only."""
        ob = Obligation(self.id, self.description, self.kind)
        if self.status == "open":
            return ob
        if self.status == "closed":
            if not self.evidence_ref:
                raise TamperedBundleError(
                    f"obligation {self.id!r} snapshot says closed but carries no evidence ref"
                )
            ob.close(self.evidence_ref)
            return ob
        declarers = {
            "declared_unanswerable": ob.declare_unanswerable,
            "failed_internal": ob.declare_failed,
            "cancelled": ob.declare_cancelled,
            "refused": ob.declare_refused,
        }
        declare = declarers.get(self.status)
        if declare is None or not self.reason:
            raise TamperedBundleError(
                f"obligation {self.id!r} snapshot has unrestorable status {self.status!r}"
            )
        declare(self.reason)
        return ob


@dataclass(frozen=True)
class ContinuationBundle:
    """The frozen, portable state of a half-run turn at the moment it died."""

    turn_id: str
    question: str
    origin_provider: str
    segments: tuple[str, ...]
    prefix_entries: tuple[dict[str, Any], ...]
    prefix_sha256: str
    dropped_outcomes: tuple[DroppedOutcome, ...]
    obligation_states: tuple[ObligationState, ...]

    def __post_init__(self) -> None:
        expected = hashlib.sha256(_canonical(list(self.prefix_entries)).encode("utf-8")).hexdigest()
        if expected != self.prefix_sha256:
            raise TamperedBundleError(
                "bundle prefix does not match its integrity digest "
                f"(expected {expected[:12]}…, got {self.prefix_sha256[:12]}…)"
            )
        ids = [st.id for st in self.obligation_states]
        if len(ids) != len(set(ids)):
            raise TamperedBundleError("duplicate obligation ids in bundle snapshot")

    def verify(self) -> EffectJournal:
        """Re-validate and return the prefix journal — the only way a bundle opens."""
        try:
            return EffectJournal.from_json(_canonical(list(self.prefix_entries)))
        except ValueError as exc:
            raise TamperedBundleError(f"bundle prefix failed tape validation: {exc}") from None

    def to_json(self) -> str:
        return json.dumps(
            {
                "schema": _BUNDLE_SCHEMA,
                "turn_id": self.turn_id,
                "question": self.question,
                "origin_provider": self.origin_provider,
                "segments": list(self.segments),
                "prefix_entries": list(self.prefix_entries),
                "prefix_sha256": self.prefix_sha256,
                "dropped_outcomes": [vars(d) for d in self.dropped_outcomes],
                "obligation_states": [vars(s) for s in self.obligation_states],
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    @classmethod
    def from_json(cls, text: str) -> "ContinuationBundle":
        try:
            raw = json.loads(text)
        except ValueError as exc:
            raise TamperedBundleError(f"bundle JSON does not parse: {exc}") from None
        if not isinstance(raw, dict) or raw.get("schema") != _BUNDLE_SCHEMA:
            raise TamperedBundleError(f"not a {_BUNDLE_SCHEMA} document")
        try:
            drops = tuple(DroppedOutcome(**d) for d in raw["dropped_outcomes"])
            states = tuple(ObligationState(**s) for s in raw["obligation_states"])
            return cls(
                turn_id=str(raw["turn_id"]),
                question=str(raw["question"]),
                origin_provider=str(raw["origin_provider"]),
                segments=tuple(str(p) for p in raw["segments"]),
                prefix_entries=tuple(dict(e) for e in raw["prefix_entries"]),
                prefix_sha256=str(raw["prefix_sha256"]),
                dropped_outcomes=drops,
                obligation_states=states,
            )
        except (KeyError, TypeError) as exc:
            raise TamperedBundleError(f"bundle document malformed: {exc}") from None


def seal_bundle(
    *,
    turn_id: str,
    question: str,
    origin_provider: str,
    journal: EffectJournal,
    obligations: list[Obligation],
    segments: tuple[str, ...] = (),
) -> ContinuationBundle:
    """Freeze a half-run turn into a continuation bundle.

    The tape's TRAILING error outcomes are dropped — they are precisely the failures
    failover replaces — and each drop is named. Anything else trailing (UNKNOWN) or any
    non-trailing failure refuses: strict-order tapes cannot skip a middle entry, and an
    ambiguous external effect must be reconciled, not retried.
    """
    entries = [dict(e) for e in journal.entries()]
    dropped: list[DroppedOutcome] = []
    while entries and "error" in entries[-1]:
        entry = entries.pop()
        err = entry["error"]
        dropped.append(
            DroppedOutcome(
                index=len(entries),
                effect_id=str(entry["effect_id"]),
                error_type=str(err["type"]),
                message=str(err["message"]),
            )
        )
    if entries and "unknown" in entries[-1]:
        raise RuntimeError(
            f"cannot seal turn {turn_id!r}: trailing effect "
            f"{entries[-1]['effect_id']!r} has an UNKNOWN outcome — reconcile it first; "
            "continuing could double-fire an ambiguous external effect"
        )
    dropped.reverse()  # tape order, not pop order
    return ContinuationBundle(
        turn_id=turn_id,
        question=question,
        origin_provider=origin_provider,
        segments=tuple(segments) + (origin_provider,),
        prefix_entries=tuple(entries),
        prefix_sha256=hashlib.sha256(_canonical(entries).encode("utf-8")).hexdigest(),
        dropped_outcomes=tuple(dropped),
        obligation_states=tuple(ObligationState.of(ob) for ob in obligations),
    )


class ContinuationRunner:
    """Replay the sealed prefix, then record the suffix — one permanent seam.

    All per-effect authority (strict order, effect-id match, args-hash match, faithful
    reproduction of recorded outcomes) is delegated to two internal ``EffectRunner``
    instances. This class owns exactly two things: the seam switch (replay → record,
    irreversible) and the combined-journal view used to persist the finished turn.
    """

    def __init__(self, bundle: ContinuationBundle) -> None:
        self._bundle = bundle
        self._prefix = EffectRunner(mode="replay", journal=bundle.verify())
        self._suffix = EffectRunner(mode="record")
        self._seamed = False

    @property
    def bundle(self) -> ContinuationBundle:
        return self._bundle

    @property
    def seam_index(self) -> int:
        return len(self._prefix.journal)

    @property
    def seamed(self) -> bool:
        return self._seamed

    def run(self, effect_id: str, fn: Any, /, *args: Any, **kwargs: Any) -> Any:
        if not self._seamed:
            try:
                return self._prefix.run(effect_id, fn, *args, **kwargs)
            except DivergenceError as exc:
                # journal_exhausted IS the seam: the prefix holds everything the dead
                # attempt settled, and this call is the first thing it never reached.
                # Any other divergence is real control-flow drift and stays an error.
                if exc.reason != "journal_exhausted":
                    raise
                self._seamed = True
        return self._suffix.run(effect_id, fn, *args, **kwargs)

    def clock(self) -> float:
        return self.run("kernel.clock", _time_time)  # type: ignore[return-value]

    def rand(self) -> float:
        return self.run("kernel.rand", _random_random)  # type: ignore[return-value]

    def combined_journal(self) -> EffectJournal:
        """Prefix + suffix through the tape's validated door, ready to persist."""
        combined = EffectJournal()
        for entry in (*self._prefix.journal.entries(), *self._suffix.journal.entries()):
            combined.record(entry)
        return combined


# Module-level indirections so clock/rand delegate to stdlib without importing time/random
# at kernel-module import cost; EffectRunner itself owns the real implementations.
def _time_time() -> float:
    import time

    return time.time()


def _random_random() -> float:
    import random

    return random.random()


def open_continuation(bundle: ContinuationBundle) -> tuple[ContinuationRunner, TurnTransaction]:
    """Verify a bundle and resume the turn: (runner, restored transaction)."""
    runner = ContinuationRunner(bundle)  # construction verifies digest + tape validity
    txn = TurnTransaction(bundle.turn_id, [state.restore() for state in bundle.obligation_states])
    return runner, txn


@dataclass(frozen=True)
class ContinuationReceipt:
    """The user-visible artifact of a seam: one turn, N providers, zero recomputation."""

    turn_id: str
    origin_provider: str
    segments: tuple[str, ...]
    seam_index: int
    total_effects: int
    prefix_sha256: str
    dropped_outcomes: tuple[DroppedOutcome, ...]
    final_status: str

    def render(self) -> str:
        chain = " → ".join(self.segments)
        drops = (
            "; dropped: " + ", ".join(f"{d.effect_id}({d.error_type})" for d in self.dropped_outcomes)
            if self.dropped_outcomes
            else ""
        )
        return (
            f"[continuation] turn {self.turn_id} {self.final_status} across "
            f"{chain}; seam after effect #{self.seam_index}, {self.total_effects} effects "
            f"on tape, prefix {self.prefix_sha256[:12]}…{drops}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": _RECEIPT_SCHEMA,
            "turn_id": self.turn_id,
            "origin_provider": self.origin_provider,
            "segments": list(self.segments),
            "seam_index": self.seam_index,
            "total_effects": self.total_effects,
            "prefix_sha256": self.prefix_sha256,
            "dropped_outcomes": [vars(d) for d in self.dropped_outcomes],
            "final_status": self.final_status,
        }


def seal_receipt(
    bundle: ContinuationBundle,
    *,
    runner: ContinuationRunner,
    result_status: str,
    resuming_provider: str | None = None,
) -> ContinuationReceipt:
    """Bind the finished turn's outcome to its seam history. Fail closed on mismatch:
    the receipt may only be minted when the runner actually crossed a seam and its
    combined tape contains the verified prefix."""
    if not runner.seamed:
        raise RuntimeError(
            "receipt refused: the runner never crossed the seam — this is not a continuation"
        )
    combined = runner.combined_journal()
    entries = combined.entries()
    if len(entries) < runner.seam_index:
        raise TamperedBundleError("combined tape lost prefix entries")
    for original, carried in zip(bundle.prefix_entries, entries[: runner.seam_index], strict=True):
        if original != carried:
            raise TamperedBundleError("combined tape diverges from the sealed prefix")
    segments = bundle.segments
    if resuming_provider and resuming_provider not in segments:
        segments = segments + (resuming_provider,)
    return ContinuationReceipt(
        turn_id=bundle.turn_id,
        origin_provider=bundle.origin_provider,
        segments=segments,
        seam_index=runner.seam_index,
        total_effects=len(entries),
        prefix_sha256=bundle.prefix_sha256,
        dropped_outcomes=bundle.dropped_outcomes,
        final_status=result_status,
    )
