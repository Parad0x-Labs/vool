"""Hostile inputs against the tool-schema re-ask: cross-tool confusion, oversize payloads, unoffered tools."""
from __future__ import annotations

import json

import pytest

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
    # The enum value is upper case; the line above alone cannot see a run left open.
    assert run is not None and str(run["state"]) == "FAILED_SAFE", run


def test_paid_correction_that_succeeds_closes_with_one_spend_receipt(wire, paid_home):
    response, error, receipts, held, run = _paid_terminal_evidence(
        wire, paid_home, [_body({"tape_id": "sample"}), _body({"archive_id": "sample"})]
    )
    assert response is not None and error is None, error
    assert len(wire.bodies) == 2
    assert held.status == "billing_ambiguous" and held.reserved_usd > 0
    assert [event for event, _ in receipts] == ["paid_call.billing_ambiguous"], receipts
    assert str(run["state"]) != "paid_running", run
    assert run is not None and str(run["state"]) == "COMPLETED", run


def _duplicate_body():
    body = _body({"archive_id": "sample"})
    body["choices"][0]["message"]["tool_calls"] *= 2
    return body


@pytest.mark.parametrize(("outcome", "kind"), [
    (lambda: _body({"archive_id": "sample"}, name="not_offered"), "unknown_tool_name"),
    (_duplicate_body, "duplicate_tool_call"),
])
def test_paid_typed_refusal_without_correction_holds_the_charge_with_one_receipt(wire, paid_home, outcome, kind):
    # The provider answered, so the refused call may be billed: it must never be released to zero.
    response, error, receipts, held, run = _paid_terminal_evidence(wire, paid_home, [outcome()])
    assert response is None and error.startswith(f"{kind}:")
    assert len(wire.bodies) == 1
    assert held.status == "billing_ambiguous" and held.reserved_usd > 0
    assert [event for event, _ in receipts] == ["paid_call.billing_ambiguous"], receipts
    assert run is not None and str(run["state"]) == "FAILED_SAFE", run


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


@pytest.mark.parametrize(("arguments", "code"), [
    ('{"left":19}', "schema_validation_failed"),  # schema violation (missing key)
    ('{"left":19,', "malformed_arguments"),  # arguments are not JSON at all
])
def test_local_certification_still_tells_schema_violations_from_broken_json(tmp_path, arguments, code):
    # Typing schema violations for the router must not relabel what the local tool
    # certification records about a model that keeps sending them.
    from core.local_model_tool_certification import run_local_model_tool_certification
    from tests.test_local_model_tool_certification import ProbeExchange, _call, _manifest, _reply

    class AlwaysBad(ProbeExchange):
        def __call__(self, **kwargs):
            if {tool.intent for tool in kwargs.get("tools") or ()} == {"probe.add", "probe.lookup_nonce"}:
                self.calls.append(kwargs)
                self.parallel_attempts += 1
                return _reply(
                    _call("vool_probe_add", arguments, "call-add"),
                    _call("vool_probe_lookup_nonce", '{"key":"alpha"}', "call-nonce"),
                )
            return super().__call__(**kwargs)

    exchange = AlwaysBad()
    result = run_local_model_tool_certification(_manifest(), exchange=exchange, db_path=tmp_path / "cert.db")
    assert exchange.parallel_attempts == 2
    assert result["stages"]["adapter_translation"]["failure_code"] == code, result["stages"]


def _not_json_body():
    body = _body({"archive_id": "sample"})
    body["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = '{"archive_id": not json'
    return body


def _sent_correction(body):
    return "argument-schema validation" in json.dumps(body)


def test_arguments_that_are_not_json_are_refused_with_one_request_and_no_reask(wire):
    # Only arguments that parse but break the offered schema are re-asked. Text that is not JSON
    # stays the train's closed refusal on every lane: one request, nothing returned to execute.
    wire.outcomes = [_not_json_body(), _body({"archive_id": "sample"})]
    response, error, context = _invoke(wire)
    assert response is None and error.startswith("malformed_tool_arguments:")
    assert len(wire.bodies) == 1
    assert context["last_tool_call_rejection"]["error_kind"] == "malformed_tool_arguments"


def test_free_lane_not_json_arguments_keep_the_train_resend_and_get_no_correction(wire):
    # The verified-free lane keeps its single identical resend for not-JSON arguments, as on the
    # train; the router adds no correction prompt on top of it.
    wire.outcomes = [_not_json_body()]
    response, error, _ = _invoke(wire, adapter_free_retry=True)
    assert response is None and error.startswith("malformed_tool_arguments:")
    assert len(wire.bodies) == 2
    assert not any(_sent_correction(body) for body in wire.bodies)

