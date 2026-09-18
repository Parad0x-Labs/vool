"""H1/H2, confirmed live 2026-08-04 (see the ledger entry for the same date): a Hermes-vs-VOOL
comparison on an identical ~200-line file surfaced ~20 findings from Hermes and only 7 from VOOL.

Two real, distinct defects were found and are pinned here:

H1 -- a nomination call that hits `_NOMINATE_MAX_TOKENS` truncates the OUTER
`{"findings": [...]}` envelope. `_first_json_object`'s brace-depth scan cannot close that outer
object, so it walks forward and returns only the FIRST complete item nested inside the array as a
bare dict, silently discarding every finding after it -- even though the model already wrote them.
Measured directly against live Ollama at three ceilings (3000 / 5048 / 9000 tokens): every single
run hit `done_reason: length` with an unterminated JSON string, and every run contained 13-26
complete finding objects before the cutoff. `_recover_findings_array` (stepped_audit.py) fixes this
by walking the array itself and keeping every item whose braces balance before the text runs out.

H2 -- `run_stepped_audit`'s candidate loop kept only the FIRST non-empty nomination batch
(`if batch_rows and not survey_rows: survey_rows = list(batch_rows)`). Driven live against a free
cloud model, three sequential nomination attempts on one file returned real, largely
non-overlapping batches (18 / 19 / 15 rows) and only the first one ever reached the report. The fix
unions every attempt's batch, deduped by `claim_signature`/`matches_any_claim` -- the same mechanism
this loop already uses to keep a later nomination from repeating a screened or refuted claim.

Both fixes are exercised two ways: a direct unit test of the recovery parser against adversarial
malformed/truncated JSON (CLAUDE.md 0.4 -- the worst case, not the happy path), and an end-to-end
drive of the real `run_stepped_audit` with a scripted provider, so the assertion is that MORE real
findings reach the rendered report, not that a helper function returns the right list in isolation.
"""
from __future__ import annotations

import json

from core.agent_runtime.stepped_audit import _recover_findings_array
from tests import test_an_audit_obeys_the_permission_it_was_given as harness

# --------------------------------------------------------------------------------------
# H1 -- the recovery parser itself, against adversarial/malformed input.
# --------------------------------------------------------------------------------------


def _item(title: str, line: int = 10) -> dict:
    return {
        "title": title,
        "file": "x.py",
        "line_start": line,
        "line_end": line,
        "cited_line_text": "pass",
        "failure_scenario": "A concrete input reaches the wrong outcome.",
        "harm_class": "integrity",
    }


def test_recovers_every_complete_item_and_drops_the_truncated_tail() -> None:
    """The exact shape a real truncated nomination call produces: N complete objects, then a
    partial one cut off mid-string by the token ceiling, no closing `]` or `}` anywhere."""

    body = (
        '{"findings": ['
        + json.dumps(_item("First real finding"))
        + ","
        + json.dumps(_item("Second real finding", line=20))
        + ','
        + json.dumps(_item("Third real finding", line=30))
        + ',{"title": "Fourth finding that never finish'
    )

    recovered = _recover_findings_array(body)

    assert [row["title"] for row in recovered] == [
        "First real finding",
        "Second real finding",
        "Third real finding",
    ], "every COMPLETE item before the cutoff must survive, and the cut-off tail must not"


def test_no_findings_key_returns_empty_not_a_guess() -> None:
    """A genuinely bare single-object reply (no batch at all) must not be misread as a truncated
    array with zero recoverable items standing in for something real."""

    assert _recover_findings_array('{"title": "one bare finding", "file": "x.py"}') == []
    assert _recover_findings_array("") == []
    assert _recover_findings_array("not json at all, just narration") == []


def test_an_empty_array_that_never_closes_recovers_nothing() -> None:
    assert _recover_findings_array('{"findings": [') == []
    assert _recover_findings_array('{"findings": [   ') == []


def test_braces_and_escaped_quotes_inside_string_values_do_not_break_the_scan() -> None:
    """A failure_scenario that itself contains `{`, `}`, and an escaped quote must not desync the
    brace-depth counter -- the classic adversarial input for a hand-rolled bracket matcher."""

    tricky = _item('Dict literal `{"a": 1}` is mutated in place')
    tricky["failure_scenario"] = 'Calling f(x) with x == {"a": 1, "b": "\\"nested\\""} corrupts x.'
    body = '{"findings": [' + json.dumps(tricky) + "," + json.dumps(_item("Second", line=5))

    recovered = _recover_findings_array(body)

    assert len(recovered) == 2
    assert recovered[0]["title"] == 'Dict literal `{"a": 1}` is mutated in place'
    assert recovered[1]["title"] == "Second"


