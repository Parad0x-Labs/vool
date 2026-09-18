"""Price-triggered admission using the existing route authority and durable chat queue.

No timer pays or grants authority. The open app pumps the queue; the server judges its
head before claiming. Only a matching claimed task can continue above its start target,
and every request still reserves against the real provider budget and current route.
"""

from __future__ import annotations

import contextvars
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from typing import Any

from core import runtime_active_clock
from core.runtime_task_events import emit_runtime_event
from core.usepod import discovery, pricing, routing

_RUNNING = contextvars.ContextVar("usepod_price_wait_run", default=None)
_POLL_LOCK = threading.RLock()
_POLLS: dict[str, tuple[float, dict[str, Any]]] = {}


def save_target(
    *, model_id: str, max_input_usdc: str, max_output_usdc: str, poll_minutes: int = 5,
    active_price_tolerance_percent: int | None = None,
) -> routing.ApprovedRouteBound:
    model_id = str(model_id).strip()
    if not model_id or len(model_id) > 200:
        raise ValueError("Choose a model")
    if isinstance(poll_minutes, bool) or not isinstance(poll_minutes, int) or not 1 <= poll_minutes <= 60:
        raise ValueError("Check interval must be 1–60 minutes")
    ceiling_in = pricing.usdc_decimal_to_microunits(max_input_usdc)
    ceiling_out = pricing.usdc_decimal_to_microunits(max_output_usdc)
    if min(ceiling_in, ceiling_out) <= 0:
        raise ValueError("Enter positive input and output prices in USDC per million tokens")
    if active_price_tolerance_percent is not None and (isinstance(active_price_tolerance_percent, bool) or not isinstance(active_price_tolerance_percent, int) or not 0 <= active_price_tolerance_percent <= 1000):
        raise ValueError("Price tolerance must be 0–1000 percent")
    state = routing.load_route_state()
    if state.error:
        raise ValueError(state.error)
    snapshot = pricing.current_snapshot(origin=discovery.configured_origin(), allow_network=True).snapshot
    # First derive the normal permitted route identity. The owner's lower START targets
    # need not be available yet; they replace only the two axes on this model's bound.
    bound = routing.approve_route_bound(snapshot, model_id=model_id, policy=state.policy)
    bound = replace(
        bound,
        max_input_microunits_per_million=ceiling_in,
        max_output_microunits_per_million=ceiling_out,
        input_basis="owner_start_price",
        output_basis="owner_start_price",
        wait_poll_seconds=poll_minutes * 60,
        active_price_tolerance_percent=active_price_tolerance_percent,
    )
    routing.save_approved_bound(bound)
    return bound


def queued_target(model_id: str) -> dict[str, str]:
    state = routing.load_route_state()
    bound = state.bounds.get(model_id)
    credential = discovery.resolve_credential()
    if state.error or not bound or not bound.wait_poll_seconds or credential is None:
        raise ValueError("Save a price target and connect a UsePod prepaid account first")
    return {"model_id": model_id, "approval_id": bound.approval_id, "account": credential.fingerprint}


def check(model_id: str) -> dict[str, Any]:
    state = routing.load_route_state()
    bound = state.bounds.get(model_id)
    if state.error:
        return {"ready": False, "state": state.error}
    if not bound or not bound.wait_poll_seconds:
        return {"ready": True, "state": "no_target", "enabled": False}
    key = bound.approval_id
    now = time.time()
    with _POLL_LOCK:
        previous = _POLLS.get(key)
        if previous and previous[0] > now:
            return dict(previous[1])
        result: dict[str, Any] = {
            "enabled": True,
            "ready": False,
            "state": "waiting_for_price",
            "next_check": now + bound.wait_poll_seconds,
        }
        try:
            snapshot = pricing.fetch_marketplace_snapshot(origin=discovery.configured_origin())
            routing.authorize_dispatch(bound, snapshot, model_id=model_id, policy=state.policy)
            result.update(ready=True, state="price_available")
        except routing.RouteUnavailableError as exc:
            result["reason"] = exc.code
        except Exception:
            result["reason"] = "price_check_unavailable"
        _POLLS[key] = (now + bound.wait_poll_seconds, result)
        return dict(result)


def check_queued(target: dict[str, str]) -> dict[str, Any]:
    from core.usepod.spend_approval import prepaid_spend_readiness

    try:
        if queued_target(target["model_id"]) != target:
            return {"ready": False, "state": "target_or_account_changed"}
    except (KeyError, ValueError):
        return {"ready": False, "state": "target_unavailable"}
    readiness = prepaid_spend_readiness(target["model_id"])
    if not readiness.get("available"):
        return {"ready": False, "state": "budget_" + str(readiness.get("state"))}
    return check(target["model_id"])


