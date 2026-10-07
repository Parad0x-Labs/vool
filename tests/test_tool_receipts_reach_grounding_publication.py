"""A tool that really ran this turn supports the answer built from it at the publication gate.

Measured on the served daemon (a real `apps.vool_api_server` behind a scripted provider): a
current-information workspace question ("What files are in my workspace right now?") ran
`workspace.list_files`, the tool returned the three real names, and the gate served the typed
refusal "The retrieval for this turn returned no usable rows". Two defects, one after the other:

1. `core.execution_records.record` stamped the turn from ``cancel_turn_id`` only. The tool loop's
   context carries ``turn_id``, so every tool-loop record was UNATTRIBUTED, and
   `harvest_execution_records` -- which reads ``cancel_turn_id`` then ``turn_id`` and only this
   turn's records -- found nothing.
2. With the stamp repaired, a text search or a file read still left nothing to match: a record kept
   only the item NAMES a tool declares, and the evidence of a search or a read is its content.
   The search found "The launch date is 14 March", the answer said so, and the gate refused it.

These tests drive the real seams: `core.tool_intent_executor._record_execution` (the one place a
tool call and its turn meet), `harvest_execution_records`, and `publication_verdict`. The negative
controls are the point as much as the positive ones: an invented value, a previous turn's tool
call, a failed tool, and the runtime's own plumbing must never become support.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from core import execution_records
from core.grounding_lifecycle import (
    harvest_execution_records,
    lifecycle_for_context,
    record_model_authorship,
    register_required,
)
from core.grounding_publication import EXIT_PARTIAL, publication_verdict
from core.tool_intent_executor import _record_execution

SEARCH_TEXT = 'Search matches for "launch date":\n- beta.md:2 The launch date is 14 March and the owner is the ops team.'
# The typed payload `workspace.search_text` returns beside that rendering; only this is evidence.
SEARCH_MATCHES = [{"path": "beta.md", "line": 2, "snippet": "The launch date is 14 March and the owner is the ops team."}]
REQUEST = "Search my workspace files for the launch date and tell me what it is right now."


def _turn_context(session_id: str, turn_id: str) -> dict:
    """The tool loop's shape: ``turn_id`` set, ``cancel_turn_id`` absent (as served)."""

    return {"session_id": session_id, "runtime_session_id": session_id, "turn_id": turn_id}


def _open_lifecycle(context: dict, request: str = REQUEST):
    lifecycle_id = register_required(context, request_text=request, reason_codes=("request_promises_evidence",))
    assert lifecycle_id, "the lifecycle must open for a current-information turn"
    return lifecycle_id


def _run_tool(context: dict, session_id: str, *, intent: str, response_text: str, ok: bool = True,
              observation: dict | None = None, payload: dict | None = None) -> None:
    execution = SimpleNamespace(
        ok=ok,
        status="executed" if ok else "failed",
        response_text=response_text,
        details={"observation": dict(observation or {"intent": intent, "ok": ok}),
                 **(payload if payload is not None else {"matches": SEARCH_MATCHES})},
    )
    _record_execution(execution, session_id=session_id, intent=intent, arguments={"query": "launch date"},
                      source_context=context)


def _verdict(context: dict, answer: str):
    harvest_execution_records(context)
    record_model_authorship(context)
    lifecycle = lifecycle_for_context(context)
    assert lifecycle is not None
    return publication_verdict(lifecycle, answer)


@pytest.fixture
def session() -> str:
    session_id = f"grounding-receipts-{uuid.uuid4().hex[:8]}"
    yield session_id
    execution_records.clear(session_id)


def test_a_tool_loop_record_is_stamped_with_the_turn_that_ran_it(session: str) -> None:
    context = _turn_context(session, "turn-a")
    _run_tool(context, session, intent="workspace.search_text", response_text=SEARCH_TEXT)
    [entry] = execution_records.records_for(session)
    assert entry.turn_id == "turn-a", "a record from a context carrying only turn_id was left unattributed"
    assert execution_records.records_for_turn(session, "turn-a") == (entry,)
    assert execution_records.unattributed_count(session) == 0


def test_cancel_turn_id_still_wins_when_both_are_present(session: str) -> None:
    context = {**_turn_context(session, "turn-internal"), "cancel_turn_id": "client-turn-7"}
    _run_tool(context, session, intent="workspace.search_text", response_text=SEARCH_TEXT)
    [entry] = execution_records.records_for(session)
    assert entry.turn_id == "client-turn-7"


def test_an_answer_restating_the_search_result_is_published(session: str) -> None:
    context = _turn_context(session, "turn-true")
    _open_lifecycle(context)
    _run_tool(context, session, intent="workspace.search_text", response_text=SEARCH_TEXT)
    answer = "According to beta.md, the launch date is 14 March."
    verdict = _verdict(context, answer)
    assert verdict.coverage == "full", verdict
    assert verdict.content == answer
    assert not verdict.withheld_claims


def test_an_invented_value_is_refused_even_though_the_tool_ran(session: str) -> None:
    context = _turn_context(session, "turn-invented")
    _open_lifecycle(context)
    _run_tool(context, session, intent="workspace.search_text", response_text=SEARCH_TEXT)
    verdict = _verdict(context, "According to beta.md, the launch date is 3 April.")
    assert verdict.coverage == "none", verdict
    assert "3 April" not in verdict.content