def test_a_stray_non_object_token_between_items_stops_rather_than_guesses() -> None:
    """Adversarial/malformed input: a comma followed by something that is not an object at all.
    The parser must stop cleanly, keeping what it already recovered, never raise, never invent."""

    body = '{"findings": [' + json.dumps(_item("Only real item")) + ', "not an object here'

    recovered = _recover_findings_array(body)

    assert [row["title"] for row in recovered] == ["Only real item"]


def test_a_fully_closed_array_recovers_every_item_too() -> None:
    """Not only the truncated case -- a complete, well-formed array must also recover in full (this
    is the path a healthy, non-truncated reply would take if ever routed through this function)."""

    body = json.dumps({"findings": [_item("A"), _item("B", line=2), _item("C", line=3)]})

    recovered = _recover_findings_array(body)

    assert [row["title"] for row in recovered] == ["A", "B", "C"]


# --------------------------------------------------------------------------------------
# H1 -- end to end: a truncated nomination reply through the REAL audit pipeline.
# --------------------------------------------------------------------------------------


def test_a_truncated_nomination_reply_still_surfaces_its_second_finding() -> None:
    """Before the fix: `_first_json_object` would recover only `TRUNCATION_FINDING` (the first
    item) and the second, real finding plus the truncated third would vanish with no trace. After
    the fix: the second finding reaches the rendered report as an observation, and the incomplete
    third item is correctly absent (it was never valid JSON)."""

    second = {
        "title": "Regex is recompiled on every compress call",
        "file": harness.TARGET,
        "line_start": harness.TRUNCATION_LINE_NO,
        "line_end": harness.TRUNCATION_LINE_NO + 2,
        "cited_line_text": harness._TRUNCATION_LINE,
        "failure_scenario": "The pattern is built inside the hot loop, so every call pays the "
        "compile cost again.",
        "harm_class": "perf",
    }
    truncated_batch = (
        '{"findings": ['
        + harness.TRUNCATION_FINDING
        + ","
        + json.dumps(second)
        + ',{"title": "A third finding the ceiling cut off mid-sentence and never clos'
    )

    decision, router, _tools = harness._drive(
        [truncated_batch, harness.TRUNCATION_SUPPORTED], session_id="audit-truncated-batch"
    )
    stepped = harness._stepped(decision)

    # 2026-08-06: CANDIDATE_UNPROVEN no longer inlines the primary or the additional-findings
    # table into chat -- checked against the structured record instead, same pattern as elsewhere.
    assert "truncat" in str(stepped.get("finding", {}).get("title", "")).lower(), (
        "the provable finding must still lead the structured record"
    )
    additional_titles = {
        str(item.get("title") or "").lower() for item in stepped.get("additional_findings") or []
    }
    assert any("recompiled" in title for title in additional_titles), (
        "a truncated nomination reply must still surface the second, complete finding it "
        f"contains instead of collapsing to the first item alone: {additional_titles}"
    )
    assert not any("cut off mid-sentence" in title for title in additional_titles), (
        "the genuinely incomplete tail item must never be recovered or invented"
    )
    assert router.steps() == ["nominate", "challenge"], (
        "recovering more findings from ONE truncated reply must not cost a second model call"
    )


# --------------------------------------------------------------------------------------
# H2 -- end to end: three nomination attempts, three distinct batches, all three unioned.
# --------------------------------------------------------------------------------------


def _round_finding(title: str, scenario: str, *, line: int, harm_class: str = "integrity") -> dict:
    return {
        "title": title,
        "file": harness.TARGET,
        "line_start": line,
        "line_end": line + 1,
        "cited_line_text": "source line",
        "failure_scenario": scenario,
        "harm_class": harm_class,
    }


def _refuted(reason: str) -> str:
    return json.dumps({"verdict": "refuted", "reason": reason, "counterexample": ""})


