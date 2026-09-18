"""A live audit reported a candidate as "ruled out by an adversarial source check" when the check
never ran -- the audit's total call budget was already spent, so the challenge step was refused
before it ever executed. `stepped_audit._challenge_finding` returns `verdict="uncertain"` for that
case (and for two other cases where a challenge call attempted to run but produced nothing usable:
an empty reply, or a reply that failed to parse as JSON). `run_stepped_audit` pushes ALL of these,
plus genuine `verdict="refuted"` falsifications, into the same `challenged_out` list with no other
marker of which is which -- only `item["verdict"]` distinguishes them.

`audit_verdict._ruled_out_lines` used to render every `challenged_out` item with one template:
"ruled out by an adversarial source check", regardless of `verdict`. That is a false claim for the
`"uncertain"` items: no check occurred. This file proves the underlying renderer still tells those
two cases apart, and reproduces the exact live-observed line to prove that specific false claim is
gone.

2026-08-06: `render_audit_report`'s `NO_FINDING` branch no longer inlines `_ruled_out_lines` at all
(see that branch's own comment in `audit_verdict.py` -- a second live incident, session
`openclaw:d77e4cf7487fcb92b78c`, found the per-candidate detail sitting directly beneath a summary
sentence that contradicted it; the detail moved to Activity, the chat-facing answer got shorter).
`_ruled_out_lines` itself is unchanged and still the one function responsible for not conflating a
genuine refutation with an unreviewed candidate -- the tests below now call it directly rather than
through a `NO_FINDING` render, since that specific inlining is the thing that changed, not the
underlying correctness this file exists to protect. A separate test below locks in the NEW
behavior: `NO_FINDING` must NOT inline this detail any more.
"""
from __future__ import annotations

from core.agent_runtime.audit_verdict import AuditVerdict, _ruled_out_lines, render_audit_report

_RULED_OUT_PHRASE = "ruled out by an adversarial source check"


def _verdict_with(challenged_out: list[dict]) -> AuditVerdict:
    return AuditVerdict(state="no_finding", challenged_out=challenged_out, model_label="m")


def _line_for(lines: list[str], title: str) -> str:
    matches = [line for line in lines if title in line]
    assert matches, f"no rendered line mentions {title!r} in:\n" + "\n".join(lines)
    assert len(matches) == 1, f"expected exactly one line for {title!r}, got: {matches}"
    return matches[0]


def test_refuted_and_uncertain_challenge_items_render_differently() -> None:
    verdict = _verdict_with(
        [
            {
                "title": "Genuinely disproved claim",
                "verdict": "refuted",
                "reason": "the runtime executed the operation and observed the opposite",
            },
            {
                "title": "Never actually checked claim",
                "verdict": "uncertain",
                "reason": "the source challenge returned no usable content",
            },
        ]
    )

    lines = _ruled_out_lines(verdict)

    refuted_line = _line_for(lines, "Genuinely disproved claim")
    uncertain_line = _line_for(lines, "Never actually checked claim")

    assert _RULED_OUT_PHRASE in refuted_line, (
        "a genuinely executed refutation must keep the original, correct wording"
    )
    assert _RULED_OUT_PHRASE not in uncertain_line, (
        "a challenge that never ran must not be described as an adversarial source check ruling "
        "it out -- that claims a check happened when it didn't"
    )
    # The candidate must still be visible in the underlying detail -- dropping it silently is its
    # own honesty gap in the other direction (see the fix's own reasoning). This detail no longer
    # reaches the NO_FINDING chat answer directly (see the test below), but it is real content the
    # runtime computed and Activity still shows it.
    assert any("Never actually checked claim" in line for line in lines)


