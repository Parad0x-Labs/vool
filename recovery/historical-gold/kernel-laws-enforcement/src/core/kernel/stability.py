"""Stability-gated commitment — measured confidence as a commit-law gate.

A model SOUNDS confident whatever the facts. Every serious incident class starts
there: an unconditional recommendation ("buy now") whose truth silently depends on
one live-world input the turn did not control. Evidence typing (Law 2) grounds each
sentence in THIS world; nothing asked whether the CONCLUSION SURVIVES NEIGHBORING
WORLDS the user explicitly cares about.

Counterfactual re-derivation made neighboring worlds computable. This module makes
their verdict a COMMIT AUTHORITY:

- **Sweep**: the caller derives K stipulated worlds (each a digest-sealed
  ``CounterfactualBundle`` — same integrity discipline as any fork) and records one
  ``WorldVerdict`` per world: the premise value and the decision token the grounded
  answer landed on.
- **Flip localization**: if verdicts disagree, :func:`bisect_flip` narrows the exact
  premise value where the conclusion flips — with the monotonicity ASSUMPTION
  verified by interior sampling, never trusted. A non-monotone response refuses the
  whole analysis rather than reporting a fictional threshold.
- **Gate**: :func:`gate_commit` stands between the finished transaction and its
  commit. Policy ``require_stable`` REFUSES (naming the flip point) unless the
  conclusion holds across every stipulated world; policy ``annotate`` commits but
  returns the honest conditional sentence the caller must ship with the answer.

The new authority, stated narrowly: a turn may no longer present as unconditional a
conclusion that stipulated worlds prove conditional. Nothing else changes — worlds
are ordinary counterfactuals, verdict tokens are read out of GROUNDED rendered text,
and the gate mints no prose of its own beyond quoting observed verdicts.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

__all__ = [
    "NonMonotoneResponse",
    "NoFlipObserved",
    "StabilitySeal",
    "UnstableCommitRefused",
    "WorldVerdict",
    "bisect_flip",
    "classify_sweep",
    "gate_commit",
]


class NonMonotoneResponse(ValueError):
    """The conclusion's response to the premise is not monotone across the range —
    a single 'flip point' does not exist, so none may be reported."""


class NoFlipObserved(ValueError):
    """Both ends of the range agree — there is nothing to localize."""


@dataclass(frozen=True)
class WorldVerdict:
    """One stipulated world: the premise value tried and the grounded decision token."""

    premise_value: float
    verdict: str


def classify_sweep(verdicts: list[WorldVerdict]) -> tuple[bool, str]:
    """(all_agree, agreeing_verdict_or_empty). Verdicts must share one premise axis."""
    if len(verdicts) < 2:
        raise ValueError(
            "a stability sweep needs at least two stipulated worlds — one world IS "
            "the original turn"
    )
    tokens = {v.verdict for v in verdicts}
    return len(tokens) == 1, (tokens.pop() if len(tokens) == 1 else "")


def bisect_flip(
    evaluate: Callable[[float], str],
    lo: float,
    hi: float,
    *,
    tolerance: float,
    max_iterations: int = 64,
) -> float:
    """Locate the premise value where ``evaluate`` flips, assuming monotone response.

    The assumption is VERIFIED, not trusted: both endpoints must disagree and three
    interior probes must be consistent with a single transition. Violation is a named
    refusal — reporting a threshold for a non-monotone world would be fabrication
    wearing arithmetic.
    """
    if not hi > lo or tolerance <= 0:
        raise ValueError(f"invalid bracket [{lo}, {hi}) or tolerance {tolerance}")
    v_lo, v_hi = evaluate(lo), evaluate(hi)
    if v_lo == v_hi:
        raise NoFlipObserved(
            f"conclusion {v_lo!r} holds across the whole stipulated range "
            f"[{lo}, {hi}] — nothing to localize"
        )

    def transition_count_violation() -> bool:
        # The full probe sequence over the bracket may cross from v_lo's side to
        # v_hi's side AT MOST ONCE. A flip-flop (A…B…A) passes per-probe checks
        # and would still fabricate a single threshold — refuse it.
        probes = [(lo, v_lo)] + [
            (lo + (hi - lo) * f, evaluate(lo + (hi - lo) * f)) for f in (0.25, 0.5, 0.75)
        ] + [(hi, v_hi)]
        transitions = sum(
            1 for (_, a), (_, b) in zip(probes, probes[1:]) if a != b
        )
        return transitions > 1

    if transition_count_violation():
        raise NonMonotoneResponse(
            "the conclusion flips back inside the stipulated band — the response is "
            "not monotone; no single flip point exists to report"
        )

    low, high = lo, hi
    iterations = 0
    while high - low > tolerance and iterations < max_iterations:
        mid = (low + high) / 2
        if evaluate(mid) == v_lo:
            low = mid
        else:
            high = mid
        iterations += 1
    return high  # first bracket edge carrying the NEW verdict


@dataclass(frozen=True)
class StabilitySeal:
    """The measured-confidence artifact: what was swept, what held, where it broke."""

    premise_description: str
    swept_values: tuple[float, ...]
    verdicts: tuple[WorldVerdict, ...]
    stable: bool
    agreeing_verdict: str
    flip_value: float | None
    flip_tolerance: float

    def render(self) -> str:
        swept = ", ".join(f"{v:g}->{v.verdict}" for v in self.verdicts)
        head = (f"[stability] premise '{self.premise_description}' swept "
                f"{len(self.swept_values)} worlds: {swept}")
        if self.stable:
            return f"{head}\n[stability] conclusion '{self.agreeing_verdict}' holds everywhere swept"
        return (f"{head}\n[stability] CONDITIONAL: holds until premise ≈ "
                f"{self.flip_value:.4g} (±{self.flip_tolerance:g}), then flips")

    def hedge_sentence(self) -> str:
        """The honest conditional the caller ships when committing an unstable turn."""
        if self.stable:
            raise ValueError("a stable conclusion needs no hedge")
        return (f"This holds while {self.premise_description} stays below "
                f"≈{self.flip_value:.4g}; beyond that the conclusion flips.")


class UnstableCommitRefused(RuntimeError):
    """Law-1-plane refusal: the turn proved conditional and may not ship unconditional."""

    def __init__(self, premise_description: str, flip_value: float) -> None:
        super().__init__(
            f"commit refused: the conclusion is conditional — it flips at "
            f"{premise_description} ≈ {flip_value:.4g}; commit with policy 'annotate' "
            "to ship the honest conditional instead"
        )
        self.premise_description = premise_description
        self.flip_value = flip_value


def gate_commit(
    seal: StabilitySeal,
    *,
    policy: str,
) -> str:
    """Commit-plane check. Returns '' (ship as-is) or the hedge sentence to attach;
    raises :class:`UnstableCommitRefused` under ``require_stable``."""
    if policy == "require_stable":
        if not seal.stable:
            raise UnstableCommitRefused(seal.premise_description, seal.flip_value)  # type: ignore[arg-type]
        return ""
    if policy == "annotate":
        return "" if seal.stable else seal.hedge_sentence()
    raise ValueError(f"unknown stability policy {policy!r}; expected require_stable|annotate")
