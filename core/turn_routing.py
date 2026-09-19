"""A9 P0 — ONE turn-scoped routing, context and retry authority.

THE CONTRACT
------------
Every turn that is about to spend a provider call is routed by ONE typed
``TurnRoutingPlan`` minted BEFORE the spend and owned by the canonical turn. The plan
carries the requested and selected provider/model, the locality/privacy ceiling, the
allowed provider/model set, the free/paid class, the cost ceiling, the explicit pin,
the fallback ladder, the retry policy, the context identity and the reason — and one
``CandidateEligibility`` row per known manifest. The ranker filters by that eligibility
and the broker (``MemoryFirstRouter._invoke_manifest``) enforces the same plan before
any adapter is built, so a prohibited candidate receives ZERO generation calls, not a
generated-then-discarded answer.

WHY A TYPED PLAN AND NOT ANOTHER SCATTERED GATE
-----------------------------------------------
Before this module, eligibility was re-derived at every hop: the ranker
(``core.model_selection_policy``) applied hard exclusions, ``resolve_provider_routing_plan``
re-scored them, the autopilot re-selected from ``ProviderCapabilityTruth`` alone, and
``_invoke_manifest`` re-checked local-only/paid/authorship fences independently — each
hop trusting that the others agreed. Sticky per-chat model ids (``model_selection:
sticky``) and process-global policy reads could widen or narrow a turn's candidate set
between hops. The plan is the single mint: frozen at turn start, snapshotted into the
turn's context under a reserved key, and enforced unchanged at the one seam every
provider call crosses.

DESIGN LAWS
-----------
* **Turn-scoped, never process-sticky.** The plan rides
  ``source_context[TURN_ROUTING_PLAN_KEY]`` (a reserved trust key — inbound HTTP bodies
  are stripped of it), so it crosses the thread-pool hops a ContextVar cannot, and dies
  with the turn. No module-level mutable state holds a live plan.
* **One eligibility decision.** ``plan.permits(provider_id, model_id)`` is the only read
  the ranker and the broker share; a manifest the plan never evaluated is not permitted
  (absence is not consent).
* **No silent widening.** free→paid and local→cloud are ceilings, not preferences: the
  paid arm opens only through an explicit grant that survives the local-only
  intersection, and a pinned model yields a ladder of exactly one. A retry generation
  inherits the STRICTER of the original and current fences.
* **Provenance is not semantic truth.** The plan is recorded durably per turn
  (``turn_routing_events.jsonl``) for "why did that fail?" / "which model served this?"
  — but it is never injected into prompt content and never participates in answer
  semantics.
* **Identity fails closed.** A plan cannot be minted without a non-blank turn id and
  session id: routing a spend onto an unnamed turn is exactly the ambiguity this
  authority exists to refuse.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import threading
import time
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.enum_compat import StrEnum

#: The source_context key the minted plan rides under. Reserved exactly like the trust
#: keys: only core code may write one, and every inbound HTTP body carrying it is
#: stripped at the door (registered in ``core.request_trust.RESERVED_TRUST_KEYS``).
TURN_ROUTING_PLAN_KEY = "turn_routing_plan"

#: The source_context key a retry linkage marker rides under: the follow-up lane stamps
#: it when a "retry that exact request" turn re-dispatches the ORIGINAL request, so the
#: plan minted for the retry turn is a NEW GENERATION linked to the original plan.
TURN_ROUTING_RETRY_KEY = "turn_routing_retry"

#: Durable per-turn routing events (plans, provenance, failures). JSONL under the
#: runtime home; read by the follow-up lane and the served surface's "why" answers.
ROUTING_EVENTS_FILENAME = "turn_routing_events.jsonl"

#: Where temporary routing rules persist. Whole-file JSON, atomically replaced, read
#: fresh on every access so a restart adopts whatever is on disk.
RULES_STORE_RELNAME = ("data", "turn_routing_rules.json")


class RoutingIdentityError(ValueError):
    """Routing identity was missing or ambiguous — refuse BEFORE any spend."""


class RoutingPlanRefused(RuntimeError):
    """The broker was asked to invoke a manifest the turn's plan does not permit."""

    def __init__(self, provider_id: str, model_id: str, reason: str) -> None:
        super().__init__(f"routing_plan_refused:{reason}:{provider_id}/{model_id}")
        self.provider_id = str(provider_id)
        self.model_id = str(model_id)
        self.reason = str(reason)


class LocalityCeiling(StrEnum):
    """Where a turn's model calls may physically execute. A CEILING, not a preference."""
    LOCAL_MACHINE_ONLY = "LOCAL_MACHINE_ONLY"
    LOCAL_FIRST = "LOCAL_FIRST"
    UNRESTRICTED = "UNRESTRICTED"


class PrivacyCeiling(StrEnum):
    PRIVATE_LOCAL_ONLY = "PRIVATE_LOCAL_ONLY"
    REMOTE_ALLOWED = "REMOTE_ALLOWED"


class PlanCostClass(StrEnum):
    FREE_LOCAL = "free_local"
    FREE_CLOUD = "free_cloud"
    REMOTE_UNKNOWN = "remote_unknown"
    PAID_CLOUD = "paid_cloud"


class PinKind(StrEnum):
    NONE = "NONE"
    MODEL = "MODEL"
    PROVIDER = "PROVIDER"


class RoutingFailureKind(StrEnum):
    """The five DISTINCT terminal vocabularies of a routed execution.

    They are deliberately not a severity ladder: a REFUSED call never reached a socket,
    a CANCELLED one was abandoned by the caller, a TIMEOUT spent its deadline on the
    wire, an UNAVAILABLE one was refused or unreachable at the transport layer, and a
    PARTIAL one answered but not completely. "Why did that fail?" must name which of
    these happened — collapsing them is how a policy refusal reads as a provider outage.
    """
    REFUSED = "REFUSED"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"
    UNAVAILABLE = "UNAVAILABLE"
    PARTIAL = "PARTIAL"


