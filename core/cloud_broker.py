from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, replace
from typing import Any

from core.cloud_privacy_policy import CloudPrivacyGrant, evaluate_cloud_privacy
from core.cloud_provider_contract import (
    CloudModelMetadata,
    CloudModelRequest,
    CloudModelResponse,
    CloudTaskRequirements,
    PricingState,
    ProviderError,
    ProviderErrorKind,
)
from core.cloud_route_receipt import (
    issue_cloud_route_receipt,
    last_cloud_route_receipt_hash,
    record_cloud_route_receipt,
)
from core.cloud_routing import CloudRouteMode, CloudRoutePlan, select_cloud_route
from core.model_spend_ledger import settle_spend
from core.output_budget_policy import (
    FREE_CLOUD,
    PAID_CLOUD,
    REMOTE_UNKNOWN,
    LaneCapability,
    OutputBudgetIntent,
    ResolvedOutputBudget,
    resolve_output_budget,
)
from core.provider_execution_boundary import invoke_provider_execution_boundary, provider_execution_boundary
from storage.cloud_model_catalog import list_provider_catalog, replace_provider_catalog

_RETRYABLE = {
    ProviderErrorKind.TEMPORARY,
    ProviderErrorKind.RATE_LIMITED,
    ProviderErrorKind.NETWORK,
    ProviderErrorKind.MALFORMED_RESPONSE,
}
# Failures the gateway returns BEFORE any model ran: a rejected key, a model it no longer serves, a
# rate or quota wall. The prompt reached the gateway and nothing else, so the attempt disclosed the
# turn to no model. Everything else (timeouts, 5xx, malformed output) may have reached one.
_REJECTED_BEFORE_INFERENCE = {
    ProviderErrorKind.AUTH,
    ProviderErrorKind.MODEL_REMOVED,
    ProviderErrorKind.RATE_LIMITED,
    ProviderErrorKind.QUOTA_EXHAUSTED,
}


@dataclass(frozen=True)
class CloudBrokerResult:
    used_cloud: bool
    response: CloudModelResponse | None
    provider_id: str = ""
    model_id: str = ""
    model_call_id: str = ""
    attempts: int = 0
    actual_usd: float = 0.0
    fallback_reason: str = ""
    errors: tuple[str, ...] = ()
    pricing_state: str = ""
    #: Bounded, sanitized detail for each entry the loop appended to ``errors`` that carried
    #: one (a validator's typed reason, the adapter's own malformed-response text). ``errors``
    #: stays the stable kind vocabulary; this is what the Activity trail shows next to it.
    error_details: tuple[str, ...] = ()


_ERROR_DETAIL_MAX_CHARS = 240
_OWN_ADAPTER_ERROR_PREFIXES = ("malformed provider response", "malformed provider output")


def _bounded_detail(text: str) -> str:
    return " ".join(str(text or "").split())[:_ERROR_DETAIL_MAX_CHARS]


def _exception_detail(exc: BaseException, error: ProviderError) -> str:
    """The adapter's OWN typed text when the exception is ours; the sanitized message otherwise.

    Transport and provider exceptions can quote response bodies, so only messages this codebase
    minted (the ``malformed provider ...`` family) are carried verbatim.
    """
    text = str(exc or "")
    if isinstance(exc, RuntimeError) and text.startswith(_OWN_ADAPTER_ERROR_PREFIXES):
        return text
    return str(getattr(error, "safe_message", "") or "")


