from __future__ import annotations

import os
from typing import Any

from core.execution.constants import asks_about_contents, explicit_path_in


def _preflight_for_text(text: str, supplied: Any | None) -> Any:
    """Use one raw-bound preflight, rebuilding only for legacy/direct callers.

    A supplied result for another rendering is never reused: its spans would resolve to plausible
    substrings of the wrong turn. The production caller supplies the post-resume authoritative raw
    text, while direct unit callers retain the old function signatures.
    """

    raw = str(text or "")
    if supplied is not None and str(getattr(getattr(supplied, "raw", None), "text", "")) == raw:
        return supplied
    from core.semantic.preflight import semantic_preflight

    return semantic_preflight(raw)


def _stable_category_error_admitted(
    preflight: Any,
    *,
    resolved_ambiguous_reference: bool = False,
    allow_quoted_reviewed_claim: bool = False,
) -> bool:
    """Whether the reviewed category registry may answer in this discourse frame."""

    from core.semantic.preflight import (
        SemanticFrame,
        StructuralIssueCode,
        admit_deterministic_candidate,
        authoritative_whole_turn_candidate,
    )

    candidate = authoritative_whole_turn_candidate(
        preflight,
        route_id="stable_category_error_reference",
        source_id="core.stable_category_error_reference.v2",
        allowed_frames=frozenset(
            {
                SemanticFrame.REAL,
                SemanticFrame.UNKNOWN,
                *([SemanticFrame.QUOTED] if allow_quoted_reviewed_claim else []),
            }
        ),
        resolved_issues=(
            frozenset({StructuralIssueCode.AMBIGUOUS_REFERENCE})
            if resolved_ambiguous_reference
            else frozenset()
        ),
    )
    return admit_deterministic_candidate(preflight, candidate).admitted


def closed_semantic_contract_covers_turn(
    text: str,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
    preflight: Any | None = None,
) -> bool:
    """Whether a reviewed zero-planner contract can finish this whole turn.

    This is an admission predicate, not a second renderer.  The normal front door still creates
    the answer and receipt.  It exists so the agent can run that front door before buying a
    planner call for a turn whose complete answer is already deterministic.
    """

    from core.agent_runtime.answer_coverage import (
        FAMILY_CURRENCY,
        coverage_for,
        slice_binding_is_unsafe,
    )
    from core.agent_runtime.fast_paths_currency import currency_fast_path
    from core.closed_world_semantic_contract import closed_world_semantic_response
    from core.currency_chat_context import chat_currency_codes
    from core.hypothetical_currency_contract import hypothetical_currency_response
    from core.raw_output_contract import parse_raw_output_contract
    from core.stable_acronym_reference import stable_acronym_reference_response
    from core.stable_category_error_reference import (
        stable_category_error_response,
        stable_quoted_false_premise_response,
    )
    from core.stable_currency_reference import stable_currency_reference_response
    from core.stable_exact_semantics import stable_exact_semantic_response
    from core.stable_file_format_standard_reference import stable_file_format_standard_response
    from core.stable_metaphor_reference import stable_metaphor_reference_response
    from core.stable_si_unit_reference import stable_si_unit_reference_response
    from core.stable_term_contract import stable_structured_definition
    from core.stable_type_mismatch_reference import stable_type_mismatch_response
    from core.underspecified_label_contract import underspecified_label_response

    raw = str(text or "")
    from core.agent_runtime.fast_paths_utility import heartbeat_poll_fast_path

    # An idle protocol poll already has a complete deterministic answer. Its
    # conditional ACK belongs to the file read, not to an independent planner task.
    if heartbeat_poll_fast_path(raw, source_context=source_context) is not None:
        return True
    semantic_preflight = _preflight_for_text(raw, preflight)
    literal = parse_raw_output_contract(raw)
    if literal is not None and literal.exact_text is not None:
        return True
    category_error = stable_category_error_response(raw)
    if category_error is not None and _stable_category_error_admitted(
        semantic_preflight,
        resolved_ambiguous_reference=stable_type_mismatch_response(raw) is not None,
        allow_quoted_reviewed_claim=stable_quoted_false_premise_response(raw) is not None,
    ):
        return True
    if any(
        resolver(raw) is not None
        for resolver in (
            stable_acronym_reference_response,
            hypothetical_currency_response,
            stable_currency_reference_response,
            stable_exact_semantic_response,
            stable_file_format_standard_response,
            stable_metaphor_reference_response,
            stable_si_unit_reference_response,
            stable_structured_definition,
            closed_world_semantic_response,
            underspecified_label_response,
        )
    ):
        return True

    coverage = coverage_for(raw, FAMILY_CURRENCY)
    if not coverage.covers_whole_turn or any(
        slice_binding_is_unsafe(raw, slice_id) for slice_id in coverage.consumed
    ):
        return False
    from core.agent_runtime.answer_coverage import fused_cross_domain_slices

    if fused_cross_domain_slices(raw):
        # A slice carrying TWO KINDS of work ("weather in rome rn, 100 usd to rub, …" —
        # currency ∥ live_info on one comma-run slice with no clause punctuation) cannot be
        # honestly closed by one of them: the other domain's clauses vanish behind the green
        # gate (measured live 2026-08-29, Q001 — three of four clauses dropped). Decline the
        # closed contract; the decomposing lanes own genuinely fused turns.
        return False
    from core.conductor.operations import fx_chain_shape

    if fx_chain_shape(raw):
        # A chained second leg ("...to gbp and then to ETH") is TWO conversions:
        # the fast path serves the first and discloses the second as unclaimed
        # (measured live, operator turn 4, 2026-08-30). Same law as the fused
        # slice above — the decomposing lanes own it; the conductor chains the
        # legs so the second consumes the first's converted amount.
        return False
    try:
        from core.agent_runtime.demand_ownership import (
            LANE_CURRENCY,
            lane_may_claim_whole_turn,
        )

        if not lane_may_claim_whole_turn(raw, LANE_CURRENCY):
            # R1e — the slice-grain whole-turn grant above cannot see a demand
            # boundary inside one punctuation slice: "convert 100 usd to eur and
            # explain what a hedge fund is" is ONE slice, two demand units, and the
            # closed pass served the conversion while the question vanished
            # (measured at base). The demand-unit mint is the finer authority: a
            # strict-subset currency claim declines the closed contract and the
            # decomposing seams own the turn.
            return False
    except Exception:
        pass
    return currency_fast_path(
        raw,
        chat_codes=chat_currency_codes(session_id=session_id, source_context=source_context),
        conversation_history=list((source_context or {}).get("conversation_history") or []),
    ) is not None


def _memory_recall_has_priority(
    query_text: str,
    *,
    session_id: str,
    access_policy: Any | None,
) -> bool:
    """Defer a generic utility fast path when scoped memory can answer the turn.

    This is deliberately a routing guard, not an answer generator.  A stored
    candidate keeps the normal provider/context path in charge so the response
    remains model-generated and the final context manifest records the memory
    that was actually selected.
    """
    lowered = " ".join(str(query_text or "").casefold().split())
    if not any(
        marker in lowered
        for marker in (
            "remember",
            "keep in mind",
            "stored",
            "memory",
            "for this chat",
            "asked you to",
            "told you",
        )
    ):
        return False
    try:
        from core.persistent_memory import has_relevant_memory_candidate

        return has_relevant_memory_candidate(
            query_text,
            session_id=session_id,
            access_policy=access_policy,
        )
    except (TypeError, ValueError):
        return False


def _currency_reply(
    text: str,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any] | None:
    """The ONE way this front door asks the currency lane anything."""
    from core.agent_runtime.fast_paths_currency import currency_fast_path
    from core.currency_chat_context import chat_currency_codes
    from core.currency_intent import fx_conversion_intent, fx_rate_lookup_intent
    from core.fresh_data.fx import FrankfurterFxProvider, retrieve_fx_quote
    from core.remote_fetch_policy import remote_fetch_allowed_by_context
    from core.retrieval_constraints import analyze_retrieval_constraints

    extra: dict[str, Any] = {
        "chat_codes": chat_currency_codes(session_id=session_id, source_context=source_context),
        "conversation_history": list((source_context or {}).get("conversation_history") or []),
    }
    constraints = analyze_retrieval_constraints(text)
    # P0 POLICY CONSERVATION — the child slice's own reading unioned with the
    # parent's frozen set (the canonical request the context carries): a
    # conversion child under a "No web." parent takes the same refusal this
    # gate gives a prohibited whole turn, instead of fetching Frankfurter.
    from core.turn_prohibitions import conserve_retrieval_constraints

    constraints = conserve_retrieval_constraints(constraints, source_context)
    request = fx_conversion_intent(text, chat_codes=extra["chat_codes"]) or fx_rate_lookup_intent(
        text, chat_codes=extra["chat_codes"]
    )
    source = dict(source_context or {})
    from core import policy_engine

    web_runtime_enabled = policy_engine.allow_web_fallback()
    # ROUTE-PARITY GATE: the live FX retrieval is a public read-only network retrieval -- the same
    # action class the model route crosses `decide_tool_call` for when it fetches (web.fetch). The
    # lane now takes its permission decision from the ONE authority before opening a socket, so a
    # mode that denies the model route the fetch denies this lane too (Plan mode), while modes that
    # allow it keep today's behavior unchanged.
    from core.authorized_tool_execution import authorize_runtime_tool
    from core.mode_permission_policy import PermissionEffect

    fx_permission = authorize_runtime_tool(
        "web.fetch", {"url": ""}, source_context=source,
    )
    remote_allowed = (
        remote_fetch_allowed_by_context(source)
        and web_runtime_enabled
        and not constraints.forbids_all_tools
        and not constraints.forbids_external_retrieval
        and fx_permission is not None
        and fx_permission.effect is PermissionEffect.ALLOW
    )
    needs_live_rate = request is not None and getattr(request, "supplied_rate", None) is None
    if needs_live_rate and remote_allowed:
        base = getattr(getattr(request, "source", None), "code", "") or getattr(
            getattr(request, "base", None), "code", ""
        )
        quote = getattr(getattr(request, "target", None), "code", "") or getattr(
            getattr(request, "quote", None), "code", ""
        )
        if base and quote:
            providers = source.get("fx_providers")
            if not isinstance(providers, (list, tuple)):
                configured = source.get("fx_provider")
                if configured is not None:
                    providers = (configured,)
                else:
                    fetcher = source.get("fx_fetch_json")
                    now = source.get("fx_now")
                    providers = (
                        FrankfurterFxProvider(
                            fetch_json=fetcher if callable(fetcher) else None,
                            **({"now": now} if callable(now) else {}),
                        ),
                    )
            try:
                timeout_s = float(source.get("live_lookup_timeout_s") or 8.0)
            except (TypeError, ValueError):
                timeout_s = 8.0
            result = retrieve_fx_quote(
                base,
                quote,
                providers=tuple(providers),
                source_context=source_context,
                authorized=remote_allowed,
                timeout_s=timeout_s,
            )
            if result.available:
                extra["live_rate"] = result.rate
                extra["rate_asof"] = result.observed_at or result.retrieved_at

    reply = currency_fast_path(text, **extra)
    if (
        reply is not None
        and reply.get("grounded") == "no_rate_declined"
        and (constraints.forbids_all_tools or constraints.forbids_external_retrieval)
    ):
        reply = {
            **reply,
            "response": (
                "A current exchange rate is a live observation. Verifying it requires external "
                "retrieval, which this turn explicitly forbids, so I will not substitute a rate "
                "from training data. Give me a rate and I can perform the Decimal arithmetic "
                "exactly without retrieving anything."
            ),
            "grounded": "retrieval_prohibited_declined",
        }
    elif (
        reply is not None
        and request is not None
        and reply.get("grounded") == "no_rate_declined"
        and remote_fetch_allowed_by_context(source)
        and not web_runtime_enabled
    ):
        reply = {
            **reply,
            "response": (
                "A current exchange rate is a live observation, but external retrieval is "
                "disabled by the runtime policy. I will not substitute a rate from training data."
            ),
            "grounded": "retrieval_disabled_declined",
        }
    elif (
        reply is not None
        and request is not None
        and reply.get("grounded") == "no_rate_declined"
        and fx_permission is not None
        and fx_permission.effect is not PermissionEffect.ALLOW
    ):
        # The permission authority denied the retrieval (Plan mode, a project deny): name the
        # authority's own verdict rather than implying the network was down.
        reply = {
            **reply,
            "response": (
                "A current exchange rate is a live observation, and the permission controller "
                f"does not permit external retrieval in this mode. {fx_permission.reason} "
                "I will not substitute a rate from training data."
            ),
            "grounded": "retrieval_denied_by_permission",
            "permission": {
                "effect": fx_permission.effect.value,
                "mode": fx_permission.mode.value,
                "actions": [action.value for action in fx_permission.actions],
                "reason": str(fx_permission.reason or ""),
            },
        }
    return reply