#: Transport-error fragments → failure kind. Order matters: cancellation is checked
#: before timeout because a cancelled wait often surfaces as a timeout underneath.
_ERROR_KIND_FRAGMENTS: tuple[tuple[tuple[str, ...], RoutingFailureKind], ...] = (
    (("cancelled", "canceled", "cancel requested"), RoutingFailureKind.CANCELLED),
    (("timed out", "timeout", "deadline"), RoutingFailureKind.TIMEOUT),
    (("partial", "incomplete response", "truncated"), RoutingFailureKind.PARTIAL),
    (
        ("unavailable", "unreachable", "connection refused", "connection reset",
         "connection refused", "refused connection", "broken pipe", "gone away",
         "bad gateway", "service unavailable", "no route", "name or service not known"),
        RoutingFailureKind.UNAVAILABLE,
    ),
)


def classify_provider_error(error: Any) -> RoutingFailureKind:
    """Map an adapter/transport error onto the five-kind vocabulary, honestly.

    Unknown errors are UNAVAILABLE, not REFUSED: REFUSED is reserved for policy
    decisions made before any socket was touched, and an unrecognized transport
    failure is a provider that could not serve, not a policy refusal.
    """
    text = " ".join(str(error or "").split()).lower()
    for fragments, kind in _ERROR_KIND_FRAGMENTS:
        if any(fragment in text for fragment in fragments):
            return kind
    return RoutingFailureKind.UNAVAILABLE


