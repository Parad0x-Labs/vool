"""A request for a survey gets a survey; a request for one bug still costs one bug.

The operator asked "audit the code please, tell me any pros and cons good and bad" and got a single
unproven candidate, while a competitor returned two dozen findings across correctness, performance,
memory and API design. The audit was not weaker at reasoning — it stopped at the FIRST supported
candidate and discarded the rest of the pass, because `AuditVerdict.finding` is singular and the
candidate loop breaks as soon as one survives.

The first fix made EVERY audit collect three, which
`tests/test_an_audit_stays_inside_its_model_and_call_budget.py` correctly rejected: it pins
"primary analysis -> optional independent challenge" and a plain bug hunt turned into
`nominate, challenge, nominate, nominate`. The second fix gated that on a REGEX over the operator's
wording, which refused their very next request because they phrased it differently - a bot, not an
agent. The answer was neither: batch the nomination so several findings cost one call, and there is
nothing left to ration.

What is deliberately NOT done: the extra candidates are never executed against, so none of them can
reach `proven`. Only the primary finding gets a proof run, and `asserts_reproduction` still has
exactly one true state.
"""
from __future__ import annotations

from core.agent_runtime.audit_verdict import (
    CANDIDATE_UNPROVEN,
    NO_FINDING,
    PROVEN,
    AuditVerdict,
    VerdictFinding,
    VerdictProof,
    render_audit_report,
)

# --------------------------------------------------------------------------------------
# The quota follows the request, so an ordinary bug hunt costs what it always did.
# --------------------------------------------------------------------------------------


def test_no_phrasing_decides_whether_the_operator_gets_a_survey() -> None:
    """The gate that used to live here is gone, and must not come back.

    It was a regex matching "pros and cons" / "good and bad" / "review this". It scored the
    operator's own next request - "audit this file for me, tell me if all is sound or we have room
    to improvements or maybe some logic fails" - as a single-bug hunt, because that is not how the
    regex was worded. Normal wording for any assistant, refused by a pattern.

    CLAUDE.md prohibits exactly this: hard-coded phrase matching standing in for a model decision.
    Reporting several findings costs nothing extra now that the nomination is batched, so there was
    never anything to ration.
    """

    import core.agent_runtime.stepped_audit as sa

    assert not hasattr(sa, "_SURVEY_REQUEST_RE"), (
        "a phrase matcher decides what the operator is allowed to ask for; batching removed the "
        "cost that was its only justification"
    )
    assert not hasattr(sa, "_reported_finding_quota")


# --------------------------------------------------------------------------------------
# The verdict carries them, and cannot borrow affirmative language for them.
# --------------------------------------------------------------------------------------


def _extra(title: str, line: int = 95) -> VerdictFinding:
    return VerdictFinding(
        title=title, file="api/x.py", line_start=line, line_end=line,
        failure_scenario="A concrete input reaches the wrong outcome.",
    )


def _primary() -> VerdictFinding:
    return VerdictFinding(
        title="Decompress truncates on a corrupt id", file="api/x.py",
        line_start=120, line_end=130, cited_line_text="if pid > len(pat_list): break",
        failure_scenario="A corrupt pattern id silently truncates the output.",
    )


def test_extra_candidates_stay_out_of_the_chat_report_for_an_unproven_primary() -> None:
    """SCALPEL fix, failure class B, 2026-08-06: `CANDIDATE_UNPROVEN` now renders the same concise
    contract as `NO_FINDING` — zero confirmed findings must never carry a candidate table into
    chat, however many extras a batch turned up. The extras are not lost; they stay in the typed
    `AuditVerdict.additional_findings` the caller already had (Activity reads it from there), they
    simply must not appear in the rendered TEXT itself."""
    verdict = AuditVerdict(
        state=CANDIDATE_UNPROVEN,
        finding=_primary(),
        additional_findings=[_extra("IPv6 falls through to raw storage"),
                             _extra("Chunk count hardcoded to 10", 128)],
        target_path="api/x.py",
    )
    report = render_audit_report(verdict)

    assert "IPv6 falls through to raw storage" not in report
    assert "Chunk count hardcoded to 10" not in report
    assert "NOT PROVEN" in report
    # The data itself is untouched — only the chat rendering is trimmed.
    assert [item.title for item in verdict.additional_findings] == [
        "IPv6 falls through to raw storage", "Chunk count hardcoded to 10",
    ]


def test_the_extra_candidates_are_never_called_proven() -> None:
    """None of them was executed against, so the section must not read as confirmed.

    This is the honesty contract the whole module exists for: affirmative bug language lives in one
    branch. A list of extras rendered with the same weight as a reproduced bug would smuggle four
    unproven claims in behind one proven one.
    """

    report = render_audit_report(
        AuditVerdict(
            state=PROVEN,
            finding=_primary(),
            proof=VerdictProof(attempted=True, test_command="pytest t.py", returncode=1),
            additional_findings=[_extra("IPv6 falls through to raw storage")],
            target_path="api/x.py",
        )
    )

    section = report.split("## Other candidates")[1]
    assert "none is proven" in section.lower()
    assert "reproduced" not in section.lower()