def test_the_exact_live_observed_budget_exhaustion_case_does_not_read_as_a_real_check() -> None:
    """Reproduces the exact defect: `audit_call_budget.refusal_for` returns this literal string
    when the audit's total call budget is spent, and the challenge step never got to run at all."""
    verdict = _verdict_with(
        [
            {
                "title": "Decompression accepts truncated custom format without validation",
                "verdict": "uncertain",
                "reason": "the audit's bounded call budget (5 model calls) was spent",
            }
        ]
    )

    lines = _ruled_out_lines(verdict)
    line = _line_for(
        lines, "Decompression accepts truncated custom format without validation"
    )

    assert _RULED_OUT_PHRASE not in line, (
        "a challenge refused because the call budget was already spent must not be rendered as "
        "though an adversarial source check took place: " + line
    )
    assert "adversarial source check" not in line
    # The reason -- the actual, true explanation -- must still reach the operator.
    assert "the audit's bounded call budget (5 model calls) was spent" in line


def test_no_finding_no_longer_inlines_the_ruled_out_detail_in_the_chat_answer() -> None:
    """Live incident, 2026-08-06, session `openclaw:d77e4cf7487fcb92b78c`: this per-candidate
    detail used to sit directly under a `blocked_reason` summary sentence that contradicted it
    (see `compose_search_ended_reason`). It is no longer inlined into the `NO_FINDING` chat answer
    at all -- this is the control proving that removal is real, not merely untested."""
    verdict = _verdict_with(
        [
            {
                "title": "Never actually checked claim",
                "verdict": "uncertain",
                "reason": "the audit's bounded call budget (5 model calls) was spent",
            }
        ]
    )

    report = render_audit_report(verdict)

    assert "Never actually checked claim" not in report, (
        "per-candidate ruled-out/not-reviewed detail must no longer be inlined into the NO_FINDING "
        "chat answer -- it belongs in Activity:\n" + report
    )
    assert "Activity" in report, "the answer must still point the reader at where the detail lives"


# --------------------------------------------------------------------------------------
# Sabotage discipline (CLAUDE.md section 6): reverting the split must fail a test that names the
# defect, and it must name the SAME false claim the live report shipped.
# --------------------------------------------------------------------------------------


def test_sabotage_collapsing_both_verdicts_to_one_template_is_caught() -> None:
    """If `_ruled_out_lines` is reverted to the single old template for every `challenged_out`
    item (regardless of `verdict`), this test must fail by naming the exact wrong text: an
    "uncertain" (never-ran) item rendered with the "ruled out by an adversarial source check"
    phrase that belongs only to a genuine, executed refutation."""

    def _sabotaged_ruled_out_lines(verdict: AuditVerdict) -> list[str]:
        if not verdict.refuted and not verdict.challenged_out:
            return []
        lines = ["", "## Ruled out", ""]
        for item in verdict.refuted:
            title = str(item.get("title") or "a candidate")
            counter = str(item.get("counterexample") or "the proof test exited 0")
            lines.append(f"- **{title}** — disproved. {counter}")
        for item in verdict.challenged_out:
            title = str(item.get("title") or "a candidate")
            reason = str(item.get("reason") or "the adversarial source check could not sustain it")
            lines.append(f"- **{title}** — ruled out by an adversarial source check. {reason}")
        return lines

    verdict = _verdict_with(
        [
            {
                "title": "Never actually checked claim",
                "verdict": "uncertain",
                "reason": "the audit's bounded call budget (5 model calls) was spent",
            }
        ]
    )

    sabotaged_lines = _sabotaged_ruled_out_lines(verdict)
    sabotaged_line = next(
        line for line in sabotaged_lines if "Never actually checked claim" in line
    )

    # This is what the live report actually shipped: a false "checked" claim on an unreviewed item.
    assert _RULED_OUT_PHRASE in sabotaged_line, (
        "sabotage setup failed to reproduce the original bug -- the old single-template code "
        "should render this phrase for an uncertain item"
    )

    # The real, fixed renderer must NOT do this.
    real_lines = _ruled_out_lines(verdict)
    real_line = _line_for(real_lines, "Never actually checked claim")
    assert _RULED_OUT_PHRASE not in real_line, (
        "the fixed renderer must differ from the sabotaged single-template version on the exact "
        "case that shipped the false claim"
    )


