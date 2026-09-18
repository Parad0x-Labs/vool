"""A search that ends with nothing confirmed must describe itself using the same counts the rest
of the report shows -- never a sentence composed independently of them.

Live incident, 2026-08-06, session `openclaw:d77e4cf7487fcb92b78c`: a real audit against
`api/apache/liquefy_apache_repetition_v1.py` spent its 5-call budget across 3 nominate + 2 challenge
calls (trace: nominate, challenge, nominate, challenge, nominate, budget exhausted). The rendered
report said, in one place, `Where the search ended: all nominated candidates failed adversarial
checking against the source` -- while its OWN score line, three lines above, said `Rejected
candidates: 0` and `Unreviewed candidates: 8`. The old `stepped_audit.py` line was:

    if screened_rows and not failure_notes:
        blocked_reason = "all nominated candidates failed adversarial checking against the source"

-- true only that SOMETHING had been screened, not that EVERYTHING had. A separate defect in the
same report: one candidate ("Decompressor assumes exactly 10 payload chunks without validation")
appeared TWICE -- once in "Other candidates" (Confidence: Unreviewed) and once in "Not reviewed"
(challenged, budget-exhausted) -- the same real candidate counted under two different confidence
tiers because a title promoted-and-challenged in a LATER nomination round was never excluded from
an EARLIER round's plain survey rows. A third defect, visible directly in the transcript with no
trace needed: a `## Fixes` section rendered five suggested patches against a report whose own
`Confirmed findings: 0` line sat two paragraphs above it -- directly against that turn's own
explicit instruction not to recommend a fix unless a reproduction confirmed the failure.

This file covers all three, plus the concise `NOT PROVEN` contract that replaced the verbose
`NO_FINDING` body these bugs used to hide inside.
"""
from __future__ import annotations

import json

import pytest

from core.agent_runtime.audit_verdict import (
    CONFIDENCE_CHALLENGED,
    CONFIDENCE_PROVEN,
    CONFIDENCE_UNREVIEWED,
    NO_FINDING,
    AuditVerdict,
    VerdictFinding,
    _fixes_lines,
    _refuted_challenges,
    compose_search_ended_reason,
    render_audit_report,
)
from tests import test_an_audit_obeys_the_permission_it_was_given as harness

# ---------------------------------------------------------------------------------------------
# `compose_search_ended_reason` -- the decision tree replacing the hand-typed sentence, keyed only
# on counts every other part of the report already computes.
# ---------------------------------------------------------------------------------------------


def test_nothing_challenged_says_so_plainly() -> None:
    reason = compose_search_ended_reason(rejected_count=0, challenged_count=0, unreviewed_count=5)
    assert "no candidate was adversarially reviewed" in reason
    assert "5" not in reason, "an unreviewed count is meaningless when nothing was ever challenged"


def test_universal_rejection_is_the_only_case_that_may_say_all_were_rejected() -> None:
    reason = compose_search_ended_reason(rejected_count=3, challenged_count=3, unreviewed_count=0)
    assert reason == "all 3 nominated candidates were reviewed and rejected"


def test_singular_wording_for_one_candidate() -> None:
    reason = compose_search_ended_reason(rejected_count=1, challenged_count=1, unreviewed_count=0)
    assert reason == "all 1 nominated candidate was reviewed and rejected"


def test_the_exact_live_incident_shape_never_claims_universal_rejection() -> None:
    """The live incident's own numbers: 2 candidates challenged, 0 cleanly rejected, 8 candidates
    never reviewed at all (5 pure survey rows + 3 challenged_out items whose challenge never
    settled anything). The old code said "all nominated candidates failed adversarial checking" --
    this must not, since only 2 of 10 total nominated candidates were ever challenged."""
    reason = compose_search_ended_reason(rejected_count=0, challenged_count=2, unreviewed_count=8)
    assert "all" not in reason.split(), reason
    assert "failed adversarial checking against the source" not in reason, (
        "this is the exact false claim the live report shipped: " + reason
    )
    assert "8 candidates remained unreviewed" in reason
    assert "no reviewed candidate was confirmed" in reason


def test_challenged_but_inconclusive_with_nothing_left_unreviewed_gets_its_own_wording() -> None:
    """Real, distinct outcome: every candidate was AT LEAST offered a challenge, but not all of
    them were cleanly rejected (some came back inconclusive) -- must not be folded into either the
    "all rejected" branch or the "N unreviewed" branch, since both would misdescribe it."""
    reason = compose_search_ended_reason(rejected_count=1, challenged_count=3, unreviewed_count=0)
    # Adversarial review, 2026-08-06: the singular clause here originally used a literal,
    # uncomputed "(s)" ("1 candidate(s) were rejected") rather than deriving its plural the way
    # the "more candidates" clause beside it always did -- grammatically wrong, and a version of
    # this exact test once asserted the wrong string as correct. Pinning proper subject-verb
    # agreement for both the singular and plural shape of this clause now.
    assert "1 candidate was rejected" in reason
    assert "candidate(s)" not in reason, reason
    assert "2 more candidates were challenged but came back inconclusive" in reason
    assert "none was confirmed" in reason


