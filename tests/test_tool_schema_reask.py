"""Schema rejection is model output, not a missing provider reply; re-ask before dispatch."""
from __future__ import annotations

import http.server
import json
import threading
import time
from types import SimpleNamespace
from unittest import mock

import pytest

from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from core.cloud_tool_call_contract import (
    MalformedToolArgumentsError,
    build_cloud_tool_definitions,
    parse_native_tool_calls,
)
from core.memory_first_router import MemoryFirstRouter
from core.provider_call_deadline import PROVIDER_DEADLINE_KEY
from storage.model_provider_manifest import ModelProviderManifest
from tests.test_paid_reasoning_model_recovery import paid_home

TOOLS = build_cloud_tool_definitions([
    {"intent": "archive.inspect", "arguments": {"archive_id": "string"}},
    {"intent": "respond.direct", "arguments": {"message": "string"}},
])


def _body(arguments, *, name="archive__inspect"):
    return {"id": "synthetic-completion", "model": "fixture/model", "choices": [{
        "finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [{
            "id": "call-1", "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)},
        }]},
    }], "usage": {"prompt_tokens": 30, "completion_tokens": 20, "cost": 0.000001}}


@pytest.fixture
def wire():
    state = SimpleNamespace(bodies=[], outcomes=[_body({"tape_id": "sample"}),
                                                _body({"archive_id": "sample"})], after=None)

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            state.bodies.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps(state.outcomes[min(len(state.bodies)-1, len(state.outcomes)-1)]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if state.after:
                state.after(len(state.bodies))
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = 1
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_port}"
    yield state
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def _request(**changes):
    values = dict(task_kind="tool_intent", prompt="Inspect the archive.",
                  messages=[{"role": "user", "content": "Inspect the archive."}],
                  max_output_tokens=400, output_mode="tool_intent",
                  tools=TOOLS, tools_required=True, tool_choice="required")
    values.update(changes)
    return ModelRequest(**values)


def _manifest(wire, *, paid=False):
    return ModelProviderManifest(
        provider_name="openrouter-byok" if paid else "fixture-model",
        model_name="openai/gpt-4.1-mini" if paid else "gpt-4.1-mini", source_type="http",
        adapter_type="openai_compatible", license_name="fixture", license_reference="fixture",
        weight_location="external", runtime_dependency="openai-compatible", capabilities=["tool_intent"],
        runtime_config={"base_url": wire.url, "api_path": "/chat/completions", "timeout_seconds": 5},
        metadata={"cost_class": "paid_cloud" if paid else "free_local", "native_tool_calling": True},
    )


def _invoke(wire, *, request=None, paid=False, authorization=None, source_context=None,
            adapter_free_retry=False):
    manifest = _manifest(wire, paid=paid)
    adapter = OpenAICompatibleAdapter(manifest)
    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter
    context = source_context if source_context is not None else {
        "session_id": "schema-reask", "turn_id": "schema-reask-turn",
        "request_id": "schema-reask-request",
    }
    if authorization:
        context["authorized_paid_call"] = authorization
    from core.turn_model_call_ledger import begin_turn

    begin_turn(context)
    with (
        mock.patch.object(router, "_resolve_dispatch_manifest", return_value=manifest),
        mock.patch.object(router, "_dispatch_binding_is_current", return_value=True),
        mock.patch.object(adapter, "_dispatch_binding", return_value=(wire.url, "")),
        mock.patch.object(adapter, "_supports_native_tools", return_value=True),
        mock.patch.object(adapter, "_is_verified_free_openrouter_lane", return_value=adapter_free_retry),
        mock.patch("core.memory_first_router.should_probe_health", return_value=False),
        mock.patch("core.memory_first_router.circuit_is_open", return_value=False),
    ):
        _, response, error = router._invoke_manifest(
            manifest=manifest, request=request or _request(), output_mode="tool_intent",
            task=SimpleNamespace(task_id="schema-reask-task", prompt_tokens=100, max_output_tokens=400),
            source_context=context,
        )
    return response, error, context


