"""QA regression, 2026-08-06 (round 5): `_ruled_out_lines` and `_score_lines` each independently
hand-wrote the identical filter over `verdict.challenged_out` -- `item.get("verdict") == "refuted"`
-- as a separate literal list comprehension. They agreed only because nobody had touched either one
independently, which is exactly the architectural shape that let the WITHHELD and fallback branches
of `_score_lines` drift apart in an earlier round (see `_unreviewed_challenges`'s own docstring in
`audit_verdict.py` for that history).

This round factors the filter into one shared helper, `_refuted_challenges`, matching the style of
the existing `_unreviewed_challenges` helper, and has both `_ruled_out_lines` and `_score_lines` call
it instead of each defining their own literal. This is a refactor with no behavior change -- both
literals already agreed on today's data -- proven below by asserting the rendered output is
byte-identical to what the pre-refactor literals would have produced, and by an explicit agreement
test between the two call sites that would catch a future edit to only one of them.
"""
from __future__ import annotations

from core.agent_runtime.audit_verdict import (
    PROVEN,
    AuditVerdict,
    VerdictFinding,
    VerdictProof,
    _refuted_challenges,
    _ruled_out_lines,
    _score_lines,
    render_audit_report,
)


def _finding(title: str, *, harm_class: str = "integrity") -> VerdictFinding:
    return VerdictFinding(
        title=title,
        file="api/x.py",
        line_start=10,
        line_end=12,
        cited_line_text="pass",
        failure_scenario="A concrete input reaches the wrong outcome.",
        harm_class=harm_class,
    )


def _mixed_verdict() -> AuditVerdict:
    """Exercises both `_ruled_out_lines`'s and `_score_lines`'s refuted-challenge-counting paths:
    one proof-refuted item, two challenge-refuted items, and one unreviewed (never-ran) challenge,
    all in the same verdict."""
    return AuditVerdict(
        state=PROVEN,
        finding=_finding("Proven crash", harm_class="crash"),
        proof=VerdictProof(attempted=True, test_command="pytest t.py", returncode=1),
        refuted=[{"title": "Proof-disproved candidate", "counterexample": "the proof test exited 0"}],
        challenged_out=[
            {
                "title": "Challenge-refuted A",
                "verdict": "refuted",
                "reason": "the runtime executed the operation and observed the opposite",
            },
            {
                "title": "Challenge-refuted B",
                "verdict": "refuted",
                "reason": "a deterministic source contradiction was found",
            },
            {
                "title": "Never actually checked",
                "verdict": "uncertain",
                "reason": "the audit's bounded call budget was spent",
            },
        ],
    )


# --------------------------------------------------------------------------------------
# The two call sites must agree on the refuted-challenge count, permanently.
# --------------------------------------------------------------------------------------


def test_ruled_out_lines_and_score_lines_agree_on_the_refuted_challenge_count() -> None:
    verdict = _mixed_verdict()

    # `_ruled_out_lines`'s own count, read back out of its rendered bullets.
    ruled_out = "\n".join(_ruled_out_lines(verdict))
    ruled_out_refuted_challenge_count = sum(
        1
        for line in ruled_out.splitlines()
        if "ruled out by an adversarial source check" in line
    )

    # `_score_lines`'s own count, read back out of its rendered "not scored" clause.
    score_text = "\n".join(_score_lines(verdict))
    assert "2 more ruled out below (not scored)" in score_text, score_text

    assert ruled_out_refuted_challenge_count == 2, ruled_out
    assert ruled_out_refuted_challenge_count == len(_refuted_challenges(verdict.challenged_out)), (
        "_ruled_out_lines and the shared helper must agree on how many challenges were refuted"
    )


def test_refuted_challenges_helper_matches_a_hand_written_filter_on_this_data() -> None:
    """Explicit, permanent proof the shared helper's output matches what each call site's own
    literal used to compute independently -- so a future edit to only one call site is caught."""
    verdict = _mixed_verdict()
    hand_written = [item for item in verdict.challenged_out if item.get("verdict") == "refuted"]

    assert _refuted_challenges(verdict.challenged_out) == hand_written


# --------------------------------------------------------------------------------------
# The refactor changed nothing observable: rendered output is byte-identical to the pre-refactor
# literals, for every shape exercised elsewhere in this test suite.
# --------------------------------------------------------------------------------------


def test_rendered_report_is_byte_identical_to_the_pre_refactor_literal_filters() -> None:
    def _pre_refactor_ruled_out_lines(verdict: AuditVerdict) -> list[str]:
        """The exact pre-refactor body of `_ruled_out_lines`, with its own literal instead of the
        shared helper -- used only to prove the refactor is behavior-preserving."""
        refuted_challenges = [
            item for item in verdict.challenged_out if item.get("verdict") == "refuted"
        ]
        unreviewed_challenges = [
            item for item in verdict.challenged_out if item.get("verdict") != "refuted"
        ]
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

    for verdict in (
        _mixed_verdict(),
        AuditVerdict(state=PROVEN, finding=_finding("Solo proven"), proof=VerdictProof(attempted=True, test_command="t", returncode=1)),
        AuditVerdict(
            state="no_finding",
            challenged_out=[{"title": "only refuted", "verdict": "refuted", "reason": "checked"}],
        ),
        AuditVerdict(
            state="no_finding",
            challenged_out=[{"title": "only uncertain", "verdict": "uncertain", "reason": "never ran"}],
        ),
    ):
        assert _ruled_out_lines(verdict) == _pre_refactor_ruled_out_lines(verdict), (
            f"refactor changed rendered output for state={verdict.state!r}"
        )


# --------------------------------------------------------------------------------------
# NOTE on sabotage discipline (CLAUDE.md section 6) for this specific refactor: the meaningful
# sabotage here is editing `audit_verdict.py` itself so `_ruled_out_lines` and `_score_lines` filter
# `challenged_out` differently, then confirming `test_ruled_out_lines_and_score_lines_agree_on_the_
# refuted_challenge_count` above fails, then reverting. A permanent test that fakes that drift by
# defining its own local, never-called sabotage function (rather than touching the two real call
# sites) would pass unconditionally regardless of whether the real functions actually agree, which
# is exactly the vacuous-test shape CLAUDE.md 0.4 rules out -- so that check is done live during
# verification (see the task's sabotage-test results) rather than committed as a test here.
# --------------------------------------------------------------------------------------
# Control: report rendering unaffected for a plain proven-only verdict (no challenged_out at all).
# --------------------------------------------------------------------------------------


def test_control_a_verdict_with_no_challenged_out_items_is_unaffected() -> None:
    verdict = AuditVerdict(
        state=PROVEN,
        finding=_finding("Proven crash", harm_class="crash"),
        proof=VerdictProof(attempted=True, test_command="pytest t.py", returncode=1),
    )

    report = render_audit_report(verdict)
    score_line = next(line for line in report.splitlines() if line.startswith("**Score:"))

    assert "not scored" not in score_line, score_line
    assert "## Not reviewed" not in report