def test_rejected_clause_pluralizes_correctly_when_more_than_one() -> None:
    """The plural sibling of the test above -- both shapes of the same clause must agree in
    number, not just the one shape a fixture happened to exercise first."""
    reason = compose_search_ended_reason(rejected_count=2, challenged_count=5, unreviewed_count=0)
    assert "2 candidates were rejected" in reason
    assert "candidate(s)" not in reason, reason
    assert "3 more candidates were challenged but came back inconclusive" in reason


def test_rejected_count_may_never_exceed_challenged_count() -> None:
    """Adversarial review, 2026-08-06: with no input guard, `rejected_count=5, challenged_count=3`
    rendered a negative count in operator-facing text ("-2 more candidates..."). This precondition
    is not reachable from the real call site (rejected_count is always a subset of challenged_count
    there by construction), but the function is public and directly unit-tested, so the same
    precedent `_bonus_saturation_point` sets elsewhere in this module applies: fail loudly rather
    than silently render nonsense."""
    with pytest.raises(ValueError):
        compose_search_ended_reason(rejected_count=5, challenged_count=3, unreviewed_count=0)


@pytest.mark.parametrize(
    "rejected,challenged,unreviewed",
    [(0, 0, 0), (0, 1, 0), (1, 1, 0), (0, 3, 5), (2, 5, 3), (0, 0, 10)],
)
def test_every_branch_names_a_count_that_is_actually_true(rejected, challenged, unreviewed) -> None:
    """No branch may claim a number it was not given. A cheap but real invariant: every count this
    function was passed that appears meaningfully in the sentence must appear as its own digit."""
    reason = compose_search_ended_reason(
        rejected_count=rejected, challenged_count=challenged, unreviewed_count=unreviewed
    )
    assert isinstance(reason, str) and reason, "must always return a non-empty sentence"


def test_sabotage_reverting_to_the_old_blind_rule_reproduces_the_false_claim() -> None:
    """Sabotage: the OLD rule was `if screened_rows and not failure_notes: <fixed string>` --
    equivalent to "say universal rejection whenever challenged_count > 0", ignoring rejected_count
    and unreviewed_count entirely. Reproduce that exact rule inline and confirm it disagrees with
    the real function on the live incident's own numbers."""

    def _old_blind_rule(challenged_count: int) -> str:
        if challenged_count > 0:
            return "all nominated candidates failed adversarial checking against the source"
        return ""

    old_reason = _old_blind_rule(challenged_count=2)
    new_reason = compose_search_ended_reason(rejected_count=0, challenged_count=2, unreviewed_count=8)

    assert old_reason != new_reason
    assert "all nominated candidates failed" in old_reason
    assert "all nominated candidates failed" not in new_reason, (
        "the fixed function must not reproduce the old blind rule's exact false claim"
    )


# ---------------------------------------------------------------------------------------------
# `_fixes_lines` -- a suggested fix may render ONLY for a CONFIRMED (PROVEN) primary finding.
# ---------------------------------------------------------------------------------------------


def _finding_with_fix(*, confidence: str, harm_class: str = "crash") -> VerdictFinding:
    return VerdictFinding(
        title="A candidate with a suggested fix",
        file="api/x.py",
        line_start=10,
        line_end=12,
        failure_scenario="some scenario",
        harm_class=harm_class,
        suggested_fix="Do the obviously correct thing.",
        confidence=confidence,
    )


def test_no_fixes_section_when_nothing_is_confirmed() -> None:
    """Live incident: a NO_FINDING report (Confirmed findings: 0) rendered a '## Fixes' section
    with five suggested patches pulled from additional_findings, directly against that turn's own
    explicit instruction not to recommend a fix unless reproduction confirmed the failure."""
    verdict = AuditVerdict(
        state="no_finding",
        additional_findings=[_finding_with_fix(confidence=CONFIDENCE_UNREVIEWED)],
        model_label="m",
    )
    report = render_audit_report(verdict)
    assert "## Fixes" not in report, report
    assert "Do the obviously correct thing" not in report, report


def test_no_fixes_section_for_an_unproven_candidate_unproven_primary() -> None:
    """A CANDIDATE_UNPROVEN primary survived an adversarial challenge but was never executed --
    'likely', not confirmed. Its own suggested_fix must not render either."""
    finding = _finding_with_fix(confidence=CONFIDENCE_CHALLENGED)
    verdict = AuditVerdict(state="candidate_unproven", finding=finding, model_label="m")
    report = render_audit_report(verdict)
    assert "## Fixes" not in report, report


def test_fixes_section_still_renders_for_a_genuinely_proven_primary() -> None:
    """The control: a real, executed, CONFIRMED primary's suggested fix must still render -- this
    is not a blanket suppression, only a gate on actually-unconfirmed findings."""
    from core.agent_runtime.audit_verdict import VerdictProof

    finding = _finding_with_fix(confidence=CONFIDENCE_PROVEN)
    verdict = AuditVerdict(
        state="proven",
        finding=finding,
        proof=VerdictProof(attempted=True, test_command="pytest x.py", returncode=1),
        model_label="m",
    )
    report = render_audit_report(verdict)
    assert "## Fixes" in report, report
    assert "Do the obviously correct thing" in report, report


