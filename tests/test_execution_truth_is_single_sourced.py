"""One execution must produce one authoritative fact that every view derives from.

The failure these tests are built around is not a wrong counter. On 2026-08-15 the runtime executed
116 live-data tool calls; the signed honesty ledger recorded `executed_tools` non-empty zero times
that day, `runtime_tool_receipts` held no `live_data.*` row at all, and the Activity panel could
render "No tool ran -- answered directly" over turns that had gone out to the network. Each of those
views was internally consistent and independently derived, from a different store, using a different
predicate, under a different identifier -- 809 turn receipts and 807 truth-metric records shared
exactly zero ids, so nothing could even be compared.

So the tests are written against the SHAPE that caused it, not against the numbers it produced:

* the thin `{tool_name, summary}` event the live-data lane emits, which carries no receipt and no
  `action_record` -- the shape that was invisible to every receipt store;
* turn scoping, because the old reader took the last 8 receipts for the whole SESSION and would
  attach another turn's tool to this turn's signature -- evidence-shaped and wrong, which is worse
  than the empty list it replaced;
* idempotence, because one execution recorded twice inflates every derived count at once;
* and a sabotage that drops the record, because views that all read one source agree perfectly while
  being wrong together. Only an independent witness can catch that, and this proves it does.

Deliberately driven through the real `emit_runtime_event` seam every production call site uses. A
test that called `record_execution` directly would prove the store works and nothing about whether
the runtime reaches it -- which was the entire defect.
"""
from __future__ import annotations

import pytest

from core.execution_truth import (
    KIND_MODEL,
    KIND_TOOL,
    executed_tools,
    facts_for_turn,
    model_calls,
    record_execution,
    resolve_turn_key,
    turn_execution_summary,
    turn_ran_tool,
    verify_execution_truth,
)
from core.runtime_task_events import emit_runtime_event

SESSION = "openclaw:execution-truth-test"


def _context(turn_id: str, session: str = SESSION) -> dict[str, object]:
    """The real shape a turn carries: the client turn id arrives as `cancel_turn_id`."""
    return {"session_id": session, "runtime_session_id": session, "cancel_turn_id": turn_id}


def _emit_thin_live_data(turn_id: str, tool: str = "live_data.weather_lookup", ok: bool = True) -> None:
    """Exactly the event the live-data lane emits: no action_record, no receipt, no tool_call_id."""
    emit_runtime_event(
        _context(turn_id),
        event_type="tool_executed" if ok else "tool_failed",
        message=f"Finished {tool}: available",
        details={"tool_name": tool, "summary": f"Finished {tool}: available", "client_turn_id": turn_id},
    )


# ---------------------------------------------------------------------------------------------
# The reported failure class: a thin execution reaches every derived view.
# ---------------------------------------------------------------------------------------------


def test_a_thin_live_data_execution_becomes_an_authoritative_fact() -> None:
    """The shape that was invisible to every receipt store is now one fact."""
    turn = "turn-thin-live-data"
    _emit_thin_live_data(turn)

    facts = facts_for_turn(turn, session_id=SESSION)
    assert len(facts) == 1, f"expected exactly one execution fact, got {facts}"
    assert facts[0].kind == KIND_TOOL
    assert facts[0].name == "live_data.weather_lookup"
    assert facts[0].ok is True


def test_every_derived_view_agrees_about_a_thin_execution() -> None:
    """Honesty ledger, Activity and the counters read the same fact, so they cannot disagree."""
    turn = "turn-views-agree"
    _emit_thin_live_data(turn)

    signed = executed_tools(turn, session_id=SESSION)
    summary = turn_execution_summary(turn, session_id=SESSION)

    # What the signed honesty receipt puts its name to -- previously [] for this exact shape.
    assert [entry["tool"] for entry in signed] == ["live_data.weather_lookup"]
    # What Activity renders "No tool ran" from -- previously True for this exact shape.
    assert turn_ran_tool(turn, session_id=SESSION) is True
    # And the one summary the API serves carries the same answer as both.
    assert summary["ran_tool"] is True
    assert summary["executed_tools"] == signed
    assert summary["tool_names"] == ["live_data.weather_lookup"]


def test_the_honesty_ledger_and_activity_cannot_disagree_across_many_executions() -> None:
    """The property, not one example: whatever ran, both views report the same set."""
    turn = "turn-many-executions"
    for tool in ("live_data.weather_lookup", "live_data.market_quote", "web.search"):
        _emit_thin_live_data(turn, tool=tool)

    signed = {entry["tool"] for entry in executed_tools(turn, session_id=SESSION)}
    summary = turn_execution_summary(turn, session_id=SESSION)
    assert signed == set(summary["tool_names"])
    assert turn_ran_tool(turn, session_id=SESSION) is bool(signed)


