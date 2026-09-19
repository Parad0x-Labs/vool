"""P1 — every remote live-data operation must produce effect truth.

The confirmed gap (measured at base a2308a26): `water_temperature` performs real
remote work (open-meteo geocoding + the marine API, through the same policy-aware
opener the weather lane uses) but the live-data lane's remote-operation receipt
set was a MANUAL frozenset spelling `{"weather_lookup", "market_quote"}` — so a
water subtask that really fetched left no retrieval receipt, no Activity tool
step, and no operation-level lifecycle record. Manual lists drift exactly this
way every time a new tool lands; the set had already drifted once before (its
first version listed three operation names that never existed).

What this file pins:

* the receipt set is DERIVED from typed capability metadata
  (`OperationCapability.effect_class == "remote_fetch"`), never hand-listed;
* every remote operation outcome — succeeded, failed, refused before the
  network, cancelled — leaves a typed lifecycle receipt naming the request,
  turn, attempt, provider and operation it belongs to;
* a failed or refused operation never mints successful evidence;
* the registry completeness invariant: an operation the runner dispatches with
  no declared effect class is REPORTED, so adding a remote capability without
  receipt coverage makes CI red here;
* a socket sentinel over hermetic transports: an operation that DECLARES
  remote_fetch really reaches the network door when it runs.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from core.live_data_retrieval_receipts import (
    operation_receipt_for_outcome,
    publish_live_data_retrieval_receipts,
    receipt_for_outcome,
    remote_fetch_operations,
    unclassified_dispatched_operations,
)


def _outcome(
    operation: str,
    *,
    ok: bool = True,
    state: str = "succeeded",
    failure: str = "",
    result: dict | None = None,
    **arguments,
):
    subtask = SimpleNamespace(
        subtask_id=f"probe-plan:{operation}", operation=operation, arguments=dict(arguments)
    )
    return SimpleNamespace(
        subtask=subtask,
        ok=ok,
        failure_reason=failure,
        state=SimpleNamespace(value=state),
        result=result,
        started_at_iso="2026-09-01T10:00:00+00:00",
        completed_at_iso="2026-09-01T10:00:02+00:00",
    )


# --- the confirmed gap: water_temperature is remote work and must be receipted ---------------


def test_water_temperature_is_derived_as_a_remote_fetch_operation() -> None:
    declared = remote_fetch_operations()

    assert {"weather_lookup", "market_quote", "water_temperature"} <= declared


def test_the_derivation_reads_the_registry_metadata_not_a_name_list(monkeypatch) -> None:
    """A capability registered tomorrow with `effect_class=remote_fetch` is
    covered with no edit to the receipt module — the property whose absence
    let water_temperature drift."""
    from core.conductor.capabilities import (
        REMOTE_FETCH_EFFECT_CLASS,
        OperationCapability,
        OperationEffect,
    )
    from core.conductor.registry import (
        OperationSpec,
        register_operation,
        unregister_operation,
    )
    from core.turn_ir import ClauseKind

    name = "synthetic_remote_probe"
    unregister_operation(name)
    register_operation(
        OperationSpec(
            name=name,
            description="synthetic remote capability registered by the derivation test",
            expand_arguments=lambda _clause: [],
            run=lambda _node, _ctx: {},
            render=lambda _node, _result: "",
            capability=OperationCapability(
                effect=OperationEffect.LIVE_OBSERVATION,
                domain="synthetic",
                accepted_kinds=frozenset({ClauseKind.KNOW}),
                effect_class=REMOTE_FETCH_EFFECT_CLASS,
            ),
        )
    )
    try:
        assert name in remote_fetch_operations()
        # ...and the receipt layer follows the same derivation, not a second list.
        outcome = _outcome(name, location="oslo")
        assert receipt_for_outcome(outcome, plan_id="p-derive") is not None
    finally:
        unregister_operation(name)
    assert name not in remote_fetch_operations()


def test_a_water_temperature_retrieval_produces_the_same_typed_receipt_as_weather() -> None:
    receipt = receipt_for_outcome(
        _outcome("water_temperature", place="baltic sea"), plan_id="water-1"
    )

    assert receipt is not None, "water_temperature performs remote work and must be receipted"
    assert receipt["schema"] == "vool.web_retrieval_receipt.v1"
    assert receipt["kind"] == "live_data_water_temperature"
    assert receipt["action"] == "water_temperature"
    assert receipt["status"] == "available"


def test_a_water_temperature_fetch_failure_is_reported_not_hidden() -> None:
    receipt = receipt_for_outcome(
        _outcome(
            "water_temperature",
            ok=False,
            state="failed",
            failure="TimeoutError: timed out",
            place="baltic sea",
        ),
        plan_id="water-2",
    )

    assert receipt is not None
    assert receipt["status"] == "failed"
    assert receipt["source_count"] == 0
    assert "TimeoutError" in receipt["failure_class"]


# --- the typed operation lifecycle: started/succeeded/failed/refused/cancelled ----------------


def _water_success_outcome():
    return _outcome(
        "water_temperature",
        place="baltic sea",
        result={
            "label": "Jurmala, Latvia (Baltic Sea)",
            "temperature_c": 17.3,
            "temperature_f": 63.1,
            "observed": "2026-09-01T09:00",
            "source": "open-meteo.com (marine)",
        },
    )


def test_a_succeeded_water_operation_emits_its_lifecycle_with_full_identity() -> None:
    receipt = operation_receipt_for_outcome(
        _water_success_outcome(),
        plan_id="plan-1",
        attempt_id="attempt-1",
        turn_id="turn-1",
        request_id="req-1",
    )

    assert receipt is not None
    assert receipt["schema"] == "vool.live_data_operation_receipt.v1"
    assert receipt["operation"] == "water_temperature"
    assert receipt["effect_class"] == "remote_fetch"
    assert receipt["lifecycle"] == "succeeded"
    assert receipt["plan_id"] == "plan-1"
    assert receipt["attempt_id"] == "attempt-1"
    assert receipt["turn_id"] == "turn-1"
    assert receipt["request_id"] == "req-1"
    assert receipt["subtask_id"].startswith("probe-plan:")
    # The provider actually observed, never guessed: the fetch's own source label.
    assert receipt["provider"] == "open-meteo.com (marine)"
    assert receipt["ok"] is True
    assert receipt["started_at"] == "2026-09-01T10:00:00+00:00"
    assert receipt["completed_at"] == "2026-09-01T10:00:02+00:00"


def test_a_failed_water_operation_emits_failed_never_succeeded() -> None:
    receipt = operation_receipt_for_outcome(
        _outcome(
            "water_temperature",
            ok=False,
            state="failed",
            failure="TimeoutError: timed out",
            place="baltic sea",
        ),
        plan_id="plan-2",
        attempt_id="attempt-2",
        turn_id="turn-2",
        request_id="req-2",
    )

    assert receipt is not None
    assert receipt["lifecycle"] == "failed"
    assert receipt["ok"] is False
    assert receipt["provider"] == ""
    assert "TimeoutError" in receipt["reason"]


def test_a_refused_water_operation_is_a_first_class_refusal() -> None:
    """Refused before the network (approval denial): no fetch happened, and the
    refusal itself is recorded — never silently absent, and never dressed as a
    retrieval."""
    receipt = operation_receipt_for_outcome(
        _outcome(
            "water_temperature",
            ok=False,
            state="waiting_approval",
            failure="requires approval",
            place="baltic sea",
        ),
        plan_id="plan-3",
        attempt_id="attempt-3",
        turn_id="turn-3",
        request_id="req-3",
    )

    assert receipt is not None
    assert receipt["lifecycle"] == "refused"
    assert receipt["ok"] is False


def test_a_cancelled_water_operation_emits_cancelled() -> None:
    receipt = operation_receipt_for_outcome(
        _outcome(
            "water_temperature",
            ok=False,
            state="cancelled",
            failure="cancelled",
            place="baltic sea",
        ),
        plan_id="plan-4",
        attempt_id="attempt-4",
    )

    assert receipt is not None
    assert receipt["lifecycle"] == "cancelled"
    assert receipt["ok"] is False


def test_an_in_flight_water_operation_reports_started_not_a_terminal() -> None:
    receipt = operation_receipt_for_outcome(
        _outcome("water_temperature", ok=False, state="running", place="baltic sea"),
        plan_id="plan-5",
        attempt_id="attempt-5",
    )

    assert receipt is not None
    assert receipt["lifecycle"] == "started"
    assert receipt["ok"] is False


@pytest.mark.parametrize("operation", ("weather_lookup", "market_quote"))
def test_weather_and_market_keep_the_same_lifecycle_truth(operation: str) -> None:
    arguments = (
        {"location": "oslo"} if operation == "weather_lookup" else {"asset_key": "bitcoin"}
    )
    receipt = operation_receipt_for_outcome(
        _outcome(operation, result={"source": "wttr.in"} if operation == "weather_lookup" else {"source": "CoinGecko"}, **arguments),
        plan_id="ctrl-1",
        attempt_id="attempt-c",
        turn_id="turn-c",
        request_id="req-c",
    )

    assert receipt is not None
    assert receipt["operation"] == operation
    assert receipt["effect_class"] == "remote_fetch"
    assert receipt["lifecycle"] == "succeeded"
    assert receipt["turn_id"] == "turn-c"
    assert receipt["request_id"] == "req-c"
    assert receipt["attempt_id"] == "attempt-c"


def test_a_never_dispatched_remote_subtask_emits_no_operation_receipt() -> None:
    """planned/pending never left the machine; the lifecycle channel reports
    work that was dispatched, not work that was imagined."""
    for state in ("planned", "pending", "approved", "skipped", "blocked"):
        assert (
            operation_receipt_for_outcome(
                _outcome("water_temperature", ok=False, state=state, place="baltic sea"),
                plan_id="plan-6",
            )
            is None
        )


def test_a_planning_only_outcome_is_not_remote_work() -> None:
    assert (
        operation_receipt_for_outcome(
            _outcome("unsupported_market_entity", requested_text="copper"), plan_id="plan-7"
        )
        is None
    )
    assert (
        operation_receipt_for_outcome(_outcome("arithmetic"), plan_id="plan-7")
        is None
    )


# --- failed/refused never mint successful evidence --------------------------------------------


def test_failed_and_refused_operation_receipts_are_not_usable_evidence() -> None:
    from core.observation_evidence import records_a_usable_observation

    refused = operation_receipt_for_outcome(
        _outcome(
            "water_temperature",
            ok=False,
            state="waiting_approval",
            failure="requires approval",
            place="baltic sea",
        ),
        plan_id="ev-1",
    )
    failed = operation_receipt_for_outcome(
        _outcome(
            "water_temperature",
            ok=False,
            state="failed",
            failure="TimeoutError: timed out",
            place="baltic sea",
        ),
        plan_id="ev-2",
    )
    succeeded = operation_receipt_for_outcome(_water_success_outcome(), plan_id="ev-3")

    assert records_a_usable_observation(refused) is False
    assert records_a_usable_observation(failed) is False
    assert records_a_usable_observation(succeeded) is True


# --- publishing: both receipt families reach the turn ------------------------------------------


def test_publish_records_water_retrieval_and_lifecycle_receipts_on_the_turn() -> None:
    source_context: dict[str, object] = {}
    published = publish_live_data_retrieval_receipts(
        source_context,
        [
            _water_success_outcome(),
            _outcome(
                "water_temperature",
                ok=False,
                state="waiting_approval",
                failure="requires approval",
                place="red sea",
            ),
        ],
        plan_id="pub-1",
        attempt_id="attempt-pub",
    )

    # Preserved family semantics: the executed fetch is an available retrieval; the
    # refused subtask keeps the shape weather/market have always produced for it
    # (a failed retrieval receipt — measured at base a2308a26).
    assert len(published) == 2
    retrievals = source_context["web_retrieval_receipts"]
    assert [r["status"] for r in retrievals] == ["available", "failed"]
    assert retrievals[1]["failure_class"] == "requires approval"
    assert all(r["kind"] == "live_data_water_temperature" for r in retrievals)
    # The typed lifecycle layer names what the retrieval family cannot: the second
    # operation was REFUSED, and both left their truth on the turn.
    lifecycle = source_context["live_data_operation_receipts"]
    assert [r["lifecycle"] for r in lifecycle] == ["succeeded", "refused"]
    assert all(r["attempt_id"] == "attempt-pub" for r in lifecycle)


def test_publish_resolves_turn_and_request_identity_from_the_context() -> None:
    from core.turn_contract import TURN_REQUEST_KEY

    source_context: dict[str, object] = {
        TURN_REQUEST_KEY: SimpleNamespace(turn_id="turn-9", request_id="req-9")
    }
    publish_live_data_retrieval_receipts(
        source_context,
        [_water_success_outcome()],
        plan_id="pub-2",
        attempt_id="attempt-2",
    )

    receipt = source_context["live_data_operation_receipts"][0]
    assert receipt["turn_id"] == "turn-9"
    assert receipt["request_id"] == "req-9"


def test_a_turn_with_no_remote_work_records_no_lifecycle() -> None:
    source_context: dict[str, object] = {}

    publish_live_data_retrieval_receipts(
        source_context, [_outcome("arithmetic")], plan_id="pub-3"
    )

    assert "live_data_operation_receipts" not in source_context
    assert "web_retrieval_receipts" not in source_context


# --- the completeness invariant: no dispatched operation may escape classification --------------


def _runner_dispatch_source() -> str:
    from core.agent_runtime import live_data_runner

    return inspect.getsource(live_data_runner._run_one)


def test_every_operation_the_runner_dispatches_declares_an_effect_class() -> None:
    gaps = unclassified_dispatched_operations(_runner_dispatch_source())

    assert not gaps, (
        "operations dispatched by the live-data runner with no declared effect_class "
        "(no capability registration, or a registration without metadata): "
        f"{gaps} — a remote operation without receipt coverage"
    )


def test_a_synthetic_remote_capability_without_metadata_is_reported() -> None:
    """The drift that actually happened, replayed: a new dispatch branch whose
    operation never declared metadata. The invariant must go red on it."""
    sabotaged = _runner_dispatch_source() + (
        '    if subtask.operation == "synthetic_tide_lookup":\n'
        "        return _run_tide_subtask(subtask, timeout_s=timeout_s)\n"
    )

    gaps = unclassified_dispatched_operations(sabotaged)

    assert gaps == ["synthetic_tide_lookup"]


def test_the_declared_remote_set_is_exactly_the_registry_declaration() -> None:
    import core.conductor.operations
    from core.conductor.capabilities import REMOTE_FETCH_EFFECT_CLASS
    from core.conductor.registry import known_operations

    declared = {
        spec.name
        for spec in known_operations()
        if spec.capability is not None and spec.capability.effect_class == REMOTE_FETCH_EFFECT_CLASS
    }

    assert declared == set(remote_fetch_operations())


# --- the socket sentinel: declared remote operations really reach the network ------------------


class _HermeticResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, *args):
        return self._payload


def test_declared_remote_operations_really_reach_the_network(monkeypatch) -> None:
    """Hermetic transports + a socket sentinel: each operation the runner
    dispatches under `effect_class=remote_fetch` must, when it really runs,
    drive at least one call through the remote-fetch door. This is the ground
    truth that keeps the metadata honest — a local operation mislabelled
    remote_fetch is caught by the count staying zero, and vice versa."""
    import json
    import urllib.request

    from core.agent_runtime.live_data_plan import LiveDataSubtask
    from core.agent_runtime.live_data_runner import _run_one
    from core.remote_fetch_policy import (
        remote_fetch_attempt_count,
        remote_fetch_policy_scope,
    )

    wttr_payload = json.dumps(
        {
            "current_condition": [
                {
                    "temp_C": "7",
                    "FeelsLikeC": "5",
                    "humidity": "60",
                    "windspeedKmph": "10",
                    "weatherDesc": [{"value": "Cloudy"}],
                    "localObsDateTime": "2026-09-01 09:00 AM",
                }
            ],
            "nearest_area": [
                {
                    "areaName": [{"value": "Oslo"}],
                    "country": [{"value": "Norway"}],
                    "region": [{"value": "Oslo"}],
                    "latitude": "59.9",
                    "longitude": "10.7",
                }
            ],
            "weather": [{"maxtempC": "9", "mintempC": "3"}],
        }
    ).encode()
    crypto_payload = json.dumps(
        {"bitcoin": {"usd": 64000.0, "usd_24h_change": 1.2, "last_updated_at": 1750000000}}
    ).encode()
    marine_payload = json.dumps(
        {
            "current": {"sea_surface_temperature": 17.3, "time": "2026-09-01T09:00"},
            "hourly": {"time": [], "sea_surface_temperature": []},
        }
    ).encode()

    def _serve(request, *args, **kwargs):
        url = str(getattr(request, "full_url", "") or "")
        if "wttr.in" in url or ("open-meteo.com" in url and "marine" not in url):
            return _HermeticResponse(wttr_payload)
        if "coingecko" in url:
            return _HermeticResponse(crypto_payload)
        return _HermeticResponse(marine_payload)

    monkeypatch.setattr(urllib.request, "urlopen", _serve)

    dispatched = ("weather_lookup", "market_quote", "water_temperature")
    subtasks = {
        "weather_lookup": LiveDataSubtask(
            subtask_id="sentinel:weather",
            entity="Oslo",
            operation="weather_lookup",
            arguments={"location": "Oslo"},
            required_result_fields=("condition", "source", "observed_at"),
            tool="weather",
            tool_intent="web.research",
        ),
        "market_quote": LiveDataSubtask(
            subtask_id="sentinel:market",
            entity="Bitcoin",
            operation="market_quote",
            arguments={"asset_key": "bitcoin", "kind": "crypto"},
            required_result_fields=("price", "currency", "source", "retrieved_at"),
            tool="market_prices",
            tool_intent="web.research",
        ),
        "water_temperature": LiveDataSubtask(
            subtask_id="sentinel:water",
            entity="baltic sea",
            operation="water_temperature",
            arguments={"place": "baltic sea"},
            required_result_fields=("temperature_c", "label", "source"),
            tool="water",
            tool_intent="web.research",
        ),
    }

    for operation in dispatched:
        with remote_fetch_policy_scope({"allow_remote_fetch": True}):
            before = remote_fetch_attempt_count()
            outcome = _run_one(subtasks[operation], timeout_s=5.0)
            after = remote_fetch_attempt_count()
        assert outcome.state.value == "succeeded", (
            f"{operation} must succeed under the hermetic transport "
            f"(got {outcome.state.value}: {outcome.failure_reason})"
        )
        assert after > before, (
            f"{operation} declares effect_class=remote_fetch but drove no call through the "
            "remote-fetch door — the metadata and the behaviour disagree"
        )


def test_an_approval_refusal_is_refused_before_the_network(monkeypatch) -> None:
    """Integration refusal path: approval denies the subtask, nothing enters the
    pool, nothing is retrieved, and the published lifecycle says `refused`."""
    from core.agent_runtime.live_data_plan import LiveDataSubtask
    from core.agent_runtime.live_data_runner import run_live_data_plan

    subtask = LiveDataSubtask(
        subtask_id="refused:water",
        entity="baltic sea",
        operation="water_temperature",
        arguments={"place": "baltic sea"},
        required_result_fields=("temperature_c", "label", "source"),
        tool="water",
        tool_intent="web.research",
    )
    plan = SimpleNamespace(
        subtasks=(subtask,), plan_id="refused-plan", attempt_id="attempt-refused"
    )
    outcomes = run_live_data_plan(
        plan,
        approval_decisions={
            "refused:water": SimpleNamespace(allowed=False, reason="requires approval", effect="require_approval")
        },
    )

    assert outcomes[0].state.value == "waiting_approval"

    source_context: dict[str, object] = {}
    publish_live_data_retrieval_receipts(
        source_context, outcomes, plan_id="refused-plan", attempt_id="attempt-refused"
    )

    # Nothing was retrieved: the retrieval receipt (preserved family shape for a
    # refused subtask) reports failure with zero sources...
    retrievals = source_context["web_retrieval_receipts"]
    assert [r["status"] for r in retrievals] == ["failed"]
    assert retrievals[0]["source_count"] == 0
    # ...and the lifecycle layer states the refusal as a first-class fact.
    lifecycle = source_context["live_data_operation_receipts"]
    assert [r["lifecycle"] for r in lifecycle] == ["refused"]
    assert lifecycle[0]["operation"] == "water_temperature"
    assert lifecycle[0]["attempt_id"] == "attempt-refused"
