from __future__ import annotations

import re
from typing import Any


def maybe_handle_memory_fast_path(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
    access_policy: Any | None = None,
    maybe_handle_memory_command_fn: Any,
) -> dict[str, Any] | None:
    handled, response = maybe_handle_memory_command_fn(
        user_input,
        session_id=session_id,
        access_policy=access_policy,
        source_context=source_context,
    )
    if handled:
        response = _gate_memory_response(response, session_id, source_context)
        if isinstance(response, str):
            # The memory lane wrote (or refused) the fact itself and composed these bytes from what it did. A
            # planner call earlier in the turn made the turn read as model-authored, and the claim gate then cut
            # "I'll remember that" and added "Nothing was saved" under a fact that WAS saved (served on 066ac524:
            # "Remember that my manager is Pieter and the quarterly review is on december 2"). Recorded byte-exact,
            # so text changed after this point is judged as before.
            from core.agent_runtime.response import record_deterministic_render

            record_deterministic_render(source_context, response, route="memory_command")
        result = agent._fast_path_result(
            session_id=session_id,
            user_input=user_input,
            response=response,
            confidence=0.93,
            source_context=source_context,
            reason="memory_command",
        )
        from core.memory.command_result import ForgetOutcome

        if isinstance(response, ForgetOutcome):
            result["_memory_command_outcome"] = response
        elif isinstance(response, str):
            # The transport finalizes with its own context, not this one; the stamp rides the result the same way
            # the closure verdict does.
            from core.agent_runtime.response import DETERMINISTIC_RENDER_KEY

            stamp = (source_context or {}).get(DETERMINISTIC_RENDER_KEY) if isinstance(source_context, dict) else None
            if isinstance(stamp, dict):
                result["_deterministic_render"] = dict(stamp)
        return result
    result = agent._maybe_handle_companion_memory_fast_path(
        user_input,
        session_id=session_id,
        source_context=source_context,
    )
    if isinstance(result, dict) and "response" in result:
        return {**result, "response": _gate_memory_response(result["response"], session_id, source_context)}
    return result


def _gate_memory_response(response, session_id, source_context):
    from core.memory.command_result import ForgetOutcome
    from core.memory.revocation import authorize_payload

    # The executor's control outcome must still block the turn when the memory
    # authority is unavailable. It is not a served memory payload.
    if isinstance(response, ForgetOutcome):
        return response
    if authorize_payload(response, session_id, (source_context or {}).get("runtime_home")):
        return response
    return "Memory is withheld because its erasure authority could not authorize this read."


def model_final_response_text(model_execution: Any) -> str:
    # Only this answer-bearing decision is read here; auxiliary decomposition verdicts do
    # not enter this function or grant action authority.
    if str(getattr(model_execution, "validation_state", "") or "").lower() == "contract_failed":
        return ""
    final_text = str(getattr(model_execution, "output_text", "") or "").strip()
    if final_text:
        return final_text
    structured = getattr(model_execution, "structured_output", None)
    if isinstance(structured, dict):
        if isinstance(structured.get("steps"), list) and isinstance(structured.get("summary"), str):
            from core.model_output_contracts import _normalize_payload
            return _normalize_payload("action_plan", structured).strip()
        return str(structured.get("summary") or structured.get("message") or "").strip()
    return ""


def chat_surface_cache_or_memory_source(model_execution: Any) -> bool:
    source = str(getattr(model_execution, "source", "") or "").strip().lower()
    return source in {"exact_cache_hit", "memory_hit"}


def chat_surface_model_final_text(model_execution: Any) -> str:
    if chat_surface_cache_or_memory_source(model_execution):
        return ""
    return model_final_response_text(model_execution)


