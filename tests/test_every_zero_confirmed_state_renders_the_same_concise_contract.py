"""SCALPEL fixture repair, failure class B/C, 2026-08-06 -- the top-level render contract.

Operator instruction: for a request that wants exactly one confirmed finding or a concise refusal,
EVERY zero-confirmed terminal state (CANDIDATE_UNPROVEN, REFUTED, NO_FINDING, and BLOCKED where
applicable) must render the identical concise "NOT PROVEN" contract -- never a headline speculative
candidate, a candidate table, strengths, or a suggested fix. Only PROVEN gets the rich rendering.

This is an intentional product-contract change, not a bug fix for something that was already
broken: CANDIDATE_UNPROVEN and REFUTED used to render verbose content on purpose (see their old
branch comments, still visible in git history). These tests pin the NEW contract directly, against
`render_audit_report`, independent of the many existing tests updated elsewhere to stop asserting
the old verbose text.
"""
from __future__ import annotations

from core.agent_runtime.audit_verdict import (
    CANDIDATE_UNPROVEN,
    PROVEN,
    REFUTED,
    AuditVerdict,
    VerdictFinding,
    VerdictProof,
    render_audit_report,
)


def _finding(title="A live candidate", **overrides):
    defaults = dict(
        title=title,
        file="api/x.py",
        line_start=10,
        line_end=12,
        cited_line_text="if x: pass",
        failure_scenario="A concrete input reaches the wrong outcome.",
        suggested_fix="Add a bounds check before the slice.",
    )
    defaults.update(overrides)
    return VerdictFinding(**defaults)


_FORBIDDEN_MARKERS = (
    "## Other candidates",
    "## Strengths noted",
    "## Fixes",
    "Add a bounds check",  # the suggested fix text itself, not just its heading
)


def test_candidate_unproven_renders_the_concise_contract_not_the_headline_candidate() -> None:
    verdict = AuditVerdict(
        state=CANDIDATE_UNPROVEN,
        finding=_finding("A specific unproven claim"),
        additional_findings=[_finding("Another candidate, never reviewed")],
        strengths=[_finding("Something the file does well", harm_class="strength")],
        target_path="api/x.py",
    )
    report = render_audit_report(verdict)

    assert "NOT PROVEN" in report
    assert "A specific unproven claim" not in report, "the headline speculative candidate leaked into chat"
    assert "Another candidate, never reviewed" not in report
    assert "Something the file does well" not in report
    for marker in _FORBIDDEN_MARKERS:
        assert marker not in report, f"{marker!r} must not appear in a zero-confirmed report:\n{report}"


def test_refuted_renders_the_concise_contract_not_the_disproved_candidates_detail() -> None:
    verdict = AuditVerdict(
        state=REFUTED,
        finding=_finding("A claim that was disproved"),
        proof=VerdictProof(attempted=True, test_command="python3 -m unittest -v x", returncode=0),
        additional_findings=[_finding("A sibling candidate")],
        strengths=[_finding("A strength", harm_class="strength")],
        target_path="api/x.py",
    )
    report = render_audit_report(verdict)

    assert "NOT PROVEN" in report
    assert "A sibling candidate" not in report
    assert "A strength" not in report
    for marker in _FORBIDDEN_MARKERS:
        assert marker not in report, f"{marker!r} must not appear in a zero-confirmed report:\n{report}"


def test_proven_is_the_one_state_that_keeps_the_rich_rendering() -> None:
    """The control: PROVEN must be UNCHANGED by this fix -- a real, confirmed finding still gets
    its full candidate table, strengths, and fix."""
    verdict = AuditVerdict(
        state=PROVEN,
        finding=_finding("A confirmed bug"),
        proof=VerdictProof(attempted=True, test_command="python3 -m unittest -v x", returncode=1),
        additional_findings=[_finding("A sibling candidate")],
        strengths=[_finding("A strength", harm_class="strength")],
        target_path="api/x.py",
    )
    report = render_audit_report(verdict)

    assert "A confirmed bug" in report
    assert "## Fixes" in report
    assert "Add a bounds check" in report


def test_sabotage_restoring_verbose_candidate_unproven_rendering_is_caught() -> None:
    """Reverts ONLY the branch consolidation -- CANDIDATE_UNPROVEN back to its own headline +
    candidate table + strengths + fixes, exactly as it rendered before this repair -- and proves
    the sabotaged version reproduces the leak; then proves the real code differs from it."""

    def _sabotaged_render(verdict: AuditVerdict) -> str:
        from core.agent_runtime.audit_verdict import (
            CONFIDENCE_CHALLENGED,
            _additional_finding_lines,
            _fixes_lines,
            _header_lines,
            _primary_classification_bullets,
            _ruled_out_lines,
            _score_lines,
            _strengths_lines,
        )

        finding = verdict.finding
        lines = [
            f"## Unproven candidate — `{finding.location}`",
            "",
            f"**{finding.title}** — a candidate, not proven.",
            "",
            f"- Suspected failure: {finding.failure_scenario}",
        ]
        lines += _primary_classification_bullets(finding, CONFIDENCE_CHALLENGED)
        lines += _additional_finding_lines(verdict)
        lines += _strengths_lines(verdict)
        lines += _ruled_out_lines(verdict)
        lines += _fixes_lines(verdict)
        return "\n".join(_header_lines(verdict) + _score_lines(verdict) + lines)

    verdict = AuditVerdict(
        state=CANDIDATE_UNPROVEN,
        finding=_finding("A specific unproven claim"),
        additional_findings=[_finding("Another candidate, never reviewed")],
        target_path="api/x.py",
    )

    sabotaged = _sabotaged_render(verdict)
    assert "A specific unproven claim" in sabotaged, "sabotage setup failed to reproduce the leak"
    assert "Another candidate, never reviewed" in sabotaged

    real = render_audit_report(verdict)
    assert "A specific unproven claim" not in real, (
        "the real, fixed renderer must not reproduce the sabotaged verbose leak:\n" + real
    )
    assert real != sabotaged
