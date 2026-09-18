"""QA regression, 2026-08-06 -- two defects found in `_score_lines`' accounting text, alongside (and
after) the WITHHELD-vs-numeric score gate itself (`test_score_is_withheld_when_nothing_is_confirmed.py`),
which is correct and untouched by this round.

DEFECT 1 -- a Challenged finding gets mislabeled 'Unreviewed' in the score line.

`verdict.additional_findings` has two different-provenance sources in the real pipeline
(`stepped_audit.py`):

- `_survey_findings` rows -- genuinely `CONFIDENCE_UNREVIEWED`, never challenged.
- `extra_findings` rows, each built via `_verdict_finding()`, which DEFAULTS confidence to
  `CONFIDENCE_CHALLENGED` because every item reaching `extra_findings` already survived the SAME
  adversarial `_challenge_finding` gate the primary did (only appended when
  `challenge.verdict == "supported"`). These are "likely", exactly like a `CANDIDATE_UNPROVEN`
  primary -- NOT unreviewed.

`_score_lines` used to bucket every item in `additional_findings` as 'unreviewed' purely by list
membership (`len(verdict.additional_findings)`), so a `CANDIDATE_UNPROVEN` verdict with one
`additional_findings` item carrying `confidence=CONFIDENCE_CHALLENGED` rendered 'Unreviewed
candidates: 1' in the score line while the SAME row's table cell, three lines below, correctly said
'Confidence: Challenged' -- an internal contradiction inside one report, the exact class of defect
this module's own docstring says it exists to prevent.

DEFECT 2 -- the PROVEN-branch (fallback/numeric) count omits `unreviewed_challenges`.

`uncounted_candidate_count` in the fallback/numeric branch used to be its own independently-written
`likely_count + len(verdict.additional_findings)`, which silently dropped `unreviewed_challenges`
that the WITHHELD branch's `unreviewed_count` already included. A PROVEN primary + 2
`additional_findings` + 1 unreviewed `challenged_out` item rendered '2 unproven candidates below are
not counted either way' while the body actually showed 3 unresolved items (2 table rows + 1 'Not
reviewed' bullet).

Defects 1 and 2 route through the one new shared helper, `_unresolved_candidate_counts`, so the
WITHHELD block and the fallback/numeric branch cannot independently drift apart again the way they
already did once.

DEFECT 3 -- the fallback/numeric branch's "disproved candidate credited" clause omits
challenge-refuted `challenged_out` items.

`rejected_count` (`= len(verdict.refuted) + refuted_challenge_count`) is computed once and already
used correctly by the WITHHELD branch's "Rejected candidates" line and by `_ruled_out_lines`'s body
rendering. The fallback/numeric branch's own "N disproved candidate(s) credited" clause never got
the same treatment -- it was gated on bare `verdict.refuted` (proof-disproved items only), so a
PROVEN primary plus one challenge-refuted `challenged_out` item (`verdict == "refuted"`) and an
EMPTY `verdict.refuted` list rendered a score line with no mention of the disproved candidate that
the body's own '## Ruled out' section showed directly below it. The fix reads `rejected_count`
instead of re-deriving from `verdict.refuted` alone, so the score line and the body can no longer
disagree about how many candidates were disproved.

ROUND 5 CORRECTION, 2026-08-06: DEFECT 3's fix above over-corrected into a new defect -- it credited
the MERGED `rejected_count` (proof-refuted + challenge-refuted) as one lump "N disproved candidate(s)
credited", but `compute_report_score`'s bonus only ever reads `len(verdict.refuted)`;
`challenged_out` is "intentionally absent from the formula below, not just under-weighted"
(`_REFUTED_BONUS_PER_ITEM`'s own comment, audit_verdict.py). So a challenge-refuted item with an
empty `verdict.refuted` still said "credited" for a count that moved the printed score by exactly
zero. The three tests originally written for DEFECT 3 below are rewritten to assert the corrected,
honest split -- see the "DEFECT 4" section below.

DEFECT 5 (round 5, found by both round-4 reviewers independently against the then-live code) -- the
"N strength(s) credited" and "N disproved candidate(s) credited" clauses (the honest split DEFECT 4
just produced) both name the RAW `len(...)` of `verdict.strengths` / `verdict.refuted`, but each
feeds a CAPPED bonus in `compute_report_score`:

- `bonus = min(len(verdict.strengths) * _STRENGTH_BONUS_PER_ITEM, _STRENGTH_BONUS_CAP)` -- with the
  live constants (0.4 per item, cap 1.5) this saturates at the 4th strength. 4 strengths and 10
  strengths produce the IDENTICAL final score, yet the old text said "4 strengths credited" vs "10
  strengths credited" -- claiming 6 more items moved a number they could not have touched.
- `bonus += min(len(verdict.refuted) * _REFUTED_BONUS_PER_ITEM, _REFUTED_BONUS_CAP)` -- with the
  live constants (0.15 per item, cap 0.45) this saturates at the 3rd proof-refuted item. 3 and 8
  produce the identical score, yet the old text said "3 disproved candidates credited" vs "8
  disproved candidates credited".

This is the exact false-confidence shape DEFECT 4's own fix-comment named as the thing to avoid
("credited" must mean "moved the score") -- just on two clauses that round didn't touch, because no
existing test asserted on the RENDERED TEXT at a count above either cap. The fix: "credited" now
names only `min(count, saturation_point)` (`_bonus_saturation_point`, audit_verdict.py); the
overflow portion is real, still-rendered content (strengths still print under "## Strengths noted",
proof-refuted items still print under "## Ruled out") that gets separate, honest "not scored"
wording instead.
"""
from __future__ import annotations