def chat_surface_honest_degraded_response(
    agent: Any,
    model_execution: Any,
    *,
    user_input: str = "",
    interpretation: Any | None = None,
) -> str:
    source = str(getattr(model_execution, "source", "") or "").strip().lower()
    live_mode = agent._live_info_mode(user_input, interpretation=interpretation) if str(user_input or "").strip() else ""
    if source in {"exact_cache_hit", "memory_hit", "no_provider_available"} and live_mode == "fresh_lookup":
        query = agent._normalize_live_info_query(user_input, mode=live_mode)
        return agent._live_info_failure_text(query=query, mode=live_mode)
    if source == "exact_cache_hit":
        return (
            "I found a matching cached answer for this topic, but this chat path requires a live model response, "
            "so I'm not passing cached text off as a fresh answer."
        )
    if source == "memory_hit":
        return (
            "I found relevant local memory for this topic, but this chat path requires a live model response, "
            "so I'm not presenting remembered text as a fresh answer."
        )
    # An AUTH failure is the one degraded case the user can actually fix, so say so instead of the
    # generic non-answer. A live drive hit `401 Unauthorized` from OpenRouter and the user was told
    # only "I couldn't get a live model response" -- the actionable fact (the stored key is being
    # rejected) was in the ledger but never surfaced, which reads as "the model is useless" rather
    # than "your key needs rotating".
    provider_hint = _provider_auth_failure_hint(model_execution)
    if provider_hint:
        return provider_hint

    preview_hint = _disclosure_preview_needed_hint(model_execution)
    if preview_hint:
        return preview_hint

    cancelled_hint = _cancelled_turn_hint(model_execution)
    if cancelled_hint:
        return cancelled_hint

    runtime_hint = _typed_model_runtime_failure_hint(model_execution)
    if runtime_hint:
        return runtime_hint

    # An ordinary multi-part Q&A failure is about answer completeness, not routing.  Provider/model
    # selection prose is internal operator detail and was observed becoming the entire final answer
    # for "Explain ... Calculate ... Give a 7-word title." Keep this user-facing lane focused on
    # what failed when there is no stronger typed runtime evidence above.
    from core.plain_task_routing import is_ordinary_multi_part_plain_task

    if is_ordinary_multi_part_plain_task(user_input):
        return (
            "I couldn't produce a complete answer to every part in this run. "
            "Retry the same question and I'll answer all parts together."
        )

    # A turn pinned to ONE model has no second candidate by design: picking a concrete model means
    # "answer with this model", and silently substituting another would be a worse failure than
    # not answering. But the operator was told only "I couldn't get a usable model response",
    # which reads as a runtime limitation rather than the fact they can act on.
    #
    # Measured live 2026-08-03: a whole-workspace turn ran 29 tools across 9 rounds, then the
    # pinned free model returned no choices at the synthesis step and the turn died with
    # `attempted=["nvidia/nemotron-3-ultra-550b-a55b:free"]`. Unpinned, the same lane ranks three.
    pinned_hint = _single_candidate_failure_hint(model_execution)
    if pinned_hint:
        return pinned_hint

    if source == "selected_model_blocked":
        # An explicit pin was resolved but could not run (its paid reservation was refused, a gate
        # excluded it, or its own call failed). Name the model AND the actual cause -- carried on the
        # decision from the authority that rejected it -- and say plainly that no other model
        # answered in its place. The cause codes are policy/gate names, never secrets.
        details = getattr(model_execution, "details", None)
        requested = str((details or {}).get("requested_model") or "").strip() if isinstance(details, dict) else ""
        block_reason = str((details or {}).get("block_reason") or (details or {}).get("reason") or "").strip() if isinstance(details, dict) else ""
        model_was_attempted = bool((details or {}).get("model_was_attempted")) if isinstance(details, dict) else False
        name = f"`{requested}`" if requested else "The model you selected"
        cause = _selected_model_block_cause(block_reason, model_was_attempted=model_was_attempted)
        return (
            f"{name} could not run this turn: {cause}. I did not answer with a different model "
            "instead -- pick another model from the selector, or switch it to Auto so the turn can "
            "route to whatever is actually available."
        )

    if source == "model_unavailable":
        details = getattr(model_execution, "details", None)
        requested = str((details or {}).get("requested_model") or "").strip() if isinstance(details, dict) else ""
        name = f"`{requested}`" if requested else "The model you selected"
        return (
            f"{name} isn't available on this runtime right now. I'm not going to answer with a "
            "different model without telling you -- pick another model from the selector, or "
            "switch it to Auto so the turn can route to whatever is actually available."
        )

    if source == "no_provider_available":
        from core.cloud_only_default import no_route_hint

        hint = no_route_hint()
        if hint:
            return f"I couldn't get a live model response in this run: {hint}"
        return (
            "I couldn't get a live model response in this run, so I'm not going to recycle cached or remembered "
            "text as if it were fresh."
        )
    return (
        "I couldn't get a usable model response in this run, so I'm not going to recycle cached or remembered "
        "text as if it were fresh."
    )