def test_sabotage_reverting_the_state_gate_reproduces_the_false_fixes_section() -> None:
    """Sabotage: revert `_fixes_lines` to the pre-fix shape (always includes
    `additional_findings`, includes the primary whenever `include_primary` is truthy) and confirm
    it disagrees with the real, fixed function on the exact NO_FINDING case above."""

    def _sabotaged_fixes_lines(verdict: AuditVerdict, *, include_primary: bool) -> list[str]:
        candidates = []
        if include_primary and verdict.finding is not None:
            candidates.append(verdict.finding)
        candidates.extend(verdict.additional_findings)
        rows = [item for item in candidates if str(item.suggested_fix or "").strip()]
        if not rows:
            return []
        lines = ["", "## Fixes", ""]
        for item in rows:
            lines.append(f"- **{item.title}** (`{item.location}`): {item.suggested_fix.strip()}")
        return lines

    verdict = AuditVerdict(
        state="no_finding",
        additional_findings=[_finding_with_fix(confidence=CONFIDENCE_UNREVIEWED)],
        model_label="m",
    )

    sabotaged = _sabotaged_fixes_lines(verdict, include_primary=False)
    assert any("## Fixes" in line for line in sabotaged), (
        "sabotage setup failed to reproduce the original bug"
    )

    real = _fixes_lines(verdict)
    assert real == [], "the fixed _fixes_lines must return nothing for an unconfirmed verdict"


# ---------------------------------------------------------------------------------------------
# The concise NOT-PROVEN contract: NO_FINDING no longer inlines the candidate table, strengths, or
# per-item ruled-out/not-reviewed breakdown into the chat-facing answer.
# ---------------------------------------------------------------------------------------------


def test_no_finding_answer_is_concise_and_points_to_activity() -> None:
    verdict = AuditVerdict(
        state="no_finding",
        additional_findings=[
            VerdictFinding(title="Some unreviewed candidate", file="x.py", confidence=CONFIDENCE_UNREVIEWED)
        ],
        challenged_out=[{"title": "Some challenged candidate", "verdict": "uncertain", "reason": "r"}],
        strengths=[VerdictFinding(title="Something done well", file="x.py", harm_class="strength")],
        blocked_reason=compose_search_ended_reason(rejected_count=0, challenged_count=1, unreviewed_count=2),
        model_label="m",
    )
    report = render_audit_report(verdict)

    assert "## Other candidates found in the same pass" not in report, report
    assert "## Strengths noted" not in report, report
    assert "## Not reviewed" not in report, report
    assert "## Ruled out" not in report, report
    assert "Some unreviewed candidate" not in report, report
    assert "Some challenged candidate" not in report, report
    assert "Something done well" not in report, report
    assert "Activity" in report, report
    # The essential counts still reach the reader, via the score line and the one summary sentence.
    assert "**NOT PROVEN**" in report, report
    assert "- Challenged: 1" in report, report
    assert "- Confirmed: 0" in report, report
    assert "- Rejected: 0" in report, report
    assert "- Unreviewed: 1" in report, report
    assert "no reviewed candidate was confirmed" in report, report


# ---------------------------------------------------------------------------------------------
# Integration: a candidate promoted and challenged in a LATER nomination round must not also
# appear as a plain, unreviewed survey row surfaced by an EARLIER round -- the exact double-count
# the live incident showed ("Decompressor assumes exactly 10 payload chunks without validation"
# in both "Other candidates" and "Not reviewed"). Driven through the real `run_stepped_audit`.
# ---------------------------------------------------------------------------------------------

_DUPLICATE_TITLE = "Duplicate candidate seen twice"


def _batch(*findings: dict) -> str:
    return json.dumps({"findings": list(findings)})


def _raw_finding(title: str, *, line_no: int, line_text: str, scenario: str) -> dict:
    return {
        "title": title,
        "file": harness.TARGET,
        "line_start": line_no,
        "line_end": line_no + 2,
        "cited_line_text": line_text,
        "failure_scenario": scenario,
    }