from core.agent_runtime.audit_verdict import (
    _REFUTED_BONUS_SATURATION_POINT,
    _STRENGTH_BONUS_SATURATION_POINT,
    CANDIDATE_UNPROVEN,
    CONFIDENCE_CHALLENGED,
    CONFIDENCE_UNREVIEWED,
    PROVEN,
    AuditVerdict,
    VerdictFinding,
    VerdictProof,
    _score_lines,
    _unresolved_candidate_counts,
    compute_report_score,
    render_audit_report,
)


def _finding(title: str, *, harm_class: str = "integrity", confidence: str = "") -> VerdictFinding:
    return VerdictFinding(
        title=title,
        file="api/x.py",
        line_start=10,
        line_end=12,
        cited_line_text="pass",
        failure_scenario="A concrete input reaches the wrong outcome.",
        harm_class=harm_class,
        confidence=confidence or CONFIDENCE_UNREVIEWED,
    )


def _score_block(report: str) -> str:
    lines = report.splitlines()
    start = next(
        i for i, line in enumerate(lines)
        if line.startswith("**Audit score:") or line.startswith("**Score:") or line.startswith("**NOT PROVEN**")
    )
    end = next((i for i in range(start, len(lines)) if lines[i] == "" and i > start), len(lines))
    return "\n".join(lines[start:end])


# ----------------------------------------------------------------------------------------------
# DEFECT 1 -- the real production shape: `extra_findings`' own default confidence, CHALLENGED, must
# be attributed as "likely", never "unreviewed", and must not contradict the table cell.
# ----------------------------------------------------------------------------------------------


def test_a_challenged_additional_finding_is_not_mislabeled_unreviewed_in_the_score_line() -> None:
    verdict = AuditVerdict(
        state=CANDIDATE_UNPROVEN,
        finding=_finding("Unproven primary", harm_class="integrity"),
        additional_findings=[
            _finding("Extra finding from a challenge-survived candidate", harm_class="crash", confidence=CONFIDENCE_CHALLENGED),
        ],
    )

    report = render_audit_report(verdict)
    score_block = _score_block(report)

    # SCALPEL fix, 2026-08-06: CANDIDATE_UNPROVEN now renders the same concise NOT-PROVEN contract
    # as NO_FINDING (Challenged/Confirmed/Rejected/Unreviewed) -- the primary itself has no row of
    # its own here (it IS the "NOT PROVEN" subject), so only the ADDITIONAL finding's own
    # confidence counts: the CHALLENGED extra must land in "Challenged", never "Unreviewed".
    assert "- Challenged: 1" in score_block, score_block
    assert "- Unreviewed: 0" in score_block, (
        "a CONFIDENCE_CHALLENGED additional finding must not be counted as unreviewed:\n" + score_block
    )

    # The candidate-by-candidate table itself no longer renders in chat for this state (Activity
    # only) -- the score-line count above is now the only chat-facing signal, and it must not
    # contradict the underlying data's own confidence field.
    assert verdict.additional_findings[0].confidence == CONFIDENCE_CHALLENGED


def test_the_unreviewed_control_a_genuine_survey_row_still_counts_as_unreviewed() -> None:
    """Control for defect 1: the genuine `_survey_findings` shape (confidence == UNREVIEWED,
    never challenged) must still count as unreviewed -- no regression from the fix."""
    verdict = AuditVerdict(
        state=CANDIDATE_UNPROVEN,
        finding=_finding("Unproven primary", harm_class="integrity"),
        additional_findings=[
            _finding("Genuine survey row", harm_class="crash", confidence=CONFIDENCE_UNREVIEWED),
        ],
    )

    report = render_audit_report(verdict)
    score_block = _score_block(report)

    assert "- Challenged: 0" in score_block, score_block
    assert "- Unreviewed: 1" in score_block, score_block
    assert verdict.additional_findings[0].confidence == CONFIDENCE_UNREVIEWED