def lane_output_budget(request: CloudModelRequest, model: CloudModelMetadata) -> ResolvedOutputBudget:
    """The answer budget for THIS request on THIS model, from the one output-budget authority.

    The router hands the broker the prompt layer's number (a chat turn asks for 220-520 tokens,
    sized when every lane was a small local model). The broker is the first place the serving
    model is actually known -- its context window, its published completion cap, its cost class
    and whether the catalog says it reasons -- so this is where ``core.output_budget_policy``
    is applied with a REAL capability instead of ``None``. The lift is a ceiling, not a spend:
    only emitted tokens are billed, and for a paid model the lifted number is what the paid
    estimate and reservation gate are re-checked against before the call.
    """
    asked = max(0, int(request.max_output_tokens or 0))
    if model.pricing_state in (PricingState.FREE, PricingState.PROMOTIONAL):
        cost_class = FREE_CLOUD
    elif model.pricing_state == PricingState.PAID:
        cost_class = PAID_CLOUD
    else:
        cost_class = REMOTE_UNKNOWN
    return resolve_output_budget(
        OutputBudgetIntent(
            output_mode="tool_intent" if request.tools_required else "plain_text",
            base_tokens=asked,
            floor=asked,
            ceiling=0,
            reason="cloud_broker_lane",
        ),
        LaneCapability(
            context_window=max(0, int(model.context_window or 0)),
            max_output_tokens=max(0, int(model.max_output_tokens or 0)),
            cost_class=cost_class,
            thinking_capable="reasoning" in tuple(model.capabilities or ()),
            runtime_family=str(model.provider_id or ""),
        ),
    )