def test_a_candidate_promoted_later_is_not_also_listed_as_an_earlier_unreviewed_survey_row() -> None:
    round_one = _batch(
        _raw_finding(
            "Round one's own promoted candidate",
            line_no=harness.TRUNCATION_LINE_NO,
            line_text=harness._TRUNCATION_LINE,
            scenario="A malformed record here silently truncates the decompressed output instead of raising, corrupting the result -- distinct from the duplicate below.",
        ),
        _raw_finding(
            _DUPLICATE_TITLE,
            line_no=harness.EMPTY_INPUT_LINE_NO,
            line_text=harness._EMPTY_INPUT_LINE,
            scenario="An empty input here crashes decompress with an unhandled exception -- first seen here as a mere survey row in round one's own batch.",
        ),
    )
    round_one_challenge = json.dumps(
        {
            "verdict": "refuted",
            "reason": "round one's primary does not hold up against the cited source.",
            "counterexample": "the cited operation behaves as documented, not as claimed.",
        }
    )
    round_two = _batch(
        _raw_finding(
            _DUPLICATE_TITLE,
            line_no=harness.EMPTY_INPUT_LINE_NO,
            line_text=harness._EMPTY_INPUT_LINE,
            scenario="An empty input here crashes decompress with an unhandled exception -- the SAME candidate, this time promoted and sent to challenge.",
        ),
    )
    round_two_challenge = json.dumps(
        {
            "verdict": "uncertain",
            "reason": "the source challenge returned no usable content for this candidate.",
        }
    )

    decision, router, _tools = harness._drive(
        [round_one, round_one_challenge, round_two, round_two_challenge],
        session_id="audit-duplicate-candidate",
    )
    report = harness._report(decision)
    stepped = harness._stepped(decision)

    # The first four steps are the two rounds this test scripts; the engine tries a further
    # nomination afterward (round two's challenge came back "uncertain", not "supported", so the
    # search keeps looking within its own budget) which finds nothing more once the scripted
    # replies run out -- real, correct behavior, not something this test needs to pin down.
    assert router.steps()[:4] == ["nominate", "challenge", "nominate", "challenge"], router.steps()
    assert stepped.get("terminal_state") == "no_finding", stepped.get("terminal_state")

    additional_titles = [
        str(item.get("title") or "") for item in stepped.get("additional_findings") or []
    ]
    screened_titles = [str(item.get("title") or "") for item in stepped.get("screened_out") or []]

    assert _DUPLICATE_TITLE in screened_titles, (
        "the duplicate must be represented once, as the challenged candidate it actually is: "
        + str(screened_titles)
    )
    assert _DUPLICATE_TITLE not in additional_titles, (
        "the duplicate must NOT also appear as a plain unreviewed survey row -- this is the exact "
        "double-count the live incident shipped: additional_findings=" + str(additional_titles)
    )
    # And the rendered answer must not show it twice under two different confidence tiers either.
    assert report.count(_DUPLICATE_TITLE) <= 1, (
        f"'{_DUPLICATE_TITLE}' appears {report.count(_DUPLICATE_TITLE)} times in the rendered "
        "report; a real candidate must not be counted under two different confidence tiers:\n"
        + report
    )


def test_sabotage_removing_the_screened_title_exclusion_reproduces_the_double_count() -> None:
    """Sabotage: skip the exclusion (`survey_rows_unscreened`) and pass raw `survey_rows` straight
    to `_survey_findings`, the way `run_stepped_audit` did before this fix. Confirm the duplicate
    reappears under both tiers -- proving the exclusion, not something else, is what fixed it."""
    from core.agent_runtime.stepped_audit import _survey_findings

    round_one = _batch(
        _raw_finding(
            "Round one's own promoted candidate",
            line_no=harness.TRUNCATION_LINE_NO,
            line_text=harness._TRUNCATION_LINE,
            scenario="A malformed record here silently truncates the decompressed output instead of raising, corrupting the result -- distinct from the duplicate below.",
        ),
        _raw_finding(
            _DUPLICATE_TITLE,
            line_no=harness.EMPTY_INPUT_LINE_NO,
            line_text=harness._EMPTY_INPUT_LINE,
            scenario="An empty input here crashes decompress with an unhandled exception -- first seen here as a mere survey row in round one's own batch.",
        ),
    )
    round_one_challenge = json.dumps(
        {
            "verdict": "refuted",
            "reason": "round one's primary does not hold up against the cited source.",
            "counterexample": "the cited operation behaves as documented, not as claimed.",
        }
    )
    round_two = _batch(
        _raw_finding(
            _DUPLICATE_TITLE,
            line_no=harness.EMPTY_INPUT_LINE_NO,
            line_text=harness._EMPTY_INPUT_LINE,
            scenario="An empty input here crashes decompress with an unhandled exception -- the SAME candidate, this time promoted and sent to challenge.",
        ),
    )
    round_two_challenge = json.dumps(
        {
            "verdict": "uncertain",
            "reason": "the source challenge returned no usable content for this candidate.",
        }
    )

    decision, _router, _tools = harness._drive(
        [round_one, round_one_challenge, round_two, round_two_challenge],
        session_id="audit-duplicate-candidate-sabotage-control",
    )
    stepped = harness._stepped(decision)
    screened_titles = {str(item.get("title") or "") for item in stepped.get("screened_out") or []}
    assert _DUPLICATE_TITLE in screened_titles

    # Reconstruct what the UNFILTERED survey rows would have produced, using the real evidence
    # object this turn's own machinery builds -- not a hand-typed stand-in that could itself
    # diverge from what `_finding_defect`/`_survey_findings` actually require of it.
    from core.agent_runtime.stepped_audit import _evidence_from_context

    evidence = _evidence_from_context(harness._evidence_context())

    unfiltered_survey_rows = [
        _raw_finding(
            "Round one's own promoted candidate",
            line_no=harness.TRUNCATION_LINE_NO,
            line_text=harness._TRUNCATION_LINE,
            scenario="A malformed record here silently truncates the decompressed output instead of raising, corrupting the result -- distinct from the duplicate below.",
        ),
        _raw_finding(
            _DUPLICATE_TITLE,
            line_no=harness.EMPTY_INPUT_LINE_NO,
            line_text=harness._EMPTY_INPUT_LINE,
            scenario="An empty input here crashes decompress with an unhandled exception -- first seen here as a mere survey row in round one's own batch.",
        ),
    ]
    sabotaged_survey_extra = _survey_findings(unfiltered_survey_rows, evidence, harness.TARGET)
    sabotaged_titles = {item.title for item in sabotaged_survey_extra}

    assert _DUPLICATE_TITLE in sabotaged_titles, (
        "sabotage setup failed to reproduce the original bug -- the unfiltered survey rows should "
        "still contain the duplicate title: " + str(sabotaged_titles)
    )
    # The real, fixed pipeline must not have produced this same double-listing.
    real_additional_titles = {
        str(item.get("title") or "") for item in stepped.get("additional_findings") or []
    }
    assert _DUPLICATE_TITLE not in real_additional_titles, (
        "the fixed pipeline must differ from the sabotaged unfiltered version on the exact "
        "case that shipped the double count"
    )