# ---------------------------------------------------------------------------------------------
# Turn scoping: the leak the old session-wide reader had.
# ---------------------------------------------------------------------------------------------


def test_another_turns_execution_is_not_evidence_for_this_turn() -> None:
    """The old reader took the session's last 8 receipts, so it signed the wrong turn's work."""
    _emit_thin_live_data("turn-earlier", tool="web.fetch")

    assert executed_tools("turn-later", session_id=SESSION) == []
    assert turn_ran_tool("turn-later", session_id=SESSION) is False
    # ...while the turn that really ran it still has it.
    assert [entry["tool"] for entry in executed_tools("turn-earlier", session_id=SESSION)] == ["web.fetch"]


def test_a_second_session_does_not_inherit_the_first_sessions_executions() -> None:
    _emit_thin_live_data("turn-shared-id", tool="workspace.read_file")
    other = "openclaw:some-other-session"
    emit_runtime_event(
        _context("turn-shared-id", session=other),
        event_type="tool_executed",
        message="Finished sandbox.run_command",
        details={"tool_name": "sandbox.run_command", "client_turn_id": "turn-shared-id"},
    )

    first = {entry["tool"] for entry in executed_tools("turn-shared-id", session_id=SESSION)}
    second = {entry["tool"] for entry in executed_tools("turn-shared-id", session_id=other)}
    assert first == {"workspace.read_file"}
    assert second == {"sandbox.run_command"}


# ---------------------------------------------------------------------------------------------
# One execution, one fact.
# ---------------------------------------------------------------------------------------------


def test_the_same_execution_recorded_twice_is_still_one_fact() -> None:
    """A retry or a re-entrant emit must not become two executions' worth of evidence."""
    turn = "turn-duplicate-emit"
    _emit_thin_live_data(turn)
    _emit_thin_live_data(turn)

    assert len(facts_for_turn(turn, session_id=SESSION)) == 1
    assert len(executed_tools(turn, session_id=SESSION)) == 1


def test_two_genuinely_distinct_calls_stay_two_facts() -> None:
    """The dedupe must not collapse real repeated work: distinct call ids are distinct executions."""
    turn = "turn-two-distinct"
    for call_id in ("tool-call-a", "tool-call-b"):
        emit_runtime_event(
            _context(turn),
            event_type="tool_executed",
            message="Finished web.search",
            details={"tool_name": "web.search", "tool_call_id": call_id, "client_turn_id": turn},
        )
    assert len(facts_for_turn(turn, session_id=SESSION)) == 2


# ---------------------------------------------------------------------------------------------
# Negative controls: the views must not fire when nothing ran.
# ---------------------------------------------------------------------------------------------


def test_a_turn_that_ran_nothing_reports_nothing() -> None:
    turn = "turn-no-execution"
    emit_runtime_event(
        _context(turn),
        event_type="task_completed",
        message="Answered directly.",
        details={"client_turn_id": turn},
    )
    assert facts_for_turn(turn, session_id=SESSION) == []
    assert executed_tools(turn, session_id=SESSION) == []
    assert turn_ran_tool(turn, session_id=SESSION) is False
    assert turn_execution_summary(turn, session_id=SESSION)["ran_tool"] is False


def test_a_failed_execution_is_recorded_but_is_not_evidence_of_success() -> None:
    """A failed tool DID run -- Activity must show it -- but it cannot back a completion claim."""
    turn = "turn-failed-tool"
    _emit_thin_live_data(turn, tool="live_data.market_quote", ok=False)

    assert turn_ran_tool(turn, session_id=SESSION) is True, "a failed tool still ran"
    assert executed_tools(turn, session_id=SESSION) == [], "a failed tool backs no claim"
    assert turn_execution_summary(turn, session_id=SESSION)["failed_tools"] == ["live_data.market_quote"]


