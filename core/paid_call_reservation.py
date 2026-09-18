"""
core/paid_call_reservation.py
=============================
The server-side paid-call reservation: the ONE place a paid cloud lane becomes executable.

Background
----------
:mod:`core.memory_first_router` refuses every paid-cloud manifest unless ``source_context``
carries an ``authorized_paid_call`` — a :class:`core.model_orchestration.AuthorizedPaidCall`
holding a real spend-ledger reservation. That key is in
:data:`core.request_trust.RESERVED_TRUST_KEYS`, so it is stripped from every inbound HTTP body
and can never arrive over the wire. Nothing in production built one, so the paid lane was
unreachable: a user could add a key, pick ``claude-*``, and the turn silently fell back to local.

This module builds that reservation, server-side, from server-held state ONLY:

* the manifest the explicit pick resolved to **in this process's own registry** (the caller
  supplies a model *name*, which is not a trust claim — it is looked up, and a name that
  resolves to nothing, or to a non-paid lane, produces no reservation),
* the server-stamped ``_owner_local`` flag (:func:`core.request_trust.request_is_owner_local`),
  derived from the real TCP peer, never from a body field,
* the persisted cloud-escalation daily cap and the spend ledger.

What it deliberately does NOT authorize
---------------------------------------
* **Auto fallback.** ``manifest`` is passed only for an EXPLICIT paid-cloud pick. A turn that
  merely became eligible for a paid fallback gets ``None`` here, so it can never reach a paid
  provider regardless of the escalation policy mode.
* **Non-owner surfaces.** A channel message or a request from a non-loopback peer is refused.
* **Verified-free models.** A catalog-priced-at-zero OpenRouter model needs no reservation and
  must not consume the paid cap, so it is refused a reservation and routes on the free lane.
* **Internal utility calls.** The pick authorizes the answer lane, not every tool-intent
  classification the turn makes internally.

Monetary limits bind at reservation. Historical daily call counts remain usage statistics;
legacy ``daily_cap`` values do not restrict either manual or automatic calls.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import replace
from pathlib import Path
from threading import Lock
from typing import Any

logger = logging.getLogger("vool.paid_call_reservation")

# Per-call / per-task / per-day / per-month USD ceilings for an owner-picked paid call, enforced
# by the spend ledger (core.model_spend_ledger.reserve_spend) on top of the escalation call-count
# cap. Overridable through policy, but never unbounded: a non-positive value in the store falls
# back to the default rather than disabling the ceiling (reserve_spend treats cap<=0 as "refuse").
_DEFAULT_PER_CALL_USD = 0.25
_DEFAULT_PER_TASK_USD = 1.00
_DEFAULT_DAILY_USD = 5.00
_DEFAULT_MONTHLY_USD = 25.00

_POLICY_PREFIX = "model_orchestration.paid_call"
_TERMINAL_PAID_GENERATION_ROLES = frozenset(
    {"answer_generation", "conductor_generation"}
)


def _positive_policy_float(key: str, default: float) -> float:
    from core import policy_engine

    try:
        value = float(policy_engine.get(f"{_POLICY_PREFIX}_{key}", default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _daily_call_cap() -> int | None:
    """No call-count quota. An unreadable policy still fails closed until repaired.

    Legacy daily_cap values are retained for compatibility but no longer limit paid calls.
    Per-call, task, daily and monthly monetary reservations remain enforced in the ledger.
    """
    from core import cloud_escalation_policy

    if cloud_escalation_policy.counter_store_is_corrupt():
        logger.warning("cloud policy unreadable; refusing paid picks until it is fixed")
        return 0
    return None


def spend_limits() -> Any:
    """Monetary ceilings enforced together in the spend reservation transaction."""
    from core.model_spend_ledger import SpendLimits

    return SpendLimits(
        per_call_usd=_positive_policy_float("per_call_max_usd", _DEFAULT_PER_CALL_USD),
        per_task_usd=_positive_policy_float("per_task_max_usd", _DEFAULT_PER_TASK_USD),
        daily_usd=_positive_policy_float("daily_max_usd", _DEFAULT_DAILY_USD),
        monthly_usd=_positive_policy_float("monthly_max_usd", _DEFAULT_MONTHLY_USD),
        daily_call_cap=_daily_call_cap(),
    )


def daily_call_cap_available() -> bool:
    """Legacy entry point: only unreadable policy blocks; call counts never do."""
    return _daily_call_cap() is None


def _workspace_roots(source_context: dict[str, Any] | None) -> tuple[str, ...]:
    context = source_context or {}
    for key in ("workspace_root", "workspace"):
        raw = str(context.get(key) or "").strip()
        if raw:
            try:
                if Path(raw).expanduser().is_dir():
                    return (raw,)
            except OSError:
                continue
    return (str(Path.cwd()),)


def _int_attr(holder: Any, name: str) -> int:
    """A positive int off the task or its wrapped request, else 0."""
    for source in (holder, getattr(holder, "request", None)):
        value = getattr(source, name, None) if source is not None else None
        if isinstance(value, int) and value > 0:
            return value
    return 0


def _reservation_ceiling_usd(
    *, provider_id: str, model_id: str, prompt_tokens: int, completion_tokens: int, limits: Any
) -> float:
    """What this specific call could cost, for the reservation to hold against the caps.

    A flat per-call ceiling makes the daily cap bound the number of calls that can be reserved at
    once rather than the money they spend: measured at the shipped defaults, a $0.25 ceiling let
    twenty reservations stand under a $5.00 day while each settled near $1.08, for $21.60 of real
    spend. Reserving what the call is actually likely to cost is what makes the dollar cap bound
    dollars.

    The prompt is known before the call; the completion is not, so it is reserved at the model's
    own output limit. That over-reserves a short answer, which is the safe direction -- the excess
    is released at settlement, and a reservation that is too small is the failure being fixed.
    """
    from core.model_pricing import estimate_call_usd, reservation_price_refusal

    refusal = reservation_price_refusal(provider_id, model_id)
    if refusal:
        # A dynamic-marketplace model with no owner-approved price bound has no defensible number to
        # reserve against (the table ceiling can sit below a real listing). Refused, typed, before
        # anything is reserved or sent.
        raise PermissionError(refusal)
    per_call_ceiling = float(getattr(limits, "per_call_usd", 0.25) or 0.25)
    prompt_tokens = max(0, int(prompt_tokens or 0))
    completion_tokens = max(0, int(completion_tokens or 0))
    if not prompt_tokens and not completion_tokens:
        # Nothing to size against: keep the configured per-call ceiling rather than guess low.
        return per_call_ceiling
    try:
        estimate = estimate_call_usd(
            provider_id=provider_id,
            model_id=model_id,
            prompt_tokens=prompt_tokens or 0,
            completion_tokens=completion_tokens or 0,
        )
    except Exception:
        return per_call_ceiling
    projected = float(getattr(estimate, "usd", 0.0) or 0.0)
    # Never reserve less than the configured ceiling: it is the floor of what a call may cost here,
    # and dropping below it would reintroduce the amplification this exists to remove.
    return max(per_call_ceiling, projected)


def reserve_owner_pick_paid_call(
    *,
    manifest: Any,
    task: Any,
    source_context: dict[str, Any] | None,
    task_kind: str = "",
    call_role: str = "",
    denial: dict[str, str] | None = None,
) -> Any | None:
    """Build a paid-call reservation for an EXPLICIT, OWNER-LOCAL pick of a paid cloud model.

    ``manifest`` must be the manifest the explicit pick resolved to against the server's own
    registry, or ``None`` when the turn was not explicitly pointed at a paid model. Returns an
    :class:`~core.model_orchestration.AuthorizedPaidCall` when — and only when — every gate below
    passes, else ``None`` (the caller then keeps the paid lane unreachable).

    ``denial`` is an optional out-dict: when a reservation is refused, ``denial["reason"]`` is set
    to the stable, non-secret code of the gate that actually rejected it (the spend ledger's own
    ``*_cap_exceeded`` code, ``not_owner_local``, ``cloud_policy_unreadable`` …), so a caller can
    name the real cause in the user-facing refusal instead of guessing it. Existing callers that do
    not pass ``denial`` are unaffected. The reason is NEVER derived by the caller re-running a
    policy check — it comes from the authority that refused, here.

    Nothing in ``source_context`` is trusted as an authorization: the only field read from it is
    the server-stamped owner-local flag plus inert identifiers (session/turn/subtask). A
    caller-supplied ``authorized_paid_call`` is never read, here or by the caller.
    """
    from core.model_selection_policy import is_verified_free_cloud_manifest, provider_cost_class
    from core.request_trust import request_is_owner_local

    def _refuse(reason: str) -> None:
        if denial is not None:
            denial["reason"] = reason
        return None

    if manifest is None:
        return _refuse("no_explicit_paid_pick")
    # Only the owner's own local session may spend the owner's key.
    if not request_is_owner_local(source_context):
        return _refuse("not_owner_local")
    # A free lane needs no reservation and must not consume the paid cap.
    if provider_cost_class(manifest) != "paid_cloud" or is_verified_free_cloud_manifest(manifest):
        return _refuse("free_pin_needs_no_reservation")
    # The pick authorizes the answering lane, not the turn's internal classification calls.
    if str(task_kind or "").strip().lower() == "tool_intent":
        return _refuse("internal_tool_intent_call")

    # CONVERGENCE BOUNDARY (the money-law migration route task 03 documented): a UsePod lane's
    # paid calls reserve, claim, dispatch and settle on the atomic money law
    # (core.effect_budget_money) inside the adapter, BEFORE any byte leaves. This legacy USD
    # reservation must NOT also be created for it: a second reservation here is a second ledger,
    # and its ``call_failed`` release returns a possibly-billed ceiling to the cap -- exactly the
    # exposure the money law's held/unknown states exist to prevent. The marker returned below
    # satisfies the router's paid-lane gates; every legacy release/settle path no-ops on it
    # (empty model_call_id), and the lane's real cap is the operator's money grant. Unrelated
    # paid providers keep this legacy path and its limits unchanged.
    if str(getattr(manifest, "provider_id", "") or "").startswith("usepod"):
        return _authorize_money_law_pick(manifest, task=task, denial=denial)

    context = source_context or {}
    task_id = str(getattr(task, "task_id", "") or context.get("task_id") or "").strip()
    model_id = str(getattr(manifest, "model_name", "") or "").strip()
    if not task_id or not model_id:
        return _refuse("missing_turn_or_model_identity")

    if not daily_call_cap_available():
        logger.info("paid model pick refused: cloud policy unreadable")
        return _refuse("cloud_policy_unreadable")

    # Carry the call's own shape so the reservation can be sized to what THIS call may cost.
    # Sized here rather than inside _authorize because this is the only frame that holds both the
    # manifest and the task. The completion side is the LANE-RESOLVED ceiling — the same
    # `core.output_budget_policy.lane_resolved_output_tokens` the adapter resolves the wire
    # `max_tokens` from for this manifest — so the funds held before dispatch cover the ceiling
    # actually sent, never a number the answering lane has already widened past. (Measured
    # 2026-09-16: a catalog-declared reasoning model on the byok lane sent the prompt table's
    # un-reserved 240–520-token ceiling, spent it all reasoning, and returned empty content; the
    # repair widens that ceiling, and the reservation widens with it by construction, from the
    # same function, rather than trusting the two numbers to coincide.) The reservation itself
    # still refuses closed when the resolved projection exceeds the ledger caps.
    from core.output_budget_policy import lane_resolved_output_tokens

    asked_completion_tokens = _int_attr(task, "max_output_tokens")
    sized_context = dict(context)
    sized_context["paid_call_provider_id"] = str(getattr(manifest, "provider_id", "") or "")
    sized_context["paid_call_prompt_tokens"] = _int_attr(task, "prompt_tokens")
    sized_context["paid_call_completion_tokens"] = lane_resolved_output_tokens(
        manifest, base_tokens=asked_completion_tokens
    )

    try:
        authorization = _authorize(
            context=sized_context,
            task_id=task_id,
            model_id=model_id,
            local_model_id=str(context.get("local_model_id") or "local"),
        )
    except PermissionError as exc:
        # A ledger cap (per-call / per-task / daily / monthly USD) refused the reservation. The
        # ledger raises the exact cap code (e.g. ``per_task_spend_cap_exceeded``) as the message;
        # carry that verbatim so the caller names the real gate rather than a generic "denied".
        logger.info("paid model pick refused by spend cap: %s", exc)
        return _refuse(str(exc).strip() or "spend_cap_exceeded")
    except Exception as exc:  # pragma: no cover - defensive: never fail OPEN into a paid call
        logger.warning("paid model pick could not be reserved (%s); staying local", exc)
        return _refuse("reservation_unavailable")
    if authorization is not None:
        authorization = replace(
            authorization,
            provider_id=str(getattr(manifest, "provider_id", "") or ""),
        )
        _emit_answer_spend_receipt(
            "paid_call.reserved",
            authorization,
            source_context=source_context,
            call_role=call_role,
            provider_id=str(getattr(manifest, "provider_id", "") or ""),
        )
    return authorization


def _authorize(*, context: dict[str, Any], task_id: str, model_id: str, local_model_id: str) -> Any | None:
    from core import cloud_escalation_policy
    from core.model_escalation_policy import EscalationReason, PaidModelMode, PaidModelPolicy
    from core.model_handoff_capsule import build_model_handoff_capsule
    from core.model_orchestration import ModelOrchestrator

    turn_id = str(context.get("turn_id") or "").strip() or f"turn-{uuid.uuid4().hex}"
    subtask_id = str(context.get("subtask_id") or "").strip() or "owner_explicit_model_pick"
    session_id = str(context.get("runtime_session_id") or context.get("session_id") or "local").strip() or "local"

    limits = spend_limits()
    hard_cap_usd = _reservation_ceiling_usd(
        provider_id=str(context.get("paid_call_provider_id") or ""),
        model_id=model_id,
        prompt_tokens=int(context.get("paid_call_prompt_tokens") or 0),
        completion_tokens=int(context.get("paid_call_completion_tokens") or 0),
        limits=limits,
    )

    orchestrator = ModelOrchestrator()
    run_id = orchestrator.start_local(
        task_id=task_id,
        turn_id=turn_id,
        subtask_id=subtask_id,
        session_id=session_id,
        lane="local",
        model_id=local_model_id,
        reason="owner_explicit_paid_model_pick",
    )
    # An empty capsule: the reservation authorizes the lane, and the router sends the turn's own
    # request through the adapter. Nothing extra leaves the device because of this call.
    capsule = build_model_handoff_capsule(
        task_id=task_id,
        turn_id=turn_id,
        subtask_id=subtask_id,
        user_goal="Owner explicitly selected this cloud model for this turn.",
        blocked_subtask="",
        rules=(),
        plan_state=(),
        expected_output_schema={},
        forbidden_operations=(),
        verification_criteria=(),
        items=(),
        approved_workspace_roots=_workspace_roots(context),
    )
    escalation = orchestrator.evaluate_paid(
        run_id,
        capsule=capsule,
        local_model_id=local_model_id,
        model_id=model_id,
        reason=EscalationReason.USER_REQUESTED_PREMIUM,
        policy=PaidModelPolicy(
            # PINNED_MODEL + an explicit user approval source: this authorizes exactly the model
            # the owner picked and nothing else. The allowlist pins it a second time so a
            # mismatch between the pinned name and the manifest cannot slip through.
            mode=PaidModelMode.PINNED_MODEL,
            per_call_max_usd=hard_cap_usd,
            per_task_max_usd=float(limits.per_task_usd),
            daily_max_usd=float(limits.daily_usd),
            monthly_max_usd=float(limits.monthly_usd),
            model_allowlist=(model_id,),
            pinned_model=model_id,
        ),
        openrouter_configured=True,
        network_allowed=True,
        data_categories=(),
        estimated_low_usd=0.0,
        estimated_high_usd=hard_cap_usd,
        hard_cap_usd=hard_cap_usd,
        approval_source="user",
    )
    if not escalation.decision.allowed:
        return None
    authorization = orchestrator.authorize_paid(escalation, limits=limits, user_approved=True)
    if authorization is None:
        return None
    # Preserve historical usage statistics. Call counts do not grant or deny spend;
    # the ledger's monetary limits are the enforcement point.
    cloud_escalation_policy.record_escalation()
    return authorization


# --- retry accounting ------------------------------------------------------------------------
#
# A provider bills every attempt it serves, not just the one that came back usable. The router
# can make more than one call under a single reservation (a failed attempt followed by a retry on
# the same model), and settling only the last one charges the user for one call while the provider
# charges for two.
#
# Two kinds of prior attempt are tracked per reservation:
#   * an attempt that returned a usage block -> its real cost, accumulated in ``billed_usd``
#   * an attempt that failed after the request was sent, returning nothing to price -> counted in
#     ``unpriced_attempts`` and charged, when the reservation finally settles, at the same cost as
#     the attempt that did succeed. That is an assumption, and it is deliberately the expensive
#     one: a failed attempt is more often not billed at all, so this can overstate. Overstating
#     makes the cap trip early, which is the direction a spend cap must err in.
#
# Process-local and best effort: a crash mid-turn loses the tally, which can only ever undercount
# a turn that never finished. It is not a substitute for the ledger, which is durable.
_ATTEMPTS_LOCK = Lock()
_ATTEMPTS: dict[str, dict[str, float]] = {}


def _attempt_state(model_call_id: str) -> dict[str, float]:
    return _ATTEMPTS.setdefault(model_call_id, {"billed_usd": 0.0, "unpriced_attempts": 0.0})


def note_unbilled_paid_attempt(authorization: Any) -> None:
    """Record that an attempt under this reservation reached the provider and returned nothing
    priceable. It is charged at the settling attempt's rate when the reservation closes."""
    model_call_id = str(getattr(authorization, "model_call_id", "") or "")
    if not model_call_id:
        return
    with _ATTEMPTS_LOCK:
        _attempt_state(model_call_id)["unpriced_attempts"] += 1.0


