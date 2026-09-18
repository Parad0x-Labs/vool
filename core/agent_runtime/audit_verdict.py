"""The audit verdict, and the only code allowed to turn one into words.

The incident's report said, in one document, `## Highest-risk bug` and `NOT reproduced — the proof
test passed`, and then appended model prose asserting the bug was real. Three claims, two of them
contradicting the executed result. That was possible because the headline, the verdict line and the
analysis were composed independently and none of them read the proof.

Here there is one typed state and one renderer, and the renderer is a pure function of the state.
Affirmative language ("the bug is real", "highest-risk bug", "reproduced by a failing test") lives
in exactly one branch — `proven` — so a refuted or unproven report cannot carry it by construction,
not by review. `asserts_reproduction` is the single predicate both the renderer and the tests read,
so the two cannot drift.

Five terminal states, and no sixth:

    candidate_unproven — a citation the evidence supports, and no execution was authorized
    refuted            — a proof test ran and EXITED 0, so the claim is disproved
    proven             — a proof test ran and failed as required, on the current code
    blocked            — the audit could not reach a verdict and says exactly where it stopped
    no_finding         — the search completed and produced nothing checkable
"""
from __future__ import annotations

import difflib
import math
from dataclasses import dataclass, field
from typing import Any

CANDIDATE_UNPROVEN = "candidate_unproven"
REFUTED = "refuted"
PROVEN = "proven"
BLOCKED = "blocked"
NO_FINDING = "no_finding"

TERMINAL_STATES: tuple[str, ...] = (CANDIDATE_UNPROVEN, REFUTED, PROVEN, BLOCKED, NO_FINDING)


def asserts_reproduction(state: str) -> bool:
    """Whether this state permits affirmative bug language. Only one state does."""
    return str(state or "") == PROVEN


# Finding E point 4, 2026-08-04: severity is COMPUTED here, from harm_class, never asserted by the
# model. This is the same invariant `evidence_basis` already protects elsewhere in this module --
# the runtime composes the verdict, the model supplies facts the runtime judges. `crash`/`integrity`
# are the two harm classes a proof run can actually settle (see `PROVABLE_HARM_CLASSES` in
# `stepped_audit.py`), so they carry the highest severity; the rest are reported observations,
# tiered by how much they typically cost in production; `strength` is not a defect at all and never
# reaches this function (see `split_strengths`).
_HIGH_SEVERITY_HARM = frozenset({"crash", "integrity"})
_MEDIUM_SEVERITY_HARM = frozenset({"perf", "memory", "api", "versioning"})
_LOW_SEVERITY_HARM = frozenset({"test-coverage"})


def compute_severity(harm_class: str) -> str:
    """Deterministic severity from a harm class. Never asked of the model -- see module docstring
    on `evidence_basis` for why runtime-composed verdicts are this module's whole point."""
    harm = str(harm_class or "").strip().lower()
    if harm in _HIGH_SEVERITY_HARM:
        return "High"
    if harm in _MEDIUM_SEVERITY_HARM:
        return "Medium"
    if harm in _LOW_SEVERITY_HARM:
        return "Low"
    # An undeclared or unrecognized harm_class is never assumed High -- that would let a vague
    # claim outrank a properly classified one.
    return "Medium"


# Confidence tiers a finding can carry, from strongest to weakest evidence:
#   proven     -- executed: a proof test ran and failed as required (state == PROVEN only)
#   challenged -- an adversarial source check tried to disprove it and could not
#     unreviewed -- a survey row from the same nomination batch; never challenged, never executed
CONFIDENCE_PROVEN = "Proven"
CONFIDENCE_CHALLENGED = "Challenged"
CONFIDENCE_UNREVIEWED = "Unreviewed"
_CONFIDENCE_RANK = {CONFIDENCE_PROVEN: 0, CONFIDENCE_CHALLENGED: 1, CONFIDENCE_UNREVIEWED: 2}
_SEVERITY_RANK = {"High": 0, "Medium": 1, "Low": 2}


@dataclass
class VerdictFinding:
    title: str = ""
    file: str = ""
    line_start: int = 0
    line_end: int = 0
    cited_line_text: str = ""
    failure_scenario: str = ""
    # Finding E points 2 and 5, 2026-08-04: collected by the nomination prompt for every candidate
    # already, but previously dropped for the primary finding and string-concatenated into the
    # title for additional ones. Real fields now, threaded from `stepped_audit.py`.
    harm_class: str = ""
    suggested_fix: str = ""
    confidence: str = CONFIDENCE_UNREVIEWED

    @property
    def location(self) -> str:
        if not self.file:
            return ""
        if self.line_start:
            return f"{self.file}:{self.line_start}-{self.line_end or self.line_start}"
        return self.file

    @property
    def is_strength(self) -> bool:
        return str(self.harm_class or "").strip().lower() == "strength"

    @property
    def severity(self) -> str:
        return compute_severity(self.harm_class)


# Weight tables for `compute_report_score`, below. Deliberately monotonic in the SAME order the
# tiers above already rank in (`_SEVERITY_RANK`, `_CONFIDENCE_RANK`), never a new taxonomy: the
# score can never rank a Low/Unreviewed finding above a High/Proven one, because it is built from
# the identical ordinals `_finding_table_rows` already sorts the visible table by. That is what
# keeps the number and the table it sits above from being two different opinions about the same
# report.
_SEVERITY_POINTS = {"High": 4.0, "Medium": 2.0, "Low": 0.75}
_CONFIDENCE_WEIGHT = {
    CONFIDENCE_PROVEN: 1.0,
    CONFIDENCE_CHALLENGED: 0.85,
    CONFIDENCE_UNREVIEWED: 0.6,
}
# The first defect a report finds tells you the file has a problem; a fifth one of comparable
# weight adds real but smaller marginal information about overall health. Without this decay, a
# 20-row survey batch would compound linearly toward the floor regardless of severity mix -- which
# both overstates the certainty a bounded, non-exhaustive pass can have, and would make the score
# swing entirely on how many rows one nomination batch happened to return rather than on how bad
# they are. That coupling is exactly what H2's "first non-empty batch wins" design (see
# `stepped_audit.py`) was already flagged as a live defect for; the decay keeps the SCORE from
# reproducing the same defect in a new place now that batches are unioned instead of dropped.
_RANK_DECAY = 0.85
_STRENGTH_BONUS_PER_ITEM = 0.4
_STRENGTH_BONUS_CAP = 1.5
# `refuted` (a proof test written and EXECUTED, exiting 0) earns more credit per item than
# `challenged_out` earns at all: a proof run is real, run evidence the described failure does not
# occur; an adversarial source challenge is a reading of the citation that found it unsupported
# before anything was ever executed. Giving both the same weight would let a model's stronger prose
# ("I checked and it's fine") buy the same score boost as an actually-green-exiting test, which
# collapses the exact proven/challenged distinction this module's docstring says the whole file
# exists to keep intact. `challenged_out` is intentionally absent from the formula below, not just
# under-weighted.
_REFUTED_BONUS_PER_ITEM = 0.15
_REFUTED_BONUS_CAP = 0.45
_SCORE_FLOOR = 0.5
_SCORE_CEILING = 10.0


# Tolerance for the float-noise comparison in `_bonus_saturation_point`, below. Keyed to
# `math.isclose`'s combined relative+absolute test rather than a bare epsilon, so it scales with
# the magnitude of whatever `per_item`/`cap` pair it is asked about instead of assuming today's
# two-decimal constants (0.15, 0.4, 0.45, 1.5) stay representative of every future weight table.
_SATURATION_TOLERANCE = 1e-9


