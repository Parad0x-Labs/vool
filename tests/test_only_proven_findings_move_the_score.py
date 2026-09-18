"""Live incident, 2026-08-05: a report headlined `Score: 2.5/10` computed from FOUR findings, none
of which were proven -- one CANDIDATE_UNPROVEN primary (survived an adversarial source challenge,
never executed) and three `additional_findings` rows (always `CONFIDENCE_UNREVIEWED` by
construction -- `_survey_findings` never challenges or executes them, per its own docstring). At
least one of the four turned out to be factually wrong on independent verification. The score read
as a confident, alarming verdict on code that was never actually confirmed broken.

The fix in `compute_report_score` (audit_verdict.py) is a hard rule, not a reduced weight: only a
finding whose state is literally `PROVEN` may deduct anything. This file reproduces the exact
live-incident shape and proves the rule, plus the two controls that show the fix does not touch
what a genuinely proven primary is allowed to cost.
"""
from __future__ import annotations

import core.agent_runtime.audit_verdict as av
from core.agent_runtime.audit_verdict import (
    CANDIDATE_UNPROVEN,
    CONFIDENCE_PROVEN,
    CONFIDENCE_UNREVIEWED,
    NO_FINDING,
    PROVEN,
    AuditVerdict,
    VerdictFinding,
    VerdictProof,
    compute_report_score,
    render_audit_report,
)

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


def _three_unreviewed_additional_findings() -> list[VerdictFinding]:
    return [
        _finding("Survey row A", harm_class="crash"),
        _finding("Survey row B", harm_class="integrity"),
        _finding("Survey row C", harm_class="crash"),
    ]


# --------------------------------------------------------------------------------------
# The exact live-incident shape: 4 findings, none proven.
# --------------------------------------------------------------------------------------


def test_candidate_unproven_primary_plus_unreviewed_survey_rows_scores_at_the_ceiling() -> None:
    """Reproduces the live incident exactly: a High-severity CANDIDATE_UNPROVEN primary plus 3
    additional High-severity Unreviewed findings -- four findings total, none proven. None of them
    may deduct anything, so the score must land at the ceiling (modulo any strength/refuted bonus,
    of which there are none here), NOT a low, alarming number like 2.5."""

    verdict = AuditVerdict(
        state=CANDIDATE_UNPROVEN,
        finding=_finding("Unproven primary", harm_class="integrity"),
        additional_findings=_three_unreviewed_additional_findings(),
    )

    score = compute_report_score(verdict)

    assert score == _SCORE_CEILING, (
        f"a report built from four unproven findings must not deduct anything -- got {score}, "
        "which is exactly the false-confidence shape ('Score: 2.5/10' from nothing proven) this "
        "fix exists to prevent"
    )


# --------------------------------------------------------------------------------------
# Control: a genuinely PROVEN primary still costs exactly what it should, and the SAME unreviewed
# additional findings still contribute nothing alongside it.
# --------------------------------------------------------------------------------------


def test_proven_primary_deducts_alone_unreviewed_siblings_still_contribute_nothing() -> None:
    verdict_with_siblings = AuditVerdict(
        state=PROVEN,
        finding=_finding("Proven primary", harm_class="integrity"),
        proof=VerdictProof(attempted=True, test_command="pytest t.py", returncode=1),
        additional_findings=_three_unreviewed_additional_findings(),
    )
    verdict_alone = AuditVerdict(
        state=PROVEN,
        finding=_finding("Proven primary", harm_class="integrity"),
        proof=VerdictProof(attempted=True, test_command="pytest t.py", returncode=1),
    )

    score_with_siblings = compute_report_score(verdict_with_siblings)
    score_alone = compute_report_score(verdict_alone)

    # Computed independently of the implementation, from the documented weight tables, so this
    # assertion cannot pass by accident if the formula silently changes shape.
    expected = round(
        av._SCORE_CEILING
        - av._SEVERITY_POINTS["High"] * av._CONFIDENCE_WEIGHT[CONFIDENCE_PROVEN],
        1,
    )

    assert score_with_siblings == expected, "only the one proven finding may drive the deduction"
    assert score_with_siblings < _SCORE_CEILING, "a real, executed defect must still cost something"
    assert score_with_siblings == score_alone, (
        "the three unreviewed additional findings must contribute exactly nothing -- the score "
        "with them present must equal the score with them absent"
    )


# --------------------------------------------------------------------------------------
# Control: nothing confirmed at all (no primary, only unreviewed survey rows) -> ceiling.
# --------------------------------------------------------------------------------------


def test_no_finding_with_only_unreviewed_additional_findings_scores_at_the_ceiling() -> None:
    verdict = AuditVerdict(
        state=NO_FINDING,
        additional_findings=_three_unreviewed_additional_findings(),
    )

    score = compute_report_score(verdict)

    assert score == _SCORE_CEILING, (
        "nothing confirmed, nothing counted against it -- the operator's explicit standard"
    )