def attempt_charge_total(model_call_id: str, *, this_attempt_usd: float) -> float:
    """Everything billed under this reservation once ``this_attempt_usd`` is added to it."""
    with _ATTEMPTS_LOCK:
        state = _attempt_state(str(model_call_id or ""))
        this_attempt = max(0.0, float(this_attempt_usd or 0.0))
        # Each unpriced prior attempt is charged at this attempt's cost -- the attempts multiplier
        # in core.model_pricing, applied to the attempts nobody could price directly.
        return round(state["billed_usd"] + this_attempt * (1.0 + state["unpriced_attempts"]), 8)


def forget_paid_attempts(model_call_id: str) -> None:
    """Drop the per-reservation attempt tally (the reservation is closed)."""
    with _ATTEMPTS_LOCK:
        _ATTEMPTS.pop(str(model_call_id or ""), None)


def settle_owner_pick_paid_call(
    authorization: Any,
    *,
    actual_usd: float,
    source_context: dict[str, Any] | None = None,
    call_role: str = "",
) -> None:
    """Close out a granted reservation with what the call actually cost, including every earlier
    attempt made under it. Fail-soft.

    ``actual_usd`` is this attempt's cost, priced by :mod:`core.model_pricing` from token counts
    and the model's published rate (or the provider's own reported cost where it supplies one) --
    never from a field most providers omit.
    """
    model_call_id = str(getattr(authorization, "model_call_id", "") or "")
    if not model_call_id:
        return
    total = attempt_charge_total(model_call_id, this_attempt_usd=actual_usd)
    try:
        from core.model_spend_ledger import settle_spend

        settled = settle_spend(model_call_id, actual_usd=total)
        forget_paid_attempts(model_call_id)
        _emit_answer_spend_receipt(
            "paid_call.settled",
            authorization,
            source_context=source_context,
            call_role=call_role,
            terminal=settled,
        )
        _terminalize_paid_generation_orchestration(
            authorization,
            succeeded=True,
            actual_usd=float(getattr(settled, "actual_usd", 0.0) or 0.0),
            call_role=call_role,
        )
    except Exception as exc:
        # The row was already finalized (an earlier attempt released it, and this is the retry).
        # The charge is real and already incurred, so it is posted as its own settled row rather
        # than dropped: a spend the ledger cannot see is a spend the caps cannot bind.
        if _post_supplemental_charge(authorization, usd=total, model_call_id=model_call_id):
            forget_paid_attempts(model_call_id)
            return
        logger.warning("paid call %s not settled (%s)", model_call_id, exc)


