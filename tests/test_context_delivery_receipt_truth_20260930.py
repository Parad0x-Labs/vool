"""Context delivery receipt truth + enforcement lane separation (2026-09-30).

Owner-review repairs, pinned as regressions:

1. RECEIPT TRUTH — ``refused`` records an actual refusal; ``over_envelope``
   records the observation. With enforcement off, an over-estimate must be
   receipted as observed-not-refused while the permit is consumable.
2. LANE SEPARATION — a measurement failure with enforcement on fails the
   seal (ProviderPayloadMeasurementFailedError, fail-closed); receipt
   build/persist failures (non-SQL included) never disable enforcement and
   never fail the seal.
3. TOOL-CALL ACCOUNTING — provider-bound message fields outside ``content``
   (tool_calls id/type/function name/arguments, tool-role name and
   tool_call_id) are estimated at the estimator's owner, so a large
   structured argument payload can flip the envelope verdict.

Every seal case runs the real gateway + storage against a disposable home;
the provider transport is intercepted at the permit hand-off (consume()).
"""
from __future__ import annotations

import copy
import json
from unittest.mock import patch

import pytest

from core import context_delivery_ledger as cdl
from core import provider_invocation_gateway as gw
from core.prompt_budget import estimate_message_tokens
from core.provider_invocation_gateway import (
    ProviderInvocationValidationError,
    seal_provider_invocation,
)
from core.runtime_paths import configure_runtime_home


@pytest.fixture
def ledger_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    monkeypatch.delenv(cdl.ENFORCE_FLAG, raising=False)
    configure_runtime_home(tmp_path)
    from storage.migrations import run_migrations
    run_migrations()
    try:
        yield tmp_path
    finally:
        configure_runtime_home(None)


def _seal(payload, *, trace="t", enforcement=None, metadata=None):
    request = gw.DirectProviderRequest(
        trace_id=trace, context={}, metadata=metadata or {},
        max_output_tokens=0)
    return seal_provider_invocation(
        request=request, provider_id="synthetic:audit",
        model_id="synthetic-audit", operation="chat", payload=payload)


def _oversized(num_ctx=128):
    return {"model": "synthetic-audit",
            "messages": [{"role": "user", "content": "夜" * 240}],
            "options": {"num_ctx": num_ctx}}


def _fitting():
    return {"model": "synthetic-audit",
            "messages": [{"role": "user", "content": "hello"}],
            "options": {"num_ctx": 4096}}


# ── 1. receipt truth, both flag states, seal → permit → transport ──────────

def test_observed_oversize_is_not_a_refusal_when_enforcement_off(ledger_home):
    payload = _oversized()
    request = gw.DirectProviderRequest(
        trace_id="observe", context={}, metadata={}, max_output_tokens=0)
    permit = seal_provider_invocation(
        request=request, provider_id="synthetic:audit",
        model_id="synthetic-audit", operation="chat", payload=payload)
    wire = permit.consume()  # the transport hand-off: payload leaves intact
    receipt = request.metadata["context_delivery_ledger"]
    assert wire == payload
    assert receipt["over_envelope"] is True       # the observation stands
    assert receipt["refused"] is False            # …but no refusal happened
    assert receipt["refusal_reason"] == ""
    assert receipt["enforcement_enabled"] is False


def test_enforced_oversize_refuses_and_receipts_the_actual_refusal(
        ledger_home, monkeypatch):
    monkeypatch.setenv(cdl.ENFORCE_FLAG, "1")
    request = gw.DirectProviderRequest(
        trace_id="enforce", context={}, metadata={}, max_output_tokens=0)
    with pytest.raises(cdl.ProviderPayloadEnvelopeExceededError):
        seal_provider_invocation(
            request=request, provider_id="synthetic:audit",
            model_id="synthetic-audit", operation="chat",
            payload=_oversized())
    receipt = request.metadata["context_delivery_ledger"]
    assert receipt["over_envelope"] is True
    assert receipt["refused"] is True             # this refusal really raised
    assert receipt["refusal_reason"] == "payload_exceeds_window_envelope"
    assert receipt["enforcement_enabled"] is True


def test_enforced_fitting_payload_seals_and_consumes(ledger_home, monkeypatch):
    monkeypatch.setenv(cdl.ENFORCE_FLAG, "1")
    permit = _seal(_fitting(), trace="fit")
    assert permit.consume() == _fitting()


