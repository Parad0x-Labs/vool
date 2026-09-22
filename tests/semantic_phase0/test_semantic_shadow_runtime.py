"""The SHADOW runtime is bounded, detached, stateless per turn, sanitized, and never waited on.
Deterministic stub transports; no model, no network."""
from __future__ import annotations

import contextvars
import json
import threading
import time

import pytest

from core.agent_runtime.semantic_shadow import ShadowRuntime
from core.semantic import reach as semantic_reach
from core.semantic.canonical_text import CanonicalText
from core.semantic.producers.lexical import lexical_request_graph
from core.semantic.resolver import ModelSemanticResolver
from core.semantic.shadow_observation import (
    FAILURE_BACKEND_ERROR,
    FAILURE_NONE,
    FAILURE_PARSE_ERROR,
    FAILURE_QUEUE_FULL,
    FAILURE_TIMEOUT,
    ShadowObservationV2,
    assert_text_free,
    clear_observations,
    recent_observations,
    record_observation,
)

OPS = ("market_quote", "weather_lookup")
TEXT = "what is the price of gold and the weather in Rome"
GOOD_REPLY = json.dumps({"requests": [
    {"key": "r1", "source": "the price of gold", "operation": "market_quote",
     "slots": [{"expected": "gold spot price", "operands": [{"role": "subject", "text": "gold", "entity": "gold"}]}]},
    {"key": "r2", "source": "the weather in Rome", "operation": "weather_lookup",
     "slots": [{"expected": "weather in Rome", "operands": [{"role": "subject", "text": "Rome", "entity": "Rome"}]}]},
], "constraints": [{"kind": "retrieval_required", "requests": ["r1", "r2"]}]})


@pytest.fixture(autouse=True)
def _clean_ring():
    clear_observations()
    yield
    clear_observations()


def _stub(reply=GOOD_REPLY, *, delay: float = 0.0, raise_error: bool = False, seen: list | None = None):
    def transport(system: str, user: str, json_schema):
        if seen is not None:
            seen.append((system, user, json_schema))
        if delay:
            time.sleep(delay)
        if raise_error:
            raise RuntimeError("provider down")
        return reply
    return transport


def _submit(runtime: ShadowRuntime, transport, *, turn_id="turn-1", session_id="", deadline_s=None, text=TEXT):
    canonical = CanonicalText.of(text)
    return runtime.submit(
        turn_id=turn_id, session_id=session_id, canonical=canonical, operations=OPS,
        heuristic_graph=lexical_request_graph(text, turn_id=turn_id),
        resolver=ModelSemanticResolver(), transport=transport, deadline_s=deadline_s,
    )


def test_submit_returns_immediately_and_the_result_arrives_on_the_ticket() -> None:
    runtime = ShadowRuntime(max_workers=1, max_queue=4)
    try:
        canonical = CanonicalText.of(TEXT)
        heuristic = lexical_request_graph(TEXT, turn_id="turn-1")
        resolver = ModelSemanticResolver()
        started = time.monotonic()
        ticket = runtime.submit(turn_id="turn-1", session_id="", canonical=canonical, operations=OPS,
                                heuristic_graph=heuristic, resolver=resolver, transport=_stub(delay=0.2))
        assert time.monotonic() - started < 0.1, "the turn must not wait on the shadow"
        assert ticket.wait(5.0)
        obs = ticket.observation
        assert obs is not None and obs.failure_type == FAILURE_NONE
        assert obs.differential is not None and "whole_turn_correct" in obs.differential
        assert obs.resolver_graph_digest and obs.heuristic_graph_digest
        assert obs.prompt_version and obs.schema_version and obs.catalog_version
        assert obs.dispatched is False and obs.latency_ms >= 150
        assert recent_observations()[-1].ticket_id == ticket.ticket_id
    finally:
        runtime.shutdown()