def _post_supplemental_charge(authorization: Any, *, usd: float, model_call_id: str) -> bool:
    """Record an already-incurred charge against a reservation that is no longer open.

    The caps exist to refuse spend that has not happened yet. This money is a fait accompli, so
    the row is admitted regardless of the caps and the ceilings are widened to exactly the amount
    being recorded -- refusing to write it would leave the real charge invisible, which is the
    defect this whole path exists to close. Recording it is what makes the NEXT reservation see a
    larger day/month total and refuse.
    """
    amount = round(max(0.0, float(usd or 0.0)), 8)
    if amount <= 0:
        return True  # nothing was spent; there is nothing to record
    try:
        from core.model_spend_ledger import SUPPLEMENTAL_SUBTASK_ID, SpendLimits, reserve_spend, settle_spend

        escalation = getattr(authorization, "escalation", None)
        capsule = getattr(escalation, "capsule", None)
        task_id = str(getattr(capsule, "task_id", "") or "") or "unknown_task"
        supplemental_id = f"{model_call_id}#retry-{uuid.uuid4().hex[:8]}"
        reserve_spend(
            model_call_id=supplemental_id,
            task_id=task_id,
            # Marked so the daily CALL cap does not count this row: it records money already
            # spent by a retry, not a new call being admitted. Leaving daily_call_cap unset
            # below also keeps this write itself ungated, which is required — the charge is a
            # fait accompli and refusing to record it would hide real spend from the caps.
            subtask_id=SUPPLEMENTAL_SUBTASK_ID,
            model_id=str(getattr(escalation, "model_id", "") or ""),
            maximum_usd=amount,
            limits=SpendLimits(
                per_call_usd=amount, per_task_usd=amount * 1e6, daily_usd=amount * 1e6, monthly_usd=amount * 1e6
            ),
        )
        settle_spend(supplemental_id, actual_usd=amount)
        logger.info("paid retry charge of $%.6f recorded for %s", amount, model_call_id)
        return True
    except Exception as exc:  # pragma: no cover - metering must never break a turn
        logger.warning("paid retry charge for %s not recorded (%s)", model_call_id, exc)
        return False


