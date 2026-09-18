"""Execution truth must say WHAT ran: model, explicit tool, governed retrieval, or governed effect.

The confirmed gap these tests are built around: a turn performed governed web retrieval through the
research lane -- receipt emitted, network reached, provider named -- while `execution_truth`
reported `ran_tool=false` and the Activity panel derived "nothing ran". The old vocabulary knew only
`tool` and `model`, so every receipt-backed retrieval and workspace effect was invisible to the
ledger while being fully visible in the raw event stream, and the two hardcoded tool-event names
were the entire definition of "an execution happened".

So the tests pin the typed registry and the category vocabulary, through the real
`emit_runtime_event` seam every production call site uses, with the real event shapes the emitting
lanes produce (`core/retrieval_observability.py`, `core/live_data_retrieval_receipts.py`,
`core/runtime_execution_tools.py`, `core/fresh_data/fx.py`):

* a receipt-only retrieval is execution truth WITHOUT becoming a pretend tool;
* a typed `web.search` invocation is both tool and retrieval at once;
* failures and refusals are recorded apart from successful execution -- a refused fetch never ran;
* the legacy fields (`ran_tool`, `executed_tools`, ...) derive from the same canonical
  classification as the new ones, so they cannot drift back into a second predicate;
* and when the independent witness saw an execution the ledger missed, the served summary says so
  instead of letting any surface claim "nothing ran" -- fail closed, visibly.
"""
from __future__ import annotations

import json

from core.execution_truth import (
    KIND_EFFECT,
    KIND_MODEL,
    KIND_RETRIEVAL,
    KIND_TOOL,
    classify_runtime_event,
    executed_tools,
    facts_for_turn,
    turn_execution_summary,
    turn_ran_effect,
    turn_ran_retrieval,
    turn_ran_tool,
    verify_execution_truth,
)
from core.runtime_task_events import emit_runtime_event

SESSION = "openclaw:execution-truth-capabilities"


def _context(turn_id: str, session: str = SESSION) -> dict[str, object]:
    """The real shape a turn carries: the client turn id arrives as `cancel_turn_id`."""
    return {"session_id": session, "runtime_session_id": session, "cancel_turn_id": turn_id}


def _web_receipt(retrieval_id: str, *, kind: str = "web_search", status: str = "available") -> dict[str, object]:
    """The terminal `vool.web_retrieval_receipt.v1` shape `finish_web_retrieval` emits."""
    return {
        "schema": "vool.web_retrieval_receipt.v1",
        "retrieval_id": retrieval_id,
        "kind": kind,
        "action": "search",
        "status": status,
        "lifecycle": "terminal",
        "provider_id": "brave" if status == "available" else "",
        "keyed_or_keyless": "keyed" if status == "available" else "",
        "source_count": 3 if status == "available" else 0,
        "source_domains": ["example.org"] if status == "available" else [],
        "failure_class": "" if status in {"available", "unavailable"} else "ConnectTimeout",
    }


def _emit_generic_retrieval(turn_id: str, retrieval_id: str, *, status: str = "available") -> None:
    """Exactly what the research lane emits: a receipt event, and NO tool event of any kind."""
    failed = status in {"failed", "refused"}
    emit_runtime_event(
        _context(turn_id),
        event_type="web_retrieval_failed" if failed else "web_retrieval_completed",
        message="Web retrieval: brave, keyed" if not failed else "Web retrieval failed.",
        details=dict(_web_receipt(retrieval_id, status=status), client_turn_id=turn_id),
    )


def _emit_tool(turn_id: str, tool: str, *, ok: bool = True, tool_call_id: str = "") -> None:
    details: dict[str, object] = {"tool_name": tool, "summary": f"Finished {tool}", "client_turn_id": turn_id}
    if tool_call_id:
        details["tool_call_id"] = tool_call_id
    emit_runtime_event(
        _context(turn_id),
        event_type="tool_executed" if ok else "tool_failed",
        message=f"Finished {tool}",
        details=details,
    )


def _emit_model_proof(turn_id: str, *, provider: str = "openrouter", model: str = "nemotron") -> None:
    emit_runtime_event(
        _context(turn_id),
        event_type="model_lane_proof",
        message="lane completed",
        details={
            "phase": "completed",
            "provider_id": provider,
            "model_id": model,
            "client_turn_id": turn_id,
        },
    )


