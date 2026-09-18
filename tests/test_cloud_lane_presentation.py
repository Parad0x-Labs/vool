"""C19 answer presentation on the cloud-broker synthesis lane.

Census 2026-09-06 (amendment: check existing implementation before adding to it): the automatic
selector (``core.presentation_selection``), the explicit contract (``core.response_constraints``)
and the turn-ledger record all EXISTED and were wired into the adapter lane
(``MemoryFirstRouter._invoke_manifest``) only. The broker lane that writes every grounded
free-cloud synthesis returned its text with no selection recorded and no explicit contract
enforced -- the VW comparison's presentation was never selected on the lane that produced it.
The lane now runs the same two authorities through the same helpers; no second selector or
formatter exists.
"""
from __future__ import annotations

from types import SimpleNamespace

from adapters.base_adapter import ModelRequest
from core.cloud_broker import CloudBrokerResult
from core.cloud_privacy_policy import CloudPrivacyGrant
from core.cloud_provider_contract import CloudModelResponse, CloudTaskRequirements, PricingState, PrivacyClass
from core.memory_first_router import MemoryFirstRouter
from core.response_constraints import constraint_safe_fallback
from core.turn_ir import ResponseConstraint
from tests.test_presentation_selection import PLAN_PROSE

MARKDOWN_TABLE = (
    "| Model | Launched | Class |\n"
    "|---|---|---|\n"
    "| Passat | 1973 | midsize |\n"
    "| Golf | 1974 | compact |"
)


class _Broker:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list = []

    def execute(self, request, **kwargs):
        self.calls.append((request, kwargs))
        return CloudBrokerResult(
            True,
            CloudModelResponse(self.text, {"cost": 0.0}, finish_reason="stop"),
            provider_id="openrouter",
            model_id="nvidia/nemotron-3.5-lightning:free",
            model_call_id="call",
            attempts=1,
            pricing_state=PricingState.FREE.value,
        )


def _context() -> dict:
    return {
        # The seam's owner-local gate admits the cli surface without a trust marker (the served
        # door stamps one); the lane under test is the same either way.
        "surface": "cli",
        "session_id": "session",
        "turn_id": "turn",
        "cloud_task_requirements": CloudTaskRequirements(
            min_context_tokens=10,
            expected_output_tokens=0,
            required_capabilities=("text",),
            privacy_class=PrivacyClass.PUBLIC,
        ),
        "cloud_privacy_grant": CloudPrivacyGrant(),
    }


def _drive(monkeypatch, text: str, *, metadata: dict | None = None):
    broker = _Broker(text)
    router = MemoryFirstRouter(cloud_broker=broker)
    monkeypatch.setattr(
        "core.memory_first_router.cloud_escalation_policy.load_policy",
        lambda: SimpleNamespace(free_cloud_enabled=True, auto_free_model="auto"),
    )
    context = _context()
    request = ModelRequest(
        task_kind="reasoning",
        prompt="compare the passat and the golf",
        messages=[{"role": "user", "content": "compare the passat and the golf"}],
        output_mode="plain_text",
        max_output_tokens=520,
        metadata=dict(metadata or {}),
    )
    decision = router._try_free_cloud_boost(
        request=request,
        task=SimpleNamespace(task_id="task"),
        task_hash="hash",
        output_mode="plain_text",
        source_context=context,
    )
    return decision, context


def test_the_broker_lane_records_an_automatic_selection_for_the_answer_it_produced(monkeypatch) -> None:
    decision, context = _drive(monkeypatch, PLAN_PROSE)
    assert decision is not None
    # Provenance only: the bytes the lane ships are untouched by the election (L4).
    assert decision.output_text.strip() == PLAN_PROSE.strip()
    record = context["presentation_selection"]
    assert record["origin"] == "automatic"
    assert record["elected"] == "comparison_matrix"
    assert record["gap_detected"] is True


def test_an_explicit_table_request_stands_the_selection_down_and_is_enforced(monkeypatch) -> None:
    metadata = {"response_constraint": ResponseConstraint(presentation_format="table").to_dict()}
    decision, context = _drive(monkeypatch, MARKDOWN_TABLE, metadata=metadata)
    assert context["presentation_selection"]["disabled_by"] == "explicit_request"
    assert decision.output_text == MARKDOWN_TABLE
    result = decision.details["constraint_result"]
    assert result["compliant"] is True and result["lane"] == "cloud_broker"
    assert "fallback_applied" not in result


