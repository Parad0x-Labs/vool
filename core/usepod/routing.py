"""Which route a UsePod request may take, the price bound the owner approved, the headers that make
UsePod enforce that bound as well, and the verdict on the route that actually answered.

The failure this module exists to prevent is the one UsePod's defaults allow by design: routing is
cheapest-first with centralized fallback always on, so a model whose cheap marketplace listing
disappears keeps working -- on a centralized provider that can cost many times more. A request that
merely asked for the model has no way to say "not like that".

So a dispatch is authorized against three things, in this order, and each one fails closed:

1. **An explicit owner approval** (:class:`ApprovedRouteBound`) for this model under this routing
   policy: the per-million input and output ceilings, the route classes that may serve, and the
   prices that were current when the owner approved. Without one, nothing is sent.
2. **A fresh price snapshot** (:mod:`core.usepod.pricing`) that still lists a permitted route at or
   below the approved ceilings. A cheap route that disappeared, a price that rose, a model that was
   delisted or a snapshot past its TTL is a typed refusal -- never a silent substitution.
3. **UsePod's own enforcement** of the same bound, through the documented request headers
   (``X-Pod-Routing-Mode``, ``X-Pod-Max-Price-Input``/``-Output``, ``X-Pod-Providers``). These are
   an additional downstream guard, not a replacement for 1 and 2.

After the response starts, :func:`evaluate_route_evidence` reads ``X-Pod-Route`` and
``X-Pod-Provider-Id``. A route class or pinned provider the approval did not permit is a VIOLATION
and the call cannot produce a compliant receipt; a missing or unreadable route header is
UNVERIFIED, which is also not compliant. Nothing here claims cryptographic proof of the route --
none is documented as live.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import tempfile
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from core.usepod.descriptor import (
    HEADER_BALANCE_REMAINING,
    HEADER_MAX_PRICE_INPUT,
    HEADER_MAX_PRICE_OUTPUT,
    HEADER_PROVIDER_ID,
    HEADER_PROVIDERS,
    HEADER_ROUTE,
    HEADER_ROUTING_MODE,
)
from core.usepod.pricing import (
    ROUTE_MARKETPLACE,
    MarketplaceModel,
    MarketplaceSnapshot,
    PriceUnitError,
    RoutePrice,
    microunits_to_usdc_decimal,
    price_header_value,
    usdc_decimal_to_microunits,
)

ROUTE_CLASS_MARKETPLACE = "marketplace"
ROUTE_CLASS_KEY_RELAY = "key_relay"
ROUTE_CLASS_CENTRALIZED = "centralized"
ROUTE_CLASS_UNKNOWN = "unknown"

COMPLIANCE_COMPLIANT = "compliant"
COMPLIANCE_VIOLATED = "violated"
COMPLIANCE_UNVERIFIED = "unverified"

MAX_PINNED_PROVIDERS = 8
_PROVIDER_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_DECIMAL_RE = re.compile(r"^-?\d{1,18}(?:\.\d{1,18})?$")
_STORE_SCHEMA = "vool.usepod.route_state.v1"


class RoutingMode(str, Enum):
    AUTO = "auto"
    MARKETPLACE_ONLY = "marketplace-only"
    CENTRALIZED_ONLY = "centralized-only"


class RoutePolicyError(ValueError):
    """A routing policy that cannot be expressed or would be refused upstream. ``code`` is stable."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = str(code)
        super().__init__(f"{self.code}: {detail}" if detail else self.code)


class RouteUnavailableError(RuntimeError):
    """No permitted route at or below the approved bound. Raised BEFORE anything is sent."""

    def __init__(self, code: str, detail: str = "", *, evidence: Mapping[str, Any] | None = None) -> None:
        self.code = str(code)
        self.detail = str(detail)
        self.evidence = dict(evidence or {})
        super().__init__(f"usepod_route_unavailable:{self.code}" + (f": {self.detail}" if self.detail else ""))


