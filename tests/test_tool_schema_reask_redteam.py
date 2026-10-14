"""Hostile inputs against the tool-schema re-ask: cross-tool confusion, oversize payloads, unoffered tools."""
from __future__ import annotations

import json

from tests.test_tool_schema_reask import _body, _invoke, paid_home, wire


def test_unknown_tool_with_passing_arguments_for_another_tool_gets_no_reask(wire):
    # The arguments are valid for archive.inspect, but the call names a tool the turn
    # never offered. The name defect must win over any argument reading, and the
    # unknown name must not buy the model a second dispatch.
    wire.outcomes = [_body({"archive_id": "sample"}, name="not_offered")]
    response, error, context = _invoke(wire)
    assert response is None and error.startswith("unknown_tool_name:")
    assert len(wire.bodies) == 1
    assert context["last_tool_call_rejection"]["error_kind"] == "unknown_tool_name"


def test_combined_name_and_argument_defects_still_get_exactly_one_dispatch(wire):
    # Both defects at once must not stack into extra correction attempts.
    wire.outcomes = [_body({"tape_id": "sample"}, name="not_offered")]
    response, error, _ = _invoke(wire)
    assert response is None and error.startswith("unknown_tool_name:")
    assert len(wire.bodies) == 1


def test_oversize_rejected_arguments_are_not_echoed_to_the_provider(wire):
    # A two-megabyte argument value that fails the schema (wrong key) must not leak
    # back into the correction dispatch: the re-ask carries the original request plus
    # static feedback only.
    huge = "A" * 2_000_000
    wire.outcomes = [_body({"tape_id": huge}), _body({"archive_id": "sample"})]
    response, error, _ = _invoke(wire)
    assert response is not None and error is None, error
    assert len(wire.bodies) == 2
    assert huge not in json.dumps(wire.bodies[1])
    assert len(json.dumps(wire.bodies[1])) < 100_000


def test_oversize_valid_arguments_do_not_break_the_turn(wire):
    # A giant value that PASSES the schema is the executor's business, not the
    # re-ask's: nothing here may crash, loop, or start a correction round.
    huge = "B" * 2_000_000
    wire.outcomes = [_body({"archive_id": huge})]
    response, error, _ = _invoke(wire)
    assert response is not None and error is None, error
    assert len(wire.bodies) == 1


def test_oversize_unknown_key_names_keep_the_rejection_bounded(wire):
    # Thousands of hostile unknown keys must still end as one typed rejection with
    # one bounded correction attempt, never a loop.
    hostile = {f"junk_key_{i}": "x" for i in range(2_000)}
    wire.outcomes = [_body(hostile)]
    response, error, _ = _invoke(wire)
    assert response is None and error.startswith("malformed_tool_arguments:")
    assert len(wire.bodies) == 2


def test_reask_answer_that_adds_an_unoffered_tool_ends_as_a_typed_rejection(wire):
    # The correction round replies with a tool the turn never offered. It must be
    # refused, never executed, and there must be no third dispatch.
    wire.outcomes = [
        _body({"tape_id": "sample"}),
        _body({"query": "anything"}, name="not_offered"),
    ]
    response, error, context = _invoke(wire)
    assert response is None and error.startswith("unknown_tool_name:")
    assert len(wire.bodies) == 2
    assert context["last_tool_call_rejection"]["error_kind"] == "unknown_tool_name"


def test_reask_answer_that_adds_an_offered_but_unrelated_tool_is_not_rewritten(wire):
    # The model may legitimately answer the correction with a DIFFERENT offered tool
    # (here: asking for clarification). The router must pass that selection through
    # as answered, never rewrite it back toward the originally rejected tool.
    wire.outcomes = [
        _body({"tape_id": "sample"}),
        _body({"message": "Which archive did you mean?"}, name="respond__direct"),
    ]
    response, error, _ = _invoke(wire)
    assert response is not None and error is None, error
    assert len(wire.bodies) == 2
    assert response.tool_calls[0].intent == "respond.direct"
    assert response.tool_calls[0].arguments == {"message": "Which archive did you mean?"}


def test_reask_answer_with_one_bad_call_in_a_batch_rejects_the_whole_batch_once(wire):
    # A batch where one call is valid and one fails the schema: the bad call must not
    # be silently dropped so the good one can run, and the single correction round
    # still applies to the batch as a unit.
    good = {"id": "call-good", "type": "function",
            "function": {"name": "archive__inspect", "arguments": json.dumps({"archive_id": "sample"})}}
    bad = {"id": "call-bad", "type": "function",
           "function": {"name": "archive__inspect", "arguments": json.dumps({"tape_id": "sample"})}}
    first = _body({"tape_id": "sample"})
    first["choices"][0]["message"]["tool_calls"] = [good, bad]
    wire.outcomes = [first, _body({"archive_id": "sample"})]
    response, error, _ = _invoke(wire)
    assert response is not None and error is None, error
    assert len(wire.bodies) == 2
    executed = {call.intent for call in response.tool_calls}
    assert executed == {"archive.inspect"}