@contextmanager
def running_task(session_id: str, text: str, *, queue_item_id: str = "", source_context=None):
    from core.runtime_continuity import list_queued_messages

    matches = [
        row
        for row in (list_queued_messages(session_id, statuses=("in_flight",)) if queue_item_id else [])
        if queue_item_id
        and row["queue_item_id"] == queue_item_id
        and row.get("payload", {}).get("text") == text
        and row.get("payload", {}).get("price_wait")
    ]
    context = None
    if len(matches) == 1:
        context = {"target": matches[0]["payload"]["price_wait"], "started": False, "source_context": source_context or {}}
    token = _RUNNING.set(context)
    try:
        with runtime_active_clock.task_clock(enabled=context is not None):
            yield
    finally:
        _RUNNING.reset(token)


def authorize_running_dispatch(state: routing.RouteState, snapshot, *, model_id: str, request=None):
    bound = state.bounds.get(model_id)
    active = _RUNNING.get()
    if active and active["target"]["model_id"] == model_id:
        # A queue record never grants spending or permits a different account, route or model.
        try:
            matches = queued_target(model_id) == active["target"]
        except ValueError:
            matches = False
        if not matches:
            raise routing.RouteUnavailableError("queued_price_target_changed")
        if active["started"] and bound.active_price_tolerance_percent is not None:
            return _continue_with_price_limit(active, bound, state, snapshot, request=request)
        if active["started"]:
            # Owner explicitly approved START-price admission, not interruption of the task.
            # Reprice the next call under the same route policy; the monetary law still bounds
            # its full maximum against the shared budget before anything is sent.
            bound = routing.approve_route_bound(snapshot, model_id=model_id, policy=state.policy)
    approval = routing.authorize_dispatch(bound, snapshot, model_id=model_id, policy=state.policy)
    if active and active["target"]["model_id"] == model_id:
        active["started"] = True
    return approval


def _continue_with_price_limit(active, bound, state, snapshot, *, request):
    """Hold this exact invocation before reservation; never restart earlier tool steps."""
    tolerance = bound.active_price_tolerance_percent
    limited = replace(bound,
        max_input_microunits_per_million=bound.max_input_microunits_per_million * (100 + tolerance) // 100,
        max_output_microunits_per_million=bound.max_output_microunits_per_million * (100 + tolerance) // 100)
    try:
        return routing.authorize_dispatch(limited, snapshot, model_id=bound.model_id, policy=state.policy)
    except routing.RouteUnavailableError as exc:
        if exc.code != "route_price_above_approved_bound":
            raise
        evidence = exc.evidence
    if request is None or not callable(getattr(request, "cancel_check", None)):
        raise routing.RouteUnavailableError("price_pause_requires_cancellable_live_task")
    if request.is_cancelled():
        raise routing.RouteUnavailableError("price_wait_cancelled")
    cap_in = pricing.microunits_to_usdc_decimal(limited.max_input_microunits_per_million)
    cap_out = pricing.microunits_to_usdc_decimal(limited.max_output_microunits_per_million)
    prices = evidence.get("current_prices", [])
    current = "; ".join(
        f"{pricing.microunits_to_usdc_decimal(p['input_microunits_per_million'])} input / "
        f"{pricing.microunits_to_usdc_decimal(p['output_microunits_per_million'])} output"
        for p in prices if p.get("input_microunits_per_million") is not None and p.get("output_microunits_per_million") is not None)
    emit_runtime_event(active["source_context"], event_type="usepod_price_paused",
        message=f"Task paused: {bound.model_id} now costs {current} USDC per 1M tokens. Your limits are {cap_in} input / {cap_out} output. VOOL will continue automatically when both prices fit. Keep VOOL open, or press Stop to end this task.",
        details={"model_id": bound.model_id, "price_limits": evidence.get("approved"), "current_prices": prices})
    with runtime_active_clock.paused():
        next_check = time.monotonic() + bound.wait_poll_seconds
        while True:
            if request.is_cancelled():
                raise routing.RouteUnavailableError("price_wait_cancelled")
            remaining = next_check - time.monotonic()
            if remaining > 0:
                _sleep(min(0.2, remaining))
                continue
            next_check = time.monotonic() + bound.wait_poll_seconds
            # No old approval/account/budget can be revived by a price timer.
            if queued_target(bound.model_id) != active["target"]:
                raise routing.RouteUnavailableError("queued_price_target_changed")
            from core.usepod.spend_approval import prepaid_spend_readiness
            if not prepaid_spend_readiness(bound.model_id).get("available"):
                raise routing.RouteUnavailableError("price_wait_budget_unavailable")
            fresh_state = routing.load_route_state()
            try:
                fresh = pricing.fetch_marketplace_snapshot(origin=bound.origin)
                approval = routing.authorize_dispatch(limited, fresh, model_id=bound.model_id, policy=fresh_state.policy)
            except routing.RouteUnavailableError as exc:
                if exc.code in {"route_price_above_approved_bound", "price_stale", "price_feed_unavailable"}:
                    continue
                raise
            except Exception:
                continue  # unavailable price observation never authorizes a call
            if request.is_cancelled():
                raise routing.RouteUnavailableError("price_wait_cancelled")
            emit_runtime_event(active["source_context"], event_type="usepod_price_resumed",
                message=f"Prices are within your limits. Continuing {bound.model_id} from the paused model call.",
                details={"model_id": bound.model_id})
            return approval


_sleep = time.sleep