def _bonus_saturation_point(per_item: float, cap: float) -> int:
    """Smallest item count whose bonus -- run through the identical `min(count * per_item, cap)`
    expression `compute_report_score` uses below -- first reaches `cap`. Every item beyond this
    point moves the final score by exactly zero.

    QA regression, 2026-08-06 (round 5): both round-4 reviewers independently re-derived the same
    live defect against the *unmodified* `_score_lines`: its "N strength(s) credited" and "N
    disproved candidate(s) credited" clauses named the raw `len(...)` of `verdict.strengths` /
    `verdict.refuted`, but the bonus each feeds is capped (`_STRENGTH_BONUS_CAP`,
    `_REFUTED_BONUS_CAP`) and saturates well before the raw count does -- confirmed live: 4
    strengths and 10 strengths produce the identical final score, as do 3 and 8 proof-refuted items,
    yet the old text claimed "4 credited" vs "10 credited" for the identical number. This is the
    same false-confidence shape this module's own round-4 fix-commit named as the thing to avoid
    ("credited" must mean "moved the score"), just on two clauses that round didn't touch.

    QA regression, 2026-08-06 (round 6): round 5's own fix above reopened the exact defect it was
    closing, in a new place. Its docstring claimed that walking the SAME `min(point * per_item,
    cap)` expression with a raw `<` comparison "can never disagree with what the score itself
    actually did" -- that claim was false. IEEE-754 double precision cannot represent `0.15`
    exactly, so `3 * 0.15 == 0.44999999999999996`, one ULP below the mathematical `0.45`. A raw
    `< cap` comparison reads that as "not yet saturated" and the loop takes one MORE step than the
    real formula needs, so this helper computed 4 for `_REFUTED_BONUS_SATURATION_POINT` while
    `compute_report_score`'s actual output -- confirmed directly by three independent reviewers,
    none of them going through this helper -- saturates at 3: item 3 always moves the score, item 4
    never does, at every severity/harm_class tier tried. The module's adjacent comment ("4 and 3
    respectively", below) was correct the whole time; only the arithmetic computing the second
    number was wrong.

    Round 5's own boundary tests could not catch this: they derived their expected `cap_point` FROM
    `_REFUTED_BONUS_SATURATION_POINT` itself -- the constant under test -- so they only ever
    exercised n = (helper's answer) vs n = (helper's answer) + 1, both already past the REAL
    boundary regardless of where an off-by-one in the helper had put it. A test built that way is
    structurally blind to this class of bug by construction, not by an oversight that a bigger
    fixture would have caught.

    Fixed by comparing with a tolerance instead of a raw `<`: the bonus counts as saturated once
    `point * per_item` lands within `_SATURATION_TOLERANCE` of `cap`, not only once it reaches or
    passes `cap` exactly. Deliberately not `cap / per_item` rounded to the nearest int either --
    that division carries the identical float-noise exposure in the opposite direction (e.g.
    `0.45 / 0.15` is `2.9999999999999996` in floating point; `round()` happens to save that case,
    but nothing guarantees it saves the next one) -- and deliberately not a fixed decimal-place
    rounding, which would need retuning the day a weight table uses finer-grained per-item values.
    `math.isclose`'s relative+absolute tolerance instead tracks the SAME expression
    `compute_report_score` evaluates, at whatever magnitude a future `per_item`/`cap` pair happens
    to land on, rather than re-deriving the boundary through a second piece of arithmetic that can
    silently drift from the first the way the `<` comparison above just did.

    QA regression, 2026-08-06 (round 6, adversarial): with `per_item <= 0` and `cap > 0`, `point *
    per_item` never approaches `cap` -- the loop never terminates. Both saturation constants below
    are computed at import time, so a future retune of either weight to zero (or a sign-flip typo)
    would hang the entire `import core.agent_runtime.audit_verdict` forever, with no exception and
    nothing to grep in a log. Not reachable by either weight table today; guarded here so the failure
    mode for a bad future retune is a loud `ValueError` at import time, not a silent hang.
    """
    if per_item <= 0:
        raise ValueError(f"_bonus_saturation_point: per_item must be positive, got {per_item!r}")
    point = 0
    while True:
        reached = min(point * per_item, cap)
        if reached >= cap or math.isclose(reached, cap, rel_tol=_SATURATION_TOLERANCE, abs_tol=_SATURATION_TOLERANCE):
            return point
        point += 1


# The count of strengths / proof-refuted items at which each one's bonus is fully saturated -- 4
# and 3 respectively at the constants above (verified directly against `compute_report_score`'s own
# output, not just against this helper -- see the round-6 comment on `_bonus_saturation_point`).
# Computed once, not per render, since the inputs are fixed module constants; if either weight
# table above is ever retuned, this recomputes with it rather than needing a matching manual
# update.
_STRENGTH_BONUS_SATURATION_POINT = _bonus_saturation_point(_STRENGTH_BONUS_PER_ITEM, _STRENGTH_BONUS_CAP)
_REFUTED_BONUS_SATURATION_POINT = _bonus_saturation_point(_REFUTED_BONUS_PER_ITEM, _REFUTED_BONUS_CAP)


def compute_report_score(verdict: AuditVerdict) -> float | None:
    """Deterministic 0.5-10.0 gut-check for the whole report, or None when nothing was checked at
    all (`BLOCKED`). Never asked of the model -- built only from fields the runtime already
    computes for the findings table (`compute_severity`, the confidence tier, `strengths`,
    `refuted`), the same "the runtime composes the verdict, the model supplies facts the runtime
    judges" invariant `compute_severity` documents above. This is the runtime-computed match to
    Hermes's single fast-gut-check number, without letting a model self-assert how good its own
    report is.

    Every terminal state except `BLOCKED` produces a score. Only a `PROVEN` primary -- an
    execution that actually ran and failed as required -- deducts anything; `strengths` and
    `refuted` (both real, executed outcomes) can still add a bonus regardless of state.
    `additional_findings` never move the number at all, in any state: a survey row is never
    challenged or executed (see `_survey_findings`'s own docstring), so it is never confirmed
    enough to count against the file, no matter how many of them a batch returns.
    """
    if verdict.state == BLOCKED:
        return None

    defect_weights: list[float] = []
    if verdict.finding is not None and verdict.state == PROVEN:
        # Live incident, 2026-08-05: a report headlined "Score: 2.5/10" computed from four
        # findings -- one CANDIDATE_UNPROVEN primary (survived an adversarial source challenge,
        # never executed) and three `additional_findings` rows (always CONFIDENCE_UNREVIEWED by
        # construction -- `_survey_findings` never challenges or executes them, per its own
        # docstring) -- and at least one of those four turned out to be factually wrong on
        # independent verification. The score read as a confident, alarming verdict on code that
        # was never actually confirmed broken. Only `PROVEN` -- an execution that actually ran and
        # failed as required -- may move the deduction now. A `CANDIDATE_UNPROVEN` primary and
        # every `additional_findings` row still render in full in the report body
        # (`_primary_classification_bullets`, `_additional_finding_lines`); they simply carry zero
        # weight in the headline number, because neither is confirmed.
        defect_weights.append(
            _SEVERITY_POINTS[verdict.finding.severity] * _CONFIDENCE_WEIGHT[CONFIDENCE_PROVEN]
        )

    defect_weights.sort(reverse=True)
    deduction = sum(weight * (_RANK_DECAY**rank) for rank, weight in enumerate(defect_weights))

    bonus = min(len(verdict.strengths) * _STRENGTH_BONUS_PER_ITEM, _STRENGTH_BONUS_CAP)
    bonus += min(len(verdict.refuted) * _REFUTED_BONUS_PER_ITEM, _REFUTED_BONUS_CAP)

    score = max(_SCORE_FLOOR, min(_SCORE_CEILING, _SCORE_CEILING - deduction + bonus))
    return round(score, 1)


def split_strengths(findings: list[VerdictFinding]) -> tuple[list[VerdictFinding], list[VerdictFinding]]:
    """(defects, strengths) -- strengths never pass through the bug-shaped table (Finding E point 3):
    `failure_scenario` is specified as "the concrete input or state, then the wrong outcome", which
    cannot honestly describe something done well."""
    defects = [item for item in findings if not item.is_strength]
    strengths = [item for item in findings if item.is_strength]
    return defects, strengths


@dataclass
class VerdictProof:
    attempted: bool = False
    test_path: str = ""
    test_command: str = ""
    returncode: int | None = None
    output_digest: str = ""
    note: str = ""