def test_a_mixed_challenged_and_unreviewed_pair_does_not_conflate_the_two_counts() -> None:
    verdict = AuditVerdict(
        state=CANDIDATE_UNPROVEN,
        finding=_finding("Unproven primary", harm_class="integrity"),
        additional_findings=[
            _finding("Extra finding, challenge-survived", harm_class="crash", confidence=CONFIDENCE_CHALLENGED),
            _finding("Survey row, never challenged", harm_class="perf", confidence=CONFIDENCE_UNREVIEWED),
        ],
    )

    report = render_audit_report(verdict)
    score_block = _score_block(report)

    # The primary has no row of its own in the new NOT-PROVEN framing; only the additional
    # findings' own confidence counts -- the one CHALLENGED row -> Challenged, the one UNREVIEWED
    # row -> Unreviewed, never conflated.
    assert "- Challenged: 1" in score_block, score_block
    assert "- Unreviewed: 1" in score_block, score_block

    likely_count, unreviewed_count = _unresolved_candidate_counts(verdict)
    # _unresolved_candidate_counts itself still includes the primary's own +1 (it is a general
    # helper also used by other states) -- 2 likely (primary + the CHALLENGED extra), 1 unreviewed.
    assert (likely_count, unreviewed_count) == (2, 1)


# ----------------------------------------------------------------------------------------------
# DEFECT 2 -- the fallback/numeric (PROVEN) branch must include `unreviewed_challenges` in its
# "not counted either way" total, matching the body's actual unresolved-item count.
# ----------------------------------------------------------------------------------------------


def test_proven_branch_uncounted_count_includes_unreviewed_challenges_not_just_additional_findings() -> None:
    verdict = AuditVerdict(
        state=PROVEN,
        finding=_finding("Proven crash", harm_class="crash"),
        proof=VerdictProof(attempted=True, test_command="pytest t.py", returncode=1),
        additional_findings=[
            _finding("Extra finding A", harm_class="integrity", confidence=CONFIDENCE_CHALLENGED),
            _finding("Extra finding B", harm_class="perf", confidence=CONFIDENCE_UNREVIEWED),
        ],
        challenged_out=[
            {
                "title": "Never actually checked claim",
                "verdict": "uncertain",
                "reason": "the audit's bounded call budget was spent",
            }
        ],
    )

    report = render_audit_report(verdict)
    score_line = next(line for line in report.splitlines() if line.startswith("**Score:"))

    # 2 additional_findings rows + 1 unreviewed challenged_out item = 3 unresolved items total,
    # matching the 2 table rows + 1 "Not reviewed" bullet the body actually renders.
    assert "3 unproven candidates below are not counted either way" in score_line, score_line
    assert "## Not reviewed" in report, report

    likely_count, unreviewed_count = _unresolved_candidate_counts(verdict)
    assert likely_count + unreviewed_count == 3


# ----------------------------------------------------------------------------------------------
# Sabotage discipline (CLAUDE.md section 6): each fix, reverted alone, must fail a test that names
# the exact wrong output it reintroduces.
# ----------------------------------------------------------------------------------------------


def test_sabotage_defect_1_alone_reproduces_the_mislabel() -> None:
    """Reverts ONLY defect 1's fix (buckets every `additional_findings` row as unreviewed by list
    membership, ignoring `.confidence`) and proves it reproduces the exact mislabel; then proves the
    real, fixed code differs from it on the same input."""
    verdict = AuditVerdict(
        state=CANDIDATE_UNPROVEN,
        finding=_finding("Unproven primary", harm_class="integrity"),
        additional_findings=[
            _finding("Extra finding, challenge-survived", harm_class="crash", confidence=CONFIDENCE_CHALLENGED),
        ],
    )

    def _sabotaged_unreviewed_count(v: AuditVerdict) -> int:
        # The pre-fix formula: every additional_findings row counted as unreviewed by membership.
        from core.agent_runtime.audit_verdict import _unreviewed_challenges as _uc

        return len(v.additional_findings) + len(_uc(v.challenged_out))

    sabotaged_unreviewed = _sabotaged_unreviewed_count(verdict)
    assert sabotaged_unreviewed == 1, (
        "sabotage setup failed to reproduce the mislabel -- expected the old formula to count the "
        f"one CHALLENGED row as unreviewed, got {sabotaged_unreviewed}"
    )

    real_lines = _score_lines(verdict)
    real_text = "\n".join(real_lines)
    assert "- Unreviewed: 0" in real_text, (
        "the real, fixed code must not reproduce the sabotaged mislabel:\n" + real_text
    )
    assert "- Unreviewed: 1" not in real_text, real_text
    assert "- Challenged: 1" in real_text, real_text


