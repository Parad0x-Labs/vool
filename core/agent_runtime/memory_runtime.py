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
        return agent._fast_path_result(
            session_id=session_id,
            user_input=user_input,
            response=response,
            confidence=0.93,
            source_context=source_context,
            reason="memory_command",
        )
    return agent._maybe_handle_companion_memory_fast_path(
        user_input,
        session_id=session_id,
        source_context=source_context,
    )


def model_final_response_text(model_execution: Any) -> str:
    final_text = str(getattr(model_execution, "output_text", "") or "").strip()
    if final_text:
        return final_text
    structured = getattr(model_execution, "structured_output", None)
    if isinstance(structured, dict):
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
    # A wallet-paid call whose answer never arrived is a money fact before it is a routing one: it did run and it was paid,
    # so it is never reported as a model that could not run.
    paid_hint = _paid_result_unknown_hint(model_execution)
    if paid_hint:
        return paid_hint

    provider_hint = _provider_auth_failure_hint(model_execution)
    if provider_hint:
        return provider_hint

    cancelled_hint = _cancelled_turn_hint(model_execution)
    if cancelled_hint:
        return cancelled_hint

    runtime_hint = _typed_model_runtime_failure_hint(model_execution)
    if runtime_hint:
        return runtime_hint

    # A prepaid UsePod dispatch the adapter refused BEFORE sending is a money/verification fact, not a
    # model failure: the request never left, nothing was charged. Judged before the single-candidate
    # hint below, which cannot tell "refused before send" from "answered nothing" and used to say
    # "did not return a usable reply ... switch to Auto" for exactly this case (observed live
    # 2026-09-16 02:26: MONEY_LIQUIDITY_UNVERIFIED reported as the model returning no reply).
    prepaid_hint = _usepod_prepaid_refusal_hint(model_execution)
    if prepaid_hint:
        return prepaid_hint

    # A UsePod 402 is the PROVIDER's answer after the request was sent, and it says one of several
    # things. "no_provider_at_price" is capacity: nobody was serving the model at the approved
    # price when the request arrived (a listing does not guarantee serving capacity). It is not
    # the local price gate (refused before sending, above) and it is not an empty balance.
    transport_hint = _usepod_transport_payment_hint(model_execution)
    if transport_hint:
        return transport_hint

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
        money_code = _usepod_refusal_code(block_reason)
        if requested.startswith("usepod:") and money_code in _MONEY_AUTHORITY_CAUSES:
            return (f"{_MONEY_AUTHORITY_CAUSES[money_code]}. {name} was not contacted. "
                    "Open Settings → Models & Providers → UsePod spending to review and allow a fresh budget, "
                    "then send your message again. Selecting a model does not renew a spending budget.")
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
        return (
            "I couldn't get a live model response in this run, so I'm not going to recycle cached or remembered "
            "text as if it were fresh."
        )
    return (
        "I couldn't get a usable model response in this run, so I'm not going to recycle cached or remembered "
        "text as if it were fresh."
    )


#: Named by the UsePod transport on a failure after the wallet's proof was bound (X402PaidResultUnknownError).
_PAID_RESULT_UNKNOWN_MARKER = "usepod_x402_paid_result_unknown"

#: The UsePod adapter's own prefix for a dispatch it refused BEFORE sending (adapters.usepod_adapter.
#: UsePodDispatchRefusedError). The router carries the exception text as the block reason.
_USEPOD_REFUSAL_PREFIX = "usepod_dispatch_refused:"
_MONEY_AUTHORITY_CAUSES = {
    "MONEY_AUTHORITY_EXPIRED": "Your spending approval expired",
    "MONEY_AUTHORITY_REVOKED": "Your spending approval was revoked",
    "MONEY_AUTHORITY_EXHAUSTED": "Your approved spending allowance has been used",
    "MONEY_AUTHORITY_INVALID": "No applicable spending approval is available",
    "MONEY_AUTHORITY_REQUIRED": "Spending approval is required",
}
_BUDGET_RECOVERY = (
    "Open Settings → Models & Providers → UsePod spending to review and allow a fresh budget, "
    "then send your message again. Selecting a model does not renew a spending budget."
)