@pytest.mark.parametrize("arguments", [
    {"archive_id": "sample", "tape_id": "sample"}, {}, {"archive_id": 42},
])
def test_schema_errors_keep_the_named_tool_argument_type(arguments):
    with pytest.raises(MalformedToolArgumentsError):
        parse_native_tool_calls(_body(arguments)["choices"][0]["message"]["tool_calls"], definitions=TOOLS)


def test_rejected_schema_gets_one_corrected_call_on_the_same_turn(wire):
    from core.turn_model_call_ledger import turn_call_accounting

    events = []
    with mock.patch("core.memory_first_router.emit_runtime_event", side_effect=lambda _context, **event: events.append(event)):
        response, error, context = _invoke(wire)
    assert error is None and response is not None, error
    assert len(wire.bodies) == 2
    assert response.tool_calls[0].arguments == {"archive_id": "sample"}
    assert wire.bodies[0]["model"] == wire.bodies[1]["model"]
    assert wire.bodies[0]["tools"] == wire.bodies[1]["tools"]
    assert wire.bodies[0]["max_tokens"] == wire.bodies[1]["max_tokens"]
    assert wire.bodies[0]["messages"] != wire.bodies[1]["messages"]
    assert "schema" in json.dumps(wire.bodies[1]["messages"]).lower()
    assert "last_tool_call_rejection" not in context
    completed = [event["details"] for event in events if event["event_type"] == "model.call_completed"]
    assert completed[-1]["tool_call_resolution"]["state"] == "parsed"
    assert any(event["details"].get("retry_policy") == "same_model_tool_schema_correction" for event in events)
    accounting = turn_call_accounting(context)
    assert accounting["calls"] == 2
    assert accounting["failed_calls"] == 1 and accounting["completed_calls"] == 1


def test_second_bad_call_stays_a_typed_rejection_without_another_attempt(wire):
    wire.outcomes = [_body({"tape_id": "sample"})]
    response, error, context = _invoke(wire)
    assert response is None and error.startswith("malformed_tool_arguments:")
    assert len(wire.bodies) == 2
    assert context["last_tool_call_rejection"]["error_kind"] == "malformed_tool_arguments"


@pytest.mark.parametrize("model_request", [
    _request(allow_provider_retry=False),
    _request(cancel_check=lambda: True),
    _request(metadata={PROVIDER_DEADLINE_KEY: time.monotonic()-1}),
])
def test_retry_opt_out_cancellation_and_deadline_do_not_start_a_second_request(wire, model_request):
    response, error, _ = _invoke(wire, request=model_request)
    assert response is None and error
    assert len(wire.bodies) <= 1


def test_unknown_tool_name_is_not_automatically_regenerated(wire):
    wire.outcomes = [_body({"archive_id": "sample"}, name="not_offered")]
    response, error, context = _invoke(wire)
    assert response is None and error.startswith("unknown_tool_name:")
    assert len(wire.bodies) == 1
    assert context["last_tool_call_rejection"]["error_kind"] == "unknown_tool_name"


def test_valid_initial_call_needs_no_reask(wire):
    wire.outcomes = [_body({"archive_id": "sample"})]
    response, error, _ = _invoke(wire)
    assert response is not None and error is None, error
    assert len(wire.bodies) == 1


def test_free_adapter_cannot_multiply_the_router_correction_budget(wire):
    wire.outcomes = [_body({"tape_id": "sample"})]
    response, error, _ = _invoke(wire, adapter_free_retry=True)
    assert response is None and error.startswith("malformed_tool_arguments:")
    assert len(wire.bodies) == 2


@pytest.mark.parametrize("arguments", [{}, {"archive_id": 42}])
def test_missing_and_wrongly_typed_arguments_recover_too(wire, arguments):
    wire.outcomes[0] = _body(arguments)
    response, error, _ = _invoke(wire)
    assert response is not None and error is None
    assert len(wire.bodies) == 2


def test_cancel_after_bad_reply_prevents_correction_dispatch(wire):
    cancelled = []
    wire.after = lambda _number: cancelled.append(True)
    response, error, _ = _invoke(wire, request=_request(cancel_check=lambda: bool(cancelled)))
    assert response is None and error
    assert len(wire.bodies) == 1