def _attach_currency_grounding(
    source_context: dict[str, object] | None,
    text: str,
    *,
    session_id: str,
) -> None:
    """Carry currency facts forward when the currency lane correctly declines to answer."""
    from core.agent_runtime.fast_paths_currency import attach_currency_grounding
    from core.currency_chat_context import chat_currency_codes

    attach_currency_grounding(
        source_context,
        text,
        chat_codes=chat_currency_codes(session_id=session_id, source_context=source_context),
        conversation_history=list((source_context or {}).get("conversation_history") or []),
    )


def explicit_model_owns_semantic_turn(source_context: dict[str, object] | None) -> bool:
    """Whether the caller explicitly pinned this turn to one concrete model.

    Control/security commands still run locally before this gate.  Everything semantic after it
    belongs to the selected model and its tool loop; otherwise a local keyword fast path can answer
    a cloud-pinned turn without the selected model ever seeing the request.
    """

    from core.auto_local_only_mode import is_auto_selection

    requested = str((source_context or {}).get("requested_model") or "").strip().lower()
    return bool(requested and not is_auto_selection(requested))


#: Which signal this call site is allowed to arbitrate. The gate exists because the two signals want
#: opposite positions in the front door: competing readings must be settled BEFORE the deterministic
#: lanes run (one of them would claim a turn that is not theirs), and a near-miss must be settled
#: AFTER them (they answer most near-misses for free, and asking a model first is a call bought to
#: rediscover an operation the runtime already owns). One function, two call sites, no duplication.
_ARBITRATE_ON_AMBIGUITY = "ambiguity"
_ARBITRATE_ON_NEAR_MISS = "near_miss"


