"""A runtime-computed 0.5-10.0 gut-check on every audit report, matching Hermes's single fast
gut-check number (its "6.5/10") without letting a model self-assert how good its own report is.

Built from `compute_report_score` (audit_verdict.py), which reads only fields the runtime already
computes for the findings table: `compute_severity` (harm_class -> High/Medium/Low),
the confidence tier (Proven/Challenged/Unreviewed), `split_strengths`, and `refuted`. No new
model-facing field, no model call, no branch that can disagree with what the table above it shows.
"""
from __future__ import annotations

from core.agent_runtime.audit_verdict import (
    BLOCKED,
    CANDIDATE_UNPROVEN,
    CONFIDENCE_CHALLENGED,
    CONFIDENCE_PROVEN,
    CONFIDENCE_UNREVIEWED,
    NO_FINDING,
    PROVEN,
    REFUTED,
    AuditVerdict,
    VerdictFinding,
    VerdictProof,
    compute_report_score,
    render_audit_report,
)

_SCORE_FLOOR = 0.5
_SCORE_CEILING = 10.0


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


# --------------------------------------------------------------------------------------
# One score per terminal state (BLOCKED is the one exception -- nothing was checked at all).
# --------------------------------------------------------------------------------------


def test_blocked_has_no_numeric_score_but_says_so_explicitly() -> None:
    """`compute_report_score` still returns `None` for `BLOCKED`, unchanged. What changed (live
    round, 2026-08-05) is that the report no longer goes silent about it: silence reads as an
    oversight, not a decision, so the renderer now says plainly that the score is withheld because
    the audit never reached a verdict -- rather than simply omitting the score section."""
    verdict = AuditVerdict(state=BLOCKED, blocked_reason="the evidence carried no readable source")

    assert compute_report_score(verdict) is None
    report = render_audit_report(verdict)
    assert "**Score:" not in report, "BLOCKED must never render a numeric score line"
    assert "WITHHELD" in report, "BLOCKED must state explicitly that the score is withheld: " + report


def test_proven_scores_a_single_isolated_bug_solidly_below_the_middle() -> None:
    """Worked example 1: one confirmed, executed defect and nothing else examined. Devastating
    would overstate it; near-perfect would misrepresent it. It should land in between."""

    verdict = AuditVerdict(
        state=PROVEN,
        finding=_finding("Malformed record silently truncates the output", harm_class="integrity"),
        proof=VerdictProof(attempted=True, test_command="pytest t.py", returncode=1),
        model_label="m",
    )

    score = compute_report_score(verdict)

    assert score == 6.0, "High severity (4.0) at Proven confidence (x1.0) deducted from the ceiling"
    assert f"Score: {score:.1f}/10" in render_audit_report(verdict)


def test_no_finding_with_only_disproved_candidates_scores_at_the_ceiling() -> None:
    """Worked example 3: nothing proven, two hypotheses tried and disproved BY EXECUTION. Real,
    run evidence the code survived adversarial attempts reads as clean."""

    verdict = AuditVerdict(
        state=NO_FINDING,
        refuted=[
            {"title": "a", "counterexample": "test passed"},
            {"title": "b", "counterexample": "test passed"},
        ],
    )

    assert compute_report_score(verdict) == _SCORE_CEILING


def test_refuted_state_with_no_survivors_also_reaches_the_ceiling() -> None:
    verdict = AuditVerdict(
        state=REFUTED,
        finding=_finding("A disproved candidate"),
        proof=VerdictProof(attempted=True, test_command="pytest t.py", returncode=0),
    )

    # REFUTED means the primary itself was disproved -- it is not counted as a live defect (the
    # state gate on the primary only counts PROVEN/CANDIDATE_UNPROVEN), and there is nothing else.
    assert compute_report_score(verdict) == _SCORE_CEILING


def test_candidate_unproven_primary_deducts_nothing_at_all() -> None:
    """2026-08-05 fix: a live report headlined 'Score: 2.5/10' from four findings, none proven --
    this is the single-primary slice of that shape. `CANDIDATE_UNPROVEN` means the primary survived
    an adversarial source challenge but nothing was ever executed against it, so it is not confirmed
    enough to deduct anything. This test used to assert 6.6 (4.0 High x 0.85 Challenged deducted
    from the ceiling) -- that value was itself computed under the now-acknowledged-wrong behavior
    this fix removes, so the expected value changes to the ceiling, not merely a smaller deduction."""
    verdict = AuditVerdict(
        state=CANDIDATE_UNPROVEN,
        finding=_finding("An unproven candidate", harm_class="integrity"),
    )

    score = compute_report_score(verdict)

    assert score == _SCORE_CEILING


