"""M2c -- a memory hit may not suppress an observation the authority says the turn needs.

WHAT WAS ACTUALLY MEASURED, AND WHAT WAS WRONG ABOUT THE FIRST DIAGNOSIS
------------------------------------------------------------------------
M2b reported that explicitly pinning a cloud model routed the turn through a lane which
bypassed retrieval and binding. That reading was WRONG, and it is corrected here rather
than left standing: the pin does not bypass anything. Driven on a served daemon with
`"model": "amazon/nova-micro-v1"` in the /api/chat body, a current-information question
about a topic the runtime home had never seen produced exactly the intended order --

    task_classified -> web_retrieval_started -> web_retrieval_completed
    -> evidence_bound_to_synthesis -> model_lane_selected (openrouter-byok:amazon/nova-micro-v1)
    -> model.call_started -> model.call_completed -> paid_call.settled

-- with `Brave Search - 4 sources`, keyed, and an answer carrying facts only those sources
had. The pinned model received the same evidence-set id and note ids an auto-selected one
would.

THE REAL DEFECT, which the pin merely happened to be present for:
`should_skip_curiosity_for_local_answer` reads "what is the current state of X right now"
as a local RECALL. So once anything about X sat in memory, the whole turn skipped retrieval
-- no search, no receipt, and no refusal row either, because nothing was ever proposed for
the M2b door to refuse -- and the model answered a live question from its weights. The
identical question about an unfamiliar topic retrieved and bound. Familiarity was the
suppressor, not the model selection.

That is the same failure class M1 and M2b close at other doors: a private heuristic
overruling the canonical authority, invisibly. Memory can make retrieval unnecessary for a
timeless recall; it cannot make an observation unnecessary for a turn the authority says
needs a current one.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest import mock

from apps.vool_agent import ChatTurnResult, ResponseClass
from core.curiosity_gate import should_skip_curiosity_for_local_answer
from core.execution_requirements import requirements_for
from core.identity_manager import load_active_persona
from core.memory_first_router import ModelExecutionDecision

# Reads as a local recall AND is current-information required -- the exact collision.
CURRENT_RECALL_SHAPED = "In two sentences, what is the current state of Rust compiler performance work right now?"
TIMELESS = "explain what a monad is in functional programming"
SELECTED_MODEL = "amazon/nova-micro-v1"

ROWS = [
    {
        "summary": "Rust compiler PGO for distributed builds landed, 15-20% gains",
        "result_title": "Rust compiler performance",
        "result_url": "https://blog.rust-lang.org/perf",
        "origin_domain": "blog.rust-lang.org",
        "search_provider": "brave",
        "source_type": "web_derived",
    },
]


# =========================================================================================
# THE PREMISE, pinned so the collision cannot quietly stop existing
# =========================================================================================


def test_the_question_is_both_a_local_recall_and_current_required() -> None:
    """Both halves of the collision, asserted. If either stops holding, the tests below stop
    testing what they claim to."""
    from core.curiosity_gate import looks_like_local_recall

    assert requirements_for(
        CURRENT_RECALL_SHAPED, source_context={"surface": "openclaw"}
    ).current_information_required is True
    # Recall-SHAPED, which is what made the memory heuristic claim it.
    assert looks_like_local_recall(CURRENT_RECALL_SHAPED) is True
    # And with a memory hit but no canonical requirement, the heuristic does suppress -- the
    # collision this lane exists to resolve.
    assert should_skip_curiosity_for_local_answer(
        CURRENT_RECALL_SHAPED, local_memory_hit=True, current_information_required=False
    ) is True


def test_a_memory_hit_no_longer_suppresses_a_current_required_turn() -> None:
    """PROOF A, at the gate. The memory-recall arm yields to the canonical requirement.

    Sabotage that reddens this: drop `current_information_required` from the final arm of
    `should_skip_curiosity_for_local_answer`.
    """
    # Without the requirement the recall arm still fires -- the heuristic is intact.
    assert should_skip_curiosity_for_local_answer(CURRENT_RECALL_SHAPED, local_memory_hit=True,
                                                  current_information_required=False) is True
    # With it, the turn must still go and look.
    assert should_skip_curiosity_for_local_answer(CURRENT_RECALL_SHAPED, local_memory_hit=True,
                                                  current_information_required=True) is False


def test_a_user_instruction_still_outranks_the_requirement() -> None:
    """Scope. Only the memory arm yields. A stipulated frame, an explicit "don't research"
    and a personal question are the USER's own instruction or a privacy boundary, and a
    classifier may not overrule those -- so they still skip even when current-required."""
    for prompt in (
        # `wants_no_research` -- the user said not to look it up.
        "answer from your own knowledge only: what is the current state of Rust right now",
        # `is_personal_knowledge_question` -- a privacy boundary, never a web search.
        "what is my wife name right now",
    ):
        assert should_skip_curiosity_for_local_answer(
            prompt, local_memory_hit=True, current_information_required=True
        ) is True, prompt


# =========================================================================================
# The turn-level shape: selection is a PROVIDER choice, not a pipeline choice
# =========================================================================================


def _configure(agent: Any, context_result: Any, recorder: dict[str, Any]) -> tuple[Any, dict[str, Any], Any, Any]:
    from apps.vool_agent import adapt_user_input

    task = SimpleNamespace(
        task_id="task-m2c",
        task_summary="Rust compiler performance",
        environment_os="darwin",
        environment_shell="zsh",
        environment_runtime="python",
        environment_version_hint="3.12",
    )
    classification = {"task_class": "research"}
    interpreted = adapt_user_input(CURRENT_RECALL_SHAPED, session_id="m2c-session")
    persona = load_active_persona(agent.persona_id)
    adaptive = SimpleNamespace(
        enabled=False, tool_gap_note="", admitted_uncertainty=False, notes=[],
        reason="not_needed", strategy="none", actions_taken=[], queries_run=[],
        to_dict=lambda: {"enabled": False, "reason": "not_needed", "strategy": "none", "actions_taken": []},
    )

    def resolve(**kwargs: Any) -> ModelExecutionDecision:
        recorder.setdefault("model_calls", []).append(dict(kwargs))
        return ModelExecutionDecision(
            source="provider", task_hash="m2c", provider_id=f"openrouter-byok:{SELECTED_MODEL}",
            used_model=True, output_text="PGO for distributed builds, 15-20% gains.",
            confidence=0.8, trust_score=0.8,
        )

    agent.context_loader.load = mock.Mock(return_value=context_result)
    agent._should_frontload_curiosity = mock.Mock(return_value=False)
    agent._maybe_execute_model_tool_intent = mock.Mock(return_value=None)
    agent._model_routing_profile = mock.Mock(return_value=(classification, {"output_mode": ""}))
    agent._collect_adaptive_research = mock.Mock(return_value=adaptive)
    agent._planned_search_query = mock.Mock(return_value=[dict(r) for r in ROWS])
    agent._search_query = mock.Mock(return_value=[dict(r) for r in ROWS])
    agent.memory_router.resolve = mock.Mock(side_effect=resolve)
    agent.media_pipeline.analyze = mock.Mock(return_value=SimpleNamespace(
        used_provider=False, provider_id="", candidate_id="", reason="no_media",
        evidence_items=[], analysis_text=""))
    agent._web_note_plan_candidates = mock.Mock(return_value=[])
    agent._default_gate = mock.Mock(return_value=SimpleNamespace(mode="advice_only", requires_user_approval=False))
    agent._maybe_publish_public_task = mock.Mock(return_value={})
    agent._grounded_response_class = mock.Mock(return_value=ResponseClass.GENERIC_CONVERSATION)
    agent._turn_result = mock.Mock(side_effect=lambda *a, **k: ChatTurnResult(
        text=str(k.get("response") or (a[0] if a else "")),
        response_class=ResponseClass.GENERIC_CONVERSATION, workflow_summary="w",
        debug_origin="grounded_model"))
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
    ), mock.patch("core.agent_runtime.agent.ingest_media_evidence", return_value=[]), mock.patch(
        "core.agent_runtime.agent.build_media_context_snippets", return_value=[]
    ), mock.patch("core.agent_runtime.agent.build_plan", return_value=SimpleNamespace(confidence=0.7)), mock.patch(
        "core.agent_runtime.agent.explicit_planner_style_requested", return_value=False
    ), mock.patch("core.agent_runtime.agent.should_use_planner_renderer", return_value=False), mock.patch(
        "core.agent_runtime.agent.feedback_engine.evaluate_outcome",
        return_value=SimpleNamespace(is_success=True, is_durable=False),
    ), mock.patch("core.agent_runtime.agent.feedback_engine.apply", return_value=None):
        return agent._execute_grounded_turn(
            task=task, effective_input=request, classification=classification,
            interpreted=interpreted, persona=persona, session_id="m2c-session",
            source_context=context,
        )


def test_a_selected_model_still_gets_retrieval_and_the_bound_evidence_ids(
    make_agent, context_result_factory
) -> None:
    """PROOF B / mandate 6. With a high local-memory score AND an explicitly selected model --
    the exact combination that answered from weights on the served daemon -- the turn must
    still retrieve, bind, and hand the SAME evidence-set and note ids to the selected model."""
    recorder: dict[str, Any] = {}
    agent = make_agent()
    task, classification, interpreted, persona = _configure(
        agent,
        # A confident local memory hit: the suppressor.
        context_result_factory(local_candidates=[], retrieval_confidence_score=0.95),
        recorder,
    )
    context = {
        "surface": "openclaw", "platform": "openclaw", "allow_remote_fetch": True,
        "requested_model": SELECTED_MODEL,
    }

    _drive(agent, task, classification, interpreted, persona, request=CURRENT_RECALL_SHAPED, context=context)

    calls = recorder.get("model_calls") or []
    assert calls, "the selected model was never called"
    assert len(calls) == 1, f"one selection must not buy more than one call: {len(calls)}"

    model_context = calls[0].get("source_context") or {}
    binding = model_context.get("evidence_synthesis_binding") or {}
    assert binding, "the selected model was called with no bound evidence"
    assert binding["proves"] == "prompt_entry", binding
    assert binding["source_count"] >= 1, binding
    assert binding["note_ids"], binding

    # The selection survived into the call that answers -- a provider preference, not a
    # different pipeline.
    assert str(model_context.get("requested_model") or "") == SELECTED_MODEL, model_context.get("requested_model")

    material = repr(calls[0])
    assert binding["evidence_set_id"] in material, binding["evidence_set_id"]
    for note_id in binding["note_ids"]:
        assert note_id in material, note_id


def test_a_direct_turn_with_the_same_selection_does_no_retrieval(
    make_agent, context_result_factory
) -> None:
    """PROOF C. Selecting a model must not turn a DIRECT question into a research turn.
    Same selection, timeless request: no transport runs and nothing is bound."""
    recorder: dict[str, Any] = {}
    agent = make_agent()
    task, classification, interpreted, persona = _configure(
        agent, context_result_factory(local_candidates=[], retrieval_confidence_score=0.0), recorder
    )
    assert requirements_for(TIMELESS, source_context={"surface": "openclaw"}).current_information_required is False

    context = {
        "surface": "openclaw", "platform": "openclaw", "allow_remote_fetch": True,
        "requested_model": SELECTED_MODEL,
    }
    _drive(agent, task, classification, interpreted, persona, request=TIMELESS, context=context)

    assert agent._planned_search_query.call_count == 0, "a DIRECT turn retrieved"
    assert agent._search_query.call_count == 0, "a DIRECT turn retrieved"
    calls = recorder.get("model_calls") or []
    assert calls, "the DIRECT turn never reached the model"
    assert not (calls[0].get("source_context") or {}).get("evidence_synthesis_binding"), "a DIRECT turn bound evidence"


# =========================================================================================
# PROOF D -- a provider failure is reported, never dressed as a grounded answer
# =========================================================================================


def test_a_provider_failure_does_not_publish_grounded_prose(make_agent, context_result_factory) -> None:
    """The retrieval succeeded and the evidence is real, so the tempting failure mode is to
    ship confident prose "about" it. The selected provider failing must instead leave a
    turn that says so: the committed bytes must not be the model's, because there are none."""
    recorder: dict[str, Any] = {}
    agent = make_agent()
    task, classification, interpreted, persona = _configure(
        agent, context_result_factory(local_candidates=[], retrieval_confidence_score=0.95), recorder
    )

    def failing_resolve(**kwargs: Any) -> ModelExecutionDecision:
        recorder.setdefault("model_calls", []).append(dict(kwargs))
        # A provider that came back with nothing usable -- the shape measured live as
        # EMPTY_PROVIDER_RESPONSE / MALFORMED_PROVIDER_RESPONSE.
        return ModelExecutionDecision(
            source="provider", task_hash="m2c-fail", provider_id=f"openrouter-byok:{SELECTED_MODEL}",
            used_model=False, output_text="", confidence=0.0, trust_score=0.0,
        )

    agent.memory_router.resolve = mock.Mock(side_effect=failing_resolve)
    agent._chat_surface_honest_degraded_response = mock.Mock(
        return_value="I couldn't get a usable model response in this run."
    )
    context = {
        "surface": "openclaw", "platform": "openclaw", "allow_remote_fetch": True,
        "requested_model": SELECTED_MODEL,
    }

    result = _drive(
        agent, task, classification, interpreted, persona,
        request=CURRENT_RECALL_SHAPED, context=context,
    )

    published = str(result.get("response") or "")
    assert published, "the turn published nothing at all"
    # The honest degraded path, not invented prose about the sources.
    assert "couldn't get a usable model response" in published, published
    # And it must not have quietly recycled the retrieved rows as if they were an answer.
    assert "15-20% gains" not in published, published


