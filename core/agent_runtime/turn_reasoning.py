from __future__ import annotations

from contextlib import suppress
from typing import Any

from core.active_mission import current_active_mission_slots
from core.curiosity_gate import should_skip_curiosity_for_local_answer
from core.curiosity_roamer import CuriosityResult
from core.execution.constants import local_fact_capability_required
from core.memory_first_router import apply_verifier_draft_caveat, execution_visibility
from core.reasoning_engine import inspect_user_response_shape
from core.remote_fetch_policy import explicit_remote_fetch_disabled
from core.token_usage_receipt import append_token_usage_receipt
from storage.shard_reuse_outcomes import record_shard_reuse_outcomes

_GROUNDED_RESPONSE_REASONS = {"grounded_plan_response", "grounded_model_response"}
_NON_QUALITY_RESPONSE_CLASSES = {"task_failed_user_safe", "system_error_user_safe"}


def _canonical_current_required(effective_input: str, source_context: Any) -> bool:
    """The turn's canonical current-information requirement, read never re-derived.

    Fail-soft to False: a requirement that cannot be read must not silently start forcing
    retrieval on every turn. The cost of False here is the pre-existing behaviour; the cost of
    a spurious True is a live search on turns that never wanted one.
    """
    try:
        from core.execution_requirements import requirements_for

        return bool(
            requirements_for(
                effective_input, source_context=source_context
            ).current_information_required
        )
    except Exception:
        return False


def _internal_payload(text: str) -> bool:
    """Machine scaffolding or a bare monologue, never an answer to a person.

    One shared question for every passthrough point — the payload shapes (a filename array welded
    to code, an unclosed reasoning block, a fabricated tool result) and the reasoning-lead check
    that catches a cloud model narrating its plan instead of answering. The logic lives in
    `core.agent_runtime.response.internal_payload_or_monologue` so this branch, the structured
    synthesis branch, the respond.direct lane and the streaming release gate cannot drift.
    Fail-soft -- a guard that raises must not take the turn down with it.
    """

    try:
        from core.agent_runtime.response import internal_payload_or_monologue

        return internal_payload_or_monologue(text)
    except Exception:
        return False


def _safe_active_mission_slots(session_id: Any) -> list[Any]:
    try:
        return current_active_mission_slots(session_id)
    except Exception:
        return []


def _dispatch_background_swarm_query(
    *,
    task: Any,
    classification: dict[str, Any],
    ranked: list[Any],
    context_result: Any,
    request_relevant_holders_fn: Any,
    dispatch_query_shard_fn: Any,
    build_generalized_query_fn: Any,
    audit_logger_module: Any,
) -> None:
    if ranked and float(getattr(context_result, "retrieval_confidence_score", 0.0) or 0.0) >= 0.65:
        return
    try:
        query = build_generalized_query_fn(task, classification)
        request_relevant_holders_fn(
            classification.get("task_class", "unknown"),
            task.task_summary,
            query_id=query["query_id"],
            limit=3,
        )
        dispatch_query_shard_fn(query, limit=5)
    except Exception as exc:
        audit_logger_module.log(
            "swarm_query_dispatch_error",
            target_id=task.task_id,
            target_type="task",
            details={"error": str(exc)},
        )


def _build_media_candidate(media_analysis: Any) -> dict[str, Any] | None:
    if not getattr(media_analysis, "analysis_text", ""):
        return None
    return {
        "summary": str(media_analysis.analysis_text or "").splitlines()[0][:220] or "Media evidence review",
        "resolution_pattern": [],
        "score": 0.58,
        "source_type": "multimodal_candidate",
        "source_node_id": media_analysis.provider_id,
        "provider_name": media_analysis.provider_id,
        "model_name": media_analysis.provider_id,
        "candidate_id": media_analysis.candidate_id,
    }


def _swarm_reuse_citations(context_snippets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        dict(snippet.get("citation") or {})
        for snippet in list(context_snippets or [])
        if isinstance(snippet, dict) and isinstance(snippet.get("citation"), dict) and snippet.get("citation")
    ]