def test_the_pool_and_queue_are_bounded_and_overflow_is_recorded() -> None:
    runtime = ShadowRuntime(max_workers=2, max_queue=2)
    release = threading.Event()
    started = [threading.Event(), threading.Event()]

    def held_transport(index):
        def transport(system, user, json_schema):
            started[index].set()
            assert release.wait(10), "the test did not release the occupied workers"
            return GOOD_REPLY
        return transport

    try:
        tickets = [_submit(runtime, held_transport(i), turn_id=f"t{i}") for i in range(2)]
        assert all(event.wait(5) for event in started), "both workers must be occupied"
        tickets.extend(_submit(runtime, _stub(), turn_id=f"t{i}") for i in range(2, 8))
        dropped = [t for t in tickets if t.status == "dropped"]
        assert len(dropped) == 4, "two running plus two queued must reject the other four"
        assert all(t.observation is not None and t.observation.failure_type == FAILURE_QUEUE_FULL for t in dropped)
        release.set()
        for t in tickets:
            assert t.wait(10.0)
        assert runtime.stats()["peak_running"] <= 2
        assert runtime.stats()["dropped"] == len(dropped)
    finally:
        release.set()
        runtime.shutdown()


def test_a_slow_transport_past_the_deadline_is_recorded_as_late_not_dispatched() -> None:
    runtime = ShadowRuntime(max_workers=1, max_queue=2)
    try:
        ticket = _submit(runtime, _stub(delay=0.3), deadline_s=0.05)
        assert ticket.wait(5.0)
        obs = ticket.observation
        assert obs is not None and obs.late is True
        assert obs.failure_type in {FAILURE_NONE, FAILURE_TIMEOUT}
    finally:
        runtime.shutdown()


def test_backend_error_and_unparseable_reply_are_typed_failures() -> None:
    runtime = ShadowRuntime(max_workers=1, max_queue=2)
    try:
        a = _submit(runtime, _stub(raise_error=True), turn_id="a")
        b = _submit(runtime, _stub("not json at all"), turn_id="b")
        assert a.wait(5.0) and b.wait(5.0)
        assert a.observation is not None and a.observation.failure_type == FAILURE_BACKEND_ERROR
        assert b.observation is not None and b.observation.failure_type == FAILURE_PARSE_ERROR
        assert runtime.stats()["failures"][FAILURE_BACKEND_ERROR] == 1
    finally:
        runtime.shutdown()


def test_the_worker_runs_in_a_fresh_context_and_sees_no_turn_state() -> None:
    marker: contextvars.ContextVar[str] = contextvars.ContextVar("shadow_probe", default="unset")
    marker.set("turn-visible")
    seen: dict = {}

    def transport(system, user, json_schema):
        seen["marker"] = marker.get()
        seen["recorder"] = semantic_reach.current()
        return GOOD_REPLY

    runtime = ShadowRuntime(max_workers=1, max_queue=2)
    try:
        with semantic_reach.observing_turn(session_id="s", turn_id="t") as recorder:
            assert recorder is not None
            ticket = _submit(runtime, transport)
            assert ticket.wait(5.0)
        assert seen["marker"] == "unset"     # the caller's ContextVar did not leak in
        assert seen["recorder"] is None      # the turn's reach recorder is absent
    finally:
        runtime.shutdown()


def test_two_turns_are_independent_and_nothing_of_a_turn_is_retained() -> None:
    runtime = ShadowRuntime(max_workers=2, max_queue=4)
    try:
        a = _submit(runtime, _stub(), turn_id="A", text="price of gold")
        b = _submit(runtime, _stub(), turn_id="B", text="weather in Rome")
        assert a.wait(5.0) and b.wait(5.0)
        assert a.observation is not None and b.observation is not None
        assert a.observation.turn_id == "A" and b.observation.turn_id == "B"
        assert a.observation.request_digest != b.observation.request_digest
    finally:
        runtime.shutdown()