# =========================================================================================
# PROOF E -- the spend gate refuses an UNSELECTED paid call
# =========================================================================================


def test_the_spend_gate_refuses_a_paid_call_nobody_selected() -> None:
    """Mandate 4. An explicit BYOK pick is the act that authorizes spending the owner's key;
    an automatic fallback is not, and must stay unreachable.

    The discriminator is one argument: `reserve_owner_pick_paid_call(manifest=...)`. An
    automatic fallback resolves no requested manifest, so it arrives as None and no
    reservation is issued -- which makes `resolved_allow_paid` False and filters every paid
    manifest out of the candidate pool. Asserted at the reservation seam, so no money moves.
    """
    from core.paid_call_reservation import reserve_owner_pick_paid_call
    from core.request_trust import OWNER_LOCAL_KEY

    task = SimpleNamespace(task_id="task-m2c-spend")
    # The SERVER-STAMPED key, not a lookalike. A context that merely says "owner_local" is not
    # owner-local, so a test using it refuses at the ownership gate and never reaches the
    # selection gate it claims to be testing -- which is exactly how this test passed
    # vacuously on its first cut, and why the reachability control below exists.
    owner_local_context = {"surface": "openclaw", "platform": "openclaw", OWNER_LOCAL_KEY: True}
    paid_manifest = SimpleNamespace(
        adapter_type="cloud_fallback_provider",
        model_name=SELECTED_MODEL,
        source_type="remote",
        runtime_config={"base_url": "https://openrouter.ai/api/v1"},
        metadata={"cost_class": "paid_cloud"},
    )

    # REACHABILITY CONTROL: with a real paid pick the call gets PAST the selection gate, proven
    # by the fact that a later gate (the daily cap) is consulted at all.
    consulted: list[bool] = []
    with mock.patch(
        "core.paid_call_reservation.daily_call_cap_available",
        side_effect=lambda *a, **k: (consulted.append(True), False)[1],
    ):
        assert reserve_owner_pick_paid_call(
            manifest=paid_manifest, task=task, source_context=owner_local_context,
            task_kind="chat", call_role="answer_generation",
        ) is None
        assert consulted, "the selection gate refused a legitimate pick; the control is broken"

        # THE ASSERTION. Same owner, same everything -- only nobody selected the model. It must
        # refuse AT the selection gate, so the later gates are never even reached.
        consulted.clear()
        assert reserve_owner_pick_paid_call(
            manifest=None, task=task, source_context=owner_local_context,
            task_kind="chat", call_role="answer_generation",
        ) is None, "an unselected paid call was authorized"
        assert not consulted, "an unselected paid call got past the selection gate"