@dataclass(frozen=True)
class CandidateEligibility:
    """ONE manifest's eligibility under the turn's fences — the shared decision."""

    provider_id: str
    model_id: str
    locality: str  # "local" | "remote"
    cost_class: str
    allowed: bool
    reason: str  # "eligible" or the first fence that refused

    def to_record(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "locality": self.locality,
            "cost_class": self.cost_class,
            "allowed": self.allowed,
            "reason": self.reason,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> CandidateEligibility:
        return cls(
            provider_id=str(record.get("provider_id") or ""),
            model_id=str(record.get("model_id") or ""),
            locality=str(record.get("locality") or ""),
            cost_class=str(record.get("cost_class") or ""),
            allowed=bool(record.get("allowed")),
            reason=str(record.get("reason") or ""),
        )


@dataclass(frozen=True)
class RetryPolicy:
    """What may happen after a failed attempt, per failure kind.

    REFUSED and CANCELLED never auto-retry: a policy refusal repeats itself and a
    cancelled turn must stay cancelled. TIMEOUT/UNAVAILABLE may advance the ladder (the
    same dead provider is not retried first). PARTIAL may retry the SAME candidate —
    the model was reachable and its answer was just incomplete.
    """

    max_attempts: int = 2
    retry_failure_kinds: tuple[RoutingFailureKind, ...] = (
        RoutingFailureKind.TIMEOUT,
        RoutingFailureKind.UNAVAILABLE,
        RoutingFailureKind.PARTIAL,
    )
    ladder_advance_kinds: tuple[RoutingFailureKind, ...] = (
        RoutingFailureKind.TIMEOUT,
        RoutingFailureKind.UNAVAILABLE,
    )

    def to_record(self) -> dict[str, Any]:
        return {
            "max_attempts": self.max_attempts,
            "retry_failure_kinds": [kind.value for kind in self.retry_failure_kinds],
            "ladder_advance_kinds": [kind.value for kind in self.ladder_advance_kinds],
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> RetryPolicy:
        def _kinds(key: str) -> tuple[RoutingFailureKind, ...]:
            return tuple(
                RoutingFailureKind(value)
                for value in (record.get(key) or [])
                if str(value) in RoutingFailureKind._value2member_map_
            )

        try:
            max_attempts = max(1, int(record.get("max_attempts") or 2))
        except (TypeError, ValueError):
            max_attempts = 2
        return cls(
            max_attempts=max_attempts,
            retry_failure_kinds=_kinds("retry_failure_kinds") or cls().retry_failure_kinds,
            ladder_advance_kinds=_kinds("ladder_advance_kinds") or cls().ladder_advance_kinds,
        )


@dataclass(frozen=True)
class TurnRoutingPlan:
    """The turn's routing truth: minted before spend, immutable after."""

    plan_id: str
    turn_id: str
    session_id: str
    conversation_ref: str
    requested_provider: str
    requested_model: str
    pin_kind: PinKind
    selected_provider: str
    selected_model: str
    selected_cost_class: str
    selected_locality: str
    locality_ceiling: LocalityCeiling
    privacy_ceiling: PrivacyCeiling
    allowed_provider_ids: tuple[str, ...]
    allowed_models: tuple[tuple[str, str], ...]
    paid_allowed: bool
    cost_ceiling_usd: float
    fallback_ladder: tuple[str, ...]
    eligibility: tuple[CandidateEligibility, ...]
    retry_policy: RetryPolicy
    context_identity: str
    context_reason: str
    reason: str
    applied_rule_ids: tuple[str, ...] = ()
    retry_of_plan_id: str = ""
    original_request_id: str = ""
    original_user_text_digest: str = ""
    generation: int = 1
    created_at_unix_ms: int = 0

    # ------------------------------------------------------------------ reads

    def permits(self, provider_id: str, model_id: str) -> CandidateEligibility | None:
        """The single eligibility read shared by the ranker and the broker.

        Matches at either provider granularity (composite ``name:model`` id or the bare
        provider name) with the model id. None means the plan never evaluated this
        candidate: absence is not consent.
        """
        wanted_provider = str(provider_id)
        wanted_model = str(model_id)
        bare = wanted_provider.split(":")[0]
        for row in self.eligibility:
            if row.model_id != wanted_model:
                continue
            if row.provider_id == wanted_provider or row.provider_id == f"{bare}:{wanted_model}":
                return row
        return None

    def ladder_index(self, provider_id: str) -> int:
        try:
            return self.fallback_ladder.index(provider_id)
        except ValueError:
            return -1

    def digest(self) -> str:
        """Stable content digest of the plan's routing truth (identity-excluded fields
        like created_at are excluded so a replayed plan digests identically)."""
        record = self.to_record()
        record.pop("created_at_unix_ms", None)
        payload = json.dumps(record, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_record(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "turn_id": self.turn_id,
            "session_id": self.session_id,
            "conversation_ref": self.conversation_ref,
            "requested_provider": self.requested_provider,
            "requested_model": self.requested_model,
            "pin_kind": self.pin_kind.value,
            "selected_provider": self.selected_provider,
            "selected_model": self.selected_model,
            "selected_cost_class": self.selected_cost_class,
            "selected_locality": self.selected_locality,
            "locality_ceiling": self.locality_ceiling.value,
            "privacy_ceiling": self.privacy_ceiling.value,
            "allowed_provider_ids": list(self.allowed_provider_ids),
            "allowed_models": [list(pair) for pair in self.allowed_models],
            "paid_allowed": self.paid_allowed,
            "cost_ceiling_usd": self.cost_ceiling_usd,
            "fallback_ladder": list(self.fallback_ladder),
            "eligibility": [row.to_record() for row in self.eligibility],
            "retry_policy": self.retry_policy.to_record(),
            "context_identity": self.context_identity,
            "context_reason": self.context_reason,
            "reason": self.reason,
            "applied_rule_ids": list(self.applied_rule_ids),
            "retry_of_plan_id": self.retry_of_plan_id,
            "original_request_id": self.original_request_id,
            "original_user_text_digest": self.original_user_text_digest,
            "generation": self.generation,
            "created_at_unix_ms": self.created_at_unix_ms,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> TurnRoutingPlan:
        return cls(
            plan_id=str(record.get("plan_id") or ""),
            turn_id=str(record.get("turn_id") or ""),
            session_id=str(record.get("session_id") or ""),
            conversation_ref=str(record.get("conversation_ref") or ""),
            requested_provider=str(record.get("requested_provider") or ""),
            requested_model=str(record.get("requested_model") or ""),
            pin_kind=PinKind(str(record.get("pin_kind") or "NONE")),
            selected_provider=str(record.get("selected_provider") or ""),
            selected_model=str(record.get("selected_model") or ""),
            selected_cost_class=str(record.get("selected_cost_class") or ""),
            selected_locality=str(record.get("selected_locality") or ""),
            locality_ceiling=LocalityCeiling(str(record.get("locality_ceiling") or "UNRESTRICTED")),
            privacy_ceiling=PrivacyCeiling(str(record.get("privacy_ceiling") or "REMOTE_ALLOWED")),
            allowed_provider_ids=tuple(str(item) for item in record.get("allowed_provider_ids") or ()),
            allowed_models=tuple(
                (str(pair[0]), str(pair[1])) for pair in record.get("allowed_models") or ()
            ),
            paid_allowed=bool(record.get("paid_allowed")),
            cost_ceiling_usd=float(record.get("cost_ceiling_usd") or 0.0),
            fallback_ladder=tuple(str(item) for item in record.get("fallback_ladder") or ()),
            eligibility=tuple(
                CandidateEligibility.from_record(row) for row in record.get("eligibility") or ()
            ),
            retry_policy=RetryPolicy.from_record(record.get("retry_policy") or {}),
            context_identity=str(record.get("context_identity") or ""),
            context_reason=str(record.get("context_reason") or ""),
            reason=str(record.get("reason") or ""),
            applied_rule_ids=tuple(str(item) for item in record.get("applied_rule_ids") or ()),
            retry_of_plan_id=str(record.get("retry_of_plan_id") or ""),
            original_request_id=str(record.get("original_request_id") or ""),
            original_user_text_digest=str(record.get("original_user_text_digest") or ""),
            generation=int(record.get("generation") or 1),
            created_at_unix_ms=int(record.get("created_at_unix_ms") or 0),
        )


def _now_ms() -> int:
    return int(time.time() * 1000)


def _manifest_locality(manifest: Any) -> str:
    try:
        from core.local_model_policy import manifest_is_local

        return "local" if manifest_is_local(manifest) else "remote"
    except Exception:
        base_url = str((getattr(manifest, "runtime_config", None) or {}).get("base_url") or "")
        host = base_url.split("//")[-1].split(":")[0] if "//" in base_url else base_url
        return "remote" if host not in {"127.0.0.1", "localhost", ""} else "local"


def _provider_matches(selector: str, *, provider_id: str, provider_name: str) -> bool:
    """Dual-granularity provider matching.

    A manifest's ``provider_id`` is the composite ``provider_name:model_name`` (e.g.
    ``stub-local:a9-free-a-1.5b``), while callers naturally name the PROVIDER
    (``stub-local``). A selector matches when it is the composite id, the bare name, or
    a prefix of the composite — one law for fences, pins and rule targets alike.
    """
    clean = str(selector or "").strip()
    if not clean:
        return False
    return (
        provider_id == clean
        or provider_name == clean
        or provider_id.startswith(f"{clean}:")
    )


def _manifest_provider_name(manifest: Any) -> str:
    return str(getattr(manifest, "provider_name", "") or "")


def _manifest_cost_class(manifest: Any) -> str:
    try:
        from core.model_selection_policy import provider_cost_class

        return str(provider_cost_class(manifest))
    except Exception:
        return str((getattr(manifest, "metadata", None) or {}).get("cost_class") or "remote_unknown")


def _charges_the_user(manifest: Any, cost_class: str) -> bool:
    """The paid fence shares the ranker's reading (``core.model_selection_policy.charges_the_user``).

    A BYOK manifest carries an explicit ``paid_cloud`` class so paid models stay metered, and a model
    the catalog prices at zero is exempt there; it MUST be exempt here too. Measured 2026-09-08: this
    fence refused the operator's pinned ``:free`` model as paid while the ranker admitted it, the pin
    fell ``pin_unresolved`` and the free-cloud boost auto-picked a different model behind the label.
    """
    try:
        from core.model_selection_policy import charges_the_user

        return bool(charges_the_user(manifest, cost_class=cost_class))
    except Exception:
        return cost_class == "paid_cloud"


def _eligibility_reason(
    manifest: Any,
    *,
    locality: str,
    cost_class: str,
    local_only: bool,
    privacy_local_only: bool,
    allowed_provider_ids: tuple[str, ...] | None,
    allowed_models: tuple[tuple[str, str], ...] | None,
    paid_allowed: bool,
) -> str:
    provider_id = str(manifest.provider_id)
    provider_name = _manifest_provider_name(manifest)
    model_id = str(manifest.model_name)
    if not bool(getattr(manifest, "enabled", True)):
        return "disabled"
    if local_only and locality != "local":
        return "local_only_turn"
    if privacy_local_only and locality != "local":
        return "privacy_ceiling_remote"
    if allowed_provider_ids is not None and not any(
        _provider_matches(selector, provider_id=provider_id, provider_name=provider_name)
        for selector in allowed_provider_ids
    ):
        return "provider_not_allowed"
    if allowed_models is not None and not any(
        _provider_matches(selector, provider_id=provider_id, provider_name=provider_name)
        and model == model_id
        for selector, model in allowed_models
    ):
        return "model_not_allowed"
    if not paid_allowed and _charges_the_user(manifest, cost_class):
        return "paid_not_permitted"
    return "eligible"


def mint_turn_routing_plan(
    *,
    turn_id: str,
    session_id: str,
    conversation_ref: str = "",
    manifests: Iterable[Any],
    requested_provider: str = "",
    requested_model: str = "",
    local_only: bool = False,
    allow_paid: bool = False,
    allowed_provider_ids: tuple[str, ...] | None = None,
    allowed_models: tuple[tuple[str, str], ...] | None = None,
    cost_ceiling_usd: float = 0.0,
    context_identity: str = "",
    context_reason: str = "",
    applied_rule_ids: tuple[str, ...] = (),
    retry_linkage: Mapping[str, Any] | None = None,
    now_unix_ms: int | None = None,
) -> TurnRoutingPlan:
    """Mint the turn's ONE routing plan — the only constructor of live plans.

    Identity first, fences second, ladder third:

    * ``turn_id``/``session_id`` must be non-blank (``RoutingIdentityError`` otherwise)
      — a spend without an owning turn is the ambiguity this authority refuses.
    * The pin, when present and resolvable, contracts the ladder to exactly that
      candidate; an unresolvable pin fails closed (``pin_unresolved``) rather than
      silently widening to whatever else is installed.
    * Fences are ceilings evaluated once per manifest; the resulting eligibility rows
      are what both the ranker and the broker consume.
    """
    clean_turn = str(turn_id or "").strip()
    clean_session = str(session_id or "").strip()
    if not clean_turn:
        raise RoutingIdentityError("turn_id is required to route a turn")
    if not clean_session:
        raise RoutingIdentityError("session_id is required to route a turn")

    retry = dict(retry_linkage or {})
    if requested_provider and requested_model:
        pin_kind = PinKind.MODEL
    elif requested_provider:
        pin_kind = PinKind.PROVIDER
    elif requested_model:
        pin_kind = PinKind.MODEL
    else:
        pin_kind = PinKind.NONE

    paid_allowed = bool(allow_paid) and not bool(local_only)
    privacy_ceiling = (
        PrivacyCeiling.PRIVATE_LOCAL_ONLY
        if local_only
        else PrivacyCeiling.REMOTE_ALLOWED
    )
    locality_ceiling = (
        LocalityCeiling.LOCAL_MACHINE_ONLY
        if local_only
        else LocalityCeiling.LOCAL_FIRST
    )

    manifest_list = list(manifests)
    # Fence precedence: explicit provider/model allow-lists first (they are operator
    # intent), then the pin, then the standing ceilings.
    effective_allowed_providers = allowed_provider_ids
    effective_allowed_models = allowed_models
    if pin_kind is PinKind.MODEL and requested_model:
        # A model pin fences by MODEL, with or without a provider half. A model-only pin
        # (the chat composer's shape: "model": "x", no provider) admits every manifest
        # carrying that model name and nothing else — otherwise a differently-tagged
        # sibling survives the fence as "eligible" and outranks the degraded pin.
        if requested_provider:
            requested_pair = (requested_provider, requested_model)
            effective_allowed_models = (
                (requested_pair,)
                if allowed_models is None
                else tuple(pair for pair in allowed_models if pair == requested_pair)
            )
        else:
            pinned_pairs = tuple(
                (_manifest_provider_name(manifest), str(manifest.model_name))
                for manifest in manifest_list
                if str(manifest.model_name) == str(requested_model)
            )
            effective_allowed_models = (
                pinned_pairs
                if allowed_models is None
                else tuple(pair for pair in allowed_models if pair[1] == requested_model)
            )
    elif pin_kind is PinKind.PROVIDER:
        effective_allowed_providers = (
            (requested_provider,)
            if allowed_provider_ids is None
            else tuple(pid for pid in allowed_provider_ids if pid == requested_provider)
        )

    rows: list[CandidateEligibility] = []
    for manifest in manifest_list:
        try:
            provider_id = str(manifest.provider_id)
            model_id = str(manifest.model_name)
        except AttributeError:
            continue
        locality = _manifest_locality(manifest)
        cost_class = _manifest_cost_class(manifest)
        reason = _eligibility_reason(
            manifest,
            locality=locality,
            cost_class=cost_class,
            local_only=bool(local_only),
            privacy_local_only=privacy_ceiling is PrivacyCeiling.PRIVATE_LOCAL_ONLY,
            allowed_provider_ids=effective_allowed_providers,
            allowed_models=effective_allowed_models,
            paid_allowed=paid_allowed,
        )
        rows.append(
            CandidateEligibility(
                provider_id=provider_id,
                model_id=model_id,
                locality=locality,
                cost_class=cost_class,
                allowed=reason == "eligible",
                reason=reason,
            )
        )

    ladder = tuple(row.provider_id for row in rows if row.allowed)
    if pin_kind is not PinKind.NONE:
        pinned_rows = [row for row in rows if row.allowed and (
            (requested_model and row.model_id == requested_model)
            or (requested_provider and not requested_model and row.provider_id == requested_provider)
        )]
        if pinned_rows:
            ladder = tuple(row.provider_id for row in pinned_rows)
            reason = "pinned"
        else:
            ladder = ()
            reason = "pin_unresolved"
    elif ladder:
        # Local-first inside the ladder unless the ceiling is unrestricted: the ceiling
        # decides locality, the ladder orders within it.
        if locality_ceiling is not LocalityCeiling.UNRESTRICTED:
            ladder = tuple(
                sorted(ladder, key=lambda pid: (0 if next(r for r in rows if r.provider_id == pid).locality == "local" else 1,))
            )
        reason = "auto_ranked"
    else:
        reason = "no_eligible_candidate"

    selected = next((row for row in rows if row.provider_id == (ladder[0] if ladder else "")), None)
    return TurnRoutingPlan(
        plan_id=f"trp-{uuid.uuid4().hex}",
        turn_id=clean_turn,
        session_id=clean_session,
        conversation_ref=str(conversation_ref or "").strip(),
        requested_provider=str(requested_provider or "").strip(),
        requested_model=str(requested_model or "").strip(),
        pin_kind=pin_kind,
        selected_provider=selected.provider_id if selected else "",
        selected_model=selected.model_id if selected else "",
        selected_cost_class=selected.cost_class if selected else "",
        selected_locality=selected.locality if selected else "",
        locality_ceiling=locality_ceiling,
        privacy_ceiling=privacy_ceiling,
        allowed_provider_ids=tuple(effective_allowed_providers or ()),
        allowed_models=tuple(effective_allowed_models or ()),
        paid_allowed=paid_allowed,
        cost_ceiling_usd=max(0.0, float(cost_ceiling_usd or 0.0)),
        fallback_ladder=ladder,
        eligibility=tuple(rows),
        retry_policy=RetryPolicy(),
        context_identity=str(context_identity or "").strip(),
        context_reason=str(context_reason or "").strip(),
        reason=reason,
        applied_rule_ids=tuple(applied_rule_ids),
        retry_of_plan_id=str(retry.get("retry_of_plan_id") or ""),
        original_request_id=str(retry.get("original_request_id") or ""),
        original_user_text_digest=str(retry.get("original_user_text_digest") or ""),
        generation=int(retry.get("generation") or 1),
        created_at_unix_ms=int(now_unix_ms if now_unix_ms is not None else _now_ms()),
    )


def mint_retry_plan(
    original: TurnRoutingPlan,
    *,
    turn_id: str,
    session_id: str,
    conversation_ref: str = "",
    manifests: Iterable[Any],
    local_only: bool = False,
    allow_paid: bool = False,
    allowed_provider_ids: tuple[str, ...] | None = None,
    allowed_models: tuple[tuple[str, str], ...] | None = None,
    cost_ceiling_usd: float = 0.0,
    context_identity: str = "",
    context_reason: str = "",
    now_unix_ms: int | None = None,
) -> TurnRoutingPlan:
    """Mint the NEXT GENERATION of an original plan, linked — never widened.

    The retry carries the original plan's identity (``retry_of_plan_id``, the original
    request id and user-text digest) and inherits the STRICTER of each fence: a plan
    that ran free stays free even if the retry caller asks for the paid arm, and a
    local-only original stays local-only.
    """
    combined_providers: tuple[str, ...] | None
    if allowed_provider_ids is None and not original.allowed_provider_ids:
        combined_providers = None
    else:
        mine = set(allowed_provider_ids or ())
        theirs = set(original.allowed_provider_ids or ())
        if not mine:
            combined_providers = tuple(theirs) or None
        elif not theirs:
            combined_providers = tuple(mine)
        else:
            combined_providers = tuple(mine & theirs) or ()

    def _intersect_models(
        mine: tuple[tuple[str, str], ...] | None,
        theirs: tuple[tuple[str, str], ...],
    ) -> tuple[tuple[str, str], ...] | None:
        if mine is None and not theirs:
            return None
        if mine is None:
            return theirs or None
        if not theirs:
            return mine
        return tuple(pair for pair in mine if pair in theirs) or ()

    combined_models = _intersect_models(allowed_models, original.allowed_models)
    ceiling = max(float(original.cost_ceiling_usd or 0.0), 0.0)
    if cost_ceiling_usd:
        ceiling = ceiling if ceiling else float(cost_ceiling_usd)
    elif not original.cost_ceiling_usd:
        ceiling = 0.0

    return mint_turn_routing_plan(
        turn_id=turn_id,
        session_id=session_id,
        conversation_ref=conversation_ref or original.conversation_ref,
        manifests=manifests,
        requested_provider=original.requested_provider,
        requested_model=original.requested_model,
        local_only=bool(local_only) or original.locality_ceiling is LocalityCeiling.LOCAL_MACHINE_ONLY,
        allow_paid=bool(allow_paid) and original.paid_allowed,
        allowed_provider_ids=combined_providers,
        allowed_models=combined_models,
        cost_ceiling_usd=ceiling,
        context_identity=context_identity or original.context_identity,
        context_reason=context_reason or original.context_reason,
        applied_rule_ids=original.applied_rule_ids,
        retry_linkage={
            "retry_of_plan_id": original.plan_id,
            "original_request_id": original.original_request_id or original.turn_id,
            "original_user_text_digest": original.original_user_text_digest,
            "generation": original.generation + 1,
        },
        now_unix_ms=now_unix_ms,
    )


# --------------------------------------------------------------------- context


def plan_from_context(source_context: Mapping[str, Any] | None) -> TurnRoutingPlan | None:
    plan = (source_context or {}).get(TURN_ROUTING_PLAN_KEY)
    return plan if isinstance(plan, TurnRoutingPlan) else None


def assert_manifest_within_plan(manifest: Any, *, plan: TurnRoutingPlan | None) -> None:
    """The broker's enforcement read: refuse a manifest the plan does not permit.

    No plan (legacy callers that never minted one) is allowed through — enforcement is
    staged at the routing-owned lanes, and this seam never invents a fence the mint
    did not declare. A plan present but silent about the candidate (never evaluated)
    is a refusal: absence is not consent.
    """
    if plan is None:
        return
    row = plan.permits(str(getattr(manifest, "provider_id", "")), str(getattr(manifest, "model_name", "")))
    if row is None or not row.allowed:
        raise RoutingPlanRefused(
            str(getattr(manifest, "provider_id", "")),
            str(getattr(manifest, "model_name", "")),
            row.reason if row is not None else "not_evaluated_by_plan",
        )


def broker_refusal_for_manifest(manifest: Any, source_context: Mapping[str, Any] | None) -> str | None:
    """The `_invoke_manifest`-shaped read: the typed refusal string, or None to proceed.

    Kept as its own function so the broker seam can return its existing
    ``(None, None, error)`` triple without raising across adapter-building code.
    """
    plan = plan_from_context(source_context)
    if plan is None:
        return None
    try:
        assert_manifest_within_plan(manifest, plan=plan)
    except RoutingPlanRefused as refused:
        return str(refused)
    return None


# ------------------------------------------------------- durable event records


def _events_path() -> Path:
    from core.runtime_paths import active_data_dir

    return active_data_dir() / ROUTING_EVENTS_FILENAME


_EVENTS_LOCK = threading.Lock()
_EVENTS_APPEND_MAX = 4096


def _append_event(row: dict[str, Any]) -> None:
    with _EVENTS_LOCK:
        path = _events_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(row, sort_keys=True, default=str)
        rotate = path.exists() and path.stat().st_size > 4 * 1024 * 1024
        if rotate:
            rotated = path.with_suffix(".jsonl.1")
            with contextlib.suppress(OSError):
                rotated.write_bytes(path.read_bytes())
            path.write_text("", encoding="utf-8")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def _read_events() -> list[dict[str, Any]]:
    path = _events_path()
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for candidate in (path, path.with_suffix(".jsonl.1")):
        if not candidate.exists():
            continue
        try:
            text = candidate.read_text("utf-8", "replace")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        if candidate is path:
            break
    # Rotated file first (older), then current — stable chronological order.
    return rows


def record_routing_provenance(plan: TurnRoutingPlan) -> None:
    """Persist the turn's routing provenance. Durable per turn, never semantic truth."""
    _append_event(
        {
            "event": "plan",
            "recorded_at_unix_ms": _now_ms(),
            "plan_digest": plan.digest(),
            **plan.to_record(),
        }
    )


def provenance_for_turn(turn_id: str) -> dict[str, Any] | None:
    wanted = str(turn_id or "").strip()
    for row in reversed(_read_events()):
        if row.get("event") == "plan" and str(row.get("turn_id")) == wanted:
            return row
    return None


def plans_for_session(session_id: str) -> list[dict[str, Any]]:
    wanted = str(session_id or "").strip()
    return [
        row for row in _read_events()
        if row.get("event") == "plan" and str(row.get("session_id")) == wanted
    ]


def plan_by_id(plan_id: str) -> TurnRoutingPlan | None:
    """Reconstruct a minted plan from its durable record (retry linkage reads this)."""
    wanted = str(plan_id or "").strip()
    for row in reversed(_read_events()):
        if row.get("event") == "plan" and str(row.get("plan_id")) == wanted:
            try:
                return TurnRoutingPlan.from_record(row)
            except (KeyError, ValueError):
                return None
    return None


def record_routing_failure(
    *,
    turn_id: str,
    session_id: str,
    provider_id: str,
    model_id: str,
    kind: str,
    stage: str,
    detail: str = "",
    plan_id: str = "",
    plan_digest: str = "",
    user_text: str = "",
) -> dict[str, Any]:
    """Record one typed failed execution. The five kinds stay DISTINCT here."""
    try:
        typed_kind = RoutingFailureKind(str(kind))
    except ValueError:
        typed_kind = classify_provider_error(kind)
    row = {
        "event": "failure",
        "recorded_at_unix_ms": _now_ms(),
        "turn_id": str(turn_id or ""),
        "session_id": str(session_id or ""),
        "provider_id": str(provider_id or ""),
        "model_id": str(model_id or ""),
        "kind": typed_kind.value,
        "stage": str(stage or "provider_call"),
        "detail": " ".join(str(detail or "").split())[:400],
        "plan_id": str(plan_id or ""),
        "plan_digest": str(plan_digest or ""),
        "user_text": str(user_text or ""),
    }
    _append_event(row)
    return row


def routing_failures_for_session(session_id: str) -> list[dict[str, Any]]:
    wanted = str(session_id or "").strip()
    return [
        row for row in _read_events()
        if row.get("event") == "failure" and str(row.get("session_id")) == wanted
    ]


def latest_routing_failure(
    session_id: str, *, exclude_turn_id: str = ""
) -> dict[str, Any] | None:
    exclude = str(exclude_turn_id or "").strip()
    for row in reversed(routing_failures_for_session(session_id)):
        if exclude and str(row.get("turn_id")) == exclude:
            continue
        return row
    return None


_KIND_OPERATOR_TEXT = {
    RoutingFailureKind.REFUSED: "was refused by routing policy before any provider call",
    RoutingFailureKind.CANCELLED: "was cancelled before it finished",
    RoutingFailureKind.TIMEOUT: "timed out waiting for the provider",
    RoutingFailureKind.UNAVAILABLE: "could not reach the provider",
    RoutingFailureKind.PARTIAL: "answered only partially and was not accepted",
}


def render_routing_failure_explanation(row: Mapping[str, Any]) -> str:
    """The deterministic 'why did that fail?' answer for one typed failed execution."""
    provider = str(row.get("provider_id") or "the provider")
    model = str(row.get("model_id") or "an unnamed model")
    try:
        kind = RoutingFailureKind(str(row.get("kind")))
    except ValueError:
        kind = RoutingFailureKind.UNAVAILABLE
    detail = str(row.get("detail") or "").strip()
    lines = [
        f"The model call for `{provider}/{model}` {_KIND_OPERATOR_TEXT[kind]}.",
        f"- Turn: `{row.get('turn_id') or 'unknown'}`",
        f"- Failure kind: {kind.value}",
    ]
    if detail:
        lines.append(f"- Detail: {detail}")
    return "\n".join(lines)


# ------------------------------------------------------- temporary routing rules


class RuleState(StrEnum):
    ARMED = "ARMED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class RuleDirective(StrEnum):
    LOCAL_ONLY = "LOCAL_ONLY"
    PROVIDER_ONLY = "PROVIDER_ONLY"
    NO_PAID = "NO_PAID"


@dataclass(frozen=True)
class TemporaryRoutingRule:
    rule_id: str
    scope_kind: str  # global | session | chat | project
    scope_id: str
    directive: RuleDirective
    target: str
    turns_remaining: int | None
    expires_at_unix_ms: int | None
    state: RuleState
    created_at_unix_ms: int

    def to_record(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "scope_kind": self.scope_kind,
            "scope_id": self.scope_id,
            "directive": self.directive.value,
            "target": self.target,
            "turns_remaining": self.turns_remaining,
            "expires_at_unix_ms": self.expires_at_unix_ms,
            "state": self.state.value,
            "created_at_unix_ms": self.created_at_unix_ms,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> TemporaryRoutingRule:
        return cls(
            rule_id=str(record.get("rule_id") or f"rule-{uuid.uuid4().hex}"),
            scope_kind=str(record.get("scope_kind") or "global"),
            scope_id=str(record.get("scope_id") or ""),
            directive=RuleDirective(str(record.get("directive") or "LOCAL_ONLY")),
            target=str(record.get("target") or ""),
            turns_remaining=(
                int(record["turns_remaining"])
                if record.get("turns_remaining") is not None
                else None
            ),
            expires_at_unix_ms=(
                int(record["expires_at_unix_ms"])
                if record.get("expires_at_unix_ms") is not None
                else None
            ),
            state=RuleState(str(record.get("state") or "ARMED")),
            created_at_unix_ms=int(record.get("created_at_unix_ms") or 0),
        )


_RULES_LOCK = threading.RLock()
_RULES_CACHE: list[TemporaryRoutingRule] | None = None
_RULES_CACHE_PATH: str | None = None
_RULES_CACHE_MTIME_NS: int | None = None


def _rules_path() -> Path:
    from core.runtime_paths import active_vool_home

    return active_vool_home().joinpath(*RULES_STORE_RELNAME)


def _rules_store_mtime_ns() -> int:
    try:
        return _rules_path().stat().st_mtime_ns
    except OSError:
        return -1


def reload_rules() -> list[TemporaryRoutingRule]:
    """Adopt whatever is on disk — the restart path. Always reads fresh."""
    global _RULES_CACHE, _RULES_CACHE_PATH, _RULES_CACHE_MTIME_NS
    with _RULES_LOCK:
        path = _rules_path()
        _RULES_CACHE_PATH = str(path)
        _RULES_CACHE_MTIME_NS = _rules_store_mtime_ns()
        if not path.exists():
            _RULES_CACHE = []
            return []
        try:
            payload = json.loads(path.read_text("utf-8", "replace"))
            rows = payload.get("rules") if isinstance(payload, dict) else payload
            _RULES_CACHE = [TemporaryRoutingRule.from_record(row) for row in rows or []]
        except (OSError, json.JSONDecodeError, ValueError):
            _RULES_CACHE = []
        return list(_RULES_CACHE)


def _write_rules(rules: list[TemporaryRoutingRule]) -> None:
    global _RULES_CACHE, _RULES_CACHE_PATH, _RULES_CACHE_MTIME_NS
    with _RULES_LOCK:
        path = _rules_path()
        _RULES_CACHE_PATH = str(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema": "vool.turn_routing_rules.v1", "rules": [r.to_record() for r in rules]}
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
        _RULES_CACHE = list(rules)
        _RULES_CACHE_MTIME_NS = _rules_store_mtime_ns()


def _current_rules() -> list[TemporaryRoutingRule]:
    with _RULES_LOCK:
        # A different VOOL_HOME means a different store, and a changed mtime means another
        # process armed/cancelled a rule since this process last looked: the cache must
        # never leak either (process-sticky routing contamination is exactly what this
        # authority exists to prevent).
        if _RULES_CACHE is None or str(_rules_path()) != _RULES_CACHE_PATH:
            return reload_rules()
        if _rules_store_mtime_ns() != _RULES_CACHE_MTIME_NS:
            return reload_rules()
        return list(_RULES_CACHE)


def arm_rule(
    *,
    scope_kind: str,
    scope_id: str = "",
    directive: str,
    target: str = "",
    turns_remaining: int | None = None,
    expires_at_unix_ms: int | None = None,
) -> TemporaryRoutingRule:
    """Arm a temporary rule. At least one expiry dimension (turns or wall clock) is
    required — a temporary rule without an end is a standing policy wearing a disguise."""
    clean_scope = str(scope_kind or "global").strip().lower()
    if clean_scope not in {"global", "session", "chat", "project"}:
        raise ValueError(f"unknown rule scope: {scope_kind}")
    if clean_scope != "global" and not str(scope_id or "").strip():
        raise ValueError(f"scope {clean_scope} requires a scope_id")
    if turns_remaining is None and expires_at_unix_ms is None:
        raise ValueError("a temporary rule must carry turns_remaining or expires_at_unix_ms")
    if turns_remaining is not None and int(turns_remaining) < 1:
        raise ValueError("turns_remaining must be at least 1")
    try:
        typed_directive = RuleDirective(str(directive).strip().upper())
    except ValueError as exc:
        raise ValueError(f"unknown rule directive: {directive}") from exc
    rule = TemporaryRoutingRule(
        rule_id=f"rr-{uuid.uuid4().hex[:12]}",
        scope_kind=clean_scope,
        scope_id=str(scope_id or "").strip(),
        directive=typed_directive,
        target=str(target or "").strip(),
        turns_remaining=(int(turns_remaining) if turns_remaining is not None else None),
        expires_at_unix_ms=(int(expires_at_unix_ms) if expires_at_unix_ms is not None else None),
        state=RuleState.ARMED,
        created_at_unix_ms=_now_ms(),
    )
    with _RULES_LOCK:
        _write_rules([*_current_rules_uncached(), rule])
    return rule


def _current_rules_uncached() -> list[TemporaryRoutingRule]:
    global _RULES_CACHE
    if _RULES_CACHE is None or str(_rules_path()) != _RULES_CACHE_PATH or _rules_store_mtime_ns() != _RULES_CACHE_MTIME_NS:
        reload_rules()
    assert _RULES_CACHE is not None
    return list(_RULES_CACHE)


def cancel_rule(rule_id: str) -> bool:
    wanted = str(rule_id or "").strip()
    with _RULES_LOCK:
        rules = _current_rules_uncached()
        remaining: list[TemporaryRoutingRule] = []
        cancelled = False
        for rule in rules:
            if rule.rule_id == wanted and rule.state is RuleState.ARMED:
                cancelled = True  # dropped from the store: cancelled rules cannot re-arm
                continue
            remaining.append(rule)
        if cancelled:
            _write_rules(remaining)
    return cancelled


def _rule_in_scope(rule: TemporaryRoutingRule, *, session_id: str, conversation_ref: str,
                   project_ref: str) -> bool:
    if rule.scope_kind == "global":
        return True
    if rule.scope_kind == "session":
        return bool(session_id) and rule.scope_id == session_id
    if rule.scope_kind == "chat":
        return bool(conversation_ref) and rule.scope_id == conversation_ref
    if rule.scope_kind == "project":
        return bool(project_ref) and rule.scope_id == project_ref
    return False


def _live_rules_for(*, session_id: str, conversation_ref: str, project_ref: str,
                    now_unix_ms: int) -> list[TemporaryRoutingRule]:
    live: list[TemporaryRoutingRule] = []
    expired_ids: list[str] = []
    for rule in _current_rules():
        if rule.state is not RuleState.ARMED:
            continue
        if rule.expires_at_unix_ms is not None and rule.expires_at_unix_ms <= now_unix_ms:
            expired_ids.append(rule.rule_id)
            continue
        if rule.turns_remaining is not None and rule.turns_remaining <= 0:
            expired_ids.append(rule.rule_id)
            continue
        if _rule_in_scope(rule, session_id=session_id, conversation_ref=conversation_ref,
                          project_ref=project_ref):
            live.append(rule)
    if expired_ids:
        with _RULES_LOCK:
            rules = [
                rule if rule.rule_id not in expired_ids
                else TemporaryRoutingRule(**{**rule.__dict__, "state": RuleState.EXPIRED})
                for rule in _current_rules_uncached()
            ]
            _write_rules([r for r in rules if r.state is not RuleState.EXPIRED])
    return live


def active_rules_for(*, session_id: str, conversation_ref: str = "", project_ref: str = "",
                     now_unix_ms: int | None = None) -> list[TemporaryRoutingRule]:
    """Read-only: the armed, unexpired rules this turn's scope matches."""
    return _live_rules_for(
        session_id=session_id,
        conversation_ref=conversation_ref,
        project_ref=project_ref,
        now_unix_ms=now_unix_ms if now_unix_ms is not None else _now_ms(),
    )


def consume_rules_for_mint(*, session_id: str, conversation_ref: str = "",
                           project_ref: str = "", now_unix_ms: int | None = None) -> list[TemporaryRoutingRule]:
    """Consume ONE turn's worth of the matching rules, exactly once per call.

    A rule with ``turns_remaining`` decrements here and disappears at zero; a rule with
    only a wall-clock expiry stays armed until it lapses. This is the ONLY decrement
    seam: per-turn, not per provider call, so a ladder of three candidates spends one
    turn of the budget, not three.
    """
    now = now_unix_ms if now_unix_ms is not None else _now_ms()
    consumed = _live_rules_for(
        session_id=session_id,
        conversation_ref=conversation_ref,
        project_ref=project_ref,
        now_unix_ms=now,
    )
    if not consumed:
        return []
    consumed_ids = {rule.rule_id for rule in consumed}
    with _RULES_LOCK:
        remaining: list[TemporaryRoutingRule] = []
        for rule in _current_rules_uncached():
            if rule.rule_id not in consumed_ids:
                remaining.append(rule)
                continue
            if rule.turns_remaining is None:
                remaining.append(rule)  # wall-clock rule: still armed
                continue
            left = int(rule.turns_remaining) - 1
            if left > 0:
                remaining.append(TemporaryRoutingRule(**{**rule.__dict__, "turns_remaining": left}))
            # left == 0: the budget is spent — the rule is gone.
        _write_rules(remaining)
    return consumed


def routing_fences_from_rules(
    rules: list[TemporaryRoutingRule],
    *,
    local_only: bool,
    allow_paid: bool,
) -> dict[str, Any]:
    """Fold consumed rules into the mint's fences. Rules may only NARROW a turn.

    A PROVIDER_ONLY target is ``provider`` or ``provider/model``; the model half, when
    present, becomes an allowed-models pair so the fence binds the exact manifest.
    """
    fences: dict[str, Any] = {"local_only": bool(local_only), "allow_paid": bool(allow_paid)}
    providers: set[str] | None = None
    models: set[tuple[str, str]] | None = None
    for rule in rules:
        if rule.directive is RuleDirective.LOCAL_ONLY:
            fences["local_only"] = True
        elif rule.directive is RuleDirective.NO_PAID:
            fences["allow_paid"] = False
        elif rule.directive is RuleDirective.PROVIDER_ONLY and rule.target:
            provider_part, _, model_part = rule.target.partition("/")
            provider_part = provider_part.strip()
            model_part = model_part.strip()
            if not provider_part:
                continue
            providers = {provider_part} if providers is None else (providers & {provider_part})
            if model_part:
                pair = (provider_part, model_part)
                models = {pair} if models is None else (models & {pair})
    if providers is not None:
        fences["allowed_provider_ids"] = tuple(sorted(providers))
    if models is not None:
        fences["allowed_models"] = tuple(sorted(models))
    return fences


def reset_for_tests() -> None:
    global _RULES_CACHE
    with _RULES_LOCK:
        _RULES_CACHE = None


__all__ = [
    "ROUTING_EVENTS_FILENAME",
    "RULES_STORE_RELNAME",
    "TURN_ROUTING_PLAN_KEY",
    "TURN_ROUTING_RETRY_KEY",
    "CandidateEligibility",
    "LocalityCeiling",
    "PinKind",
    "PlanCostClass",
    "PrivacyCeiling",
    "RetryPolicy",
    "RoutingFailureKind",
    "RoutingIdentityError",
    "RoutingPlanRefused",
    "RuleDirective",
    "RuleState",
    "TemporaryRoutingRule",
    "TurnRoutingPlan",
    "active_rules_for",
    "arm_rule",
    "assert_manifest_within_plan",
    "broker_refusal_for_manifest",
    "cancel_rule",
    "classify_provider_error",
    "consume_rules_for_mint",
    "latest_routing_failure",
    "mint_retry_plan",
    "mint_turn_routing_plan",
    "plan_by_id",
    "plan_from_context",
    "plans_for_session",
    "provenance_for_turn",
    "record_routing_failure",
    "record_routing_provenance",
    "reload_rules",
    "render_routing_failure_explanation",
    "reset_for_tests",
    "routing_failures_for_session",
    "routing_fences_from_rules",
]