def _ceiling(value: Any, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise RoutePolicyError("ceiling_invalid", f"{name} must be an integer number of microunits")
    if value < 1:
        raise RoutePolicyError("ceiling_invalid", f"{name} must be at least one microunit")
    return value


@dataclass(frozen=True)
class RoutePolicy:
    """The owner's routing choice for UsePod. Validation mirrors what UsePod itself would refuse."""

    mode: RoutingMode = RoutingMode.MARKETPLACE_ONLY
    allow_centralized_fallback: bool = False
    pinned_providers: tuple[str, ...] = ()
    max_input_microunits_per_million: int | None = None
    max_output_microunits_per_million: int | None = None

    def validated(self) -> RoutePolicy:
        try:
            mode = RoutingMode(str(getattr(self.mode, "value", self.mode)))
        except ValueError as exc:
            raise RoutePolicyError("routing_mode_unknown") from exc
        pins: list[str] = []
        for raw in tuple(self.pinned_providers or ()):
            name = str(raw or "").strip().lower()
            if not _PROVIDER_NAME_RE.fullmatch(name):
                raise RoutePolicyError("pinned_provider_name_invalid")
            if name not in pins:
                pins.append(name)
        if len(pins) > MAX_PINNED_PROVIDERS:
            raise RoutePolicyError("too_many_pinned_providers")
        max_input = _ceiling(self.max_input_microunits_per_million, "max_input_microunits_per_million")
        max_output = _ceiling(self.max_output_microunits_per_million, "max_output_microunits_per_million")
        if not isinstance(self.allow_centralized_fallback, bool):
            raise RoutePolicyError("fallback_flag_invalid")
        if pins and mode is RoutingMode.MARKETPLACE_ONLY:
            # Documented: pinning and marketplace-only together are a 400 upstream.
            raise RoutePolicyError("pin_conflicts_with_marketplace_only")
        if pins and mode is RoutingMode.AUTO:
            # Documented: a pin implies centralized-only. Say so explicitly instead of letting the
            # header change what the owner believes the mode is.
            raise RoutePolicyError("pin_requires_centralized_only")
        if mode is RoutingMode.AUTO:
            if not self.allow_centralized_fallback:
                raise RoutePolicyError("auto_requires_explicit_fallback_permission")
            if max_input is None or max_output is None:
                raise RoutePolicyError("auto_fallback_requires_both_ceilings")
        elif self.allow_centralized_fallback:
            raise RoutePolicyError("fallback_flag_only_applies_to_auto")
        return RoutePolicy(
            mode=mode,
            allow_centralized_fallback=bool(self.allow_centralized_fallback),
            pinned_providers=tuple(pins),
            max_input_microunits_per_million=max_input,
            max_output_microunits_per_million=max_output,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": RoutingMode(str(getattr(self.mode, "value", self.mode))).value,
            "allow_centralized_fallback": bool(self.allow_centralized_fallback),
            "pinned_providers": list(self.pinned_providers),
            "max_input_microunits_per_million": self.max_input_microunits_per_million,
            "max_output_microunits_per_million": self.max_output_microunits_per_million,
        }

    def digest(self) -> str:
        encoded = json.dumps(self.validated().to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("ascii")).hexdigest()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> RoutePolicy:
        """Build and validate. Ceilings may come as integer microunits or as exact decimal USDC strings."""
        payload = dict(data or {})
        allowed = {
            "mode",
            "allow_centralized_fallback",
            "pinned_providers",
            "max_input_microunits_per_million",
            "max_output_microunits_per_million",
            "max_input_usdc_per_million",
            "max_output_usdc_per_million",
        }
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise RoutePolicyError("unknown_policy_fields", ",".join(unknown))

        def _axis(micro_key: str, decimal_key: str) -> int | None:
            micro = payload.get(micro_key)
            decimal = payload.get(decimal_key)
            if micro is not None and decimal is not None:
                raise RoutePolicyError("ceiling_given_twice", micro_key)
            if decimal is not None:
                if not isinstance(decimal, str):
                    raise RoutePolicyError("ceiling_invalid", f"{decimal_key} must be a decimal string")
                try:
                    return usdc_decimal_to_microunits(decimal)
                except PriceUnitError as exc:
                    raise RoutePolicyError("ceiling_invalid", f"{decimal_key}: {exc.code}") from exc
            return micro

        pins_raw = payload.get("pinned_providers") or ()
        if isinstance(pins_raw, str):
            pins_raw = [part for part in pins_raw.split(",") if part.strip()]
        if not isinstance(pins_raw, (list, tuple)):
            raise RoutePolicyError("pinned_providers_invalid")
        fallback = payload.get("allow_centralized_fallback", False)
        if not isinstance(fallback, bool):
            raise RoutePolicyError("fallback_flag_invalid")
        return cls(
            mode=payload.get("mode", RoutingMode.MARKETPLACE_ONLY.value),
            allow_centralized_fallback=fallback,
            pinned_providers=tuple(str(item) for item in pins_raw),
            max_input_microunits_per_million=_axis("max_input_microunits_per_million", "max_input_usdc_per_million"),
            max_output_microunits_per_million=_axis("max_output_microunits_per_million", "max_output_usdc_per_million"),
        ).validated()


def _route_price_from_dict(data: Any) -> RoutePrice:
    if not isinstance(data, Mapping):
        raise ValueError("route price must be an object")

    def _int_or_none(value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("route price value invalid")
        return value

    return RoutePrice(
        route_class=str(data.get("route_class") or ""),
        provider=str(data.get("provider") or ""),
        input_microunits_per_million=_int_or_none(data.get("input_microunits_per_million")),
        output_microunits_per_million=_int_or_none(data.get("output_microunits_per_million")),
    )


@dataclass(frozen=True)
class ApprovedRouteBound:
    """The owner's explicit approval of a price bound for one model under one routing policy.

    Durable: it stands until the owner changes the policy or the approval. It never widens itself --
    a dispatch may only be authorized at or below these ceilings. A price-wait rule may
    additionally carry an explicit percentage ceiling for later calls of its claimed task.
    """

    approval_id: str
    model_id: str
    policy: RoutePolicy
    max_input_microunits_per_million: int
    max_output_microunits_per_million: int
    #: Per axis, where the ceiling came from: ``explicit_policy_ceiling`` or the discovered price kind.
    input_basis: str
    output_basis: str
    allowed_route_classes: tuple[str, ...]
    header_routing_mode: str
    header_providers: str
    discovered_prices: tuple[RoutePrice, ...]
    unlisted_pins: tuple[str, ...]
    snapshot_sha256: str
    snapshot_fetched_at: float
    snapshot_evidence: str
    approved_at: float
    #: The UsePod origin whose prices were approved. An approval never authorizes another operator.
    origin: str = ""
    wait_poll_seconds: int = 0
    active_price_tolerance_percent: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            **({"wait_poll_seconds": self.wait_poll_seconds} if self.wait_poll_seconds else {}),
            "active_price_tolerance_percent": self.active_price_tolerance_percent,
            "approval_id": self.approval_id,
            "model_id": self.model_id,
            "policy": self.policy.to_dict(),
            "policy_digest": self.policy.digest(),
            "max_input_microunits_per_million": self.max_input_microunits_per_million,
            "max_output_microunits_per_million": self.max_output_microunits_per_million,
            "max_input_usdc_per_million": microunits_to_usdc_decimal(self.max_input_microunits_per_million),
            "max_output_usdc_per_million": microunits_to_usdc_decimal(self.max_output_microunits_per_million),
            "input_basis": self.input_basis,
            "output_basis": self.output_basis,
            "allowed_route_classes": list(self.allowed_route_classes),
            "header_routing_mode": self.header_routing_mode,
            "header_providers": self.header_providers,
            "discovered_prices": [price.as_dict() for price in self.discovered_prices],
            "unlisted_pins": list(self.unlisted_pins),
            "snapshot_sha256": self.snapshot_sha256,
            "snapshot_fetched_at": self.snapshot_fetched_at,
            "snapshot_evidence": self.snapshot_evidence,
            "approved_at": self.approved_at,
            "origin": self.origin,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ApprovedRouteBound:
        policy = RoutePolicy.from_dict(dict(data.get("policy") or {}))
        if str(data.get("policy_digest") or "") != policy.digest():
            raise ValueError("approved bound policy digest does not match its policy")
        max_input = data.get("max_input_microunits_per_million")
        max_output = data.get("max_output_microunits_per_million")
        for value in (max_input, max_output):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError("approved bound ceilings invalid")
        origin = str(data.get("origin") or "")
        if not origin:
            raise ValueError("approved bound names no origin")
        classes = tuple(str(item) for item in list(data.get("allowed_route_classes") or []))
        if not classes or any(
            item not in {ROUTE_CLASS_MARKETPLACE, ROUTE_CLASS_KEY_RELAY, ROUTE_CLASS_CENTRALIZED} for item in classes
        ):
            raise ValueError("approved bound route classes invalid")
        interval = data.get("wait_poll_seconds", 0)
        if isinstance(interval, bool) or not isinstance(interval, int) or (interval and not 60 <= interval <= 3600):
            raise ValueError("wait interval must be 60–3600 seconds")
        tolerance = data.get("active_price_tolerance_percent")
        if tolerance is not None and (isinstance(tolerance, bool) or not isinstance(tolerance, int) or not 0 <= tolerance <= 1000):
            raise ValueError("Price tolerance must be 0–1000 percent")
        return cls(
            active_price_tolerance_percent=tolerance,
            wait_poll_seconds=interval,
            approval_id=str(data["approval_id"]),
            model_id=str(data["model_id"]),
            policy=policy,
            max_input_microunits_per_million=int(max_input),
            max_output_microunits_per_million=int(max_output),
            input_basis=str(data.get("input_basis") or ""),
            output_basis=str(data.get("output_basis") or ""),
            allowed_route_classes=classes,
            header_routing_mode=str(data.get("header_routing_mode") or ""),
            header_providers=str(data.get("header_providers") or ""),
            discovered_prices=tuple(_route_price_from_dict(item) for item in list(data.get("discovered_prices") or [])),
            unlisted_pins=tuple(str(item) for item in list(data.get("unlisted_pins") or [])),
            snapshot_sha256=str(data.get("snapshot_sha256") or ""),
            snapshot_fetched_at=float(data.get("snapshot_fetched_at") or 0.0),
            snapshot_evidence=str(data.get("snapshot_evidence") or ""),
            approved_at=float(data.get("approved_at") or 0.0),
            origin=origin,
        )


@dataclass(frozen=True)
class DispatchRouteApproval:
    """One dispatch's authorization: the approved bound re-checked against a fresh snapshot, now."""

    approval_id: str
    bound_approval_id: str
    model_id: str
    header_routing_mode: str
    header_providers: str
    pinned_providers: tuple[str, ...]
    max_input_microunits_per_million: int
    max_output_microunits_per_million: int
    allowed_route_classes: tuple[str, ...]
    eligible_prices: tuple[RoutePrice, ...]
    fallback_expected: bool
    snapshot_sha256: str
    snapshot_fetched_at: float
    snapshot_evidence: str
    snapshot_age_seconds: float
    checked_at: float
    notes: tuple[str, ...] = field(default_factory=tuple)

    def request_headers(self) -> tuple[tuple[str, str], ...]:
        headers = [
            (HEADER_ROUTING_MODE, self.header_routing_mode),
            (HEADER_MAX_PRICE_INPUT, price_header_value(self.max_input_microunits_per_million)),
            (HEADER_MAX_PRICE_OUTPUT, price_header_value(self.max_output_microunits_per_million)),
        ]
        if self.header_providers:
            headers.append((HEADER_PROVIDERS, self.header_providers))
        return tuple(headers)

    def as_evidence(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "bound_approval_id": self.bound_approval_id,
            "model_id": self.model_id,
            "routing_mode": self.header_routing_mode,
            "pinned_providers": list(self.pinned_providers),
            "max_input_microunits_per_million": self.max_input_microunits_per_million,
            "max_output_microunits_per_million": self.max_output_microunits_per_million,
            "allowed_route_classes": list(self.allowed_route_classes),
            "eligible_prices": [price.as_dict() for price in self.eligible_prices],
            "fallback_expected": self.fallback_expected,
            "price_source": {
                "snapshot_sha256": self.snapshot_sha256,
                "fetched_at": self.snapshot_fetched_at,
                "evidence": self.snapshot_evidence,
                "age_seconds": round(self.snapshot_age_seconds, 3),
            },
            "checked_at": self.checked_at,
            "notes": list(self.notes),
        }


def _require_fresh_model(snapshot: MarketplaceSnapshot | None, model_id: str, now: float, *, missing_code: str) -> MarketplaceModel:
    if snapshot is None:
        raise RouteUnavailableError("price_feed_unavailable")
    if snapshot.is_stale(now):
        raise RouteUnavailableError(
            "price_stale",
            evidence={"fetched_at": snapshot.fetched_at, "ttl_seconds": snapshot.ttl_seconds, "age_seconds": round(snapshot.age_seconds(now), 3)},
        )
    model = snapshot.models.get(str(model_id or ""))
    if model is None:
        raise RouteUnavailableError(missing_code, evidence={"model_id": str(model_id or ""), "snapshot_sha256": snapshot.body_sha256})
    reason = model.unusable_reason
    if reason:
        raise RouteUnavailableError(reason, evidence={"model_id": model.model_id, "problems": list(model.problems)})
    return model


def _origins_match(left: str, right: str) -> bool:
    from core.usepod.descriptor import normalize_origin

    try:
        return normalize_origin(left) == normalize_origin(right)
    except Exception:
        return False


def _marketplace_candidate(model: MarketplaceModel) -> RoutePrice | None:
    price = model.marketplace
    if price is None or not price.complete:
        return None
    if model.marketplace_provider_count is None or model.marketplace_provider_count < 1:
        return None
    return price


def _cheapest(prices: list[RoutePrice]) -> RoutePrice:
    return min(
        prices,
        key=lambda price: (
            int(price.input_microunits_per_million or 0) + int(price.output_microunits_per_million or 0),
            int(price.input_microunits_per_million or 0),
            price.provider,
        ),
    )


def approve_route_bound(
    snapshot: MarketplaceSnapshot | None,
    *,
    model_id: str,
    policy: RoutePolicy,
    now: float | None = None,
    reviewed_max_input_microunits: int | None = None,
    reviewed_max_output_microunits: int | None = None,
) -> ApprovedRouteBound:
    """Turn the owner's policy plus the CURRENT prices into an explicit, storable price bound.

    An axis with an explicit policy ceiling uses it. An axis without one is bound to the price the
    owner is looking at right now, exactly: the cheapest marketplace listing (marketplace-only), the
    cheapest centralized provider (centralized-only), or the dearest pinned provider (a pin list is a
    try-order, so every pinned provider must fit). A later price rise above that bound is a refusal,
    not a quiet re-approval.

    ``reviewed_max_*_microunits`` are the TWO-AXIS maxima an owner edited in the price review,
    for THIS model's bound only: they override the policy's axes for this approval (basis
    ``explicit_owner_review``) without rewriting the stored policy every other model approves
    under. The review is two-axis by contract — one axis alone is refused, because a half-reviewed
    bound would silently keep the unreviewed axis's old ceiling while reading as newly approved.
    """
    reviewed_axes = reviewed_max_input_microunits is not None or reviewed_max_output_microunits is not None
    if reviewed_axes and (reviewed_max_input_microunits is None or reviewed_max_output_microunits is None):
        raise RoutePolicyError("owner_review_requires_both_axes")
    if reviewed_axes:
        reviewed_max_input_microunits = _ceiling(reviewed_max_input_microunits, "reviewed_max_input_microunits")
        reviewed_max_output_microunits = _ceiling(reviewed_max_output_microunits, "reviewed_max_output_microunits")
    moment = float(time.time() if now is None else now)
    clean = policy.validated()
    model = _require_fresh_model(snapshot, model_id, moment, missing_code="model_not_listed")
    assert snapshot is not None  # for type checkers; _require_fresh_model raised otherwise

    unlisted: list[str] = []
    if clean.mode is RoutingMode.MARKETPLACE_ONLY:
        candidate = _marketplace_candidate(model)
        if candidate is None:
            raise RouteUnavailableError("marketplace_route_unavailable", evidence={"model_id": model.model_id})
        candidates = [candidate]
        derived = (int(candidate.input_microunits_per_million or 0), int(candidate.output_microunits_per_million or 0))
        derived_basis = "discovered_marketplace_price"
        allowed = (ROUTE_CLASS_MARKETPLACE, ROUTE_CLASS_KEY_RELAY)
    elif clean.mode is RoutingMode.CENTRALIZED_ONLY:
        complete = [price for price in model.centralized if price.complete]
        if clean.pinned_providers:
            listed = [model.centralized_provider(name) for name in clean.pinned_providers]
            unlisted = [name for name, price in zip(clean.pinned_providers, listed, strict=True) if price is None or not price.complete]
            candidates = [price for price in listed if price is not None and price.complete]
            if not candidates:
                raise RouteUnavailableError(
                    "pinned_providers_not_listed_for_model",
                    evidence={"model_id": model.model_id, "pinned_providers": list(clean.pinned_providers)},
                )
            derived = (
                max(int(price.input_microunits_per_million or 0) for price in candidates),
                max(int(price.output_microunits_per_million or 0) for price in candidates),
            )
            derived_basis = "discovered_pinned_price_max"
        else:
            if not complete:
                raise RouteUnavailableError("centralized_route_unavailable", evidence={"model_id": model.model_id})
            cheapest = _cheapest(complete)
            candidates = complete
            derived = (int(cheapest.input_microunits_per_million or 0), int(cheapest.output_microunits_per_million or 0))
            derived_basis = f"discovered_centralized_price:{cheapest.provider}"
        allowed = (ROUTE_CLASS_CENTRALIZED,)
    else:  # AUTO: validation guaranteed explicit ceilings on both axes
        market = _marketplace_candidate(model)
        candidates = ([market] if market is not None else []) + [price for price in model.centralized if price.complete]
        if not candidates:
            raise RouteUnavailableError("no_route_listed_for_model", evidence={"model_id": model.model_id})
        derived = (0, 0)
        derived_basis = ""
        allowed = (ROUTE_CLASS_MARKETPLACE, ROUTE_CLASS_KEY_RELAY, ROUTE_CLASS_CENTRALIZED)

    if reviewed_axes:
        # The owner's edited review outranks both the stored policy's axes and the derived
        # current-price snapshot for THIS bound. It is never applied silently: the value written
        # is exactly the value confirmed, and a price rising past it later is still a refusal.
        max_input, input_basis = reviewed_max_input_microunits, "explicit_owner_review"
        max_output, output_basis = reviewed_max_output_microunits, "explicit_owner_review"
    else:
        if clean.max_input_microunits_per_million is not None:
            max_input, input_basis = clean.max_input_microunits_per_million, "explicit_policy_ceiling"
        else:
            max_input, input_basis = derived[0], derived_basis
        if clean.max_output_microunits_per_million is not None:
            max_output, output_basis = clean.max_output_microunits_per_million, "explicit_policy_ceiling"
        else:
            max_output, output_basis = derived[1], derived_basis
    if max_input < 1 or max_output < 1:
        # A listed price of zero cannot be expressed as a ceiling (the header needs >= 1 microunit),
        # and "free" is not a bound this module will assume about a paid marketplace.
        raise RouteUnavailableError("zero_price_cannot_bound_a_ceiling", evidence={"model_id": model.model_id})
    if not any(price.within(max_input=max_input, max_output=max_output) for price in candidates):
        raise RouteUnavailableError(
            "approved_ceiling_below_every_current_price",
            evidence={
                "model_id": model.model_id,
                "max_input_microunits_per_million": max_input,
                "max_output_microunits_per_million": max_output,
                "current_prices": [price.as_dict() for price in candidates],
            },
        )
    return ApprovedRouteBound(
        approval_id=f"upb_{uuid.uuid4().hex[:24]}",
        model_id=model.model_id,
        policy=clean,
        max_input_microunits_per_million=int(max_input),
        max_output_microunits_per_million=int(max_output),
        input_basis=input_basis,
        output_basis=output_basis,
        allowed_route_classes=allowed,
        header_routing_mode=clean.mode.value,
        header_providers=",".join(clean.pinned_providers),
        discovered_prices=tuple(candidates),
        unlisted_pins=tuple(unlisted),
        snapshot_sha256=snapshot.body_sha256,
        snapshot_fetched_at=snapshot.fetched_at,
        snapshot_evidence=snapshot.evidence,
        approved_at=moment,
        origin=snapshot.origin,
    )


def authorize_dispatch(
    bound: ApprovedRouteBound | None,
    snapshot: MarketplaceSnapshot | None,
    *,
    model_id: str,
    policy: RoutePolicy,
    now: float | None = None,
) -> DispatchRouteApproval:
    """Re-check the owner's approved bound against a fresh snapshot immediately before dispatch."""
    moment = float(time.time() if now is None else now)
    if bound is None:
        raise RouteUnavailableError("route_not_approved", evidence={"model_id": str(model_id or "")})
    if bound.model_id != str(model_id or ""):
        raise RouteUnavailableError("route_approval_is_for_another_model", evidence={"model_id": str(model_id or ""), "approved_model_id": bound.model_id})
    if bound.policy.digest() != policy.digest():
        raise RouteUnavailableError("route_policy_changed_since_approval", evidence={"model_id": bound.model_id})
    if snapshot is not None and not _origins_match(snapshot.origin, bound.origin):
        # Approved against one operator's prices; this snapshot is another origin's (a re-pointed
        # credential, a staging gateway). Refused until the owner approves again for this origin.
        raise RouteUnavailableError(
            "route_approval_is_for_another_origin",
            evidence={"model_id": bound.model_id, "approved_origin": bound.origin, "snapshot_origin": snapshot.origin},
        )
    model = _require_fresh_model(snapshot, bound.model_id, moment, missing_code="model_disappeared")
    assert snapshot is not None

    notes: list[str] = []
    fallback_expected = False
    mode = bound.policy.mode
    if mode is RoutingMode.MARKETPLACE_ONLY:
        candidate = _marketplace_candidate(model)
        if candidate is None:
            raise RouteUnavailableError("marketplace_route_disappeared", evidence={"model_id": model.model_id})
        candidates = [candidate]
    elif mode is RoutingMode.CENTRALIZED_ONLY:
        if bound.policy.pinned_providers:
            candidates = [
                price
                for price in (model.centralized_provider(name) for name in bound.policy.pinned_providers)
                if price is not None and price.complete
            ]
            if not candidates:
                raise RouteUnavailableError(
                    "pinned_providers_not_listed_for_model",
                    evidence={"model_id": model.model_id, "pinned_providers": list(bound.policy.pinned_providers)},
                )
        else:
            candidates = [price for price in model.centralized if price.complete]
            if not candidates:
                raise RouteUnavailableError("centralized_route_disappeared", evidence={"model_id": model.model_id})
    else:
        market = _marketplace_candidate(model)
        candidates = ([market] if market is not None else []) + [price for price in model.centralized if price.complete]
        if not candidates:
            raise RouteUnavailableError("no_route_listed_for_model", evidence={"model_id": model.model_id})
    eligible = [
        price
        for price in candidates
        if price.within(max_input=bound.max_input_microunits_per_million, max_output=bound.max_output_microunits_per_million)
    ]
    if not eligible:
        evidence = {
            "model_id": model.model_id,
            "approved": {
                "max_input_microunits_per_million": bound.max_input_microunits_per_million,
                "max_output_microunits_per_million": bound.max_output_microunits_per_million,
            },
            "current_prices": [price.as_dict() for price in candidates],
        }
        # The axes in exact human units ride the evidence itself, so every surface that shows the
        # refusal (receipt, Activity, the recovery message) names the same facts from one source.
        with contextlib.suppress(Exception):
            axes = price_refusal_axes(evidence)
            if axes:
                evidence["refusal_axes"] = axes
        raise RouteUnavailableError(
            "route_price_above_approved_bound",
            evidence=evidence,
        )
    if mode is RoutingMode.AUTO and not any(price.route_class == ROUTE_MARKETPLACE for price in eligible):
        # The owner allowed fallback within ceilings; say BEFORE dispatch that it is what will happen.
        fallback_expected = True
        notes.append("no_marketplace_listing_within_bound_centralized_fallback_expected")
    return DispatchRouteApproval(
        approval_id=f"upd_{uuid.uuid4().hex[:24]}",
        bound_approval_id=bound.approval_id,
        model_id=bound.model_id,
        header_routing_mode=bound.header_routing_mode,
        header_providers=bound.header_providers,
        pinned_providers=bound.policy.pinned_providers,
        max_input_microunits_per_million=bound.max_input_microunits_per_million,
        max_output_microunits_per_million=bound.max_output_microunits_per_million,
        allowed_route_classes=bound.allowed_route_classes,
        eligible_prices=tuple(eligible),
        fallback_expected=fallback_expected,
        snapshot_sha256=snapshot.body_sha256,
        snapshot_fetched_at=snapshot.fetched_at,
        snapshot_evidence=snapshot.evidence,
        snapshot_age_seconds=snapshot.age_seconds(moment),
        checked_at=moment,
        notes=tuple(notes),
    )


# --- what the response says about the route that served it ---------------------------------------


@dataclass(frozen=True)
class RouteEvidence:
    route_raw: str | None
    route_class: str
    provider_id_raw: str | None
    provider_id_kind: str
    balance_remaining_raw: str | None
    balance_remaining_decimal: str | None
    balance_unit: str
    compliance: str
    reasons: tuple[str, ...]
    fallback_used: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "route_raw": self.route_raw,
            "route_class": self.route_class,
            "provider_id": self.provider_id_raw,
            "provider_id_kind": self.provider_id_kind,
            "balance_remaining_raw": self.balance_remaining_raw,
            "balance_remaining_decimal": self.balance_remaining_decimal,
            "balance_unit": self.balance_unit,
            "compliance": self.compliance,
            "reasons": list(self.reasons),
            "fallback_used": self.fallback_used,
            "route_verification": "header_reported_only_no_cryptographic_proof",
        }


def _header(headers: Mapping[str, Any], name: str) -> str | None:
    wanted = name.lower()
    for key, value in dict(headers or {}).items():
        if str(key).lower() == wanted:
            text = str(value)
            return text
    return None


def _printable(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    cleaned = "".join(ch for ch in value if 32 <= ord(ch) < 127).strip()
    return cleaned[:limit]


def normalize_route_class(raw: str | None) -> str:
    if not raw:
        return ROUTE_CLASS_UNKNOWN
    words = set(re.split(r"[\s_\-]+", raw.strip().lower()))
    if "relay" in words or "keyrelay" in words:
        return ROUTE_CLASS_KEY_RELAY
    if words & {"centralized", "centralised", "fallback"}:
        return ROUTE_CLASS_CENTRALIZED
    if "marketplace" in words:
        return ROUTE_CLASS_MARKETPLACE
    return ROUTE_CLASS_UNKNOWN


def evaluate_route_evidence(approval: DispatchRouteApproval, headers: Mapping[str, Any]) -> RouteEvidence:
    """The verdict on the route that answered, from the documented response headers only."""
    route_raw = _printable(_header(headers, HEADER_ROUTE), 64)
    provider_raw = _printable(_header(headers, HEADER_PROVIDER_ID), 128)
    balance_raw = _printable(_header(headers, HEADER_BALANCE_REMAINING), 64)
    route_class = normalize_route_class(route_raw)
    if provider_raw is None or provider_raw == "":
        provider_kind = "absent"
    elif _UUID_RE.fullmatch(provider_raw):
        provider_kind = "uuid"
    elif _PROVIDER_NAME_RE.fullmatch(provider_raw.lower()):
        provider_kind = "name"
    else:
        provider_kind = "unrecognized"

    reasons: list[str] = []
    compliance = COMPLIANCE_COMPLIANT
    if route_raw is None or route_raw == "":
        compliance = COMPLIANCE_UNVERIFIED
        reasons.append("route_header_missing")
    elif route_class == ROUTE_CLASS_UNKNOWN:
        compliance = COMPLIANCE_UNVERIFIED
        reasons.append("route_header_unrecognized")
    elif route_class not in approval.allowed_route_classes:
        compliance = COMPLIANCE_VIOLATED
        reasons.append(f"route_class_not_permitted:{route_class}")

    if compliance != COMPLIANCE_VIOLATED and approval.pinned_providers:
        if provider_kind == "name" and provider_raw is not None and provider_raw.lower() not in approval.pinned_providers:
            compliance = COMPLIANCE_VIOLATED
            reasons.append(f"provider_not_in_pin:{provider_raw.lower()}")
        elif provider_kind != "name" and compliance == COMPLIANCE_COMPLIANT:
            compliance = COMPLIANCE_UNVERIFIED
            reasons.append("pinned_provider_not_confirmed_by_response")
    if compliance == COMPLIANCE_COMPLIANT:
        expected_kind = "name" if route_class == ROUTE_CLASS_CENTRALIZED else "uuid"
        if provider_kind == "absent":
            reasons.append("provider_id_header_missing")
        elif provider_kind != expected_kind:
            # Documented: a name for centralized routes, a UUID for marketplace and key relay. A
            # mismatch means the metadata does not describe itself consistently.
            compliance = COMPLIANCE_UNVERIFIED
            reasons.append("provider_id_shape_inconsistent_with_route")

    balance_decimal = balance_raw if balance_raw is not None and _DECIMAL_RE.fullmatch(balance_raw) else None
    if balance_raw is not None and balance_decimal is None:
        reasons.append("balance_header_unparseable")
    return RouteEvidence(
        route_raw=route_raw,
        route_class=route_class,
        provider_id_raw=provider_raw,
        provider_id_kind=provider_kind,
        balance_remaining_raw=balance_raw,
        balance_remaining_decimal=balance_decimal,
        balance_unit="unverified_header_unit",
        compliance=compliance,
        reasons=tuple(reasons),
        fallback_used=approval.header_routing_mode == RoutingMode.AUTO.value and route_class == ROUTE_CLASS_CENTRALIZED,
    )


# --- the owner's stored policy and approvals -----------------------------------------------------------


@dataclass(frozen=True)
class RouteState:
    policy: RoutePolicy
    bounds: Mapping[str, ApprovedRouteBound]
    #: "" when the store read cleanly. A non-empty error means NO approval is honoured.
    error: str = ""


def _store_path() -> Path:
    from core.runtime_paths import active_data_dir

    return (active_data_dir() / "usepod" / "route_state.json").resolve()


def load_route_state() -> RouteState:
    """The owner's policy and approvals. A missing store is the default policy with no approvals; an
    unreadable or tampered one is the default policy with no approvals AND an error saying so."""
    path = _store_path()
    if not path.exists():
        return RouteState(RoutePolicy(), {})
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or record.get("schema") != _STORE_SCHEMA:
            raise ValueError("schema")
        policy = RoutePolicy.from_dict(dict(record.get("policy") or {}))
        bounds: dict[str, ApprovedRouteBound] = {}
        for model_id, raw in dict(record.get("bounds") or {}).items():
            bound = ApprovedRouteBound.from_dict(dict(raw))
            if bound.model_id != model_id:
                raise ValueError("bound keyed under another model")
            bounds[model_id] = bound
        return RouteState(policy, bounds)
    except Exception:
        return RouteState(RoutePolicy(), {}, "route_store_unreadable")


def _write_state(policy: RoutePolicy, bounds: Mapping[str, ApprovedRouteBound]) -> None:
    path = _store_path()
    record = {
        "schema": _STORE_SCHEMA,
        "policy": policy.to_dict(),
        "bounds": {model_id: bound.to_dict() for model_id, bound in sorted(bounds.items())},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, sort_keys=True, separators=(",", ":"))
        os.replace(temp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp)


def save_route_policy(policy: RoutePolicy) -> RouteState:
    """Persist a new policy. Approvals made under a different policy are dropped, not reinterpreted."""
    clean = policy.validated()
    state = load_route_state()
    kept = {model_id: bound for model_id, bound in state.bounds.items() if bound.policy.digest() == clean.digest()}
    _write_state(clean, kept)
    return load_route_state()


def save_approved_bound(bound: ApprovedRouteBound) -> RouteState:
    state = load_route_state()
    if state.error:
        raise RoutePolicyError("route_store_unreadable")
    if bound.policy.digest() != state.policy.digest():
        raise RoutePolicyError("approval_policy_is_not_the_stored_policy")
    bounds = dict(state.bounds)
    bounds[bound.model_id] = bound
    _write_state(state.policy, bounds)
    return load_route_state()


def forget_approved_bound(model_id: str) -> RouteState:
    state = load_route_state()
    if state.error:
        return state
    bounds = {key: value for key, value in state.bounds.items() if key != str(model_id or "")}
    _write_state(state.policy, bounds)
    return load_route_state()


def price_refusal_axes(evidence: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Which axis exceeded which saved maximum, in exact human units, from a refusal's evidence.

    Reads the ``route_price_above_approved_bound`` evidence ``authorize_dispatch`` raises (the
    saved two-axis maxima plus the current prices, in microunits) and converts each violating
    axis with the SAME exact decimal utility the ceilings were stored through — never binary
    float money arithmetic. One row per axis that actually exceeded, for the CHEAPEST current
    price (the best case; if even that exceeds the saved maximum, that is the fact to show).
    [] for any other shape: absent evidence is not a verdict about any axis.
    """
    facts = dict(evidence or {})
    approved = facts.get("approved") if isinstance(facts.get("approved"), Mapping) else {}
    prices = facts.get("current_prices") if isinstance(facts.get("current_prices"), list) else []
    if not approved or not prices:
        return []

    def _axis_usdc(microunits: Any) -> str | None:
        try:
            return str(microunits_to_usdc_decimal(int(microunits)))
        except (TypeError, ValueError, PriceUnitError):
            return None

    def _price_sum(row: Mapping[str, Any]) -> int:
        return int(row.get("input_microunits_per_million") or 0) + int(row.get("output_microunits_per_million") or 0)

    cheapest = min((row for row in prices if isinstance(row, Mapping)), key=_price_sum, default=None)
    if cheapest is None:
        return []
    axes: list[dict[str, Any]] = []
    for axis, saved_key, current_key in (
        ("input", "max_input_microunits_per_million", "input_microunits_per_million"),
        ("output", "max_output_microunits_per_million", "output_microunits_per_million"),
    ):
        saved_micro = approved.get(saved_key)
        current_micro = cheapest.get(current_key)
        if saved_micro is None or current_micro is None:
            continue
        if int(current_micro) <= int(saved_micro):
            continue
        saved_usdc = _axis_usdc(saved_micro)
        observed_usdc = _axis_usdc(current_micro)
        if saved_usdc is None or observed_usdc is None:
            continue
        axes.append(
            {
                "axis": axis,
                "observed_usdc_per_million": observed_usdc,
                "saved_max_usdc_per_million": saved_usdc,
                "route_class": str(cheapest.get("route_class") or cheapest.get("kind") or ""),
                "provider": str(cheapest.get("provider") or ""),
            }
        )
    return axes


__all__ = [
    "COMPLIANCE_COMPLIANT",
    "COMPLIANCE_UNVERIFIED",
    "COMPLIANCE_VIOLATED",
    "ROUTE_CLASS_CENTRALIZED",
    "ROUTE_CLASS_KEY_RELAY",
    "ROUTE_CLASS_MARKETPLACE",
    "ROUTE_CLASS_UNKNOWN",
    "ApprovedRouteBound",
    "DispatchRouteApproval",
    "RouteEvidence",
    "RoutePolicy",
    "RoutePolicyError",
    "RouteState",
    "RouteUnavailableError",
    "RoutingMode",
    "approve_route_bound",
    "authorize_dispatch",
    "evaluate_route_evidence",
    "forget_approved_bound",
    "load_route_state",
    "normalize_route_class",
    "price_refusal_axes",
    "save_approved_bound",
    "save_route_policy",
]