def _authorize_money_law_pick(manifest: Any, *, task: Any, denial: dict[str, str] | None) -> Any:
    """Authorize a UsePod paid pick against the money law, without the legacy USD ledger.

    The gate here is read-only (an active grant whose allowlist covers the pinned model); the
    authoritative reservation -- grant consumption, liquidity, caps -- happens atomically inside
    ``core.effect_budget_money.reserve_liability`` at dispatch, before any byte leaves. A refusal
    here names the money law's code so the router surfaces the real reason.
    """
    from core.usepod import money_law, monetary

    model_id = str(getattr(manifest, "model_name", "") or "").strip()

    # The route check that used to live in this function's legacy reservation ceiling still
    # belongs HERE: a pinned model with no approved route bound refuses at the pick, before the
    # lane is attempted at all (the adapter's own authorize_dispatch is the second line).
    try:
        from core.usepod import routing

        _route_state = routing.load_route_state()
        _bound = _route_state.bounds.get(model_id) if hasattr(_route_state, "bounds") else None
        if _bound is None:
            reason = "usepod_route_not_approved"
            if denial is not None:
                denial["reason"] = reason
            logger.info("usepod paid pick refused: no approved route bound for %s", model_id)
            return None
    except Exception:
        pass

    # An EXPLICITLY installed authority (a labelled test double) is the lane's gate: the adapter
    # consults it before any byte leaves, and this router-level check steps aside. Only the
    # production money law is grant-gated here, so a paid pin without a grant refuses visibly at
    # the pick instead of dying at dispatch.
    installed_label = str(getattr(monetary.monetary_authority(), "label", "") or "")

    # The lane's OWN transport decides which dependency a pick names. Accountless x402 needs the
    # wallet payment authority AND a network it can pay on now; either missing is refused here,
    # before any quote, reservation or payment, with the code the Settings panel names.
    x402_lane = False
    x402_networks: tuple[str, ...] = ()
    try:
        from core.usepod.lane import load_lane_preference
        from core.usepod.transport import payment_authority

        _preference, _lane_error = load_lane_preference()
        x402_lane = str(_preference.transport_mode) == "x402"
        if x402_lane:
            _payment = payment_authority()
            if str(getattr(_payment, "label", "")).startswith("unavailable:"):
                reason = "wallet_payment_authority_unavailable"
                if denial is not None:
                    denial["reason"] = reason
                logger.info("usepod x402 pick refused: no wallet payment authority is integrated")
                return None
            x402_networks = tuple(str(item) for item in (getattr(_payment, "networks", ()) or ()))
            if not x402_networks:
                # Crypto off, another environment, no ready pilot wallet on a documented row, or
                # the row's endpoint unproven: the wallet can pay nowhere, so nothing is attempted.
                reason = "wallet_payment_network_unverified"
                if denial is not None:
                    denial["reason"] = reason
                logger.info("usepod x402 pick refused: the wallet payment authority pays on no network now")
                return None
    except Exception:
        pass

    if installed_label != money_law.AUTHORITY_LABEL:
        class _PassthroughPick:
            """The marker for a lane whose installed monetary authority gates the call itself."""

            __slots__ = ("escalation", "reservation", "model_call_id", "provider_id")

            def __init__(self) -> None:
                self.escalation = type("_E", (), {"model_id": model_id, "capsule": type("_C", (), {"task_id": str(getattr(task, "task_id", "") or "")})()})()
                self.reservation = type("_R", (), {"status": "reserved", "model_call_id": ""})()
                self.model_call_id = ""
                self.provider_id = str(getattr(manifest, "provider_id", "") or "")

        return _PassthroughPick()

    # The consents that may cover this pick are the lane's own kind: prepaid spends the account
    # balance, x402 pays from the wallet.
    operation_kind = money_law.OPERATION_X402 if x402_lane else money_law.OPERATION_PREPAID
    grant_rows = money_law.active_grants(money_law.OPERATION_X402) if x402_lane else money_law.active_prepaid_grants()
    grants = [
        row
        for row in grant_rows
        if not (row["spec"].get("models") or ()) or model_id in tuple(row["spec"].get("models") or ())
    ]
    if not grants:
        # Name WHY: a revoked or expired grant says so; only a grant that never existed reads
        # as AUTHORITY_INVALID. The refusal the router surfaces is the money law's own code. An
        # x402 consent names one asset, so each asset UsePod may quote is asked and the most
        # precise answer wins.
        from core.usepod.monetary import ProviderLiability

        if x402_lane:
            network = x402_networks[0] if x402_networks else ""
            shapes = [("x402", "USDC", "usdc_microunit", "x402_payer_wallet", network), ("x402", "SOL", "lamport", "x402_payer_wallet", network)]
        else:
            shapes = [("prepaid_token", "USDC", "usdc_microunit", "usepod_prepaid_token_balance", "usepod_internal_account_ledger")]
        codes = []
        for transport_mode, asset, unit, account_kind, network in shapes:
            probe = ProviderLiability(
                operation_id=f"usepod-pin-probe-{model_id[:48]}",
                provider_id="usepod",
                transport_mode=transport_mode,
                asset=asset,
                unit=unit,
                account_kind=account_kind,
                account_ref="",
                network=network,
                max_amount_atomic=1,
                model_id=model_id,
                route_approval_id="",
                envelope_binding_sha256="",
                basis={},
            )
            codes.append(money_law.grant_refusal_code(probe, operation_kind=operation_kind))
        reason = next((code for code in ("MONEY_AUTHORITY_REVOKED", "MONEY_AUTHORITY_EXPIRED", "MONEY_AUTHORITY_EXHAUSTED") if code in codes), codes[0])
        if denial is not None:
            denial["reason"] = reason
        logger.info("usepod paid pick refused (%s): no active spend grant for %s", reason, model_id)
        return None

    class _MoneyLawEscalation:
        __slots__ = ("model_id", "capsule")

        def __init__(self) -> None:
            self.model_id = model_id
            self.capsule = type("_Capsule", (), {"task_id": str(getattr(task, "task_id", "") or "")})()

    class _MoneyLawReservation:
        # An intentionally EMPTY model_call_id: no legacy ledger row exists for this call, so
        # every legacy release/settle path (which all guard on a non-empty id) is a no-op. The
        # durable monetary truth is the money law's liability under the operation id.
        __slots__ = ("status", "model_call_id")

        def __init__(self) -> None:
            self.status = "reserved"
            self.model_call_id = ""

    class MoneyLawAuthorizedPick:
        """The router-facing authorization for a lane whose money is governed by the money law.

        Releases and settlements on this object are no-ops by construction; the lane's caps are
        the operator's money grant, and its uncertain outcomes are held by the money law.
        """

        __slots__ = ("escalation", "reservation", "model_call_id", "provider_id")

        def __init__(self) -> None:
            self.escalation = _MoneyLawEscalation()
            self.reservation = _MoneyLawReservation()
            self.model_call_id = ""
            self.provider_id = str(getattr(manifest, "provider_id", "") or "")

    return MoneyLawAuthorizedPick()