def test_sabotage_defect_2_alone_reproduces_the_undercount() -> None:
    """Reverts ONLY defect 2's fix (the fallback branch's own `likely_count +
    len(additional_findings)`, omitting `unreviewed_challenges`) and proves it reproduces the exact
    undercount ('2' instead of '3'); then proves the real, fixed code differs from it.

    Both additional findings are left at the default (genuinely `CONFIDENCE_UNREVIEWED`) confidence
    here deliberately, so `likely_count` is 0 and this isolates defect 2 alone from defect 1's split
    -- with a CHALLENGED row mixed in, `likely_count + len(additional_findings)` double-counts that
    row (once via the split, once via the raw list length) and can coincidentally land on the same
    total as the fix, masking the very drift this test exists to catch.
    """
    verdict = AuditVerdict(
        state=PROVEN,
        finding=_finding("Proven crash", harm_class="crash"),
        proof=VerdictProof(attempted=True, test_command="pytest t.py", returncode=1),
        additional_findings=[
            _finding("Extra finding A", harm_class="integrity"),
            _finding("Extra finding B", harm_class="perf"),
        ],
        challenged_out=[
            {
                "title": "Never actually checked claim",
                "verdict": "uncertain",
                "reason": "the audit's bounded call budget was spent",
            }
        ],
    )

    likely_count, _unreviewed_count = _unresolved_candidate_counts(verdict)
    sabotaged_uncounted = likely_count + len(verdict.additional_findings)
    assert sabotaged_uncounted == 2, (
        "sabotage setup failed to reproduce the undercount -- expected the old fallback formula to "
        f"omit the unreviewed challenged_out item and land on 2, got {sabotaged_uncounted}"
    )

    real_lines = _score_lines(verdict)
    real_text = "\n".join(real_lines)
    assert "3 unproven candidates below are not counted either way" in real_text, (
        "the real, fixed code must not reproduce the sabotaged undercount:\n" + real_text
    )
    assert "2 unproven candidates below are not counted either way" not in real_text, real_text


# ----------------------------------------------------------------------------------------------
# DEFECT 3 (round 4) -- the fallback/numeric branch's "disproved candidate credited" clause used
# to drop a challenge-refuted `challenged_out` item entirely whenever `verdict.refuted` was empty.
# Round 4's fix merged both counts into `rejected_count` and printed "N disproved candidate(s)
# credited" for the sum -- which over-corrected into DEFECT 4 (round 5, below): `credited` now
# claimed a challenge-refuted item moved `compute_report_score`'s number when the formula
# (`_REFUTED_BONUS_PER_ITEM`, audit_verdict.py) never reads `challenged_out` at all, only
# `verdict.refuted`. These three tests used to assert round 4's over-corrected, still-wrong
# wording; they are rewritten below (round 5) to assert the honest split instead. See DEFECT 4.
# ----------------------------------------------------------------------------------------------


# ----------------------------------------------------------------------------------------------
# DEFECT 4 (round 5) -- "credited" must attach ONLY to `len(verdict.refuted)`, the one count that
# actually feeds `compute_report_score`'s deduction/bonus math. A challenge-refuted `challenged_out`
# item (`verdict == "refuted"`) is a real, executed falsification too, but is -- per
# `_REFUTED_BONUS_PER_ITEM`'s own comment -- "intentionally absent from the formula below, not just
# under-weighted." Round 4's fix said "N disproved candidate(s) credited" for
# `len(verdict.refuted) + refuted_challenge_count` combined, so a challenge-refuted item with an
# EMPTY `verdict.refuted` still made the line say "1 disproved candidate credited" for a count that
# moved the score by exactly zero -- confirmed live: removing the item leaves the printed score
# unchanged while this clause's old count would have dropped by one, as if it had mattered.
# ----------------------------------------------------------------------------------------------