def test_a_duplicate_of_the_primary_finding_is_dropped() -> None:
    """Two entries for one search result would read as two independent problems."""

    verdict = AuditVerdict(
        state=CANDIDATE_UNPROVEN,
        finding=_primary(),
        additional_findings=[_extra(_primary().title), _extra("A genuinely different one")],
    )

    assert [item.title for item in verdict.additional_findings] == ["A genuinely different one"]


def test_no_section_appears_when_there_is_only_one_finding() -> None:
    """The control. A heading with nothing under it is worse than no heading."""

    report = render_audit_report(
        AuditVerdict(state=CANDIDATE_UNPROVEN, finding=_primary(), target_path="api/x.py")
    )

    assert "Other candidates" not in report


def test_a_no_finding_verdict_stays_empty() -> None:
    report = render_audit_report(AuditVerdict(state=NO_FINDING, target_path="api/x.py"))

    assert "Other candidates" not in report


# --------------------------------------------------------------------------------------
# The batched nomination. One call, N findings — which is what makes a survey affordable.
# --------------------------------------------------------------------------------------


def test_one_nomination_call_can_return_several_findings() -> None:
    """The change that actually moves the row count.

    Every earlier fix raised a quota that something downstream overrode. The binding cap was that
    ONE nomination call yields ONE finding, so N findings cost N calls and the read-only ledger
    (`{"nominate": 3, "challenge": 2}`) capped the report at two rows however high the quota went.
    A batched reply makes a survey cost the same five calls as the single-finding audit.

    Driven through the real audit rather than the parser: removing the batch handling left the
    parser-level tests green.
    """

    import json

    from tests import test_an_audit_obeys_the_permission_it_was_given as harness

    batch = json.dumps(
        {
            "findings": [
                json.loads(harness.TRUNCATION_FINDING),
                {
                    "title": "Regex is recompiled on every compress call",
                    "file": harness.TARGET,
                    "line_start": harness.TRUNCATION_LINE_NO,
                    "line_end": harness.TRUNCATION_LINE_NO + 2,
                    "cited_line_text": harness._TRUNCATION_LINE,
                    "failure_scenario": "The pattern is built inside the hot loop, so every call "
                    "pays the compile cost again.",
                    "harm_class": "perf",
                },
            ]
        }
    )

    decision, router, _tools = harness._drive([batch, harness.TRUNCATION_SUPPORTED], session_id="audit-batch")
    stepped = harness._stepped(decision)

    # 2026-08-06: `CANDIDATE_UNPROVEN` no longer inlines the second item into the chat report (see
    # that branch's own comment) — checked against the structured detail instead, the same pattern
    # `test_observations_survive_a_refuted_primary_finding` below already uses for `NO_FINDING`.
    assert stepped.get("finding", {}).get("title", "").lower().count("truncat"), (
        "the provable finding must still lead the structured record"
    )
    additional_titles = {
        str(item.get("title") or "").lower() for item in stepped.get("additional_findings") or []
    }
    assert any("recompiled" in title for title in additional_titles), (
        "the second item of the SAME nomination call never reached the structured record, so a "
        f"batched reply is still costing one finding per call: {additional_titles}"
    )
    assert router.steps() == ["nominate", "challenge"], (
        "two findings must cost ONE nomination, not two"
    )


def test_observations_survive_a_refuted_primary_finding() -> None:
    """The gap the operator's own run exposed.

    Measured live 2026-08-03: the sole provable candidate was refuted by the adversarial check and
    the turn reported "No verified bug found" with ZERO observations — despite the same nomination
    call having returned a batch. `survey_rows` was only captured when the primary SURVIVED, so a
    refuted candidate took every observation down with it.

    Observations are never challenged and never executed; they cannot be refuted by a check aimed
    at a different claim. Discarding them throws away work the model already did and the operator
    already paid for.
    """

    import json

    from tests import test_an_audit_obeys_the_permission_it_was_given as harness

    batch = json.dumps(
        {
            "findings": [
                json.loads(harness.EMPTY_INPUT_FINDING),
                {
                    "title": "The record pattern is recompiled on every call",
                    "file": harness.TARGET,
                    "line_start": harness.TRUNCATION_LINE_NO,
                    "line_end": harness.TRUNCATION_LINE_NO + 2,
                    "cited_line_text": harness._TRUNCATION_LINE,
                    # A CURRENT property of the code, not a hypothetical change. The first draft of
                    # this fixture said "a format change silently corrupts decode" and was rejected
                    # by the future-change guard - correctly, and the guard is not the thing under
                    # test here.
                    "failure_scenario": "The pattern is built inside the decode loop, so every "
                    "call pays the compile cost again on the current code.",
                    "harm_class": "perf",
                },
            ]
        }
    )

    decision, _router, _tools = harness._drive(
        [batch, harness.EMPTY_INPUT_REJECTED], session_id="audit-refuted-primary"
    )
    # 2026-08-06: this is an `additional_findings` survey row on a `NO_FINDING` outcome --
    # `render_audit_report`'s `NO_FINDING` branch no longer inlines that table into the chat-facing
    # text (see that branch's own comment), so "did the observation survive" is now checked against
    # the structured detail the runtime exposes for Activity, not the rendered report string.
    additional_titles = {
        str(item.get("title") or "").lower()
        for item in harness._stepped(decision).get("additional_findings") or []
    }

    assert any("recompiled on every call" in title for title in additional_titles), (
        "the observation was discarded because the PROVABLE candidate beside it was refuted: "
        + " | ".join(additional_titles)
    )