def _paid_terminal_evidence(wire, paid_home, outcomes):
    from unittest import mock

    from core.model_orchestration_state import ModelOrchestrationStore
    from core.model_pricing import ModelPrice
    from core.model_spend_ledger import get_spend_reservation
    from tests.test_tool_schema_reask import _reserve_paid

    wire.outcomes = outcomes
    authorization, context = _reserve_paid(wire, paid_home)
    receipts = []

    def _record(_context, *, event_type, **event):
        if str(event_type).startswith("paid_call."):
            receipts.append((event_type, event["details"]))

    with (
        mock.patch("core.model_pricing._openrouter_price", return_value=ModelPrice(
            prompt_usd_per_token=0.0000004, completion_usd_per_token=0.0000016,
            request_usd=0.0, known=True, source="synthetic catalog tariff",
        )),
        mock.patch("core.runtime_task_events.emit_runtime_event", side_effect=_record),
    ):
        response, error, _ = _invoke(wire, paid=True, authorization=authorization, source_context=context)
    run = ModelOrchestrationStore().current(authorization.escalation.run_id)
    return response, error, receipts, get_spend_reservation(authorization.model_call_id), run


def test_paid_correction_that_is_refused_again_still_closes_with_one_spend_receipt(wire, paid_home):
    # Both paid attempts were sent and may be billed. The hold stays, and the owner still gets
    # exactly one terminal spend receipt and a closed paid run, as for any other refused call.
    response, error, receipts, held, run = _paid_terminal_evidence(
        wire, paid_home, [_body({"tape_id": "sample"})]
    )
    assert response is None and error.startswith("malformed_tool_arguments:")
    assert len(wire.bodies) == 2
    assert held.status == "billing_ambiguous" and held.reserved_usd > 0
    assert [event for event, _ in receipts] == ["paid_call.billing_ambiguous"], receipts
    assert receipts[0][1]["spend_status"] == "billing_ambiguous"
    assert str(run["state"]) != "paid_running", run


def test_paid_correction_that_succeeds_closes_with_one_spend_receipt(wire, paid_home):
    response, error, receipts, held, run = _paid_terminal_evidence(
        wire, paid_home, [_body({"tape_id": "sample"}), _body({"archive_id": "sample"})]
    )
    assert response is not None and error is None, error
    assert len(wire.bodies) == 2
    assert held.status == "billing_ambiguous" and held.reserved_usd > 0
    assert [event for event, _ in receipts] == ["paid_call.billing_ambiguous"], receipts
    assert str(run["state"]) != "paid_running", run


def test_free_lane_keeps_its_one_resend_for_an_unoffered_tool_name(wire):
    # The verified-free lane's existing single resend for a named tool-call defect stays as it
    # was; only argument defects moved to the router's corrective re-ask.
    wire.outcomes = [_body({"archive_id": "sample"}, name="not_offered"), _body({"archive_id": "sample"})]
    response, error, _ = _invoke(wire, adapter_free_retry=True)
    assert response is not None and error is None, error
    assert len(wire.bodies) == 2
    assert response.tool_calls[0].arguments == {"archive_id": "sample"}


def test_free_lane_unoffered_tool_name_twice_ends_typed_after_two_sends(wire):
    wire.outcomes = [_body({"archive_id": "sample"}, name="not_offered")]
    response, error, _ = _invoke(wire, adapter_free_retry=True)
    assert response is None and error.startswith("unknown_tool_name:")
    assert len(wire.bodies) == 2


def test_free_lane_resend_and_router_correction_never_stack_past_three_sends(wire):
    # Unoffered name, then a bad argument on the resend, then the router's correction: the
    # correction dispatch itself gets no further hidden resend.
    wire.outcomes = [
        _body({"archive_id": "sample"}, name="not_offered"),
        _body({"tape_id": "sample"}),
        _body({"archive_id": "sample"}, name="not_offered"),
        _body({"archive_id": "sample"}),
    ]
    response, error, _ = _invoke(wire, adapter_free_retry=True)
    assert response is None and error.startswith("unknown_tool_name:")
    assert len(wire.bodies) == 3