def release_owner_pick_paid_call(
    authorization: Any,
    *,
    reason: str = "call_failed",
    source_context: dict[str, Any] | None = None,
    call_role: str = "",
) -> None:
    """Return an unspent reservation to the caps after a failed paid call. Fail-soft.

    ``reason`` says whether the provider was reached. ``call_failed`` means the request went out
    and may have been served and billed, so the attempt is remembered and charged if a retry under
    the same reservation later settles. Every other reason (circuit open, unhealthy provider,
    prompt over budget) means no request was ever sent, so nothing is owed.
    """
    model_call_id = str(getattr(authorization, "model_call_id", "") or "")
    if not model_call_id:
        return
    if str(reason or "") == "call_failed":
        note_unbilled_paid_attempt(authorization)
    else:
        forget_paid_attempts(model_call_id)
    try:
        from core.model_spend_ledger import get_spend_reservation, release_spend

        before = get_spend_reservation(model_call_id)
        release_spend(model_call_id, reason=reason)
        terminal = get_spend_reservation(model_call_id)
        # Only the transaction that changed `reserved` to a terminal state owns the event. Repeated
        # cleanup calls are intentionally idempotent and cannot append contradictory terminals.
        if (
            before is not None
            and str(before.status or "") == "reserved"
            and terminal is not None
            and str(terminal.status or "") != "reserved"
        ):
            _emit_answer_spend_receipt(
                "paid_call.released",
                authorization,
                source_context=source_context,
                call_role=call_role,
                terminal=terminal,
                reason=reason,
            )
            _terminalize_paid_generation_orchestration(
                authorization,
                succeeded=False,
                actual_usd=float(getattr(terminal, "actual_usd", 0.0) or 0.0),
                reason=reason,
                call_role=call_role,
            )
    except Exception as exc:  # pragma: no cover - metering must never break a turn
        logger.warning("paid call %s not released (%s)", model_call_id, exc)