def _usepod_refusal_code(block_reason: str) -> str:
    """The adapter's refusal code without its ``usepod_dispatch_refused:`` prefix (a bare code passes through)."""
    reason = str(block_reason or "").strip()
    if reason.startswith(_USEPOD_REFUSAL_PREFIX):
        reason = reason[len(_USEPOD_REFUSAL_PREFIX):]
    return reason.strip()


def _usepod_prepaid_refusal_hint(model_execution: Any) -> str:
    """One plain statement for a UsePod dispatch the adapter refused BEFORE sending.

    Says three things the receipt already established and the old text contradicted: nothing was
    sent, nothing was charged, and WHICH verification blocked it -- a balance the money law could not
    verify, a balance too small for this request's maximum, an expired/revoked/spent budget, or a
    route/price fact. Each names its own recovery. Auto is never offered as the remedy: a balance or
    budget defect is not fixed by answering from another provider. "" for anything else -- an
    after-send transport failure on a UsePod pin, a bare gate code, another provider -- so every
    other lane keeps its own wording. The pre-send families are the footer's
    (core.response_provenance.usepod_pre_send_refusal_code): one reading, two surfaces."""
    from core.response_provenance import usepod_pre_send_refusal_code

    details = getattr(model_execution, "details", None)
    if not isinstance(details, dict):
        return ""
    source = str(getattr(model_execution, "source", "") or "").strip().lower()
    if source != "selected_model_blocked":
        return ""
    requested = str(details.get("requested_model") or "").strip()
    code = usepod_pre_send_refusal_code(str(details.get("block_reason") or details.get("reason") or ""))
    if not code:
        return ""
    name = f"`{requested}`" if requested else "The UsePod model you selected"
    not_sent = f"{name} was not contacted: nothing was sent and nothing was charged."
    if code == "MONEY_LIQUIDITY_UNVERIFIED":
        return (
            f"UsePod could not verify your account balance before sending, so this request was refused. {not_sent} "
            "Open Settings → Models & Providers → UsePod, check that the token is accepted (Test) and that the balance "
            "reads, then send your message again."
        )
    if code == "MONEY_LIQUIDITY_INSUFFICIENT":
        return (
            f"Your UsePod balance does not cover this request's maximum cost after the spending already held, so it "
            f"was refused. {not_sent} Top up the UsePod account or shorten the request, then send it again."
        )
    if code in _MONEY_AUTHORITY_CAUSES:
        return f"{_MONEY_AUTHORITY_CAUSES[code]}. {not_sent} {_BUDGET_RECOVERY}"
    if code.startswith("MONEY_"):
        return (
            f"The spending check refused this request ({code}). {not_sent} "
            "Open Settings → Models & Providers → UsePod spending to review the budget, then send your message again."
        )
    if code in {"price_stale", "price_feed_unavailable"}:
        return (
            f"The UsePod price feed is {'stale' if code == 'price_stale' else 'unavailable'}, so the maximum cost of this request "
            f"could not be bounded and it was refused. {not_sent} Open Settings → Models & Providers → UsePod and refresh "
            "the marketplace prices, then send your message again."
        )
    if code == "route_price_above_approved_bound":
        # Name the axes the refusal actually measured, from the adapter's own evidence carried on
        # the decision (saved maxima vs current prices, exact USDC per 1M). With no evidence the
        # wording falls back to the axes-free sentence and says it has no values to show.
        axes = _usepod_price_refusal_axes(details)
        if axes:
            axis_text = " and ".join(
                f"the current {axis['axis']} price {axis['observed_usdc_per_million']} USDC per 1M is above your saved maximum {axis['saved_max_usdc_per_million']}"
                for axis in axes
            )
            return (
                f"UsePod refused this request before sending it: {axis_text}. {not_sent} "
                "Use Review price limits for this model to raise the maxima you are willing to pay, then send your message again."
            )
        return (
            f"UsePod refused this request before sending it: the current price is above the maximum you approved for this model. {not_sent} "
            "Use Review price limits for this model to raise the maxima you are willing to pay, then send your message again."
        )
    if code in {"route_not_approved", "route_policy_changed_since_approval", "model_disappeared"}:
        return (
            f"The route for this model is not approved at the current prices ({code}), so the request was refused. {not_sent} "
            "Open Settings → Models & Providers → UsePod and approve the route for this model, then send your message again."
        )
    if code in {"usepod_token_not_configured", "usepod_credential_pair_unresolved"}:
        return (
            f"No usable UsePod token is stored right now ({code}). {not_sent} "
            "Save the UsePod token under Settings → API Keys, then send your message again."
        )
    return (
        f"UsePod refused this request before sending it ({code}). {not_sent} "
        "Review the UsePod settings for this model, then send your message again."
    )



