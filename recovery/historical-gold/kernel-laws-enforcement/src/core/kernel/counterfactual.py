"""Counterfactual re-derivation — time travel over committed turns.

A committed turn is a sealed statement about the world: "given THESE tool results and
THESE model judgments, THIS answer, grounded by Law 2." Users constantly want the
neighboring question — *what if one premise had been different?* What if the rate was
1.30 instead of 1.17? What if the lookup had returned the other warehouse? Until now
that question had no representation in the kernel: you could not mutate history, only
re-ask the model free-hand and lose every guarantee.

This module makes counterfactuals a FIRST-CLASS transaction with three load-bearing
rules:

**The original tape is sacred.** The base turn's journal enters only through a
digest-verified seal (same discipline as ContinuationBundle). Nothing here writes to
it; after a counterfactual runs, its digest is bit-identical. History does not change —
a FORK of it exists beside it, labeled as such forever.

**A mutated premise invalidates everything downstream — and nothing upstream.**
The runner serves base-tape entries until it reaches the first premise; the premise's
result is substituted WITHOUT executing anything (the user stipulated it — that is
Law 2's ``stipulated`` move, applied to an effect), and from that point on every effect
re-derives LIVE into a fresh delta tape, because no downstream recorded result can be
trusted once reality shifted. Effects before the premise are never re-executed —
recomputing settled facts would be exactly the waste and double-side-effect hazard
Turn Continuation exists to prevent.

**Stipulation is visible, never silent.** Every premise is named in the receipt:
which effect, which args hash, applied or unapplied (control flow never reached it).
An override result rides the delta tape as a normal success entry at the same args
hash, so the counterfactual replays like any turn — while the receipt keeps the fork
honest about which line is world-fact and which is stipulation.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from core.kernel.continuation import ObligationState, TamperedBundleError
from core.kernel.effects import (
    DivergenceError,
    EffectJournal,
    EffectRunner,
    effect_args_hash,
)
from core.kernel.obligations import Obligation, TurnTransaction

__all__ = [
    "CounterfactualBundle",
    "CounterfactualReceipt",
    "CounterfactualRunner",
    "Premise",
    "open_counterfactual",
    "seal_counterfactual",
]

_BUNDLE_SCHEMA = "vool.counterfactual_bundle.v1"
_RECEIPT_SCHEMA = "vool.counterfactual_receipt.v1"


@dataclass(frozen=True)
class Premise:
    """One stipulated override: this effect call now returns this result."""

    effect_id: str
    args_hash: str
    new_result: Any

    def key(self) -> tuple[str, str]:
        return (self.effect_id, self.args_hash)


def _canonical(entries: list[dict[str, Any]]) -> str:
    return json.dumps(entries, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class CounterfactualBundle:
    """A completed turn plus explicit premises to vary. The base tape is frozen."""

    turn_id: str
    question: str
    base_entries: tuple[dict[str, Any], ...]
    base_sha256: str
    premises: tuple[Premise, ...]
    obligation_states: tuple[ObligationState, ...]

    def __post_init__(self) -> None:
        if not self.premises:
            raise ValueError(
                "a counterfactual with zero premises IS the original turn — refuse the "
                "fork and replay instead"
            )
        keys = [p.key() for p in self.premises]
        if len(keys) != len(set(keys)):
            raise ValueError("conflicting premises: two overrides target the same effect call")
        known = {(e["effect_id"], e["args_hash"]) for e in self.base_entries}
        for premise in self.premises:
            target = premise.key()
            if target not in known:
                raise ValueError(
                    f"premise targets {premise.effect_id!r} @ {premise.args_hash[:12]}…, "
                    "which the base tape does not contain — name a real settled effect"
                )
            matching = [
                e for e in self.base_entries
                if (e["effect_id"], e["args_hash"]) == target
            ]
            # Strict-order tapes may hold the same call twice; a premise must be
            # unambiguous or it silently rewrites one occurrence and not the other.
            if len(matching) > 1:
                raise ValueError(
                    f"premise on {premise.effect_id!r} is ambiguous: the base tape holds "
                    f"{len(matching)} identical calls — disambiguate before stipulating"
                )
            if "result" not in matching[0]:
                raise ValueError(
                    f"premise targets {premise.effect_id!r}, which FAILED in the base "
                    "turn — you cannot stipulate the value of a failure; re-run instead"
                )
        _reject_non_str_keys_deep([p.new_result for p in self.premises])
        expected = hashlib.sha256(_canonical(list(self.base_entries)).encode("utf-8")).hexdigest()
        if expected != self.base_sha256:
            raise TamperedBundleError(
                "counterfactual base tape does not match its integrity digest "
                f"(expected {expected[:12]}…, got {self.base_sha256[:12]}…)"
            )

    def verify(self) -> EffectJournal:
        try:
            return EffectJournal.from_json(_canonical(list(self.base_entries)))
        except ValueError as exc:
            raise TamperedBundleError(f"base tape failed validation: {exc}") from None

    def to_json(self) -> str:
        return json.dumps(
            {
                "schema": _BUNDLE_SCHEMA,
                "turn_id": self.turn_id,
                "question": self.question,
                "base_entries": list(self.base_entries),
                "base_sha256": self.base_sha256,
                "premises": [
                    {"effect_id": p.effect_id, "args_hash": p.args_hash,
                     "new_result": p.new_result}
                    for p in self.premises
                ],
                "obligation_states": [vars(s) for s in self.obligation_states],
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    @classmethod
    def from_json(cls, text: str) -> "CounterfactualBundle":
        try:
            raw = json.loads(text)
        except ValueError as exc:
            raise TamperedBundleError(f"bundle JSON does not parse: {exc}") from None
        if not isinstance(raw, dict) or raw.get("schema") != _BUNDLE_SCHEMA:
            raise TamperedBundleError(f"not a {_BUNDLE_SCHEMA} document")
        try:
            return cls(
                turn_id=str(raw["turn_id"]),
                question=str(raw["question"]),
                base_entries=tuple(dict(e) for e in raw["base_entries"]),
                base_sha256=str(raw["base_sha256"]),
                premises=tuple(
                    Premise(effect_id=p["effect_id"], args_hash=p["args_hash"],
                            new_result=p["new_result"])
                    for p in raw["premises"]
                ),
                obligation_states=tuple(ObligationState(**s) for s in raw["obligation_states"]),
            )
        except (KeyError, TypeError) as exc:
            raise TamperedBundleError(f"bundle document malformed: {exc}") from None


def _reject_non_str_keys_deep(value: object) -> None:
    """Premise results ride the delta tape — they must satisfy the tape's type system."""
    seen: set[int] = set()
    stack: list[object] = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            if id(current) in seen:
                continue
            seen.add(id(current))
            for key in current:
                if not isinstance(key, str):
                    raise ValueError(
                        f"premise results must use str dict keys, got {key!r}"
                    )
            stack.extend(current.values())
        elif isinstance(current, (list, tuple)):
            if id(current) in seen:
                continue
            seen.add(id(current))
            stack.extend(current)