def test_a_mixed_answer_keeps_the_supported_line_and_withholds_the_invented_one(session: str) -> None:
    context = _turn_context(session, "turn-mixed")
    _open_lifecycle(context)
    _run_tool(context, session, intent="workspace.search_text", response_text=SEARCH_TEXT)
    verdict = _verdict(
        context,
        "According to beta.md, the launch date is 14 March.\nThe project lead is Dana Whitfield.",
    )
    assert verdict.state == EXIT_PARTIAL, verdict
    assert "14 March" in verdict.content
    assert any("Dana Whitfield" in claim for claim in verdict.withheld_claims)


def test_a_previous_turns_tool_call_does_not_support_this_turn(session: str) -> None:
    earlier = _turn_context(session, "turn-earlier")
    _open_lifecycle(earlier)
    _run_tool(earlier, session, intent="workspace.search_text", response_text=SEARCH_TEXT)

    now = _turn_context(session, "turn-now")
    _open_lifecycle(now, "And what is the launch date in my workspace right now?")
    verdict = _verdict(now, "According to beta.md, the launch date is 14 March.")
    assert verdict.coverage == "no_sources", "an earlier turn's search grounded this turn's claim"
    assert "14 March" not in verdict.content


def test_a_failed_tool_never_supports_a_claim(session: str) -> None:
    context = _turn_context(session, "turn-failed")
    _open_lifecycle(context)
    _run_tool(context, session, intent="workspace.search_text", response_text=SEARCH_TEXT, ok=False)
    verdict = _verdict(context, "According to beta.md, the launch date is 14 March.")
    assert verdict.coverage == "no_sources", verdict
    assert "14 March" not in verdict.content


@pytest.mark.parametrize(
    "intent",
    [
        # The runtime's own plumbing observes nothing outside the runtime.
        "capability.expand_family",
        # The model's own words may never certify themselves.
        "respond.direct",
        # No registered contract: no declared provenance.
        "unregistered.tool_that_does_not_exist",
    ],
)
def test_runtime_plumbing_and_unknown_tools_contribute_no_support(session: str, intent: str) -> None:
    context = _turn_context(session, f"turn-{intent}")
    _open_lifecycle(context)
    _run_tool(context, session, intent=intent, response_text="According to beta.md, the launch date is 14 March.")
    verdict = _verdict(context, "According to beta.md, the launch date is 14 March.")
    assert verdict.coverage == "no_sources", (intent, verdict)


def test_the_kept_result_text_is_bounded_and_secret_redacted(session: str) -> None:
    # A labelled placeholder, not a credential: the redaction rule keys on the label.
    secret = "Pl4ceholder-not-a-real-value-9271"
    context = _turn_context(session, "turn-bounded")
    _run_tool(context, session, intent="workspace.read_file", response_text=f"password={secret}\n" + "x" * 20000,
              payload={"lines": [{"line_number": 1, "text": f"password={secret}"},
                                 {"line_number": 2, "text": "x" * 20000}]})
    [entry] = execution_records.records_for(session)
    assert entry.result_text.startswith("password"), "the read's lines were not kept"
    assert secret not in entry.result_text
    assert len(entry.result_text) <= execution_records._MAX_RESULT_TEXT_CHARS + 32


@pytest.mark.parametrize("has_match", [False, True])
def test_only_a_real_search_match_grounds_the_queried_fact(tmp_path, session: str, has_match: bool) -> None:
    # Pack 2b re-audit, 2026-10-07: a no-results search echoes its query ('No text matches for "The
    # launch date is 14 March." were found'), and the whole rendered result was kept as evidence, so
    # a search that found nothing grounded the very fact it searched for.
    from core.runtime_execution_tools import _search_text

    statement = "The launch date is 14 March."
    (tmp_path / "beta.md").write_text(statement if has_match else "No launch schedule has been approved.")
    context = _turn_context(session, f"turn-search-{has_match}")
    _open_lifecycle(context)
    result = _search_text({"query": statement}, workspace_root=tmp_path)
    assert result.ok and result.status == ("executed" if has_match else "no_results")
    _record_execution(result, session_id=session, intent="workspace.search_text", arguments={"query": statement},
                      source_context=context)
    verdict = _verdict(context, statement)
    assert (verdict.coverage == "full") is has_match, (result.response_text, verdict)


def test_a_read_files_lines_ground_and_an_empty_slice_does_not(tmp_path, session: str) -> None:
    from core.runtime_execution_tools import execute_runtime_tool

    (tmp_path / "beta.md").write_text("Owner: ops team\nThe launch date is 14 March.\n")
    context = _turn_context(session, "turn-read")
    _open_lifecycle(context, "Read beta.md in my workspace and tell me the launch date right now.")
    empty = execute_runtime_tool("workspace.read_file", {"path": "beta.md", "start_line": 40},
                                 source_context={"workspace": str(tmp_path)})
    assert empty is not None and empty.ok and empty.status == "empty_slice"
    _record_execution(empty, session_id=session, intent="workspace.read_file", arguments={"path": "beta.md"},
                      source_context=context)
    assert _verdict(context, "The launch date is 14 March.").coverage != "full"
    read = execute_runtime_tool("workspace.read_file", {"path": "beta.md"}, source_context={"workspace": str(tmp_path)})
    assert read is not None and read.ok and read.status == "executed", read
    _record_execution(read, session_id=session, intent="workspace.read_file", arguments={"path": "beta.md"},
                      source_context=context)
    assert _verdict(context, "The launch date is 14 March.").coverage == "full"