def _emit_workspace_mutation(
    turn_id: str,
    *,
    intent: str = "workspace.write_file",
    ok: bool = True,
    result_state: str = "applied",
    permission_decision: str = "allowed",
    rollback_id: str = "",
) -> None:
    """The terminal `workspace_mutation_*` shape `core/runtime_execution_tools.py` emits."""
    emit_runtime_event(
        _context(turn_id),
        event_type="workspace_mutation_completed" if ok else "workspace_mutation_failed",
        message=f"{intent}: {result_state}",
        details={
            "tool_intent": intent,
            "canonical_target": "notes/today.md",
            "result_state": result_state,
            "ok": ok,
            "action": "write",
            "permission_decision": permission_decision,
            "rollback_id": rollback_id,
            "client_turn_id": turn_id,
        },
    )


# ---------------------------------------------------------------------------------------------
# The confirmed gap: governed retrieval with no tool event must be execution truth.
# ---------------------------------------------------------------------------------------------


def test_a_receipt_only_retrieval_is_execution_truth_without_becoming_a_tool() -> None:
    """The defect turn: Brave was reached, a receipt exists, and no tool event was ever emitted."""
    turn = "turn-generic-brave"
    _emit_generic_retrieval(turn, "web-retrieval-brave-1")
    summary = turn_execution_summary(turn, session_id=SESSION)
    assert summary["ran_retrieval"] is True
    assert summary["retrieval_count"] == 1
    # `ran_tool` keeps its meaning: no explicit tool was invoked, and the retrieval must not be
    # dressed up as one to become visible -- it is visible under its own category.
    assert summary["ran_tool"] is False
    assert summary["executed_tools"] == []
    assert turn_ran_retrieval(turn, session_id=SESSION) is True
    assert turn_ran_tool(turn, session_id=SESSION) is False
    [operation] = summary["executed_operations"]
    assert operation["kind"] == KIND_RETRIEVAL
    assert operation["name"] == "web_search"
    assert summary["witness"]["consistent"] is True


def test_a_typed_web_search_is_both_tool_and_retrieval() -> None:
    """One invocation, two truths: the model asked for `web.search` AND the network was reached."""
    turn = "turn-typed-web-search"
    _emit_tool(turn, "web.search", tool_call_id="call-ws-1")
    _emit_generic_retrieval(turn, "web-retrieval-ws-1")
    summary = turn_execution_summary(turn, session_id=SESSION)
    assert summary["ran_tool"] is True
    assert summary["ran_retrieval"] is True
    assert summary["tool_names"] == ["web.search"]
    assert [entry["tool"] for entry in summary["executed_tools"]] == ["web.search"]
    # The receipt-backed record stays ONE retrieval: the dual-category tool fact does not add a
    # second fetch to the count of what physically went out.
    assert summary["retrieval_count"] == 1
    [tool_fact] = [fact for fact in facts_for_turn(turn, session_id=SESSION) if fact.kind == KIND_TOOL]
    assert set(tool_fact.categories) == {KIND_RETRIEVAL, KIND_TOOL}


def test_a_failed_typed_market_lookup_is_recorded_apart_from_success() -> None:
    """The live-data lane's own shapes: a per-operation tool_failed plus its batch receipt event."""
    turn = "turn-market-failed"
    _emit_tool(turn, "live_data.market_quote", ok=False)
    emit_runtime_event(
        _context(turn),
        event_type="web_retrieval_completed",
        message="Completed 1 live-data retrieval(s).",
        details={
            "plan_id": "plan-market-1",
            "receipts": [
                dict(
                    _web_receipt("web-retrieval-market-1", kind="live_data_market_quote", status="failed"),
                    action="market_quote",
                )
            ],
            "client_turn_id": turn,
        },
    )
    summary = turn_execution_summary(turn, session_id=SESSION)
    # The attempt ran and broke: that is a run for "did anything execute", and it is NEVER evidence
    # of success -- nothing failed may appear among executed tools or executed operations.
    assert summary["ran_tool"] is True
    assert summary["ran_retrieval"] is True
    assert summary["executed_tools"] == []
    assert summary["executed_operations"] == []
    assert {operation["name"] for operation in summary["failed_operations"]} == {
        "live_data.market_quote",
        "live_data_market_quote",
    }
    assert summary["failed_tools"] == ["live_data.market_quote"]


def test_a_workspace_write_is_a_governed_effect_and_a_read_is_not() -> None:
    turn = "turn-workspace-write"
    _emit_tool(turn, "workspace.read_file", tool_call_id="call-read-1")
    assert turn_ran_effect(turn, session_id=SESSION) is False
    _emit_workspace_mutation(turn, rollback_id="rb-write-1")
    summary = turn_execution_summary(turn, session_id=SESSION)
    assert summary["ran_tool"] is True
    assert summary["ran_effect"] is True
    assert summary["effect_count"] == 1
    assert summary["ran_retrieval"] is False
    effect_ops = [op for op in summary["executed_operations"] if op["kind"] == KIND_EFFECT]
    assert [op["name"] for op in effect_ops] == ["workspace.write_file"]