def test_a_cloud_answer_that_ignores_the_requested_table_lands_on_the_honest_table_fallback(monkeypatch) -> None:
    metadata = {"response_constraint": ResponseConstraint(presentation_format="table").to_dict()}
    decision, context = _drive(monkeypatch, "The Passat is larger than the Golf and costs more.", metadata=metadata)
    assert context["presentation_selection"]["disabled_by"] == "explicit_request"
    assert decision.output_text == constraint_safe_fallback(ResponseConstraint(presentation_format="table"))
    result = decision.details["constraint_result"]
    assert result["compliant"] is False and result["fallback_applied"] is True
    assert result["retry_attempted"] is False  # the bounded model repair stays a free-local privilege


def test_ordinary_prose_without_a_request_stays_prose(monkeypatch) -> None:
    decision, context = _drive(monkeypatch, "The Passat is larger than the Golf and costs more.")
    assert decision.output_text == "The Passat is larger than the Golf and costs more."
    record = context["presentation_selection"]
    assert record["elected"] == "prose"
    assert "constraint_result" not in decision.details


# ---- the broker lane files its provider call where the adapter lane files its own ---------------
#
# Live 2026-09-06 (private profile, real provider): Brave returned 4 sources, the prefetch bound
# them into the synthesis prompt (`evidence_bound_to_synthesis`), the broker call finished `stop`
# inside its budget -- and the publication gate refused at bound_to_synthesis, because the lane
# that wrote the answer had recorded no provider call, so the lifecycle held no synthesis call
# naming the bound evidence set. The lane now records entry and outcome through the ledger's
# one seam, exactly as `_invoke_manifest` does.


class _EmittingBroker(_Broker):
    def execute(self, request, **kwargs):
        sink = kwargs.get("event_sink")
        call_id = "model-call-broker-1"
        if callable(sink):
            sink("model.call_started", {"model_call_id": call_id, "provider_id": "openrouter", "model_id": "nvidia/nemotron-3.5-lightning:free", "pricing_state": "free"})
            sink("model.call_completed", {"model_call_id": call_id, "provider_id": "openrouter", "model_id": "nvidia/nemotron-3.5-lightning:free", "pricing_state": "free", "finish_reason": "stop"})
        return super().execute(request, **kwargs)


def test_the_broker_lane_records_its_call_on_the_turn_ledger_and_the_grounding_lifecycle(monkeypatch) -> None:
    from core.grounding_lifecycle import _record_for, register_required
    from core.turn_model_call_ledger import begin_turn, turn_call_accounting, turn_model_calls

    broker = _EmittingBroker("The Passat is the larger car; the Golf is the cheaper one.")
    router = MemoryFirstRouter(cloud_broker=broker)
    monkeypatch.setattr(
        "core.memory_first_router.cloud_escalation_policy.load_policy",
        lambda: SimpleNamespace(free_cloud_enabled=True, auto_free_model="auto"),
    )
    context = _context()
    context["model_call_role"] = "grounded_synthesis"
    begin_turn(context)
    register_required(context, request_text="how does VW passat compare to golf?", reason_codes=("current_information",))
    context["evidence_synthesis_binding"] = {"evidence_set_id": "evset-test-4e42", "outcome": "bound"}
    request = ModelRequest(
        task_kind="reasoning",
        prompt="how does VW passat compare to golf?",
        messages=[{"role": "user", "content": "how does VW passat compare to golf?"}],
        output_mode="plain_text",
        max_output_tokens=496,
    )
    decision = router._try_free_cloud_boost(
        request=request, task=SimpleNamespace(task_id="task"), task_hash="hash", output_mode="plain_text", source_context=context
    )
    assert decision is not None and decision.used_model
    # The turn ledger saw exactly one provider call, with the broker's identity, and it closed.
    assert turn_model_calls(context) == 1
    accounting = turn_call_accounting(context)
    assert accounting.get("pending_calls", 0) == 0 or accounting.get("pending") in (0, None)
    # The lifecycle holds the synthesis call, naming the evidence set the prompt carried.
    record = _record_for(context)
    assert record is not None
    assert [(call.model_call_id, call.evidence_set_id, call.call_role) for call in record.synthesis_calls] == [
        ("model-call-broker-1", "evset-test-4e42", "grounded_synthesis")
    ]