def seal_counterfactual(
    *,
    turn_id: str,
    question: str,
    journal: EffectJournal,
    premises: tuple[Premise, ...],
    obligations: list[Obligation],
) -> CounterfactualBundle:
    entries = [dict(e) for e in journal.entries()]
    return CounterfactualBundle(
        turn_id=turn_id,
        question=question,
        base_entries=tuple(entries),
        base_sha256=hashlib.sha256(_canonical(entries).encode("utf-8")).hexdigest(),
        premises=premises,
        obligation_states=tuple(ObligationState.of(ob) for ob in obligations),
    )


class CounterfactualRunner:
    """Serve the base tape up to the first premise, then re-derive live into a delta.

    All identity checks (order, effect id, args hash) reuse the tape's own hashing via
    :func:`effects.effect_args_hash` — no second canonicalizer exists anywhere here.
    """

    def __init__(self, bundle: CounterfactualBundle) -> None:
        self._bundle = bundle
        self._entries = bundle.verify().entries()
        self._cursor = 0
        self._live_runner = EffectRunner(mode="record")
        self._live = False  # becomes True at the first applied premise: downstream re-derives
        self._applied: set[tuple[str, str]] = set()

    @property
    def bundle(self) -> CounterfactualBundle:
        return self._bundle

    @property
    def delta_journal(self) -> EffectJournal:
        """The full counterfactual tape: served prefix + stipulations + live re-derivations."""
        return self._live_runner.journal

    @property
    def served_from_base(self) -> int:
        return self._cursor

    def run(self, effect_id: str, fn: Any, /, *args: Any, **kwargs: Any) -> Any:
        args_h = effect_args_hash(effect_id, *args, **kwargs)
        override = next(
            (p for p in self._bundle.premises if p.key() == (effect_id, args_h)), None
        )

        if not self._live and self._cursor < len(self._entries):
            entry = self._entries[self._cursor]
            if entry["effect_id"] != effect_id:
                raise DivergenceError(
                    effect_id, "effect_id_mismatch",
                    f"base position {self._cursor} holds {entry['effect_id']!r}",
                )
            if entry["args_hash"] != args_h:
                raise DivergenceError(effect_id, "args_hash_mismatch")
            self._cursor += 1
            if override is not None:
                # THE STIPULATION POINT: reality is replaced, nothing executes.
                self._live = True
                self._applied.add(override.key())
            else:
                self._delta_keep(entry)
                return entry["result"]

        # Past the fork (or past the tape): everything re-derives live — except an
        # explicitly stipulated call, which is honored wherever control flow meets it.
        if override is not None:
            self._applied.add(override.key())
            self._live_runner.journal.record(
                {"effect_id": effect_id, "args_hash": args_h,
                 "result": override.new_result}
            )
            return override.new_result
        return self._live_runner.run(effect_id, fn, *args, **kwargs)

    def _delta_keep(self, entry: dict[str, Any]) -> None:
        """Carry one settled base entry onto the delta tape under its ORIGINAL identity."""
        self._live_runner.journal.record({
            "effect_id": entry["effect_id"],
            "args_hash": entry["args_hash"],
            "result": entry["result"],
        })

    def clock(self) -> float:
        import time

        return self.run("kernel.clock", time.time)  # type: ignore[return-value]

    def rand(self) -> float:
        import random

        return self.run("kernel.rand", random.random)  # type: ignore[return-value]