def _usepod_price_refusal_axes(details: Any) -> list[dict[str, Any]]:
    """The violating price axes off a blocked decision's carried route evidence, else [].

    The evidence is the adapter's own typed ``route_evidence`` (see
    ``core.usepod.routing.price_refusal_axes``): ids and exact human-unit prices only, never a
    credential. Absent evidence returns [] — the caller words the refusal without values rather
    than inventing any.
    """
    if not isinstance(details, dict):
        return []
    evidence = details.get("block_route_evidence")
    if not isinstance(evidence, dict):
        return []
    axes = evidence.get("refusal_axes")
    if not isinstance(axes, list):
        return []
    out = []
    for axis in axes:
        if not isinstance(axis, dict):
            continue
        if str(axis.get("axis") or "") not in {"input", "output"}:
            continue
        observed = str(axis.get("observed_usdc_per_million") or "").strip()
        saved = str(axis.get("saved_max_usdc_per_million") or "").strip()
        if observed and saved:
            out.append({"axis": str(axis["axis"]), "observed_usdc_per_million": observed, "saved_max_usdc_per_million": saved})
    return out


_USEPOD_TRANSPORT_PAYMENT_RE = re.compile(
    r"usepod_transport:payment_or_balance_required(?:\s+status=(?P<status>\d+))?"
    r"(?:\s+dispatch=(?P<dispatch>[a-z_]+))?(?::\s*(?P<hint>[A-Za-z0-9_.:-]+))?"
)


def _usepod_transport_payment_hint(model_execution: Any) -> str:
    """One plain statement for a UsePod HTTP 402 answered AFTER the request was sent.

    Reads the transport's own code and hint (`core.usepod.transport.error_for_response`), keeps
    the distinction the provider drew, and never turns every 402 into an empty balance:

    * ``no_provider_at_price`` -- no provider was serving this model at the approved price when
      the request arrived; capacity, not money;
    * ``insufficient_balance`` / ``insufficient_funds`` -- the provider says the prepaid balance
      did not cover the call;
    * anything else -- a 402 with the provider's own hint, quoted as a code.

    The request DID leave, so the reservation is retained until its owner reconciles it (the
    receipt already says "LIABILITY RETAINED"); this text says so instead of "did not return a
    usable reply". "" for every other failure.
    """
    details = getattr(model_execution, "details", None)
    if not isinstance(details, dict):
        return ""
    source = str(getattr(model_execution, "source", "") or "").strip().lower()
    if source != "selected_model_blocked":
        return ""
    reason = str(details.get("block_reason") or details.get("reason") or "")
    match = _USEPOD_TRANSPORT_PAYMENT_RE.search(reason)
    if match is None:
        return ""
    requested = str(details.get("requested_model") or "").strip()
    name = f"`{requested}`" if requested else "The UsePod model you selected"
    hint = str(match.group("hint") or "").strip().lower()
    status = str(match.group("status") or "402")
    retained = (
        "The request was sent, so its spending reservation stays retained until UsePod's record of "
        "the operation is reconciled; nothing was answered and no other model was used."
    )
    if hint == "no_provider_at_price":
        return (
            f"UsePod answered HTTP {status} no_provider_at_price: no provider was serving {name} at the "
            f"approved price when the request arrived. A marketplace listing does not guarantee serving "
            f"capacity. {retained} Send the message again later, or review the approved route for this "
            "model under Settings → Models & Providers → UsePod."
        )
    if hint in {"insufficient_balance", "insufficient_funds", "balance_required", "payment_required"}:
        return (
            f"UsePod answered HTTP {status} {hint}: the provider reports that the prepaid balance did not "
            f"cover this request to {name}. {retained} Top up the UsePod account, then send the message again."
        )
    shown = f" ({hint})" if hint else ""
    return (
        f"UsePod answered HTTP {status} payment or balance required{shown} for {name}. {retained} "
        "Review the UsePod account and route under Settings → Models & Providers → UsePod, then send the "
        "message again."
    )