# --------------------------------------------------------------------------------------
# Rendering: with nothing proven and a real candidate present, the score line is now WITHHELD
# entirely rather than printing any number, "weighed" or otherwise.
#
# Follow-on round, 2026-08-05: live-testing this same fix found a second, subtler shape of the
# false-confidence gap -- a report with the exact findings below still headlined a bare
# `**Score: 10.0/10**` (no "weighed" claim, so the assertions this test used to make all passed)
# sitting directly above a table of real, unverified High-severity candidates. Zero proven findings
# earning a confident-looking ceiling is the same false-reassurance shape as a low score earning
# false alarm, just with an asterisk instead of none. `_score_lines` (audit_verdict.py) now
# withholds the number outright whenever `confirmed_defect_count` is 0 and a real candidate is
# present (a CANDIDATE_UNPROVEN primary, an additional finding, or an unreviewed challenged_out
# item), and states the four counts a single digit would otherwise compress into. This test is
# updated to assert THAT rendering rather than the old (technically-not-wrong-but-still-misleading)
# ceiling; nothing about `compute_report_score`'s numeric behavior changed.
# --------------------------------------------------------------------------------------


def test_score_line_is_withheld_not_a_bare_ceiling_for_the_live_incident_shape() -> None:
    verdict = AuditVerdict(
        state=CANDIDATE_UNPROVEN,
        finding=_finding("Unproven primary", harm_class="integrity"),
        additional_findings=_three_unreviewed_additional_findings(),
    )

    report = render_audit_report(verdict)

    # SCALPEL fix, 2026-08-06: CANDIDATE_UNPROVEN now withholds the number under the same
    # concise "NOT PROVEN" contract NO_FINDING already used, not its own WITHHELD framing —
    # the score-withholding behavior itself, which this test exists to pin, is unchanged.
    assert "NOT PROVEN" in report, (
        "nothing is proven and a real candidate is present -- the headline number must be "
        "withheld, not rendered as a bare 10.0 sitting above unverified High-severity candidates: "
        + report
    )
    assert f"**Score: {_SCORE_CEILING:.1f}/10**" not in report, (
        "a numeric score line must not render when nothing is proven and a candidate is present: "
        + report
    )
    assert "- Confirmed: 0" in report
    assert "- Challenged: 0" in report
    assert "- Unreviewed: 3" in report
    assert "- Rejected: 0" in report


# --------------------------------------------------------------------------------------
# Sabotage discipline (CLAUDE.md section 6): reverting the fix must fail these tests by naming the
# actual wrong (low) score value the live incident shipped.
# --------------------------------------------------------------------------------------


def test_sabotage_restoring_the_old_scoring_reproduces_the_live_incidents_wrong_score() -> None:
    """Restores the exact pre-fix formula inline (the `CANDIDATE_UNPROVEN` branch and the
    `additional_findings` loop) and confirms it reproduces a low score for the live-incident shape
    -- proving the real, fixed `compute_report_score` differs from it precisely because of the fix,
    not by coincidence."""

    def _sabotaged_compute_report_score(verdict: AuditVerdict) -> float:
        defect_weights: list[float] = []
        if verdict.finding is not None and verdict.state in (PROVEN, CANDIDATE_UNPROVEN):
            primary_confidence = (
                av.CONFIDENCE_PROVEN if verdict.state == PROVEN else av.CONFIDENCE_CHALLENGED
            )
            defect_weights.append(
                av._SEVERITY_POINTS[verdict.finding.severity]
                * av._CONFIDENCE_WEIGHT[primary_confidence]
            )
        for item in verdict.additional_findings:
            confidence = item.confidence or av.CONFIDENCE_UNREVIEWED
            defect_weights.append(
                av._SEVERITY_POINTS[item.severity]
                * av._CONFIDENCE_WEIGHT.get(confidence, av._CONFIDENCE_WEIGHT[av.CONFIDENCE_UNREVIEWED])
            )
        defect_weights.sort(reverse=True)
        deduction = sum(w * (av._RANK_DECAY**rank) for rank, w in enumerate(defect_weights))
        bonus = min(len(verdict.strengths) * av._STRENGTH_BONUS_PER_ITEM, av._STRENGTH_BONUS_CAP)
        bonus += min(len(verdict.refuted) * av._REFUTED_BONUS_PER_ITEM, av._REFUTED_BONUS_CAP)
        return round(
            max(av._SCORE_FLOOR, min(av._SCORE_CEILING, av._SCORE_CEILING - deduction + bonus)), 1
        )

    verdict = AuditVerdict(
        state=CANDIDATE_UNPROVEN,
        finding=_finding("Unproven primary", harm_class="integrity"),
        additional_findings=_three_unreviewed_additional_findings(),
    )

    real_score = compute_report_score(verdict)
    sabotaged_score = _sabotaged_compute_report_score(verdict)

    assert real_score == _SCORE_CEILING
    # 4.0 (High, Challenged 0.85) + 4.0*0.85 (rank 1) + 4.0*0.85^2 (rank 2) + 4.0*0.85^3 (rank 3),
    # each at the appropriate confidence weight -- the exact low, alarming number the live incident
    # shipped. Asserted structurally (must be far below the ceiling) rather than pinned to a float
    # so the control does not silently rot if the weight table is retuned.
    assert sabotaged_score < 5.0, (
        f"sabotage setup failed to reproduce a low score -- got {sabotaged_score}, expected "
        "something in the live incident's damning range"
    )
    assert sabotaged_score != real_score, (
        "the fixed compute_report_score must differ from the pre-fix formula on the exact shape "
        "that produced the live incident's wrong 'Score: 2.5/10'"
    )