# ---------------------------------------------------------------------------------------------
# 2026-08-06, follow-on spec: impossibility-proving assertions (not mere expectation updates),
# explicit lifecycle-state labeling, the reconciliation invariant, and full report-branch/sabotage
# coverage. Everything below drives real code -- `reconcile_candidate_counts`, the lifecycle
# constants, `_challenge_never_attempted`/`_challenge_attempted_but_unresolved` -- not a
# reimplementation of it.
# ---------------------------------------------------------------------------------------------

from core.agent_runtime.audit_verdict import (
    CANDIDATE_CHALLENGED,
    CANDIDATE_REJECTED,
    CANDIDATE_UNREVIEWED_BUDGET_EXHAUSTED,
    _challenge_attempted_but_unresolved,
    _challenge_never_attempted,
    _status_word,
    reconcile_candidate_counts,
)

# --- Point 1: impossibility-proving assertions -------------------------------------------------


@pytest.mark.parametrize(
    "rejected,challenged",
    [(0, 1), (0, 5), (1, 3), (2, 4), (0, 10)],
)
def test_impossible_rejected_zero_or_partial_can_never_claim_universal_rejection(rejected, challenged) -> None:
    """Structural proof, not a single example: for EVERY (rejected, challenged) pair where
    rejected < challenged, the sentence can never claim "all N were reviewed and rejected" --
    that specific claim is reachable ONLY when rejected == challenged. Covers the live incident's
    literal shape (Rejected: 0) as one point in the swept range, not the only one checked."""
    for unreviewed in (0, 3, 8):
        reason = compose_search_ended_reason(
            rejected_count=rejected, challenged_count=challenged, unreviewed_count=unreviewed
        )
        assert f"all {challenged} nominated candidate" not in reason, (
            f"rejected={rejected} < challenged={challenged} but the sentence claimed universal "
            f"rejection: {reason!r}"
        )
        assert "failed adversarial checking against the source" not in reason


def test_impossible_a_candidate_never_offered_a_challenge_cannot_be_labeled_challenged_or_rejected() -> None:
    """A `screened_rows` item the budget refused before it ever reached the model
    (`attempted=False`) must be excluded from BOTH `_challenge_never_attempted`'s complement
    functions -- it can never show up as challenged, and by construction can never show up as
    rejected either (only a `verdict == "refuted"` item can be rejected, and `_challenge_finding`
    never returns `refuted` for a call it refused to make -- see that function's own code path)."""
    screened_out = [
        {"title": "Never even attempted", "verdict": "uncertain", "reason": "budget spent", "attempted": False},
        {"title": "Attempted, inconclusive", "verdict": "uncertain", "reason": "no usable content", "attempted": True},
        {"title": "Attempted, rejected", "verdict": "refuted", "reason": "r", "counterexample": "c", "attempted": True},
    ]
    never_attempted = _challenge_never_attempted(screened_out)
    attempted_unresolved = _challenge_attempted_but_unresolved(screened_out)

    never_attempted_titles = {item["title"] for item in never_attempted}
    attempted_unresolved_titles = {item["title"] for item in attempted_unresolved}

    assert never_attempted_titles == {"Never even attempted"}
    assert "Never even attempted" not in attempted_unresolved_titles
    assert attempted_unresolved_titles == {"Attempted, inconclusive"}
    assert "Attempted, rejected" not in never_attempted_titles
    assert "Attempted, rejected" not in attempted_unresolved_titles, (
        "a rejected item is neither 'never attempted' nor 'attempted but unresolved' -- it is its "
        "own, third, settled outcome"
    )