def test_a_lane_that_mints_its_own_turn_id_still_files_under_the_turn() -> None:
    """Found by a live drive, not by design: one turn split across two identities.

    `checkpoints.prepare_runtime_checkpoint` stamps ONE ambient `turn_id` on the source context for
    the whole turn, but individual lanes mint their own and pass it in the event's details -- the
    fast path emits `model_lane_proof` with `turn_id=response_id`. Preferring the event's own value
    filed that one event under a synthetic key while the rest of the turn filed under the ambient
    one, which is the same fragmentation this module exists to remove, re-created one layer down.
    """
    ambient = "turn-ambient-from-checkpoint"
    context = {"session_id": SESSION, "runtime_session_id": SESSION, "turn_id": ambient}

    emit_runtime_event(
        context,
        event_type="tool_executed",
        message="Finished web.search",
        details={"tool_name": "web.search"},
    )
    # The same turn, and a lane that named itself something else entirely.
    emit_runtime_event(
        context,
        event_type="model_lane_proof",
        message="Fast-path response completed without model inference.",
        details={"turn_id": "fast-f575c179eae3", "phase": "completed", "provider_id": "runtime-fast-path"},
    )
    assert resolve_turn_key(context, {"turn_id": "fast-f575c179eae3"}) == ambient
    assert turn_ran_tool(ambient, session_id=SESSION) is True
    assert facts_for_turn("fast-f575c179eae3", session_id=SESSION) == [], (
        "a lane-local id created a second turn identity"
    )


def test_a_reader_holding_only_a_stored_row_resolves_the_same_turn_as_the_writer() -> None:
    """Found against the live API: two independent derivations of one identity, in this module.

    The writer resolves identity with the source context in hand. A reader -- the events endpoint,
    the turn trace -- holds only the stored row and no context, so if it re-derives it falls to
    different fallbacks and files the same event under a different key. Against the running product
    that produced twenty turn keys (checkpoint ids, request ids) for a session with three real turns,
    none matching the facts written for them. Once stamped, `turn_key` IS the identity and every
    reader must take it.
    """
    ambient = "turn-writer-resolved"
    context = {
        "session_id": SESSION,
        "runtime_session_id": SESSION,
        "turn_id": ambient,
        # The decoys the reader used to land on instead.
        "checkpoint_id": "runtime-decoy-checkpoint",
        "request_id": "decoy-request-id",
    }
    emit_runtime_event(
        context,
        event_type="tool_executed",
        message="Finished live_data.market_quote",
        details={"tool_name": "live_data.market_quote"},
    )

    from core.runtime_continuity import list_runtime_session_events

    stored = [e for e in list_runtime_session_events(SESSION, limit=50) if e.get("tool_name") == "live_data.market_quote"]
    assert stored, "the event was not stored at all"
    row = stored[-1]

    assert row.get("turn_key") == ambient, "the writer did not stamp the resolved identity onto the row"
    # A reader with NO context must reach the same answer from the row alone.
    assert resolve_turn_key(None, dict(row)) == ambient
    assert turn_ran_tool(resolve_turn_key(None, dict(row)), session_id=SESSION) is True


def test_a_lane_local_turn_id_is_still_used_when_there_is_no_ambient_one() -> None:
    """The demotion must not become a deletion: with no checkpoint, the lane's id is all there is."""
    context = {"session_id": SESSION, "runtime_session_id": SESSION}
    assert resolve_turn_key(context, {"turn_id": "lane-only-id"}) == "lane-only-id"


def test_the_client_turn_id_outranks_an_ambient_checkpoint_id() -> None:
    """The product's own turn id is the turn, whatever the runtime minted alongside it."""
    context = {"session_id": SESSION, "cancel_turn_id": "client-abc", "turn_id": "turn-runtime-xyz"}
    assert resolve_turn_key(context, {"client_turn_id": "client-abc"}) == "client-abc"


def test_an_event_with_no_turn_identity_records_no_fact() -> None:
    """Never invent an identity: a synthesized key joins to nothing and re-creates the defect."""
    emit_runtime_event(
        {"session_id": SESSION, "runtime_session_id": SESSION},
        event_type="tool_executed",
        message="Finished web.search",
        details={"tool_name": "web.search"},
    )
    assert resolve_turn_key({"session_id": SESSION}, {"tool_name": "web.search"}) == ""


# ---------------------------------------------------------------------------------------------
# Model calls: the second kind of execution a claim can be grounded in.
# ---------------------------------------------------------------------------------------------


def test_a_completed_provider_lane_is_a_model_execution_fact() -> None:
    turn = "turn-model-call"
    emit_runtime_event(
        _context(turn),
        event_type="model_lane_proof",
        message="openrouter-byok completed with runtime proof.",
        details={
            "client_turn_id": turn,
            "phase": "completed",
            "provider_id": "openrouter-byok",
            "actual_adapter_model_id": "nvidia/nemotron-3.5-lightning:free",
            "schema": "vool.model_lane_proof.v1",
        },
    )
    assert model_calls(turn, session_id=SESSION) == 1
    assert facts_for_turn(turn, session_id=SESSION)[0].kind == KIND_MODEL