def test_deadline_after_bad_reply_prevents_correction_dispatch(wire):
    request = _request()
    # Both per-candidate and retry metadata are copies. Change the active clock,
    # not the original request, when the first provider response arrives.
    deadline = time.monotonic() + 100
    request.metadata[PROVIDER_DEADLINE_KEY] = deadline
    with mock.patch("core.runtime_active_clock.monotonic", side_effect=lambda: (
        deadline + 1 if wire.bodies else deadline - 1
    )):
        response, error, _ = _invoke(wire, request=request)
    assert response is None and error
    assert len(wire.bodies) == 1


def test_duplicate_batch_is_not_regenerated(wire):
    body = _body({"archive_id": "sample"})
    body["choices"][0]["message"]["tool_calls"] *= 2
    wire.outcomes = [body]
    response, error, _ = _invoke(wire)
    assert response is None and error.startswith("duplicate_tool_call:")
    assert len(wire.bodies) == 1


def test_correction_does_not_stack_with_empty_response_retry(wire):
    # No native call and no content after the correction.
    wire.outcomes[1] = {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]}
    response, error, _ = _invoke(wire)
    assert response is None and error
    assert len(wire.bodies) == 2


def test_paid_without_authority_never_dispatches(wire):
    response, error, _ = _invoke(wire, paid=True)
    assert response is None and error == "paid_call_not_authorized_or_reserved"
    assert wire.bodies == []


def _reserve_paid(wire, workspace):
    from core.paid_call_reservation import reserve_owner_pick_paid_call

    manifest = _manifest(wire, paid=True)
    context = {
        "workspace": str(workspace), "workspace_root": str(workspace),
        "surface": "api", "_owner_local": True, "session_id": "schema-reask",
        "turn_id": "schema-reask-turn", "requested_model": manifest.model_name,
        "model_selection": "pin", "model_call_role": "answer_generation",
    }
    authorization = reserve_owner_pick_paid_call(
        manifest=manifest,
        task=SimpleNamespace(task_id="schema-reask-task", prompt_tokens=100, max_output_tokens=400),
        source_context=context, task_kind="chat_conversation", call_role="answer_generation",
    )
    assert authorization is not None
    return authorization, context


def test_paid_correction_uses_the_existing_reservation_and_accounts_both_calls(wire, paid_home):
    from core.model_pricing import ModelPrice
    from core.model_spend_ledger import get_spend_reservation

    authorization, context = _reserve_paid(wire, paid_home)
    # Supply a synthetic catalog tariff, not a network lookup or a mocked money gate.
    with mock.patch("core.model_pricing._openrouter_price", return_value=ModelPrice(
        prompt_usd_per_token=0.0000004, completion_usd_per_token=0.0000016,
        request_usd=0.0, known=True, source="synthetic catalog tariff",
    )):
        response, error, _ = _invoke(wire, paid=True, authorization=authorization, source_context=context)
    assert response is not None and error is None, error
    assert len(wire.bodies) == 2
    held = get_spend_reservation(authorization.model_call_id)
    assert held.status == "billing_ambiguous"
    assert held.reserved_usd > 0  # Failed first response is held, never assumed free.
    assert held.actual_usd == pytest.approx(0.000001)  # Only the replacement's reported cost.


@pytest.mark.parametrize("price_case", ["too_large", "unknown", "ledger_failure"])
def test_paid_correction_admission_failure_sends_only_the_first_call(wire, paid_home, price_case):
    from core.model_spend_ledger import get_spend_reservation

    authorization, context = _reserve_paid(wire, paid_home)
    target = "core.model_spend_ledger.get_spend_reservation" if price_case == "ledger_failure" else "core.model_pricing.estimate_call_usd"
    patch = (mock.patch(target, side_effect=RuntimeError("synthetic ledger unavailable"))
             if price_case == "ledger_failure" else mock.patch(target, return_value=SimpleNamespace(
                 known=price_case != "unknown", usd=10 if price_case == "too_large" else 0,
             )))
    with patch:
        response, error, context = _invoke(wire, paid=True, authorization=authorization, source_context=context)
    assert response is None and error.startswith("malformed_tool_arguments:")
    assert len(wire.bodies) == 1
    assert context["last_tool_call_rejection"]["error_kind"] == "malformed_tool_arguments"
    held = get_spend_reservation(authorization.model_call_id)
    assert held.status in {"reserved", "billing_ambiguous"} and held.reserved_usd > 0
    if price_case != "ledger_failure":
        # With the ledger reachable the refused first attempt is closed, not left reserved.
        assert held.status == "billing_ambiguous"