def _selected_model_block_cause(block_reason: str, *, model_was_attempted: bool) -> str:
    """Map a stable, non-secret block code (from the authority that refused the pin) to one plain
    clause for the user. ``model_was_attempted`` distinguishes a pin whose adapter actually ran and
    returned nothing usable from one the runtime never started -- so a gate rejection is never shown
    as "did not return a usable response", which would falsely imply the model ran. An unrecognised
    code falls back to an honest phrase, never a raw internal string."""
    code = str(block_reason or "").strip().lower()
    if code in {
        "per_call_spend_cap_exceeded",
        "per_task_spend_cap_exceeded",
        "daily_spend_cap_exceeded",
        "monthly_spend_cap_exceeded",
        "spend_cap_exceeded",
    }:
        return "running it would exceed your spend cap"
    if code == "device_monthly_spend_cap_exceeded":
        return "this phone has reached its monthly cloud limit; raise it for this phone in VOOL Settings on your computer"
    if code == "cloud_policy_unreadable":
        return "its saved cloud policy could not be read; repair it in Settings before trying again"
    if code == "daily_call_cap_exceeded":
        return "the daily paid-call cap has been reached"
    if code == "not_owner_local":
        return "paid models can only run from your own local session"
    if code == "internal_tool_intent_call":
        return "a paid model is not spent on this turn's internal step"
    if code == "reservation_unavailable":
        return "its spend reservation could not be created"
    if code == "missing_turn_or_model_identity":
        return "the request was missing its routing identity"
    if code == "selected_provider_excluded_before_invocation":
        return "its provider lane is not available right now"
    if code == "payment_lane_removed":
        # A pick saved for UsePod, whose lane left VOOL in 0.8: refused before anything was sent.
        return "it ran on UsePod, which is no longer part of VOOL, so nothing was sent and nothing was charged"
    if model_was_attempted:
        if _is_http_rate_limit(code):
            return "the provider refused the request with HTTP 429 (rate limit); wait and retry, or select another model"
        return "it did not return a usable response"
    return "it could not be started this turn"


def _is_http_rate_limit(error: str) -> bool:
    """Recognize a reported HTTP status; never echo the provider's raw error text."""
    return bool(re.search(
        r"(?:\b429\s+(?:client error|too many requests|rate limit|provider returned error)"
        r"|\b(?:http(?:error)?|status(?:_code)?)\s*[:= ]+429\b)",
        str(error), re.IGNORECASE,
    ))


#: How every runtime no-answer notice below begins and ends. A person reads one plain sentence on
#: what happened, that nothing was made up in its place, and what they can do; the model name is
#: plain text, never a code span. The leads are what core.context_history_authority recognizes in a
#: later prompt (with the older wordings still stored in chats), so they stay stable.
#:
#: App bugs 2026-11-12 (real app, a 503 that persisted through the retry): the reply read like a log
#: line -- "`qwen3.5:4b` failed before producing a usable answer. No cached or remembered text was
#: substituted. Retry the turn."
NO_ANSWER_LEAD = "I couldn't get an answer this time"
UNFINISHED_ANSWER_LEAD = "I couldn't finish this answer"
NOTHING_MADE_UP = "Nothing was made up in its place."
TRY_AGAIN = "You can try again."


def _attempt_failure_words(error: str, error_class: str = "") -> str:
    """What one model attempt did, in words a person reads: never a status code, a gate name or the
    provider's own error text (those stay in the turn trace)."""

    lowered = str(error or "").casefold()
    if error_class == "EMPTY_PROVIDER_RESPONSE":
        return "sent back an empty reply"
    if _is_http_rate_limit(error):
        return "was turned away by its provider for now (rate limit)"
    if "model_load_gated_low_memory" in lowered or "low_memory" in lowered:
        return "could not start because this computer was short on free memory"
    if "timed out" in lowered or "timeout" in lowered:
        return "took too long to reply"
    if "connection" in lowered or "unreachable" in lowered:
        return "could not be reached"
    if error:
        return "stopped with an error before it replied"
    return "did not reply"


def _pinned_model_name(details: dict[str, Any]) -> str:
    """The model a person picked, by its own name, for a notice about that pin.

    A pin arrives as the lane id the router dispatched ("openrouter:meta-llama/llama-3.3-70b-instruct",
    "ollama-local:qwen3.5:4b"); the part before the first colon is how VOOL reaches the model, not
    the model the person picked. A requested name that is not one of the attempted lane ids is kept
    whole, because a model name can hold a colon of its own ("qwen3.5:4b").

    CI #681 (served pin lane, 2026-11-13): an empty reply from a pin read "<lane>:<model> sent back an
    empty reply".
    """

    requested = str(details.get("requested_model") or "").strip()
    attempted = {str(item).strip() for item in list(details.get("attempted") or []) if str(item).strip()}
    if requested in attempted and ":" in requested:
        return requested.split(":", 1)[1]
    return requested