def test_a_fast_path_lane_proof_is_not_a_model_call() -> None:
    """The fast path emits the SAME event type to say no model ran. The provider separates them.

    Keyed on the provider rather than on the message text ("completed without model inference"),
    which is prose and drifts. This is the negative control for the model fact: the runtime must not
    start counting a model call on every turn that emits a lane proof.
    """
    turn = "turn-fast-path"
    emit_runtime_event(
        _context(turn),
        event_type="model_lane_proof",
        message="Fast-path response completed without model inference.",
        details={
            "client_turn_id": turn,
            "phase": "completed",
            "provider_id": "runtime-fast-path",
            "model_id": "",
        },
    )
    assert model_calls(turn, session_id=SESSION) == 0


# ---------------------------------------------------------------------------------------------
# The truth gate.
# ---------------------------------------------------------------------------------------------


def test_the_gate_passes_when_every_witnessed_execution_has_a_fact() -> None:
    turn = "turn-gate-clean"
    _emit_thin_live_data(turn)
    verdict = verify_execution_truth(turn, session_id=SESSION)
    assert verdict.consistent is True, verdict.detail
    assert verdict.recorded == verdict.witnessed == 1


def test_the_gate_fails_when_a_witnessed_execution_has_no_fact() -> None:
    """The load-bearing case: the runtime event stream saw work the ledger never recorded.

    Written as a direct simulation of a dropped record so the gate's own logic is proven here; the
    scripted sabotage in `tools/truth/sabotage_execution_truth.py` proves the same thing by really
    removing the production call and confirming this file goes red.
    """
    turn = "turn-gate-dropped"
    # The witness records the execution...
    emit_runtime_event(
        _context(turn),
        event_type="tool_executed",
        message="Finished live_data.weather_lookup",
        details={"tool_name": "live_data.weather_lookup", "client_turn_id": turn},
    )
    # ...and the ledger is then emptied, exactly as a dropped `record_execution` would leave it.
    from core.runtime_continuity import _conn

    conn = _conn()
    try:
        conn.execute("DELETE FROM execution_facts WHERE turn_key = ?", (turn,))
        conn.commit()
    finally:
        conn.close()

    verdict = verify_execution_truth(turn, session_id=SESSION)
    assert verdict.consistent is False
    assert "live_data.weather_lookup" in verdict.missing
    assert "no authoritative execution fact" in verdict.detail


def test_the_gate_is_not_vacuous_when_the_ledger_is_empty_for_an_idle_turn() -> None:
    """A turn that witnessed nothing and recorded nothing is consistent, not silently failing."""
    verdict = verify_execution_truth("turn-never-existed", session_id=SESSION)
    assert verdict.consistent is True
    assert verdict.witnessed == 0


def test_the_gate_scopes_its_witness_to_the_turn_under_test() -> None:
    """A busy session must not make a clean turn look inconsistent, or a dirty one look clean.

    The witness reads the SESSION's event stream, so it only tells the truth about a turn if it is
    filtered to that turn. Without a second turn in the session this is untestable -- every event
    belongs to the turn under test and an unscoped witness gives the same answer. So this builds the
    condition that makes the scoping load-bearing: one turn whose execution is properly recorded,
    beside one whose execution was dropped.
    """
    clean_turn, dirty_turn = "turn-scope-clean", "turn-scope-dirty"
    _emit_thin_live_data(clean_turn, tool="web.search")
    emit_runtime_event(
        _context(dirty_turn),
        event_type="tool_executed",
        message="Finished pdf.extract_tables",
        details={"tool_name": "pdf.extract_tables", "client_turn_id": dirty_turn},
    )
    from core.runtime_continuity import _conn

    conn = _conn()
    try:
        conn.execute("DELETE FROM execution_facts WHERE turn_key = ?", (dirty_turn,))
        conn.commit()
    finally:
        conn.close()

    clean = verify_execution_truth(clean_turn, session_id=SESSION)
    dirty = verify_execution_truth(dirty_turn, session_id=SESSION)

    # The clean turn stays clean even though the session contains an unrecorded execution.
    assert clean.consistent is True, f"the other turn's failure leaked in: {clean.detail}"
    assert "pdf.extract_tables" not in clean.missing
    # ...and the dirty turn is still caught, naming only its own missing execution.
    assert dirty.consistent is False
    assert dirty.missing == ("pdf.extract_tables",)