def _budget_fields(budget: ResolvedOutputBudget, *, thinking_capable: bool) -> dict[str, Any]:
    return {
        "intent_base": int(budget.intent_base),
        "tokens": int(budget.tokens),
        "source": str(budget.source),
        "capped_by": str(budget.capped_by),
        "reserve_applied": int(budget.reserve_applied),
        "thinking_capable": bool(thinking_capable),
    }


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class CloudModelBroker:
    def __init__(
        self,
        *,
        registry: Any,
        transports: dict[str, Any],
        sleeper: Any = time.sleep,
    ) -> None:
        self._registry = registry
        self._transports = dict(transports)
        self._sleep = sleeper

    def _enabled_provider_ids(self) -> tuple[str, ...]:
        return tuple(provider.provider_id for provider in self._registry.list() if provider.provider_id in self._transports)

    @provider_execution_boundary
    def refresh_catalog(self, provider_id: str) -> tuple[CloudModelMetadata, ...]:
        provider = self._registry.get(provider_id)
        transport = self._transports.get(provider_id)
        if provider is None or transport is None:
            return ()
        models = invoke_provider_execution_boundary(provider, "discover_models", transport)
        try:
            limits = invoke_provider_execution_boundary(provider, "get_account_limits", transport)
        except Exception:
            limits = None
        if limits is not None:
            models = tuple(
                replace(
                    model,
                    quota_remaining=_minimum_known(model.quota_remaining, limits.quota_remaining),
                    rate_limit_remaining=_minimum_known(
                        model.rate_limit_remaining,
                        limits.rate_limit_remaining,
                    ),
                )
                for model in models
            )
        replace_provider_catalog(provider_id, models)
        return models

    def plan(
        self,
        *,
        requirements: CloudTaskRequirements,
        mode: CloudRouteMode,
        privacy_allowed: bool,
        paid_approved: bool = False,
        paid_budget_remaining_usd: float = 0.0,
        preferred_model_id: str = "",
    ) -> CloudRoutePlan:
        enabled = self._enabled_provider_ids()
        if mode == CloudRouteMode.LOCAL_ONLY:
            return select_cloud_route(
                (),
                requirements=requirements,
                mode=mode,
                enabled_providers=enabled,
                network_allowed_providers=enabled,
                privacy_allowed=privacy_allowed,
            )
        models: list[CloudModelMetadata] = []
        for provider_id in enabled:
            try:
                models.extend(self.refresh_catalog(provider_id))
            except Exception:
                models.extend(list_provider_catalog(provider_id))
        pinned = str(preferred_model_id or "").strip()
        if pinned:
            # A user-selected Auto fallback is a strict contract just like a composer pin. If it is
            # unavailable, remain local; never drift to a different cloud model behind the label.
            models = [model for model in models if model.model_id == pinned]
        return select_cloud_route(
            tuple(models),
            requirements=requirements,
            mode=mode,
            enabled_providers=enabled,
            network_allowed_providers=enabled,
            privacy_allowed=privacy_allowed,
            paid_approved=paid_approved,
            paid_budget_remaining_usd=paid_budget_remaining_usd,
        )

    @provider_execution_boundary
    def execute(
        self,
        request: CloudModelRequest,
        *,
        requirements: CloudTaskRequirements,
        mode: CloudRouteMode,
        privacy_grant: CloudPrivacyGrant | None = None,
        payload_texts: tuple[str, ...] = (),
        paths: tuple[str, ...] = (),
        authorized_paid_call: Any | None = None,
        paid_budget_remaining_usd: float = 0.0,
        max_attempts: int = 3,
        response_validator: Any | None = None,
        event_sink: Any | None = None,
    ) -> CloudBrokerResult:
        if mode == CloudRouteMode.LOCAL_ONLY:
            return CloudBrokerResult(False, None, fallback_reason="local_only")
        privacy = evaluate_cloud_privacy(
            privacy_class=requirements.privacy_class,
            payload_texts=payload_texts,
            paths=paths,
            data_categories=requirements.data_categories,
            grant=privacy_grant,
        )
        if not privacy.allowed:
            return CloudBrokerResult(False, None, fallback_reason=privacy.reason)
        paid_approved = authorized_paid_call is not None
        plan = self.plan(
            requirements=requirements,
            mode=mode,
            privacy_allowed=True,
            paid_approved=paid_approved,
            paid_budget_remaining_usd=paid_budget_remaining_usd,
            preferred_model_id=str(request.model_id or "").strip(),
        )
        if plan.primary is None:
            return CloudBrokerResult(False, None, fallback_reason=plan.route_reason)

        candidates = (plan.primary, *plan.fallbacks)
        authorized_model_id = str(
            getattr(getattr(authorized_paid_call, "escalation", None), "model_id", "") or ""
        )
        if authorized_paid_call is not None:
            candidates = tuple(
                model
                for model in candidates
                if model.pricing_state != PricingState.PAID or model.model_id == authorized_model_id
            )
        if not candidates:
            return CloudBrokerResult(
                False,
                None,
                fallback_reason="paid_authorization_mismatch",
                errors=("paid_authorization_mismatch",),
            )
        allow_fallback = bool((privacy_grant or CloudPrivacyGrant()).allow_provider_fallback)
        attempt_limit = max(1, min(int(max_attempts), 5))
        attempt_count = 0
        errors: list[str] = []
        error_details: list[str] = []
        fallback_chain = tuple(model.catalog_key for model in candidates)
        # A11 execution-truth provenance: what receipts report as `fallback_from` is the ACTUAL
        # immediately preceding EXECUTED hop — recorded only once an attempt's signed "started"
        # receipt has landed (guards that break to the next candidate BEFORE issuing any
        # receipt never fabricate a predecessor) — not the planned candidate chain. Empty means
        # no previous distinct model executed: first-try wins and same-model retries say "".
        last_executed_key = ""
        session_id = str(request.metadata.get("session_id") or "local")
        # The fanout grant is a disclosure budget, not a candidate count: without it the prompt may
        # reach ONE model. A gateway that rejected the attempt before inference never became that
        # model, so the next candidate on the SAME gateway is still the first real recipient and the
        # chain keeps walking. A different gateway is a new recipient and still needs the grant.
        # Measured 2026-09-08 (operator profile, build 0.5.0): one 40x on the top auto-picked free
        # model ended the whole turn with two untried candidates in the signed chain.
        disclosed_to_a_model = False
        contacted_providers: set[str] = set()

        for candidate_index, candidate in enumerate(candidates):
            if (
                candidate_index > 0
                and not allow_fallback
                and (disclosed_to_a_model or candidate.provider_id not in contacted_providers)
            ):
                break
            retry_index = 0
            while attempt_count < attempt_limit:
                attempt_count += 1
                validator_detail_recorded = False
                provider = self._registry.get(candidate.provider_id)
                transport = self._transports.get(candidate.provider_id)
                if provider is None or transport is None:
                    errors.append("provider_unavailable")
                    break
                try:
                    fresh_models = self.refresh_catalog(candidate.provider_id)
                except Exception as exc:
                    error = provider.classify_provider_error(exc)
                    errors.append(error.kind.value)
                    break
                fresh = next((item for item in fresh_models if item.model_id == candidate.model_id), None)
                if fresh is None:
                    errors.append(ProviderErrorKind.MODEL_REMOVED.value)
                    break
                # The serving model is known from here on: resolve the answer budget against
                # ITS capability, and re-check the route (paid estimate, reservation) against
                # the tokens that will really be sent, never against the prompt layer's number.
                budget = lane_output_budget(request, fresh)
                budget_thinking = "reasoning" in tuple(fresh.capabilities or ())
                lane_requirements = requirements
                if budget.tokens > max(0, int(requirements.expected_output_tokens or 0)):
                    lane_requirements = replace(requirements, expected_output_tokens=int(budget.tokens))
                recheck = select_cloud_route(
                    (fresh,),
                    requirements=lane_requirements,
                    mode=mode,
                    enabled_providers=(fresh.provider_id,),
                    network_allowed_providers=(fresh.provider_id,),
                    privacy_allowed=True,
                    paid_approved=paid_approved,
                    paid_budget_remaining_usd=paid_budget_remaining_usd,
                )
                if recheck.primary is None:
                    errors.append(recheck.rejected[0]["reason"] if recheck.rejected else "execution_recheck_failed")
                    break
                if fresh.pricing_state == PricingState.PAID and not self._paid_authorization_matches(
                    authorized_paid_call, request=request, model=fresh
                ):
                    errors.append("paid_authorization_mismatch")
                    break

                model_call_id = str(
                    getattr(authorized_paid_call, "model_call_id", "") or request.model_call_id or f"model-call-{uuid.uuid4().hex}"
                )
                attempt_id = f"cloud-attempt-{uuid.uuid4().hex}"
                native_strict_supported = "structured_output" in fresh.capabilities
                candidate_tools = tuple(
                    replace(tool, strict=native_strict_supported) for tool in request.tools
                )
                call_request = replace(
                    request,
                    model_id=fresh.model_id,
                    model_call_id=model_call_id,
                    tools=candidate_tools,
                    max_output_tokens=(
                        int(budget.tokens) if budget.tokens > 0 else int(request.max_output_tokens or 0)
                    ),
                )
                estimated_max = float(recheck.estimated_max_usd or 0.0)
                current_executed_key = str(fresh.catalog_key or f"{fresh.provider_id}:{fresh.model_id}")
                predecessor_key = (
                    last_executed_key
                    if last_executed_key and last_executed_key != current_executed_key
                    else ""
                )
                preflight = issue_cloud_route_receipt(
                    attempt_id=attempt_id,
                    phase="started",
                    session_id=session_id,
                    task_id=request.task_id,
                    turn_id=request.turn_id,
                    subtask_id=request.subtask_id,
                    model_call_id=model_call_id,
                    provider_id=fresh.provider_id,
                    model_id=fresh.model_id,
                    pricing_state=fresh.pricing_state.value,
                    estimated_max_usd=estimated_max,
                    privacy_class=requirements.privacy_class.value,
                    data_categories=requirements.data_categories,
                    policy_decision=mode.value,
                    route_reason=recheck.route_reason,
                    retry_index=retry_index,
                    fallback_chain=fallback_chain,
                    prev_hash=last_cloud_route_receipt_hash(session_id),
                    requested_model=str(
                        request.metadata.get("requested_model") or request.model_id or ""
                    ),
                    selection_mode=str(request.metadata.get("selection_mode") or ""),
                    lane="cloud",
                    fallback_from=predecessor_key,
                )
                try:
                    record_cloud_route_receipt(preflight)
                except Exception:
                    return CloudBrokerResult(
                        False,
                        None,
                        attempts=attempt_count,
                        fallback_reason="signed_receipt_unavailable",
                        errors=tuple(errors),
                    )

                # The hop is now committed to the signed trail: it counts as executed.
                last_executed_key = current_executed_key
                contacted_providers.add(str(fresh.provider_id))

                if callable(event_sink):
                    event_sink(
                        "model.call_started",
                        {
                            "task_id": request.task_id,
                            "turn_id": request.turn_id,
                            "subtask_id": request.subtask_id,
                            "model_call_id": model_call_id,
                            "provider_id": fresh.provider_id,
                            "model_id": fresh.model_id,
                            "pricing_state": fresh.pricing_state.value,
                            "cost_class": f"cloud_{fresh.pricing_state.value}",
                            "estimated_cost": estimated_max,
                            "route_reason": recheck.route_reason,
                            "max_output_tokens": int(call_request.max_output_tokens or 0),
                            "output_budget": _budget_fields(budget, thinking_capable=budget_thinking),
                        },
                    )

                actual_usd: float | None = None
                response_usage: dict[str, Any] = {}
                try:
                    response = invoke_provider_execution_boundary(
                        provider,
                        "send_request",
                        transport,
                        call_request,
                    )
                    if callable(response_validator):
                        validation = response_validator(response)
                        valid = bool(validation[0]) if isinstance(validation, tuple) else bool(validation)
                        validation_error = str(validation[1] or "") if isinstance(validation, tuple) and len(validation) > 1 else ""
                        if not valid:
                            error_details.append(_bounded_detail(f"validator:{validation_error}"))
                            validator_detail_recorded = True
                            raise RuntimeError(f"malformed provider output:{validation_error}")
                    response_usage = dict(response.usage or {})
                    reported_cost = _reported_cost(response_usage, model=fresh)
                    paid_cost_unknown = fresh.pricing_state == PricingState.PAID and reported_cost is None
                    actual_usd = float(reported_cost or 0.0)
                    free_violation = fresh.verified_zero_price and actual_usd > 0
                    paid_cap_breached = False
                    if fresh.pricing_state == PricingState.PAID and not free_violation:
                        settlement = settle_spend(
                            model_call_id,
                            actual_usd=actual_usd,
                            billing_ambiguous=paid_cost_unknown,
                        )
                        paid_cap_breached = str(getattr(settlement, "status", "")) == "cap_breached"
                    route_error = None
                    if free_violation:
                        route_error = ProviderError(
                            ProviderErrorKind.UNKNOWN,
                            "provider reported a non-zero charge for a verified free route",
                        )
                    elif paid_cost_unknown:
                        route_error = ProviderError(
                            ProviderErrorKind.UNKNOWN,
                            "paid provider did not report enough usage to determine actual cost",
                            billing_ambiguous=True,
                        )
                    elif paid_cap_breached:
                        route_error = ProviderError(
                            ProviderErrorKind.UNKNOWN,
                            "paid provider charge exceeded the authorized reservation",
                        )
                    terminal = self._terminal_receipt(
                        preflight=preflight,
                        request=request,
                        model=fresh,
                        requirements=requirements,
                        mode=mode,
                        estimated_max=estimated_max,
                        actual_usd=actual_usd,
                        usage=response_usage,
                        success=not (free_violation or paid_cost_unknown or paid_cap_breached),
                        retry_index=retry_index,
                        fallback_chain=fallback_chain,
                        error=route_error,
                        fallback_from=predecessor_key,
                    )
                    try:
                        record_cloud_route_receipt(terminal)
                    except Exception:
                        return CloudBrokerResult(
                            False,
                            None,
                            provider_id=fresh.provider_id,
                            model_id=fresh.model_id,
                            model_call_id=model_call_id,
                            attempts=attempt_count,
                            actual_usd=actual_usd,
                            fallback_reason="signed_receipt_unavailable",
                            errors=tuple(errors),
                            pricing_state=fresh.pricing_state.value,
                        )
                    if free_violation:
                        return CloudBrokerResult(
                            False,
                            None,
                            provider_id=fresh.provider_id,
                            model_id=fresh.model_id,
                            model_call_id=model_call_id,
                            attempts=attempt_count,
                            actual_usd=actual_usd,
                            fallback_reason="free_route_reported_charge",
                            errors=tuple((*errors, "unexpected_charge")),
                            pricing_state=fresh.pricing_state.value,
                        )
                    if paid_cost_unknown or paid_cap_breached:
                        reason = "paid_cost_billing_ambiguous" if paid_cost_unknown else "paid_cap_breached"
                        return CloudBrokerResult(
                            False,
                            None,
                            provider_id=fresh.provider_id,
                            model_id=fresh.model_id,
                            model_call_id=model_call_id,
                            attempts=attempt_count,
                            actual_usd=actual_usd,
                            fallback_reason=reason,
                            errors=tuple((*errors, reason)),
                            pricing_state=fresh.pricing_state.value,
                        )
                    if callable(event_sink):
                        event_sink(
                            "model.call_completed",
                            {
                                "task_id": request.task_id,
                                "turn_id": request.turn_id,
                                "subtask_id": request.subtask_id,
                                "model_call_id": model_call_id,
                                "response_id": str(response.usage.get("response_id") or f"response-{uuid.uuid4().hex}"),
                                "provider_id": fresh.provider_id,
                                "model_id": fresh.model_id,
                                "pricing_state": fresh.pricing_state.value,
                                "cost_class": f"cloud_{fresh.pricing_state.value}",
                                "actual_cost": actual_usd,
                                "route_reason": recheck.route_reason,
                                "finish_reason": str(getattr(response, "finish_reason", "") or ""),
                                "max_output_tokens": int(call_request.max_output_tokens or 0),
                                "completion_tokens": _int_or_none(response_usage.get("completion_tokens")),
                                "reasoning_tokens": _int_or_none(
                                    dict(response_usage.get("completion_tokens_details") or {}).get(
                                        "reasoning_tokens"
                                    )
                                ),
                            },
                        )
                    return CloudBrokerResult(
                        True,
                        response,
                        fresh.provider_id,
                        fresh.model_id,
                        model_call_id,
                        attempt_count,
                        actual_usd,
                        errors=tuple(errors),
                        pricing_state=fresh.pricing_state.value,
                    )
                except Exception as exc:
                    error = provider.classify_provider_error(exc)
                    errors.append(error.kind.value)
                    if error.kind not in _REJECTED_BEFORE_INFERENCE:
                        disclosed_to_a_model = True
                    if not validator_detail_recorded:
                        error_details.append(
                            _bounded_detail(f"{error.kind.value}:{_exception_detail(exc, error)}")
                        )
                    if fresh.pricing_state == PricingState.PAID and error.billing_ambiguous:
                        try:
                            settle_spend(model_call_id, actual_usd=0.0, billing_ambiguous=True)
                        except Exception:
                            errors.append("spend_reconciliation_failed")
                    terminal = self._terminal_receipt(
                        preflight=preflight,
                        request=request,
                        model=fresh,
                        requirements=requirements,
                        mode=mode,
                        estimated_max=estimated_max,
                        actual_usd=actual_usd,
                        usage=response_usage,
                        success=False,
                        retry_index=retry_index,
                        fallback_chain=fallback_chain,
                        error=error,
                        fallback_from=predecessor_key,
                    )
                    try:
                        record_cloud_route_receipt(terminal)
                    except Exception:
                        return CloudBrokerResult(
                            False,
                            None,
                            provider_id=fresh.provider_id,
                            model_id=fresh.model_id,
                            model_call_id=model_call_id,
                            attempts=attempt_count,
                            actual_usd=float(actual_usd or 0.0),
                            fallback_reason="signed_receipt_unavailable",
                            errors=tuple(errors),
                            pricing_state=fresh.pricing_state.value,
                        )
                    if callable(event_sink):
                        event_sink(
                            "model.call_failed",
                            {
                                "task_id": request.task_id,
                                "turn_id": request.turn_id,
                                "subtask_id": request.subtask_id,
                                "model_call_id": model_call_id,
                                "provider_id": fresh.provider_id,
                                "model_id": fresh.model_id,
                                "pricing_state": fresh.pricing_state.value,
                                "cost_class": f"cloud_{fresh.pricing_state.value}",
                                "reason": error.kind.value,
                                "route_reason": recheck.route_reason,
                                "detail": error_details[-1] if error_details else "",
                                "max_output_tokens": int(call_request.max_output_tokens or 0),
                            },
                        )
                    can_retry = (
                        fresh.verified_zero_price
                        and error.kind in _RETRYABLE
                        # A malformed model turn is safe to retry against the same provider.
                        # Provider fallback remains subject to the user's fanout permission.
                        and (allow_fallback or error.kind == ProviderErrorKind.MALFORMED_RESPONSE)
                        and retry_index < 1
                        and attempt_count < attempt_limit
                    )
                    if not can_retry:
                        break
                    retry_index += 1
                    self._sleep(min(2.0, max(0.0, float(error.retry_after_seconds or 0.0))))
        return CloudBrokerResult(
            False,
            None,
            attempts=attempt_count,
            fallback_reason="cloud_attempts_exhausted",
            errors=tuple(errors),
            error_details=tuple(error_details),
        )

    @staticmethod
    def _paid_authorization_matches(authorization: Any, *, request: CloudModelRequest, model: CloudModelMetadata) -> bool:
        escalation = getattr(authorization, "escalation", None)
        reservation = getattr(authorization, "reservation", None)
        capsule = getattr(escalation, "capsule", None)
        return bool(
            authorization
            and escalation
            and reservation
            and capsule
            and str(getattr(escalation, "model_id", "")) == model.model_id
            and str(getattr(capsule, "task_id", "")) == request.task_id
            and str(getattr(reservation, "status", "")) == "reserved"
            and str(getattr(reservation, "model_call_id", "")) == str(getattr(authorization, "model_call_id", ""))
        )

    @staticmethod
    def _terminal_receipt(
        *,
        preflight: Any,
        request: CloudModelRequest,
        model: CloudModelMetadata,
        requirements: CloudTaskRequirements,
        mode: CloudRouteMode,
        estimated_max: float,
        actual_usd: float | None,
        usage: dict[str, Any],
        success: bool,
        retry_index: int,
        fallback_chain: tuple[str, ...],
        error: ProviderError | None,
        fallback_from: str = "",
    ):
        return issue_cloud_route_receipt(
            attempt_id=preflight.attempt_id,
            phase="completed",
            session_id=preflight.session_id,
            task_id=request.task_id,
            turn_id=request.turn_id,
            subtask_id=request.subtask_id,
            model_call_id=preflight.model_call_id,
            provider_id=model.provider_id,
            model_id=model.model_id,
            pricing_state=model.pricing_state.value,
            estimated_max_usd=estimated_max,
            actual_usd=actual_usd,
            usage=usage,
            privacy_class=requirements.privacy_class.value,
            data_categories=requirements.data_categories,
            policy_decision=mode.value,
            route_reason=preflight.route_reason,
            success=success,
            retry_index=retry_index,
            fallback_chain=fallback_chain,
            error_kind=error.kind.value if error else "",
            safe_error=error.safe_message if error else "",
            prev_hash=preflight.content_hash,
            requested_model=str(
                request.metadata.get("requested_model") or request.model_id or ""
            ),
            selection_mode=str(request.metadata.get("selection_mode") or ""),
            lane="cloud",
            # Execution truth, not the plan: the ACTUAL immediately preceding executed hop.
            fallback_from=fallback_from or "",
        )


def _reported_cost(usage: dict[str, Any], *, model: CloudModelMetadata) -> float | None:
    value = dict(usage or {}).get("cost")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0.0, float(value))
    prompt_tokens = dict(usage or {}).get("prompt_tokens", dict(usage or {}).get("input_tokens"))
    output_tokens = dict(usage or {}).get("completion_tokens", dict(usage or {}).get("output_tokens"))
    prices = (model.input_usd_per_token, model.output_usd_per_token, model.request_usd)
    if (
        isinstance(prompt_tokens, (int, float))
        and not isinstance(prompt_tokens, bool)
        and isinstance(output_tokens, (int, float))
        and not isinstance(output_tokens, bool)
        and all(price is not None for price in prices)
    ):
        return round(
            max(0.0, float(prompt_tokens)) * float(model.input_usd_per_token or 0.0)
            + max(0.0, float(output_tokens)) * float(model.output_usd_per_token or 0.0)
            + float(model.request_usd or 0.0),
            8,
        )
    return 0.0 if model.verified_zero_price else None


def _minimum_known(first: Any, second: Any) -> Any:
    if first is None:
        return second
    if second is None:
        return first
    return min(first, second)


__all__ = ["CloudBrokerResult", "CloudModelBroker"]