def _typed_model_runtime_failure_hint(model_execution: Any) -> str:
    """Describe a measured model failure in plain words without exposing raw provider errors.

    The turn trace keeps full diagnostics.  This surface names only the models and what each one did
    (an empty reply, too slow, short on memory, unreachable, the length limit), which is enough to
    tell a model failure from a weak answer without leaking endpoints, headers, or provider
    response bodies.
    """

    details = getattr(model_execution, "details", None)
    if not isinstance(details, dict):
        return ""
    source = str(getattr(model_execution, "source", "") or "").strip().lower()
    model_name = str(getattr(model_execution, "model_name", "") or "").strip()
    constraint = details.get("response_constraint")
    if model_name and isinstance(constraint, dict):
        control = constraint.get("response_control")
        if not isinstance(control, dict):
            control = constraint
        completion = control.get("provider_completion")
        final = completion.get("final") if isinstance(completion, dict) else None
        initial = completion.get("initial") if isinstance(completion, dict) else None
        final_reasons = [str(item).casefold() for item in list((final or {}).get("reasons") or [])]
        initial_reasons = [str(item).casefold() for item in list((initial or {}).get("reasons") or [])]
        hit_limit = bool((final or {}).get("at_output_limit")) or any(
            "finish_reason:length" in reason or "output_cap" in reason
            for reason in final_reasons + initial_reasons
        )
        if hit_limit:
            repaired = bool(control.get("retry_attempted"))
            suffix = ", and one more try stopped at the same limit" if repaired else ""
            return (
                f"{UNFINISHED_ANSWER_LEAD}: {model_name} reached its length limit before it was done{suffix}. "
                f"{NOTHING_MADE_UP} You can try again, or ask for a shorter answer."
            )

    if source == "selected_model_blocked" and details.get("model_was_attempted"):
        failure = details.get("provider_failure") or {}
        name = _pinned_model_name(details) or "The model you selected"
        if failure.get("error_class") == "EMPTY_PROVIDER_RESPONSE":
            return f"{NO_ANSWER_LEAD}: {name} sent back an empty reply. {NOTHING_MADE_UP} {TRY_AGAIN}"
        error = str(details.get("block_reason") or details.get("reason") or "")
        if _is_http_rate_limit(error):
            return (
                f"{NO_ANSWER_LEAD}: {name} was turned away by its provider for now (rate limit). "
                "No other model answered in its place. Wait a moment and try again, or pick another model."
            )
    if source not in {"no_provider_available", "provider_fallback_budget_exceeded"}:
        return ""
    timings = [item for item in list(details.get("attempt_timings") or []) if isinstance(item, dict)]
    attempted = [str(item).strip() for item in list(details.get("attempted") or []) if str(item).strip()]
    reasons = [str(item) for item in list(details.get("attempted_error_reasons") or [])]
    attempts: list[tuple[str, str]] = []
    for index, provider_id in enumerate(attempted):
        timing = timings[index] if index < len(timings) else {}
        name = str(timing.get("model_id") or "").strip()
        if not name:
            name = provider_id.split(":", 1)[-1]
        error = str(timing.get("error") or (reasons[index] if index < len(reasons) else ""))
        attempts.append((name, _attempt_failure_words(error, str(timing.get("error_class") or ""))))
    if not attempts:
        return ""
    # Each model the turn tried, in order: "qwen3:4b took too long to reply, then qwen2.5:7b could
    # not start because this computer was short on free memory".
    summary = ", then ".join(f"{name} {kind}" for name, kind in attempts)
    return f"{NO_ANSWER_LEAD}: {summary}. {NOTHING_MADE_UP} {TRY_AGAIN}"