def test_proven_branch_does_not_credit_a_challenge_refuted_candidate_with_an_empty_verdict_refuted() -> None:
    """The exact adversarial repro: a PROVEN primary with ONLY a challenge-refuted `challenged_out`
    item and an EMPTY `verdict.refuted`. 'credited' must not appear anywhere near this item's
    count, and the NUMERIC score must be identical whether or not the item is present at all --
    because `compute_report_score` never reads `challenged_out`, so the item cannot have moved it."""
    challenged_out = [
        {
            "title": "Some candidate",
            "verdict": "refuted",
            "reason": "the adversarial source check could not sustain it",
        }
    ]
    verdict_with_item = AuditVerdict(
        state=PROVEN,
        finding=_finding("Proven crash", harm_class="crash"),
        proof=VerdictProof(attempted=True, test_command="pytest t.py", returncode=1),
        challenged_out=challenged_out,
    )
    verdict_without_item = AuditVerdict(
        state=PROVEN,
        finding=_finding("Proven crash", harm_class="crash"),
        proof=VerdictProof(attempted=True, test_command="pytest t.py", returncode=1),
        challenged_out=[],
    )
    assert verdict_with_item.refuted == []

    # The number this item must never move, present or absent.
    from core.agent_runtime.audit_verdict import compute_report_score

    assert compute_report_score(verdict_with_item) == compute_report_score(verdict_without_item), (
        "a challenge-refuted item must never move compute_report_score's number -- it is "
        "intentionally absent from the formula"
    )

    report = render_audit_report(verdict_with_item)
    score_line = next(line for line in report.splitlines() if line.startswith("**Score:"))

    assert "credited" not in score_line, (
        "a challenge-refuted item with no proof-refuted sibling must not be described as "
        "'credited' -- it moved the score by exactly zero:\n" + score_line
    )
    assert "1 more ruled out below (not scored)" in score_line, score_line
    assert "## Ruled out" in report, report
    assert "- **Some candidate** — ruled out by an adversarial source check." in report, report


def test_proven_branch_credits_only_the_proof_refuted_candidate_in_a_mixed_pair() -> None:
    """Mixed case: one proof-disproved item (`verdict.refuted`) AND one challenge-refuted item
    (`challenged_out`, `verdict == "refuted"`) together. 'credited' must appear with count 1 --
    only the proof-refuted one, the one that actually feeds `compute_report_score` -- and the
    challenge-refuted one gets the separate, honest, non-'credited' phrasing."""
    verdict = AuditVerdict(
        state=PROVEN,
        finding=_finding("Proven crash", harm_class="crash"),
        proof=VerdictProof(attempted=True, test_command="pytest t.py", returncode=1),
        refuted=[
            {
                "title": "Proof-disproved candidate",
                "counterexample": "the proof test exited 0",
            }
        ],
        challenged_out=[
            {
                "title": "Challenge-refuted candidate",
                "verdict": "refuted",
                "reason": "the adversarial source check could not sustain it",
            }
        ],
    )

    report = render_audit_report(verdict)
    score_line = next(line for line in report.splitlines() if line.startswith("**Score:"))

    assert "1 disproved candidate credited" in score_line, score_line
    assert "2 disproved candidates credited" not in score_line, (
        "only the proof-refuted item may be 'credited' -- the challenge-refuted one must not be "
        "folded into the same credited count:\n" + score_line
    )
    assert "1 more ruled out below (not scored)" in score_line, score_line
    assert "- **Proof-disproved candidate** — disproved." in report, report
    assert "- **Challenge-refuted candidate** — ruled out by an adversarial source check." in report, report


def test_sabotage_reverting_the_credited_split_reproduces_the_false_claim() -> None:
    """Reverts ONLY round 5's wording split -- back to round 4's merged `rejected_count` driving a
    single 'N disproved candidate(s) credited' clause -- and proves it reproduces the exact false
    claim: a challenge-refuted item with an empty `verdict.refuted` reads as 'credited' even though
    it moved `compute_report_score`'s number by zero. Then proves the real, fixed code differs from
    it on the same input."""
    verdict = AuditVerdict(
        state=PROVEN,
        finding=_finding("Proven crash", harm_class="crash"),
        proof=VerdictProof(attempted=True, test_command="pytest t.py", returncode=1),
        challenged_out=[
            {
                "title": "Some candidate",
                "verdict": "refuted",
                "reason": "the adversarial source check could not sustain it",
            }
        ],
    )

    from core.agent_runtime.audit_verdict import _refuted_challenges

    sabotaged_rejected_count = len(verdict.refuted) + len(_refuted_challenges(verdict.challenged_out))
    sabotaged_parts = []
    if sabotaged_rejected_count:  # round 4's merged gate
        sabotaged_parts.append(
            f"{sabotaged_rejected_count} disproved candidate"
            f"{'s' if sabotaged_rejected_count != 1 else ''} credited"
        )
    assert sabotaged_parts == ["1 disproved candidate credited"], (
        "sabotage setup failed to reproduce round 4's false claim -- expected the merged gate to "
        f"credit the challenge-refuted item, got {sabotaged_parts}"
    )

    real_lines = _score_lines(verdict)
    real_text = "\n".join(real_lines)
    assert "1 disproved candidate credited" not in real_text, (
        "the real, fixed code must not reproduce the sabotaged false 'credited' claim:\n" + real_text
    )
    assert "1 more ruled out below (not scored)" in real_text, real_text