@dataclass
class AuditVerdict:
    """Everything the renderer is allowed to know. No prose enters except `analysis`, and that is
    admitted only when the state is `proven`."""

    state: str
    finding: VerdictFinding | None = None
    proof: VerdictProof | None = None
    refuted: list[dict[str, Any]] = field(default_factory=list)
    # Candidates that survived the adversarial source check but were not the one carried to proof.
    # A single-finding verdict answered "audit this file, tell me pros and cons" with one defect and
    # discarded the rest of the search, so a file with four issues reported one. These are ALWAYS
    # candidate-grade: only `finding` is ever executed against, so only it can reach `proven`.
    additional_findings: list[VerdictFinding] = field(default_factory=list)
    # How much of the project this audit actually opened. The operator asked to audit a whole
    # project, the collector read 193 of its 493 source files, and the report said neither number -
    # so "one unproven candidate" read as "I looked everywhere and found one thing". Per CLAUDE.md
    # a bounded pass must say what it bounded; this is that sentence.
    coverage_note: str = ""
    analysis: str = ""
    remaining_proof: str = ""
    blocked_reason: str = ""
    model_label: str = ""
    usage_line: str = ""
    target_path: str = ""
    # Set when this turn was a continuation ("prove it") that could NOT resume the finding the
    # operator meant — because the previous turn disproved it or never produced one. Rendering a
    # different candidate without saying so is a silent substitution: the operator asked about one
    # claim and would be answered about another under the same pronoun.
    continuation_note: str = ""
    # Finding E point 6, 2026-08-04: candidates the challenge step screened out (`verdict` !=
    # "supported"). This list mixes TWO different outcomes, distinguished only by the `verdict`
    # key on each item -- it is NOT uniformly "a REAL, executed falsification" as an earlier
    # version of this comment claimed:
    #   - `verdict == "refuted"`: a challenge genuinely RAN (a direct execution via
    #     `refuting_claim`, or a deterministic source contradiction) and found a contradiction.
    #     This is the real, executed falsification case.
    #   - any other value (`stepped_audit._challenge_finding` actually returns `"uncertain"` for
    #     all of these): the challenge call never produced a usable result -- it was refused
    #     outright (e.g. the audit's total call budget was already spent before this step could
    #     run), it came back empty, or its reply failed to parse. NO check ran in this case.
    # Previously reached only internal telemetry (`details.stepped_audit.screened_out`) and never
    # the visible report; now it does, via `_ruled_out_lines` below, which is the renderer
    # responsible for not conflating the two (see the live-observed defect that motivated this:
    # a budget-exhausted refusal, `verdict="uncertain"`, was rendered with the exact same "ruled
    # out by an adversarial source check" wording as a genuine `"refuted"` falsification). Each
    # item carries title/verdict/reason/counterexample (see `stepped_audit._challenge_finding`).
    challenged_out: list[dict[str, Any]] = field(default_factory=list)
    # Split out of `additional_findings` in `__post_init__` below (Finding E point 3): a
    # `harm_class == "strength"` row is never a defect and must never share the bug-shaped table.
    strengths: list[VerdictFinding] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.state not in TERMINAL_STATES:
            raise ValueError(f"{self.state!r} is not a terminal audit state")
        # Structural, not advisory: analysis is model prose, and model prose about a claim the
        # execution disproved is exactly the contradiction this module exists to make impossible.
        if not asserts_reproduction(self.state):
            self.analysis = ""
        # The primary finding is the only one a proof ran against. An additional finding that
        # duplicated it would read as two independent problems where the search found one.
        if self.finding is not None:
            primary = (self.finding.title or "").strip().lower()
            self.additional_findings = [
                item
                for item in self.additional_findings
                if (item.title or "").strip().lower() != primary
            ]
        # A strength can never be the PRIMARY finding (only `integrity`/`crash` reach that slot —
        # see `_finding_defect` in stepped_audit.py), so this only ever pulls from the additional
        # list. Callers may also pass `strengths` directly; both routes land here so the split is
        # enforced once, not re-derived by every call site.
        extra_defects, extra_strengths = split_strengths(self.additional_findings)
        self.additional_findings = extra_defects
        self.strengths = [*self.strengths, *extra_strengths]


