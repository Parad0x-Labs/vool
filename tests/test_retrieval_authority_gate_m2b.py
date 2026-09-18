"""M2b -- every current-answer retrieval passes the canonical door, and none passes it late.

M1 built the authority and wired ONE production scheduler to it (the live-info fast path).
M2 fixed the ORDER in the grounded reasoning lane. Neither closed the remaining schedulers:
`ResearchToolLoopFacade._collect_live_web_notes` asked its private `_wants_fresh_info` and
went straight to `begin_web_retrieval`, and the adaptive-research roamer did the same behind
its own enable decision. A private yes that never reaches the canonical record is invisible
to every downstream grounding guard.

And the freeze itself had no teeth where they were needed: M1 called
`begin_synthesis_freeze` at the response-guard seam, ~350 lines AFTER the answering model
call, so a retrieval scheduled after the answer was written was still PRE-freeze and was
duly authorized. That is the window the measured fabrication lives inside.

WHAT IS ASSERTED HERE
---------------------
A. DYNAMIC invariant: every production `begin_web_retrieval` actually exercised by a real
   turn has a canonical current-required record at the moment it fires, and fires before
   the synthesis freeze. Not a grep -- the call is intercepted and the record read.
B. A private recognizer firing while the canonical requirement is closed does NOT start a
   retrieval, and leaves a typed refusal saying so.

The two are separable on purpose, and the sabotage record shows they redden independently.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest

from apps.vool_agent import ChatTurnResult, ResponseClass
from core.execution_requirements import (
    begin_synthesis_freeze,
    current_requirement_record,
    requirements_for,
)
from core.identity_manager import load_active_persona
from core.memory_first_router import ModelExecutionDecision
from core.retrieval_authority_gate import (
    DECLINED_SCHEMA,
    REASON_FROZEN,
    REASON_NOT_CURRENT,
)

CURRENT_REQUEST = "what is the latest news on the Rust programming language"

# Every module that binds `begin_web_retrieval` at import time. Patching
# `core.retrieval_observability.begin_web_retrieval` alone would miss all of them, which is
# exactly how a "we patched the door" test can pass while the door is wide open.
BINDING_MODULES = (
    "core.curiosity_roamer",
    "core.agent_runtime.research_tool_loop_facade",
    "core.agent_runtime.fast_live_info_runtime_search",
)

# Exempt, with the reason each is exempt. Neither takes part in current-answer synthesis.
EXEMPT_MODULES = {
    # A settings "Test search provider" probe. It answers "does this key work", not a user's
    # question, and has no turn to be current about.
    "core.search_connection_state": "settings connection probe, not a turn",
    # The typed `web.search` tool-call branch, explicitly out of scope for this lane.
    "core.execution.web_tools": "typed tool-call branch, out of scope",
}

ROWS = [
    {
        "summary": "Phoronix | 2026-08-31 | Rust Coreutils 0.11 Released",
        "result_title": "Rust Coreutils 0.11 Released",
        "result_url": "https://www.phoronix.com/news/rust-coreutils-0-11",
        "origin_domain": "phoronix.com",
        "search_provider": "brave",
        "source_type": "web_derived",
    },
]


class RetrievalWitness:
    """Intercepts every production `begin_web_retrieval` and records the canonical state."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import core.retrieval_observability as observability

        real = observability.begin_web_retrieval

        def witnessed(source_context, **kwargs):
            record = current_requirement_record(source_context)
            self.calls.append(
                {
                    "kind": str(kwargs.get("kind") or ""),
                    "has_record": record is not None,
                    "current_required": bool(
                        record is not None
                        and record.requirements.current_information_required
                    ),
                    "synthesis_started": bool(
                        record is not None and record.synthesis_started
                    ),
                    # WHICH lane the canonical record credits for this decision. Without it the
                    # invariant below could not tell a gated scheduler from an ungated one on an
                    # already-current turn, because M1's door returns True there without
                    # recording anything.
                    "lane_codes": tuple(
                        c
                        for c in (
                            record.requirements.reason_codes if record is not None else ()
                        )
                        if str(c).startswith("current_info_signal:lane:")
                    ),
                }
            )
            return real(source_context, **kwargs)

        monkeypatch.setattr(observability, "begin_web_retrieval", witnessed)
        for module_name in BINDING_MODULES:
            import importlib

            monkeypatch.setattr(
                importlib.import_module(module_name), "begin_web_retrieval", witnessed
            )