# ----------------------------------------------------------------------------------------------
# DEFECT 5 (round 5, found independently by both round-4 reviewers against the then-live,
# unmodified code) -- "N strength(s) credited" and "N disproved candidate(s) credited" (the honest
# split DEFECT 4 above produced) both named the RAW `len(...)` past the point where
# `compute_report_score`'s bonus actually saturates: `_STRENGTH_BONUS_CAP` (0.4/item, cap 1.5,
# saturating at the 4th strength) and `_REFUTED_BONUS_CAP` (0.15/item, cap 0.45, saturating at the
# 3rd proof-refuted item). Proof, both live-confirmed: 4 vs 10 strengths score identically; 3 vs 8
# proof-refuted items score identically -- yet the old text claimed every raw count moved the score.
# ----------------------------------------------------------------------------------------------


def test_strengths_beyond_the_bonus_cap_are_named_separately_not_credited() -> None:
    """Boundary: `_STRENGTH_BONUS_SATURATION_POINT` + 1 strengths (5 today). The score line must cap
    'credited' at the saturation point and name the overflow with separate, honest wording -- never
    'credited', since it moved the score by exactly zero."""
    cap_point = _STRENGTH_BONUS_SATURATION_POINT
    strengths = [_finding(f"Strength {i}", harm_class="strength") for i in range(cap_point + 1)]

    verdict = AuditVerdict(
        state=PROVEN,
        finding=_finding("Proven crash", harm_class="crash"),
        proof=VerdictProof(attempted=True, test_command="pytest t.py", returncode=1),
        strengths=strengths,
    )
    verdict_at_cap = AuditVerdict(
        state=PROVEN,
        finding=_finding("Proven crash", harm_class="crash"),
        proof=VerdictProof(attempted=True, test_command="pytest t.py", returncode=1),
        strengths=strengths[:cap_point],
    )

    # Proves the (cap_point + 1)th strength really does move nothing -- if this fails, the test's
    # own premise is wrong, not the renderer.
    assert compute_report_score(verdict) == compute_report_score(verdict_at_cap)

    report = render_audit_report(verdict)
    score_line = next(line for line in report.splitlines() if line.startswith("**Score:"))

    assert f"{cap_point} strengths credited" in score_line, score_line
    assert f"{cap_point + 1} strengths credited" not in score_line, (
        "must not claim more strengths moved the score than the cap actually counted:\n" + score_line
    )
    assert "1 more noted below (not scored)" in score_line, score_line
    assert "## Strengths noted" in report, report


def test_proof_refuted_items_beyond_the_bonus_cap_fold_into_more_ruled_out_not_credited() -> None:
    """Boundary: `_REFUTED_BONUS_SATURATION_POINT` + 1 proof-refuted items (4 today). Same shape as
    the strengths test above, for the sibling clause -- and the overflow must fold into the SAME
    'more ruled out below (not scored)' bucket a challenge-refuted item already uses, not a second,
    indistinguishable copy of that phrase."""
    cap_point = _REFUTED_BONUS_SATURATION_POINT
    refuted = [
        {"title": f"Disproved candidate {i}", "counterexample": "the proof test exited 0"}
        for i in range(cap_point + 1)
    ]

    verdict = AuditVerdict(
        state=PROVEN,
        finding=_finding("Proven crash", harm_class="crash"),
        proof=VerdictProof(attempted=True, test_command="pytest t.py", returncode=1),
        refuted=refuted,
    )
    verdict_at_cap = AuditVerdict(
        state=PROVEN,
        finding=_finding("Proven crash", harm_class="crash"),
        proof=VerdictProof(attempted=True, test_command="pytest t.py", returncode=1),
        refuted=refuted[:cap_point],
    )

    assert compute_report_score(verdict) == compute_report_score(verdict_at_cap)

    report = render_audit_report(verdict)
    score_line = next(line for line in report.splitlines() if line.startswith("**Score:"))

    assert f"{cap_point} disproved candidates credited" in score_line, score_line
    assert f"{cap_point + 1} disproved candidates credited" not in score_line, (
        "must not claim more disproved candidates moved the score than the cap actually counted:\n"
        + score_line
    )
    assert "1 more ruled out below (not scored)" in score_line, score_line
    assert "## Ruled out" in report, report