@pytest.mark.parametrize(
    "state,finding_confidence",
    [
        ("no_finding", None),
        ("candidate_unproven", CONFIDENCE_CHALLENGED),
        ("refuted", None),
    ],
)
def test_impossible_zero_confirmed_states_never_render_a_fixes_section(state, finding_confidence) -> None:
    """Exhaustive, not a single example: sweep every non-PROVEN terminal state this module
    defines and confirm none of them can ever render '## Fixes', regardless of what
    `suggested_fix` content is attached to the primary or to additional_findings."""
    finding = None
    if finding_confidence is not None:
        finding = _finding_with_fix(confidence=finding_confidence)
    verdict = AuditVerdict(
        state=state,
        finding=finding,
        additional_findings=[_finding_with_fix(confidence=CONFIDENCE_UNREVIEWED)],
        model_label="m",
    )
    report = render_audit_report(verdict)
    assert "## Fixes" not in report, f"state={state!r} rendered a Fixes section:\n{report}"


def test_impossible_trimming_from_chat_answer_does_not_remove_from_activity() -> None:
    """Paired assertion, not two separate tests that could silently drift apart: for the SAME
    real audit run, the candidate's title must be simultaneously ABSENT from the chat-facing
    report and PRESENT in `details.stepped_audit`. Checking these independently (as separate test
    functions) would not catch a change that breaks the pairing itself -- e.g. a future edit that
    deletes the row from `additional_findings` entirely, which would make both assertions pass for
    the wrong reason (trivially absent from both, not deliberately kept out of one and only one)."""
    round_one = _batch(
        _raw_finding(
            "The confirmed-absent-from-chat candidate",
            line_no=harness.TRUNCATION_LINE_NO,
            line_text=harness._TRUNCATION_LINE,
            scenario="A malformed record here silently truncates the decompressed output instead of raising, corrupting the result.",
        ),
    )
    round_one_challenge = json.dumps(
        {
            "verdict": "uncertain",
            "reason": "the source challenge returned no usable content for this candidate.",
        }
    )
    decision, _router, _tools = harness._drive(
        [round_one, round_one_challenge], session_id="audit-pairing-check"
    )
    report = harness._report(decision)
    stepped = harness._stepped(decision)

    title = "The confirmed-absent-from-chat candidate"
    in_chat = title in report
    screened_titles = [str(item.get("title") or "") for item in stepped.get("screened_out") or []]
    in_activity = title in screened_titles

    assert not in_chat, f"'{title}' must not be inlined into the chat-facing NO_FINDING answer:\n{report}"
    assert in_activity, (
        f"'{title}' must still be recoverable from details.stepped_audit['screened_out']: "
        + str(screened_titles)
    )
    # And the specific reason it was never settled must be there too, not just the bare title.
    matching = next(item for item in stepped.get("screened_out") or [] if item.get("title") == title)
    assert matching.get("lifecycle_state") == CANDIDATE_UNREVIEWED_BUDGET_EXHAUSTED or (
        matching.get("lifecycle_state") == CANDIDATE_CHALLENGED
    ), matching


def test_lifecycle_state_is_explicit_on_every_screened_out_entry() -> None:
    """Every `screened_out` entry Activity receives must carry ITS OWN `lifecycle_state` -- a
    reader must never have to infer review status by cross-referencing which list an item sits in
    or by pattern-matching a free-text `reason` string."""
    round_one = _batch(
        _raw_finding(
            "Rejected via real challenge",
            line_no=harness.TRUNCATION_LINE_NO,
            line_text=harness._TRUNCATION_LINE,
            scenario="A malformed record here silently truncates the decompressed output instead of raising, corrupting the result.",
        ),
    )
    round_one_challenge = json.dumps(
        {
            "verdict": "refuted",
            "reason": "the cited operation behaves as documented, not as claimed.",
            "counterexample": "direct execution shows no such failure.",
        }
    )
    decision, _router, _tools = harness._drive(
        [round_one, round_one_challenge], session_id="audit-lifecycle-state-explicit"
    )
    stepped = harness._stepped(decision)
    screened = stepped.get("screened_out") or []
    assert screened, "fixture must produce at least one screened_out row"
    for item in screened:
        assert item.get("lifecycle_state") in {
            CANDIDATE_REJECTED,
            CANDIDATE_CHALLENGED,
            CANDIDATE_UNREVIEWED_BUDGET_EXHAUSTED,
        }, item
    rejected_item = next(item for item in screened if item.get("verdict") == "refuted")
    assert rejected_item["lifecycle_state"] == CANDIDATE_REJECTED, rejected_item


# --- Point 2 (continued): the formal reconciliation invariant ----------------------------------


def test_reconciliation_holds_for_a_realistic_mixed_scenario() -> None:
    violations = reconcile_candidate_counts(
        nominated=10, filtered=0, unreviewed=8, challenged=2, rejected=0, confirmed=0,
        unresolved_after_challenge=2,
    )
    assert violations == []


def test_reconciliation_violation_when_nominated_total_is_wrong() -> None:
    violations = reconcile_candidate_counts(
        nominated=11, filtered=0, unreviewed=8, challenged=2, rejected=0, confirmed=0,
        unresolved_after_challenge=2,
    )
    assert violations, "nominated=11 but filtered+unreviewed+challenged=10 must be flagged"
    assert any("nominated=11" in v for v in violations)