def test_missing_window_identity_is_receipt_only_under_both_flags(
        ledger_home, monkeypatch):
    payload = {"model": "synthetic-audit",
               "messages": [{"role": "user", "content": "x" * 9000}]}
    for flag in (None, "1"):
        monkeypatch.delenv(cdl.ENFORCE_FLAG, raising=False)
        if flag:
            monkeypatch.setenv(cdl.ENFORCE_FLAG, flag)
        permit = _seal(copy.deepcopy(payload), trace=f"nowin-{flag}")
        assert permit.consume() == payload  # no envelope resolved: never refused


# ── 2. lane separation ─────────────────────────────────────────────────────

def test_measurement_failure_with_enforcement_on_fails_the_seal(
        ledger_home, monkeypatch):
    monkeypatch.setenv(cdl.ENFORCE_FLAG, "1")
    with patch.object(cdl, "measure_provider_payload",
                      side_effect=RuntimeError("synthetic measurement down")):
        with pytest.raises(cdl.ProviderPayloadMeasurementFailedError):
            _seal(_fitting(), trace="measfail-on")


def test_measurement_failure_classifies_fail_closed_not_as_budget(
        ledger_home):
    # Router classification preserved: the router's fail-closed local-defect
    # branch (ProviderInvocationValidationError) handles it, NOT the
    # prompt-budget/candidate-retry branch.
    assert issubclass(cdl.ProviderPayloadMeasurementFailedError,
                      ProviderInvocationValidationError)
    from core.prompt_budget import PromptBudgetExceededError
    assert not issubclass(cdl.ProviderPayloadMeasurementFailedError,
                          PromptBudgetExceededError)


def test_measurement_failure_with_enforcement_off_still_serves(
        ledger_home):
    with patch.object(cdl, "measure_provider_payload",
                      side_effect=RuntimeError("synthetic measurement down")):
        permit = _seal(_fitting(), trace="measfail-off")
        assert permit.consume() == _fitting()


def test_non_sql_receipt_failure_never_breaks_the_seal(ledger_home):
    with patch.object(cdl, "record_delivery_receipt",
                      side_effect=RuntimeError("synthetic non-SQL store hit")):
        permit = _seal(_fitting(), trace="receiptfail-off")
        assert permit.consume() == _fitting()


def test_non_sql_receipt_failure_cannot_disable_enforcement(
        ledger_home, monkeypatch):
    monkeypatch.setenv(cdl.ENFORCE_FLAG, "1")
    with patch.object(cdl, "record_delivery_receipt",
                      side_effect=RuntimeError("synthetic non-SQL store hit")):
        with pytest.raises(cdl.ProviderPayloadEnvelopeExceededError):
            _seal(_oversized(), trace="receiptfail-on")


def test_ledger_module_unavailable_fails_closed_only_under_explicit_flag(
        ledger_home, monkeypatch, tmp_path):
    import builtins
    real_import = builtins.__import__

    def broken_import(name, *args, **kwargs):
        if name == "core.context_delivery_ledger":
            raise ImportError("synthetic ledger module missing")
        return real_import(name, *args, **kwargs)

    with patch.object(builtins, "__import__", side_effect=broken_import):
        permit = _seal(_fitting(), trace="modfail-off")
        assert permit.consume() == _fitting()
    monkeypatch.setenv(cdl.ENFORCE_FLAG, "1")
    with patch.object(builtins, "__import__", side_effect=broken_import):
        with pytest.raises(ProviderInvocationValidationError) as raised:
            _seal(_fitting(), trace="modfail-on")
    assert "prompt_enforcement_unavailable" in str(raised.value)


# ── 3. provider-bound tool-call field accounting ───────────────────────────

def _tool_call_message(arguments):
    return {"role": "assistant", "content": "",
            "tool_calls": [{"id": "call-a", "type": "function",
                            "function": {"name": "save",
                                         "arguments": arguments}}]}


def test_tool_call_arguments_move_the_token_estimate():
    small = estimate_message_tokens([_tool_call_message("{}")])
    large = estimate_message_tokens([
        _tool_call_message(json.dumps({"value": "abcdef " * 1800}))])
    assert large > small + 2000


def test_structured_dict_arguments_are_counted_like_string_arguments():
    dict_form = estimate_message_tokens([
        _tool_call_message({"blob": "abcdef " * 400})])
    string_form = estimate_message_tokens([
        _tool_call_message(json.dumps({"blob": "abcdef " * 400}))])
    assert abs(dict_form - string_form) <= 4  # same payload, same estimate