def _single_candidate_failure_hint(model_execution: Any) -> str:
    """Name what actually happened when only one model was TRIED this turn.

    Says nothing when several were attempted - that is an ordinary exhausted-fallback failure and
    the generic sentence is right for it.

    ARGUS-confirmed defect: this used to claim "`{name}` was the only model this turn was allowed
    to use" whenever exactly one model was ATTEMPTED (`len(attempted) == 1`), regardless of how
    many were actually RANKED/eligible. That is false whenever the ranked pool held more than one
    candidate and only one was ever tried -- e.g. the turn's fallback time budget ran out before
    trying the rest, or a policy block (an explicit-heavy-lane failure, an autopilot block) refused
    to try the rest. "Only model allowed" is true only when the ranked pool was genuinely singular
    (including the explicit-pin case, where ranking is narrowed to exactly one provider before any
    attempt is made).
    """

    details = getattr(model_execution, "details", None)
    if not isinstance(details, dict):
        return ""
    attempted = [str(item).strip() for item in list(details.get("attempted") or []) if str(item).strip()]
    if len(attempted) != 1:
        return ""
    name = attempted[0].split(":", 1)[-1] if ":" in attempted[0] else attempted[0]
    ranked_candidates = [str(item).strip() for item in list(details.get("ranked_candidates") or []) if str(item).strip()]

    if len(ranked_candidates) <= 1:
        # Genuinely singular pool: nothing else was ever eligible (an ordinary single-candidate
        # lane), or an explicit pin narrowed ranking to exactly this one provider before any
        # attempt was made. The original claim is true here.
        return (
            f"`{name}` was the only model this turn was allowed to use, and it did not return a usable "
            "reply. I won't answer as a different model than the one selected. Retry, or switch the "
            "model selector to Auto so the turn can fall back to another provider."
        )

    source = str(getattr(model_execution, "source", "") or "").strip().lower()
    untried_count = len(ranked_candidates) - len(attempted)
    if source == "provider_fallback_budget_exceeded":
        return (
            f"`{name}` failed, and this turn's fallback time budget ran out before "
            f"{'the other ranked candidate' if untried_count == 1 else f'the other {untried_count} ranked candidates'} "
            "could be tried. Retry, or allow more time for this turn."
        )
    if source in {"explicit_heavy_lane_failed", "autopilot_blocked"}:
        # Deliberately generic about WHY -- the raw fallback_reason/error string is an internal
        # identifier, not something to hand the user unnecessarily.
        return (
            f"`{name}` failed. This turn was locked to a specific large/heavy model by policy, so the "
            "runtime did not automatically fall back to a smaller one. Retry, or switch the model "
            "selector to Auto so the turn can route to whatever is actually available."
        )
    # Some other reason left only one candidate attempted despite a larger ranked pool. Still must
    # not claim singularity, but without inventing a more specific story than the evidence supports.
    return (
        f"`{name}` failed and did not return a usable reply. Other candidates were ranked for this "
        "turn but were not tried. Retry, or switch the model selector to Auto so the turn can fall "
        "back to another provider."
    )


#: Word-bounded status markers. A bare substring "401" matched the PRICE 1401000 in
#: ``route_price_above_approved_bound: 1401000`` and read a spend refusal as an auth failure;
#: a digit run is one token, so the boundary requirement is what keeps numbers that merely
#: CONTAIN the status out of the auth reading.
_REMOTE_AUTH_REJECTED_RE = re.compile(r"\b401\b|\bunauthorized\b|\binvalid[ _]api[ _]?key\b", re.IGNORECASE)
_REMOTE_ACCESS_REFUSED_RE = re.compile(r"\b403\b|\bforbidden\b", re.IGNORECASE)
_REMOTE_THROTTLED_RE = re.compile(
    r"\b429\b|\brate[ _-]?limit\b|\btoo\s+many\s+requests\b|\bthrottl\w*\b|\bquota\b", re.IGNORECASE
)

#: The adapter's stable pre-dispatch refusal code (adapters.openai_compatible_adapter.
#: ProviderCredentialUnavailableError): the saved credential was missing or unreadable and
#: NOTHING was sent. A local fact, never a provider rejection.
_LOCAL_CREDENTIAL_UNAVAILABLE = "provider_credential_unavailable"


def _provider_failure_evidence(details: Any) -> str:
    """Every error-bearing field the routing decision actually carries, joined for one reading.

    The live 401 masking (2026-09-18) was a data-flow loss, not a vocabulary loss: the decision
    carried the cause on ``block_reason`` and ``attempted_error_reasons``/``attempt_timings``
    while this surface read only ``fallback_reason``/``error`` — so the answer fell through to
    "the only model allowed ... switch to Auto". One reader, every collection the router writes.
    """
    if not isinstance(details, dict):
        return ""
    parts: list[str] = []
    for key in ("fallback_reason", "rejection_reason", "block_reason", "error", "contract_error", "last_error"):
        value = details.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value)
    for key in ("attempted_error_reasons", "attempt_errors"):
        value = details.get(key)
        if isinstance(value, (list, tuple)):
            parts.extend(str(item) for item in value if str(item or "").strip())
    timings = details.get("attempt_timings")
    if isinstance(timings, (list, tuple)):
        for timing in timings:
            if isinstance(timing, dict):
                value = timing.get("error")
                if isinstance(value, str) and value.strip():
                    parts.append(value)
    return " ".join(parts)


#: The typed Retry-After the adapter stamps into a 429 error string (numeric seconds only).
_RETRY_AFTER_MARKER_RE = re.compile(r"\[retry_after=(\d{1,5})s\]")