def handle_turn_frontdoor(
    agent: Any,
    *,
    raw_user_input: str,
    effective_input: str,
    normalized_input: str,
    source_surface: str,
    session_id: str,
    source_context: dict[str, object] | None,
    persona: Any,
    interpreted: Any,
    access_policy: Any | None = None,
    maybe_handle_preference_command_fn: Any,
    set_hive_interaction_state_fn: Any,
    semantic_preflight: Any | None = None,
) -> dict[str, Any]:
    from core.agent_runtime.intent_claims import ActionPolicy, action_policy_from_context

    action_policy = action_policy_from_context(source_context)
    action_forbidden = action_policy is ActionPolicy.FORBIDDEN
    preflight = _preflight_for_text(raw_user_input, semantic_preflight)
    startup_message = agent._startup_sequence_fast_path(effective_input)
    if startup_message:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=startup_message,
                confidence=0.97,
                source_context=source_context,
                reason="startup_sequence_fast_path",
            )
        }

    heavy_model_block = agent._explicit_heavy_model_block_response(effective_input)
    if heavy_model_block:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=heavy_model_block,
                confidence=0.99,
                source_context=source_context,
                reason="explicit_heavy_model_blocked",
            )
        }

    heartbeat_reply = agent._heartbeat_poll_fast_path(
        raw_user_input,
        source_context=source_context,
    )
    if heartbeat_reply:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=heartbeat_reply,
                confidence=0.99,
                source_context=source_context,
                reason="heartbeat_poll_fast_path",
            )
        }

    # Exact literal user directives are already fully determined by the current turn. Sending a
    # JSON-looking ``task: output the word BANANA`` through tool-intent routing made every local
    # provider fail for not calling a tool, then bought a second synthesis pass for text the user
    # had supplied verbatim. Bind only the typed raw-output contract here; ordinary JSON data and
    # payloads claiming a system/assistant role do not produce one.
    from core.closed_world_semantic_contract import closed_world_semantic_response
    from core.hypothetical_currency_contract import hypothetical_currency_response
    from core.raw_output_contract import parse_raw_output_contract
    from core.stable_acronym_reference import stable_acronym_reference_response
    from core.stable_category_error_reference import (
        stable_category_error_response,
        stable_quoted_false_premise_response,
    )
    from core.stable_currency_reference import stable_currency_reference_response
    from core.stable_exact_semantics import stable_exact_semantic_response
    from core.stable_file_format_standard_reference import stable_file_format_standard_response
    from core.stable_metaphor_reference import stable_metaphor_reference_response
    from core.stable_si_unit_reference import stable_si_unit_reference_response
    from core.stable_term_contract import stable_structured_definition
    from core.stable_type_mismatch_reference import stable_type_mismatch_response
    from core.underspecified_label_contract import underspecified_label_response

    literal_contract = parse_raw_output_contract(raw_user_input)
    if literal_contract is not None and literal_contract.exact_text is not None:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=literal_contract.exact_text,
                confidence=1.0,
                source_context=source_context,
                reason="exact_literal_output_contract",
            )
        }

    label_ambiguity = underspecified_label_response(raw_user_input)
    if label_ambiguity is not None:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=label_ambiguity,
                confidence=1.0,
                source_context=source_context,
                reason="underspecified_label_contract",
            )
        }

    closed_world = closed_world_semantic_response(raw_user_input)
    if closed_world is not None:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=closed_world,
                confidence=1.0,
                source_context=source_context,
                reason="closed_world_semantic_contract",
            )
        }

    hypothetical_currency = hypothetical_currency_response(raw_user_input)
    if hypothetical_currency is not None:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=hypothetical_currency,
                confidence=1.0,
                source_context=source_context,
                reason="hypothetical_currency_contract",
            )
        }

    acronym_reference = stable_acronym_reference_response(raw_user_input)
    if acronym_reference is not None:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=acronym_reference,
                confidence=1.0,
                source_context=source_context,
                reason="stable_acronym_reference_contract",
            )
        }

    category_error = stable_category_error_response(raw_user_input)
    if category_error is not None and _stable_category_error_admitted(
        preflight,
        resolved_ambiguous_reference=stable_type_mismatch_response(raw_user_input) is not None,
        allow_quoted_reviewed_claim=(
            stable_quoted_false_premise_response(raw_user_input) is not None
        ),
    ):
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=category_error,
                confidence=1.0,
                source_context=source_context,
                reason="stable_category_error_contract",
            )
        }

    stable_definition = stable_structured_definition(raw_user_input)
    if stable_definition is not None:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=stable_definition,
                confidence=1.0,
                source_context=source_context,
                reason="stable_term_output_contract",
            )
        }

    exact_semantic = stable_exact_semantic_response(raw_user_input)
    if exact_semantic is not None:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=exact_semantic,
                confidence=1.0,
                source_context=source_context,
                reason="stable_exact_semantic_contract",
            )
        }

    metaphor_reference = stable_metaphor_reference_response(raw_user_input)
    if metaphor_reference is not None:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=metaphor_reference,
                confidence=1.0,
                source_context=source_context,
                reason="stable_metaphor_reference_contract",
            )
        }

    file_format_standard = stable_file_format_standard_response(raw_user_input)
    if file_format_standard is not None:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=file_format_standard,
                confidence=1.0,
                source_context=source_context,
                reason="stable_file_format_standard_contract",
            )
        }

    si_unit_reference = stable_si_unit_reference_response(raw_user_input)
    if si_unit_reference is not None:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=si_unit_reference,
                confidence=1.0,
                source_context=source_context,
                reason="stable_si_unit_reference_contract",
            )
        }

    currency_reference = stable_currency_reference_response(raw_user_input)
    if currency_reference is not None:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=currency_reference,
                confidence=1.0,
                source_context=source_context,
                reason="stable_currency_reference_contract",
            )
        }

    # Operator Profile (P1): the ONE seat where a turn's statements about the operator (name,
    # language, timezone, response preferences, signature, default account) meet the memory law.
    # Runs ahead of the preference-command surface and the memory-command lane so neither can
    # write a name or a style silently. Explicit -> persisted + reported (claims the turn when
    # nothing else is asked); stated -> candidate chip (turn continues); weak -> chat-local.
    import core.operator_profile_turn as _profile_turn

    profile_text = raw_user_input
    try:
        profile_outcome = _profile_turn.observe_profile_turn(
            agent,
            raw_user_input,
            session_id=session_id,
            source_context=source_context,
            access_policy=access_policy,
        )
    except Exception:
        profile_outcome = None
    if profile_outcome is not None:
        if profile_outcome.get("result") is not None:
            return {"result": profile_outcome["result"]}
        profile_text = str(profile_outcome.get("remaining_text") or raw_user_input)

    handled, response = maybe_handle_preference_command_fn(effective_input)
    if handled:
        agent._sync_public_presence(
            status=agent._idle_public_presence_status(),
            source_context=source_context,
        )
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=response,
                confidence=0.92,
                source_context=source_context,
                reason="user_preference_command",
            )
        }

    from core.agent_runtime.fast_command_surface import (
        maybe_handle_bare_secret,
        maybe_handle_brakes_command,
        maybe_handle_catalog_refresh_intent,
        maybe_handle_cloud_command,
        maybe_handle_cloud_key_command,
        maybe_handle_cloud_key_status_intent,
        maybe_handle_cloud_model_command,
        maybe_handle_cloud_models_command,
        maybe_handle_cloud_switch_intent,
        maybe_handle_cloud_usage_command,
        maybe_handle_connection_test_intent,
        maybe_handle_free_models_intent,
        maybe_handle_image_key_command,
        maybe_handle_model_lookup_intent,
        maybe_handle_openrouter_intent,
        maybe_handle_spend_cap_explainer_intent,
        maybe_handle_telegram_setup_intent,
    )
    from core.request_trust import request_is_owner_local

    _owner_local = request_is_owner_local(source_context)
    # R1g — the catalog's finalize law, enforced at the one seam every
    # whole-text fast intent reads. The recognizers below are single-request
    # readers living under `turn_frontdoor_deterministic`, which LANE_CATALOG
    # declares single-unit-limited — and at base that declaration was enforced
    # nowhere here: "what free models are on offer and explain entropy briefly"
    # was claimed whole by the free-models intent and the explanation VANISHED
    # (measured on the untouched base, the R1e defect class through a new
    # family). A message minting several demand units is not claimable by any
    # of them; it continues to the composite seams (conductor, demand-owned
    # plan, model fallback), which own whole unit plans. The SECRET-consuming
    # intents below keep reading the full text whatever the mint says: a key
    # pasted beside a question must be consumed here, never handed to a model.
    _single_request_intents_may_claim = True
    try:
        from core.agent_runtime.demand_ownership import lane_may_claim_whole_turn

        _single_request_intents_may_claim = lane_may_claim_whole_turn(
            str(raw_user_input or effective_input), "turn_frontdoor_deterministic"
        )
    except Exception:
        _single_request_intents_may_claim = True
    _intent_input = effective_input if _single_request_intents_may_claim else ""
    # Cloud KEY first: it carries a secret, so it must be consumed here rather than reach a model.
    cloud_key_reply = maybe_handle_cloud_key_command(effective_input, owner_local=_owner_local)
    if cloud_key_reply is not None:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=cloud_key_reply,
                confidence=0.95,
                source_context=source_context,
                reason="cloud_key_command",
            )
        }

    # Image KEY also carries a secret (fal.ai BYOK), so consume it here before any model sees it.
    image_key_reply = maybe_handle_image_key_command(effective_input, owner_local=_owner_local)
    if image_key_reply is not None:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=image_key_reply,
                confidence=0.95,
                source_context=source_context,
                reason="image_key_command",
            )
        }

    # "connect VOOL to Telegram / work from my phone" — guide the bridge setup instead of fumbling it
    # as a file write (the bridge is relay.bridge_workers.telegram_chat_bridge, owner-locked).
    telegram_reply = maybe_handle_telegram_setup_intent(_intent_input, owner_local=_owner_local)
    if telegram_reply is not None:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=telegram_reply,
                confidence=0.95,
                source_context=source_context,
                reason="telegram_setup_intent",
            )
        }

    # Resource governor surface: "why is my mac roaring?" answered with measured numbers, and
    # "free up memory" that actually unloads what is resident. Deterministic reads/actions.
    from core.agent_runtime.fast_paths_governor import (
        maybe_handle_free_memory_command,
        maybe_handle_resource_hog_question,
    )

    free_memory_reply = None if action_forbidden else maybe_handle_free_memory_command(
        _intent_input,
        owner_local=_owner_local,
    )
    if free_memory_reply is not None:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=free_memory_reply,
                confidence=0.97,
                source_context=source_context,
                reason="governor_free_memory",
            )
        }
    hog_reply = maybe_handle_resource_hog_question(_intent_input, owner_local=_owner_local)
    if hog_reply is not None:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=hog_reply,
                confidence=0.96,
                source_context=source_context,
                reason="governor_hog_report",
            )
        }

    # A secret pasted bare (users answer "paste your key" with just the key) — consumed here so it
    # can never reach a model. An OpenRouter-shaped key is sealed as if `cloud key` had been typed.
    bare_secret_reply = maybe_handle_bare_secret(effective_input, owner_local=_owner_local)
    if bare_secret_reply is not None:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=bare_secret_reply,
                confidence=0.95,
                source_context=source_context,
                reason="bare_secret_intercept",
            )
        }

    # "Do we have the key set?" — answered from the credential store, never by the model.
    key_status_reply = maybe_handle_cloud_key_status_intent(_intent_input, owner_local=_owner_local)
    if key_status_reply is not None:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=key_status_reply,
                confidence=0.9,
                source_context=source_context,
                reason="cloud_key_status_intent",
            )
        }

    from core.agent_runtime.fast_command_surface import maybe_handle_cloud_diagnostics_intent

    cloud_diag_reply = maybe_handle_cloud_diagnostics_intent(_intent_input, owner_local=_owner_local)
    if cloud_diag_reply is not None:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=cloud_diag_reply,
                confidence=0.95,
                source_context=source_context,
                reason="cloud_diagnostics_intent",
            )
        }

    cloud_models_reply = maybe_handle_cloud_models_command(_intent_input, owner_local=_owner_local)
    if cloud_models_reply is not None:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=cloud_models_reply,
                confidence=0.95,
                source_context=source_context,
                reason="cloud_models_command",
            )
        }

    cloud_model_reply = maybe_handle_cloud_model_command(_intent_input, owner_local=_owner_local)
    if cloud_model_reply is not None:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=cloud_model_reply,
                confidence=0.95,
                source_context=source_context,
                reason="cloud_model_command",
            )
        }

    cloud_usage_reply = maybe_handle_cloud_usage_command(_intent_input, owner_local=_owner_local)
    if cloud_usage_reply is not None:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=cloud_usage_reply,
                confidence=0.95,
                source_context=source_context,
                reason="cloud_usage_command",
            )
        }

    # "How does the spend cap work / can you guarantee my $5 limit?" — answered from the real
    # policy and ledger. The local model improvises here, and an improvised money guarantee is the
    # worst answer this product can give, so it never reaches a model.
    spend_cap_reply = maybe_handle_spend_cap_explainer_intent(_intent_input, owner_local=_owner_local)
    if spend_cap_reply is not None:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=spend_cap_reply,
                confidence=0.95,
                source_context=source_context,
                reason="spend_cap_explainer_intent",
            )
        }

    # Natural-language ACTIONS (switch / refresh / connection test): these PERFORM the real
    # operation and report its actual outcome — placed after the exact commands (which keep
    # precedence) and before the informational intents, so "lets go with HY3" acts instead of
    # merely describing HY3. Ambiguous/refused cases come back advice_only (no action ran).
    import uuid as _uuid

    _nl_actions = () if action_forbidden else (
        maybe_handle_cloud_switch_intent,
        maybe_handle_catalog_refresh_intent,
        maybe_handle_connection_test_intent,
    )
    for _nl_action in _nl_actions:
        _action = _nl_action(_intent_input, owner_local=_owner_local)
        if _action is None:
            continue
        if _action.get("advice_only"):
            return {
                "result": agent._fast_path_result(
                    session_id=session_id,
                    user_input=effective_input,
                    response=str(_action.get("response") or ""),
                    confidence=0.9,
                    source_context=source_context,
                    reason=str(_action.get("reason") or "cloud_nl_action"),
                )
            }
        return {
            "result": agent._action_fast_path_result(
                task_id=f"cloud-{_uuid.uuid4().hex[:12]}",
                session_id=session_id,
                user_input=effective_input,
                response=str(_action.get("response") or ""),
                confidence=0.9,
                source_context=source_context,
                reason=str(_action.get("reason") or "cloud_nl_action"),
                success=bool(_action.get("success")),
                details=dict(_action.get("details") or {}),
                task_outcome="success" if _action.get("success") else "failed",
            )
        }

    # "What free AIs are on offer?" — answered from the LIVE catalog so the local model cannot
    # invent model ids.
    free_models_reply = maybe_handle_free_models_intent(_intent_input, owner_local=_owner_local)
    if free_models_reply is not None:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=free_models_reply,
                confidence=0.9,
                source_context=source_context,
                reason="free_models_intent",
            )
        }

    # "what about HY3?" — a model question with a real catalog match, answered from live data.
    model_lookup_reply = maybe_handle_model_lookup_intent(_intent_input, owner_local=_owner_local)
    if model_lookup_reply is not None:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=model_lookup_reply,
                confidence=0.9,
                source_context=source_context,
                reason="model_lookup_intent",
            )
        }

    # Natural-language onboarding ("connect to openrouter for me") — answered deterministically
    # so it cannot fall through to the local model, which does not know VOOL's own tooling.
    openrouter_reply = maybe_handle_openrouter_intent(
        _intent_input,
        owner_local=_owner_local,
        requested_model=str((source_context or {}).get("requested_model") or "").strip(),
    )
    if openrouter_reply is not None:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=openrouter_reply,
                confidence=0.9,
                source_context=source_context,
                reason="openrouter_onboarding_intent",
            )
        }

    cloud_reply = maybe_handle_cloud_command(_intent_input, owner_local=_owner_local)
    if cloud_reply is not None:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=cloud_reply,
                confidence=0.95,
                source_context=source_context,
                reason="cloud_escalation_command",
            )
        }

    brakes_reply = maybe_handle_brakes_command(_intent_input, owner_local=_owner_local)
    if brakes_reply is not None:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=brakes_reply,
                confidence=0.95,
                source_context=source_context,
                reason="spend_brakes_command",
            )
        }

    credit_result = agent._maybe_handle_credit_command(
        _intent_input,
        session_id=session_id,
        source_context=source_context,
    )
    if credit_result is not None:
        return {"result": credit_result}

    # A bound workspace audit first gathers deterministic evidence against the authoritative root.
    # The helper returns a result only for a scope refusal.  On success it attaches a typed tool
    # observation and the answering model still owns the verdict; this prevents a keyword script
    # from impersonating the model selected in the composer.
    #
    # ANSWER COVERAGE (see `core.agent_runtime.answer_coverage`). This lane may only END the turn
    # when no other family claims a clause it does not. Measured 2026-08-12 on an eight-request
    # message: `looks_like_code_audit_request` read the WHOLE text as an audit by fusing the verb
    # from the traveler-exchange clause with the filename `app.py` from the StormWatch clause --
    # neither clause is an audit request -- and the General-chat scope refusal it produced was
    # delivered as the entire answer. Four currency questions, a README and a project build were
    # dropped without being refused or named.
    #
    # When the audit really is one clause of several, it is answered against THAT clause's own
    # words and carried forward as a slice answer, so the General-chat refusal still lands on the
    # file/action clause it belongs to and takes nothing else with it.
    from core.agent_runtime.answer_coverage import (
        FAMILY_CURRENCY,
        FAMILY_RECEIPT_LOCATION,
        FAMILY_WORKSPACE_AUDIT,
        coverage_for,
        is_mixed_intent_turn,
        record_slice_answer,
        record_slice_contradiction,
        slice_binding_is_unsafe,
        slice_text,
    )

    # The same contract, asked once for the lanes whose reading this module cannot compute per
    # clause. On a mixed turn NO lane covers the whole message by construction, so any of them
    # returning here ends the turn on a subject the user only partly asked about -- which is how,
    # with the audit lane correctly scoped, "What is TRY? Also audit this project and give me a
    # verdict." was then answered by the folder-overview lane with "The workspace folder is empty."
    # False for an ordinary turn, which is the common path and costs nothing beyond the clause split.
    mixed_turn = is_mixed_intent_turn(effective_input)

    # P0 MIXED-DEMAND — the catalog's finalize law, asked PER LANE.
    #
    # `mixed_turn` above is the FAMILY reading (`is_mixed_intent_turn`): it needs
    # two distinct recognized families on two different clauses. That is strictly
    # weaker than the catalog law, and the gap is not theoretical — it was the
    # measured defect at base 840a2392:
    #
    #     "Return exactly the second line of notes.txt. Calculate 37 x 19.
    #      Decide whether that line describes walking."
    #
    # mints THREE demand units and no family recognizer claims any clause, so
    # `mixed_turn` is False, so the workspace-runtime lane below took the WHOLE
    # turn — while `lane_may_claim_whole_turn(..., "turn_frontdoor_deterministic")`
    # was already returning False for exactly this text. The law existed and the
    # call site asked a different question.
    #
    # `_lane_may_finalize` asks the ONE canonical question — may THIS lane end
    # THIS turn — of the lane that is actually about to finalize, so a lane
    # covering some of the demand cannot end a turn holding the rest. Fail-soft
    # in the pre-existing direction (an unavailable probe grants, never a new
    # veto), exactly like `_single_request_intents_may_claim` above.
    def _lane_may_finalize(lane_id: str) -> bool:
        try:
            from core.agent_runtime.demand_ownership import lane_may_claim_whole_turn

            return bool(
                lane_may_claim_whole_turn(
                    str(raw_user_input or effective_input), lane_id
                )
            )
        except Exception:
            return True

    audit_coverage = coverage_for(effective_input, FAMILY_WORKSPACE_AUDIT)
    if audit_coverage.covers_whole_turn:
        workspace_audit = agent._maybe_handle_workspace_audit_request(
            effective_input,
            session_id=session_id,
            source_surface=source_surface,
            source_context=source_context,
        )
        if workspace_audit is not None:
            return {"result": workspace_audit}
        if bool((source_context or {}).get("workspace_audit_evidence_collected")):
            return {"result": None}
    elif audit_coverage.consumed:
        audit_slice_result = agent._maybe_handle_workspace_audit_request(
            slice_text(effective_input, audit_coverage.consumed),
            session_id=session_id,
            source_surface=source_surface,
            source_context=source_context,
        )
        if audit_slice_result is not None:
            record_slice_answer(
                source_context,
                text=effective_input,
                family=FAMILY_WORKSPACE_AUDIT,
                response=str(audit_slice_result.get("response") or ""),
                # `_fast_path_result` renames its `reason` to `route_reason`; the test doubles in
                # the suite return the keyword as given. Read both so the recorded reason names the
                # actual outcome (`workspace_audit_default_scope_refused`) rather than the family.
                reason=str(
                    audit_slice_result.get("route_reason")
                    or audit_slice_result.get("reason")
                    or "workspace_audit"
                ),
            )
    # Not an audit — so if it continues an ordinary investigation, hand that investigation's subject
    # forward. "Continue from the three files we already identified" had nothing to continue from:
    # the three files were never recorded, the turn was re-planned from scratch, and it answered
    # with keyword hits from a search the operator never asked for (measured 2026-08-07). The
    # capsule is attached as a tool observation rather than as prose, so the subject the model works
    # from is runtime state and is labelled as such.
    if isinstance(source_context, dict):
        from core.agent_runtime.active_finding import scope_from_context
        from core.agent_runtime.investigation_session import (
            continuation_observation,
            investigation_follow_up_resumes,
        )

        _inv_scope = scope_from_context(source_context, session_id=session_id)
        _resumed_investigation = investigation_follow_up_resumes(
            effective_input,
            session_id=session_id,
            project_id=_inv_scope.project_id,
            chat_id=_inv_scope.chat_id,
        )
        if _resumed_investigation is not None:
            _observations = [
                dict(item)
                for item in list(source_context.get("runtime_tool_observations") or [])
                if isinstance(item, dict)
            ]
            _observations.append(continuation_observation(_resumed_investigation))
            source_context["runtime_tool_observations"] = _observations[-12:]
            source_context["investigation_continuation"] = True

    # A concrete model selected in the composer owns the remaining semantic turn. The commands above are
    # deliberately retained because they protect secrets or mutate/read VOOL's control plane.
    # Media, folder, memory, utility, and small-talk fast paths below this line must not
    # silently replace the selected model.  The normal reasoning/tool loop remains responsible for
    # executing grounded actions with that model.
    # A greeting and a trusted runtime fact are answered BEFORE the pinned-model gate below.
    #
    # Selecting a model changes who reasons. It does not mean "hello" should cost a round trip.
    # Measured on the installed app with a cloud model pinned: `hi` took 68.2s, of which the model
    # itself was 1.9s — the remaining 66s was local history reconstruction, summarization, embedding
    # and model-load contention, for a turn whose answer comes from a curated phrase table. A clock
    # question took 72.3s the same way, and was classified `research` on the way past.
    #
    # These two are exactly the cases where the runtime owns the answer outright: a greeting has no
    # reasoning content, and the clock is a fact VOOL holds and no model does. Everything genuinely
    # semantic still belongs to the selected model and stays below the gate.
    # What VOOL can actually do on this machine is a runtime fact about the runtime itself, so it
    # belongs above the gate with the clock and the workspace — and specifically AHEAD of the
    # greeting path, whose help branch also claims "what can you do". Moving smalltalk up without
    # this preempted the grounded capability answer for phrasings like "what can you actually do on
    # this machine right now?", which three contract tests caught immediately.
    # THE CLOCK ITSELF, not merely the clock fact in the prompt.
    #
    # An earlier commit in this same work moved the greeting, capability truth and workspace
    # identity above the gate and its comment spoke of "the clock" — but `_date_time_fast_path` was
    # left at line 586, below the gate, so a pinned cloud model never reached it. The prompt fact
    # made the ANSWER correct; it did nothing for the route. Measured after that commit: a clock
    # question still cost a full provider round trip (2.4-2.9s), and a composite one 83.7s.
    #
    # That comment described work that had not been done, which is the failure mode this whole
    # effort exists to remove. It is corrected here rather than reworded.
    memory_recall_priority = _memory_recall_has_priority(
        normalized_input,
        session_id=session_id,
        access_policy=access_policy,
    )
    # P0 MIXED-DEMAND TERMINAL CLOSURE — the catalog's finalize law, asked by the
    # clock arm before it ends a turn. The clock detector fires on any clock phrase
    # ANYWHERE in the message, so without this gate it claimed the three-demand
    # bench turn ("what time is it in Tokyo? Convert 100 US dollars to euros. And
    # finish with a two-word joke."), served "01:09 JST" and the conversion and the
    # joke vanished — minted 3, satisfied 0, no state at all (measured at base
    # 84bf8b6a on the served surface). `turn_frontdoor_deterministic` is
    # single-unit-limited in LANE_CATALOG; a message minting several demand units
    # belongs to the composite seams, which re-dispatch the clock clause as a
    # single-unit sub-turn this lane may then serve legally.
    _date_time_may_claim = _lane_may_finalize("date_time_fast_path")
    date_time_status = None
    if not memory_recall_priority and _date_time_may_claim:
        date_time_status = agent._date_time_fast_path(
            normalized_input,
            source_surface=source_surface,
            session_id=session_id,
            source_context=source_context,
        )
    if not date_time_status and not memory_recall_priority:
        # A continuation of a clock turn is still a clock turn. Measured on the installed build:
        # "what time is now?" answered locally in ~23ms and the next message "and date?" was billed
        # `cloud | nemotron | 2,078 tok`. The detector above does not fire on a bare fragment, and
        # `_TIME_FOLLOWUP_EXCLUSION_MARKERS` explicitly excludes "date", so the follow-up fell to the
        # provider for a fact the runtime had just supplied.
        from core.agent_runtime.fast_paths_utility import is_runtime_fact_followup

        _recent = agent._recent_utility_context(
            session_id=session_id, source_context=source_context
        )
        if is_runtime_fact_followup(
            normalized_input, recent_utility_kind=str((_recent or {}).get("utility_kind") or "")
        ) and _date_time_may_claim:
            date_time_status = agent._date_time_fast_path(
                "what is the date and time now",
                source_surface=source_surface,
                session_id=session_id,
                source_context=source_context,
            )
    if date_time_status:
        cleaned_date_time_input = str(normalized_input or "").strip().lower().strip(" \t\r\n?!.,")
        requested_timezone, requested_label = agent._extract_utility_timezone(cleaned_date_time_input)
        if not requested_timezone:
            recent_utility_context = agent._recent_utility_context(
                session_id=session_id,
                source_context=source_context,
            )
            requested_timezone, requested_label = agent._contextual_time_followup_timezone(
                cleaned_date_time_input,
                recent_utility_context=recent_utility_context,
            )
        utility_payload: dict[str, Any] = {}
        if "current time" in str(date_time_status or "").lower():
            utility_payload = {
                "utility_kind": "time",
                "timezone": requested_timezone,
                "label": requested_label,
            }
        set_hive_interaction_state_fn(session_id, mode="utility", payload=utility_payload)
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=date_time_status,
                confidence=0.97,
                source_context=source_context,
                reason="date_time_fast_path",
            )
        }

    # A currency CODE's meaning is a fact this runtime holds, like the clock above it — TRY is the
    # Turkish lira regardless of what day it is — so it is answered here rather than bought from a
    # provider. The conversion case sits in the same claim for the opposite reason: leaving
    # "1000 TRY to USD?" to the model is what produced the invented $590-610 rate (QA-050-026),
    # so the turn is claimed and answered with the missing rate named outright. `currency_fast_path`
    # declines any turn that asks to MOVE money, which keeps the negative controls on the ordinary
    # path with their own safeguards.
    #
    # The codes THIS CHAT already settled thread in here (QA-050-027), and comparison follow-ups
    # may need this chat's earlier user turns. The coverage contract says this family may END the
    # turn only when it covers the whole turn; otherwise it records slice answers/grounding and lets
    # the conductor carry the rest.
    currency_coverage = coverage_for(effective_input, FAMILY_CURRENCY)
    currency_binding_poisoned = any(
        slice_binding_is_unsafe(effective_input, slice_id)
        for slice_id in currency_coverage.consumed
    )
    # The slice arm runs for a genuinely mixed turn AND for a multi-clause currency turn whose
    # per-slice answering failed partway: both are "the lane did not take the whole turn".
    whole_turn_answered = False
    from core.agent_runtime.answer_coverage import fused_cross_domain_slices as _fused

    if (
        currency_coverage.covers_whole_turn
        # P0 MIXED-DEMAND TERMINAL CLOSURE — the same finalize law the closed
        # contract above already asks. The slice coverage reads
        # `covers_whole_turn=True` whenever the slicer hands back one slice, so a
        # conversion buried beside two other demands still LOOKS like a pure
        # currency turn; the demand-unit mint is the finer authority (R1e) and
        # must have the veto. Refused here, the slice arm below still records the
        # conversion it can serve and the composite seams own the whole turn.
        and _lane_may_finalize("currency_frontdoor")
        and not currency_binding_poisoned
        and not _fused(effective_input)
    ):
        # The fused-slice guard mirrors the closed-contract pass above: a slice carrying two
        # kinds of work is answered by the slice arm below (which records the conversion for
        # composition), never by a whole-text reply that drops the other domains.
        currency_reply = None
        if len(currency_coverage.consumed) > 1:
            # A turn that is entirely currency but SEVERAL clauses ("1000 EUR to USD. Also 500
            # GBP to JPY.") owns the whole turn per slice, and `_currency_reply` over the fused
            # text answers only the FIRST frame `fx_conversion_intent` returns -- the second
            # conversion was dropped with the coverage gate green (measured 2026-08-19). Answer
            # each owned clause; if any cannot be answered, this lane does not take the whole
            # turn and the slice arm below preserves the part it could serve.
            slice_replies: list[str] = []
            for slice_id in currency_coverage.consumed:
                clause_reply = _currency_reply(
                    slice_text(effective_input, (slice_id,)),
                    session_id=session_id,
                    source_context=source_context,
                )
                if clause_reply is None:
                    slice_replies = []
                    break
                slice_replies.append(str(clause_reply["response"]))
            if slice_replies:
                whole_turn_answered = True
                return {
                    "result": agent._fast_path_result(
                        session_id=session_id,
                        user_input=effective_input,
                        response="\n\n".join(slice_replies),
                        confidence=0.95,
                        source_context=source_context,
                        reason="currency_multislice_fast_path",
                    )
                }
        else:
            currency_reply = _currency_reply(
                effective_input, session_id=session_id, source_context=source_context
            )
        if currency_reply is not None:
            whole_turn_answered = True
            return {
                "result": agent._fast_path_result(
                    session_id=session_id,
                    user_input=effective_input,
                    response=str(currency_reply["response"]),
                    confidence=0.95,
                    source_context=source_context,
                    reason=f"currency_{currency_reply['kind']}_fast_path",
                )
            }
        if len(currency_coverage.consumed) <= 1:
            _attach_currency_grounding(source_context, effective_input, session_id=session_id)
    if not whole_turn_answered and currency_coverage.consumed:
        currency_slices: list[str] = []
        for slice_id in currency_coverage.consumed:
            clause = slice_text(effective_input, (slice_id,))
            if slice_binding_is_unsafe(effective_input, slice_id):
                record_slice_contradiction(
                    source_context,
                    text=effective_input,
                    family=FAMILY_CURRENCY,
                    slice_id=slice_id,
                )
                continue
            clause_reply = _currency_reply(
                clause, session_id=session_id, source_context=source_context
            )
            if clause_reply is not None:
                currency_slices.append(f"{clause}\n{clause_reply['response']}")
            else:
                _attach_currency_grounding(source_context, clause, session_id=session_id)
        if currency_slices:
            record_slice_answer(
                source_context,
                text=effective_input,
                family=FAMILY_CURRENCY,
                response="\n\n".join(currency_slices),
                reason="currency_slice_answers",
            )

    # P0 MIXED-DEMAND TERMINAL CLOSURE — the finalize law for the remaining
    # single-unit-limited deterministic arms (capability truth, smalltalk,
    # workspace identity). `mixed_turn` is the FAMILY reading and is strictly
    # weaker than the demand-unit mint (the measured gap at base 840a2392): the
    # mint is the authority that decides whether one of these may END the turn.
    _single_unit_deterministic_may_claim = _lane_may_finalize(
        "turn_frontdoor_deterministic"
    )
    from core.agent_runtime.answer_coverage import demand_units
    from core.runtime_lane_truth import runtime_lane_question

    status_units = demand_units(effective_input)
    runtime_status_only = bool(status_units) and all(
        runtime_lane_question(unit.text) for unit in status_units
    )
    capability_truth = None if (
        (mixed_turn or not _single_unit_deterministic_may_claim) and not runtime_status_only
    ) else agent._maybe_handle_capability_truth_request(
        effective_input,
        session_id=session_id,
        source_context=source_context,
    )
    if capability_truth is not None:
        return {"result": capability_truth}

    smalltalk = None
    for candidate in (
        ()
        if (mixed_turn or not _single_unit_deterministic_may_claim)
        else (normalized_input, effective_input, raw_user_input)
    ):
        smalltalk = agent._smalltalk_fast_path(
            candidate,
            source_surface=source_surface,
            session_id=session_id,
        )
        if smalltalk:
            break
    if smalltalk:
        smalltalk_phrase = " ".join(str(raw_user_input or normalized_input).lower().split()).strip(" \t\r\n?!.,")
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=smalltalk,
                confidence=0.90,
                source_context=source_context,
                reason="help_fast_path" if smalltalk_phrase in {"what can you do", "help"} else "smalltalk_fast_path",
            )
        }

    # Where a file this chat already WROTE now sits, read from that write's own receipt. Above
    # identity, and so above the arbiter and the machine-read lane, because both measured wrong
    # answers came from there: one turn fell past this door and the model guessed a `/` search for
    # a folder named after the user's typo, another was claimed by identity below and answered with
    # the bound folder's name, never the file. Measurements: `core/action_receipt_location.py`.
    from core.agent_runtime.fast_paths_receipt_location import (
        maybe_handle_action_receipt_location_request,
    )

    # Coverage-scoped like the audit and currency lanes above. "where was this file stored?" next
    # to "create a StormWatch project" is two requests, and answering only the first ended the turn
    # with the build request unmentioned. The receipt answer is still produced -- it is carried
    # forward as a slice answer instead of as the whole reply.
    receipt_coverage = coverage_for(effective_input, FAMILY_RECEIPT_LOCATION)
    # P0 MIXED-DEMAND TERMINAL CLOSURE — the finalize law again: slice coverage
    # cannot see a demand boundary inside one slice, and a whole-turn finalize on
    # a multi-demand message drops the siblings (same measured class as the clock
    # and currency arms). Refused, the request still runs below as a slice answer.
    _receipt_may_claim = _lane_may_finalize("turn_frontdoor_deterministic")
    receipt_location = maybe_handle_action_receipt_location_request(
        agent,
        effective_input if (receipt_coverage.covers_whole_turn and _receipt_may_claim)
        else slice_text(effective_input, receipt_coverage.consumed),
        session_id=session_id,
        source_surface=source_surface,
        source_context=source_context,
    ) if (receipt_coverage.covers_whole_turn or receipt_coverage.consumed) else None
    if receipt_location is not None and not (
        receipt_coverage.covers_whole_turn and _receipt_may_claim
    ):
        record_slice_answer(
            source_context,
            text=effective_input,
            family=FAMILY_RECEIPT_LOCATION,
            response=str(receipt_location.get("response") or ""),
            reason="action_receipt_location",
        )
        receipt_location = None
    if receipt_location is not None:
        receipt_location.update(
            {
                "route": "action_receipt_location",
                "route_reason": "file_location_followup_from_receipt",
                "route_skips": ["model", "capsule", "web", "tool_loop"],
                "fast_path_hit": True,
            }
        )
        return {"result": receipt_location}

    workspace_identity_first = None if (
        mixed_turn or not _single_unit_deterministic_may_claim
    ) else agent._maybe_handle_workspace_identity_request(
        effective_input,
        session_id=session_id,
        source_surface=source_surface,
        source_context=source_context,
    )
    if workspace_identity_first is not None:
        return {"result": workspace_identity_first}

    # Selecting a model changes who REASONS. It does not hand over the runtime.
    #
    # This gate used to `return {"result": None}` outright. That value means "no fast path claimed
    # this turn", so `apps/vool_agent.py` fell straight through to `_prepare_turn_task_bundle` and
    # into the LOCAL builder — the gate written to protect a pinned turn was routing it to local
    # `qwen3:8b`. It also skipped everything below: the machine fast path that owns
    # `machine.inspect_specs`, the live-info/price lookup, the deterministic workspace read, the
    # math path, and the intent arbiter. Measured on the installed build with a cloud model pinned,
    # "what is my machine scpecs?" and "price of sol?" both reached no tool at all.
    #
    # The line is fact versus judgement, not fast versus slow. A machine spec, a file's contents, a
    # live price, a receipt, an arithmetic result and an executed action are facts VOOL owns and
    # verifies; they run for a pinned turn exactly as for any other. A folder OVERVIEW, an
    # evaluative reply and a build are interpretation and language, and those belong to the model
    # the operator selected — so they are the ones this flag skips.
    model_owns_judgement = explicit_model_owns_semantic_turn(source_context)

    # A named skill reaches the skill tools, for the same reason a named PDF reaches the PDF tools.
    # `skill.create`/`validate`/`install` work when called directly and NOTHING routed chat to them:
    # `plan_tool_workflow` returns handled=False for every create and validate phrasing measured, so
    # the only route was a small local model choosing to emit the tool JSON itself. A blind drive of
    # eight skill phrasings created zero skills and hung three times at 300s.
    #
    # Claimed ahead of the hive lane below because one phrasing was not merely uncovered but WRONG:
    # "new skill: qa-wordcount. counts words in a block of text. go ahead and create it." routed
    # deterministically to `hive.create_topic` and posted a research topic titled with the request.
    #
    # And claimed ahead of the AGENTIC-BUILD early return below, which is the subtler half. `skill`
    # is a PROJECT noun in `build_request_intent`, so "create a skill called qa-echo ..." satisfies
    # `looks_like_agentic_build_request` and that gate returns `{"result": None}` -- meaning "no fast
    # path claimed this turn" -- handing a skill-authoring request to the app builder. Measured
    # live: with the lane sitting below it, `new skill: qa-wordcount ...` was drafted in 0s while
    # `create a skill called qa-echo ...` and `make a skill called qa-upper ...` both fell through
    # to a cloud model that then failed to produce anything. Authoring a SKILL is not building an
    # app, however much the sentence looks like one.
    from core.agent_runtime.fast_paths_skill import maybe_handle_skill_request

    skill_turn = maybe_handle_skill_request(
        agent,
        effective_input,
        session_id=session_id,
        source_context=source_context,
    )
    if skill_turn is not None:
        return {"result": skill_turn}

    # An agentic build request ("create an app ... build and verify it in the workspace") must not be
    # intercepted by the keyword fast paths below: a "delete" command in its spec would trip the Hive
    # delete route, and a filename it names would trip the workspace read. When a workspace is present,
    # let it fall through to the reasoning stage, where the builder controller (Build/Auto) or the
    # read-only mode refusal (Ask/Plan) handles it. Command fast paths above still run first.
    from core.agent_runtime.fast_paths_utility import looks_like_agentic_build_request

    if looks_like_agentic_build_request(effective_input) and str(
        (source_context or {}).get("workspace") or (source_context or {}).get("workspace_root") or ""
    ).strip():
        return {"result": None}

    if action_forbidden:
        hive_frontdoor_result, effective_hive_create_draft, _pending_hive_create_confirmation = (
            None,
            None,
            None,
        )
    else:
        hive_frontdoor_result, effective_hive_create_draft, _pending_hive_create_confirmation = agent._maybe_handle_hive_frontdoor(
            raw_user_input=raw_user_input,
            effective_input=effective_input,
            session_id=session_id,
            source_context=source_context,
        )
    if hive_frontdoor_result is not None:
        return {"result": hive_frontdoor_result}

    # Memory commands must receive the user's raw text.  The general input normalizer is allowed
    # to repair routing typos, but it can also rewrite an ordinary fact (for example, the literal
    # phrase "North Star" was normalized to "North start" because ``star`` is close to the
    # routing vocabulary entry ``start``).  Persisting the normalized form silently changes what
    # the user asked us to remember.  Keep normalization for routing/model paths; keep memory
    # capture, correction, and forget targets lossless.
    memory_result = agent._maybe_handle_memory_fast_path(
        profile_text,
        session_id=session_id,
        source_context=source_context,
        access_policy=access_policy,
    )
    if memory_result is not None:
        return {"result": memory_result}

    # Ahead of every builder and file lane, because each of them has its own way of quietly
    # relocating a path it cannot reach: the machine-write lane resolved /tmp/... to ~/Documents,
    # the workspace read lane rebased it by stripping the leading slash, and the builder scaffold
    # fell back to `generated/workspace-starter`. A rooted path the user typed gets one answer --
    # that path, or why not.
    from core.agent_runtime.write_root_honesty import (
        unreachable_write_response,
        unreachable_write_target,
    )

    unreachable_target = unreachable_write_target(effective_input, source_context=source_context)
    if unreachable_target:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=unreachable_write_response(unreachable_target, source_context=source_context),
                confidence=0.95,
                source_context=source_context,
                reason="write_root_unreachable",
            )
        }

    web0_builder_result = None if (model_owns_judgement or action_forbidden) else agent._maybe_handle_web0_builder_fast_path(
        effective_input,
        session_id=session_id,
        source_context=source_context,
    )
    if web0_builder_result is not None:
        return {"result": web0_builder_result}

    ui_command = agent._ui_command_fast_path(normalized_input, source_surface=source_surface)
    if ui_command:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=ui_command,
                confidence=0.97,
                source_context=source_context,
                reason="ui_command_fast_path",
            )
        }

    credit_status = None
    if effective_hive_create_draft is None:
        # The message as written: the detector reads the asking units of the interpretation,
        # and the normalized copy (`effective_input` is the normalizer's text too) has lost the
        # newlines that bound a described list.
        credit_status = agent._credit_status_fast_path(
            str(raw_user_input or effective_input or normalized_input), source_surface=source_surface
        )
    if credit_status:
        receipt_like_credit_query = any(
            marker in str(normalized_input or "").lower()
            for marker in (
                "receipt",
                "receipts",
                "ledger",
                "payout",
                "payouts",
                "recent credits",
            )
        )
        if agent._is_chat_truth_surface(source_context) and not receipt_like_credit_query:
            return {
                "result": agent._chat_surface_model_wording_result(
                    session_id=session_id,
                    user_input=effective_input,
                    source_context=source_context,
                    persona=persona,
                    interpretation=interpreted,
                    task_class="unknown",
                    response_class=agent.ResponseClass.UTILITY_ANSWER,
                    reason="credit_status_model_wording",
                    model_input=agent._chat_surface_credit_status_model_input(
                        user_input=effective_input,
                        credit_snapshot=credit_status,
                    ),
                    fallback_response=credit_status,
                    allow_provider_inference=False,
                )
            }
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=credit_status,
                confidence=0.95,
                source_context=source_context,
                reason="credit_status_fast_path",
            )
        }

    direct_math = None if not _lane_may_finalize("direct_math_fast_path") else (
        agent._direct_math_fast_path(
            normalized_input,
            source_surface=source_surface,
        )
    )
    if direct_math:
        result = agent._fast_path_result(
            session_id=session_id,
            user_input=effective_input,
            response=direct_math,
            confidence=0.99,
            source_context=source_context,
            reason="direct_math_fast_path",
        )
        result.update(
            {
                "route": "arithmetic",
                "route_reason": "pure_arithmetic_expression",
                "route_skips": ["model", "capsule", "web", "tool_loop"],
                "fast_path_hit": True,
            }
        )
        return {
            "result": result
        }

    same_chat_recall = agent._same_chat_transcript_recall_fast_path(
        effective_input,
        source_surface=source_surface,
        source_context=source_context,
    )
    if same_chat_recall is not None:
        result = agent._fast_path_result(
            session_id=session_id,
            user_input=effective_input,
            response=str(same_chat_recall.response),
            confidence=1.0,
            source_context=source_context,
            reason="same_chat_transcript_recall",
        )
        result.update(
            {
                "route": "same_chat_history",
                "route_reason": str(same_chat_recall.route_reason),
                "recall_kind": str(same_chat_recall.recall_kind),
                "recall_found": bool(same_chat_recall.found),
                "route_skips": ["model", "capsule", "web", "tool_loop"],
                "fast_path_hit": True,
            }
        )
        return {"result": result}

    action_history = agent._action_history_honesty_fast_path(
        effective_input,
        source_surface=source_surface,
        session_id=session_id,
        source_context=source_context,
    )
    if action_history:
        result = agent._fast_path_result(
            session_id=session_id,
            user_input=effective_input,
            response=action_history,
            confidence=1.0,
            source_context=source_context,
            reason="action_history_honesty",
        )
        result.update(
            {
                "route": "action_history_honesty",
                "route_reason": "empty_session_action_history",
                "route_skips": ["model", "capsule", "web", "tool_loop"],
                "fast_path_hit": True,
            }
        )
        return {"result": result}

    if action_forbidden:
        machine_download = None
    else:
        machine_download = agent._maybe_handle_direct_machine_download_request(
            raw_user_input,
            session_id=session_id,
            source_surface=source_surface,
            source_context=source_context,
        )
    if machine_download is not None:
        return {"result": machine_download}

    if action_forbidden:
        machine_write = None
    else:
        machine_write = agent._maybe_handle_direct_machine_write_request(
            raw_user_input,
            session_id=session_id,
            source_surface=source_surface,
            source_context=source_context,
        )
    if machine_write is not None:
        return {"result": machine_write}

    if action_forbidden:
        machine_write_guard = None
    else:
        machine_write_guard = agent._maybe_handle_safe_machine_write_guard(
            raw_user_input,
            session_id=session_id,
            source_surface=source_surface,
            source_context=source_context,
        )
    if machine_write_guard is not None:
        return {"result": machine_write_guard}

    from core.agent_runtime.fast_paths_media import maybe_handle_image_generation

    image_generation = None if action_forbidden else maybe_handle_image_generation(
        agent,
        effective_input,
        session_id=session_id,
        source_context=source_context,
    )
    if image_generation is not None:
        return {"result": image_generation}

    # A named `.pdf` is answered by the PDF tools and nothing else, so it is claimed ahead of the
    # arbiter, the machine-read lane and the live-info lane below.
    #
    # Ahead of the ARBITER because its menu has no PDF option at all: the best it can do with
    # "extract the text from ~/Downloads/report.pdf" is read_file, which answers a PDF with
    # `binary_file`. Ahead of the MACHINE lane because that lane stands down on an explicit path
    # under a home folder and hands off to general tools that cannot open a PDF. Ahead of LIVE-INFO
    # because a path on this disk must never become a web search — one measured turn spent 236s
    # searching Wikipedia for a local file path.
    from core.agent_runtime.fast_paths_pdf import maybe_handle_pdf_read

    # Gated like every other tool-executing lane: a "do not use tools" turn must not run one.
    # This was the census-measured exception.
    pdf_read = None
    if not action_forbidden:
        pdf_read = maybe_handle_pdf_read(
            agent,
            effective_input,
            session_id=session_id,
            source_context=source_context,
        )
    if pdf_read is not None:
        return {"result": pdf_read}

    # The mirror of the lane above. `web.fetch` works when called directly and nothing routed a URL
    # to it, so "fetch https://example.com and tell me what the page says" was answered by the
    # machine file-read lane ("I can only read files inside: ~/Desktop, ~/Downloads, ~/Documents")
    # and "can you open https://httpbin.org/json" by the URL-grounding backstop, which is right to
    # refuse an invented summary and cannot supply the page. Claimed here so the fetch actually
    # happens and the backstop has nothing left to catch.
    from core.agent_runtime.fast_paths_web import maybe_handle_url_read

    url_read = maybe_handle_url_read(
        agent,
        effective_input,
        session_id=session_id,
        source_context=source_context,
    )
    if url_read is not None:
        return {"result": url_read}

    # --- Intent arbitration gate, position 1 of 2: COMPETING READINGS ------------------------
    # (understanding layer; flag VOOL_INTENT_ARBITER)
    #
    # Two or more families read the same message differently (the "check X folder on this machine"
    # specs-hijack). Priority order would pick one by accident, so a constrained multiple-choice
    # model call picks it on purpose -- and it has to run BEFORE the lanes below, because the whole
    # point is that one of them would otherwise claim a turn that is not theirs. This is genuine
    # semantic interpretation and it keeps its model call. Fail-open: flag off / model down /
    # chat-pick all continue exactly as today.
    #
    # A NEAR-MISS -- nothing claimed at all, but the sentence names a tool-ish noun -- is handled at
    # position 2, after the deterministic lanes. See the note there.
    arbitrated = None if (action_forbidden or mixed_turn) else _maybe_arbitrate_intent(
        agent,
        effective_input=effective_input,
        session_id=session_id,
        source_surface=source_surface,
        source_context=source_context,
        gate=_ARBITRATE_ON_AMBIGUITY,
    )
    if arbitrated is not None:
        return {"result": arbitrated}

    # `effective_input`, not `raw_user_input`: this is the handler a typo'd noun blocks.
    #
    # Measured on the installed build, and the reason the typo fix looked done and was not. The
    # normalizer corrects "what is my machine scpecs?" to "...specs?" and the machine family claims
    # the corrected text — both unit-verified — but this call site handed the handler the ORIGINAL
    # string, so the correction was computed and thrown away at the one place that needed it. The
    # correctly spelled question answered in 0.2s while the typo refused, in auto and pinned alike.
    #
    # Its write/download/guard siblings above deliberately keep `raw_user_input`: those carry
    # content to write and URLs to fetch, which must stay verbatim. A READ names a thing and has no
    # verbatim payload, and `_protect_literals` already shields paths, filenames and versions from
    # normalization, so the corrected text is strictly better here.
    if action_forbidden or mixed_turn:
        machine_read = None
    else:
        machine_read = agent._maybe_handle_direct_machine_read_request(
            effective_input,
            session_id=session_id,
            source_surface=source_surface,
            source_context=source_context,
        )
    if machine_read is not None:
        return {"result": machine_read}

    # A metadata question ("which folder is this chat bound to?") is answered with the folder, not
    # with its contents — so it runs ahead of BOTH the workspace-read handler and the overview
    # reader. Measured 2026-07-29: placed after the read handler, three phrasings
    # ("what folder is in use for this workspace?", "do you see what folder our workspace is set
    # on?", "show me the workspace folder") were still claimed upstream and answered with an 8-, 14-
    # or 46-line directory dump, while the same question in six other wordings answered in one line.
    workspace_identity = None if mixed_turn else agent._maybe_handle_workspace_identity_request(
        effective_input,
        session_id=session_id,
        source_surface=source_surface,
        source_context=source_context,
    )
    if workspace_identity is not None:
        return {"result": workspace_identity}

    # Explicit workspace reads/searches get first refusal at the real runtime entry point. The
    # overview handler remains immediately after it for broad "describe this project" requests.
    # The finalize veto below speaks for `workspace_read_fast_path`, whose coverage
    # probe is the file-READ recognizer (`_direct_workspace_read_requests`). This
    # handler serves BOTH reads and workspace SEARCHES, and the search half has no
    # pure-text recognizer to declare -- it is decided by `_plan_tool_workflow`, which
    # needs the agent. So on a pure SEARCH turn the read lane claims no unit at all,
    # and asking the law about it returns "a lane covering ZERO units is trying to end
    # the turn" -- a verdict about a lane that has nothing to do with this turn.
    #
    # Measured: 'look through the workspace and find "runtime_capabilities"' is one
    # request that the unit splitter cuts at the "and find …" head into two units,
    # neither claimed by the read probe. The veto fired, the deterministic search never
    # ran, and the turn fell to the model tool loop, which answered "model synthesis
    # failed (empty_synthesis)" instead of the file's matches.
    #
    # So the veto is asked only when the read lane actually claims some of this turn --
    # which is exactly when it could swallow demand it does not cover. Every such turn
    # keeps it: the incident turn (file read + arithmetic + judgement) claims 1 of 3,
    # and "No web. Read notes.txt and give the current ETH price." claims 1 of 2.
    # `lane_may_claim_whole_turn` itself is untouched; this narrows one call site.
    _read_lane_claims_part_of_this_turn = True
    try:
        from core.agent_runtime.demand_ownership import demand_coverage

        _read_lane_claims_part_of_this_turn = bool(
            demand_coverage(str(raw_user_input or effective_input)).lane_unit_ids(
                "workspace_read_fast_path"
            )
        )
    except Exception:
        # Fail-soft in the pre-existing direction, exactly like `_lane_may_finalize`:
        # an unavailable probe keeps the veto rather than inventing a new grant.
        _read_lane_claims_part_of_this_turn = True

    workspace_runtime = None if (
        action_forbidden
        or mixed_turn
        or (
            _read_lane_claims_part_of_this_turn
            and not _lane_may_finalize("workspace_read_fast_path")
        )
    ) else agent._maybe_handle_direct_workspace_runtime_request(
        effective_input,
        session_id=session_id,
        source_surface=source_surface,
        source_context=source_context,
    )
    if workspace_runtime is not None:
        return {"result": workspace_runtime}

    # "what is this project about / check this folder" — a folder-LEVEL overview that names no file.
    # A small local model won't emit the workspace-read tool for this and confabulates, so read the
    # real folder deterministically. Explicit searches have already had priority above.
    folder_overview = None if (model_owns_judgement or action_forbidden or mixed_turn) else agent._maybe_handle_folder_overview_request(
        effective_input,
        session_id=session_id,
        source_surface=source_surface,
        source_context=source_context,
    )
    if folder_overview is not None:
        return {"result": folder_overview}

    # --- Intent arbitration gate, position 2 of 2: NEAR-MISS --------------------------------
    # A near-miss is "no family claimed, but the sentence names a tool-ish noun". It used to be
    # arbitrated at position 1, which bought a model call to decide a route the lanes above had not
    # yet been asked about -- and they answer most of these themselves. Measured on this branch,
    # hermetic, counting provider invocations rather than elapsed time:
    #
    #     "read the qa_test.txt file and tell me exactly what it says"
    #         arbiter call -> then workspace.read_file answered it, in 0.0008s
    #     "List files in this project." / "What files are in this folder?"
    #         arbiter call -> then the folder-overview lane answered it
    #
    # In each case the model was paid to rediscover an operation a deterministic authority already
    # owned, and the turn's own proof then reported `model_not_used`. The near-miss signal only ever
    # meant "the nine machine probes found nothing" -- it never asked the workspace, identity or
    # overview lanes, which are right here and decide from the file the user named and the folder
    # the chat is bound to. If one of them claims, there was no ambiguity for a model to resolve.
    #
    # So the arbiter still sees every near-miss NOTHING deterministic could answer (the phrasings it
    # was built for -- "give me a rundown of what lives in <path>" -- reach it unchanged, one gate
    # later), and pays nothing for the ones that were never ambiguous.
    arbitrated_near_miss = None if (action_forbidden or mixed_turn) else _maybe_arbitrate_intent(
        agent,
        effective_input=effective_input,
        session_id=session_id,
        source_surface=source_surface,
        source_context=source_context,
        gate=_ARBITRATE_ON_NEAR_MISS,
    )
    if arbitrated_near_miss is not None:
        return {"result": arbitrated_near_miss}

    # Deterministic mission summary/recall answered from typed slots BEFORE any context/model call,
    # so cross-session "Prior session continuity" can never override an exact mission value.
    mission_render = agent._maybe_handle_mission_render_request(
        effective_input,
        session_id=session_id,
        source_context=source_context,
    )
    if mission_render is not None:
        return {"result": mission_render}

    voolbook_fast = agent._maybe_handle_voolbook_fast_path(
        effective_input,
        raw_user_input=raw_user_input,
        session_id=session_id,
        source_context=source_context,
    )
    if voolbook_fast is not None:
        return {"result": voolbook_fast}

    # Coverage-scoped like the lanes above, and it has to be: with the audit, currency and receipt
    # lanes each declining to swallow the eight-request message, THIS lane claimed it instead and
    # answered "I couldn't map `TRY i meant money TRY 1000 TRY to USD ...`" -- the whole message
    # flattened into one failed lookup subject. Its classifier needs the agent and the turn's
    # interpretation, so the per-clause reading is supplied here rather than computed in the
    # contract; the arbitration itself stays in the one place.
    from core.agent_runtime.answer_coverage import FAMILY_LIVE_INFO

    def _clause_wants_live_info(clause: str) -> bool:
        from core.agent_runtime.fast_live_info_mode_classifier import live_info_mode

        return bool(live_info_mode(agent, clause, interpretation=interpreted))

    live_info_coverage = coverage_for(
        effective_input, FAMILY_LIVE_INFO, probe=_clause_wants_live_info
    )
    # Coverage-scoped LIKE THE LANES ABOVE, in both directions. Until this was symmetrical, live
    # info was the one blocked family that produced nothing at all: the audit, currency, receipt
    # and identity lanes each answer their OWN clauses and record the result when they lose the
    # whole turn, and this one was simply skipped. Measured live 2026-08-18:
    #
    #   "who are you? Also whats the WHEATHER in Rome rihgt now?"
    #     -> "1. My name is VOOL. 2. I cannot provide real-time weather updates."
    #
    # The identity half was carried verbatim from its recorded slice answer, exactly as the
    # observation instructs. The weather half had no recorded fact to carry, so the model did the
    # honest thing with nothing -- and the clause the user actually asked was never served by the
    # lane that owns it.
    #
    # The interpretation is dropped for a slice: it was derived from the WHOLE turn, and its topic
    # hints describe clauses this call is not answering.
    # P0 MIXED-DEMAND TERMINAL CLOSURE — the finalize law for the live arm too:
    # its classifier claims the whole text whenever the slicer returns one slice,
    # so a live clause beside other demands still reads `covers_whole_turn` and
    # the siblings would vanish behind a one-clause answer (the clock/currency
    # measured class). Refused, the arm below records the live slice answer and
    # the composite seams own the whole unit plan.
    # A whole claim is only true when the CANONICAL demand mint agrees: this lane's own slicer
    # demotes an independent sibling to a chained step ("weather in Vilnius and calculate 37 x
    # 19" reads as one weather request plus a step), and finalizing on that reading swallowed
    # the sibling -- the arithmetic answered by nobody, or by the fast path's search road
    # instead of the conductor. When the spine's own mint holds more than one REQUEST, the
    # demand-owned composite owns the whole turn.
    from core.agent_runtime.answer_coverage import KIND_REQUEST, demand_units

    try:
        _minted_requests = [
            unit for unit in (demand_units(effective_input) or ())
            if getattr(unit, "kind", KIND_REQUEST) == KIND_REQUEST
        ]
    except Exception:
        _minted_requests = []
    _live_info_whole_claim = (
        live_info_coverage.covers_whole_turn
        and _lane_may_finalize("live_info_fast_path")
        and len(_minted_requests) <= 1
    )
    live_info_wanted = _live_info_whole_claim or bool(live_info_coverage.consumed)
    if live_info_wanted and not _live_info_whole_claim and len(_minted_requests) > 1 \
            and not live_info_coverage.conflicting:
        # The slice design answers a live clause BESIDE clauses other fast families serve
        # (identity beside weather). When the sibling requests have no family -- the
        # arithmetic the conductor computes -- this lane's search road answering the live
        # clause first splits the turn, and the composite never runs its typed plan through
        # the transport door. Siblings without an owner send the whole turn to the composite.
        live_info_wanted = False
    live_info_status = None if (
        action_forbidden or not live_info_wanted
    ) else agent._maybe_handle_live_info_fast_path(
        effective_input if _live_info_whole_claim
        else slice_text(effective_input, live_info_coverage.consumed),
        session_id=session_id,
        source_context=source_context,
        interpretation=interpreted if _live_info_whole_claim else None,
    )
    if live_info_status is not None and not _live_info_whole_claim:
        record_slice_answer(
            source_context,
            text=effective_input,
            family=FAMILY_LIVE_INFO,
            response=str(live_info_status.get("response") or ""),
            reason="live_info_fast_path",
            consumed=live_info_coverage.consumed,
        )
        live_info_status = None
    if live_info_status is not None and live_info_status.get("live_info_refusal"):
        # A whole-turn claim the lane then REFUSED (web lookup disabled on this runtime) may
        # not finalize the turn, and -- measured -- may not CONSUME the demand either: a
        # recorded slice answer marks the unit served, the demand-owned composite then skips
        # it, and the arithmetic beside the weather answered while the weather quietly died.
        # The refusal answers nothing; the turn continues so the lanes that can serve the
        # demand (the typed live-data lane through the transport door) still own it -- EXCEPT
        # under the Local Only composite, where the same policy blocks every egress lane the
        # continuation could reach. There, continuing is guaranteed-failure theater: the
        # lanes run contained, the synthesis pass finds no certified author, and a completed
        # honest refusal is destroyed by a task-failure that was policy all along (the
        # first-run denial demo measured exactly this shape).
        from core import policy_engine as _policy_engine

        if (
            bool(_policy_engine.local_only_mode())
            and not bool(_policy_engine.allow_web_fallback())
            and not bool(_policy_engine.get("network.outbound_enabled", False))
        ):
            return {"result": live_info_status}
        live_info_status = None
    if live_info_status is not None:
        return {"result": live_info_status}

    evaluative = None if (model_owns_judgement or mixed_turn) else agent._evaluative_conversation_fast_path(
        normalized_input, source_surface=source_surface
    )
    if evaluative:
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=evaluative,
                confidence=0.88,
                source_context=source_context,
                reason="evaluative_conversation_fast_path",
            )
        }

    return {"result": None}