def test_observations_carry_no_text_prompt_reply_or_secret() -> None:
    secret_reply = GOOD_REPLY.replace("gold spot price", "gold spot price sk-SECRET-KEY-12345")
    seen: list = []
    runtime = ShadowRuntime(max_workers=1, max_queue=2)
    try:
        ticket = _submit(runtime, _stub(secret_reply, seen=seen), session_id="")
        assert ticket.wait(5.0)
        obs = ticket.observation
        assert obs is not None
        system, user, _schema = seen[0]
        payload = obs.to_dict()
        assert_text_free(payload, ["sk-SECRET-KEY-12345", TEXT, user, system, "price of gold", "Rome", "gold spot price"])
        with pytest.raises(AssertionError, match="leaks text"):
            assert_text_free({"x": "the price of gold"}, ["price of gold"])  # the check itself bites
    finally:
        runtime.shutdown()


def test_a_result_after_the_turn_closed_is_late_and_changes_nothing() -> None:
    runtime = ShadowRuntime(max_workers=1, max_queue=2)
    try:
        ticket = _submit(runtime, _stub(delay=0.2), turn_id="closed-turn")
        runtime.close_turn("closed-turn")
        assert ticket.wait(5.0)
        assert ticket.observation is not None and ticket.observation.late is True
        assert runtime.stats()["late"] >= 1
    finally:
        runtime.shutdown()


def test_the_ring_refuses_a_dispatched_observation() -> None:
    with pytest.raises(ValueError):
        ShadowObservationV2(ticket_id="x", turn_id="t", session_id="", request_digest="d", mode="shadow", dispatched=True)
    with pytest.raises(ValueError):
        ShadowObservationV2(ticket_id="x", turn_id="t", session_id="", request_digest="d", mode="shadow", failure_type="weird")
    record_observation(ShadowObservationV2(ticket_id="x", turn_id="t", session_id="", request_digest="d", mode="shadow"))
    assert recent_observations()[-1].ticket_id == "x"


def test_the_runtime_transport_strips_the_turns_ledger_keys_and_binds_a_deadline(monkeypatch) -> None:
    from core.agent_runtime import semantic_shadow as module

    captured: dict = {}

    class _Router:
        def _invoke_manifest(self, *, manifest, request, output_mode, task, source_context):
            captured["context"] = dict(source_context)
            captured["request"] = request
            class _R:
                output_text = GOOD_REPLY
            return None, _R(), None

    class _Agent:
        memory_router = _Router()

    import core.agent_runtime.audit_routing as audit_routing
    import core.agent_runtime.turn_planner_hook as hook

    monkeypatch.setattr(audit_routing, "resolve_routing_mode", lambda ctx: type("R", (), {"pinned": False})())
    monkeypatch.setattr(audit_routing, "select_audit_manifests", lambda agent, ctx, routing: (["manifest-a"], "ok"))
    monkeypatch.setattr(hook, "_unpaid_manifests", lambda manifests: list(manifests))
    monkeypatch.setattr(hook, "_prioritize_planner_residency", lambda manifests: list(manifests))
    context = {"session_id": "s", "runtime_event_stream_id": "stream-1", "_turn_model_call_ledger_id": "L1",
               "_turn_model_call_ledger_turn": "T1", "_owner_local": True}
    transport = module.build_shadow_transport(_Agent(), context, timeout_s=3.0)
    reply = transport("SYS", "USER", {"type": "object"})
    assert reply == GOOD_REPLY
    sent = captured["context"]
    for key in ("runtime_event_stream_id", "_turn_model_call_ledger_id", "_turn_model_call_ledger_turn"):
        assert key not in sent
    assert sent.get("_owner_local") is True
    assert any("deadline" in str(k) for k in sent), "the provider deadline must be bound onto the call"
    assert captured["request"].metadata.get("semantic_shadow") is True
    assert captured["request"].allow_provider_retry is False