def _emit_answer_spend_receipt(
    event_type: str,
    authorization: Any,
    *,
    source_context: dict[str, Any] | None,
    call_role: str,
    terminal: Any | None = None,
    reason: str = "",
    provider_id: str = "",
) -> None:
    """Emit spend evidence only for the ordinary answer role this seam owns.

    Conductor generation already has a stricter one-call scope and emits the same schema from that
    scope. Keeping its existing owner avoids duplicate receipts while putting ordinary generation
    beside it under the shared event contract.
    """
    role = str(call_role or "").strip().lower()
    if role != "answer_generation" or not isinstance(source_context, dict):
        return
    reservation = getattr(authorization, "reservation", None)
    escalation = getattr(authorization, "escalation", None)
    model_call_id = str(getattr(authorization, "model_call_id", "") or "")
    reservation_id = str(getattr(reservation, "reservation_id", "") or "")
    if not model_call_id or not reservation_id:
        return
    try:
        limits = spend_limits()
    except Exception:
        limits = None
    try:
        from core.model_spend_ledger import calls_today

        daily_count = calls_today()
    except Exception:
        daily_count = 0
    reserved_usd = float(getattr(reservation, "reserved_usd", 0.0) or 0.0)
    actual_usd = float(getattr(terminal, "actual_usd", 0.0) or 0.0)
    details = {
        "call_role": role,
        "provider_id": str(provider_id or getattr(authorization, "provider_id", "") or ""),
        "model_call_id": model_call_id,
        "reservation_id": reservation_id,
        "model_id": str(getattr(escalation, "model_id", "") or ""),
        "reserved_usd": reserved_usd,
        "actual_usd": actual_usd,
        "per_call_cap_usd": float(getattr(limits, "per_call_usd", 0.0) or 0.0),
        "daily_call_count": int(daily_count),
        "daily_call_cap": getattr(limits, "daily_call_cap", None),
        "spend_status": str(getattr(terminal, "status", "reserved") or "reserved"),
        **({"reason": str(reason)} if reason else {}),
    }
    try:
        from core.runtime_task_events import emit_runtime_event

        emit_runtime_event(
            source_context,
            event_type=event_type,
            message=f"Paid answer generation {details['spend_status']}.",
            details=details,
        )
    except Exception:
        # Evidence failure must not change spend authority or break an otherwise valid turn.
        return