def test_sabotage_reverting_the_saturation_cap_reproduces_the_overclaim() -> None:
    """Reverts ONLY round 5's cap-aware wording -- back to naming the raw `len(...)` as 'credited'
    for both clauses -- and proves it reproduces the exact overclaim; then proves the real, fixed
    code differs from it on the same input."""
    cap_point_strength = _STRENGTH_BONUS_SATURATION_POINT
    cap_point_refuted = _REFUTED_BONUS_SATURATION_POINT
    strengths = [_finding(f"Strength {i}", harm_class="strength") for i in range(cap_point_strength + 1)]
    refuted = [
        {"title": f"Disproved candidate {i}", "counterexample": "the proof test exited 0"}
        for i in range(cap_point_refuted + 1)
    ]
    verdict = AuditVerdict(
        state=PROVEN,
        finding=_finding("Proven crash", harm_class="crash"),
        proof=VerdictProof(attempted=True, test_command="pytest t.py", returncode=1),
        strengths=strengths,
        refuted=refuted,
    )

    sabotaged_parts = []
    if verdict.strengths:
        n = len(verdict.strengths)
        sabotaged_parts.append(f"{n} strength{'s' if n != 1 else ''} credited")  # pre-fix wording
    n = len(verdict.refuted)
    if n:
        sabotaged_parts.append(f"{n} disproved candidate{'s' if n != 1 else ''} credited")  # pre-fix wording
    assert sabotaged_parts == [
        f"{cap_point_strength + 1} strengths credited",
        f"{cap_point_refuted + 1} disproved candidates credited",
    ], "sabotage setup failed to reproduce the pre-fix overclaim -- got " + repr(sabotaged_parts)

    real_lines = _score_lines(verdict)
    real_text = "\n".join(real_lines)
    assert f"{cap_point_strength + 1} strengths credited" not in real_text, (
        "the real, fixed code must not reproduce the sabotaged strengths overclaim:\n" + real_text
    )
    assert f"{cap_point_refuted + 1} disproved candidates credited" not in real_text, (
        "the real, fixed code must not reproduce the sabotaged refuted overclaim:\n" + real_text
    )
    assert f"{cap_point_strength} strengths credited" in real_text, real_text
    assert f"{cap_point_refuted} disproved candidates credited" in real_text, real_text


# ----------------------------------------------------------------------------------------------
# DEFECT 6 (round 6) -- round 5's own `_bonus_saturation_point` helper reopened the exact
# "credited must mean moved the score" defect it was written to close, for `_REFUTED_BONUS_
# SATURATION_POINT` specifically. `3 * 0.15 == 0.44999999999999996` in IEEE-754 double precision,
# one ULP below the mathematical 0.45 -- so the helper's raw `< cap` loop took one extra step and
# computed 4 instead of the real saturation point, 3. `compute_report_score`'s own bonus arithmetic
# (unchanged, confirmed correct across 6 rounds) saturates at 3 either way: a 4th proof-refuted item
# moves the printed score by exactly zero.
#
# Every assertion below pins the boundary to a LITERAL integer (3, never
# `_REFUTED_BONUS_SATURATION_POINT` or an expression built from it) specifically because round 5's
# own boundary tests (above) derived their expected `cap_point` FROM that constant and were
# therefore structurally unable to notice it had drifted one off from reality. A test that reads
# the constant under test to build its own expectation cannot ever fail when that constant is
# wrong -- it can only ever agree with itself.
# ----------------------------------------------------------------------------------------------