# --------------------------------------------------------------------------------------
# Worked example 2 -- the worst case actually reaches genuinely bad territory.
# --------------------------------------------------------------------------------------


def test_additional_findings_no_longer_stack_onto_a_proven_primarys_score() -> None:
    """2026-08-05 fix: this used to assert 1.6, treating the Challenged/Unreviewed additional
    findings as independently-weighted real defects that stack toward the floor alongside the one
    PROVEN crash. That value was itself computed under the now-acknowledged-wrong behavior this fix
    removes -- none of `additional_findings` is ever challenged or executed (`_survey_findings`'s
    own docstring), so none of it may move the score regardless of the `confidence` field on the
    row. The expected value changes to exactly what the lone proven primary costs by itself: 4.0
    (High) x 1.0 (Proven) = 4.0 deducted from the ceiling -- the same 6.0 a proven-crash-alone
    verdict scores (see `test_proven_scores_a_single_isolated_bug_solidly_below_the_middle` above)."""
    verdict = AuditVerdict(
        state=PROVEN,
        finding=_finding("Proven crash", harm_class="crash"),
        proof=VerdictProof(attempted=True, test_command="pytest t.py", returncode=1),
        additional_findings=[
            _finding("Challenged high", harm_class="integrity", confidence=CONFIDENCE_CHALLENGED),
            _finding("Challenged medium", harm_class="perf", confidence=CONFIDENCE_CHALLENGED),
            _finding("Unreviewed low", harm_class="test-coverage", confidence=CONFIDENCE_UNREVIEWED),
        ],
    )

    score = compute_report_score(verdict)

    assert score == 6.0, "only the one PROVEN primary may deduct anything -- the additional rows must not stack"
    assert score > _SCORE_FLOOR


# --------------------------------------------------------------------------------------
# Adversarial case (CLAUDE.md 0.4): many LOW-confidence findings must not alone hit the floor.
# --------------------------------------------------------------------------------------


def test_many_unreviewed_survey_rows_count_for_exactly_nothing_not_merely_decayed() -> None:
    """2026-08-05 fix: this test's docstring used to credit the per-rank decay with keeping a wide,
    shallow survey batch from reading as certain and terminal -- true under the old formula, where
    `wide_score` landed around 7.1 (20 Low/Unreviewed rows, decayed but still summed). That number
    was itself computed under the now-acknowledged-wrong behavior this fix removes: `additional_findings`
    is never challenged or executed (`_survey_findings`'s own docstring), so 20 rows of it must count
    for exactly zero, every time, landing at the ceiling -- a stronger guarantee than mere decay, not
    a weaker one. The two assertions below both still hold under the new, exact expected value."""

    wide_shallow = AuditVerdict(
        state=NO_FINDING,
        additional_findings=[
            _finding(f"Unreviewed candidate {i}", harm_class="test-coverage")
            for i in range(20)
        ],
    )
    narrow_strong = AuditVerdict(
        state=PROVEN,
        finding=_finding("One proven crash", harm_class="crash"),
        proof=VerdictProof(attempted=True, test_command="pytest t.py", returncode=1),
    )

    wide_score = compute_report_score(wide_shallow)
    narrow_score = compute_report_score(narrow_strong)

    assert wide_score == _SCORE_CEILING, (
        "20 unreviewed rows must count for nothing at all, not merely decay toward the floor"
    )
    assert wide_score > narrow_score, (
        "a wide shallow survey of unreviewed Low findings must not outrank one proven crash"
    )


def test_score_never_leaves_its_documented_range() -> None:
    extreme = AuditVerdict(
        state=PROVEN,
        finding=_finding("Proven crash", harm_class="crash"),
        proof=VerdictProof(attempted=True, test_command="pytest t.py", returncode=1),
        additional_findings=[
            _finding(f"Extra {i}", harm_class="crash", confidence=CONFIDENCE_PROVEN)
            for i in range(50)
        ],
    )

    score = compute_report_score(extreme)

    assert score is not None
    assert _SCORE_FLOOR <= score <= _SCORE_CEILING


# --------------------------------------------------------------------------------------
# Rendering: the score line appears second, right after the header, ahead of every section.
# --------------------------------------------------------------------------------------