def _annotate_swarm_reuse_citations(
    citations: list[dict[str, Any]],
    *,
    rendered_via: str,
    response_reason: str,
    selected_shard_id: str = "",
    answer_backed: bool = False,
    quality_backed: bool = False,
    counterfactual_details: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    annotated: list[dict[str, Any]] = []
    for citation in list(citations or []):
        enriched = dict(citation)
        shard_id = str(enriched.get("shard_id") or "").strip()
        is_selected = bool(selected_shard_id) and shard_id == selected_shard_id
        enriched["selected_for_plan"] = is_selected
        enriched["answer_backed"] = bool(is_selected and answer_backed)
        enriched["quality_backed"] = bool(is_selected and quality_backed)
        enriched["rendered_via"] = rendered_via if is_selected else ""
        enriched["response_reason"] = response_reason if is_selected else ""
        if is_selected and counterfactual_details:
            enriched.update(dict(counterfactual_details))
        annotated.append(enriched)
    return annotated


def _selected_remote_shard_id(citations: list[dict[str, Any]]) -> str:
    return next(
        (
            str(citation.get("shard_id") or "").strip()
            for citation in list(citations or [])
            if str(citation.get("kind") or "").strip() == "remote_shard"
            and str(citation.get("shard_id") or "").strip()
        ),
        "",
    )


def _counterfactual_evidence_without_selected_remote_shard(
    evidence: dict[str, Any],
    *,
    selected_shard_id: str,
) -> tuple[dict[str, Any], bool]:
    removed = False
    filtered_context_snippets: list[dict[str, Any]] = []
    for snippet in list(evidence.get("context_snippets") or []):
        if not isinstance(snippet, dict):
            filtered_context_snippets.append(snippet)
            continue
        citation = snippet.get("citation")
        shard_id = ""
        if isinstance(citation, dict):
            shard_id = str(citation.get("shard_id") or "").strip()
        if (
            not removed
            and shard_id
            and shard_id == selected_shard_id
            and str(citation.get("kind") or "").strip() == "remote_shard"
        ):
            removed = True
            continue
        filtered_context_snippets.append(snippet)
    counterfactual_evidence = dict(evidence)
    counterfactual_evidence["context_snippets"] = filtered_context_snippets
    return counterfactual_evidence, removed


def _answer_backed_counterfactual(
    *,
    task: Any,
    classification: dict[str, Any],
    evidence: dict[str, Any],
    persona: Any,
    plan: Any,
    citations: list[dict[str, Any]],
    response_reason: str,
    build_plan_fn: Any,
) -> tuple[str, bool, dict[str, Any]]:
    selected_shard_id = _selected_remote_shard_id(citations)
    grounded = response_reason in _GROUNDED_RESPONSE_REASONS
    details: dict[str, Any] = {
        "counterfactual_tested": False,
        "counterfactual_grounded_response": grounded,
        "counterfactual_confidence_drop": 0.0,
        "counterfactual_winning_source_changed": False,
        "counterfactual_confidence_without_shard": 0.0,
        "counterfactual_primary_source_without_shard": "",
    }
    if not selected_shard_id or not grounded:
        return selected_shard_id, False, details
    counterfactual_evidence, removed = _counterfactual_evidence_without_selected_remote_shard(
        evidence,
        selected_shard_id=selected_shard_id,
    )
    if not removed:
        return selected_shard_id, False, details
    try:
        counterfactual_plan = build_plan_fn(
            task=task,
            classification=classification,
            evidence=counterfactual_evidence,
            persona=persona,
        )
    except Exception as exc:  # pragma: no cover - fail closed on counterfactual errors
        details["counterfactual_error"] = str(exc)
        return selected_shard_id, False, details

    actual_confidence = float(getattr(plan, "confidence", 0.0) or 0.0)
    counterfactual_confidence = float(getattr(counterfactual_plan, "confidence", 0.0) or 0.0)
    actual_sources = [
        str(source).strip()
        for source in list(getattr(plan, "evidence_sources", []) or [])
        if str(source).strip()
    ]
    counterfactual_sources = [
        str(source).strip()
        for source in list(getattr(counterfactual_plan, "evidence_sources", []) or [])
        if str(source).strip()
    ]
    confidence_drop = max(0.0, actual_confidence - counterfactual_confidence)
    winning_source_changed = bool(actual_sources or counterfactual_sources) and actual_sources[:1] != counterfactual_sources[:1]
    details.update(
        {
            "counterfactual_tested": True,
            "counterfactual_confidence_drop": confidence_drop,
            "counterfactual_winning_source_changed": winning_source_changed,
            "counterfactual_confidence_without_shard": counterfactual_confidence,
            "counterfactual_primary_source_without_shard": counterfactual_sources[0] if counterfactual_sources else "",
        }
    )
    answer_backed = confidence_drop >= 0.05 or winning_source_changed
    return selected_shard_id, answer_backed, details


def _quality_backed_remote_shard(
    *,
    answer_backed: bool,
    response_text: str,
    response_class: str,
    surface: str,
    rendered_via: str,
) -> tuple[bool, dict[str, bool]]:
    render_metrics = inspect_user_response_shape(
        response_text,
        surface=surface,
        rendered_via=rendered_via,
    )
    quality_backed = (
        bool(answer_backed)
        and str(response_class or "").strip().lower() not in _NON_QUALITY_RESPONSE_CLASSES
        and not bool(render_metrics.get("planner_leakage"))
        and not bool(render_metrics.get("template_fallback_hit"))
    )
    return quality_backed, {
        "planner_leakage": bool(render_metrics.get("planner_leakage")),
        "template_fallback_hit": bool(render_metrics.get("template_fallback_hit")),
    }


_ESCALATION_ATTEMPTED_KEY = "_final_answer_author_escalation_attempted"


def _escalate_to_a_certified_author(
    agent: Any,
    model_execution: Any,
    *,
    task: Any,
    classification: Any,
    interpretation: Any,
    context_result: Any,
    persona: Any,
    force_model: bool,
    surface: str,
    model_source_context: Any,
    effective_input: str,
) -> Any:
    """Re-run this turn against a certified author when the one that answered is not one.

    The publication gate (`core.finalization` -> `core.final_answer_authorship`) will refuse
    bytes an uncertified model wrote with nothing behind them. Refusing is correct and it is
    also the LAST resort: when the operator has a certified model configured and permitted, the
    right outcome is that model writing the answer, not a refusal.

    So the escalation happens HERE -- after the author is known, which is the first moment the
    question can be asked of a real identity, and before the bytes are used. Bounded to one
    extra attempt per turn by a stamp on the turn's own context, and pinned to the exact model
    the authority named, so the escalation cannot wander into a third model. Both identities
    reach execution truth: the requested model is never erased from the record, because a
    silent substitution is a defect in its own right.

    Fail-soft in one direction only: any error here leaves the original result standing, and
    the publication gate still refuses it. The escalation can add an answer; it can never
    remove the refusal.
    """

    context = model_source_context if isinstance(model_source_context, dict) else None
    if context is None or context.get(_ESCALATION_ATTEMPTED_KEY):
        return model_execution
    served_provider_id = str(getattr(model_execution, "provider_id", "") or "").strip()
    if not served_provider_id or not bool(getattr(model_execution, "used_model", False)):
        return model_execution
    try:
        from core.final_answer_authorship import escalation_target, record_authorship_decision
        from core.model_registry import ModelRegistry

        registry = ModelRegistry()
        manifests = list(registry.list_manifests(enabled_only=True))
        served_manifest = next(
            (
                manifest
                for manifest in manifests
                if str(getattr(manifest, "provider_id", "") or "") == served_provider_id
            ),
            None,
        )
        if served_manifest is None:
            return model_execution
        from core.auto_local_only_mode import turn_is_local_only

        decision = escalation_target(
            served_provider_id=served_provider_id,
            served_manifest=served_manifest,
            request_text=str(effective_input or ""),
            candidates=manifests,
            local_only=bool(turn_is_local_only(context)),
        )
        if decision is None:
            return model_execution
        context[_ESCALATION_ATTEMPTED_KEY] = True
        escalated_context = {
            **context,
            "requested_model": decision.selected_model,
            "model_selection": "authorship_escalation",
        }
        escalated = agent.memory_router.resolve(
            task=task,
            classification=classification,
            interpretation=interpretation,
            context_result=context_result,
            persona=persona,
            force_model=force_model,
            surface=surface,
            source_context=escalated_context,
        )
    except Exception:
        return model_execution
    if not bool(getattr(escalated, "used_model", False)) or not str(
        getattr(escalated, "output_text", "") or ""
    ).strip():
        # The certified model did not answer. The original stands and the gate refuses it --
        # a failed escalation must not be able to turn a refusal into a served answer.
        return model_execution
    with suppress(Exception):
        # `supersedes_author` only here, and only past the two checks above: the certified
        # model was reached AND returned publishable output, so it is the author of the bytes
        # this turn will serve. Every other recorder is raise-only.
        record_authorship_decision(
            context,
            decision,
            served_model=decision.selected_model,
            supersedes_author=True,
        )
    return escalated


def execute_grounded_turn(
    agent: Any,
    *,
    task: Any,
    effective_input: str,
    classification: dict[str, Any],
    interpreted: Any,
    persona: Any,
    session_id: str,
    source_context: dict[str, object] | None,
    adapt_user_input_fn: Any,
    ingest_media_evidence_fn: Any,
    build_media_context_snippets_fn: Any,
    orchestrate_parent_task_fn: Any,
    build_plan_fn: Any,
    render_response_fn: Any,
    explicit_planner_style_requested_fn: Any,
    should_use_planner_renderer_fn: Any,
    request_relevant_holders_fn: Any,
    dispatch_query_shard_fn: Any,
    build_generalized_query_fn: Any,
    feedback_engine_module: Any,
    policy_engine_module: Any,
    from_task_result_fn: Any,
    append_conversation_event_fn: Any,
    audit_logger_module: Any,
) -> dict[str, Any]:
    surface = str((source_context or {}).get("surface", "cli")).lower()
    is_chat_surface = surface in {"channel", "openclaw", "api"}
    from core.agent_runtime.intent_claims import (
        ActionPolicy,
        action_policy_from_context,
    )

    action_forbidden = (
        action_policy_from_context(source_context) is ActionPolicy.FORBIDDEN
    )
    from core.canonical_project_knowledge import retrieve_canonical_passages
    from core.plain_task_routing import plain_task_kind

    canonical_grounding_required = bool(
        is_chat_surface
        and retrieve_canonical_passages(effective_input, limit=1)
    )
    if canonical_grounding_required:
        source_context = {
            **dict(source_context or {}),
            "canonical_grounding_required": True,
        }
    plain_kind = (
        plain_task_kind(effective_input)
        if is_chat_surface and not canonical_grounding_required
        else ""
    )
    if plain_kind:
        from core.tiered_context_loader import empty_tiered_context_result

        context_result = empty_tiered_context_result(
            task_id=str(getattr(task, "task_id", "")),
            reason="plain_task_minimal_no_context",
            source_context=source_context,
        )
    else:
        # The answering model is resolved only AFTER this context exists (memory_router.resolve
        # below), but the loader needs that model's window NOW to size its layer budgets. The
        # router pre-resolves the ranking it will run later and stamps the planned window into
        # the loader's copy of the context; an unknown window stamps nothing and the declared
        # budgets hold.
        context_result = agent.context_loader.load(
            task=task,
            classification=classification,
            interpretation=interpreted,
            persona=persona,
            session_id=session_id,
            source_context=agent.memory_router.stamp_planned_context_window(
                source_context,
                classification=classification,
            ),
        )
    if is_chat_surface:
        from core.bootstrap_context import canonical_runtime_transcript
        from core.context_retrieval import (
            capsule_exact_response,
            get_last_retrieval_telemetry,
            inject_retrieved,
            is_capsule_exact_recall_query,
            resolve_semantic_access_policy,
            update_retrieval_telemetry,
            validate_capsule_exact_response,
        )

        if is_capsule_exact_recall_query(effective_input):
            try:
                semantic_policy = resolve_semantic_access_policy(
                    session_id=session_id,
                    source_context=source_context,
                )
            except (TypeError, ValueError):
                semantic_policy = None
            canonical_runtime_transcript(
                session_id=session_id,
                source_context=dict(source_context or {}),
                current_user_text=effective_input,
                access_policy=semantic_policy,
            )
            telemetry_after_transcript = get_last_retrieval_telemetry()
            if telemetry_after_transcript.get("capsule_mode") == "not_run":
                inject_retrieved(
                    session_id,
                    effective_input,
                    [{"role": "user", "content": effective_input}],
                    access_policy=semantic_policy,
                    source_context=source_context,
                )
        capsule_response = capsule_exact_response(
            effective_input,
            get_last_retrieval_telemetry(),
            session_id=session_id,
        )
        if capsule_response:
            vetted_response = agent._sanitize_user_chat_text(
                capsule_response,
                response_class=agent.ResponseClass.GENERIC_CONVERSATION,
            )
            if validate_capsule_exact_response(
                vetted_response,
                effective_input,
                session_id=session_id,
            ):
                update_retrieval_telemetry(
                    response_control="capsule_exact",
                    model_calls=0,
                )
                result = agent._fast_path_result(
                    session_id=session_id,
                    user_input=effective_input,
                    response=vetted_response,
                    confidence=1.0,
                    source_context=source_context,
                    reason="capsule_exact_pre_model",
                )
                result.update(
                    {
                        "route": "capsule_exact_pre_model",
                        "route_reason": "session_authorized_exact_recall",
                        "route_skips": ["model", "web", "tool_loop"],
                        "fast_path_hit": True,
                        "response_control": {"mode": "capsule_exact"},
                    }
                )
                return result
    ranked = context_result.local_candidates
    curiosity_result = None
    curiosity_plan_candidates: list[dict[str, Any]] = []
    curiosity_context_snippets: list[dict[str, Any]] = []
    # An explicit no-action instruction is a stronger boundary than the
    # ordinary remote-fetch preference.  Curiosity is itself a live action,
    # so it must not run merely because routing labelled a conversational turn
    # as research before the policy was applied.
    _remote_fetch_disabled = action_forbidden or explicit_remote_fetch_disabled(
        source_context
    )
    # Don't run curiosity (a live web search) before answering a high-confidence local/memory/
    # exact-recall/personal question — that turned a ~3s memory recall into a ~40s turn. Explicit
    # search/latest/public requests and genuinely-external questions still roam.
    from core.stipulated_frame import stipulated_frame_active

    _skip_curiosity_local = stipulated_frame_active(
        effective_input,
        source_context=source_context,
    ) or should_skip_curiosity_for_local_answer(
        effective_input,
        active_mission_present=bool(_safe_active_mission_slots(session_id)),
        local_memory_hit=float(getattr(context_result, "retrieval_confidence_score", 0.0) or 0.0) >= 0.45,
        # M2c -- the canonical authority outranks the memory heuristic on this one arm.
        #
        # Measured on a served daemon with an explicitly selected cloud model: "in two sentences,
        # what is the current state of Rust compiler performance work right now" reads as a local
        # RECALL, so once earlier turns about Rust sat in memory the whole turn skipped retrieval
        # -- no search, no receipt, and no refusal row either, because nothing was ever proposed
        # for the M2b door to refuse -- and the selected model answered a live question from its
        # weights. The identical question about a topic the home had never seen retrieved four
        # Brave sources and bound them. So the suppressor was familiarity, not the model pin.
        #
        # Read through the same authority every other gate on this turn reads, so the decision
        # that governs retrieval is the decision the guards downstream will judge against.
        current_information_required=_canonical_current_required(effective_input, source_context),
    )
    if _remote_fetch_disabled:
        curiosity_result = CuriosityResult(
            enabled=False,
            mode="off",
            reason=(
                "action_policy_forbidden"
                if action_forbidden
                else "remote_fetch_disabled"
            ),
        )
    elif _skip_curiosity_local:
        # Set a disabled result so the post-response fallback roam (below) also does not run —
        # otherwise the skipped web search would just move later in the same turn.
        curiosity_result = CuriosityResult(enabled=False, mode="off", reason="local_answer_no_research")
    elif agent._should_frontload_curiosity(
        query_text=effective_input,
        classification=classification,
        interpretation=interpreted,
    ):
        curiosity_result = agent.curiosity.maybe_roam(
            task=task,
            user_input=effective_input,
            classification=classification,
            interpretation=interpreted,
            context_result=context_result,
            session_id=session_id,
        )
        curiosity_plan_candidates, curiosity_context_snippets = agent._curiosity_candidate_evidence(
            curiosity_result.candidate_ids
        )
    tool_execution = agent._maybe_execute_model_tool_intent(
        task=task,
        effective_input=effective_input,
        classification=classification,
        interpretation=interpreted,
        context_result=context_result,
        persona=persona,
        session_id=session_id,
        source_context=source_context,
        surface=surface,
    )
    # Tool-required gate: a this-machine FACT question must be answered by a real tool. If no
    # GROUNDED tool result was produced (tool loop returned nothing, or a non-executed failure
    # like "couldn't map that to a tool"), refuse to fabricate a value -- return an honest
    # blocker naming the capability and stop safely, instead of the vague failure or the model
    # prose path below (the fabrication sink).
    required_capability = (
        None if action_forbidden else local_fact_capability_required(effective_input)
    )
    grounded_tool = (
        tool_execution is not None
        and bool(tool_execution.get("success"))
        and str(tool_execution.get("mode") or "") == "tool_executed"
    )
    if required_capability and not grounded_tool:
        # This turn refused rather than guessing, which is right — but the decision that led here
        # is already in the candidate cache, recorded at model-call time when the only question
        # asked was "is this a well-formed tool intent". A well-formed WRONG choice is stored
        # `valid` at trust 1.0 and then replayed, so the refusal becomes permanent for this exact
        # wording and the user's only escape is to reword.
        #
        # Measured on the deployed build 2026-07-28: "give me a rundown of what lives in
        # ~/Desktop/my-workshop-folder" had the model pick `workspace.list_files` instead of
        # `machine.list_directory`, and then refused identically twice in a row, while the same
        # sentence against a different folder answered correctly. Drop the decision so the next
        # identical ask gets a fresh attempt; the failure itself stays in the audit trail.
        try:
            from core.cache_invalidation import invalidate_failed_turn_decision

            invalidate_failed_turn_decision(
                agent.memory_router.tool_intent_task_hash(
                    task=task,
                    classification=classification,
                    interpretation=interpreted,
                    source_context=source_context,
                ),
                reason="local_fact_no_tool",
            )
        except Exception:
            pass
        blocker = (
            f"I can only answer that by inspecting {required_capability} on your machine, and "
            f"that tool didn't run — so I won't guess a value. If it needs permission, grant it "
            f"and ask again; or name the exact drive/folder and I'll retry."
        )
        return agent._action_fast_path_result(
            task_id=task.task_id,
            session_id=session_id,
            user_input=effective_input,
            response=blocker,
            confidence=0.4,
            source_context=source_context,
            reason="local_fact_no_tool",
            success=False,
            details={"required_capability": required_capability, "tool_backed": False},
            mode_override="tool_failed",
            task_outcome="failed",
            workflow_summary="- local fact required a tool; none executed; refused to fabricate",
        )

    if tool_execution is not None:
        return agent._action_fast_path_result(
            task_id=task.task_id,
            session_id=session_id,
            user_input=effective_input,
            response=tool_execution["response"],
            confidence=float(tool_execution["confidence"]),
            source_context=source_context,
            reason=f"model_tool_intent_{tool_execution['status']}",
            success=bool(tool_execution["success"]),
            details=dict(tool_execution["details"]),
            mode_override=str(tool_execution["mode"]),
            task_outcome=str(tool_execution["task_outcome"]),
            learned_plan=tool_execution.get("learned_plan"),
            workflow_summary=str(tool_execution["workflow_summary"]),
        )

    if not action_forbidden:
        orchestrate_parent_task_fn(
            parent_task_id=task.task_id,
            user_input=effective_input,
            classification=classification,
            environment_tags={
                "os": task.environment_os,
                "shell": task.environment_shell,
                "runtime": task.environment_runtime,
                "version_family": task.environment_version_hint,
            },
            exclude_host_group_hint_hash=None,
        )

    routing_classification, routing_profile = agent._model_routing_profile(
        user_input=effective_input,
        classification=classification,
        interpretation=interpreted,
        source_context=source_context,
    )
    adaptive_research = agent._collect_adaptive_research(
        task_id=task.task_id,
        query_text=effective_input,
        classification=routing_classification,
        interpretation=interpreted,
        source_context=source_context,
        skip_for_local_answer=_skip_curiosity_local,
    )
    model_interpretation = interpreted
    adaptive_web_notes = [dict(note) for note in list(adaptive_research.notes or []) if isinstance(note, dict)]
    if is_chat_surface and (
        adaptive_research.enabled or adaptive_research.tool_gap_note or adaptive_research.admitted_uncertainty
    ):
        # `persist=False`: the argument is a PROMPT (the user's text plus this turn's research
        # observations), and `adapt_user_input` otherwise records it as the session's newest user
        # turn -- raw_input, current_user_goal and active-mission slots all derived from runtime
        # scaffolding. Measured before the flag: the goal for "yes ok my bad, do compare those
        # models" was persisted with 'Grounding observations for this turn...{"channel":
        # "adaptive_research"...}' appended, and replayed into the turns after it. The interpretation
        # it returns is unchanged, so routing and context selection still see the enriched reading.
        model_interpretation = adapt_user_input_fn(
            agent._chat_surface_adaptive_research_model_input(
                user_input=effective_input,
                task_class=str(routing_classification.get("task_class") or "unknown"),
                research_result=adaptive_research,
            ),
            session_id=session_id,
            persist=False,
        )
    # M2 -- RETRIEVAL BEFORE SYNTHESIS.
    #
    # Everything below this point that talks to the model must be able to see what this turn
    # retrieved. At base it could not: the live web lookup sat ~80 lines further down, AFTER
    # `memory_router.resolve` had already produced the publishable bytes. Measured on 0.5.0 and
    # shipped as an open defect with the live-search UI proof -- `model.call_completed` then
    # `web_retrieval_started`, three real dated rows fetched and receipted behind four invented
    # headlines. Ordering is upstream of every grounding predicate: an answer written before its
    # evidence existed cannot be grounded in that evidence, however the predicate scores the text.
    #
    # Applies only to turns the requirements authority ALREADY marks current-information (read,
    # never re-derived, never widened here), and only when nothing was retrieved pre-model
    # already. Every other turn returns `ran=False` after one classifier read: no network, no
    # second model call, no added latency on the DIRECT lane.
    from core.agent_runtime.current_evidence_prefetch import (
        prefetch_current_evidence,
        unavailable_current_answer,
    )

    current_evidence = prefetch_current_evidence(
        agent,
        task=task,
        effective_input=effective_input,
        classification=routing_classification,
        interpretation=interpreted,
        source_context=source_context,
        is_chat_surface=is_chat_surface,
        action_forbidden=action_forbidden,
        remote_fetch_disabled=_remote_fetch_disabled,
        skip_for_local_answer=_skip_curiosity_local,
        already_have_notes=bool(adaptive_web_notes),
        already_collected_notes=adaptive_web_notes,
        # The research lane's own weak-evidence handling. When it is engaged it tells the model
        # in the prompt that the evidence is thin and the answer comes back tentative -- a better
        # outcome than a hard refusal, and the behaviour this lane must not destroy.
        uncertainty_handled=bool(
            getattr(adaptive_research, "admitted_uncertainty", False)
            or getattr(adaptive_research, "enabled", False)
        ),
    )
    if current_evidence.blocking:
        # The turn needed a current observation and the retrieval refused or failed. Calling the
        # model now would be calling it AS IF it had current evidence -- the exact shape that
        # produced the measured fabrication. Return the typed truth instead, before the call.
        return agent._action_fast_path_result(
            task_id=task.task_id,
            session_id=session_id,
            user_input=effective_input,
            response=unavailable_current_answer(effective_input, current_evidence.outcome),
            confidence=0.3,
            source_context=source_context,
            reason=f"current_evidence_{current_evidence.outcome}",
            success=False,
            details={
                "current_information_required": True,
                "retrieval_outcome": current_evidence.outcome,
                "evidence_bound": False,
                "web_retrieval_receipt": dict(current_evidence.receipt),
            },
            mode_override=f"current_evidence_{current_evidence.outcome}",
            task_outcome="failed",
            workflow_summary="- current information required; governed retrieval did not deliver; refused to answer from memory",
        )
    if current_evidence.bound:
        adaptive_web_notes = [dict(note) for note in current_evidence.notes]
        # Same `persist=False` reasoning as the adaptive-research prompt above: this argument is a
        # PROMPT, not the user's turn, and persisting it would replay the retrieved rows as the
        # session's newest user text.
        #
        # Two shapes, because they are different situations. When THIS lane performed the
        # retrieval it owns the prompt and builds it. When another pre-model lane already
        # retrieved, that lane framed its own observations and this one appends only the identity
        # manifest -- restating the same rows under a different channel would change what the
        # model is asked without adding anything, and it silently dropped the adaptive-research
        # framing when the classifier widened to cover those turns.
        if current_evidence.model_input:
            model_interpretation = adapt_user_input_fn(
                current_evidence.model_input,
                session_id=session_id,
                persist=False,
            )
        elif current_evidence.prompt_suffix:
            _existing_prompt = str(
                getattr(model_interpretation, "reconstructed_text", "")
                or getattr(model_interpretation, "raw_text", "")
                or effective_input
            )
            model_interpretation = adapt_user_input_fn(
                f"{_existing_prompt}\n\n{current_evidence.prompt_suffix}",
                session_id=session_id,
                persist=False,
            )
    model_source_context = dict(source_context or {})
    if current_evidence.bound:
        # The envelope stamp: exactly which evidence-set and note ids entered THIS call's prompt.
        # It claims `prompt_entry` and nothing else -- whether the answer used them is
        # `core.evidence_binding`'s question, asked later against the final text.
        model_source_context["evidence_synthesis_binding"] = dict(current_evidence.binding)
    if routing_profile.get("task_envelope"):
        model_source_context["task_envelope"] = dict(routing_profile.get("task_envelope") or {})
    if routing_profile.get("task_role"):
        model_source_context["task_role"] = str(routing_profile.get("task_role") or "")
    if routing_profile.get("plain_task_kind"):
        model_source_context["plain_task_kind"] = str(routing_profile.get("plain_task_kind") or "")
    if routing_classification.get("task_class"):
        model_source_context["task_class"] = str(routing_classification.get("task_class") or "")
    # An audit turn does NOT get the one open-ended call that let a cloud model monologue for
    # 8,320 tokens over the whole evidence pile (measured 2026-08-01, same model finished the same
    # audit under a stepped harness). The stepped driver makes small bounded calls — nominate the
    # bug with a checked citation, prove it with a failing test, synthesize — and returns a
    # decision-shaped result; everything downstream (guards, receipts, honesty checks) is unchanged.
    # None means the stepped lane could not run (no evidence, no checkable finding) and the turn
    # falls back to the single-shot lane exactly as before.
    # M2b -- SYNTHESIS BEGINS HERE, so the requirement decision closes HERE.
    #
    # M1 froze the record at the response-guard seam, ~350 lines below this point and well after
    # the answering call had already produced the publishable bytes. That left a window the
    # measured defect lives inside: a retrieval scheduled AFTER the answer was written was still
    # PRE-freeze, so the canonical door authorized it, and the turn ended holding a green receipt
    # for evidence no answer had ever seen. The freeze is only teeth if it precedes the call it
    # is protecting.
    #
    # Placed above the stepped-audit branch so it covers BOTH answering lanes -- the stepped
    # driver's bounded calls and the single-shot resolve below are equally "synthesis".
    # `begin_synthesis_freeze` is idempotent, so the later M1 call at the guard seam keeps
    # working unchanged and keeps reading the same frozen record.
    #
    # What this does NOT do: stop the retrieval M2 hoists ABOVE this line. That one completes and
    # binds before the freeze, which is the whole ordering this pair of lanes exists to enforce.
    from core.execution_requirements import begin_synthesis_freeze as _begin_synthesis_freeze

    _begin_synthesis_freeze(source_context, effective_input)
    stepped_execution = None
    if bool((source_context or {}).get("workspace_audit_evidence_collected")):
        from core.agent_runtime.stepped_audit import run_stepped_audit

        # The ORIGINAL source_context, not the model copy: the prove step's tool receipts must land
        # on the dict `_guard_final_result` reads, or the completion-claim honesty gate judges the
        # report against an empty receipt list and rewrites it to "I did not create those files"
        # (measured live 2026-08-01, drive 6).
        stepped_execution = run_stepped_audit(
            agent,
            task=task,
            effective_input=effective_input,
            source_context=source_context if isinstance(source_context, dict) else model_source_context,
            session_id=session_id,
        )
    model_execution = stepped_execution if stepped_execution is not None else agent.memory_router.resolve(
        task=task,
        classification=routing_classification,
        interpretation=model_interpretation,
        context_result=context_result,
        persona=persona,
        force_model=is_chat_surface,
        surface=surface,
        source_context=model_source_context,
    )
    model_execution = _escalate_to_a_certified_author(
        agent,
        model_execution,
        task=task,
        classification=routing_classification,
        interpretation=model_interpretation,
        context_result=context_result,
        persona=persona,
        force_model=is_chat_surface,
        surface=surface,
        model_source_context=model_source_context,
        effective_input=effective_input,
    )
    # An ordinary investigation leaves a subject behind, so its follow-up has something to continue.
    # Recorded HERE rather than at the front door because the paths are only known once the tools
    # have run: the capsule carries what the runtime actually opened, not what the request asked
    # for. Audit turns are excluded — they have their own capsule, and a generic investigation
    # record standing in for one would let a follow-up resume an audit as though it were a browse.
    if isinstance(source_context, dict) and not bool(
        source_context.get("workspace_audit_evidence_collected")
    ):
        with suppress(Exception):
            from core.agent_runtime.active_finding import scope_from_context
            from core.agent_runtime.investigation_session import (
                record_investigation,
                resolved_paths_from_observations,
            )

            _inv_paths = resolved_paths_from_observations(
                source_context.get("runtime_tool_observations")
            )
            if _inv_paths:
                _inv_scope = scope_from_context(source_context, session_id=session_id)
                record_investigation(
                    session_id=session_id,
                    project_id=_inv_scope.project_id,
                    chat_id=_inv_scope.chat_id,
                    origin_turn_id=_inv_scope.request_id,
                    workspace_root=str(
                        source_context.get("workspace")
                        or source_context.get("workspace_root")
                        or ""
                    ),
                    subject=effective_input,
                    resolved_paths=_inv_paths,
                )
    # Model execution owns a private request dictionary.  Return only the
    # redacted provenance and output-control receipts to the enclosing turn.
    if isinstance(source_context, dict):
        for key in (
            "context_manifest_id",
            "context_manifest_trace_id",
            "ordinary_chat_output_policy",
            "provider_manifest_links",
            "response_control",
        ):
            if key in model_source_context:
                source_context[key] = model_source_context[key]
    model_candidate = model_execution.as_plan_candidate()
    media_source_context = dict(source_context or {})
    if is_chat_surface and "fetch_text_references" not in media_source_context:
        media_source_context["fetch_text_references"] = True
    media_evidence = ingest_media_evidence_fn(
        task_id=task.task_id,
        trace_id=task.task_id,
        user_input=effective_input,
        source_context=media_source_context,
    )
    try:
        media_analysis = agent.media_pipeline.analyze(
            task_id=task.task_id,
            task_summary=task.task_summary,
            evidence_items=media_evidence,
        )
    except Exception as exc:
        # A candidate-only review of external media is never the turn. Measured 2026-09-02: a
        # provider 400 raised out of this call ended the whole turn as "Task failed: 400 Client
        # Error" although the answering lane had not even been reached. The failure is recorded
        # on the result, and the turn goes on to answer from what it has.
        from core.media_analysis_pipeline import MediaAnalysisResult

        media_analysis = MediaAnalysisResult(
            False,
            evidence_items=list(media_evidence),
            reason=f"analysis_error:{type(exc).__name__}",
        )
    media_context_snippets = build_media_context_snippets_fn(media_analysis.evidence_items or media_evidence)
    media_candidate = _build_media_candidate(media_analysis)

    web_notes = list(adaptive_web_notes)
    # A high-confidence local answer must not fall back to a live web lookup either (e.g. "current"
    # in "what is the current mission?" would otherwise trip the fresh-info web-notes path).
    #
    # `not current_evidence.ran` keeps the hoist a MOVE rather than an addition. Without it a
    # current-information turn whose retrieval came back empty would search a second time here --
    # same query, same provider, same cost, and no better chance of an answer than the attempt
    # that just failed. One turn gets one governed retrieval.
    if (
        not web_notes
        and not current_evidence.ran
        and not action_forbidden
        and not _skip_curiosity_local
        and not _remote_fetch_disabled
    ):
        web_notes = agent._collect_live_web_notes(
            task_id=task.task_id,
            query_text=effective_input,
            classification=classification,
            interpretation=interpreted,
            source_context=source_context,
        )
    web_plan_candidates = agent._web_note_plan_candidates(
        query_text=effective_input,
        classification=classification,
        web_notes=web_notes,
    )

    _dispatch_background_swarm_query(
        task=task,
        classification=classification,
        ranked=ranked,
        context_result=context_result,
        request_relevant_holders_fn=request_relevant_holders_fn,
        dispatch_query_shard_fn=dispatch_query_shard_fn,
        build_generalized_query_fn=build_generalized_query_fn,
        audit_logger_module=audit_logger_module,
    )

    visibility = execution_visibility(model_execution)
    evidence = {
        "candidates": sorted(
            curiosity_plan_candidates + web_plan_candidates,
            key=lambda item: float(item.get("score") or 0.0),
            reverse=True,
        )[:3],
        "local_candidates": ranked[:3],
        "swarm_candidates": context_result.swarm_metadata[:3],
        "model_candidates": [candidate for candidate in [model_candidate, media_candidate] if candidate],
        "context_snippets": curiosity_context_snippets + context_result.context_snippets() + media_context_snippets,
        "assembled_context": context_result.assembled_context(),
        "prompt_assembly_report": context_result.report.to_dict(),
        "model_execution": {
            "source": model_execution.source,
            "provider_id": model_execution.provider_id,
            "used_model": model_execution.used_model,
            "cache_hit": model_execution.cache_hit,
            "candidate_id": model_execution.candidate_id,
            "trust_score": model_execution.trust_score,
            "validation_state": model_execution.validation_state,
            "details": {
                "model_was_attempted": bool((getattr(model_execution, "details", None) or {}).get("model_was_attempted")),
            },
            "model_call_id": str(model_execution.details.get("model_call_id") or ""),
            "response_id": str(model_execution.details.get("response_id") or ""),
            "locality": visibility["residency"],
            "active_inference": visibility["active_inference"],
        },
        "media_analysis": {
            "used_provider": media_analysis.used_provider,
            "provider_id": media_analysis.provider_id,
            "candidate_id": media_analysis.candidate_id,
            "reason": media_analysis.reason,
            "evidence_count": len(media_analysis.evidence_items or media_evidence),
        },
        "adaptive_research": adaptive_research.to_dict(),
        "external_media_evidence": media_analysis.evidence_items or media_evidence,
        "web_notes": web_notes,
    }
    swarm_reuse_citations = _swarm_reuse_citations(evidence["context_snippets"])

    plan = build_plan_fn(
        task=task,
        classification=classification,
        evidence=evidence,
        persona=persona,
    )
    gate = agent._default_gate(plan, classification)

    planner_style_requested = explicit_planner_style_requested_fn(effective_input)
    planner_renderer_allowed = should_use_planner_renderer_fn(
        surface=surface,
        output_mode=str(routing_profile.get("output_mode") or ""),
        user_input=effective_input,
    )
    model_final_text = (
        agent._chat_surface_model_final_text(model_execution)
        if is_chat_surface
        else agent._model_final_response_text(model_execution)
    )
    model_final_answer_hit = bool(model_final_text)
    rendered_via = "model_final_wording"
    response_reason = "grounded_model_response"

    from core.plain_task_routing import ordinary_multi_part_answer_complete

    multi_part_answer_incomplete = bool(
        model_final_answer_hit
        and plain_kind == "multi_part_qa"
        and not ordinary_multi_part_answer_complete(effective_input, model_final_text)
    )
    # Whether the PROVIDER ran out of output room, rather than the model choosing to omit parts.
    # The router already stamped its verdict on the turn context: an answer cut at the output
    # boundary carries the honest "(Incomplete: ...)" notice on its own bytes. Swapping that
    # partial for the degraded hint (the 2026-09-15 incident's turn 3) threw away every usable
    # sentence the user paid the input tokens for; the omission arm below keeps that behavior for
    # answers that finished and simply missed parts.
    provider_output_truncated = False
    if isinstance(source_context, dict):
        _control = source_context.get("response_control")
        if isinstance(_control, dict):
            _completion = _control.get("provider_completion")
            if isinstance(_completion, dict):
                provider_output_truncated = bool(
                    (_completion.get("final") or {}).get("incomplete")
                )

    if planner_renderer_allowed and (not is_chat_surface or bool(model_execution.used_model)):
        response = render_response_fn(
            plan,
            gate,
            persona,
            input_interpretation=interpreted,
            prompt_assembly_report=context_result.report,
            surface=surface,
            allow_planner_style=planner_style_requested,
        )
        rendered_via = "reasoning_engine"
        response_reason = "grounded_plan_response"
        model_final_answer_hit = False
    elif model_final_answer_hit and (
        _internal_payload(model_final_text)
        or (multi_part_answer_incomplete and not provider_output_truncated)
    ):
        # Scaffolding is not an answer. This is the lane a bound workspace audit falls through to
        # once its evidence is collected (turn_frontdoor hands the verdict to the model), so it is
        # exactly where a cloud model's raw monologue reached the operator on 2026-08-01 -- 7,905
        # tokens of "The user wants me to audit..." cut off mid-sentence, and on the next turn a
        # `["app.py", "test_app.py", "README.md"]# Security Regression Test Project` payload.
        response = agent._chat_surface_honest_degraded_response(
            model_execution,
            user_input=effective_input,
            interpretation=interpreted,
        )
        rendered_via = (
            "multi_part_incomplete_refused"
            if multi_part_answer_incomplete
            else "internal_payload_refused"
        )
        response_reason = (
            "model_omitted_requested_parts"
            if multi_part_answer_incomplete
            else "model_returned_internal_payload"
        )
        model_final_answer_hit = False
        if multi_part_answer_incomplete and isinstance(source_context, dict):
            response_control = dict(source_context.get("response_control") or {})
            response_control["fallback_applied"] = True
            response_control["fulfillment_outcome"] = {
                "fulfillment_status": "failed",
                "failure_stage": "output_validation",
                "failure_codes": ["missing_requested_parts"],
                "retryable": True,
            }
            source_context["response_control"] = response_control
    elif model_final_answer_hit:
        response = model_final_text
    elif is_chat_surface:
        response = agent._chat_surface_honest_degraded_response(
            model_execution,
            user_input=effective_input,
            interpretation=interpreted,
        )
        rendered_via = "honest_degraded_chat"
        response_reason = "chat_model_unavailable_degraded"
        # This turn produced a RUNTIME NOTICE, not an answer. An answer-shape contract must not be
        # applied to it: measured on the served surface, a turn ending "Output exactly one word"
        # trimmed the model-unavailable explanation to the single token `nemotron-3, which tells
        # the reader nothing and looks like a crash. The user constrained the ANSWER; there is no
        # answer to constrain.
        if isinstance(source_context, dict):
            source_context["runtime_notice_not_an_answer"] = True
    else:
        response = render_response_fn(
            plan,
            gate,
            persona,
            input_interpretation=interpreted,
            prompt_assembly_report=context_result.report,
            surface=surface,
            allow_planner_style=planner_style_requested,
        )
        rendered_via = "reasoning_engine"
        response_reason = "grounded_plan_response"

    if rendered_via in {
        "honest_degraded_chat", "internal_payload_refused", "multi_part_incomplete_refused",
    } or response_reason == "chat_model_unavailable_degraded":
        from core.agent_runtime.response import _mark_turn_unfulfilled

        _mark_turn_unfulfilled(source_context, response_reason)
        if isinstance(source_context, dict):
            control = dict(source_context.get("response_control") or {})
            control.setdefault("fulfillment_outcome", {
                "fulfillment_status": "failed", "failure_stage": "output_validation",
                "failure_codes": [response_reason], "retryable": True,
            })
            source_context["response_control"] = control

    # Verifier gate -> visible caveat: if an independent verifier flagged this answer
    # (needs_review set by the router), prepend a short draft caveat so the user knows
    # it did not pass verification. Non-blocking; a passing / verifier-unavailable /
    # simple answer is returned unchanged.
    response = apply_verifier_draft_caveat(
        response,
        model_execution,
        user_text=effective_input,
        source_context=source_context,
    )

    execution_result = {"mode": "advice_only"}
    outcome = feedback_engine_module.evaluate_outcome(task, plan, gate, execution_result)
    feedback_engine_module.apply(task, evidence, outcome)

    if curiosity_result is None and not action_forbidden:
        if adaptive_research.queries_run:
            # THIS TURN ALREADY SEARCHED, AND THE ANSWER IS ALREADY WRITTEN. A second, independent
            # web exploration here cannot change the response above it -- it runs after
            # `render_response_fn` -- so everything it costs is added to the reader's wait.
            #
            # Measured on the served daemon (build 9e437955, "What is the latest stable version of
            # Python?", session openclaw:bad12c7a7befa): adaptive research ran two searches
            # (48 s each, both `unavailable`), the model answered at 14:37:24, and the turn then
            # sat for 96 s -- `curiosity_roam_completed` at 14:39:00, `task_completed` in the same
            # second, nothing else in the window but 30 s maintenance ticks, and `sample(1)`
            # during it showing the process blocked in `__semwait_signal`/`poll` with
            # `_PyEval_EvalFrameDefault` at 88 samples. 221 s end to end for a turn whose content
            # existed at 124 s.
            #
            # This is the rule the skip above already states -- "otherwise the skipped web search
            # would just move later in the same turn" -- applied to the case where the search DID
            # happen. A turn that did no research still roams here.
            curiosity_result = CuriosityResult(
                enabled=False, mode="off", reason="adaptive_research_already_searched"
            )
        else:
            curiosity_result = agent.curiosity.maybe_roam(
                task=task,
                user_input=effective_input,
                classification=classification,
                interpretation=interpreted,
                context_result=context_result,
                session_id=session_id,
            )
    workflow_summary = agent._task_workflow_summary(
        classification=classification,
        context_result=context_result,
        model_execution=evidence["model_execution"],
        media_analysis=evidence["media_analysis"],
        curiosity_result=curiosity_result.to_dict(),
        gate_mode=gate.mode,
    )
    if outcome.is_success and outcome.is_durable:
        shard = from_task_result_fn(task, plan, outcome)
        if policy_engine_module.validate_learned_shard(shard):
            agent._store_local_shard(
                shard,
                origin_task_id=task.task_id,
                origin_session_id=session_id,
            )

    public_export = agent._maybe_publish_public_task(
        task=task,
        classification=classification,
        assistant_response=response,
        session_id=session_id,
    )
    topic_id = str((public_export or {}).get("topic_id") or "").strip()
    if topic_id:
        agent.hive_activity_tracker.note_watched_topic(session_id=session_id, topic_id=topic_id)
    turn_result = agent._turn_result(
        response,
        agent._grounded_response_class(gate=gate, classification=classification),
        workflow_summary=workflow_summary,
        debug_origin="grounded_plan",
        allow_planner_style=planner_style_requested,
    )
    agent._apply_interaction_transition(session_id, turn_result)
    response = agent._decorate_chat_response(
        turn_result,
        session_id=session_id,
        source_context=source_context,
    )
    # The evidence SUBSTITUTION that used to sit here is gone. It replaced the model's answer with a
    # rendering of the retrieved notes whenever the answer did not echo them, and it was wrong twice:
    #
    #   1. It overrode honest degradations. "latest telegram bot api updates" retrieved one POINTER
    #      ("the docs are the canonical source"), the runtime correctly said it could not ground an
    #      answer, and the substitution replaced that with "Here is what the sources returned" --
    #      a non-answer dressed as an answer. Three honest-degradation tests failed.
    #   2. Worse, and what ended it: "Water freezes at what temperature at standard pressure?" -- a
    #      static fact needing no retrieval -- ran a search anyway (web_calls=5), the search returned
    #      a Florida drop-box address, and the substitution replaced a CORRECT answer with that.
    #
    # The condition it fired on -- evidence exists and the answer does not echo it -- is true of every
    # turn where retrieval was simply irrelevant, which is not a defect at all. Narrowing it once was
    # not enough, so it is removed rather than guarded a second time.
    #
    # What survives is the ACCOUNTING (1df07094): the binding verdict still rides the payload, and a
    # turn that genuinely dropped evidence is still reported partially_fulfilled/retryable instead of
    # fulfilled. That half told the truth without deciding what the user should read, and it caused
    # no regression in the full suite.
    # And the other side of the same coin: the turn needed a current observation and made NONE, yet
    # the answer states one. `core.live_data_continuation` closed the route that produced the
    # measured cases; this closes the class, on every path that leaves a live question with the
    # model. Fires only when all three hold together -- the REQUEST required a current observation
    # (read from the requirements authority, never from the reply's wording, which is what keeps
    # every static and historical fact untouched), the turn observed nothing, and the answer
    # asserts a measured value or attributes a source.
    from core.execution_requirements import begin_synthesis_freeze, requirements_for
    from core.unsourced_current_claim import (
        inspect_unsourced_current_claim,
        unverified_current_answer,
    )

    # M1: synthesis has produced its answer, so the turn's requirement decision is now
    # CLOSED -- no later signal may widen or flip it -- and the guard below reads the
    # same frozen record every scheduler read (`requirements_for` returns the record for
    # this text), never a fresh reclassification that could disagree with what scheduled.
    begin_synthesis_freeze(source_context, effective_input)
    try:
        _requires_current = bool(
            requirements_for(
                effective_input, source_context=source_context
            ).current_information_required
        )
    except Exception:
        _requires_current = False
    _current_claim = inspect_unsourced_current_claim(
        answer=response,
        requires_current=_requires_current,
        notes=web_notes,
        session_id=str(session_id or ""),
        turn_id=str((source_context or {}).get("cancel_turn_id") or ""),
        source_context=source_context,
    )
    if _current_claim.unsupported:
        response = unverified_current_answer(effective_input)

    # A question about this turn's token cost is answered from MEASURED values, appended after the
    # decoration so nothing downstream can drop it and everything downstream — transcript,
    # checkpoint, truth metrics — sees exactly what the operator sees. Asked on 2026-08-01, the
    # model invented "~2,800 tokens"; the provider had reported the real figure on the same
    # response, and the per-component ledger was already built. See core/token_usage_receipt.py.
    response = append_token_usage_receipt(
        response,
        user_input=effective_input,
        model_execution=model_execution,
        report=context_result.report,
    )
    selected_shard_id, answer_backed, counterfactual_details = _answer_backed_counterfactual(
        task=task,
        classification=classification,
        evidence=evidence,
        persona=persona,
        plan=plan,
        citations=swarm_reuse_citations,
        response_reason=response_reason,
        build_plan_fn=build_plan_fn,
    )
    quality_backed, response_shape = _quality_backed_remote_shard(
        answer_backed=answer_backed,
        response_text=response,
        response_class=turn_result.response_class.value,
        surface=surface,
        rendered_via=rendered_via,
    )
    swarm_reuse_citations = _annotate_swarm_reuse_citations(
        swarm_reuse_citations,
        rendered_via=rendered_via,
        response_reason=response_reason,
        selected_shard_id=selected_shard_id,
        answer_backed=answer_backed,
        quality_backed=quality_backed,
        counterfactual_details=counterfactual_details,
    )
    reuse_outcome_records = record_shard_reuse_outcomes(
        citations=swarm_reuse_citations,
        task_id=str(task.task_id or ""),
        session_id=session_id,
        task_class=str(classification.get("task_class") or ""),
        response_class=turn_result.response_class.value,
        success=bool(outcome.is_success),
        durable=bool(outcome.is_durable),
        details={
            "source_surface": str((source_context or {}).get("surface") or ""),
            "source_platform": str((source_context or {}).get("platform") or ""),
            "model_execution_source": str(model_execution.source or ""),
            "gate_mode": str(gate.mode or ""),
            "planner_leakage": bool(response_shape.get("planner_leakage")),
            "template_fallback_hit": bool(response_shape.get("template_fallback_hit")),
        },
    )
    # Did the evidence this turn paid for reach the answer it is about to ship? This is the only
    # seam where the retrieved notes and the FINAL response text are both in scope (`response` is
    # last assigned above). Record the verdict rather than acting on it here: translating it into
    # fulfilment truth belongs to `core.runtime_task_outcome`, which owns that vocabulary.
    #
    # The verdict rides the RETURNED PAYLOAD, not `source_context`. Provider execution crosses a
    # shallow copy of the request context (same reason `core.turn_model_call_ledger` and
    # `core.auto_local_only_mode` both refuse to use a ContextVar), so a stamp written here lands
    # on the copy and the API front door reads `base_context` and never sees it -- measured: the
    # trace still said `fulfilled` with the stamp in place. The payload is the turn's own return
    # value and crosses the boundary intact.
    from core.evidence_binding import inspect_evidence_binding

    evidence_binding = inspect_evidence_binding(
        answer=response,
        notes=web_notes,
        request_text=effective_input,
    ).as_dict()
    # M3 stage CLAIM_SUPPORTED, plus the reasoning lane's own answer to "did a model write
    # this?". Recorded on the turn's grounding lifecycle for the publication gate, which is
    # the only seam that can refuse bytes.
    #
    # The map recorded here is ADVISORY and the gate recomputes it: between this line and
    # finalization the text still passes through the sealing transforms, the honesty passes and
    # the response controls, so a verdict about the text as it stands here is a verdict about
    # bytes that may not be the ones served. What this call contributes that the gate cannot get
    # for itself is `model_authored` -- `model_execution` is in scope here and nowhere later.
    #
    # The ORIGINAL `source_context`, not the model copy: the lifecycle id was stamped on this
    # dict, and the copy the provider travelled through has already been discarded.
    with suppress(Exception):
        from core.grounding_lifecycle import (
            record_claim_support,
            record_computed_values,
            record_typed_observations,
        )

        record_claim_support(
            source_context,
            payload=evidence_binding.get("claim_support") or {},
            model_authored=bool(model_execution.used_model),
        )
        # Requirement 7: a typed lane's successful observations mint support of their own.
        # `core.observation_evidence` owns the success question, so a failed lookup contributes
        # nothing here for exactly the reason it contributes nothing to `turn_ran_observations`.
        for _channel in ("runtime_tool_observations", "fresh_data_retrieval_receipts"):
            record_typed_observations(
                source_context, entries=(source_context or {}).get(_channel)
            )
        # A deterministic computation's rendered line mints support of its own kind: not an
        # observation (nothing was looked up), but matchable, so a gated mixed turn keeps its
        # computed clause when a sibling clause needed current information.
        record_computed_values(
            source_context,
            entries=(source_context or {}).get("runtime_computed_values"),
        )

    agent._emit_chat_truth_metrics(
        task_id=task.task_id,
        reason=response_reason,
        response_text=response,
        response_class=turn_result.response_class.value,
        source_context=source_context,
        rendered_via=rendered_via,
        fast_path_hit=False,
        model_inference_used=bool(model_execution.used_model),
        model_final_answer_hit=model_final_answer_hit,
        model_execution_source=str(model_execution.source or ""),
        tool_backing_sources=[
            source
            for source in (
                "web_lookup" if web_notes else "",
                "media_analysis" if media_analysis.used_provider else "",
            )
            if source
        ],
    )

    audit_logger_module.log(
        "agent_run_once_complete",
        target_id=task.task_id,
        target_type="task",
        details={
            "mode": gate.mode,
            "confidence": plan.confidence,
            "swarm_candidates_present": len(ranked),
            "understanding_confidence": interpreted.understanding_confidence,
            "input_quality_flags": interpreted.quality_flags,
            "context_retrieval_confidence": context_result.report.retrieval_confidence,
            "context_budget_used": context_result.report.total_tokens_used(),
            "swarm_reuse_citation_count": len(swarm_reuse_citations),
            "swarm_reuse_outcome_count": len(reuse_outcome_records),
            "model_execution_source": model_execution.source,
            "model_provider_id": model_execution.provider_id,
            "media_analysis_reason": media_analysis.reason,
            "media_evidence_count": len(media_analysis.evidence_items or media_evidence),
            "curiosity_mode": curiosity_result.mode,
            "curiosity_reason": curiosity_result.reason,
            "curiosity_candidate_count": len(curiosity_result.candidate_ids),
            "adaptive_research_enabled": adaptive_research.enabled,
            "adaptive_research_reason": adaptive_research.reason,
            "adaptive_research_strategy": adaptive_research.strategy,
            "adaptive_research_actions": list(adaptive_research.actions_taken),
            "adaptive_research_uncertainty": adaptive_research.admitted_uncertainty,
            "source_surface": (source_context or {}).get("surface"),
            "source_platform": (source_context or {}).get("platform"),
        },
    )

    # A-1/R-5 ORDERING REPAIR: A7 finalization has MOVED OUT of this seam.
    # This lane used to finalize HERE — before the sealing context applied
    # exact-response-control/honesty transforms and before A2 admission —
    # producing committed bytes != admitted bytes and an sr='' citation
    # (pipeline backwards). Correct order now: transforms inside
    # _seal_semantic_result -> A2 admit -> finalize at the transport shim
    # (`_response_commit`), which binds EXACTLY the admitted bytes.
    from core.response_provenance import strip_provenance_footer

    canonical_response = strip_provenance_footer(response)

    append_conversation_event_fn(
        session_id=session_id,
        user_input=effective_input,
        assistant_output=canonical_response,
        source_context=source_context,
    )
    # The companion consumes this event before the outer trace is published.
    # Use the same fulfillment authority for provider refusals and output validation.
    from core.runtime_task_outcome import terminal_fulfillment_outcome

    terminal_outcome = terminal_fulfillment_outcome({
        "response": canonical_response,
        "model_execution": evidence["model_execution"],
        "evidence_binding": evidence_binding,
    }, source_context=source_context)
    agent._emit_runtime_event(
        source_context,
        event_type="task_failed" if terminal_outcome.is_unfulfilled else "task_completed",
        message=(
            "Task did not meet its completion requirements."
            if terminal_outcome.is_unfulfilled
            else f"Completed task with final response: {agent._runtime_preview(response)}"
        ),
        task_id=task.task_id,
        task_class=str(classification.get("task_class") or "unknown"),
        **({"fulfillment_outcome": terminal_outcome.to_dict()} if terminal_outcome.is_unfulfilled else {}),
    )
    agent._finalize_runtime_checkpoint(
        source_context,
        status="completed",
        final_response=canonical_response,
    )

    from core.context_retrieval import get_last_retrieval_telemetry
    from core.remote_fetch_policy import remote_fetch_attempt_count

    route_telemetry = dict(get_last_retrieval_telemetry() or {})
    capsule_mode = str(route_telemetry.get("capsule_mode") or "none")
    model_selected = str(model_execution.model_name or "")
    prompt_eval_count = int(route_telemetry.get("prompt_eval_count") or 0)
    # The ledger is the count; the other two are floors under it, kept so a lane that never reached
    # the router seam cannot report fewer calls than it used to.
    #
    # What they were, and why neither could be the count: `route_telemetry` is context-retrieval
    # CAPSULE telemetry, which knows nothing about the answering call, and `used_model` is a
    # BOOLEAN -- so a turn that called a provider for a tool choice and again for the answer
    # reported 1, and a turn whose call failed reported 0 with the wall clock already spent.
    from core.turn_model_call_ledger import turn_model_calls

    # Read from the model copy: that is the dict provider execution actually travelled through, and
    # it carries the same stamped ledger id as the caller's context.
    model_calls = max(
        turn_model_calls(model_source_context),
        int(route_telemetry.get("model_calls") or 0),
        int(bool(model_execution.used_model)),
    )
    web_calls = max(
        int(route_telemetry.get("web_calls") or 0),
        int(remote_fetch_attempt_count()),
    )
    output_mode = str(routing_profile.get("output_mode") or "")
    if capsule_mode == "distilled":
        selected_route = f"capsule:{model_selected or 'local'}"
        route_reason = "distilled_context_required_model"
    elif is_chat_surface and routing_profile.get("plain_task_minimal"):
        selected_route = f"plain_task_minimal:{model_selected or 'local'}"
        route_reason = "ordinary_plain_text_task"
    elif is_chat_surface and output_mode == "plain_text":
        selected_route = f"model_minimal:{model_selected or 'local'}"
        route_reason = "ordinary_plain_text_chat"
    else:
        selected_route = f"model:{model_selected or 'local'}"
        route_reason = "grounded_model_route"
    route_skips = ["tool_execution"]
    if not web_notes:
        route_skips.append("web")
    if capsule_mode != "distilled":
        route_skips.append("capsule")

    return {
        "task_id": task.task_id,
        "response": response,
        # A-1/R-5: NO commit minted here anymore. The transport shim
        # (`_response_commit`) finalizes AFTER the sealing context's transforms
        # + A2 admission, binding exactly the admitted bytes.
        "mode": gate.mode,
        "confidence": plan.confidence,
        "understanding_confidence": interpreted.understanding_confidence,
        "interpreted_input": effective_input,
        "topic_hints": interpreted.topic_hints,
        "prompt_assembly_report": context_result.report.to_dict(),
        "model_execution": evidence["model_execution"],
        "media_analysis": evidence["media_analysis"],
        "research_controller": adaptive_research.to_dict(),
        "curiosity": curiosity_result.to_dict(),
        "backend": agent.backend_name,
        "device": agent.device,
        "session_id": session_id,
        "source_context": dict(source_context or {}),
        "workflow_summary": workflow_summary,
        "response_class": turn_result.response_class.value,
        "swarm_reuse_citations": swarm_reuse_citations,
        "swarm_reuse_outcome_count": len(reuse_outcome_records),
        "route": selected_route,
        "route_reason": route_reason,
        "route_skips": route_skips,
        "model_selected": model_selected,
        "model_residency": visibility,
        "prompt_eval_count": prompt_eval_count,
        "model_calls": model_calls,
        "web_calls": web_calls,
        "evidence_binding": evidence_binding,
        "capsule_mode": capsule_mode,
        "exact_response_control": False,
        "fast_path_hit": False,
    }