def test_refuted_saturation_boundary_is_literally_three_not_whatever_the_constant_claims() -> None:
    """At exactly 3 proof-refuted items, all 3 are credited. At exactly 4, only 3 may ever be
    named 'credited' -- the 4th is real, still-rendered content (folds into '## Ruled out') that
    moved the score by exactly zero, so it must read as '1 more ruled out below (not scored)', and
    the rendered text must never say '4 disproved candidates credited'. `3` and `4` are written as
    literal integers on purpose: this must keep failing if a future edit reintroduces the round-6
    off-by-one in `_bonus_saturation_point`, even if that same edit also moves
    `_REFUTED_BONUS_SATURATION_POINT` to agree with itself."""

    def _refuted_verdict(n: int) -> AuditVerdict:
        return AuditVerdict(
            state=PROVEN,
            finding=_finding("Proven crash", harm_class="crash"),
            proof=VerdictProof(attempted=True, test_command="pytest t.py", returncode=1),
            refuted=[
                {"title": f"Disproved candidate {i}", "counterexample": "the proof test exited 0"}
                for i in range(n)
            ],
        )

    verdict_at_3 = _refuted_verdict(3)
    verdict_at_4 = _refuted_verdict(4)

    # The real invariant this whole module exists to protect: the 4th proof-refuted item must move
    # the ACTUAL score by exactly zero. If this assertion ever fails, the boundary genuinely moved
    # and the literal `3`/`4` below need updating along with `_REFUTED_BONUS_CAP` -- but as long as
    # this holds, "credited" beyond 3 is a provable overclaim, not a stylistic nitpick.
    score_at_3 = compute_report_score(verdict_at_3)
    score_at_4 = compute_report_score(verdict_at_4)
    assert score_at_3 == score_at_4, (
        "test premise broken: a 4th proof-refuted item was supposed to move the score by zero "
        f"but {score_at_3!r} != {score_at_4!r} -- the real cap moved, update the literals in this "
        "test, don't just trust the constant"
    )

    report_at_3 = render_audit_report(verdict_at_3)
    score_line_at_3 = next(line for line in report_at_3.splitlines() if line.startswith("**Score:"))
    assert "3 disproved candidates credited" in score_line_at_3, score_line_at_3
    assert "4 disproved candidates credited" not in score_line_at_3, score_line_at_3

    report_at_4 = render_audit_report(verdict_at_4)
    score_line_at_4 = next(line for line in report_at_4.splitlines() if line.startswith("**Score:"))
    assert "3 disproved candidates credited" in score_line_at_4, score_line_at_4
    assert "1 more ruled out below (not scored)" in score_line_at_4, score_line_at_4
    # The exact overclaim round 6 found live: naming the raw count of 4 as "credited" when the 4th
    # item moved nothing.
    assert "4 disproved candidates credited" not in score_line_at_4, (
        "overclaim reproduced: the 4th proof-refuted item moved the score by exactly zero (proven "
        "above) but the score line still credits all 4:\n" + score_line_at_4
    )


def test_sabotage_round_6_reverting_the_tolerance_fix_reproduces_the_four_item_overclaim() -> None:
    """Reverts ONLY round 6's tolerance-based comparison in `_bonus_saturation_point` -- back to
    the raw `< cap` float comparison round 5 shipped -- and proves that alone is enough to
    reproduce the exact "4 disproved candidates credited" overclaim on a real render, using the
    SAME production code path (`_score_lines` via `render_audit_report`), not a hand-built string.
    Then proves the real, fixed helper disagrees with the sabotaged one on the identical input."""
    per_item = 0.15
    cap = 0.45

    def _sabotaged_saturation_point(per_item: float, cap: float) -> int:
        # Round 5's exact pre-round-6 arithmetic: a raw `<` comparison with no tolerance.
        point = 0
        while min(point * per_item, cap) < cap:
            point += 1
        return point

    sabotaged_point = _sabotaged_saturation_point(per_item, cap)
    assert sabotaged_point == 4, (
        "sabotage setup failed to reproduce the round-5 float-rounding bug -- got "
        f"{sabotaged_point!r}, expected the literal 4 the bug actually produced"
    )

    verdict = AuditVerdict(
        state=PROVEN,
        finding=_finding("Proven crash", harm_class="crash"),
        proof=VerdictProof(attempted=True, test_command="pytest t.py", returncode=1),
        refuted=[
            {"title": f"Disproved candidate {i}", "counterexample": "the proof test exited 0"}
            for i in range(4)
        ],
    )

    proof_refuted_count = len(verdict.refuted)
    sabotaged_credited_count = min(proof_refuted_count, sabotaged_point)
    sabotaged_text = (
        f"{sabotaged_credited_count} disproved candidate"
        f"{'s' if sabotaged_credited_count != 1 else ''} credited"
    )
    assert sabotaged_text == "4 disproved candidates credited", (
        "sabotage did not reproduce the exact overclaiming text:\n" + sabotaged_text
    )

    real_report = render_audit_report(verdict)
    real_score_line = next(line for line in real_report.splitlines() if line.startswith("**Score:"))
    assert sabotaged_text not in real_score_line, (
        "the real, fixed code must not reproduce the round-5 four-item overclaim:\n" + real_score_line
    )
    assert "3 disproved candidates credited" in real_score_line, real_score_line
    assert _REFUTED_BONUS_SATURATION_POINT == 3, (
        "the real constant must land on the literal 3 confirmed against compute_report_score's "
        f"own output above, got {_REFUTED_BONUS_SATURATION_POINT!r}"
    )