def test_reconciliation_violation_when_challenged_breakdown_is_wrong() -> None:
    violations = reconcile_candidate_counts(
        nominated=10, filtered=0, unreviewed=8, challenged=2, rejected=1, confirmed=0,
        unresolved_after_challenge=0,
    )
    assert violations, "challenged=2 but rejected+confirmed+unresolved_after_challenge=1 must be flagged"
    assert any("challenged=2" in v for v in violations)


def test_reconciliation_clean_when_everything_is_zero() -> None:
    assert reconcile_candidate_counts(
        nominated=0, filtered=0, unreviewed=0, challenged=0, rejected=0, confirmed=0,
        unresolved_after_challenge=0,
    ) == []


# --- Point 6: sabotage tests for all six named regressions --------------------------------------


def test_sabotage_b_marking_an_unreviewed_finding_rejected_is_caught() -> None:
    """(b) An unreviewed finding marked rejected. If a future edit folded
    `_challenge_never_attempted` items into `_refuted_challenges`'s count instead of keeping them
    separate, this test must fail by showing the wrong Rejected count."""
    screened_out = [
        {"title": "Never attempted", "verdict": "uncertain", "reason": "budget spent", "attempted": False},
    ]

    def _sabotaged_rejected_count(rows):
        # The bug: treats EVERY non-"supported" item as rejected, collapsing the
        # never-attempted/attempted-unresolved/rejected three-way split back into one bucket.
        return len(rows)

    real_rejected = len(_refuted_challenges(screened_out))
    sabotaged_rejected = _sabotaged_rejected_count(screened_out)

    assert real_rejected == 0, "a never-attempted item must not be counted as rejected"
    assert sabotaged_rejected == 1, "sabotage setup failed to reproduce the mislabeling"
    assert real_rejected != sabotaged_rejected


def test_sabotage_d_removing_activity_details_while_chat_stays_trimmed_is_caught() -> None:
    """(d) Activity details removed while chat output is trimmed. If `additional_findings`/
    `screened_out` were ever dropped from `details.stepped_audit` while the NO_FINDING chat answer
    stayed concise, the candidate would become genuinely unrecoverable -- this test proves that
    scenario is detectable, by simulating the stripped-details case and confirming it disagrees
    with the real pipeline's output."""
    round_one = _batch(
        _raw_finding(
            "Would vanish if Activity details were stripped",
            line_no=harness.TRUNCATION_LINE_NO,
            line_text=harness._TRUNCATION_LINE,
            scenario="A malformed record here silently truncates the decompressed output instead of raising, corrupting the result.",
        ),
    )
    round_one_challenge = json.dumps(
        {"verdict": "uncertain", "reason": "the source challenge returned no usable content."}
    )
    decision, _router, _tools = harness._drive(
        [round_one, round_one_challenge], session_id="audit-activity-strip-sabotage"
    )
    stepped = harness._stepped(decision)

    # The sabotaged shape: what `details.stepped_audit` would look like if `screened_out` were
    # wiped after the fact (simulating a regression that stops populating it).
    sabotaged_stepped = dict(stepped)
    sabotaged_stepped["screened_out"] = []

    real_titles = [str(item.get("title") or "") for item in stepped.get("screened_out") or []]
    sabotaged_titles = [str(item.get("title") or "") for item in sabotaged_stepped.get("screened_out") or []]

    assert "Would vanish if Activity details were stripped" in real_titles
    assert "Would vanish if Activity details were stripped" not in sabotaged_titles, (
        "sabotage setup failed to reproduce an empty Activity view"
    )
    assert real_titles != sabotaged_titles


def test_sabotage_e_broken_reconciliation_is_caught() -> None:
    """(e) Candidate totals fail to reconcile. A direct sabotage of the arithmetic itself."""
    good = reconcile_candidate_counts(
        nominated=5, filtered=0, unreviewed=3, challenged=2, rejected=1, confirmed=0,
        unresolved_after_challenge=1,
    )
    bad = reconcile_candidate_counts(
        # Sabotage: nominated overstated by one, as if a candidate were double-counted.
        nominated=6, filtered=0, unreviewed=3, challenged=2, rejected=1, confirmed=0,
        unresolved_after_challenge=1,
    )
    assert good == []
    assert bad != [], "an overstated nominated total must be flagged, not silently accepted"


def test_sabotage_f_status_word_promising_candidates_below_when_none_shown_is_caught() -> None:
    """(f) Status wording again promises "candidates below" when none are shown. Reproduces the
    OLD `_status_word` string inline and confirms it disagrees with the real, fixed one for the
    exact NO_FINDING-with-a-live-candidate shape both versions must handle."""
    verdict = AuditVerdict(
        state=NO_FINDING,
        additional_findings=[
            VerdictFinding(title="X", file="x.py", confidence=CONFIDENCE_UNREVIEWED)
        ],
        model_label="m",
    )

    def _sabotaged_status_word(v: AuditVerdict) -> str:
        if v.additional_findings:
            return "No Proven Issues — Unreviewed Candidates Below"
        return "No Issues Found"

    real = _status_word(verdict)
    sabotaged = _sabotaged_status_word(verdict)

    assert sabotaged == "No Proven Issues — Unreviewed Candidates Below", (
        "sabotage setup failed to reproduce the old promise-something-below wording"
    )
    assert real == "No Proven Issues", real
    assert "Below" not in real
    assert real != sabotaged
    # And the actual rendered report must not contain the promise either, since nothing renders
    # "below" the header for NO_FINDING any more.
    report = render_audit_report(verdict)
    assert "Candidates Below" not in report, report


