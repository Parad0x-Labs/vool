"""How much of this runtime's routing survives a phrasing it has never seen.

The number this produces answers one question: **of all the ways a user might say a thing, how many
does VOOL route correctly WITHOUT a keyword table having anticipated the words?** That is the only
form of the question worth measuring, because the alternative -- "how many of our test phrasings do
we route correctly?" -- is satisfied by typing the test phrasings into a keyword table, and a metric
you can satisfy by copying the test set into the implementation measures nothing.

**The score.**

    generalization = |classes routed correctly AND not lexically covered| / |all classes|

A *class* is a semantic equivalence class: several surface phrasings that mean the same thing and
must route the same way. A class is *lexically covered* when **any** of its phrasings contains a
term from the lexical-authority inventory. A class is *routed correctly* when **every** one of its
phrasings reaches the expected route -- one phrasing working while its paraphrase does not is the
exact failure this measures, so it cannot count as a pass.

**Why it cannot be gamed by adding a keyword.** Adding vocabulary can only move a class from
not-covered to covered. A covered class contributes zero to the numerator, and the denominator is
every class in the corpus regardless. So the score after adding a keyword is less than or equal to
the score before, always -- adding lexical authority spends generalization rather than earning it.
`tests/.../test_ldar_tamper.py` asserts exactly this inequality.

The two directions are worth stating plainly, because only one of them is closed:

* **Adding vocabulary** -- closed. Monotone non-increasing, proven by the inequality above.
* **Shrinking what counts as vocabulary** -- open in principle, and deliberately narrowed to a
  single lever: `lexical_authority._FUNCTION_WORD_SYMBOLS`. Everything else about coverage is
  derived. See `AuthorityInventory.function_words` for why that lever is an exact symbol list
  rather than a name pattern.

**Executed, not matched.** `score_corpus` takes an `execute` callable and runs every case through
it. A case that raises is recorded as an error and counts as not-routed; it is never dropped. A
scorer that decided "correct" by matching the case text against the same tables it is measuring
would be marking its own homework.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from core.semantic.lexical_authority import AuthorityInventory


@dataclass(frozen=True)
class CorpusCase:
    """One phrasing, and the route it must reach.

    `style` records HOW this phrasing differs from its siblings -- "plain", "polite", "terse",
    "typo", "indirect", "non_native". Carried so a report can say *which kind* of rephrasing the
    runtime loses, which is actionable, rather than only that it lost one.
    """

    case_id: str
    equivalence_class: str
    text: str
    expected_route: str
    style: str = "plain"


@dataclass(frozen=True)
class CaseOutcome:
    """What actually happened when one case was executed."""

    case: CorpusCase
    actual_route: str
    covered_by: tuple[str, ...] = ()
    error: str = ""

    @property
    def routed_correctly(self) -> bool:
        return not self.error and self.actual_route == self.case.expected_route

    @property
    def is_covered(self) -> bool:
        return bool(self.covered_by)


@dataclass(frozen=True)
class ClassResult:
    """A whole equivalence class: covered if ANY phrasing is, correct only if EVERY phrasing is."""

    equivalence_class: str
    outcomes: tuple[CaseOutcome, ...]

    @property
    def is_covered(self) -> bool:
        """ANY phrasing covered marks the class covered.

        The strict reading on purpose: if one phrasing of "how warm is it outside" is in a keyword
        table, the runtime's success on that meaning is not evidence it generalized to the meaning.
        Requiring every phrasing to be covered would let a class stay in the numerator while a table
        did most of the work.
        """
        return any(outcome.is_covered for outcome in self.outcomes)

    @property
    def is_routed_correctly(self) -> bool:
        """EVERY phrasing must reach the expected route. One paraphrase failing fails the class."""
        return bool(self.outcomes) and all(outcome.routed_correctly for outcome in self.outcomes)

    @property
    def generalized(self) -> bool:
        return self.is_routed_correctly and not self.is_covered


@dataclass(frozen=True)
class GeneralizationReport:
    """The reading, with every count a reader needs to check the score themselves."""

    classes: tuple[ClassResult, ...]
    inventory_terms: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)
    #: Whether EVERY authority-bearing source was actually readable when this was scored. False the
    #: moment any vocabulary is computed at runtime, packed dynamically, or in a file that will not
    #: parse -- and when it is False, `score` is withheld entirely. See the property for why.
    inventory_complete: bool = True
    unmeasured_detail: str = ""

    @property
    def total_classes(self) -> int:
        return len(self.classes)

    @property
    def covered_classes(self) -> int:
        return sum(1 for result in self.classes if result.is_covered)

    @property
    def held_out_classes(self) -> int:
        """Classes no keyword table anticipated -- the ones a score can legitimately be earned on."""
        return sum(1 for result in self.classes if not result.is_covered)

    @property
    def routed_classes(self) -> int:
        return sum(1 for result in self.classes if result.is_routed_correctly)

    @property
    def generalized_classes(self) -> int:
        return sum(1 for result in self.classes if result.generalized)

    @property
    def executed_cases(self) -> int:
        return sum(len(result.outcomes) for result in self.classes)

    @property
    def score(self) -> float | None:
        """Generalized classes over ALL classes -- or **None** when the inventory was incomplete.

        This is the repair for the deepest way the metric could be gamed. Coverage is what stops a
        class counting as generalized; making vocabulary unreadable removes coverage; removing
        coverage RAISES the score. So every trick that hides authority -- `tuple("a b".split())`,
        `dict.fromkeys([...])`, a dynamically packed regex -- paid off, and each one previously
        needed its own scanner fix. That is an arms race the scanner loses.

        The invariant instead: **making lexical authority opaque may never improve the metric.** It
        is enforced by refusing to produce a metric at all. If anything in scope could not be read,
        there is no number here to improve -- `None`, with `unmeasured_detail` saying what was
        unreadable. A lower bound is available from `score_lower_bound` for anyone who needs a
        figure, and it assumes every opaque table covers everything, which is the pessimistic
        reading rather than the flattering one.
        """
        if not self.inventory_complete:
            return None
        if not self.total_classes:
            return 0.0
        return self.generalized_classes / self.total_classes

    @property
    def score_lower_bound(self) -> float:
        """The score under the assumption that every unreadable table covers every class.

        Always defined, always pessimistic. When the inventory is incomplete this is 0.0, because
        an opaque table might cover anything and the safe floor is that it covers everything.
        """
        if not self.inventory_complete:
            return 0.0
        return self.score or 0.0

    @property
    def score_is_publishable(self) -> bool:
        """Whether a scalar may be quoted at all. False whenever authority went unmeasured."""
        return self.inventory_complete

    @property
    def held_out_accuracy(self) -> float:
        """Accuracy restricted to held-out classes.

        Reported beside `score` but never in place of it: this one CAN be gamed upward by covering a
        failing class with a keyword, which removes it from the denominator. It is useful for
        reading progress and unsafe as a target, and saying so here is cheaper than someone
        rediscovering it during a review.
        """
        held_out = [result for result in self.classes if not result.is_covered]
        if not held_out:
            return 0.0
        return sum(1 for result in held_out if result.is_routed_correctly) / len(held_out)

    def failures(self) -> tuple[CaseOutcome, ...]:
        return tuple(
            outcome
            for result in self.classes
            for outcome in result.outcomes
            if not outcome.routed_correctly
        )

    def by_style(self) -> dict[str, dict[str, int]]:
        """Correct/total per rephrasing style -- which KIND of paraphrase the runtime loses."""
        tally: dict[str, dict[str, int]] = {}
        for result in self.classes:
            for outcome in result.outcomes:
                row = tally.setdefault(outcome.case.style, {"correct": 0, "total": 0})
                row["total"] += 1
                if outcome.routed_correctly:
                    row["correct"] += 1
        return dict(sorted(tally.items()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "generalization_report_v2",
            "score": None if self.score is None else round(self.score, 4),
            "score_publishable": self.score_is_publishable,
            "score_lower_bound": round(self.score_lower_bound, 4),
            "inventory_complete": self.inventory_complete,
            "unmeasured_detail": self.unmeasured_detail,
            "held_out_accuracy": round(self.held_out_accuracy, 4),
            "total_classes": self.total_classes,
            "covered_classes": self.covered_classes,
            "held_out_classes": self.held_out_classes,
            "routed_classes": self.routed_classes,
            "generalized_classes": self.generalized_classes,
            "executed_cases": self.executed_cases,
            "inventory_terms": self.inventory_terms,
            "error_count": len(self.errors),
            "by_style": self.by_style(),
        }


def score_corpus(
    cases: Sequence[CorpusCase],
    *,
    inventory: AuthorityInventory,
    execute: Callable[[str], str],
) -> GeneralizationReport:
    """Run every case through `execute` and score by equivalence class.

    `execute` must actually drive the routing path and return the route it reached. It is injected
    rather than imported so the corpus can be scored against the deterministic front door, against a
    future semantic resolver, or against both for comparison -- without this module acquiring an
    opinion about which one is the runtime.

    Nothing is skipped. A case whose execution raises becomes an outcome with `error` set and
    `actual_route` empty, which fails its class; the alternative -- dropping it -- would make the
    score improve every time the runtime crashed on a phrasing.
    """
    grouped: dict[str, list[CaseOutcome]] = {}
    errors: list[str] = []
    for case in cases:
        covered = inventory.covers(case.text)
        try:
            actual = str(execute(case.text) or "")
            outcome = CaseOutcome(case=case, actual_route=actual, covered_by=covered)
        except Exception as exc:
            message = f"{case.case_id}: {type(exc).__name__}: {exc}"[:200]
            errors.append(message)
            outcome = CaseOutcome(case=case, actual_route="", covered_by=covered, error=message)
        grouped.setdefault(case.equivalence_class, []).append(outcome)

    classes = tuple(
        ClassResult(equivalence_class=name, outcomes=tuple(outcomes))
        for name, outcomes in sorted(grouped.items())
    )
    opaque = getattr(inventory, "opaque_entries", ())
    unreadable = getattr(inventory, "unreadable_files", ())
    return GeneralizationReport(
        classes=classes,
        inventory_terms=len(inventory.coverage_terms),
        errors=tuple(errors),
        inventory_complete=bool(getattr(inventory, "is_complete", True)),
        unmeasured_detail=(
            f"{len(opaque)} opaque table(s), {len(unreadable)} unreadable file(s)"
            if (opaque or unreadable) else ""
        ),
    )


__all__ = [
    "CaseOutcome",
    "ClassResult",
    "CorpusCase",
    "GeneralizationReport",
    "score_corpus",
]