def _configure(agent: Any, context_result: Any, *, rows: list[dict[str, Any]] | None = None) -> tuple[Any, dict[str, Any], Any, Any]:
    from apps.vool_agent import adapt_user_input

    task = SimpleNamespace(
        task_id="task-m2b",
        task_summary="Latest news on Rust",
        environment_os="darwin",
        environment_shell="zsh",
        environment_runtime="python",
        environment_version_hint="3.12",
    )
    classification = {"task_class": "research"}
    interpreted = adapt_user_input(CURRENT_REQUEST, session_id="m2b-session")
    persona = load_active_persona(agent.persona_id)
    adaptive = SimpleNamespace(
        enabled=False,
        tool_gap_note="",
        admitted_uncertainty=False,
        notes=[],
        reason="not_needed",
        strategy="none",
        actions_taken=[], queries_run=[],
        to_dict=lambda: {"enabled": False, "reason": "not_needed", "strategy": "none", "actions_taken": []},
    )
    served = [dict(r) for r in (ROWS if rows is None else rows)]

    agent.context_loader.load = mock.Mock(return_value=context_result)
    agent._should_frontload_curiosity = mock.Mock(return_value=False)
    agent._maybe_execute_model_tool_intent = mock.Mock(return_value=None)
    agent._model_routing_profile = mock.Mock(return_value=(classification, {"output_mode": ""}))
    agent._collect_adaptive_research = mock.Mock(return_value=adaptive)
    # The transport, not the collector: the REAL governed collector runs, so the real door,
    # the real receipt and the real refusal path are all exercised.
    agent._planned_search_query = mock.Mock(return_value=served)
    agent._search_query = mock.Mock(return_value=served)
    agent.memory_router.resolve = mock.Mock(
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="m2b-hash",
            provider_id="openrouter:test",
            used_model=True,
            output_text="Rust Coreutils 0.11 was released, per phoronix.com.",
            confidence=0.8,
            trust_score=0.8,
        )
    )
    agent.media_pipeline.analyze = mock.Mock(
        return_value=SimpleNamespace(
            used_provider=False, provider_id="", candidate_id="", reason="no_media",
            evidence_items=[], analysis_text="",
        )
    )
    agent._web_note_plan_candidates = mock.Mock(return_value=[])
    agent._default_gate = mock.Mock(return_value=SimpleNamespace(mode="advice_only", requires_user_approval=False))
    agent._maybe_publish_public_task = mock.Mock(return_value={})
    agent._grounded_response_class = mock.Mock(return_value=ResponseClass.GENERIC_CONVERSATION)
    agent._turn_result = mock.Mock(
        side_effect=lambda *a, **k: ChatTurnResult(
            text=str(k.get("response") or (a[0] if a else "")),
            response_class=ResponseClass.GENERIC_CONVERSATION,
            workflow_summary="w",
            debug_origin="grounded_model",
        )
    )
    agent._apply_interaction_transition = mock.Mock()
    agent._decorate_chat_response = mock.Mock(side_effect=lambda turn, *a, **k: getattr(turn, "text", turn))
    agent._emit_chat_truth_metrics = mock.Mock()
    agent._finalize_runtime_checkpoint = mock.Mock()
    agent._runtime_preview = mock.Mock(return_value="preview")
    agent._task_workflow_summary = mock.Mock(return_value="w")
    agent._chat_surface_honest_degraded_response = mock.Mock(return_value="degraded")
    agent._store_local_shard = mock.Mock()
    agent.hive_activity_tracker.note_watched_topic = mock.Mock()
    return task, classification, interpreted, persona