# --------------------------------------------------------------------------------------
# QA follow-up, 2026-08-05: `_ruled_out_lines` stopped the body from claiming a check happened
# when it didn't -- but `_status_word` and `_score_lines` build the HEADLINE and the SCORE LINE
# from `verdict.additional_findings` alone, never from `challenged_out`. A `NO_FINDING` report
# whose only candidate is an unreviewed `challenged_out` item (empty `additional_findings`) still
# said "No Issues Found" / "10.0/10 ... nothing counted against or for it" -- exactly the
# headline-contradicts-body failure this file's own fix already closed for the body text, now
# reproduced one level up for a reader who stops at the headline.
# --------------------------------------------------------------------------------------

_NO_ISSUES_HEADLINE = "**Status: No Issues Found**"
_NOTHING_COUNTED_PHRASE = "nothing counted against or for it"


def test_unreviewed_challenge_alone_does_not_headline_as_clean() -> None:
    """The real incident's shape: NO additional_findings, ONE challenged_out item that was never
    actually checked (verdict='uncertain'). The headline must not say 'No Issues Found'.

    Follow-on round, 2026-08-05: this is also exactly the shape a later live-testing pass found
    still headlining a bare, confident `**Score: 10.0/10**` above the very candidate this test's
    own docstring says was "found; it just was not checked" -- the same false-reassurance gap as a
    false alarm, just with a ceiling instead of a low number. `_score_lines` (audit_verdict.py) now
    withholds the number outright whenever nothing is proven and a live, unreviewed candidate is
    present -- an unreviewed `challenged_out` item counts as exactly that.

    2026-08-06: `NO_FINDING` now renders the plain NOT-PROVEN counter block instead of the WITHHELD
    Confirmed-findings/Likely-findings framing (see `_score_lines`'s own comment); updated to match.
    `attempted=False` is now set explicitly -- the reason text ("bounded call budget... was spent")
    is exactly the shape `FindingChallenge.attempted=False` exists to make structured rather than
    inferred from that free-text string.
    """
    verdict = AuditVerdict(
        state="no_finding",
        additional_findings=[],
        challenged_out=[
            {
                "title": "Decompression accepts truncated custom format without validation",
                "verdict": "uncertain",
                "reason": "the audit's bounded call budget (5 model calls) was spent",
                "attempted": False,
            }
        ],
        model_label="m",
    )

    report = render_audit_report(verdict)

    assert _NO_ISSUES_HEADLINE not in report, (
        "an unreviewed candidate was found but the headline still claimed a clean pass:\n" + report
    )
    assert not any(line.startswith("**Score:") for line in report.splitlines()), (
        "an unreviewed candidate exists -- the score must be withheld, not rendered as a number "
        "of any kind:\n" + report
    )
    assert "**NOT PROVEN**" in report, report
    assert "- Unreviewed: 1" in report, report
    assert "- Challenged: 0" in report, report


def test_control_genuinely_empty_pass_still_headlines_clean() -> None:
    """No regression: an actually empty pass (no additional_findings, no challenged_out at all)
    must still say 'No Issues Found'."""
    verdict = AuditVerdict(
        state="no_finding",
        additional_findings=[],
        challenged_out=[],
        model_label="m",
    )

    report = render_audit_report(verdict)

    assert _NO_ISSUES_HEADLINE in report, report
    score_line = next(
        (line for line in report.splitlines() if line.startswith("**Score:")), None
    )
    if score_line is not None:
        assert _NOTHING_COUNTED_PHRASE in score_line, score_line


def test_control_a_genuinely_refuted_challenge_alone_still_headlines_clean() -> None:
    """A challenge that actually RAN and found a contradiction (`verdict='refuted'`) is a real,
    executed disproof -- not an unreviewed candidate. This must not trip the 'unreviewed
    candidates below' headline; it belongs under '## Ruled out', and the pass is still clean."""
    verdict = AuditVerdict(
        state="no_finding",
        additional_findings=[],
        challenged_out=[
            {
                "title": "Genuinely disproved claim",
                "verdict": "refuted",
                "reason": "the runtime executed the operation and observed the opposite",
            }
        ],
        model_label="m",
    )

    report = render_audit_report(verdict)

    assert _NO_ISSUES_HEADLINE in report, (
        "a genuinely refuted (executed) challenge must not be treated like an unreviewed one:\n"
        + report
    )
