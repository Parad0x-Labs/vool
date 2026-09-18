"""The planner's auxiliary asks run on the author the turn will actually use.

MEASURED (s50 rig on the frozen build 3e1bf797, three FRESH sessions of the verbatim
three-clause turn "Answer all three: What is 5+5? ... Emperor of Japan? ... Berlin Wall fall?",
raw `validation-logs/consolidation-continuation-20260909/results/baseline-takeover-3e1bf797-*`):

    planner clause split on ollama-local:qwen2.5:7b   14.1 s   completed
    planner clause split on ollama-local:qwen2.5:7b   13.8 s   completed
    planner clause split on ollama-local:qwen2.5:7b   15.0 s   model.call_failed (budget)
      -> no plan -> research lane -> 49 s of keyless retrieval -> qwen3:8b wrote the turn
      -> grounding refused the WHOLE turn, "5 + 5 = 10" and "1989" included (96 s).

The daily default was chosen by residency alignment, but on that runtime the authorship policy
skips it on every generation ("model_lane_failed ... trying fallback" in 0.2 s); the certified
author that serves the nodes, qwen3:8b, was resident the whole time. The contract under test:
in Auto/Local-only, a policy-eligible final-answer author is asked BEFORE any ineligible
candidate; the residency order stands among equals and stands untouched when nobody is
eligible; a manual pin is exact-model authority and is never reordered.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest


class _RecordingRouter:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def _invoke_manifest(self, *, manifest: Any, request: Any, **_: Any) -> tuple[Any, Any, Any]:
        self.calls.append(str(manifest.provider_id))
        return SimpleNamespace(), SimpleNamespace(output_text="[]"), None


DAILY = "ollama-local:qwen2.5:7b"
CERTIFIED = "ollama-local:qwen3:8b"


def _drive(
    monkeypatch: pytest.MonkeyPatch,
    manifests: list[str],
    *,
    eligible: set[str],
    raising: set[str] = frozenset(),
    pinned: bool = False,
    semantic: bool = False,
) -> list[str]:
    from core.agent_runtime.turn_planner_hook import (
        build_planner_ask_model,
        build_semantic_proof_ask_model,
    )

    rows = [SimpleNamespace(provider_id=pid, model_name=pid.split(":", 1)[1]) for pid in manifests]
    router = _RecordingRouter()
    agent = SimpleNamespace(memory_router=router)
    monkeypatch.setattr(
        "core.agent_runtime.audit_routing.select_audit_manifests",
        lambda agent, context, routing: (rows, ""),
    )
    monkeypatch.setattr(
        "core.agent_runtime.audit_routing.resolve_routing_mode",
        lambda context: SimpleNamespace(pinned=pinned),
    )
    monkeypatch.setattr("core.model_selection_policy.provider_cost_class", lambda m: "free_local")
    monkeypatch.setattr(
        "core.agent_runtime.turn_planner_hook._prioritize_planner_residency", lambda c: list(c)
    )

    def _decide(**kw: Any) -> SimpleNamespace:
        pid = str(kw.get("requested_model") or "")
        if pid in raising:
            raise RuntimeError("certification store unavailable")
        return SimpleNamespace(eligible=pid in eligible, reason="policy-eligible-in-test")

    monkeypatch.setattr("core.final_answer_authorship.decide_final_answer_author", _decide)
    context = {"turn_id": uuid.uuid4().hex}
    build = build_semantic_proof_ask_model if semantic else build_planner_ask_model
    build(agent, context)("system", "Answer all three: What is 5+5? ...")
    return router.calls


def test_the_eligible_author_is_asked_before_the_daily_default(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _drive(monkeypatch, [DAILY, CERTIFIED], eligible={CERTIFIED})
    assert calls[:1] == [CERTIFIED], calls


def test_the_semantic_proof_ask_shares_the_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _drive(monkeypatch, [DAILY, CERTIFIED], eligible={CERTIFIED}, semantic=True)
    assert calls[:1] == [CERTIFIED], calls


def test_no_eligible_author_keeps_the_residency_order(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _drive(monkeypatch, [DAILY, CERTIFIED], eligible=set())
    assert calls[:1] == [DAILY], calls


def test_a_raising_eligibility_check_is_not_eligible_and_not_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _drive(monkeypatch, [DAILY, CERTIFIED], eligible={DAILY, CERTIFIED}, raising={DAILY})
    assert calls[:1] == [CERTIFIED], calls


def test_every_check_raising_keeps_the_order_and_still_asks(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _drive(monkeypatch, [DAILY, CERTIFIED], eligible=set(), raising={DAILY, CERTIFIED})
    assert calls[:1] == [DAILY], calls


def test_a_manual_pin_is_never_reordered(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _drive(monkeypatch, [DAILY, CERTIFIED], eligible={CERTIFIED}, pinned=True)
    assert calls[:1] == [DAILY], "exact-model authority: a pin is asked as pinned"


def test_the_partition_is_stable_among_eligible_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    other = "ollama-local:qwen3:4b"
    calls = _drive(monkeypatch, [DAILY, other, CERTIFIED], eligible={other, CERTIFIED})
    assert calls[:1] == [other], "the first eligible candidate in residency order is asked"