def test_the_selection_that_authorizes_spending_is_the_explicit_one() -> None:
    """The other side of the same line, so this is not a test that only says no.

    A turn with no `requested_model` resolves no manifest at all, which is exactly why the
    automatic lane cannot spend. Read through the router's own resolver rather than a
    restatement of it.
    """
    from core.memory_first_router import MemoryFirstRouter

    resolver = MemoryFirstRouter._requested_model_manifest
    unselected = resolver(SimpleNamespace(registry=SimpleNamespace(list_manifests=lambda **k: [])), {})
    assert unselected is None, unselected


# =========================================================================================
# PROOF F -- a retry keeps the preference and does not pay twice
# =========================================================================================


def test_a_repeated_turn_keeps_the_selection_and_retrieves_once_each(
    make_agent, context_result_factory
) -> None:
    """Mandate 5 and RED->GREEN F. Driving the same selected turn twice must keep the provider
    preference on both, and must not let one turn buy two retrievals or two model calls --
    which is what a retry that re-enters the lane without the turn's own receipts would do."""
    context_template = {
        "surface": "openclaw", "platform": "openclaw", "allow_remote_fetch": True,
        "requested_model": SELECTED_MODEL,
    }
    for attempt in (1, 2):
        recorder: dict[str, Any] = {}
        agent = make_agent()
        task, classification, interpreted, persona = _configure(
            agent, context_result_factory(local_candidates=[], retrieval_confidence_score=0.95), recorder
        )
        context = dict(context_template)

        _drive(agent, task, classification, interpreted, persona,
               request=CURRENT_RECALL_SHAPED, context=context)

        calls = recorder.get("model_calls") or []
        assert len(calls) == 1, f"attempt {attempt}: {len(calls)} model calls for one turn"
        model_context = calls[0].get("source_context") or {}
        assert str(model_context.get("requested_model") or "") == SELECTED_MODEL, (
            f"attempt {attempt}: the selection was dropped"
        )
        assert model_context.get("evidence_synthesis_binding"), f"attempt {attempt}: nothing bound"
        # One governed retrieval per turn, on the retry as much as the first run.
        receipts = context.get("web_retrieval_receipts") or []
        assert len(receipts) == 1, f"attempt {attempt}: {len(receipts)} retrievals for one turn"