def _attempt_records(details: Any) -> list[dict[str, str]]:
    """The turn's provider attempts as PER-ATTEMPT records: {provider, error, kind}.

    The router mirrors ``attempt_kinds`` 1:1 with ``attempted``/``attempted_error_reasons``; a
    decision without them falls back to deriving each attempt's kind from its own error string,
    never from the JOINED turn evidence -- an early timeout followed by a local credential
    refusal is two facts about two attempts, and joining them is how "nothing was sent" got
    claimed about a turn whose first call reached the wire.
    """
    if not isinstance(details, dict):
        return []
    attempted = [str(item) for item in list(details.get("attempted") or [])]
    reasons = [str(item) for item in list(details.get("attempted_error_reasons") or [])]
    if not attempted and not reasons:
        return []
    kinds = [str(item) for item in list(details.get("attempt_kinds") or [])]
    records: list[dict[str, str]] = []
    for index in range(max(len(attempted), len(reasons))):
        provider = attempted[index] if index < len(attempted) else ""
        error = reasons[index] if index < len(reasons) else ""
        if index < len(kinds) and kinds[index].strip():
            kind = kinds[index].strip()
        else:
            try:
                from core.turn_routing import classify_provider_error

                kind = classify_provider_error(error).value
            except Exception:
                kind = ""
        records.append({"provider": provider, "error": error, "kind": kind})
    return records


def _attempt_reached_wire(record: dict[str, str]) -> bool:
    """Whether THIS attempt's own evidence says a remote endpoint was contacted.

    Typed kinds: TIMEOUT/UNAVAILABLE/PARTIAL spent their budget on the wire; a remote status in
    the attempt's own error string (401/403/429/5xx) is a provider that ANSWERED. REFUSED and the
    local credential-unavailable code never touched a socket.
    """
    error = str(record.get("error") or "")
    if _LOCAL_CREDENTIAL_UNAVAILABLE in error.lower():
        return False
    if str(record.get("kind") or "") in {"TIMEOUT", "UNAVAILABLE", "PARTIAL"}:
        return True
    return bool(
        re.search(r"\b(?:40[13]|429|5\d\d)\b", error)
        or _REMOTE_AUTH_REJECTED_RE.search(error)
        or _REMOTE_ACCESS_REFUSED_RE.search(error)
        or _REMOTE_THROTTLED_RE.search(error)
    )


def _provider_display_name(model_execution: Any, details: Any) -> str:
    """The provider's UI label, from the lane identity the decision carries.

    ``provider_id`` when present; otherwise the first ATTEMPTED lane id (``openrouter-byok:model``)
    is the only identity the decision still holds, and it is the one the user selected.
    """
    provider = str(getattr(model_execution, "provider_id", "") or "").split(":", 1)[0].strip()
    if not provider and isinstance(details, dict):
        attempted = [str(item).strip() for item in list(details.get("attempted") or []) if str(item).strip()]
        if attempted:
            provider = attempted[0].split(":", 1)[0]
    base = provider.replace("-byok", "").strip()
    if not base:
        return "the cloud provider"
    try:
        from core.cloud_providers import PROVIDERS

        config = PROVIDERS.get(base)
        if config is not None and str(config.label or "").strip():
            return str(config.label).strip()
    except Exception:
        pass
    return base


def _local_attempt_models(details: Any) -> dict[str, str]:
    """``provider_id -> model name`` for every attempt the router marked as running on this computer
    (``attempt_timings[].local``, written from the manifest's own locality)."""

    if not isinstance(details, dict):
        return {}
    found: dict[str, str] = {}
    for timing in list(details.get("attempt_timings") or []):
        if isinstance(timing, dict) and timing.get("local") is True:
            provider_id = str(timing.get("provider_id") or "").strip()
            if provider_id:
                found[provider_id] = str(timing.get("model_id") or "").strip() or provider_id.split(":", 1)[-1]
    if details.get("selected_model_local") is True:
        # An explicit pin carries no per-attempt timings; the router marks the pin itself.
        for provider_id in list(details.get("attempted") or []):
            provider_id = str(provider_id or "").strip()
            if provider_id:
                found.setdefault(provider_id, provider_id.split(":", 1)[-1])
    return found