def _terminalize_paid_generation_orchestration(
    authorization: Any,
    *,
    succeeded: bool,
    actual_usd: float,
    reason: str = "",
    call_role: str = "",
) -> None:
    """Close the run paired with a terminal, explicitly authorized paid generation.

    Ordinary answer generation and the bounded conductor helper have separate receipt owners,
    but both reservations originate from this module and therefore share this lifecycle owner.
    Keeping the role allowlist closed prevents internal paid utility calls from acquiring terminal
    answer semantics merely by reaching the spend seam.
    """
    role = str(call_role or "").strip().lower()
    if role not in _TERMINAL_PAID_GENERATION_ROLES:
        return
    generation_kind = "conductor" if role == "conductor_generation" else "answer"
    escalation = getattr(authorization, "escalation", None)
    run_id = str(getattr(escalation, "run_id", "") or "")
    paid_model = str(getattr(escalation, "model_id", "") or "")
    local_model = str(getattr(escalation, "local_model_id", "") or "")
    if not run_id:
        return
    try:
        from core.model_orchestration_state import ModelOrchestrationState, ModelOrchestrationStore

        store = ModelOrchestrationStore()
        current = store.current(run_id)
        if current is None or str(current.get("state") or "") != ModelOrchestrationState.PAID_RUNNING:
            return
        if succeeded:
            store.transition(
                run_id,
                ModelOrchestrationState.PAID_VALIDATING,
                source_model=paid_model,
                target_model=paid_model,
                reason=f"paid_{generation_kind}_received",
                actual_spend_usd=actual_usd,
                verification_result="provider_response_received",
            )
            store.transition(
                run_id,
                ModelOrchestrationState.TASK_VERIFYING,
                source_model=paid_model,
                target_model=paid_model,
                reason=f"paid_{generation_kind}_output_controls_completed",
                actual_spend_usd=actual_usd,
                verification_result="accepted",
            )
            store.transition(
                run_id,
                ModelOrchestrationState.COMPLETED,
                source_model=paid_model,
                target_model=paid_model,
                reason=f"paid_{generation_kind}_served",
                actual_spend_usd=actual_usd,
                verification_result="completed",
            )
        else:
            store.transition(
                run_id,
                ModelOrchestrationState.PAID_FAILED,
                source_model=paid_model,
                target_model=local_model,
                reason=str(reason or f"paid_{generation_kind}_failed"),
                actual_spend_usd=actual_usd,
                verification_result="not_served",
            )
            store.transition(
                run_id,
                ModelOrchestrationState.FAILED_SAFE,
                source_model=paid_model,
                target_model=local_model,
                reason="exact_paid_pin_failed_without_substitution",
                actual_spend_usd=actual_usd,
                verification_result="failed_safe",
            )
    except Exception as exc:
        # State evidence must not alter spend settlement or turn delivery. It fails loudly in logs
        # and remains recoverable by the existing interrupted-paid-run recovery routine.
        logger.warning("paid generation orchestration run %s was not terminalized (%s)", run_id, exc)


__all__ = [
    "attempt_charge_total",
    "daily_call_cap_available",
    "forget_paid_attempts",
    "note_unbilled_paid_attempt",
    "release_owner_pick_paid_call",
    "reserve_owner_pick_paid_call",
    "settle_owner_pick_paid_call",
    "spend_limits",
]