def test_a_selected_model_does_not_take_the_turn_away_from_the_tool_loop(
    make_agent, context_result_factory
) -> None:
    """Mandate 7, the other half. Choosing a model must not disturb TOOL EXECUTION either.

    The model-tool-intent loop runs before the grounded lane's own retrieval and returns the
    turn when it serves it. A provider preference is a choice about WHO answers, so a turn the
    tool loop owns must still be owned by it, and its result must still be what ships --
    otherwise "selecting a model" would quietly be selecting a different execution path, which
    is the one thing this lane exists to prevent.
    """
    recorder: dict[str, Any] = {}
    agent = make_agent()
    task, classification, interpreted, persona = _configure(
        agent, context_result_factory(local_candidates=[], retrieval_confidence_score=0.0), recorder
    )
    tool_result = {
        "response": "Listed 3 files in the workspace.",
        "confidence": 0.9,
        "success": True,
        "details": {"tool": "workspace.list_files"},
        "mode": "tool_executed",
        "status": "executed",
        "task_outcome": "success",
        "workflow_summary": "- ran workspace.list_files",
    }
    agent._maybe_execute_model_tool_intent = mock.Mock(return_value=tool_result)

    context = {
        "surface": "openclaw", "platform": "openclaw", "allow_remote_fetch": True,
        "requested_model": SELECTED_MODEL,
    }
    result = _drive(
        agent, task, classification, interpreted, persona,
        request=CURRENT_RECALL_SHAPED, context=context,
    )

    assert agent._maybe_execute_model_tool_intent.called, "the tool loop was skipped"
    assert "Listed 3 files" in str(result.get("response") or ""), result.get("response")
    # The tool loop owned the turn, so the grounded lane's own machinery never ran behind it:
    # no second retrieval, and no answering model call to bind evidence into.
    assert agent._planned_search_query.call_count == 0, "the tool-served turn also retrieved"
    assert agent._search_query.call_count == 0, "the tool-served turn also retrieved"
    assert not (recorder.get("model_calls") or []), "the tool-served turn also called the model"