def _local_model_refusal_hint(details: Any, records: list[dict[str, str]], evidence: str) -> str:
    """Plain words when the model app on THIS computer refused the request (401, 403 or 429), else "".

    The cloud wording names an account, an API key and Settings → API Keys; a local model has none
    of those, so it is used only when a refusing attempt was a cloud one. App bugs 2026-11-12 (real
    app, C3): a 401 from a local model was told "ollama-local rejected the stored API key
    (authentication failed), so no cloud model could run this turn ... Check the key in Settings →
    API Keys". A 401 from a model app on this computer means it asked for a sign-in (an account-run
    model in Ollama, or a password set in front of it).
    """

    local = _local_attempt_models(details)
    if not local:
        return ""

    def _refused(text: str) -> bool:
        return bool(
            _REMOTE_AUTH_REJECTED_RE.search(text)
            or _REMOTE_ACCESS_REFUSED_RE.search(text)
            or _REMOTE_THROTTLED_RE.search(text)
        )

    refusing = [r for r in records if _refused(str(r.get("error") or ""))]
    if not refusing:
        # Only the joined reason carries the status: local only when every attempt was local.
        if not (records and _refused(evidence) and all(str(r.get("provider") or "") in local for r in records)):
            return ""
        refusing = records
    if any(str(r.get("provider") or "") not in local for r in refusing):
        return ""
    names: list[str] = []
    for record in refusing:
        name = local[str(record.get("provider") or "")]
        if name not in names:
            names.append(name)
    model = " and ".join(names)
    said = " ".join(str(r.get("error") or "") for r in refusing) or evidence
    if _REMOTE_AUTH_REJECTED_RE.search(said):
        return (
            f"{NO_ANSWER_LEAD}: the model app on this computer would not run {model} because it asked "
            f"for a sign-in. {NOTHING_MADE_UP} If this model needs an account in your model app (such "
            "as Ollama), sign in there, then try again."
        )
    if _REMOTE_ACCESS_REFUSED_RE.search(said):
        return (
            f"{NO_ANSWER_LEAD}: the model app on this computer refused to run {model}. "
            f"{NOTHING_MADE_UP} Check your model app (such as Ollama), then try again."
        )
    # A 429 keeps the 429 law the cloud wording follows: the model app's own Retry-After, when the
    # attempt carries it, is the wait to name; without it nothing promises that a short wait fixes
    # it (an account-run model in Ollama has a usage limit of its own). CI #681 (2026-11-13): "Wait a
    # moment" stood in for the app's own "27 seconds".
    retry_after = _RETRY_AFTER_MARKER_RE.search(said)
    if retry_after:
        return (
            f"{NO_ANSWER_LEAD}: the model app on this computer was too busy to run {model} just now "
            f"and asked to wait about {int(retry_after.group(1))} seconds. {NOTHING_MADE_UP} Try again "
            "after that wait."
        )
    return (
        f"{NO_ANSWER_LEAD}: the model app on this computer was too busy to run {model} just now. "
        f"{NOTHING_MADE_UP} Try again later. If it keeps happening, your model app (such as Ollama) "
        "may limit how much this model can be used."
    )