def test_a_denied_workspace_mutation_is_refused_not_executed() -> None:
    """Policy stopped the write before it touched the workspace: nothing ran, and the record says so."""
    turn = "turn-workspace-denied"
    _emit_workspace_mutation(
        turn, ok=False, result_state="permission_denied", permission_decision="denied"
    )
    summary = turn_execution_summary(turn, session_id=SESSION)
    assert summary["ran_effect"] is False
    assert turn_ran_effect(turn, session_id=SESSION) is False
    [refused] = summary["refused_operations"]
    assert refused["name"] == "workspace.write_file"
    assert refused["kind"] == KIND_EFFECT
    assert summary["failed_operations"] == []


def test_a_refused_web_retrieval_never_counts_as_having_run() -> None:
    turn = "turn-retrieval-refused"
    _emit_generic_retrieval(turn, "web-retrieval-refused-1", status="refused")
    summary = turn_execution_summary(turn, session_id=SESSION)
    assert summary["ran_retrieval"] is False
    assert summary["executed_operations"] == []
    assert summary["failed_operations"] == []
    [refused] = summary["refused_operations"]
    assert refused["kind"] == KIND_RETRIEVAL
    # Refusal stays VISIBLE: excluded from every "this ran" answer, present in the record.
    assert refused["status"] == "refused"


def test_a_model_only_turn_reports_model_and_nothing_else() -> None:
    turn = "turn-model-only"
    _emit_model_proof(turn)
    summary = turn_execution_summary(turn, session_id=SESSION)
    assert summary["ran_model"] is True
    assert summary["model_calls"] == 1
    assert summary["ran_tool"] is False
    assert summary["ran_retrieval"] is False
    assert summary["ran_effect"] is False
    [operation] = summary["executed_operations"]
    assert operation["kind"] == KIND_MODEL


def test_a_mixed_turn_reports_every_category_without_conflation() -> None:
    turn = "turn-mixed"
    _emit_model_proof(turn)
    _emit_tool(turn, "web.search", tool_call_id="call-mixed-1")
    _emit_generic_retrieval(turn, "web-retrieval-mixed-1")
    _emit_workspace_mutation(turn, rollback_id="rb-mixed-1")
    summary = turn_execution_summary(turn, session_id=SESSION)
    assert summary["ran_model"] is True
    assert summary["ran_tool"] is True
    assert summary["ran_retrieval"] is True
    assert summary["ran_effect"] is True
    assert summary["model_calls"] == 1
    assert summary["tool_count"] == 1
    assert summary["retrieval_count"] == 1
    assert summary["effect_count"] == 1
    assert summary["fact_count"] == 4
    assert summary["witness"]["consistent"] is True


def test_the_same_retrieval_recorded_twice_is_still_one_execution() -> None:
    """A retry of the recording (same retrieval_id) must not become a second fetch."""
    turn = "turn-retrieval-retry"
    _emit_generic_retrieval(turn, "web-retrieval-retry-1")
    _emit_generic_retrieval(turn, "web-retrieval-retry-1")
    summary = turn_execution_summary(turn, session_id=SESSION)
    assert summary["retrieval_count"] == 1
    assert len(summary["executed_operations"]) == 1


def test_two_distinct_retrievals_stay_two_executions() -> None:
    turn = "turn-retrieval-two"
    _emit_generic_retrieval(turn, "web-retrieval-two-a")
    _emit_generic_retrieval(turn, "web-retrieval-two-b")
    assert turn_execution_summary(turn, session_id=SESSION)["retrieval_count"] == 2


# ---------------------------------------------------------------------------------------------
# Receipt/event disagreement must fail closed and remain visible.
# ---------------------------------------------------------------------------------------------