def _maybe_arbitrate_intent(
    agent: Any,
    *,
    effective_input: str,
    session_id: str,
    source_surface: str,
    source_context: dict[str, object] | None,
    gate: str = _ARBITRATE_ON_AMBIGUITY,
) -> dict[str, Any] | None:
    """Consult the constrained intent arbiter on ambiguous / near-miss messages. Fail-open.

    `gate` selects WHICH signal this call site answers -- see `_ARBITRATE_ON_AMBIGUITY`. A message
    carrying the other signal returns None here without consulting anything, so exactly one of the
    two call sites can spend a model call on any one turn.

    Returns a finished fast-path result when the arbiter picked a family AND its deterministic
    execution produced one; every other outcome (flag off, unmatched signal, model down, chat-pick,
    no usable argument) returns None so dispatch continues exactly as today.
    """
    try:
        from core import intent_arbiter
        from core.agent_runtime.intent_claims import (
            FAMILY_DISK_USAGE,
            FAMILY_HOST_STATE,
            FAMILY_LIST_PROCESSES,
            FAMILY_MACHINE_SPECS,
            is_ambiguous,
            near_miss,
            probe_claims,
        )
        from core.routing_decision_log import record_decision

        # Every family whose mapped arguments are CONSTANT, so it can never fall open on a missing
        # argument. This said "the two families" and named two of them; `_execute_arbitrated_family`
        # maps `list_processes` to a sort key derived from the sentence and `host_state` to `{}`,
        # and neither can be None either -- so the near-miss guard below silently did not cover
        # them. Measured live 2026-07-30: "Explain how an operating system decides which process
        # gets CPU time next." was claimed by no detector, counted as a near-miss on the word
        # "process", and answered with "Top processes by CPU right now:" -- a ranking of the
        # operator's own Mac in reply to a question about scheduling theory.
        #
        # Keep this in step with the tool_map in `_execute_arbitrated_family`: a family belongs
        # here exactly when its arguments do not depend on something the user actually named.
        _ZERO_ARGUMENT_MACHINE_READS = {
            FAMILY_DISK_USAGE,
            FAMILY_MACHINE_SPECS,
            FAMILY_LIST_PROCESSES,
            FAMILY_HOST_STATE,
        }

        if not intent_arbiter.arbiter_enabled():
            return None
        claims = probe_claims(effective_input)
        was_near_miss = near_miss(effective_input, claims)
        was_ambiguous = is_ambiguous(claims)
        if not (was_ambiguous or was_near_miss):
            return None
        # The signals are mutually exclusive by construction (`near_miss` requires an empty claim
        # list, `is_ambiguous` requires two), so this routes each message to exactly one call site.
        if gate == _ARBITRATE_ON_NEAR_MISS and not was_near_miss:
            return None
        if gate == _ARBITRATE_ON_AMBIGUITY and not was_ambiguous:
            return None
        claim_names = [claim.family for claim in claims]
        # A turn the lane registry already assigns, whole, to a lane of a domain this menu does not
        # read is neither ambiguous nor a near-miss of any option here. Measured on the served path
        # (2026-09-15, arbiter model stood in by a recorded pick): 'rename my Apple note "Plan" in the
        # Work folder to "Plan v2"' is a near-miss on "folder", this gate ran before operator dispatch,
        # and a `find_folder` pick executed `machine.find_folder("Work")` and ended the turn, while the
        # registry named `operator_action_dispatch` as the only lane covering its one unit. Owners in
        # the menu's own domains are sibling readings and keep the arbiter: those near-misses are the
        # path listings it exists for.
        owner = _registered_owner_outside_the_menu(effective_input)
        if owner:
            record_decision(
                session_id=session_id, user_input=effective_input, family="intent_arbiter",
                handled=False, claims=claim_names, arbiter=f"declined:registered_owner:{owner}",
            )
            return None
        request_context = dict(source_context or {})
        project_id = str(request_context.get("project_id") or "").strip()
        decision = intent_arbiter.arbitrate(
            effective_input,
            claims,
            request_id=str(
                request_context.get("request_id")
                or request_context.get("trace_id")
                or ""
            ),
            chat_id=session_id,
            project_id=project_id,
            context_manifest={
                "chat_id": session_id,
                "project_id": project_id,
                "items_included": [],
                "items_excluded": [],
                "capsule_version": "none",
            },
            # So the arbiter's own provider call lands in this turn's ledger. Without it the one
            # gate that can spend a model call before any lane runs was also the one call the
            # turn's `model_calls` could not see.
            source_context=source_context,
        )
        if decision is None:
            record_decision(
                session_id=session_id, user_input=effective_input, family="intent_arbiter",
                handled=False, claims=claim_names,
                arbiter=f"failed_open:{intent_arbiter.last_failure() or 'unknown'}",
            )
            return None
        if decision.family == intent_arbiter.CHOICE_CHAT:
            record_decision(
                session_id=session_id, user_input=effective_input, family="intent_arbiter",
                handled=False, claims=claim_names, arbiter="declined:chat",
            )
            return None
        # A NEAR-MISS is "no detector claimed anything, but a tool-ish noun is in there" — the whole
        # read-only menu gets offered and a 0.6b model picks from it. Every other family needs an
        # ARGUMENT and falls open when it has none ("never guess"); `disk_usage` and `machine_specs`
        # take `{}`, so on a near-miss they are the two picks that can never decline, and a wrong one
        # replaces the user's answer with a drive report. On a near-miss the detector by definition
        # did NOT corroborate them (it would have claimed), so a guess is all there is — decline.
        # An AMBIGUOUS message is different and keeps its authority: `_candidate_options` only offers
        # families that actually claimed, so the arbiter is choosing between real readings there.
        if was_near_miss and decision.family in _ZERO_ARGUMENT_MACHINE_READS:
            record_decision(
                session_id=session_id, user_input=effective_input, family="intent_arbiter",
                handled=False, claims=claim_names,
                arbiter=f"declined:uncorroborated_near_miss:{decision.family}",
            )
            return None
        result = _execute_arbitrated_family(
            agent,
            decision=decision,
            effective_input=effective_input,
            session_id=session_id,
            source_surface=source_surface,
            source_context=source_context,
        )
        record_decision(
            session_id=session_id, user_input=effective_input, family="intent_arbiter",
            handled=result is not None, claims=claim_names, arbiter=f"picked:{decision.family}",
        )
        return result
    except Exception:
        return None  # arbitration must never break a turn