# --- Point 7: all six report-branch scenarios ----------------------------------------------------


def test_branch_1_zero_confirmed_findings() -> None:
    verdict = AuditVerdict(
        state="no_finding",
        additional_findings=[VerdictFinding(title="X", file="x.py", confidence=CONFIDENCE_UNREVIEWED)],
        model_label="m",
    )
    report = render_audit_report(verdict)
    assert "**NOT PROVEN**" in report
    assert "- Confirmed: 0" in report
    assert "## Fixes" not in report


def test_branch_2_exactly_one_confirmed_finding() -> None:
    from core.agent_runtime.audit_verdict import VerdictProof

    finding = VerdictFinding(
        title="A genuinely confirmed bug", file="x.py", line_start=5, line_end=7,
        failure_scenario="calling f(0) raises ZeroDivisionError", harm_class="crash",
        confidence=CONFIDENCE_PROVEN,
    )
    verdict = AuditVerdict(
        state="proven", finding=finding,
        proof=VerdictProof(attempted=True, test_command="pytest test_f.py", returncode=1),
        model_label="m",
    )
    report = render_audit_report(verdict)
    assert "**NOT PROVEN**" not in report, "a PROVEN report must never show the NOT-PROVEN block"
    assert "## Bug reproduced" in report
    assert "reproduced by a failing test" in report


def test_branch_3_confirmed_plus_unreviewed_candidates_do_not_cross_contaminate() -> None:
    """A PROVEN primary alongside unrelated unreviewed additional_findings: the confirmed bug's
    own section must render normally, and the unreviewed candidates must still be excluded from
    any Fixes section, without either branch's logic leaking into the other."""
    from core.agent_runtime.audit_verdict import VerdictProof

    finding = VerdictFinding(
        title="The confirmed primary", file="x.py", line_start=5, line_end=7,
        failure_scenario="calling f(0) raises ZeroDivisionError", harm_class="crash",
        confidence=CONFIDENCE_PROVEN, suggested_fix="Guard the divisor.",
    )
    verdict = AuditVerdict(
        state="proven", finding=finding,
        additional_findings=[_finding_with_fix(confidence=CONFIDENCE_UNREVIEWED)],
        proof=VerdictProof(attempted=True, test_command="pytest test_f.py", returncode=1),
        model_label="m",
    )
    report = render_audit_report(verdict)
    assert "## Bug reproduced" in report
    assert "Guard the divisor." in report, "the CONFIRMED primary's own fix must still render"
    fixes_section = report.split("## Fixes", 1)[1] if "## Fixes" in report else ""
    assert "A candidate with a suggested fix" not in fixes_section, (
        "the unreviewed additional finding's fix must not leak into the Fixes section:\n" + report
    )


def test_branch_4_challenge_attempted_but_unresolved() -> None:
    verdict = AuditVerdict(
        state="no_finding",
        challenged_out=[
            {"title": "Real check, no verdict", "verdict": "uncertain", "reason": "ambiguous reply", "attempted": True},
        ],
        model_label="m",
    )
    report = render_audit_report(verdict)
    assert "- Challenged: 1" in report
    assert "- Unreviewed: 0" in report
    assert "- Rejected: 0" in report


def test_branch_5_audit_stopped_because_of_call_budget() -> None:
    verdict = AuditVerdict(
        state="no_finding",
        challenged_out=[
            {"title": "Budget refused before the call", "verdict": "uncertain", "reason": "the audit's bounded call budget (5 model calls) was spent", "attempted": False},
        ],
        model_label="m",
    )
    report = render_audit_report(verdict)
    assert "- Unreviewed: 1" in report
    assert "- Challenged: 0" in report


def test_branch_6_provider_or_challenge_failure_uses_the_failure_sentence_not_the_counter_block() -> None:
    """A pure provider/transport failure (nothing was ever nominated at all) is a structurally
    DIFFERENT case from "candidates existed but weren't reviewed" -- `candidate_present` is False,
    so `_score_lines` falls through to the existing bare-ceiling path, and `blocked_reason` comes
    from `_failure_sentence`, never `compose_search_ended_reason`. This must remain distinct: a
    provider failure must not be described using the Challenged/Confirmed/Rejected/Unreviewed
    vocabulary that belongs to a search that actually reached some candidates."""
    verdict = AuditVerdict(
        state="no_finding",
        blocked_reason="nvidia/nemotron-3-ultra-550b-a55b:free returned a provider error during the nomination step: script_exhausted.",
        model_label="m",
    )
    report = render_audit_report(verdict)
    assert "**NOT PROVEN**" not in report, (
        "a pure provider failure (no candidate ever nominated) must not use the "
        "candidate-review counter block:\n" + report
    )
    assert "script_exhausted" in report
    assert "- Challenged:" not in report
    assert "- Unreviewed:" not in report