def open_counterfactual(
    bundle: CounterfactualBundle,
) -> tuple[CounterfactualRunner, TurnTransaction]:
    runner = CounterfactualRunner(bundle)
    txn = TurnTransaction(
        f"{bundle.turn_id}:counterfactual",
        [state.restore() for state in bundle.obligation_states],
    )
    return runner, txn


@dataclass(frozen=True)
class CounterfactualReceipt:
    """Proves which lines of the forked answer are world-fact and which are stipulation."""

    parent_turn_id: str
    parent_sha256: str
    premises: tuple[Premise, ...]
    #: (effect_id, args_hash) of every premise control flow actually reached.
    applied_keys: tuple[tuple[str, str], ...]
    served_from_base: int
    rederived_effects: tuple[str, ...]
    delta_sha256: str

    @property
    def applied(self) -> tuple[str, ...]:
        return tuple(f"{eid}@{h[:12]}…" for eid, h in self.applied_keys)

    @property
    def unapplied(self) -> tuple[str, ...]:
        applied = set(self.applied_keys)
        return tuple(
            f"{p.effect_id}@{p.args_hash[:12]}…" for p in self.premises
            if p.key() not in applied
        )

    def render(self) -> str:
        lines = [
            f"[counterfactual of turn {self.parent_turn_id} {self.parent_sha256[:12]}…]",
            f"  stipulated: {', '.join(self.applied) if self.applied else '(none applied)'}",
        ]
        if self.unapplied:
            lines.append(f"  UNAPPLIED (control flow never reached them): "
                         f"{', '.join(self.unapplied)}")
        lines.append(f"  {self.served_from_base} effects kept from history; "
                     f"{len(self.rederived_effects)} re-derived live: "
                     f"{', '.join(self.rederived_effects)}")
        lines.append(f"  delta tape {self.delta_sha256[:12]}…")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": _RECEIPT_SCHEMA,
            "parent_turn_id": self.parent_turn_id,
            "parent_sha256": self.parent_sha256,
            "premises": [
                {"effect_id": p.effect_id, "args_hash": p.args_hash}
                for p in self.premises
            ],
            "applied": list(self.applied),
            "served_from_base": self.served_from_base,
            "rederived_effects": list(self.rederived_effects),
            "delta_sha256": self.delta_sha256,
        }


def seal_counterfactual_receipt(
    *,
    bundle: CounterfactualBundle,
    runner: CounterfactualRunner,
    original_journal: EffectJournal | None = None,
) -> CounterfactualReceipt:
    """Bind the finished counterfactual to its parent. When ``original_journal`` is
    supplied it is re-digested and must STILL match the bundle — proving the fork left
    history untouched."""
    if original_journal is not None:
        digest = hashlib.sha256(
            _canonical(list(original_journal.entries())).encode("utf-8")).hexdigest()
        if digest != bundle.base_sha256:
            raise TamperedBundleError(
                "the base turn's tape changed during the counterfactual — history is "
                "not safe to fork"
            )
    rederived = tuple(
        entry["effect_id"] for entry in runner.delta_journal.entries()[runner.served_from_base:]
    )
    return CounterfactualReceipt(
        parent_turn_id=bundle.turn_id,
        parent_sha256=bundle.base_sha256,
        premises=bundle.premises,
        applied_keys=tuple(p.key() for p in bundle.premises
                           if p.key() in runner._applied),
        served_from_base=runner.served_from_base,
        rederived_effects=rederived,
        delta_sha256=hashlib.sha256(runner.delta_journal.to_json().encode("utf-8")).hexdigest(),
    )
