"""The conductor turn's phase budgets — planning may not starve execution.

MEASURED (served on 79a9b357, turn 9419658a, FINDINGS F43's open residual)
--------------------------------------------------------------------------
"Answer all three: What is 5+5? [Emperor] [Berlin Wall]" spent its one 45 s
conductor deadline like this: TWO unpaid planner calls on a COLD qwen2.5:7b
(first call pays the multi-GB load) consumed the front of the budget; the
Berlin Wall node's qwen3:8b generation then inherited ~12 s of remainder and
died as PROVIDER_TIMEOUT (fault-ccf1835ca197), which the node misreported as
"the explanation came back empty". The 5+5 calculation and the Emperor
refusal were correct; the knowledge clause lost its opportunity to the phase
that runs FIRST and had no bound of its own.

THE CONTRACT UNDER TEST
-----------------------
One owner, one clock stays true: the turn deadline is absolute and nothing
may extend it. What changes is ALLOCATION, through the existing
bind-only-shortens mechanism (`core.provider_call_deadline`):

* the PLANNER phase (clause split + semantic proof) runs under a sub-deadline
  capped at PLANNER_PHASE_CAP_S of wall clock, so a cold planner declines the
  conductor turn instead of executing a plan it has already starved — the
  ordinary lanes then serve the user with their own budget;
* node execution keeps the FULL remaining turn deadline (never shortened by
  the planner cap);
* a provider call that crosses the deadline STOPS candidate iteration and
  raises ProviderCallDeadlineExceededError, so a node's failure names the
  timeout instead of laundering it as an empty generation.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest

from core.conductor.scheduler import DEFAULT_PLAN_DEADLINE_S, PLANNER_PHASE_CAP_S
from core.provider_call_deadline import (
    PROVIDER_DEADLINE_KEY,
    ProviderCallDeadlineExceededError,
    bind_provider_deadline,
    deadline_expired,
)


def test_the_planner_cap_is_a_real_share_of_the_turn_deadline() -> None:
    """A cap that isn't materially shorter than the turn protects nothing; one so
    short no plan can be built is a silent conductor off-switch. 40% of the
    default turn keeps both properties measurable."""
    assert PLANNER_PHASE_CAP_S < DEFAULT_PLAN_DEADLINE_S * 0.5
    assert PLANNER_PHASE_CAP_S >= DEFAULT_PLAN_DEADLINE_S * 0.3


def test_planner_phase_binding_never_extends_and_never_exceeds_the_cap() -> None:
    now = time.monotonic()
    turn_deadline = now + DEFAULT_PLAN_DEADLINE_S
    # Fresh turn: the planner phase is capped at the cap.
    planner_ctx = bind_provider_deadline(
        {}, turn_deadline_monotonic=now + PLANNER_PHASE_CAP_S, reason="conductor planner phase cap"
    )
    assert planner_ctx[PROVIDER_DEADLINE_KEY] <= now + PLANNER_PHASE_CAP_S + 0.5
    # A turn already mostly spent: the planner inherits only what REMAINS.
    spent_ctx = bind_provider_deadline(
        {PROVIDER_DEADLINE_KEY: now + 4.0},
        turn_deadline_monotonic=now + PLANNER_PHASE_CAP_S,
        reason="conductor planner phase cap",
    )
    assert spent_ctx[PROVIDER_DEADLINE_KEY] <= now + 4.0


# ------------------------------------------------------- the generation seam's deadline honesty


class _FakeRouter:
    def __init__(self, failures: int, latency: float = 0.0) -> None:
        self.invocations = 0
        self._failures = failures
        self._latency = latency

    def _invoke_manifest(self, *, manifest: Any, request: Any, **_: Any) -> tuple[Any, Any, Any]:
        self.invocations += 1
        if self._latency:
            time.sleep(self._latency)
        if self.invocations <= self._failures:
            return None, None, "provider error"
        response = SimpleNamespace(output_text="The Berlin Wall fell in 1989.")
        return SimpleNamespace(), response, None


def _manifest(provider_id: str) -> SimpleNamespace:
    return SimpleNamespace(provider_id=provider_id)


def _agent_with(router: _FakeRouter, manifests: list[SimpleNamespace]) -> SimpleNamespace:
    agent = SimpleNamespace(memory_router=router)
    agent.memory_router._manifests = manifests
    return agent


def _eligible_manifests_in_router(agent: SimpleNamespace) -> None:
    """Let decide_final_answer_author see one eligible candidate per manifest.

    The policy reads the registry; pointing it at a stub store keeps this test on
    the deadline contract, not on certification plumbing.
    """

    import core.final_answer_authorship as fa

    agent._fa = fa
    fa_author_cert = lambda *a, **k: None  # noqa: E731
    agent._cert_stub = fa_author_cert


def test_a_crossed_deadline_stops_candidate_iteration_and_names_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.agent_runtime.turn_planner_hook import build_conductor_ask_model

    router = _FakeRouter(failures=99)
    manifests = [_manifest("ollama-local:qwen3:8b"), _manifest("ollama-local:qwen2.5:7b")]
    agent = _agent_with(router, manifests)

    monkeypatch.setattr(
        "core.model_selection_policy.provider_cost_class",
        lambda manifest: "free_local",
    )
    monkeypatch.setattr(
        "core.final_answer_authorship.decide_final_answer_author",

        lambda **_: SimpleNamespace(eligible=True, reason="stub"),
    )
    monkeypatch.setattr(
        "core.agent_runtime.audit_routing.select_audit_manifests",
        lambda agent, context, routing: (manifests, ""),
    )
    monkeypatch.setattr(
        "core.agent_runtime.audit_routing.resolve_routing_mode",
        lambda context: "auto",
    )

    # A deadline that is already behind us: the seam must raise the typed error,
    # not iterate every candidate and return "" (which the knowledge node then
    # misreports as an empty generation).
    context = {PROVIDER_DEADLINE_KEY: time.monotonic() - 1.0}
    ask = build_conductor_ask_model(agent, context)
    with pytest.raises(ProviderCallDeadlineExceededError):
        ask("system", "briefing")
    assert router.invocations == 0, "no provider call may start past the deadline"


def test_a_deadline_crossed_mid_iteration_stops_churning(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.agent_runtime.turn_planner_hook import build_conductor_ask_model

    # First candidate errors AND takes longer than the remaining deadline: the
    # second candidate must NOT be invoked.
    router = _FakeRouter(failures=99, latency=0.2)
    manifests = [_manifest("ollama-local:qwen3:8b"), _manifest("ollama-local:qwen2.5:7b")]
    agent = _agent_with(router, manifests)

    monkeypatch.setattr(
        "core.model_selection_policy.provider_cost_class",
        lambda manifest: "free_local",
    )
    monkeypatch.setattr(
        "core.final_answer_authorship.decide_final_answer_author",

        lambda **_: SimpleNamespace(eligible=True, reason="stub"),
    )
    monkeypatch.setattr(
        "core.agent_runtime.audit_routing.select_audit_manifests",
        lambda agent, context, routing: (manifests, ""),
    )
    monkeypatch.setattr(
        "core.agent_runtime.audit_routing.resolve_routing_mode",
        lambda context: "auto",
    )

    now = time.monotonic()
    context = {PROVIDER_DEADLINE_KEY: now + 0.05}
    ask = build_conductor_ask_model(agent, context)
    with pytest.raises(ProviderCallDeadlineExceededError):
        ask("system", "briefing")
    assert router.invocations == 1, (
        f"iteration continued past the deadline: {router.invocations} calls"
    )


def test_healthy_fallback_survives_the_deadline_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.agent_runtime.turn_planner_hook import build_conductor_ask_model

    router = _FakeRouter(failures=1)
    manifests = [_manifest("ollama-local:qwen3:8b"), _manifest("ollama-local:qwen2.5:7b")]
    agent = _agent_with(router, manifests)

    monkeypatch.setattr(
        "core.model_selection_policy.provider_cost_class",
        lambda manifest: "free_local",
    )
    monkeypatch.setattr(
        "core.final_answer_authorship.decide_final_answer_author",

        lambda **_: SimpleNamespace(eligible=True, reason="stub"),
    )
    monkeypatch.setattr(
        "core.agent_runtime.audit_routing.select_audit_manifests",
        lambda agent, context, routing: (manifests, ""),
    )
    monkeypatch.setattr(
        "core.agent_runtime.audit_routing.resolve_routing_mode",
        lambda context: "auto",
    )

    context = {PROVIDER_DEADLINE_KEY: time.monotonic() + 60.0}
    ask = build_conductor_ask_model(agent, context)
    assert ask("system", "briefing") == "The Berlin Wall fell in 1989."
    assert router.invocations == 2
    assert not deadline_expired(context)