def _provider_auth_failure_hint(model_execution: Any) -> str:
    """The actionable message when a provider CREDENTIAL stopped the turn, else "".

    Read PER-ATTEMPT, never from one joined story:

    * ``provider_credential_unavailable`` on EVERY attempt, with no attempt that reached the
      wire — a LOCAL refusal before the wire: the saved key was missing or unreadable, nothing
      was sent, nothing was charged. Never worded as a rejection. But when an EARLIER attempt
      reached the provider (a timeout, a 5xx), "nothing was sent" would be false about the turn
      even though it is true about the last attempt — the wording then says both facts instead.
    * HTTP 401 — the provider RECEIVED a request and rejected the credential.
    * HTTP 403 — an access or policy refusal: possibly the key's permissions, never asserted to
      be a bad key.
    * HTTP 429 — rate/quota limit. The provider's own Retry-After, when the attempt carries it,
      is quoted as typed evidence; without it the wording names the limit WITHOUT pretending a
      short wait resolves it, because a quota exhaustion is not fixed by waiting a moment.

    "" for everything else so every other failure family keeps its own wording.
    """
    details = getattr(model_execution, "details", None)
    if not isinstance(details, dict):
        return ""
    evidence = _provider_failure_evidence(details)
    if not evidence.strip():
        return ""
    who = _provider_display_name(model_execution, details)
    records = _attempt_records(details)
    # Per-attempt law applies only where per-attempt DETAIL exists; a decision that carries only
    # the joined reason (older producers, single-block refusals) keeps the joined-evidence law.
    per_attempt_detail = any(str(r.get("error") or "").strip() for r in records)
    local_refusals = [r for r in records if _LOCAL_CREDENTIAL_UNAVAILABLE in str(r.get("error") or "").lower()]
    wire_attempts = [r for r in records if _attempt_reached_wire(r)]
    remote_in_evidence = bool(
        _REMOTE_AUTH_REJECTED_RE.search(evidence)
        or _REMOTE_ACCESS_REFUSED_RE.search(evidence)
        or _REMOTE_THROTTLED_RE.search(evidence)
    )
    local_in_evidence = _LOCAL_CREDENTIAL_UNAVAILABLE in evidence.lower()
    if local_refusals and not wire_attempts:
        return (
            f"{who} could not run this turn: its saved API key was not available, so the request "
            "was stopped before sending. Nothing was sent and nothing was charged. Restore the key "
            "under Settings → API Keys, then send your message again."
        )
    if local_refusals and wire_attempts:
        first_wire = wire_attempts[0]
        wire_name = str(first_wire.get("provider") or "").split(":", 1)[0].replace("-byok", "") or who
        wire_kind = str(first_wire.get("kind") or "transport error")
        return (
            f"This turn tried {who} and could not finish it. One call reached {wire_name} and failed "
            f"({wire_kind}); the follow-up attempt was then stopped before sending because the saved "
            "API key was not available. Because a call did reach the provider, this is not a "
            "nothing-was-sent turn -- the failed call's cost, if any, is between you and that "
            "provider's billing. Restore the key under Settings → API Keys, then send your message "
            "again."
        )
    if not per_attempt_detail and local_in_evidence and not remote_in_evidence:
        return (
            f"{who} could not run this turn: its saved API key was not available, so the request "
            "was stopped before sending. Nothing was sent and nothing was charged. Restore the key "
            "under Settings → API Keys, then send your message again."
        )
    if details.get("model_was_attempted") is False:
        # A remote status answer presupposes a sent request. The authority that built this
        # decision says the model was never attempted, so an HTTP-shaped string in the reason is
        # a gate's own code, not the provider's reply -- never worded as provider contact.
        return ""
    local_refusal = _local_model_refusal_hint(details, records, evidence)
    if local_refusal:
        return local_refusal
    throttled = [r for r in records if _REMOTE_THROTTLED_RE.search(str(r.get("error") or ""))]
    throttle_source = (
        str(throttled[0].get("error") or "") if throttled
        else (evidence if _REMOTE_THROTTLED_RE.search(evidence) else "")
    )
    if throttle_source:
        retry_match = _RETRY_AFTER_MARKER_RE.search(throttle_source)
        wait_clause = (
            f" The provider's own Retry-After header says to wait about {int(retry_match.group(1))} "
            "seconds before sending again."
            if retry_match
            else ""
        )
        return (
            f"{who} throttled this request (rate or quota limit, HTTP 429), so nothing was answered."
            f"{wait_clause}"
            + (
                " If it keeps happening after the wait, check the account's rate limits and quota for "
                "the models in use."
                if retry_match
                else " If it keeps happening, check the account's rate limits and quota for the "
                "models in use -- a quota exhaustion is not resolved by waiting."
            )
        )
    if _REMOTE_AUTH_REJECTED_RE.search(evidence):
        return (
            f"{who} rejected the stored API key (authentication failed), so no cloud model could run "
            "this turn. Nothing was answered from cache or memory. Check the key in Settings → API "
            "Keys — it has most likely expired, been revoked, or been replaced."
        )
    if _REMOTE_ACCESS_REFUSED_RE.search(evidence):
        return (
            f"{who} refused the request (access denied). The request reached the provider, and this "
            "can be the key's permissions or an account policy — it is not necessarily a wrong key. "
            "Check the key and its permissions under Settings → API Keys, then send your message again."
        )
    return ""


def _disclosure_preview_needed_hint(model_execution: Any) -> str:
    """A cloud call stopped at a preview its caller cannot show is a wait for review, not a failure.

    The disclosure wire raises it before anything is sent (core.disclosure.wire.
    DisclosurePreviewNeeded); worded as a model failure it would send the user to the model
    selector instead of to the chat page, where the preview is waiting for them.
    """
    from core.disclosure.wire import PREVIEW_NEEDED_DETAIL

    details = getattr(model_execution, "details", None)
    if not isinstance(details, dict):
        return ""
    return PREVIEW_NEEDED_DETAIL if PREVIEW_NEEDED_DETAIL in _provider_failure_evidence(details) else ""


def _cancelled_turn_hint(model_execution: Any) -> str:
    """A cancelled call is a stop, not a model failure — never worded as "no usable reply".

    ``model_call_cancelled`` is the adapter's own stable code (raised at the dispatch boundary and
    on mid-stream cancellation). It reached this surface as "did not return a usable reply", which
    reads as the model failing when the turn was in fact stopped. Says what stopped and what
    restarting does, without claiming anything about a charge.
    """
    details = getattr(model_execution, "details", None)
    if not isinstance(details, dict):
        return ""
    evidence = _provider_failure_evidence(details).lower()
    if "model_call_cancelled" not in evidence and "cancelled" not in str(
        getattr(model_execution, "source", "") or ""
    ).lower():
        return ""
    if "provider_credential_unavailable" in evidence or _REMOTE_AUTH_REJECTED_RE.search(evidence):
        # A typed cause already named above owns the wording; cancellation did not stop this turn.
        return ""
    return (
        "This turn was cancelled before the model finished, so there is no answer to show. "
        "Send the message again to retry it."
    )