def test_a_witnessed_retrieval_the_ledger_missed_fails_the_gate_and_marks_the_summary() -> None:
    """The event stream proves a retrieval ran; the fact was dropped. No surface may say "nothing ran"."""
    turn = "turn-retrieval-dropped"
    _emit_generic_retrieval(turn, "web-retrieval-dropped-1")
    from core.runtime_continuity import _conn

    conn = _conn()
    try:
        conn.execute("DELETE FROM execution_facts WHERE turn_key = ?", (turn,))
        conn.commit()
    finally:
        conn.close()

    verdict = verify_execution_truth(turn, session_id=SESSION)
    assert verdict.consistent is False
    assert "web_search" in verdict.missing
    assert f"{KIND_RETRIEVAL}:web_search" in verdict.missing_qualified

    summary = turn_execution_summary(turn, session_id=SESSION)
    assert summary["witness"]["consistent"] is False
    assert f"{KIND_RETRIEVAL}:web_search" in summary["witness"]["missing"]


def test_the_gate_witnesses_effects_as_well_as_tools() -> None:
    turn = "turn-effect-dropped"
    _emit_workspace_mutation(turn, rollback_id="rb-dropped-1")
    from core.runtime_continuity import _conn

    conn = _conn()
    try:
        conn.execute("DELETE FROM execution_facts WHERE turn_key = ?", (turn,))
        conn.commit()
    finally:
        conn.close()

    verdict = verify_execution_truth(turn, session_id=SESSION)
    assert verdict.consistent is False
    assert f"{KIND_EFFECT}:workspace.write_file" in verdict.missing_qualified


# ---------------------------------------------------------------------------------------------
# The typed registry is the one definition, and the legacy fields derive from it.
# ---------------------------------------------------------------------------------------------


def test_the_registry_classifies_each_governed_event_family() -> None:
    """Removing or miswiring one registry mapping must fail here by name."""
    tool_specs = classify_runtime_event("tool_executed", {"tool_name": "web.search"})
    assert [spec.kind for spec in tool_specs] == [KIND_TOOL]
    assert set(tool_specs[0].categories) == {KIND_RETRIEVAL, KIND_TOOL}

    plain_specs = classify_runtime_event("tool_executed", {"tool_name": "pdf.extract_tables"})
    assert plain_specs[0].categories == (KIND_TOOL,)

    retrieval_specs = classify_runtime_event("web_retrieval_completed", _web_receipt("web-retrieval-cls-1"))
    assert [spec.kind for spec in retrieval_specs] == [KIND_RETRIEVAL]
    assert retrieval_specs[0].ok is True

    fx_specs = classify_runtime_event(
        "fx_retrieval_completed", {"retrieval_id": "fx-1", "kind": "fx_quote", "status": "available"}
    )
    assert [spec.kind for spec in fx_specs] == [KIND_RETRIEVAL]

    effect_specs = classify_runtime_event(
        "workspace_mutation_completed",
        {"tool_intent": "workspace.write_file", "result_state": "applied", "ok": True},
    )
    assert [spec.kind for spec in effect_specs] == [KIND_EFFECT]

    model_specs = classify_runtime_event(
        "model_lane_proof", {"phase": "completed", "provider_id": "ollama", "model_id": "qwen3"}
    )
    assert [spec.kind for spec in model_specs] == [KIND_MODEL]

    # Non-executions stay non-executions: selection and planning prove nothing ran.
    assert classify_runtime_event("tool_selected", {"tool_name": "web.search"}) == []
    assert classify_runtime_event("live_data_plan_created", {"plan_id": "p"}) == []
    assert classify_runtime_event("web_retrieval_started", _web_receipt("web-retrieval-cls-2", status="started")) == []


def test_legacy_fields_are_projections_of_the_same_canonical_facts() -> None:
    """`ran_tool`/`executed_tools` must be the category projection of the ledger, not a second store."""
    turn = "turn-legacy-derived"
    _emit_model_proof(turn)
    _emit_tool(turn, "web.search", tool_call_id="call-legacy-1")
    _emit_generic_retrieval(turn, "web-retrieval-legacy-1")
    facts = facts_for_turn(turn, session_id=SESSION)
    summary = turn_execution_summary(turn, session_id=SESSION)
    canonical_tools = [fact for fact in facts if fact.in_category(KIND_TOOL)]
    assert summary["ran_tool"] is bool(canonical_tools)
    assert summary["tool_count"] == len(canonical_tools)
    assert [entry["receipt_key"] for entry in summary["executed_tools"]] == [
        fact.fact_id for fact in canonical_tools if fact.ok
    ]
    assert [entry["receipt_key"] for entry in executed_tools(turn, session_id=SESSION)] == [
        fact.fact_id for fact in canonical_tools if fact.ok
    ]
    # And the serialized fact carries its categories, so no reader has to re-derive them.
    [tool_fact] = canonical_tools
    assert json.loads(json.dumps(tool_fact.to_dict()))["categories"] == sorted(tool_fact.categories)