def _unreviewed_challenges(challenged_out: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every `challenged_out` item whose adversarial challenge never actually ran (see the field's
    docstring on `AuditVerdict.challenged_out`): `verdict != "refuted"` means the check was refused
    outright, came back empty, or failed to parse -- not that it ran and found nothing.

    QA regression, 2026-08-05: `_status_word` and `_score_lines` both decide what the headline and
    the score line say, and neither one looked at `challenged_out` at all -- only at
    `additional_findings`. A `NO_FINDING` report whose ONLY candidate was a budget-exhausted,
    never-checked item (the exact shape of the live incident `_ruled_out_lines` above was fixed
    for) still rendered "No Issues Found" / "10.0/10 ... nothing counted against or for it", which
    tells the headline-only reader the opposite of what the body shows. Factored into one function
    so `_ruled_out_lines`, `_status_word`, and `_score_lines` all use the identical definition of
    "unreviewed" and cannot drift apart on it.

    2026-08-06: takes the raw list rather than a whole `AuditVerdict` so `stepped_audit.py` can call
    it directly on `screened_rows` -- the same list that becomes `verdict.challenged_out` -- BEFORE
    an `AuditVerdict` exists yet, to compose an honest `blocked_reason` from the identical counts
    the rendered report will show, instead of a second, independently-typed sentence that can (and
    did) disagree with them. See `compose_search_ended_reason` below.
    """
    return [item for item in challenged_out if item.get("verdict") != "refuted"]


def _refuted_challenges(challenged_out: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every `challenged_out` item whose adversarial challenge actually RAN and found a
    contradiction (`item["verdict"] == "refuted"`) -- the real, executed-falsification half of
    `challenged_out` (see the field's docstring on `AuditVerdict.challenged_out`), as opposed to
    `_unreviewed_challenges` above.

    QA regression, 2026-08-06 (round 5): `_ruled_out_lines` and `_score_lines` each independently
    hand-wrote this exact filter as a separate literal list comprehension. They agreed only because
    nobody had touched either one on its own -- the identical shape that let the WITHHELD and
    fallback branches drift apart in an earlier round (see `_unreviewed_challenges`'s own docstring
    for that history). Factored out so both call sites read the same list and cannot re-diverge.

    2026-08-06: takes the raw list rather than a whole `AuditVerdict`, for the same reason given on
    `_unreviewed_challenges` above.
    """
    return [item for item in challenged_out if item.get("verdict") == "refuted"]


def _challenge_never_attempted(challenged_out: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The subset of `_unreviewed_challenges` where NO real check ever ran at all -- the audit's
    call budget refused this candidate's challenge before it ever reached the model.

    2026-08-06: `_unreviewed_challenges` alone cannot tell "we tried and the reply was useless"
    apart from "we never got to try" -- both produce `verdict != "refuted"`, and until now both
    were told apart only by the free-text `reason` string. `stepped_audit.FindingChallenge.
    attempted` (see its own field comment) makes that distinction structured: `False` exactly when
    `SteppedCallResult.attempted` was `False`, which is exactly when the budget's own `may_call`
    check refused the call before any request was made. A candidate in THIS bucket was never
    adversarially reviewed in any sense -- it belongs with `UNREVIEWED_BUDGET_EXHAUSTED`, not with
    `CHALLENGED`, no matter which list it happens to be stored alongside.

    Absence of the `attempted` key (older or synthetic data that predates this field) is treated as
    `True` -- the same default `FindingChallenge.attempted` itself uses -- so a caller who has not
    been updated to supply it is not silently reclassified as "never reviewed".
    """
    return [item for item in _unreviewed_challenges(challenged_out) if item.get("attempted", True) is False]


def _challenge_attempted_but_unresolved(challenged_out: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The subset of `_unreviewed_challenges` where a real check DID run (a model reply of any
    kind, however unusable, or a deterministic execution) but did not settle the claim either way.
    The complement of `_challenge_never_attempted` within `_unreviewed_challenges` -- see that
    function's docstring for the distinction and why it now exists as a structured field rather
    than free-text `reason` sniffing.
    """
    return [item for item in _unreviewed_challenges(challenged_out) if item.get("attempted", True) is not False]


def _has_unreviewed_challenge(verdict: AuditVerdict) -> bool:
    return bool(_unreviewed_challenges(verdict.challenged_out))


def compose_search_ended_reason(
    *, rejected_count: int, challenged_count: int, unreviewed_count: int
) -> str:
    """The ONE sentence describing how a search that confirmed nothing actually ended, derived only
    from the same counts the report's own score line and candidate tables show -- never composed
    independently of them, and never phrased by the model.

    Live incident, 2026-08-06, session `openclaw:d77e4cf7487fcb92b78c`: a real audit against
    `api/apache/liquefy_apache_repetition_v1.py` spent its whole 5-call budget across 3 nominate + 2
    challenge calls (the trace: nominate, challenge, nominate, challenge, nominate, budget
    exhausted), leaving only 2 of 8 nominated candidates ever actually reviewed by a challenge call.
    The OLD `stepped_audit.py` line here was `if screened_rows and not failure_notes: blocked_reason
    = "all nominated candidates failed adversarial checking against the source"` -- true only that
    `screened_rows` (the challenged-and-not-supported candidates) was non-empty, which fired the
    sentence whenever ANY candidate had been reviewed, regardless of how many others never were. The
    rendered report showed this exact sentence sitting next to `Rejected candidates: 0` and 8
    `Unreviewed candidates` explicitly reasoned as "the adversarial check never ran" -- three mutually
    exclusive claims in one report. This function replaces that hand-typed string with a decision
    tree keyed only on the counts every other part of the report already uses
    (`_refuted_challenges`, `_unreviewed_challenges`, `_unresolved_candidate_counts`), so the summary
    sentence cannot disagree with the body again.

    `challenged_count` is every candidate a REAL check actually ran against, whatever the outcome
    (`rejected_count` refuted cleanly; the rest came back inconclusive -- a model reply, however
    unusable, or a deterministic execution, that did not settle the claim either way). `
    unreviewed_count` is every candidate that never had a real check run at all -- a plain survey
    row nobody offered to a challenge, OR a candidate the budget refused to challenge before the
    call ever reached the model (see `stepped_audit.FindingChallenge.attempted` and
    `_challenge_never_attempted`/`_challenge_attempted_but_unresolved`, which is what a caller
    should use to split `challenged_out` into these two counts correctly -- a budget-refused
    candidate must count as unreviewed here, never as challenged, no matter which raw list it is
    stored in).
    """
    if rejected_count > challenged_count:
        # Not reachable from the real call site (rejected_count is always a subset of
        # challenged_count by construction there), but this function is public and directly unit
        # tested, so the same precedent as `_bonus_saturation_point` applies: fail loudly on a
        # violated precondition rather than silently rendering a negative count in operator-facing
        # text (QA regression, adversarial review, 2026-08-06 -- confirmed live that calling this
        # with rejected_count=5, challenged_count=3 produced "...-2 more candidates...").
        raise ValueError(
            f"rejected_count ({rejected_count}) cannot exceed challenged_count ({challenged_count})"
        )
    if challenged_count <= 0:
        return "no candidate was adversarially reviewed before the search ended"
    if rejected_count == challenged_count and unreviewed_count <= 0:
        plural = challenged_count != 1
        return (
            f"all {challenged_count} nominated candidate{'s' if plural else ''} "
            f"{'were' if plural else 'was'} reviewed and rejected"
        )
    if unreviewed_count > 0:
        plural = "s" if unreviewed_count != 1 else ""
        return (
            f"no reviewed candidate was confirmed; {unreviewed_count} candidate{plural} "
            "remained unreviewed when the search ended"
        )
    # challenged_count > 0, rejected_count < challenged_count, unreviewed_count == 0: every
    # candidate was AT LEAST offered a challenge, but some came back inconclusive rather than
    # cleanly rejected -- a real, distinct outcome from either branch above, not folded into
    # either one so it does not misreport a candidate as either "rejected" or "never reviewed".
    #
    # QA regression (adversarial review), 2026-08-06: the `rejected_count` clause below used a
    # literal, uncomputed "(s)" rather than deriving its plural the way `inconclusive_count` two
    # lines below always did -- `rejected_count=1` rendered the grammatically broken "1
    # candidate(s) were rejected". Not reachable from the real call site (there, `unreviewed_
    # count == 0` here always implies `rejected_count == challenged_count`, which routes to the
    # earlier branch instead), but a prior version of this file's own test asserted the broken
    # string as correct -- checked-in-wrong grammar is still wrong, and the fix costs nothing.
    inconclusive_count = challenged_count - rejected_count
    rejected_plural = rejected_count != 1
    inconclusive_plural = "s" if inconclusive_count != 1 else ""
    return (
        f"{rejected_count} candidate{'s' if rejected_plural else ''} "
        f"{'were' if rejected_plural else 'was'} rejected and {inconclusive_count} more "
        f"candidate{inconclusive_plural} were challenged but came back inconclusive; "
        "none was confirmed"
    )


# The terminal lifecycle state a single candidate can end this turn in. Every candidate a
# nomination call ever proposes is NOMINATED; from there it resolves to exactly one of the other
# five -- never more than one, never zero, for any candidate that survives to the final report (a
# candidate a deterministic pre-filter would reject before it is even shown to a challenge call is
# FILTERED, not populated by anything in this round of fixes -- see `reconcile_candidate_counts`'s
# own docstring on why the invariant still names it).
CANDIDATE_NOMINATED = "nominated"
CANDIDATE_FILTERED = "filtered"
CANDIDATE_UNREVIEWED_BUDGET_EXHAUSTED = "unreviewed_budget_exhausted"
CANDIDATE_CHALLENGED = "challenged"
# SCALPEL fix, failure class D/I, 2026-08-06: two of `CANDIDATE_CHALLENGED`'s prior members split
# out into their own states. A challenge whose reply never parsed as usable JSON and a challenge
# the PROVIDER itself failed (transport error, empty/errored completion) were both indistinguishable
# `CANDIDATE_CHALLENGED` rows, told apart only by reading free-text `reason` prose. Both still count
# as "challenged" (a real call was attempted; see `_challenge_attempted_but_unresolved`, which reads
# `verdict`/`attempted`, not this field) for every reconciliation invariant — this is additional
# display/Activity granularity, not a new counting bucket.
CANDIDATE_CHALLENGE_PARSE_FAILED = "challenge_parse_failed"
CANDIDATE_CHALLENGE_PROVIDER_FAILURE = "challenge_provider_failure"
CANDIDATE_REJECTED = "rejected"
CANDIDATE_CONFIRMED = "confirmed"


def reconcile_candidate_counts(
    *,
    nominated: int,
    filtered: int,
    unreviewed: int,
    challenged: int,
    rejected: int,
    confirmed: int,
    unresolved_after_challenge: int,
) -> list[str]:
    """Two arithmetic facts that must hold for ANY audit report, checked directly rather than
    trusted: every nominated candidate lands in exactly one top-level bucket, and every challenged
    candidate lands in exactly one of the three ways a challenge can end.

        nominated = filtered + unreviewed + challenged
        challenged = rejected + confirmed + unresolved_after_challenge

    Returns the list of violated invariants (empty when everything reconciles) rather than raising,
    so a caller in the middle of composing a live report can choose to log the discrepancy and keep
    going rather than fail the whole turn over an accounting bug -- the same posture
    `_bonus_saturation_point` takes for a "should be structurally impossible" input elsewhere in
    this module, but non-fatal here because these counts come from live, model-influenced runtime
    state, not fixed module constants.

    `filtered` is always 0 in the current runtime -- no deterministic pre-filter exists yet to
    populate it (candidate-quality work, explicitly out of scope for this round of fixes) -- but
    the term stays in the formula rather than being dropped, so the day a filter IS added, this
    function does not need to change to account for it; only the caller's `filtered` argument does.

    `confirmed` here means PROVEN specifically -- an executed, confirmed defect -- not merely
    "survived its challenge" (a challenge-supported candidate that was never promoted to proof is
    counted in `unresolved_after_challenge`... no: it is `CHALLENGED` and therefore already outside
    the `NO_FINDING`/`BLOCKED` accounting this function's real call site uses, since a challenge-
    supported candidate always becomes `finding`/`extra_findings` and blocks the search from ever
    reaching `NO_FINDING` at all -- see the comment at that call site in `stepped_audit.py` for the
    structural reason `confirmed` and "challenge-supported-but-not-yet-proven" are both provably 0
    whenever this function is actually invoked there today).
    """
    violations: list[str] = []
    left = filtered + unreviewed + challenged
    if left != nominated:
        violations.append(
            f"nominated={nominated} but filtered({filtered}) + unreviewed({unreviewed}) + "
            f"challenged({challenged}) = {left}"
        )
    right = rejected + confirmed + unresolved_after_challenge
    if right != challenged:
        violations.append(
            f"challenged={challenged} but rejected({rejected}) + confirmed({confirmed}) + "
            f"unresolved_after_challenge({unresolved_after_challenge}) = {right}"
        )
    return violations


def duplicate_candidate_ids(*row_groups: list[dict[str, Any]]) -> tuple[str, ...]:
    """Candidate ids that appear in more than one of the given row groups.

    `reconcile_candidate_counts` checks that the AGGREGATE counts add up; it cannot catch the SAME
    candidate occupying two buckets at once if a miscount elsewhere happens to cancel out (e.g. one
    row double-counted while a different row is dropped, leaving the totals looking clean). This is
    the per-candidate complement, made possible by `SteppedFinding.id`/`candidate_id_for` (SCALPEL
    fix, failure class D): the exact contradiction the incident named -- one candidate rendered as
    both the promoted primary and a still-unreviewed row -- is a duplicate id across the groups
    passed here, independent of whether the aggregate arithmetic happens to still balance.

    A row with no ``id`` (older or synthetic data predating this field) is never flagged — this
    tightens the guarantee only for rows that carry the identity to check.
    """
    seen: dict[str, int] = {}
    for group in row_groups:
        ids_in_group = {str(row.get("id")) for row in group if isinstance(row, dict) and row.get("id")}
        for candidate_id in ids_in_group:
            seen[candidate_id] = seen.get(candidate_id, 0) + 1
    return tuple(sorted(candidate_id for candidate_id, count in seen.items() if count > 1))


def _status_word(verdict: AuditVerdict) -> str:
    """Header status word (Finding E point 8). One word (or short phrase) per terminal state.

    QA regression, 2026-08-04: `NO_FINDING` rendered a bare "No Issues Found" even when
    `additional_findings` carried real Unreviewed candidates from the same nomination pass --
    live-confirmed on `session_store.py` ('rate session_store.py, look for issues'), where the
    headline read "No Issues Found" directly above an "Other candidates" table listing 8
    concrete High/Medium findings (insecure token comparison, a concurrent-modification crash,
    ...). An operator skimming only the headline was told the opposite of what the body showed.
    The state itself is unchanged -- `NO_FINDING` still means no PROVEN/executed claim -- but the
    WORD must not imply the report is empty when it is not.

    QA regression, 2026-08-05: the same gap existed for an unreviewed `challenged_out` item (a
    candidate found, but never checked because the challenge step never ran) -- see
    `_unreviewed_challenges` above. Extended below so this headline also fires for that case.

    QA regression, 2026-08-06: the phrase "Unreviewed Candidates Below" promised content that, as
    of this date, the `NO_FINDING` branch of `render_audit_report` no longer inlines (see that
    branch's own comment) -- the per-candidate table moved to Activity, so "below" pointed at
    nothing. The counts themselves are still directly below, in the WITHHELD score block.
    """
    state = verdict.state
    if state == PROVEN:
        return "Confirmed"
    if state == CANDIDATE_UNPROVEN:
        return "Unverified"
    if state == REFUTED:
        return "Ruled Out"
    if state == NO_FINDING:
        if verdict.additional_findings or _has_unreviewed_challenge(verdict):
            return "No Proven Issues"
        return "No Issues Found"
    if state == BLOCKED:
        return "Incomplete"
    return "Incomplete"


def _header_lines(verdict: AuditVerdict) -> list[str]:
    """Target file(s), a one-word status, and the coverage note inline (Finding E point 8)."""
    target = verdict.target_path or "the workspace"
    line = f"**Status: {_status_word(verdict)}** — `{target}`"
    if verdict.coverage_note:
        line += f". {verdict.coverage_note}"
    return [line, ""]


def _unresolved_candidate_counts(verdict: AuditVerdict) -> tuple[int, int]:
    """(likely_count, unreviewed_count) -- every live, unresolved candidate in the verdict, split
    by each item's OWN confidence, not by which list it happens to sit in.

    QA regression, 2026-08-06: `verdict.additional_findings` has two different-provenance sources
    in `stepped_audit.py` that this function used to conflate by list membership alone:

    - `_survey_findings` rows -- genuinely `CONFIDENCE_UNREVIEWED`, never challenged.
    - `extra_findings` rows -- built via `_verdict_finding()`, which DEFAULTS confidence to
      `CONFIDENCE_CHALLENGED` because every item reaching `extra_findings` already survived the
      same adversarial `_challenge_finding` gate the primary did (only appended when
      `challenge.verdict == "supported"`). These are "likely", exactly like a `CANDIDATE_UNPROVEN`
      primary -- not "unreviewed".

    Treating every row of `additional_findings` as unreviewed produced a report where the score
    line said "Unreviewed candidates: 1" for a row whose own table cell, three lines below, said
    "Confidence: Challenged" -- an internal contradiction inside one report. Splitting on
    `.confidence` here (the same field `_additional_finding_lines` already reads for that table
    cell) makes the two agree by construction.

    This is also the ONE place both the WITHHELD block and the fallback/numeric branch of
    `_score_lines` get their counts from, so the two cannot independently drift the way they did
    before: the fallback branch used to compute `likely_count + len(verdict.additional_findings)`
    on its own, silently omitting `unreviewed_challenges` that the WITHHELD branch already counted.
    """
    likely_count = 1 if verdict.finding is not None and verdict.state == CANDIDATE_UNPROVEN else 0
    unreviewed_count = 0
    for item in verdict.additional_findings:
        if (item.confidence or CONFIDENCE_UNREVIEWED) == CONFIDENCE_UNREVIEWED:
            unreviewed_count += 1
        else:
            likely_count += 1
    unreviewed_count += len(_unreviewed_challenges(verdict.challenged_out))
    return likely_count, unreviewed_count


def _score_lines(verdict: AuditVerdict) -> list[str]:
    """The runtime-computed score line, printed second (right after the header) to match where
    Hermes prints its own `6.5/10` -- ahead of every detail section, not buried after them.

    Live-testing, 2026-08-05: a run with ZERO `PROVEN` findings -- one Challenged-but-unexecuted
    primary, plus several Unreviewed `additional_findings` -- still rendered a bare
    `**Score: 10.0/10**`, sitting directly above a table of real, plausible, unconfirmed
    High-severity candidates. `compute_report_score` is untouched and correct here (only a `PROVEN`
    primary may move it -- see its own docstring); what was wrong is printing a NUMBER at all when a
    live candidate is sitting in the report below it with nothing settling it either way. A number
    reads as "nothing left to check"; that is false whenever an unconfirmed candidate remains. So:

    - Zero `PROVEN` findings but at least one live, unresolved candidate (an unproven primary, a
      survey row, or a `challenged_out` item whose challenge never actually ran -- see
      `_unreviewed_challenges`) withholds the number and states the four counts a number would
      otherwise compress into one misleading digit, none silently dropped.
    - A real `PROVEN` primary earns a real number, exactly as before -- `confirmed_defect_count`
      below is the same gate `compute_report_score` itself uses to decide whether to deduct.
    - Zero `PROVEN` findings AND zero live candidates of any kind -- nothing left unresolved in any
      tier -- is the one case a bare ceiling is honest, because nothing sits below it to contradict
      it (this also covers a fully `refuted`/challenge-refuted pass: a real, executed disproof is
      resolved, not a lingering candidate, so it does not withhold the number either).
    - `BLOCKED` used to fall straight through to `compute_report_score`'s `None` and return `[]` --
      silence here reads as an oversight, not a decision. It now says plainly that the score is
      withheld because the audit never reached a verdict, in the same register `_status_word`
      already uses for `BLOCKED` ("Incomplete").
    """
    if verdict.state == BLOCKED:
        return [
            "**Audit score: WITHHELD** — Incomplete: the audit stopped before it reached a "
            "verdict, so there is nothing complete enough this pass to put a number on.",
            "",
        ]

    score = compute_report_score(verdict)
    if score is None:
        # No other terminal state returns None from `compute_report_score` -- kept as a hard stop,
        # not a silent skip, so a future terminal state can't fall through this function unscored
        # without someone noticing.
        raise AssertionError(f"{verdict.state!r} produced no score outside BLOCKED")

    # Only a PROVEN primary moves the score (see `compute_report_score`), so it is the only thing
    # "weighed" may honestly name here. QA regression, 2026-08-05: the old `defect_count` counted a
    # CANDIDATE_UNPROVEN primary plus every `additional_findings` row -- the exact four findings,
    # none proven, one factually wrong on independent check, that produced a live "Score: 2.5/10"
    # report. Saying "4 defects weighed" next to a score only 0 or 1 of them actually moved is the
    # same false-confidence gap this fix closes in the number itself; the text must not reopen it.
    confirmed_defect_count = 1 if verdict.finding is not None and verdict.state == PROVEN else 0
    # A CANDIDATE_UNPROVEN primary survived an adversarial source challenge but was never executed
    # -- "likely", not confirmed. Each `additional_findings` row is split the same way by its OWN
    # `.confidence` field in `_unresolved_candidate_counts`: only a genuinely `CONFIDENCE_UNREVIEWED`
    # row (a `_survey_findings` row, never challenged) counts as unreviewed; a `CONFIDENCE_CHALLENGED`
    # row (an `extra_findings` row, already challenge-survived) counts as likely, same as a
    # `challenged_out` item whose own challenge never ran (`_unreviewed_challenges`).
    likely_count, unreviewed_count = _unresolved_candidate_counts(verdict)
    unreviewed_challenges = _unreviewed_challenges(verdict.challenged_out)
    refuted_challenge_count = len(_refuted_challenges(verdict.challenged_out))
    rejected_count = len(verdict.refuted) + refuted_challenge_count
    # A live, unresolved candidate -- not a candidate that was already checked and disproved
    # (`verdict.refuted`, or a `challenged_out` item with `verdict == "refuted"`), both of which are
    # settled outcomes, not open ones.
    candidate_present = bool(likely_count or verdict.additional_findings or unreviewed_challenges)

    if confirmed_defect_count == 0 and candidate_present:
        # `NO_FINDING` specifically gets the plain Challenged/Confirmed/Rejected/Unreviewed shape
        # (point 3 of the 2026-08-06 spec) instead of the WITHHELD block's Confirmed/Likely/
        # Unreviewed/Rejected framing below -- `NO_FINDING` has no live candidate left to call
        # "Likely" (see `candidate_present`'s own note: a `NO_FINDING` resolution structurally
        # cannot reach here with anything in `extra_findings`, so `likely_count` is always 0 in
        # this branch), and a plain "here is what actually happened to each of the N candidates"
        # reads more honestly than a near-score framing when nothing survived to be even a
        # candidate. `CANDIDATE_UNPROVEN` (a live, uncontradicted candidate genuinely exists) keeps
        # the WITHHELD framing unchanged below, since "Likely: 1" is real, useful information there.
        # SCALPEL fix, failure class B, 2026-08-06: this used to fire ONLY for `NO_FINDING`, on the
        # reasoning that `CANDIDATE_UNPROVEN` has a live, uncontradicted candidate worth calling
        # "Likely" (the WITHHELD framing below). Operator instruction overrides that: for a request
        # that wants exactly one confirmed finding or a concise refusal, EVERY zero-confirmed state
        # — including a live but unproven primary, and a proof-refuted primary — must render the
        # identical concise contract, not a framing that varies by which internal state produced it.
        if verdict.state in (NO_FINDING, CANDIDATE_UNPROVEN, REFUTED):
            never_attempted_count = len(_challenge_never_attempted(verdict.challenged_out))
            attempted_unresolved_count = len(_challenge_attempted_but_unresolved(verdict.challenged_out))
            # SCALPEL fix, 2026-08-06: `verdict.additional_findings` CAN hold a `CONFIDENCE_
            # CHALLENGED` row here now that `CANDIDATE_UNPROVEN` reaches this same branch (unlike
            # the old `NO_FINDING`-only routing, where `extra_findings` was always empty by
            # construction) -- a challenge-survived extra finding must count toward Challenged,
            # never toward Unreviewed. Split ADDITIONAL FINDINGS ONLY, inline, rather than reusing
            # `_unresolved_candidate_counts`: that helper's `unreviewed_count` already folds in
            # EVERY `challenged_out` uncertain item (both never-attempted and attempted-but-
            # unresolved) without splitting them the way this branch needs to -- reusing it here
            # double-counted the never-attempted ones on top of `never_attempted_count` below
            # (caught live: a single never-attempted `challenged_out` item rendered "Unreviewed: 2").
            additional_likely_count = 0
            additional_unreviewed_count = 0
            for item in verdict.additional_findings:
                if (item.confidence or CONFIDENCE_UNREVIEWED) == CONFIDENCE_UNREVIEWED:
                    additional_unreviewed_count += 1
                else:
                    additional_likely_count += 1
            challenged_count = rejected_count + attempted_unresolved_count + additional_likely_count
            never_reviewed_count = additional_unreviewed_count + never_attempted_count
            return [
                "**NOT PROVEN** — no current correctness bug was confirmed in this pass.",
                f"- Challenged: {challenged_count}",
                f"- Confirmed: {confirmed_defect_count}",
                f"- Rejected: {rejected_count}",
                f"- Unreviewed: {never_reviewed_count}",
                "",
            ]
        return [
            "**Audit score: WITHHELD** — no finding has been confirmed yet, so a headline number "
            "would assert more than this pass established.",
            f"- Confirmed findings: {confirmed_defect_count}",
            f"- Likely findings: {likely_count}",
            f"- Unreviewed candidates: {unreviewed_count}",
            f"- Rejected candidates: {rejected_count}",
            "",
        ]

    # Either a real PROVEN primary earns a real number (unchanged from before this fix), or there
    # is genuinely nothing live left unresolved for a number to misrepresent -- both cases share the
    # rendering below exactly as it read before this fix.
    #
    # QA regression, 2026-08-06: this used to be its own independently-written
    # `likely_count + len(verdict.additional_findings)`, which silently omitted
    # `unreviewed_challenges` that the WITHHELD branch above already counts -- a PROVEN primary + 2
    # `additional_findings` + 1 unreviewed `challenged_out` item rendered "2 unproven candidates
    # below are not counted either way" while the body actually showed 3 unresolved items. Now both
    # branches read the same total from the same helper, so they cannot drift apart again.
    uncounted_candidate_count = likely_count + unreviewed_count
    parts = []
    if confirmed_defect_count:
        parts.append(
            f"{confirmed_defect_count} proven defect{'s' if confirmed_defect_count != 1 else ''} weighed"
        )
    if verdict.strengths:
        strength_count = len(verdict.strengths)
        # QA regression, 2026-08-06 (round 5): naming the raw `strength_count` here as "credited"
        # overclaims past `_STRENGTH_BONUS_CAP` -- the bonus saturates at
        # `_STRENGTH_BONUS_SATURATION_POINT` items (see that helper's docstring; confirmed live by
        # both round-4 reviewers: 4 strengths and 10 strengths produce the identical final score).
        # Only the portion that actually reached the cap may be called "credited"; the remainder is
        # real content -- it still prints under "## Strengths noted" -- but it moved the score by
        # exactly zero, so it gets the same "not scored" register `refuted_challenge_count` below
        # already uses for its own zero-marginal-score items.
        credited_strength_count = min(strength_count, _STRENGTH_BONUS_SATURATION_POINT)
        parts.append(
            f"{credited_strength_count} strength{'s' if credited_strength_count != 1 else ''} credited"
        )
        overflow_strength_count = strength_count - credited_strength_count
        if overflow_strength_count:
            parts.append(f"{overflow_strength_count} more noted below (not scored)")
    # QA regression, 2026-08-06 (round 5): the round-4 fix above (`rejected_count`, used correctly
    # by the WITHHELD branch's "Rejected candidates" line) got copied into THIS clause wholesale,
    # but `compute_report_score`'s actual bonus (`_REFUTED_BONUS_PER_ITEM`, above) only ever reads
    # `len(verdict.refuted)` -- `challenged_out` is, per that constant's own comment, "intentionally
    # absent from the formula below, not just under-weighted." So a challenge-refuted item with an
    # EMPTY `verdict.refuted` still made this line say "1 disproved candidate credited" for a count
    # that moved nothing: confirmed live by removing the item and observing the printed number does
    # not change even though this clause's count did. "Credited" now attaches only to
    # `proof_refuted_count` -- the one count that actually feeds the deduction/bonus math -- and a
    # challenge-refuted item gets its own wording that says plainly it is shown, not scored.
    #
    # QA regression, 2026-08-06 (round 5, second finding): `proof_refuted_count` itself has the
    # identical cap-overclaim shape the strengths clause above was just fixed for --
    # `compute_report_score`'s refuted bonus saturates at `_REFUTED_BONUS_SATURATION_POINT` items
    # (confirmed live: 3 and 8 proof-refuted items produce the identical final score). "Credited" now
    # names only the capped portion; the overflow folds into the SAME "more ruled out below (not
    # scored)" bucket `refuted_challenge_count` already populates just below, since both are real,
    # executed disproofs rendered together under the one "## Ruled out" section (`_ruled_out_lines`)
    # without moving the number -- one combined count, not two back-to-back "more ruled out" clauses
    # a reader could not tell apart.
    proof_refuted_count = len(verdict.refuted)
    credited_refuted_count = min(proof_refuted_count, _REFUTED_BONUS_SATURATION_POINT)
    if credited_refuted_count:
        parts.append(
            f"{credited_refuted_count} disproved candidate"
            f"{'s' if credited_refuted_count != 1 else ''} credited"
        )
    more_ruled_out_count = (proof_refuted_count - credited_refuted_count) + refuted_challenge_count
    if more_ruled_out_count:
        parts.append(f"{more_ruled_out_count} more ruled out below (not scored)")
    if parts:
        detail = ", ".join(parts)
    else:
        # Reachable only when confirmed_defect_count == 0 and candidate_present is False, i.e.
        # nothing live is left unresolved -- the WITHHELD branch above already caught every case
        # where an unreviewed challenge would otherwise make this fallback a false "nothing found".
        detail = "nothing counted against or for it this pass"
    # Say so honestly rather than silently omitting them: an unproven primary or an unreviewed
    # survey row is real content in the report body even though it carries zero score weight.
    if uncounted_candidate_count:
        detail += (
            f"; {uncounted_candidate_count} unproven candidate"
            f"{'s' if uncounted_candidate_count != 1 else ''} below "
            f"{'are' if uncounted_candidate_count != 1 else 'is'} not counted either way"
        )
    return [
        f"**Score: {score:.1f}/10** — runtime gut-check from {detail}; severity and confidence "
        "are computed from harm_class and proof state, never self-asserted by the model.",
        "",
    ]


def _table_cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ").strip()


_TITLE_DUPLICATE_CUTOFF = 0.72


def _dedupe_restated_findings(
    entries: list[tuple[VerdictFinding, str]],
) -> list[tuple[VerdictFinding, str, int]]:
    """Collapse restatements of the SAME underlying gap into one representative row.

    QA regression, 2026-08-04: a nomination pass returned six rows for one gap -- 'No Token
    Revocation by Time' / 'by User' / 'by Token' / 'by Session' / 'by IP Address' / 'by Device' --
    padding the findings table with variations on a theme rather than the non-repetitive report the
    mandate asks for. These titles differ by one trailing qualifier and share the same file, which
    is exactly the shape a genuinely distinct finding does NOT have (different files, or
    substantially different wording). Two findings are merged only when BOTH the title is a close
    restatement (`difflib` ratio, case-insensitive) AND the file is the same -- a coincidentally
    similar title in a different file is never merged, so two real, separate defects that happen to
    read alike are not silently combined into one.
    """
    kept: list[tuple[VerdictFinding, str, int]] = []
    for finding, confidence in entries:
        title = str(finding.title or "").strip().lower()
        file_key = str(finding.file or "").strip().lower()
        merged = False
        for index, (existing_finding, existing_confidence, count) in enumerate(kept):
            existing_title = str(existing_finding.title or "").strip().lower()
            existing_file = str(existing_finding.file or "").strip().lower()
            if file_key != existing_file:
                continue
            if difflib.SequenceMatcher(None, title, existing_title).ratio() < _TITLE_DUPLICATE_CUTOFF:
                continue
            # Keep the stronger-evidence row as the representative; only the count grows.
            keep_existing = (
                _CONFIDENCE_RANK.get(existing_confidence, 9),
                _SEVERITY_RANK.get(compute_severity(existing_finding.harm_class), 9),
            ) <= (
                _CONFIDENCE_RANK.get(confidence, 9),
                _SEVERITY_RANK.get(compute_severity(finding.harm_class), 9),
            )
            representative = (existing_finding, existing_confidence) if keep_existing else (finding, confidence)
            kept[index] = (representative[0], representative[1], count + 1)
            merged = True
            break
        if not merged:
            kept.append((finding, confidence, 1))
    return kept


def _finding_table_rows(
    entries: list[tuple[VerdictFinding, str]], *, default_target: str
) -> list[str]:
    """Location | Issue | Category | Severity | Confidence | Description, sorted by confidence
    then severity (Finding E point 8) -- the runtime computes Severity, the model never asserts it
    (point 4). Restatements of one underlying gap are collapsed first (`_dedupe_restated_findings`)
    so the table reports distinct issues, not a title's near-synonyms."""
    deduped = _dedupe_restated_findings(entries)
    ordered = sorted(
        deduped,
        key=lambda row: (
            _CONFIDENCE_RANK.get(row[1], 9),
            _SEVERITY_RANK.get(compute_severity(row[0].harm_class), 9),
        ),
    )
    lines = [
        "| Location | Issue | Category | Severity | Confidence | Description |",
        "|---|---|---|---|---|---|",
    ]
    for finding, confidence, count in ordered:
        location = finding.location or default_target or "this file"
        category = _table_cell(finding.harm_class) or "unclassified"
        title = _table_cell(finding.title)
        if count > 1:
            title += f" (+{count - 1} similar variant(s) collapsed)"
        lines.append(
            f"| `{location}` | {title} | {category} | "
            f"{finding.severity} | {confidence} | {_table_cell(finding.failure_scenario)} |"
        )
    return lines


def _additional_finding_lines(verdict: AuditVerdict) -> list[str]:
    """Every other candidate that survived source checking, each labelled for what it is.

    Deliberately not called "findings" in the heading and deliberately never affirmative: none of
    these was executed against, so each is a citation the evidence supports and nothing more. The
    wording has to survive being read by someone who skips the preamble. A columned table
    (Location / Issue / Category / Severity / Confidence / Description) rather than a bare bullet
    list, sorted so the strongest-evidence, highest-severity rows lead (Finding E point 8).
    """

    if not verdict.additional_findings:
        return []
    lines = [
        "",
        "## Other candidates found in the same pass",
        "",
        "Each matches the source that was read. None was executed against, so none is proven:",
        "",
    ]
    entries = [
        (item, item.confidence or CONFIDENCE_UNREVIEWED) for item in verdict.additional_findings
    ]
    lines += _finding_table_rows(entries, default_target=verdict.target_path)
    return lines


def _strengths_lines(verdict: AuditVerdict) -> list[str]:
    """What the audit found done WELL, pulled entirely out of the bug-shaped schema (point 3): a
    review that lists only faults is not a review, and `failure_scenario`'s own contract ("the
    concrete input or state, then the wrong outcome") cannot honestly describe something good."""
    if not verdict.strengths:
        return []
    lines = ["", "## Strengths noted", ""]
    for item in verdict.strengths:
        location = item.location or verdict.target_path or "this file"
        lines.append(f"- **{item.title}** — `{location}`. {item.failure_scenario}".rstrip())
    return lines


def _ruled_out_lines(verdict: AuditVerdict) -> list[str]:
    """Two genuinely different outcomes live in `verdict.challenged_out`, and this function is the
    one place responsible for not rendering them as the same claim (see the field's docstring on
    `AuditVerdict.challenged_out` for the live-observed defect this was written to fix).

    - Proof-refuted (`verdict.refuted`) and challenge-refuted (`challenged_out` items with
      `item["verdict"] == "refuted"`) are candidates a REAL, executed check disproved -- a proof
      test that ran and exited 0, or a challenge that actually ran (execution or a deterministic
      source contradiction) and found one. These go under '## Ruled out', wording unchanged from
      before this fix.
    - Every other `challenged_out` item -- `item["verdict"] == "uncertain"`, or any other/missing
      value, all treated the same way here because an unrecognized value must never be assumed
      refuted -- never had its challenge run at all: the call was refused (e.g. the audit's total
      call budget was already spent), the reply was empty, or it could not be parsed. Calling that
      "ruled out by an adversarial source check" asserts a check happened when it didn't, which is
      exactly the false claim a live audit produced. These get their own '## Not reviewed' section
      (a separate top-level heading, not a bullet under '## Ruled out', so the section title itself
      never implies a check happened) with wording that cannot be misread as a check having run.
      They are kept, not dropped -- the operator should still learn the candidate was found, even
      though it was never checked.
    """
    refuted_challenges = _refuted_challenges(verdict.challenged_out)
    unreviewed_challenges = _unreviewed_challenges(verdict.challenged_out)

    lines: list[str] = []
    if verdict.refuted or refuted_challenges:
        lines += ["", "## Ruled out", ""]
        for item in verdict.refuted:
            title = str(item.get("title") or "a candidate")
            counter = str(item.get("counterexample") or "the proof test exited 0")
            lines.append(f"- **{title}** — disproved. {counter}")
        for item in refuted_challenges:
            title = str(item.get("title") or "a candidate")
            reason = str(item.get("reason") or "the adversarial source check could not sustain it")
            lines.append(f"- **{title}** — ruled out by an adversarial source check. {reason}")

    if unreviewed_challenges:
        lines += [
            "",
            "## Not reviewed",
            "",
            "Found, but the adversarial check never ran against them -- the audit's call budget "
            "ran out, the challenge reply was empty, or it could not be parsed. Neither confirmed "
            "nor ruled out:",
            "",
        ]
        for item in unreviewed_challenges:
            title = str(item.get("title") or "a candidate")
            reason = str(item.get("reason") or "the challenge never ran")
            lines.append(f"- **{title}** — not reviewed. {reason}")

    return lines


def _fixes_lines(verdict: AuditVerdict) -> list[str]:
    """One row for the primary finding's `suggested_fix` (Finding E point 5) -- and ONLY when that
    finding was actually CONFIRMED (`state == PROVEN`). Never invented: a finding without a
    `suggested_fix` simply has no row here, rather than a placeholder.

    Live incident, 2026-08-06, session `openclaw:d77e4cf7487fcb92b78c`: a `NO_FINDING` report
    (`Confirmed findings: 0`) still rendered a `## Fixes` section with five suggested patches, one
    per `additional_findings` row -- directly against that turn's own explicit instruction, "do not
    recommend a fix unless the reproduction confirms the failure." The prior version of this
    function took an `include_primary` flag (`True` for `PROVEN` and `CANDIDATE_UNPROVEN`, `False`
    for `REFUTED`/`NO_FINDING`) but ALWAYS appended `verdict.additional_findings` regardless of
    state -- and `CANDIDATE_UNPROVEN` passed `include_primary=True` too, so an unproven, unexecuted
    primary's own suggested fix rendered as if it were a confirmed recommendation. Neither
    `additional_findings` nor a `CANDIDATE_UNPROVEN` primary can ever reach `CONFIDENCE_PROVEN` by
    construction (see `_verdict_finding`/`_survey_findings`), so gating on `state == PROVEN` alone
    is exhaustive: it is the only state a fix may ever render under, for any finding.
    """
    if verdict.state != PROVEN or verdict.finding is None:
        return []
    suggested = str(verdict.finding.suggested_fix or "").strip()
    if not suggested:
        return []
    location = verdict.finding.location or verdict.target_path or "this file"
    return ["", "## Fixes", "", f"- **{verdict.finding.title}** (`{location}`): {suggested}"]


def _proof_lines(verdict: AuditVerdict) -> list[str]:
    """The proof run as command / observed / expected / verdict.

    The generated test's SOURCE is deliberately absent. It is an artifact of how the claim was
    checked, not evidence about the code, and pasting it into a normal answer buries the four facts
    that settle the question. It stays in the workspace and in Activity for anyone who asks.
    """
    proof = verdict.proof
    if proof is None or not proof.attempted:
        return []
    reproduced = verdict.state == PROVEN
    lines = [
        "",
        "## Proof" if reproduced else "## Proof run",
        f"- Command: `{proof.test_command}` (exit code {proof.returncode})",
    ]
    if proof.output_digest:
        lines.append(f"- Observed: {proof.output_digest}")
    if reproduced:
        lines.append(
            "- Expected: the same command exits 0 once the defect is fixed; it does not on this code"
        )
        lines.append("- Verdict: CONFIRMED")
    else:
        lines.append("- Verdict: NOT CONFIRMED")
    if proof.test_path:
        lines.append(f"- Test artifact: `{proof.test_path}` (source not shown; ask to see it)")
    return lines


def _primary_classification_bullets(finding: VerdictFinding, confidence: str) -> list[str]:
    """Category / Severity / Confidence, the three findings-table columns that don't already have
    a bullet of their own in the headline block (Finding E point 8). Omitted when the model never
    supplied a harm_class -- an undeclared category is not printed as a confident one."""
    if not finding.harm_class:
        return []
    return [
        f"- Category: {finding.harm_class}",
        f"- Severity: {finding.severity}",
        f"- Confidence: {confidence}",
    ]


def render_audit_report(verdict: AuditVerdict) -> str:
    """The operator-visible report. A pure function of the typed state — no model call, no branch
    that can disagree with `asserts_reproduction`."""
    state = verdict.state
    finding = verdict.finding
    lines: list[str]

    if state == PROVEN and finding is not None:
        lines = [
            f"## Bug reproduced — `{finding.location}`",
            "",
            f"**{finding.title}** — reproduced by a failing test on the current code.",
            "",
            f"- Cited line {finding.line_start}: `{finding.cited_line_text}`",
            f"- Failure scenario: {finding.failure_scenario}",
        ]
        lines += _primary_classification_bullets(finding, CONFIDENCE_PROVEN)
        lines += _proof_lines(verdict)
        lines += _additional_finding_lines(verdict)
        lines += _strengths_lines(verdict)
        lines += _ruled_out_lines(verdict)
        lines += _fixes_lines(verdict)
        if verdict.analysis:
            lines += ["", "## Analysis", verdict.analysis]
    elif state in (CANDIDATE_UNPROVEN, REFUTED) and (finding is not None or state == REFUTED):
        # SCALPEL fix, failure class B, 2026-08-06: these two branches used to print the full
        # candidate table, strengths, ruled-out breakdown, and (for CANDIDATE_UNPROVEN) a headline
        # speculative candidate — real content, but for a request that wants exactly one confirmed
        # finding or a concise refusal, an unproven or disproved primary is exactly the zero-
        # confirmed case that content must not leak into. Both now render the SAME minimal body
        # `NO_FINDING` already used, with the state's own reason folded into "where the search
        # ended" instead of a separate verbose paragraph. Candidate-by-candidate detail — the
        # primary's own citation/scenario, the proof command and output, strengths, ruled-out — is
        # unchanged in `details.stepped_audit` for Activity; only the CHAT answer is trimmed.
        title = finding.title if finding is not None else "the candidate"
        reason = str(verdict.blocked_reason or verdict.remaining_proof or "").strip()
        lines = [
            "## No verified bug found",
            "",
            (
                f"**{title}** was tested and the test passed, which disproves the claim rather "
                "than supporting it."
                if state == REFUTED
                else "The candidate found during this pass did not survive to a confirmed bug, "
                "so I won't present it as a real defect."
            ),
        ]
        if reason:
            lines += ["", f"- Where the search ended: {reason}"]
        lines += ["", "_Candidate-by-candidate detail for this pass is in Activity._"]
    elif state == NO_FINDING:
        # The operator asked for a bug. The answer is that there isn't one they can act on, said in
        # those words — the old heading ("No checkable finding") named an internal terminal state,
        # and a reader who does not know the state machine cannot tell whether that means "clean
        # file" or "the audit broke".
        #
        # Live incident, 2026-08-06, session `openclaw:d77e4cf7487fcb92b78c`: this branch used to
        # ALSO inline the full candidate table (`_additional_finding_lines`), strengths, and the
        # per-item `_ruled_out_lines` breakdown ("## Ruled out" / "## Not reviewed") -- real content,
        # but it sat directly beneath a `blocked_reason` sentence that (before the fix above)
        # falsely claimed universal adversarial rejection while the very detail below it showed
        # `Rejected candidates: 0` and 8 items explicitly reasoned as "never reviewed". A NOT-PROVEN
        # verdict is exactly the state where a wrong or padded final answer does the most damage,
        # since there is no confirmed finding to anchor the reader against. The WITHHELD score block
        # above (`_score_lines`) already states the four counts that matter (confirmed / likely /
        # unreviewed / rejected) from the same source `compose_search_ended_reason` now uses for
        # this sentence, so the two cannot disagree; the candidate-by-candidate detail remains fully
        # intact in `details.stepped_audit` for Activity, it is simply no longer duplicated -- and
        # risking self-contradiction -- in the chat-facing final answer.
        lines = [
            "## No verified bug found",
            "",
            "The candidates found during this audit did not survive verification against the "
            "source, so I won't present one as a real defect.",
        ]
        if verdict.blocked_reason:
            lines += ["", f"- Where the search ended: {verdict.blocked_reason}"]
        lines += ["", "_Candidate-by-candidate detail for this pass is in Activity._"]
    else:  # BLOCKED
        lines = [
            "## Audit blocked",
            "",
            "The audit stopped before it could reach a verdict, so it reports no finding.",
            "",
            f"- Reason: {verdict.blocked_reason or 'the audit could not proceed'}",
        ]
        lines += _ruled_out_lines(verdict)

    if verdict.continuation_note:
        # Above the evidence, below the headline: the operator must read what this finding IS
        # before they read how well supported it is.
        lines.insert(1, "")
        lines.insert(2, f"> {verdict.continuation_note}")

    # Header last, prepended: target(s), a one-word status, and coverage inline (Finding E point
    # 8). Built after the branch above so `continuation_note`'s insert-at-index-1 logic keeps
    # operating on the state-specific headline it was written against.
    lines = _header_lines(verdict) + _score_lines(verdict) + lines

    if verdict.target_path:
        lines += ["", f"_Target: `{verdict.target_path}`._"]
    else:
        # Say the scope out loud rather than omitting the line. A pass with no single subject used
        # to be handed `inspected_paths[0]` and printed it as the target, so a 189-file sweep read
        # as an audit of one file nobody had named; now the absence is stated, because a silently
        # missing Target line is the same ambiguity one step quieter.
        lines += [
            "",
            "_Scope: the whole repository — this pass was not narrowed to a single file, so there "
            "is no one target to name._",
        ]
    if verdict.usage_line:
        lines += ["", verdict.usage_line]
    if verdict.model_label:
        proof_ran = verdict.proof is not None and verdict.proof.attempted and bool(
            verdict.proof.test_command
        )
        if verdict.state in {PROVEN, REFUTED}:
            evidence_basis = "the executed proof result"
        elif verdict.state == CANDIDATE_UNPROVEN:
            # A candidate CAN have an attempted proof that settled nothing. Saying "no proof command
            # ran" in that case contradicts the proof-run section printed directly above it, which
            # is the same class of self-contradiction this module exists to make impossible.
            evidence_basis = (
                "the checked source artifacts and a proof run that settled nothing"
                if proof_ran
                else "the checked source artifacts; no proof command ran"
            )
        else:
            # "the bounded audit state" is the runtime's own vocabulary for its state machine, and
            # naming it in a normal answer tells the reader nothing they can use. What they can use
            # is what the verdict was read FROM.
            evidence_basis = "the source that was read and the checks that ran against it"
        lines += [
            "",
            f"_Audited stepwise by `{verdict.model_label}`; the verdict above is composed by the "
            f"runtime from {evidence_basis}, not written by the model._",
        ]
    lines += [
        "",
        "_Detailed tool activity and token composition remain available in Activity._",
    ]
    return _sanitizer_safe("\n".join(lines))


def _sanitizer_safe(text: str) -> str:
    """Neutralize the substrings that make the chat sanitizer read a report as a leaked traceback.

    Driven live 2026-08-01: an errored proof's digest carries `ERROR:` headers, every audit report
    contains "line " because it CITES lines, and the report is multi-line — together that satisfies
    `looks_like_runtime_traceback` and `sanitize_user_chat_text` replaced the whole report with
    "I couldn't resolve that cleanly."
    """
    return (
        str(text or "")
        .replace("Traceback (most recent call last)", "trace")
        .replace('File "', "file ")
        .replace("ERROR:", "ERROR —")
        .replace("Error:", "Error —")
        .replace("error:", "error —")
    )


def sample_verdict(state: str) -> AuditVerdict:
    """A representative verdict for each terminal state.

    Exists so the contradiction test can exercise EVERY state through the real renderer rather than
    the states a particular scripted run happens to reach.
    """
    finding = VerdictFinding(
        title="Malformed record silently truncates the output",
        file="api/apache/codec.py",
        line_start=143,
        line_end=152,
        cited_line_text="            except: break",
        failure_scenario="A trailing 0x80 varint returns the first 89 bytes and raises nothing.",
    )
    proof = VerdictProof(
        attempted=True,
        test_path="generated/proof/test_codec_bug.py",
        test_command="python3 -m unittest -v test_codec_bug",
        returncode=1,
        output_digest="Ran 1 test | FAILED (failures=1)",
        note="",
    )
    if state == PROVEN:
        return AuditVerdict(state=state, finding=finding, proof=proof, analysis="The decoder swallows malformed records.", model_label="m")
    if state == CANDIDATE_UNPROVEN:
        return AuditVerdict(state=state, finding=finding, remaining_proof="What proof remains: a failing test.", model_label="m")
    if state == REFUTED:
        green = VerdictProof(attempted=True, test_path=proof.test_path, test_command=proof.test_command, returncode=0, output_digest="Ran 1 test | OK", note="passed")
        return AuditVerdict(state=state, finding=finding, proof=green, model_label="m")
    if state == NO_FINDING:
        return AuditVerdict(state=state, blocked_reason="two citations were refuted by the source", model_label="m")
    return AuditVerdict(state=BLOCKED, blocked_reason="the audit evidence carried no readable source", model_label="m")