def _drive_real_loop(wire, monkeypatch, workspace, *, write=False, always_bad=False, outcomes=None):
    from tests import test_tool_loop_liveness as loop

    intent = "workspace.write_file" if write else "workspace.read_file"
    arguments = {"path": "file_a.py", **({"content": "replaced"} if write else {})}
    offered = build_cloud_tool_definitions([
        {"intent": intent, "arguments": {key: "string" for key in arguments}},
        {"intent": "respond.direct", "arguments": {"message": "string"}},
    ])
    wire.outcomes = [_body({"wrong_path": "file_a.py"}, name=intent.replace(".", "__"))]
    if not always_bad:
        wire.outcomes.extend([
            _body(arguments, name=intent.replace(".", "__")),
            _body({"message": "file_a.py contains UNIQ_00."}, name="respond__direct"),
        ])
    if outcomes is not None:
        wire.outcomes = outcomes
    base_router = loop._Router

    class RealSelection(base_router):
        def resolve_tool_intent(self, **kwargs):
            self.calls += 1
            response, error, _ = _invoke(
                wire, request=_request(tools=offered, prompt="Read file_a.py."),
                source_context=kwargs["source_context"],
            )
            if error:
                return SimpleNamespace(used_model=False, structured_output=None, details={"reason": error}, source="provider_execution")
            calls = response.tool_calls
            return loop._tool_decision(calls[0].intent, calls[0].arguments, batch=list(calls))

    monkeypatch.setattr(loop, "_Router", RealSelection)
    return loop._drive(
        [], workspace_path=str(workspace), user_input="Read file_a.py.",
        extra_context={"session_id": "schema-flow", "turn_id": "schema-flow-turn"},
    )


def test_real_loop_recovers_reads_once_and_finishes_the_original_turn(wire, tmp_path, monkeypatch):
    (tmp_path / "file_a.py").write_text("UNIQ_00\n")
    result, router, events = _drive_real_loop(wire, monkeypatch, tmp_path)
    assert result.get("success") is True, result
    assert result["details"]["tool_steps"] == ["workspace.read_file"]
    assert router.calls == 2 and len(wire.bodies) == 3
    assert sum(event.get("event_type") == "tool_executed" for event in events) == 1


def test_repeated_bad_selection_runs_nothing_and_cannot_fall_into_answer_only_chat(wire, tmp_path, monkeypatch):
    result, router, events = _drive_real_loop(wire, monkeypatch, tmp_path, always_bad=True)
    assert result["status"] == "tool_call_rejected" and result["success"] is False
    assert result["details"]["tool_steps"] == []
    assert router.calls == 1 and len(wire.bodies) == 2
    assert not any(event.get("event_type") == "tool_executed" for event in events)


def test_corrected_write_still_stops_at_the_real_approval_boundary(wire, tmp_path, monkeypatch):
    target = tmp_path / "file_a.py"
    target.write_text("UNIQ_00\n")
    result, _, _ = _drive_real_loop(wire, monkeypatch, tmp_path, write=True)
    assert target.read_text() == "UNIQ_00\n", result
    assert "approval" in str(result).lower(), result
    assert len(wire.bodies) == 2


def test_recovery_cannot_reexecute_work_from_an_earlier_round(wire, tmp_path, monkeypatch):
    (tmp_path / "file_a.py").write_text("UNIQ_00\n")
    result, _, events = _drive_real_loop(wire, monkeypatch, tmp_path, outcomes=[
        _body({"path": "file_a.py"}, name="workspace__read_file"),
        _body({"wrong_path": "file_a.py"}, name="workspace__read_file"),
        _body({"path": "file_a.py"}, name="workspace__read_file"),
    ])
    assert result["details"]["tool_steps"] == ["workspace.read_file"]
    assert sum(event.get("event_type") == "tool_executed" for event in events) == 1
    assert result["details"]["loop_stop_reason"] == "repeated_tool_request"
    assert len(wire.bodies) == 3