def _paid_result_unknown_hint(model_execution: Any) -> str:
    """A call the wallet paid for whose answer never arrived: paid, result unknown.

    Nothing is made up in the answer's place and no refund is implied. The operation stays unresolved until it is resumed,
    which resends the same paid request without paying again."""
    details = getattr(model_execution, "details", None)
    if not isinstance(details, dict):
        return ""
    reasons = [str(details.get("block_reason") or ""), str(details.get("reason") or "")]
    reasons += [str(item) for item in list(details.get("attempted_error_reasons") or [])]
    if not any(_PAID_RESULT_UNKNOWN_MARKER in reason for reason in reasons):
        return ""
    requested = str(details.get("requested_model") or "").strip()
    attempted = [str(item).strip() for item in list(details.get("attempted") or []) if str(item).strip()]
    name = requested or (attempted[0].split(":", 1)[-1] if attempted else "")
    label = f"`{name}`" if name else "The model you selected"
    return (
        f"{label} was paid from your wallet for this turn, but its answer never arrived, so the result is unknown. "
        "No answer was made up in its place and no refund is assumed: the payment stays on record as unresolved. "
        "Settings lists it under Paid calls waiting for an answer, where Resume asks for this same answer once "
        "without paying again."
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
    if code == "usepod_route_not_approved":
        # The paid-call reservation refused BEFORE sending: no approved price bound exists for
        # this model's route. A local gate, never a provider answer -- and not an empty balance.
        return (
            "its UsePod route is not approved at the current prices, so nothing was sent and "
            "nothing was charged (approve the route under Settings → Models & Providers → UsePod)"
        )
    if code == "usepod_route_state_unavailable":
        return "its UsePod route state could not be read, so nothing was sent and nothing was charged"
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


def _typed_model_runtime_failure_hint(model_execution: Any) -> str:
    """Describe a measured local failure without exposing raw provider errors.

    The turn trace keeps full diagnostics.  This surface names only the models and typed condition
    (timeout, memory admission, output limit), which is enough to distinguish a model/runtime
    failure from a weak answer without leaking endpoints, headers, or provider response bodies.
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
            suffix = " and its one bounded repair also hit the limit" if repaired else ""
            return (
                f"`{model_name}` hit its output limit before it completed the answer{suffix}. "
                "No cached or remembered text was substituted. Retry the turn."
            )

    if source == "selected_model_blocked" and details.get("model_was_attempted"):
        error = str(details.get("block_reason") or details.get("reason") or "")
        if _is_http_rate_limit(error):
            name = str(details.get("requested_model") or "Selected model")
            return (f"`{name}` was refused by its provider with HTTP 429 (rate limit). "
                    "Wait and retry, or select another model. No alternate model was used.")
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
        lowered = error.casefold()
        if _is_http_rate_limit(error):
            kind = "was refused by its provider with HTTP 429 (rate limit)"
        elif "model_load_gated_low_memory" in lowered or "low_memory" in lowered:
            kind = "was blocked by the local memory-safety admission gate"
        elif "timed out" in lowered or "timeout" in lowered:
            kind = "timed out before producing a usable answer"
        elif "connection" in lowered or "unreachable" in lowered:
            kind = "could not reach its local runtime"
        elif error:
            kind = "failed before producing a usable answer"
        else:
            kind = "did not produce a usable answer"
        attempts.append((name, kind))
    if not attempts:
        return ""
    clauses = [f"`{name}` {kind}" for name, kind in attempts]
    if len(clauses) == 1:
        summary = clauses[0]
    else:
        summary = "; ".join(clauses[:-1]) + f"; fallback `{attempts[-1][0]}` {attempts[-1][1]}"
    return f"{summary}. No cached or remembered text was substituted. Retry the turn."


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