def _registered_owner_outside_the_menu(text: str) -> str:
    """The registered lane outside the arbiter menu's domains that owns every unit of `text`, or "".

    Asked as the front-door tier, the family the arbiter's picks finalize under, with the menu's own
    coverage capabilities (`intent_arbiter.MENU_COVERAGE`). A registry fault answers "", so the arbiter
    is consulted exactly as before.
    """
    try:
        from core import intent_arbiter
        from core.agent_runtime.demand_ownership import registered_owner_ahead_of

        return registered_owner_ahead_of(
            text, "turn_frontdoor_deterministic", capability=intent_arbiter.MENU_COVERAGE
        )
    except Exception:
        return ""


# "a concrete path the user typed" and "the sentence asks what is INSIDE a place" are now decided
# in ONE place (core.execution.constants), because the machine fast path routes on the same two
# facts. Two copies drifting apart would mean the deterministic route and the arbiter's repair of
# it disagreed about what the user wrote.
def _asks_about_contents(text: str) -> bool:
    return asks_about_contents(text)


def _explicit_path_in(text: str) -> str:
    """The first concrete path in the user's own words, or '' when they named no path."""
    return explicit_path_in(text)


def _execute_arbitrated_family(
    agent: Any,
    *,
    decision: Any,
    effective_input: str,
    session_id: str,
    source_surface: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any] | None:
    """Run the arbiter's pick through the SAME deterministic tools the fast paths use.

    Only read-only intents are mapped; a pick with no usable argument falls open rather than
    guessing. The answer text always comes from a real tool execution — the model only chose
    the route.
    """
    from core.agent_runtime.fast_paths_machine import _machine_tool_fast_path_result
    from core.agent_runtime.intent_claims import (
        FAMILY_DISK_USAGE,
        FAMILY_FIND_FOLDER,
        FAMILY_FOLDER_OVERVIEW,
        FAMILY_HOST_STATE,
        FAMILY_LIST_DIRECTORY,
        FAMILY_LIST_PROCESSES,
        FAMILY_MACHINE_SPECS,
    )
    from core.authorized_tool_execution import execute_authorized_runtime_tool
    from core.execution.constants import machine_process_sort

    family = str(getattr(decision, "family", "") or "")
    argument = str(getattr(decision, "argument", "") or "").strip()
    # A path the user typed is already exact -- do not depend on a 0.6B model to copy it back. Asked
    # about "/Users/me/Desktop/route-planner" the arbiter returned argument="Desktop", so the answer
    # was a listing of the whole Desktop. When the raw text contains a concrete path, that path wins.
    explicit_path = _explicit_path_in(effective_input)
    if explicit_path and family in {FAMILY_LIST_DIRECTORY, FAMILY_FOLDER_OVERVIEW}:
        argument = explicit_path
    if explicit_path and family == FAMILY_FIND_FOLDER:
        # You do not search a machine for a folder whose path you were just handed. A concrete
        # path means the user already knows where it is and wants something done with it, so
        # FIND_FOLDER here is a misclassification by the arbiter rather than a request to locate
        # anything. Measured 2026-07-28: "open up ~/Desktop/vool-q4m8 and name every file you
        # find there" was classified FIND_FOLDER, and the user asking for files was answered with
        # a list of folder locations. The verb "find" in the sentence is what drew it there.
        #
        # Same reasoning as the explicit-path rule directly above, which already exists because
        # the arbiter returned argument="Desktop" for a full path: where the raw text carries a
        # concrete path, that path is better evidence than a small model's classification.
        family = FAMILY_LIST_DIRECTORY
        argument = explicit_path
    if family == FAMILY_FIND_FOLDER and not explicit_path:
        # FIND_FOLDER only: this derives a NAME, and list_directory needs a PATH. Feeding a name
        # to list_directory made "show me the files in my travel folder on the desktop" list the whole
        # Desktop. The arbiter's argument extraction is unreliable on a descriptive phrase. Measured on the
        # deployed build 2026-07-28: "whats in my invoices folder on the desktop" came back with
        # argument="my", which searched every drive for folders containing "my" and returned 25
        # unrelated hits including /System/Applications/FindMy.app.
        #
        # The raw sentence is right here and is better evidence than a 0.6B model's span pick, so
        # re-derive the name from it and use that when the arbiter's answer is a stopword or is
        # not present in the request at all.
        from core.runtime_execution_tools import _FIND_FOLDER_STOPWORDS, _find_folder_name_from_phrase

        derived, _scope = _find_folder_name_from_phrase(effective_input)
        current = str(argument or "").strip().lower()
        if derived and (not current or current not in str(effective_input or "").lower() or len(current) <= 3):
            argument = derived
        elif not derived and current in _FIND_FOLDER_STOPWORDS:
            # A where-question that reduces to pure fillers ("ok and where is the foldeR?!")
            # leaves the arbiter's filler span as the "name". A filler is never a folder name:
            # clear it so the family falls open to the model turn instead of walking the disk
            # for folders named "ok" (measured live 2026-09-18).
            argument = ""

    if family == FAMILY_LIST_DIRECTORY and argument and not explicit_path:
        # The arbiter hands over whatever span it extracted, and on a no-path phrase that is a
        # NAME ("orchard", or the whole phrase) — but machine.list_directory takes a PATH, so the
        # allowlist rejected it and the user saw "I can only inspect safe local directories in
        # this lane" for a folder sitting in ~/Desktop. Traced live 2026-07-28 (routing log:
        # picked:list_directory, then the lane refusal).
        #
        # Resolve the phrase to a unique child of an allowed root. Unresolvable → clear the
        # argument, which maps to None below and lets the turn fall through to the workflow
        # planner, whose extractor now resolves these phrasings itself. Either way the user gets
        # a listing or a model turn — never a refusal caused by our own argument shape.
        from core.execution.planner import _named_child_within
        from core.runtime_execution_tools import (
            _find_folder_name_from_phrase,
            _resolved_existing_directory,
        )

        if _resolved_existing_directory(argument) is None:
            _name, scope = _find_folder_name_from_phrase(effective_input)
            roots = (scope,) if scope else ("~/Desktop", "~/Downloads", "~/Documents")
            hits = [h for h in (_named_child_within(root, effective_input) for root in roots) if h]
            argument = hits[0] if len(hits) == 1 else ""

    if family == FAMILY_FIND_FOLDER and _asks_about_contents(effective_input):
        # "what's in my recipes folder on the desktop" asks WHAT IS INSIDE, and was answered with
        # WHERE IT IS — a correct find_folder result that does not answer the question. Measured
        # on the deployed build 2026-07-28.
        #
        # Locating is only ever a step here, so take it: resolve the folder with the same search,
        # then list what is in it. If the search does not land on exactly one directory there is
        # nothing unambiguous to list, and the ordinary find_folder answer is the honest one.
        from core.authorized_tool_execution import execute_authorized_runtime_tool as _execute

        located = _execute("machine.find_folder", {"name": argument}, source_context=dict(source_context or {}))
        matches = list(((located.details or {}) if located is not None else {}).get("matches") or [])
        if located is not None and located.ok and matches:
            # A common word matches widely — "budget" hit 22 directories across the machine. The
            # phrase usually says where to look ("on the desktop"), so honour that before giving up:
            # narrowing to the named place is what turns 22 ambiguous hits into the one the user meant.
            from core.runtime_execution_tools import _find_folder_name_from_phrase, _resolved_existing_directory

            _name, scope = _find_folder_name_from_phrase(effective_input)
            scope_root = _resolved_existing_directory(scope) if scope else None
            if scope_root:
                scoped = [m for m in matches if str(m).startswith(scope_root + os.sep)]
                if scoped:
                    matches = scoped
            if len(matches) == 1:
                family = FAMILY_LIST_DIRECTORY
                argument = str(matches[0])

    if family == FAMILY_FOLDER_OVERVIEW:
        # The arbiter dispatches here directly, so the front door's ordering does not apply and the
        # same precedence has to be restated. Measured 2026-07-29: with it only in the front door,
        # "what folder is in use for this workspace?", "do you see what folder our workspace is set
        # on?" and "show me the workspace folder" were classified folder-overview by the arbiter and
        # answered with an 8-, 14- or 46-line directory dump, while six other wordings of the same
        # question answered in one line — the inconsistency was the arbiter, not the detector.
        identity = agent._maybe_handle_workspace_identity_request(
            effective_input,
            session_id=session_id,
            source_surface=source_surface,
            source_context=source_context,
        )
        if identity is not None:
            return identity
        return agent._maybe_handle_folder_overview_request(
            effective_input,
            session_id=session_id,
            source_surface=source_surface,
            source_context=source_context,
        )
    tool_map: dict[str, tuple[str, dict[str, str] | None]] = {
        FAMILY_FIND_FOLDER: ("machine.find_folder", {"name": argument} if argument else None),
        FAMILY_MACHINE_SPECS: ("machine.inspect_specs", {}),
        FAMILY_DISK_USAGE: ("machine.disk_usage", {}),
        # The ranking comes from the sentence, not from the arbiter: it picked the family, and
        # a CPU question answered with a memory ranking is still the wrong answer.
        FAMILY_LIST_PROCESSES: ("machine.list_processes", {"sort": machine_process_sort(effective_input)}),
        FAMILY_HOST_STATE: ("machine.host_state", {}),
        # `path` is what the machine.list_directory contract declares. This said `directory`, which
        # the contract has never accepted, so every routed listing was refused with
        # "`machine.list_directory` received unsupported argument(s): directory" and that refusal was
        # delivered to the user as the answer. No model was involved -- the wrong name was ours.
        FAMILY_LIST_DIRECTORY: ("machine.list_directory", {"path": argument} if argument else None),
    }
    mapped = tool_map.get(family)
    if mapped is None:
        return None  # families without a safe direct mapping (read_file, image) fall open
    intent, arguments = mapped
    if arguments is None:
        return None  # no usable argument -> never guess
    if intent == "machine.list_directory" and not bool(
        (source_context or {}).get("_semantic_machine_list_directory_admitted")
    ):
        # A small arbiter can still pick list_directory from response-shape prose such as "List
        # only actual members of the standard".  The raw semantic preflight has already proved
        # whether the turn names a real machine/path target.  A missing proof means this direct
        # fast path must decline before tool selection or execution; the ordinary knowledge lane
        # remains available to answer the user's actual question.
        return None
    execution = execute_authorized_runtime_tool(intent, arguments, source_context=dict(source_context or {}))
    if execution is None:
        return None
    return _machine_tool_fast_path_result(
        agent,
        user_input=effective_input,
        session_id=session_id,
        source_context=source_context,
        intent=intent,
        execution=execution,
        reason="intent_arbiter_fast_path",
    )