def test_the_score_line_is_rendered_second_right_after_the_header() -> None:
    verdict = AuditVerdict(
        state=PROVEN,
        finding=_finding("Proven crash", harm_class="crash"),
        proof=VerdictProof(attempted=True, test_command="pytest t.py", returncode=1),
        target_path="api/x.py",
    )

    lines = [line for line in render_audit_report(verdict).splitlines() if line.strip()]

    assert lines[0].startswith("**Status:")
    assert lines[1].startswith("**Score:")


def test_the_score_line_never_lets_the_model_assert_its_own_number() -> None:
    """Structural guarantee: `compute_report_score` is a pure function of the typed verdict fields
    (severity/confidence/strengths/refuted) -- it never reads `analysis`, so a model asserting its
    own rating in the one field it is allowed to write prose into cannot move the printed score."""

    grounded = AuditVerdict(
        state=PROVEN,
        finding=_finding("Proven crash", harm_class="crash"),
        proof=VerdictProof(attempted=True, test_command="pytest t.py", returncode=1),
    )
    with_self_rating = AuditVerdict(
        state=PROVEN,
        finding=_finding("Proven crash", harm_class="crash"),
        proof=VerdictProof(attempted=True, test_command="pytest t.py", returncode=1),
        analysis="I would rate this file 9.9/10.",
    )

    assert compute_report_score(grounded) == compute_report_score(with_self_rating) == 6.0
    score_line = next(
        line for line in render_audit_report(with_self_rating).splitlines() if line.startswith("**Score:")
    )
    assert score_line.startswith("**Score: 6.0/10**"), (
        "the model's own '9.9/10' in analysis prose must not reach the computed Score line"
    )


# --------------------------------------------------------------------------------------
# Sabotage discipline (CLAUDE.md §6): reverting the decay must fail a test that names the defect.
# --------------------------------------------------------------------------------------


def test_sabotage_rank_decay_is_now_structurally_inert_through_this_path() -> None:
    """2026-08-05 fix: this test used to sabotage `_RANK_DECAY` (set it to 1.0, i.e. no decay by
    rank) and assert the stacked-defect score at the top of this file changed -- it did, because
    `defect_weights` used to hold up to four items (the CANDIDATE_UNPROVEN-or-PROVEN primary plus
    three `additional_findings` rows), so rank mattered.

    After the fix, `defect_weights` can never hold more than ONE item: only a `PROVEN` primary is
    ever appended to it, and `additional_findings` never reaches it at all regardless of confidence.
    With at most one item, `enumerate` always assigns rank 0, and `_RANK_DECAY ** 0 == 1` no matter
    what `_RANK_DECAY` is set to -- the decay is mechanically inert through `compute_report_score`
    now, whatever its value. Sabotaging it therefore must NOT change the score anymore; the mirror
    image of what this test asserted before the fix. The behavior the decay was written for -- a
    wide, stacked pass reading less certain than one strong finding -- is still real, but it no
    longer lives in the score at all (see
    `tests/test_only_proven_findings_move_the_score.py` for the control that actually exercises
    this fix, by sabotaging the PROVEN-only gate itself rather than the now-dead decay constant)."""

    import core.agent_runtime.audit_verdict as av

    verdict = AuditVerdict(
        state=PROVEN,
        finding=_finding("Proven crash", harm_class="crash"),
        proof=VerdictProof(attempted=True, test_command="pytest t.py", returncode=1),
        additional_findings=[
            _finding("Challenged high", harm_class="integrity", confidence=CONFIDENCE_CHALLENGED),
            _finding("Challenged medium", harm_class="perf", confidence=CONFIDENCE_CHALLENGED),
            _finding("Unreviewed low", harm_class="test-coverage", confidence=CONFIDENCE_UNREVIEWED),
        ],
    )

    real_score = compute_report_score(verdict)
    original_decay = av._RANK_DECAY
    try:
        av._RANK_DECAY = 1.0  # would-be sabotage: no decay by rank, every weight counts in full
        sabotaged_score = compute_report_score(verdict)
    finally:
        av._RANK_DECAY = original_decay

    assert real_score == 6.0, "only the proven crash primary (High x Proven = 4.0) counts now"
    assert sabotaged_score == real_score, (
        "defect_weights holds at most one item (the PROVEN primary) after the fix, so its rank is "
        "always 0 and _RANK_DECAY ** 0 == 1 regardless of the constant -- decay is structurally "
        "inert through compute_report_score now"
    )