def _drive(agent, task, classification, interpreted, persona, *, request: str, context: dict[str, Any]):
    with mock.patch("core.policy_engine.allow_web_fallback", return_value=True), mock.patch(
        "core.agent_runtime.agent.orchestrate_parent_task", return_value=None
    ), mock.patch(
        "core.agent_runtime.agent.ingest_media_evidence", return_value=[]
    ), mock.patch(
        "core.agent_runtime.agent.build_media_context_snippets", return_value=[]
    ), mock.patch(
        "core.agent_runtime.agent.build_plan", return_value=SimpleNamespace(confidence=0.7)
    ), mock.patch(
        "core.agent_runtime.agent.explicit_planner_style_requested", return_value=False
    ), mock.patch(
        "core.agent_runtime.agent.should_use_planner_renderer", return_value=False
    ), mock.patch(
        "core.agent_runtime.agent.feedback_engine.evaluate_outcome",
        return_value=SimpleNamespace(is_success=True, is_durable=False),
    ), mock.patch(
        "core.agent_runtime.agent.feedback_engine.apply", return_value=None
    ):
        return agent._execute_grounded_turn(
            task=task,
            effective_input=request,
            classification=classification,
            interpreted=interpreted,
            persona=persona,
            session_id="m2b-session",
            source_context=context,
        )


# =========================================================================================
# INVENTORY -- the door's coverage is asserted, not assumed
# =========================================================================================