# ---------------------------------------------------------------------------------------------
# The signed honesty ledger: the consumer the original failure actually landed in.
# ---------------------------------------------------------------------------------------------


def test_the_signed_honesty_receipt_records_the_tools_that_really_ran() -> None:
    """The production failure, end to end: 2,399 signed receipts, 2 with a non-empty tool list.

    Driven through `emit_turn_honesty_receipt` -- the real signing path -- rather than through the
    collector, because the defect was never in the collector's logic. It was that the collector
    asked a store the live-data lane does not write to, so the signature went onto an empty list
    while the turn had genuinely hit the network.
    """
    from core.agent_runtime.action_honesty_validator import emit_turn_honesty_receipt

    turn = "turn-signed-receipt"
    context = _context(turn)
    _emit_thin_live_data(turn, tool="live_data.weather_lookup")

    result = emit_turn_honesty_receipt(
        {"response": "It is 24C and sunny in Pasvalys right now."},
        user_input="weather in pasvalys",
        session_id=SESSION,
        source_context=context,
    )

    stub = result.get("honesty_receipt") or {}
    assert stub, "no honesty receipt was issued at all"

    from core.honesty_receipt import list_honesty_receipts

    signed = [r for r in list_honesty_receipts(SESSION) if r.get("receipt_id") == stub.get("receipt_id")]
    assert signed, "the issued receipt was not persisted to the signed ledger"
    tools = [entry.get("tool") for entry in (signed[0].get("executed_tools") or [])]
    assert "live_data.weather_lookup" in tools, (
        f"the signed receipt does not name the tool that ran: {signed[0].get('executed_tools')!r}"
    )


def test_the_signed_receipt_does_not_borrow_another_turns_tool() -> None:
    """The session-wide reader's failure mode: a signature backed by an unrelated turn's work."""
    from core.agent_runtime.action_honesty_validator import emit_turn_honesty_receipt
    from core.honesty_receipt import list_honesty_receipts

    _emit_thin_live_data("turn-with-the-tool", tool="sandbox.run_command")

    quiet_turn = "turn-that-ran-nothing"
    result = emit_turn_honesty_receipt(
        {"response": "Here is how that works, in general."},
        user_input="explain it",
        session_id=SESSION,
        source_context=_context(quiet_turn),
    )
    stub = result.get("honesty_receipt") or {}
    signed = [r for r in list_honesty_receipts(SESSION) if r.get("receipt_id") == stub.get("receipt_id")]
    assert signed, "no receipt persisted for the quiet turn"
    assert signed[0].get("executed_tools") == [], (
        f"a turn that ran nothing was signed as tool-backed: {signed[0].get('executed_tools')!r}"
    )


# ---------------------------------------------------------------------------------------------
# Distance: the invariant must not depend on the vocabulary it was found with.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool",
    [
        "calendar.create_event",           # nothing to do with weather, markets or the web
        "pdf.extract_tables",
        "ключ.чтение",                     # non-ASCII name
        "a" * 180,                         # absurdly long name
        "tool with spaces and — dashes",
    ],
)
def test_the_invariant_holds_for_tools_never_seen_when_designing_it(tool: str) -> None:
    turn = f"turn-unseen-{abs(hash(tool)) % 10_000}"
    emit_runtime_event(
        _context(turn),
        event_type="tool_executed",
        message=f"Finished {tool}",
        details={"tool_name": tool, "client_turn_id": turn},
    )
    assert [entry["tool"] for entry in executed_tools(turn, session_id=SESSION)] == [tool]
    assert turn_ran_tool(turn, session_id=SESSION) is True
    assert verify_execution_truth(turn, session_id=SESSION).consistent is True


def test_recording_never_raises_on_garbage_input() -> None:
    """Observability must not be able to fail a turn that really worked."""
    assert record_execution(session_id="", turn_key="", kind="", name="", ok=True) == ""
    assert record_execution(session_id=SESSION, turn_key="t", kind=KIND_TOOL, name="", ok=True) == ""
    # A detail dict full of unserializable objects must still not raise.
    assert record_execution(
        session_id=SESSION,
        turn_key="turn-garbage",
        kind=KIND_TOOL,
        name="weird.tool",
        ok=True,
        detail={"obj": object(), "receipt_id": "r-1"},
    )
