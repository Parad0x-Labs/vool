"""The live-data plan lane is a retrieval lane, and must account for itself like one.

Reproduced live on 2026-08-13 against the converged candidate: a Local Only turn answered

    Ulaanbaatar: Clear, 12 C (today's high 17 C / low 9 C). Source: wttr.in ...

for two cities that had never been queried -- distinct, correct, current conditions, so a real
remote fetch beyond any doubt -- while its terminal trace reported `web_calls: 0` and zero receipts
of every family. Only `live_data_plan_*` events showed anything had been fetched.

`core.retrieval_observability` receipts adaptive research and the reasoning fallback, and
`core.fresh_data.fx` receipts direct FX. This lane was the one that reached the network with no
typed receipt at all -- which matters twice over, because the release harness treats a non-empty
receipt list as evidence of prohibited retrieval, so a silent lane can never trip it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.live_data_retrieval_receipts import (
    publish_live_data_retrieval_receipts,
    receipt_for_outcome,
)


def _outcome(operation: str, *, ok: bool = True, state: str = "succeeded", failure: str = "", **arguments):
    subtask = SimpleNamespace(operation=operation, arguments=dict(arguments))
    return SimpleNamespace(
        subtask=subtask, ok=ok, failure_reason=failure, state=SimpleNamespace(value=state)
    )


@pytest.mark.parametrize(
    ("operation", "arguments"),
    (
        ("weather_lookup", {"location": "ulaanbaatar"}),
        ("market_quote", {"asset_key": "bitcoin", "kind": "crypto"}),
        ("water_temperature", {"place": "baltic sea"}),
    ),
)
def test_every_remote_subtask_produces_a_typed_receipt(operation: str, arguments: dict) -> None:
    receipt = receipt_for_outcome(_outcome(operation, **arguments), plan_id="livedata-1")

    assert receipt is not None
    assert receipt["schema"] == "vool.web_retrieval_receipt.v1"
    assert receipt["status"] == "available"
    assert receipt["kind"].startswith("live_data_")


def test_a_subtask_refused_before_the_network_is_not_reported_as_retrieval() -> None:
    """The honest direction: a plan that never fetched must not claim a retrieval."""

    refused = _outcome(
        "weather_lookup",
        ok=False,
        state="failed",
        failure="rejected implausible location before fetch: 'summarize it'",
        location="summarize it",
    )

    assert receipt_for_outcome(refused, plan_id="livedata-2") is None


@pytest.mark.parametrize("state", ("skipped", "blocked", "pending", "planned"))
def test_a_subtask_that_never_ran_is_not_reported_as_retrieval(state: str) -> None:
    outcome = _outcome("weather_lookup", ok=False, state=state, location="oslo")

    assert receipt_for_outcome(outcome, plan_id="livedata-3") is None


def test_a_local_subtask_is_never_counted_as_a_web_call() -> None:
    assert receipt_for_outcome(_outcome("arithmetic"), plan_id="livedata-4") is None


def test_a_failed_fetch_is_reported_truthfully_rather_than_hidden() -> None:
    outcome = _outcome(
        "weather_lookup", ok=False, state="failed", failure="TimeoutError: timed out", location="nuuk"
    )
    receipt = receipt_for_outcome(outcome, plan_id="livedata-5")

    assert receipt is not None
    assert receipt["status"] == "failed"
    assert receipt["source_count"] == 0
    assert "TimeoutError" in receipt["failure_class"]


def test_receipts_reach_the_turn_so_the_terminal_trace_can_report_them() -> None:
    source_context: dict[str, object] = {}
    published = publish_live_data_retrieval_receipts(
        source_context,
        [_outcome("weather_lookup", location="tbilisi"), _outcome("market_quote", asset_key="bitcoin")],
        plan_id="livedata-6",
    )

    assert len(published) == 2
    assert len(source_context["web_retrieval_receipts"]) == 2
    # `web_calls` belongs to the remote-fetch scope, not to this layer; publishing a receipt must
    # not invent a second counter that could disagree with it.
    assert "web_calls" not in source_context


def test_a_plan_with_nothing_remote_records_nothing() -> None:
    source_context: dict[str, object] = {}

    assert publish_live_data_retrieval_receipts(source_context, [_outcome("arithmetic")], plan_id="p") == []
    assert "web_retrieval_receipts" not in source_context


def test_the_weather_fetch_reports_itself_to_the_remote_fetch_scope(monkeypatch) -> None:
    """The counter's own guarantee: every remote HTTP path reports itself before fetching.

    Without this, `web_calls == 0` cannot prove a negative for this lane -- which is exactly the
    guarantee `remote_fetch_scope_active` documents.

    This used to assert the guarantee by reading the source and comparing the offsets of
    "note_remote_fetch_attempt()" and "urlopen". That could pass while the behaviour was broken --
    and it did: six sibling fetch paths in the same module reported nothing at all, so the counter
    could read 0 while live data came back, and this test stayed green throughout because it only
    ever looked at one function's text. Measured now instead, through the scope that owns the
    count. See tests/test_every_outbound_fetch_reports_and_obeys_the_veto.py for the per-path family.
    """

    import json as _json
    import urllib.request

    from core.remote_fetch_policy import remote_fetch_attempt_count, remote_fetch_policy_scope
    from tools.web.web_research import structured_weather_lookup

    def _response_for(payload: bytes):
        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self, *args):
                return payload

        return _Response()

    # Empty payloads: the first provider yields nothing, so the SECOND provider's geocoder is
    # tried -- two remote attempts, EACH reported. Since 2026-08-28 the weather lookup has two
    # independent providers (wttr.in geocoded "Rome" to Lome, Togo live, and the correspondence
    # guard rightly refused it; open-meteo is the fallback), so "exactly one attempt" is only the
    # contract when the first provider answers.
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *a, **k: _response_for(b"{}")
    )
    with remote_fetch_policy_scope({"allow_remote_fetch": True}):
        before = remote_fetch_attempt_count()
        structured_weather_lookup("Oslo", timeout_s=5.0)
        after = remote_fetch_attempt_count()
    assert after == before + 2, (
        "an empty first provider must fall through to the second, and BOTH attempts must report"
    )

    # A healthy first provider answers alone: exactly one reported attempt, no fallback fetch.
    wttr_payload = _json.dumps(
        {
            "current_condition": [{"temp_C": "7", "FeelsLikeC": "5", "humidity": "60",
                                   "windspeedKmph": "10", "weatherDesc": [{"value": "Cloudy"}],
                                   "localObsDateTime": "2026-08-28 09:00 AM"}],
            "nearest_area": [{"areaName": [{"value": "Oslo"}], "country": [{"value": "Norway"}],
                              "region": [{"value": "Oslo"}], "latitude": "59.9", "longitude": "10.7"}],
            "weather": [{"maxtempC": "9", "mintempC": "3"}],
        }
    ).encode()
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *a, **k: _response_for(wttr_payload)
    )
    with remote_fetch_policy_scope({"allow_remote_fetch": True}):
        before = remote_fetch_attempt_count()
        result = structured_weather_lookup("Oslo", timeout_s=5.0)
        after = remote_fetch_attempt_count()
    assert result is not None and result.source_label == "wttr.in"
    assert after == before + 1, (
        "a healthy first provider answers alone: exactly one reported attempt"
    )


def test_the_receipted_operations_are_derived_from_declared_capability_metadata() -> None:
    """An invented operation name in the registry fails SILENTLY -- it just never matches.

    That is exactly what happened: the first version of this module listed `market_lookup`,
    `price_lookup` and `quote_lookup`, none of which exist. Market retrieval produced no receipt
    at all, and these tests passed anyway because they used the same invented names. Then the
    opposite drift: `water_temperature` landed in the runner's dispatch and NOT in the manual
    receipt set, so real remote work went un-receipted while this test's hard-coded equality
    `{"weather_lookup", "market_quote"} == _REMOTE_OPERATIONS` stayed green — the assertion was
    the drift, codified.

    The set is now DERIVED from the typed capability metadata an operation must declare anyway
    (`OperationCapability.effect_class == "remote_fetch"`), the same law `core.conductor.evidence`
    already applies to observation effects. This test binds the derivation to the runner's real
    dispatch so the two still cannot drift apart; the reverse direction (a dispatched operation
    with no declaration) is pinned in tests/test_remote_operation_receipt_completeness.py.
    """

    import inspect

    from core.agent_runtime import live_data_runner
    from core.live_data_retrieval_receipts import remote_fetch_operations

    dispatch = inspect.getsource(live_data_runner._run_one)
    for operation in remote_fetch_operations():
        if operation in {"fx_quote", "place_search", "structured_research"}:
            # Conductor-side remote capabilities: they receipt through their own lanes
            # (`core.fresh_data.fx`, `core.retrieval_observability`), never through this
            # plan-lane publisher, so the runner's dispatch never names them.
            continue
        assert f'"{operation}"' in dispatch, f"{operation!r} is not an operation the runner dispatches"


# --- Activity must not call a real retrieval "no tool ran" -----------------------------------
#
# Reproduced in the served UI on 2026-08-13: the flagship weather turn rendered
#   tool | live_data_typed_plan
# on its own provenance line, its runtime attempt showed LIVE_DATA -> SUCCEEDED, and a real
# wttr.in fetch happened -- while the Activity Work log said "No tool ran — answered directly".
#
# Activity derives that claim from `tool_selected`/`tool_executed`, which the LLM tool-call loop
# emits. This lane emitted neither, so Activity was reading "no LLM tool-call object" as "no
# capability executed". The steps below come from the same receipts as the counter, so the three
# surfaces cannot disagree.


def _emitted(outcomes, plan_id="livedata-activity"):
    from core.live_data_retrieval_receipts import publish_live_data_retrieval_receipts

    captured: list[dict] = []

    def _capture(_ctx, *, event_type, message="", details=None):
        captured.append({"event_type": event_type, "message": message, **(details or {})})

    import core.live_data_retrieval_receipts as module

    original = module.emit_runtime_event
    module.emit_runtime_event = _capture
    try:
        publish_live_data_retrieval_receipts({}, outcomes, plan_id=plan_id)
    finally:
        module.emit_runtime_event = original
    return captured


def test_a_real_retrieval_emits_the_events_activity_reads_for_tool_use() -> None:
    events = _emitted([_outcome("weather_lookup", location="vilnius")])
    kinds = [e["event_type"] for e in events]

    assert "tool_selected" in kinds
    assert "tool_executed" in kinds
    # This is the exact predicate the Activity panel uses (`ledgerRanNoTool`).
    assert any(k in {"tool_selected", "tool_executed"} for k in kinds)


def test_a_failed_retrieval_is_recorded_as_failed_not_as_success() -> None:
    events = _emitted(
        [_outcome("weather_lookup", ok=False, state="failed", failure="TimeoutError: t", location="nuuk")]
    )
    kinds = [e["event_type"] for e in events]

    assert "tool_failed" in kinds
    assert "tool_executed" not in kinds


def test_a_turn_that_fetched_nothing_emits_no_tool_activity() -> None:
    """The negative control: a pure answer must never claim tool use."""

    assert _emitted([_outcome("arithmetic")]) == []


def test_a_subtask_refused_before_the_network_emits_no_tool_activity() -> None:
    """Refused before the fetch means nothing ran, and Activity must not invent a step.

    Since P1 the refusal itself leaves a typed lifecycle record
    (`live_data_operation_outcome`, lifecycle `refused`) — a refusal is a fact worth
    keeping. What must NEVER appear is tool activity: no tool ran, so Activity may
    not show one."""

    refused = _outcome(
        "weather_lookup",
        ok=False,
        state="failed",
        failure="rejected implausible location before fetch: 'summarize it'",
        location="summarize it",
    )

    events = _emitted([refused])
    kinds = [e["event_type"] for e in events]
    assert not any(k in {"tool_selected", "tool_executed", "tool_failed"} for k in kinds)
    assert "live_data_operation_outcome" in kinds
    refused_records = [e for e in events if e["event_type"] == "live_data_operation_outcome"]
    assert all(e.get("lifecycle") == "refused" for e in refused_records)


def test_the_activity_step_carries_no_copy_of_the_user_subject() -> None:
    """An Activity row is provenance; the receipt already hashes the subject."""

    events = _emitted([_outcome("weather_lookup", location="Ulaanbaatar")])

    assert events, "expected activity steps"
    assert not any("ulaanbaatar" in str(e).casefold() for e in events)


def test_every_executed_subtask_gets_its_own_step() -> None:
    events = _emitted(
        [_outcome("weather_lookup", location="lima"), _outcome("market_quote", asset_key="bitcoin")]
    )

    assert sum(1 for e in events if e["event_type"] == "tool_selected") == 2
    assert sum(1 for e in events if e["event_type"] == "tool_executed") == 2