def test_every_binding_module_is_either_gated_or_explicitly_exempt() -> None:
    """A new module that binds `begin_web_retrieval` must be classified, not silently added.

    This is the static half. It cannot prove the door RUNS -- that is the dynamic test
    below -- but it does stop a sixth scheduler appearing with neither a gate nor a
    recorded exemption, which is how the first five diverged.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent.parent
    binders: set[str] = set()
    for path in (root / "core").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"^\s*from core\.retrieval_observability import .*begin_web_retrieval", text, re.M):
            binders.add(str(path.relative_to(root)).replace("/", ".")[: -len(".py")])

    classified = set(BINDING_MODULES) | set(EXEMPT_MODULES)
    assert binders <= classified, (
        "a module binds begin_web_retrieval and is neither gated nor exempt: "
        f"{sorted(binders - classified)}"
    )
    for gated in BINDING_MODULES:
        assert gated in binders, f"{gated} no longer binds begin_web_retrieval"


# =========================================================================================
# PROOF A -- the dynamic invariant
# =========================================================================================


def test_every_exercised_retrieval_is_canonically_authorized_and_pre_freeze(
    make_agent, context_result_factory, monkeypatch
) -> None:
    """PROOF A. Drive a real current-information turn and intercept every production
    `begin_web_retrieval` it actually performs. Each one must, AT THE MOMENT IT FIRES,
    hold a canonical record that is current-required and not yet frozen."""
    witness = RetrievalWitness()
    witness.install(monkeypatch)

    agent = make_agent()
    task, classification, interpreted, persona = _configure(
        agent, context_result_factory(local_candidates=[], retrieval_confidence_score=0.0)
    )
    context = {"surface": "openclaw", "platform": "openclaw", "allow_remote_fetch": True}

    _drive(agent, task, classification, interpreted, persona, request=CURRENT_REQUEST, context=context)

    assert witness.calls, "the turn performed no retrieval at all, so nothing was proven"
    for call in witness.calls:
        assert call["has_record"], call
        assert call["current_required"] is True, call
        assert call["synthesis_started"] is False, call
        # The retrieval was authorized THROUGH the canonical door, not merely alongside a
        # record that happened to be current. Removing the door from a scheduler reds here.
        assert call["lane_codes"], call


def test_the_record_is_frozen_by_the_time_the_turn_ends(
    make_agent, context_result_factory, monkeypatch
) -> None:
    """The other half of PROOF A: the freeze genuinely happens on this path, so
    "not yet frozen at retrieval time" is a real ordering claim and not a vacuous one
    about a freeze that never runs."""
    witness = RetrievalWitness()
    witness.install(monkeypatch)

    agent = make_agent()
    task, classification, interpreted, persona = _configure(
        agent, context_result_factory(local_candidates=[], retrieval_confidence_score=0.0)
    )
    context = {"surface": "openclaw", "platform": "openclaw", "allow_remote_fetch": True}

    _drive(agent, task, classification, interpreted, persona, request=CURRENT_REQUEST, context=context)

    record = current_requirement_record(context)
    assert record is not None
    assert record.synthesis_started is True, "synthesis never froze the decision"


# =========================================================================================
# PROOF B -- a private recognizer cannot retrieve behind the authority's back
# =========================================================================================


def test_a_frozen_turn_refuses_the_fallback_search_and_records_why(make_agent, monkeypatch) -> None:
    """PROOF B. `_wants_fresh_info` says yes -- the private recognizer fires -- but the
    canonical decision is already closed. No retrieval may start, and the refusal must be
    a typed row rather than a silent empty list, because "the provider had nothing" and
    "this lane was not allowed to ask" are opposite facts about the turn."""
    witness = RetrievalWitness()
    witness.install(monkeypatch)

    agent = make_agent()
    agent._wants_fresh_info = mock.Mock(return_value=True)
    agent._planned_search_query = mock.Mock(return_value=[dict(r) for r in ROWS])
    agent._search_query = mock.Mock(return_value=[dict(r) for r in ROWS])

    context = {"surface": "openclaw", "platform": "openclaw", "allow_remote_fetch": True}
    requirements_for(CURRENT_REQUEST, source_context=context)
    begin_synthesis_freeze(context, CURRENT_REQUEST)

    with mock.patch("core.policy_engine.allow_web_fallback", return_value=True):
        notes = agent._collect_live_web_notes(
            task_id="task-m2b-frozen",
            query_text=CURRENT_REQUEST,
            classification={"task_class": "research"},
            interpretation=SimpleNamespace(topic_hints=[]),
            source_context=context,
        )

    assert notes == [], notes
    assert agent._wants_fresh_info.called, "the premise of this test stopped holding"
    assert witness.calls == [], "a retrieval started on a frozen turn"
    assert agent._planned_search_query.call_count == 0
    assert agent._search_query.call_count == 0

    refusals = context.get("retrieval_authority_refusals") or []
    assert refusals, "the refusal was silent"
    refusal = refusals[-1]
    assert refusal["schema"] == DECLINED_SCHEMA, refusal
    assert refusal["lane"] == "reasoning_fallback_search", refusal
    assert refusal["reason_code"] == REASON_FROZEN, refusal
    assert refusal["retrieval_started"] is False, refusal
    assert refusal["proposal_reason"] == "_wants_fresh_info", refusal


def test_the_refusal_names_a_closed_decision_apart_from_one_that_was_never_current() -> None:
    """Two different facts, two different reason codes. A reader told `not_current` looks at
    the request; a reader told `decision_closed` looks at the ordering. Collapsing them would
    send every investigation to the wrong place."""
    from core.retrieval_authority_gate import record_retrieval_refusal

    frozen_ctx: dict[str, Any] = {"surface": "openclaw"}
    requirements_for(CURRENT_REQUEST, source_context=frozen_ctx)
    begin_synthesis_freeze(frozen_ctx, CURRENT_REQUEST)
    assert record_retrieval_refusal(frozen_ctx, lane="l")["reason_code"] == REASON_FROZEN

    open_ctx: dict[str, Any] = {"surface": "openclaw"}
    requirements_for("explain what a monad is", source_context=open_ctx)
    assert record_retrieval_refusal(open_ctx, lane="l")["reason_code"] == REASON_NOT_CURRENT


def test_the_door_authorizes_by_escalating_and_attributes_the_lane() -> None:
    """The contract this lane relies on, pinned so it cannot drift: before the freeze the
    authority does not merely permit -- it RECORDS the lane's claim as the turn's decision,
    reason-coded, so what scheduled the search is what every later guard reads."""
    from core.retrieval_authority_gate import authorize_retrieval

    context: dict[str, Any] = {"surface": "openclaw"}
    timeless = "explain what a monad is in functional programming"
    requirements_for(timeless, source_context=context)
    assert current_requirement_record(context).requirements.current_information_required is False

    assert authorize_retrieval(context, timeless, lane="reasoning_fallback_search") is True

    record = current_requirement_record(context)
    assert record.requirements.current_information_required is True
    assert "current_info_signal:lane:reasoning_fallback_search" in record.requirements.reason_codes
    assert not (context.get("retrieval_authority_refusals") or []), "an authorized call recorded a refusal"


def test_a_frozen_turn_refuses_the_adaptive_research_roam_and_records_why(monkeypatch) -> None:
    """PROOF B, second scheduler. The roamer is gated PER ROUND, so a roam already in flight
    when synthesis freezes stops at the next round instead of continuing to search behind an
    answer that is already being written.

    This also pins the thing that would have made the gate a silent no-op: the roamer's own
    decision helper takes a LOCAL COPY of source_context, so a gate written against that copy
    would record refusals nobody can read and escalate a record nobody sees. The assertions
    below read the CALLER's dict, so they fail if the gate is ever moved onto a copy.
    """
    witness = RetrievalWitness()
    witness.install(monkeypatch)

    from core.curiosity_roamer import CuriosityRoamer

    context: dict[str, Any] = {"surface": "openclaw", "platform": "openclaw", "allow_remote_fetch": True}
    requirements_for(CURRENT_REQUEST, source_context=context)
    begin_synthesis_freeze(context, CURRENT_REQUEST)

    roamer = CuriosityRoamer()
    with mock.patch("core.policy_engine.allow_web_fallback", return_value=True), mock.patch(
        "core.curiosity_roamer._adaptive_research_decision",
        return_value={
            "enabled": True,
            "reason": "research",
            "strategy": "web",
            "escalated_from_chat": False,
            "tool_gap_note": "",
        },
    ), mock.patch("retrieval.web_adapter.WebAdapter.planned_search_query", return_value=[dict(r) for r in ROWS]) as transport:
        result = roamer.adaptive_research(
            task_id="task-m2b-roam-frozen",
            user_input=CURRENT_REQUEST,
            classification={"task_class": "research"},
            interpretation=SimpleNamespace(topic_hints=[]),
            source_context=context,
        )

    assert witness.calls == [], "the roamer retrieved on a frozen turn"
    assert transport.call_count == 0, "the roamer reached its transport on a frozen turn"
    assert list(result.notes or []) == [], result.notes

    refusals = context.get("retrieval_authority_refusals") or []
    assert refusals, "the roamer's refusal was silent, or was written to a copy of the context"
    assert refusals[-1]["lane"] == "adaptive_research", refusals[-1]
    assert refusals[-1]["reason_code"] == REASON_FROZEN, refusals[-1]


def test_no_retrieval_starts_after_the_answer_has_been_written(
    make_agent, context_result_factory, monkeypatch
) -> None:
    """PROOF F's target. The post-model collector is the seam the measured defect used: it sits
    ~90 lines BELOW the answering call, so anything it fetches arrives after the bytes exist.

    Driven here with a timeless request, so M2's pre-model hoist declines and the turn reaches
    that collector for real, with the lane's private recognizer forced ON so it genuinely tries.
    The turn must reach the collector (asserted, so this cannot pass by never getting there) and
    the retrieval must NOT start, because synthesis froze the decision before the model call.

    Sabotage that reddens this: move `begin_synthesis_freeze` back below the model call, and the
    post-answer search is authorized again exactly as it was at base.
    """
    witness = RetrievalWitness()
    witness.install(monkeypatch)

    timeless = "explain what a monad is in functional programming"
    assert requirements_for(timeless, source_context={"surface": "openclaw"}).current_information_required is False

    agent = make_agent()
    task, classification, interpreted, persona = _configure(
        agent, context_result_factory(local_candidates=[], retrieval_confidence_score=0.0)
    )
    # The private recognizer proposes on a turn the authority never called current.
    agent._wants_fresh_info = mock.Mock(return_value=True)
    context = {"surface": "openclaw", "platform": "openclaw", "allow_remote_fetch": True}

    _drive(agent, task, classification, interpreted, persona, request=timeless, context=context)

    assert agent._wants_fresh_info.called, "the turn never reached the post-model collector"
    assert witness.calls == [], f"a retrieval started after the answer: {witness.calls}"
    assert agent._planned_search_query.call_count == 0
    assert agent._search_query.call_count == 0

    refusals = context.get("retrieval_authority_refusals") or []
    assert refusals, "the post-answer retrieval was declined silently"
    assert refusals[-1]["reason_code"] == REASON_FROZEN, refusals[-1]
    assert refusals[-1]["lane"] == "reasoning_fallback_search", refusals[-1]


def test_the_lane_is_attributed_even_when_the_turn_was_already_current() -> None:
    """Found by adversarial review of this lane's own first cut, not by a failing drive.

    M1's door short-circuits on a record that is ALREADY current-required and returns True
    without escalating. Correct for the question it asks -- and it means the record never
    learns which lane scheduled the search. The consequence is not cosmetic: with no
    `current_info_signal:lane:*` code, the frozen record of an already-current turn is
    byte-identical whether this door ran or was deleted, so nothing downstream can tell a
    gated scheduler from an ungated one. The gate therefore stamps attribution itself.
    """
    from core.retrieval_authority_gate import authorize_retrieval

    context: dict[str, Any] = {"surface": "openclaw"}
    requirements_for(CURRENT_REQUEST, source_context=context)
    record = current_requirement_record(context)
    assert record.requirements.current_information_required is True, "premise: already current"
    assert not any(
        c.startswith("current_info_signal:lane:") for c in record.requirements.reason_codes
    ), "premise: no lane attribution yet"

    assert authorize_retrieval(context, CURRENT_REQUEST, lane="reasoning_fallback_search") is True

    codes = current_requirement_record(context).requirements.reason_codes
    assert "current_info_signal:lane:reasoning_fallback_search" in codes, codes


def test_the_planned_search_fallthrough_is_authorized_separately(make_agent, monkeypatch) -> None:
    """The alternate branch. A planned search that returns EMPTY falls through to a second,
    different transport under the receipt the first one minted -- one authorization, two
    fetches. The unit protected here is a TRANSPORT, not a receipt.

    Driven with the planned branch returning nothing so the fall-through is actually taken,
    and with the decision frozen between the two, which is the case a single top-of-function
    check cannot see.
    """
    witness = RetrievalWitness()
    witness.install(monkeypatch)

    agent = make_agent()
    agent._wants_fresh_info = mock.Mock(return_value=True)

    context: dict[str, Any] = {"surface": "openclaw", "platform": "openclaw", "allow_remote_fetch": True}
    requirements_for(CURRENT_REQUEST, source_context=context)

    def planned_then_freeze(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        # The planned transport comes back empty, and synthesis begins before the lane can try
        # its alternate. Without a second door the plain search below would still run.
        begin_synthesis_freeze(context, CURRENT_REQUEST)
        return []

    agent._planned_search_query = mock.Mock(side_effect=planned_then_freeze)
    agent._search_query = mock.Mock(return_value=[dict(r) for r in ROWS])

    with mock.patch("core.policy_engine.allow_web_fallback", return_value=True):
        notes = agent._collect_live_web_notes(
            task_id="task-m2b-fallthrough",
            query_text=CURRENT_REQUEST,
            classification={"task_class": "research"},
            interpretation=SimpleNamespace(topic_hints=[]),
            source_context=context,
        )

    assert agent._planned_search_query.call_count == 1, "the planned branch never ran"
    assert agent._search_query.call_count == 0, "the fall-through transport ran unauthorized"
    assert notes == [], notes

    refusals = context.get("retrieval_authority_refusals") or []
    assert refusals, "the fall-through refusal was silent"
    assert refusals[-1]["proposal_reason"] == "planned_search_empty_fallthrough", refusals[-1]
    assert refusals[-1]["reason_code"] == REASON_FROZEN, refusals[-1]

    # The open receipt closes as REFUSED, not as a provider that merely had nothing.
    receipts = context.get("web_retrieval_receipts") or []
    assert receipts and receipts[-1]["status"] == "refused", receipts[-1] if receipts else None