def test_tool_role_name_and_call_id_are_counted():
    base = estimate_message_tokens([{"role": "tool", "content": "ok"}])
    named = estimate_message_tokens([
        {"role": "tool", "content": "ok", "name": "save",
         "tool_call_id": "call-a-very-long-identifier-0123456789"}])
    assert named > base


def test_ordinary_and_empty_messages_estimate_unchanged():
    empty = estimate_message_tokens([{"role": "user", "content": ""}])
    assert empty == 10  # overhead only: no fields, no tokens
    ordinary = estimate_message_tokens([{"role": "user", "content": "hi"}])
    assert ordinary == empty + 1  # sub-token content ceils to exactly one


def test_large_tool_arguments_flip_the_envelope_verdict():
    payload = {"model": "m",
               "messages": [_tool_call_message(
                   json.dumps({"value": "abcdef " * 1800}))],
               "options": {"num_ctx": 128}}
    measurement = cdl.measure_provider_payload(payload)
    assert measurement["payload_bytes"] > 10000
    assert measurement["over_envelope"] is True


def test_top_level_schemas_and_format_still_counted_alongside_messages():
    payload = {
        "model": "m",
        "messages": [_tool_call_message(json.dumps({"value": "x" * 200}))],
        "tools": [{"type": "function", "function": {
            "name": "t", "description": "d " * 400}}],
        "response_format": {"type": "json_object"},
        "options": {"num_ctx": 512},
    }
    measurement = cdl.measure_provider_payload(payload)
    assert measurement["estimated_tool_schema_tokens"] > 100
    assert measurement["estimated_format_schema_tokens"] > 0
    # ~215 chars of argument text ≈ 75 est. tokens; content alone would be 10
    assert measurement["estimated_messages_tokens"] > 50


def test_maybe_refuse_and_receipt_share_one_flag_reading(ledger_home,
                                                         monkeypatch):
    measurement = cdl.measure_provider_payload(_oversized())
    monkeypatch.delenv(cdl.ENFORCE_FLAG, raising=False)
    # enforcement decision passed explicitly beats the env: off
    cdl.maybe_refuse(measurement, enforce=False)  # no raise despite over
    monkeypatch.setenv(cdl.ENFORCE_FLAG, "1")
    cdl.maybe_refuse(measurement, enforce=False)  # explicit off still no raise
    with pytest.raises(cdl.ProviderPayloadEnvelopeExceededError):
        cdl.maybe_refuse(measurement, enforce=True)


# ── 4. router classification (existing branches, unchanged) ────────────────

def test_measurement_failure_routes_to_the_fail_closed_branch(
        ledger_home):
    """The new error rides the router's existing local-defect branch:
    fail-closed result, no provider-health recording, no circuit opening,
    and never the prompt-budget/candidate-retry classification."""
    from types import SimpleNamespace
    from unittest import mock

    from adapters.base_adapter import ModelRequest
    from core.memory_first_router import MemoryFirstRouter
    from core.model_health import (
        circuit_is_open,
        get_provider_health,
        reset_provider_health,
    )

    adapter = mock.Mock()
    adapter.health_check.return_value = {"ok": True}
    adapter.supports_streaming.return_value = False
    adapter.run_text_task.side_effect = (
        cdl.ProviderPayloadMeasurementFailedError(
            "prompt_enforcement_unavailable: synthetic"))
    registry = mock.Mock()
    registry.build_adapter.return_value = adapter
    manifest = SimpleNamespace(
        provider_id="ollama-local:qwen2.5:7b",
        model_name="qwen2.5:7b",
        metadata={"runtime_family": "ollama", "cost_class": "free_local"},
        runtime_config={"context_window": 4096},
        adapter_type="openai_compatible",
    )
    router = MemoryFirstRouter(registry=registry)
    reset_provider_health(manifest.provider_id)

    with mock.patch("core.memory_first_router.record_provider_failure") \
            as failure_mock:
        _adapter, response, error = router._invoke_manifest(
            manifest=manifest,
            request=ModelRequest(
                task_kind="normalization_assist",
                prompt="CURRENT USER",
                system_prompt="SYSTEM MUST SURVIVE",
                max_output_tokens=64,
            ),
            output_mode="text",
            task=SimpleNamespace(task_id="task-1"),
            source_context=None,
        )

    failure_mock.assert_not_called()
    assert response is None
    assert error is not None
    assert error.startswith("context_manifest_invalid:")
    assert get_provider_health(manifest.provider_id).consecutive_failures == 0
    assert circuit_is_open(manifest.provider_id) is False