def test_batches_from_all_three_nomination_attempts_are_unioned_not_just_the_first() -> None:
    """H2's exact live shape: three sequential nomination attempts, each adversarially screened
    out (the operator's transcript: 3/3 rejected), each carrying a real, distinct extra finding
    beside its screened primary. Before the fix, only the FIRST attempt's extra ever reached the
    report; the second and third were generated at real model cost and thrown away whole."""

    round_one = json.dumps(
        {
            "findings": [
                _round_finding(
                    "A truncated tail record is silently dropped",
                    "When the last chunk's length prefix exceeds the remaining bytes, the except "
                    "clause fires and decompress returns the bytes already collected without "
                    "raising, discarding the tail record.",
                    line=harness.TRUNCATION_LINE_NO,
                ),
                _round_finding(
                    "Startswith check leaves non-prefixed payloads unflagged",
                    "A blob that does not begin with RPT1 skips the strip branch and is decoded "
                    "as raw record data, with no marker recording which format applied.",
                    line=harness.EMPTY_INPUT_LINE_NO,
                    harm_class="api",
                ),
            ]
        }
    )
    round_two = json.dumps(
        {
            "findings": [
                _round_finding(
                    "One bare except merges two unrelated failure causes",
                    "Both a corrupt length byte and a corrupt payload byte reach the same except "
                    "clause at the decode loop, so decompress cannot distinguish which occurred "
                    "and returns a shortened buffer for either.",
                    line=harness.TRUNCATION_LINE_NO,
                ),
                _round_finding(
                    "Declared record length is copied without a bounds check",
                    "A length byte larger than the remaining buffer is copied by extend without "
                    "checking it against the bytes actually available.",
                    line=harness.TRUNCATION_LINE_NO,
                    harm_class="crash",
                ),
            ]
        }
    )
    round_three = json.dumps(
        {
            "findings": [
                _round_finding(
                    "Loop end depends on the buffer draining, not a decoded end marker",
                    "The while loop keeps iterating as long as blob has bytes left, with no "
                    "length field marking where the encoded record stream is supposed to end.",
                    line=harness.EMPTY_INPUT_LINE_NO,
                ),
                _round_finding(
                    "Repeated marker bytes later in the stream are not distinguished from the "
                    "leading one",
                    "Only the very first four bytes are checked against RPT1, so a payload that "
                    "happens to contain that same byte sequence again later is decoded as plain "
                    "data with no ambiguity flagged.",
                    line=harness.EMPTY_INPUT_LINE_NO,
                    harm_class="api",
                ),
            ]
        }
    )

    replies = [
        round_one,
        _refuted("the except clause cannot be reached the way the scenario describes"),
        round_two,
        _refuted("both failure causes described are not distinguishable from the cited source"),
        round_three,
        _refuted("no end marker is claimed anywhere in the format this code implements"),
    ]

    decision, router, _tools = harness._drive(replies, session_id="audit-three-attempts")
    # 2026-08-06: these three are `additional_findings` (survey rows, never promoted or
    # challenged) on a `NO_FINDING` outcome -- `render_audit_report`'s `NO_FINDING` branch no
    # longer inlines them into the chat-facing text (see that branch's own comment), so this
    # union is now checked against the structured detail the runtime exposes for Activity,
    # `details.stepped_audit["additional_findings"]`, instead of the rendered report string.
    additional_titles = {
        str(item.get("title") or "").lower()
        for item in harness._stepped(decision).get("additional_findings") or []
    }
    joined_titles = " | ".join(additional_titles)

    assert any("startswith check leaves non-prefixed payloads" in title for title in additional_titles), (
        "round 1's extra finding must reach additional_findings: " + joined_titles
    )
    assert any(
        "declared record length is copied without a bounds check" in title for title in additional_titles
    ), "round 2's extra finding was thrown away by the old first-batch-wins logic: " + joined_titles
    assert any("repeated marker bytes later in the stream" in title for title in additional_titles), (
        "round 3's extra finding was thrown away by the old first-batch-wins logic: " + joined_titles
    )
    # The read-only ledger bounds a turn at 3 nominate / 2 challenge calls (see the comment beside
    # `_MAX_PROVABLE_CANDIDATE_ROUNDS` in stepped_audit.py), so round 3's challenge is refused by
    # the BUDGET, not answered by the scripted model -- it never reaches `_invoke_manifest`. That
    # is fine and expected: the union this test is pinning happens immediately after each
    # `_nominate_finding` call, before its challenge outcome is known, so round 3's batch is
    # already merged into `survey_rows` by the time its challenge would have run.
    assert router.steps() == ["nominate", "challenge", "nominate", "challenge", "nominate"], (
        "three nomination attempts fit inside the existing read-only ledger already -- the union "
        "must not cost any extra model calls beyond what the ledger already spends"
    )
