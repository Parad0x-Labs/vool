"""Wire `turn_planner` to a real model and to the runtime's own turn execution.

Kept out of `turn_planner.py` so the planner's logic stays pure and testable without a runtime, and
out of `apps/vool_agent.py` so the turn path gains a call rather than a mechanism.

Two decisions are enforced here rather than left to the caller:

**The planner never reaches a paid model.** It runs before the runtime knows whether a plan will be
useful, so a paid planner would charge merely to classify a turn.  `_unpaid_manifests` remains its
only candidate path.

**A reasoning node may honor an explicit paid pin.** The pin is the owner's approval for this turn,
but not a reusable permission.  Each answer-producing node gets a fresh server-side reservation,
the node scope is capped across the whole turn, and it never falls through to another manifest.
Auto and an unreserved pin remain unpaid-only.

**A planner failure is never a turn failure.** Everything here is wrapped, and `plan_turn` treats any
exception as "one request". The worst outcome of this whole module misbehaving is the single-request
behaviour the runtime already had.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from threading import Lock
from typing import Any

from core import runtime_active_clock

# Minimum capacity for short routing artifacts. Longer requests need room for the
# user text the schema requires the planner to preserve, not a fixed 192-token cap.
_PLANNER_MAX_OUTPUT_TOKENS = 192
# One single-attempt call. Sized above what the call has been OBSERVED to need, never as a
# guess (CLAUDE.md 4b): the clause split is 1.3-1.4 s on a warm Ollama runner, ~6 s with a cold
# prompt cache, and 20.3 s when the author's runner itself is cold (qwen3:8b, 14.0 s load;
# measured 2026-09-10). 15 s failed exactly the cold-runner case -- 14.1 / 13.8 / 15.0 s on the
# frozen build's qwen2.5:7b, 15.0 s (deadline) on 6249a0d9's qwen3:8b -- and each failure cost
# the whole conductor turn. The planner phase cap (scheduler.PLANNER_PHASE_CAP_S) still bounds
# the phase; this ceiling only stops the runtime from abandoning a call that is about to answer.
_PLANNER_TIMEOUT_SECONDS = 25.0
_SHARED_PLANNER_ARTIFACT_KEY = "_shared_preclassification_planner_artifact"

# Exactly one answer-producing reasoning node may spend.  A model-written plan can name up to eight
# clauses, but selecting a paid model must not turn that shape ceiling into eight implicit charges.
# One is deliberately a HARD maximum even if a future policy value is larger.  The ordinary
# answer lane has its own one-call reservation, so a conductor that declines still cannot reuse a
# helper reservation for the eventual answer.
_PINNED_PAID_HELPER_MAX_CALLS = 1
_PAID_HELPER_RECEIPTS_KEY = "pinned_paid_helper_receipts"


class PlannerCallKind(str, Enum):
    """Which pre-classification question a model is being asked.

    A cache identity that does not include the CONTRACT is not an identity. Both of these calls used
    to share one unkeyed record, so the second one -- whichever it was -- received the first one's
    answer: measured at bb14a695 as one provider invocation and a byte-identical reply, which made
    the entire semantic proposer dead code while 134 tests passed against a stand-in.

    Each member owns a distinct system prompt, a distinct provider schema, a distinct parser and a
    distinct cache slot. A reply to one may not validate as the other, which is what stops the two
    from aliasing again by accident rather than by convention.
    """

    CLAUSE_DECOMPOSITION = "clause_decomposition"
    SEMANTIC_PROOF = "semantic_proof"


@dataclass
class _CallRecord:
    attempted: bool = False
    raw: str = ""
    #: Whether the invocation RAISED, as opposed to answering with nothing. Both are cached and both
    #: return "", so the difference changes no behaviour -- it exists so a fault is a stateable fact
    #: rather than something inferred from an empty string, and so a test can assert that a faulting
    #: slot holds its OWN failure instead of another slot's answer.
    failed: bool = False


@dataclass
class SharedPlannerArtifact:
    """One authoritative auxiliary result PER CALL KIND, for ONE user turn.

    The object is installed in the server-owned source context before the conductor binds its
    shorter deadline. Context copies retain this object's identity, so the conductor and generic
    planner cannot independently call providers for the same question. Empty is a cached result too:
    a failed planner falls through to the ordinary lane instead of being retried under another
    prompt and another timeout.

    Identity is `(turn, call kind)`, and it took two passes to get both halves. The first version
    keyed on NEITHER, so the second call of a turn received the first one's reply. The second keyed
    on the kind alone -- which fixed the aliasing inside one turn and left the sentence "for one
    user turn" as an aspiration, because nothing in the identity said which turn:

        `apps/vool_agent.py` keeps the caller's `source_context` BY REFERENCE and installs this
        object on it, and `apps/vool_chat.py` builds one such dict outside its input loop. Measured
        live on qwen2.5:7b through `run_once`: three turns, three distinct `turn_id`s, ONE artifact,
        and turns 2 and 3 made zero provider calls because they were handed turn 1's clause array
        and turn 1's semantic proposal. The checkpoint front door does clear the caller's dict, but
        it repopulates it from a copy that carried this key forward, so the clear bought nothing.

    `turn_id` is therefore carried here and `ensure_shared_planner_artifact` replaces the object
    when the incoming turn differs. Sharing WITHIN a turn is the point and is untouched.
    """

    #: The turn this artifact belongs to, from `source_context["turn_id"]` -- server-owned, one
    #: fresh uuid per user message (`core/agent_runtime/checkpoints.py`). Empty means the caller
    #: had no turn identity to give, which is a normal state for a library caller and a test.
    turn_id: str = ""
    _lock: Lock = field(default_factory=Lock, repr=False)
    _records: dict[str, _CallRecord] = field(default_factory=dict, repr=False)

    def resolve(
        self,
        invoke: Callable[[], str],
        *,
        kind: PlannerCallKind = PlannerCallKind.CLAUSE_DECOMPOSITION,
    ) -> str:
        key = PlannerCallKind(kind).value
        with self._lock:
            record = self._records.get(key)
            if record is not None and record.attempted:
                return record.raw
            # Installed BEFORE the call and only ever written from its own `invoke`. A slot holds
            # its own answer or its own failure; there is no branch on which it can be assigned
            # from another slot, which is what stops a faulting semantic call from inheriting the
            # decomposition reply that happens to be sitting beside it.
            record = _CallRecord(attempted=True)
            self._records[key] = record
            try:
                record.raw = str(invoke() or "")
            except Exception:
                record.raw = ""
                record.failed = True
            return record.raw

    def attempted_kinds(self) -> frozenset[str]:
        """Which questions have been asked this turn. For receipts and for tests."""
        with self._lock:
            return frozenset(key for key, record in self._records.items() if record.attempted)

    def failed_kinds(self) -> frozenset[str]:
        """Which questions were asked and RAISED. For receipts and for tests."""
        with self._lock:
            return frozenset(key for key, record in self._records.items() if record.failed)


def _context_turn_id(source_context: dict[str, Any] | None) -> str:
    """This turn's server-owned identity, or empty when the caller has none.

    Read from state, never derived. Two identical messages are two turns, so the text cannot be the
    identity; the context dict is mutated in place across turns, so its object identity cannot be
    either; and a timestamp says when rather than which.
    """
    if not isinstance(source_context, dict):
        return ""
    return str(source_context.get("turn_id") or "").strip()


#: Turn-scoped artifact registry. `source_context` copies (bind_provider_deadline and friends)
#: each install their own artifact object, so two consumers of the SAME turn could observe two
#: artifacts and pay TWO generations of the same classification question (measured: conductor's
#: clause ask and the generic planner's ask on one turn). This registry is the cross-copy view:
#: one artifact per turn_id, bounded to the most recent turns of this process.
_ARTIFACTS_BY_TURN: dict[str, SharedPlannerArtifact] = {}
_ARTIFACT_REGISTRY_MAX = 64


def ensure_shared_planner_artifact(
    source_context: dict[str, Any] | None,
) -> SharedPlannerArtifact:
    """Return THIS turn's planner artifact, installing it in trusted context when possible.

    An artifact stamped with a different turn is replaced rather than reused. Absent identity on
    both sides keeps the previous behaviour exactly -- the conductor and the generic planner below
    it hand in different copies of one dict and must still observe one object, which is the whole
    reason the memoization exists.
    """

    turn_id = _context_turn_id(source_context)
    existing = (
        source_context.get(_SHARED_PLANNER_ARTIFACT_KEY)
        if isinstance(source_context, dict)
        else None
    )
    if isinstance(existing, SharedPlannerArtifact) and existing.turn_id == turn_id:
        return existing
    if turn_id:
        by_turn = _ARTIFACTS_BY_TURN.get(turn_id)
        if isinstance(by_turn, SharedPlannerArtifact) and by_turn.turn_id == turn_id:
            # Re-install on THIS context copy so every consumer of the turn shares one
            # classification. Read-only consumers; the artifact's per-kind slots decide reuse.
            if isinstance(source_context, dict):
                source_context[_SHARED_PLANNER_ARTIFACT_KEY] = by_turn
            return by_turn
    artifact = SharedPlannerArtifact(turn_id=turn_id)
    if isinstance(source_context, dict):
        source_context[_SHARED_PLANNER_ARTIFACT_KEY] = artifact
    if turn_id:
        if len(_ARTIFACTS_BY_TURN) >= _ARTIFACT_REGISTRY_MAX:
            _ARTIFACTS_BY_TURN.clear()  # bounded: stale turns are never valid to reuse anyway
        _ARTIFACTS_BY_TURN[turn_id] = artifact
    return artifact


def _strict_json_array(text: str) -> str:
    """Decode the planner envelope or legacy array; refuse other artifact contracts.

    The CLAUSE parser. A semantic-proof object is not a clause array and must come back empty here
    -- that refusal is half of what stops the two contracts aliasing.
    """

    body = str(text or "").strip()
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return ""
    if isinstance(payload, dict):
        if set(payload) != {"requests"} or not isinstance(payload["requests"], list):
            return ""
        payload = payload["requests"]
        return json.dumps(payload, ensure_ascii=True) if payload else ""
    return body if isinstance(payload, list) and payload else ""


def _strict_semantic_proposal(text: str) -> str:
    """Return exact semantic-proposal object text, or empty for anything else.

    The SEMANTIC parser, and the mirror of the rule above: a clause array is not a proposal. The
    `frames` key is required and must be a list, so a reply that happens to be a JSON object of some
    other shape is refused rather than handed to `parse_proposal` to puzzle over.
    """

    body = str(text or "").strip()
    if not body.startswith("{") or not body.endswith("}"):
        return ""
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return ""
    if not isinstance(payload, dict) or not isinstance(payload.get("frames"), list):
        return ""
    return body


def _planner_json_schema() -> dict[str, Any]:
    """Provider-native structured shape shared by conductor and generic split consumers."""

    return {
        "type": "array",
        "minItems": 1,
        "maxItems": 8,
        "items": {
            "type": "object",
            "properties": {
                "request": {"type": "string"},
                "operation": {"type": "string"},
                "depends_on": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 0},
                },
            },
            "required": ["request", "operation", "depends_on"],
            "additionalProperties": False,
        },
    }


def _planner_wire_schema(array_schema: dict[str, Any]) -> dict[str, Any]:
    """Native structured providers require an object root, not the internal clause array."""
    return {"type": "object", "properties": {"requests": array_schema},
            "required": ["requests"], "additionalProperties": False}


def _semantic_proposal_json_schema() -> dict[str, Any]:
    """Provider-native shape for a bounded semantic proposal. NOT the clause array.

    Every value the model supplies here is a POINTER: a substring it copied out of the message, or
    the polarity it read. There is no field it could write an operation parameter into, which is
    why a model proposal can be validated rather than trusted.

    `group` and `ordinal` are accepted and ignored by production -- see `parse_proposal`. They stay
    in the schema because a model that has been told to emit them produces better-structured role
    lists, and because refusing a reply for carrying them would be the ceremony this contract exists
    to stop requiring.
    """

    return {
        "type": "object",
        "properties": {
            "frames": {
                "type": "array",
                "maxItems": 8,
                "items": {
                    "type": "object",
                    "properties": {
                        "frame_id": {"type": "string"},
                        "family": {"type": "string"},
                        "scope": {"type": "string"},
                        "predicate": {"type": "string"},
                        "polarity": {
                            "type": "string",
                            "enum": ["affirmed", "negated", "unresolved"],
                        },
                        "roles": {
                            "type": "array",
                            "maxItems": 8,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "role": {"type": "string"},
                                    "text": {"type": "string"},
                                },
                                "required": ["role", "text"],
                                "additionalProperties": True,
                            },
                        },
                    },
                    "required": ["family", "scope", "predicate", "polarity", "roles"],
                    "additionalProperties": True,
                },
            }
        },
        "required": ["frames"],
        "additionalProperties": False,
    }


#: A semantic proposal is longer than a clause split: eight frames with role lists, in JSON. Sized
#: against WALL CLOCK rather than spend (CLAUDE.md 4b) -- at roughly 30 tok/s on the local lane a
#: 640-token ceiling is ~21s, which is why the timeout below is 25 and not the clause split's 15.
#: A truncated proposal loses every frame after the cut, and the input is spent either way.
_SEMANTIC_PROOF_MAX_OUTPUT_TOKENS = 640
_SEMANTIC_PROOF_TIMEOUT_SECONDS = 25.0


@dataclass(frozen=True)
class _PaidHelperTask:
    """The identity and size the paid gate binds to one helper request."""

    task_id: str
    task_summary: str
    prompt_tokens: int
    max_output_tokens: int


class PinnedPaidTurnScope:
    """A bounded, exact-model authority for paid conductor generation in one turn.

    The object is built in trusted runtime code, never read from ``source_context``, and shared by
    all conductor-node closures.  Claiming a slot is atomic because nodes may run on worker
    threads.  Every claimed slot receives a distinct spend reservation; there is no reusable
    blanket authorization.  The planner never receives this object.
    """

    def __init__(
        self,
        agent: Any,
        *,
        manifest: Any,
        source_context: dict[str, Any] | None,
        max_calls: int = _PINNED_PAID_HELPER_MAX_CALLS,
    ) -> None:
        self._agent = agent
        self.manifest = manifest
        self._source_context = source_context if isinstance(source_context, dict) else {}
        self.max_calls = max(1, min(int(max_calls), _PINNED_PAID_HELPER_MAX_CALLS))
        self._lock = Lock()
        self._claimed = 0
        turn_identity = next(
            (
                str(self._source_context.get(key) or "").strip()
                for key in ("task_id", "request_id", "cancel_turn_id", "turn_id")
                if str(self._source_context.get(key) or "").strip()
            ),
            "",
        )
        self.task_id = turn_identity or f"paid-helper-turn-{uuid.uuid4().hex}"

    def matches(self, manifest: Any) -> bool:
        return bool(
            manifest is not None
            and str(getattr(manifest, "provider_id", "") or "")
            == str(getattr(self.manifest, "provider_id", "") or "")
            and str(getattr(manifest, "model_name", "") or "")
            == str(getattr(self.manifest, "model_name", "") or "")
        )

    def _emit_receipt_event(self, event_type: str, receipt: dict[str, Any]) -> None:
        emitter = getattr(self._agent, "_emit_runtime_event", None)
        if not callable(emitter):
            return
        # A telemetry sink must never turn a refusal into permission or break the turn.
        with suppress(Exception):
            emitter(
                self._source_context,
                event_type=event_type,
                message=(
                    f"Pinned paid {receipt.get('call_role')}: {receipt.get('result')} "
                    f"({receipt.get('call')}/{self.max_calls})."
                ),
                # Top-level fields make the live gauntlet able to join reserve -> provider call ->
                # terminal outcome without interpreting prose or opening the spend database.
                call_role=str(receipt.get("call_role") or ""),
                provider_id=str(receipt.get("provider_id") or ""),
                model_id=str(receipt.get("model_id") or ""),
                model_call_id=str(receipt.get("model_call_id") or ""),
                reservation_id=str(receipt.get("reservation_id") or ""),
                reserved_usd=float(receipt.get("reserved_usd") or 0.0),
                actual_usd=float(receipt.get("actual_usd") or 0.0),
                per_call_cap_usd=float(receipt.get("per_call_cap_usd") or 0.0),
                daily_call_count=int(receipt.get("daily_call_count") or 0),
                daily_call_cap=receipt.get("daily_call_cap"),
                paid_helper_call=int(receipt.get("call") or 0),
                paid_helper_call_cap=self.max_calls,
                spend_result=str(receipt.get("result") or ""),
                receipt=dict(receipt),
            )

    def _receipt(self, **details: Any) -> dict[str, Any]:
        receipt = {
            "scope": "explicit_pinned_paid_turn_helper",
            "provider_id": str(getattr(self.manifest, "provider_id", "") or ""),
            "model_id": str(getattr(self.manifest, "model_name", "") or ""),
            "max_calls": self.max_calls,
            **details,
        }
        receipts = self._source_context.setdefault(_PAID_HELPER_RECEIPTS_KEY, [])
        if isinstance(receipts, list):
            receipts.append(receipt)
        event_type = (
            "paid_call.reserved"
            if str(receipt.get("result") or "") == "reserved"
            else "paid_call.refused"
        )
        self._emit_receipt_event(event_type, receipt)
        return receipt

    @staticmethod
    def _reservation_fields(authorization: Any) -> dict[str, Any]:
        from core.model_spend_ledger import calls_today
        from core.paid_call_reservation import spend_limits

        reservation = getattr(authorization, "reservation", None)
        try:
            limits = spend_limits()
        except Exception:
            limits = None
        try:
            daily_count = calls_today()
        except Exception:
            daily_count = 0
        return {
            "reservation_id": str(getattr(reservation, "reservation_id", "") or ""),
            "reserved_usd": float(getattr(reservation, "reserved_usd", 0.0) or 0.0),
            "per_call_cap_usd": float(getattr(limits, "per_call_usd", 0.0) or 0.0),
            "daily_call_count": int(daily_count),
            "daily_call_cap": getattr(limits, "daily_call_cap", None),
        }

    def reserve(
        self,
        *,
        manifest: Any,
        request: Any,
        purpose: str,
    ) -> tuple[_PaidHelperTask | None, Any | None, dict[str, Any] | None]:
        if not self.matches(manifest):
            self._receipt(call_role=purpose, call=0, result="refused_model_mismatch")
            return None, None, None
        with self._lock:
            if self._claimed >= self.max_calls:
                self._receipt(
                    call_role=purpose,
                    call=self._claimed + 1,
                    result="refused_turn_call_cap",
                )
                return None, None, None
            self._claimed += 1
            call_number = self._claimed

        prompt_chars = len(str(getattr(request, "system_prompt", "") or "")) + len(
            str(getattr(request, "prompt", "") or "")
        )
        task = _PaidHelperTask(
            task_id=self.task_id,
            task_summary=f"bounded {purpose} for an explicit pinned paid turn",
            prompt_tokens=max(1, (prompt_chars + 3) // 4),
            max_output_tokens=max(1, int(getattr(request, "max_output_tokens", 0) or 1)),
        )
        from core.paid_call_reservation import reserve_owner_pick_paid_call

        authorization = reserve_owner_pick_paid_call(
            manifest=manifest,
            task=task,
            source_context=self._source_context,
            task_kind="normalization_assist",
        )
        if authorization is None:
            self._receipt(
                call_role=purpose,
                call=call_number,
                result="refused_spend_reservation",
            )
            return None, None, None
        receipt = self._receipt(
            call_role=purpose,
            call=call_number,
            result="reserved",
            model_call_id=str(getattr(authorization, "model_call_id", "") or ""),
            **self._reservation_fields(authorization),
        )
        return task, authorization, receipt

    def finish(self, receipt: dict[str, Any] | None, *, result: str) -> None:
        if receipt is None:
            return
        receipt["result"] = str(result or "")
        try:
            from core.model_spend_ledger import get_spend_reservation

            terminal = get_spend_reservation(str(receipt.get("model_call_id") or ""))
        except Exception:
            terminal = None
        receipt["actual_usd"] = float(getattr(terminal, "actual_usd", 0.0) or 0.0)
        if terminal is not None:
            receipt["spend_status"] = str(getattr(terminal, "status", "") or "")
        spend_status = str(receipt.get("spend_status") or "")
        if result == "completed":
            event_type = (
                "paid_call.settled"
                if spend_status in {"settled", "cap_breached", "billing_ambiguous"}
                else "paid_call.settlement_failed"
            )
        else:
            event_type = (
                "paid_call.released"
                if spend_status and spend_status != "reserved"
                else "paid_call.release_failed"
            )
        self._emit_receipt_event(event_type, receipt)

    def call_context(self, authorization: Any) -> dict[str, Any]:
        # The reservation exists only on this private per-call copy.  It is never written into the
        # inbound/request context and cannot be reused by another helper call or the answer lane.
        return {
            **self._source_context,
            "authorized_paid_call": authorization,
            "model_call_role": "conductor_generation",
        }


def build_pinned_paid_turn_scope(
    agent: Any,
    source_context: dict[str, Any] | None,
) -> PinnedPaidTurnScope | None:
    """Return a paid-helper scope only for an exact owner-local manual paid pin."""
    from core.agent_runtime.audit_routing import MANUAL, resolve_routing_mode, select_audit_manifests
    from core.model_selection_policy import is_verified_free_cloud_manifest, provider_cost_class
    from core.request_trust import request_is_owner_local

    routing = resolve_routing_mode(source_context)
    if routing.mode != MANUAL or not request_is_owner_local(source_context):
        return None
    manifests, _reason = select_audit_manifests(agent, source_context, routing)
    if len(manifests) != 1:
        return None
    manifest = manifests[0]
    if provider_cost_class(manifest) != "paid_cloud" or is_verified_free_cloud_manifest(manifest):
        return None
    return PinnedPaidTurnScope(agent, manifest=manifest, source_context=source_context)


def _unpaid_manifests(manifests: list[Any]) -> list[Any]:
    """Local and verified-free-cloud manifests only, order preserved."""
    from core.model_selection_policy import is_verified_free_cloud_manifest, provider_cost_class

    kept: list[Any] = []
    for manifest in list(manifests or []):
        try:
            if provider_cost_class(manifest) != "paid_cloud" or is_verified_free_cloud_manifest(manifest):
                kept.append(manifest)
        except Exception:
            # Cost class could not be established -- exclude it. An unknown manifest is not worth
            # the risk of an unapproved paid call on a per-turn code path.
            continue
    return kept


@lru_cache(maxsize=1)
def _planner_default_model_tag() -> str | None:
    """Host-fitted daily baseline, resolved once for the same process-wide inventory."""

    try:
        from core.runtime_provider_defaults import default_runtime_model_tag

        return str(default_runtime_model_tag() or "").strip() or None
    except Exception:
        return None


def _prioritize_planner_residency(manifests: list[Any]) -> list[Any]:
    """Reuse daily Auto scoring so the helper does not evict the model that must answer."""

    candidates = list(manifests or [])
    if len(candidates) < 2:
        return candidates
    try:
        from core.local_inference_autopilot import prioritize_daily_residency_capabilities
        from core.local_inference_evidence import hydrate_capability_truth_with_benchmarks
        from core.provider_routing import provider_capability_truth_for_manifest

        truth = hydrate_capability_truth_with_benchmarks(
            tuple(provider_capability_truth_for_manifest(item) for item in candidates)
        )
        prioritized = prioritize_daily_residency_capabilities(
            truth,
            default_model_tag=_planner_default_model_tag(),
        )
    except Exception:
        return candidates
    order = {item.provider_id: index for index, item in enumerate(prioritized)}
    return sorted(
        candidates,
        key=lambda item: order.get(str(getattr(item, "provider_id", "") or ""), len(order)),
    )


def _prefer_final_answer_authors(manifests: list[Any], *, request_text: str) -> list[Any]:
    """Stable partition: policy-eligible final-answer authors first, everything else after.

    The eligibility question is `core.final_answer_authorship.decide_final_answer_author`
    with escalation off -- the pre-call form the conductor's generation seam already uses.
    A candidate whose check raises is treated as not eligible (never as a reason to drop it),
    and an order with no eligible candidate is returned unchanged, so a home without a
    certified author keeps the residency ranking it had.
    """

    candidates = list(manifests or [])
    if len(candidates) < 2:
        return candidates
    try:
        from core.final_answer_authorship import (
            FINAL_ANSWER_ROLE,
            decide_final_answer_author,
        )
    except Exception:
        return candidates
    eligible: list[Any] = []
    others: list[Any] = []
    for manifest in candidates:
        try:
            verdict = decide_final_answer_author(
                request_text=str(request_text or ""),
                author_role=FINAL_ANSWER_ROLE,
                requested_manifest=manifest,
                requested_model=str(getattr(manifest, "provider_id", "") or ""),
                allow_escalation=False,
            )
            is_eligible = getattr(verdict, "eligible", None) is True
        except Exception:
            is_eligible = False
        (eligible if is_eligible else others).append(manifest)
    if not eligible:
        return candidates
    return eligible + others


def _clause_output_capacity(prompt: str, schema: dict[str, Any]) -> int:
    """Size a legal split with shared context repeated in every supported clause."""
    from math import ceil

    from core.prompt_budget import estimate_text_tokens

    count = int(schema["maxItems"])
    artifact = [
        {"request": prompt, "operation": "quantitative_reasoning", "depends_on": list(range(index))}
        for index in range(count)
    ]
    return max(_PLANNER_MAX_OUTPUT_TOKENS, ceil(estimate_text_tokens(json.dumps(artifact))))


def _build_auxiliary_ask_model(
    agent: Any,
    source_context: dict[str, Any] | None,
    *,
    kind: PlannerCallKind,
    json_schema: dict[str, Any],
    parse: Callable[[str], str],
    max_output_tokens: int,
    timeout_seconds: float,
) -> Callable[[str, str], str]:
    """One single-attempt auxiliary call, bound to ONE contract.

    Everything that distinguishes the two pre-classification questions is a parameter here: the
    cache slot, the provider schema, the parser and the budget. They were shared, which is how a
    reply to one question came back as the answer to the other.
    """

    artifact = ensure_shared_planner_artifact(source_context)

    def _ask(system_prompt: str, prompt: str) -> str:
        def _invoke_once() -> str:
            from adapters.base_adapter import ModelRequest
            from core.agent_runtime.audit_routing import (
                resolve_routing_mode,
                select_audit_manifests,
            )
            from core.provider_call_deadline import bind_provider_deadline

            routing = resolve_routing_mode(source_context)
            manifests, _reason = select_audit_manifests(agent, source_context, routing)
            candidates = _unpaid_manifests(manifests)
            # Manual means exact-model authority. Only Auto/Local Only may align an auxiliary call
            # to the reliable daily resident; reordering a manual pin would be silent substitution.
            if not bool(getattr(routing, "pinned", False)):
                candidates = _prioritize_planner_residency(candidates)
                # THE AUTHOR THE TURN WILL USE COMES FIRST. Residency alignment above picks the
                # DAILY default, but who answers is the authorship policy's decision, and on a
                # runtime whose daily default is not certified the policy skips it on every
                # generation. Measured on the s50 rig (3e1bf797, three fresh sessions of the
                # verbatim three-clause turn): the clause split ran on qwen2.5:7b for 14.1 s,
                # 13.8 s and 15.0 s against its 15 s single-attempt budget -- the third crossed
                # it, the conductor declined, and the plain lane refused the WHOLE turn while
                # the certified author (qwen3:8b), resident because the nodes use it, sat idle.
                # The same pre-call question `build_conductor_ask_model` asks is asked here, so
                # planner and nodes share one resident author; nothing is removed -- when no
                # candidate is eligible the residency order stands exactly as before.
                candidates = _prefer_final_answer_authors(candidates, request_text=prompt)
            if not candidates:
                return ""
            manifest = candidates[0]
            output_capacity = max_output_tokens
            request_schema = json_schema
            request_system = system_prompt
            if kind == PlannerCallKind.CLAUSE_DECOMPOSITION:
                from core.agent_runtime.planner_sources import source_contract
                from core.output_budget_policy import LaneCapability, OutputBudgetIntent, resolve_output_budget

                request_schema, source_instructions = source_contract(prompt, json_schema)
                request_schema = _planner_wire_schema(request_schema)
                request_system += source_instructions + (
                    '\nWire format: return ONLY {"requests": [...]} with the plan entries inside '
                    'requests. This object envelope replaces the top-level array instruction.\n'
                )
                output_capacity = _clause_output_capacity(prompt, json_schema)
                config = {**dict(getattr(manifest, "metadata", {}) or {}),
                          **dict(getattr(manifest, "runtime_config", {}) or {})}
                limits = {}
                for key in ("context_window", "max_output_tokens"):
                    try:
                        limits[key] = max(0, int(config.get(key) or 0))
                    except (TypeError, ValueError):
                        limits[key] = 0
                output_capacity = resolve_output_budget(
                    OutputBudgetIntent(output_mode="json_object", base_tokens=output_capacity,
                                       ceiling=output_capacity, reason="clause_artifact_capacity"),
                    LaneCapability(**limits),
                ).tokens
            request = ModelRequest(
                task_kind="normalization_assist",
                prompt=prompt,
                system_prompt=request_system,
                temperature=0.0,
                max_output_tokens=output_capacity,
                output_mode="json_object",
                reasoning_mode="disabled",
                contract={"json_schema": request_schema},
                metadata={
                    "turn_planner": True,
                    "auxiliary_call_cap": 1,
                    "planner_call_kind": kind.value,
                },
                allow_response_control_retry=False,
                allow_provider_retry=False,
            )
            # A helper never streams and receives one short absolute deadline. The first ranked
            # unpaid candidate is the entire auxiliary call budget; provider failover belongs to
            # the answer lane, not to a disposable classification artifact.
            call_context = {
                key: value
                for key, value in dict(source_context or {}).items()
                if key != "runtime_event_stream_id"
            }
            call_context = bind_provider_deadline(
                call_context,
                turn_deadline_monotonic=runtime_active_clock.monotonic() + timeout_seconds,
                cleanup_margin_seconds=0.0,
                reason=f"pre-classification {kind.value} budget",
            )
            try:
                _adapter, response, error = agent.memory_router._invoke_manifest(
                    manifest=manifest,
                    request=request,
                    output_mode="json_object",
                    task=None,
                    source_context=call_context,
                )
            except Exception:
                return ""
            if error or response is None:
                return ""
            parsed = parse(str(getattr(response, "output_text", "") or ""))
            if parsed and kind == PlannerCallKind.CLAUSE_DECOMPOSITION:
                from core.agent_runtime.planner_sources import bind_sources

                return bind_sources(parsed, prompt, required=bool(source_instructions))
            return parsed

        return artifact.resolve(_invoke_once, kind=kind)

    return _ask


def build_planner_ask_model(
    agent: Any,
    source_context: dict[str, Any] | None,
) -> Callable[[str, str], str]:
    """The CLAUSE DECOMPOSITION call: one shared, single-attempt split for the whole turn."""

    return _build_auxiliary_ask_model(
        agent,
        source_context,
        kind=PlannerCallKind.CLAUSE_DECOMPOSITION,
        json_schema=_planner_json_schema(),
        parse=_strict_json_array,
        max_output_tokens=_PLANNER_MAX_OUTPUT_TOKENS,
        timeout_seconds=_PLANNER_TIMEOUT_SECONDS,
    )


def build_semantic_proof_ask_model(
    agent: Any,
    source_context: dict[str, Any] | None,
) -> Callable[[str, str], str]:
    """The SEMANTIC PROOF call: a bounded classification, on its own contract and cache slot.

    Deliberately a separate builder rather than a flag. The two calls differ in every field that
    matters -- prompt, schema, parser, ceiling, deadline -- and a boolean would have kept them one
    function whose behaviour depended on remembering to pass it.
    """

    return _build_auxiliary_ask_model(
        agent,
        source_context,
        kind=PlannerCallKind.SEMANTIC_PROOF,
        json_schema=_semantic_proposal_json_schema(),
        parse=_strict_semantic_proposal,
        max_output_tokens=_SEMANTIC_PROOF_MAX_OUTPUT_TOKENS,
        timeout_seconds=_SEMANTIC_PROOF_TIMEOUT_SECONDS,
    )


#: A conductor node answers one clause of a message: a short paragraph, or the JSON for a handful of
#: arithmetic steps. Sized above the largest reply either shape has been observed to need rather
#: than as a spend guess (CLAUDE.md 4b) -- a truncated step list loses its arithmetic entirely, and
#: the same input is billed again on the re-run.
_CONDUCTOR_NODE_MAX_OUTPUT_TOKENS = 1400


def _record_conductor_generation_authorship(
    manifest: Any,
    *,
    request: Any,
    authorship_log: list[dict[str, Any]],
    source_context: dict[str, Any] | None,
) -> None:
    """Ask the authorship policy about the manifest that just served, and log the verdict.

    The permission the stable-knowledge exemption exercises is not created here: it is
    `core.final_answer_authorship`'s to give (`decide_final_answer_author`, escalation off —
    the bytes exist), asked about the manifest that actually served THIS call. The verdict is
    appended to a per-turn log the conductor's stable-knowledge publisher joins against the
    node renders (by the clause the briefing embeds), so an exemption can name the demand it
    authorizes and the author that earned it. This deliberately does NOT write the turn's
    whole-answer authorship record — that record's consumer (`gate_authored_content`) holds
    whole-turn jurisdiction, and a per-node uncertified writer must not newly refuse a mixed
    answer the composer already accounts for. Fail-closed and visible: on any internal error
    nothing is appended (no verdict, no exemption) and the failure is logged, never silent.
    """

    try:
        from core.final_answer_authorship import (
            FINAL_ANSWER_ROLE,
            decide_final_answer_author,
        )

        prompt = str(getattr(request, "prompt", "") or "")
        decision = decide_final_answer_author(
            request_text=prompt,
            author_role=FINAL_ANSWER_ROLE,
            requested_manifest=manifest,
            requested_model=str(getattr(manifest, "provider_id", "") or ""),
            allow_escalation=False,
        )
        authorship_log.append(
            {
                "prompt": prompt,
                "provider_id": str(getattr(manifest, "provider_id", "") or ""),
                "eligible": bool(decision.eligible),
                "reason": str(decision.reason or ""),
            }
        )
        if isinstance(source_context, dict):
            source_context["conductor_generation_authorship"] = authorship_log
    except Exception:
        import logging

        logging.getLogger(__name__).exception(
            "conductor generation authorship decision not recorded; the stable-knowledge "
            "publication exemption is unavailable for this turn"
        )


def conductor_generation_candidates(
    manifests: list[Any], paid_scope: PinnedPaidTurnScope | None = None,
) -> list[Any]:
    """The callable candidates shared by generation and its infrastructure pre-checks.

    A paid pin without an explicitly supplied paid scope is unavailable to an unpaid
    helper. Its absence is not an empty provider reply or a failed model judgment.
    """
    return ([manifest for manifest in manifests if paid_scope.matches(manifest)]
            if paid_scope is not None else _unpaid_manifests(manifests))


def build_conductor_ask_model(
    agent: Any,
    source_context: dict[str, Any] | None,
    *,
    paid_scope: PinnedPaidTurnScope | None = None,
    reasoning_mode: str = "auto",
    default_json_schema: dict[str, Any] | None = None,
) -> Callable[[str, str], str]:
    """A `run_generation(system_prompt, prompt)` for a conductor node that reasons.

    Auto uses the same unpaid-manifest seam as the planner.  An exact paid pin can add only its
    server-built, per-turn-capped scope; routing through `_invoke_manifest` then enforces the
    reservation and puts the call in the runtime's own model-call ledger rather than beside it.

    It differs from the planner's ask in two ways, both of them the node's shape rather than
    preference. The ceiling is higher because a node returns an answer, not a split. And reasoning
    is left at the model's default instead of disabled: a node working out what an exchange implies
    is doing the thing reasoning is for, where the planner is only labelling clauses.
    Classifiers sharing this policy boundary can explicitly disable reasoning and bind a
    default JSON contract without changing ordinary answer generation.
    """

    # The turn's authorship verdicts, one per generation call that served (see
    # `_record_conductor_generation_authorship`). Enclosing-scope so every call appends to
    # the same list the stable-knowledge publisher will join against.
    authorship_log: list[dict[str, Any]] = [
        dict(item)
        for item in list(dict(source_context or {}).get("conductor_generation_authorship") or [])
        if isinstance(item, dict)
    ]

    def _ask(system_prompt: str, prompt: str, *, json_schema: dict[str, Any] | None = None) -> str:
        from adapters.base_adapter import ModelRequest
        from core.agent_runtime.audit_routing import resolve_routing_mode, select_audit_manifests

        if json_schema is None:
            json_schema = default_json_schema
        routing = resolve_routing_mode(source_context)
        manifests, _reason = select_audit_manifests(agent, source_context, routing)
        # A paid scope is also an exact-model scope.  Do not consider even an unpaid extra if a
        # future selector bug returns more than the pinned manifest: that would silently change who
        # answered after the owner selected a concrete model.
        candidates = conductor_generation_candidates(manifests, paid_scope)
        if not candidates:
            return ""
        request = ModelRequest(
            # The same task kind the planner uses. `core.model_capabilities` keys off this, and a
            # kind nothing in the runtime knows about would take the call off every path that
            # already serves it -- a new name here buys nothing and costs the capability lookup.
            task_kind="normalization_assist",
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=0.0,
            reasoning_mode=reasoning_mode,
            max_output_tokens=_CONDUCTOR_NODE_MAX_OUTPUT_TOKENS,
            output_mode="json_object" if json_schema is not None else "plain_text",
            contract={"json_schema": json_schema} if json_schema is not None else {},
            metadata={"conductor_node": True},
        )
        # A node's reply is assembled into the answer by `compose_answer`; streaming it would put
        # one clause's working on the screen as though it were the whole reply.
        call_context = {
            key: value
            for key, value in dict(source_context or {}).items()
            if key != "runtime_event_stream_id"
        }
        for manifest in candidates:
            from core.provider_call_deadline import (
                ProviderCallDeadlineExceededError,
                deadline_expired,
            )

            if deadline_expired(call_context):
                # The enclosing deadline is behind us. Iterating further would start calls
                # destined to be cancelled (abandoned work) and end with `return ""`, which
                # the knowledge node misreports as an empty generation — measured on
                # 79a9b357: the Berlin Wall node's PROVIDER_TIMEOUT surfaced as
                # "ValueError: the explanation came back empty". Raise the typed error so
                # the node's failure names what actually happened.
                raise ProviderCallDeadlineExceededError(
                    "conductor generation deadline expired before a provider call could start"
                )
            # The authorship policy is asked BEFORE the call, in its own voice: a conductor
            # generation node's bytes are served verbatim by `compose_answer`, so this is
            # final-answer authorship, and `decide_final_answer_author` with escalation off is
            # the same question `precall_author_verdict` asks everywhere else ("an ineligible
            # one means the call must not be made at all"). Skipping the ineligible candidate
            # ALSO stops the measured deadline race where cold uncertified fallbacks load for
            # tens of seconds before the certified author is ever reached (F29/F43, served on
            # 6a636236: the knowledge node died 'missing: text' after two doomed cold loads).
            # A home with NO eligible author keeps its existing honest outcome: the node fails
            # its generation seam, exactly as the whole-turn law already refuses.
            from core.final_answer_authorship import (
                FINAL_ANSWER_ROLE,
            )
            from core.final_answer_authorship import (
                decide_final_answer_author as _decide_for_call,
            )

            _call_decision = _decide_for_call(
                request_text=prompt,
                author_role=FINAL_ANSWER_ROLE,
                requested_manifest=manifest,
                requested_model=str(getattr(manifest, "provider_id", "") or ""),
                allow_escalation=False,
            )
            if _call_decision.eligible is not True:
                authorship_log.append(
                    {
                        "prompt": prompt,
                        "provider_id": str(getattr(manifest, "provider_id", "") or ""),
                        "eligible": False,
                        "reason": f"skipped_before_call:{_call_decision.reason}",
                    }
                )
                if isinstance(source_context, dict):
                    source_context["conductor_generation_authorship"] = authorship_log
                continue
            task = None
            receipt = None
            if paid_scope is not None and paid_scope.matches(manifest):
                task, authorization, receipt = paid_scope.reserve(
                    manifest=manifest,
                    request=request,
                    purpose="conductor_generation",
                )
                if task is None or authorization is None:
                    return ""
                call_context = paid_scope.call_context(authorization)
            try:
                _adapter, response, error = agent.memory_router._invoke_manifest(
                    manifest=manifest,
                    request=request,
                    output_mode=request.output_mode,
                    task=task,
                    source_context=call_context,
                )
            except Exception as exc:
                if paid_scope is not None and receipt is not None:
                    # `_invoke_manifest` normally releases every failed reservation itself.  This
                    # catches an exception outside its guarded provider block so a standing row is
                    # never left behind while the receipt claims a terminal outcome.
                    from core.paid_call_reservation import release_owner_pick_paid_call

                    release_owner_pick_paid_call(
                        authorization,
                        reason="conductor_helper_boundary_exception",
                        source_context=call_context,
                        call_role="conductor_generation",
                    )
                    paid_scope.finish(receipt, result=f"failed:{type(exc).__name__}")
                if deadline_expired(call_context):
                    raise ProviderCallDeadlineExceededError(
                        "conductor generation deadline expired during a provider call"
                    ) from exc
                continue
            if error or response is None:
                if paid_scope is not None and receipt is not None:
                    paid_scope.finish(receipt, result=f"failed:{error or 'no_response'}")
                if deadline_expired(call_context):
                    raise ProviderCallDeadlineExceededError(
                        "conductor generation deadline expired during a provider call"
                    )
                continue
            from core.incomplete_answer import ProviderOutputIncompleteError, inspect_provider_completion

            recorded_completion = dict(getattr(response, "constraint_result", {}) or {}).get(
                "response_control", {}).get("provider_completion", {}).get("final", {})
            completion = inspect_provider_completion(
                str(getattr(response, "output_text", "") or ""),
                finish_reason=str(getattr(response, "finish_reason", "") or ""),
                usage=dict(getattr(response, "usage", {}) or {}),
                max_output_tokens=request.max_output_tokens,
            )
            # The router's final disposition wins over the original provider finish reason
            # when its single authorized recovery completed successfully.
            incomplete = recorded_completion.get("incomplete", completion.incomplete)
            if incomplete:
                if paid_scope is not None and receipt is not None:
                    paid_scope.finish(receipt, result="failed:provider_output_incomplete")
                raise ProviderOutputIncompleteError(tuple(recorded_completion.get("reasons", completion.reasons)))
            text = str(getattr(response, "output_text", "") or "").strip()
            if text:
                if paid_scope is not None and receipt is not None:
                    paid_scope.finish(receipt, result="completed")
                _record_conductor_generation_authorship(
                    manifest,
                    request=request,
                    authorship_log=authorship_log,
                    source_context=source_context,
                )
                return text
            if paid_scope is not None and receipt is not None:
                paid_scope.finish(receipt, result="failed:empty_response")
        return ""

    return _ask


#: The same-turn evidence channels `core.model_output_guard.turn_ran_observations`
#: reads — mirrored here (not imported private) so a sub-turn's observations can be
#: propagated to the external turn that ran it (see the R1e note in `_run_one`).
_SUBTURN_EVIDENCE_KEYS = (
    "runtime_tool_observations",
    "web_retrieval_receipts",
    "fresh_data_retrieval_receipts",
    "external_evidence",
)


def _merge_evidence_lists(parent_value: list, child_value: list) -> list:
    """Order-preserving union of two evidence lists, deduplicated stably.

    Canonical JSON (`sort_keys`, `default=str`) is the identity of an entry, so the
    same observation a later child re-reports (inherited through its context copy)
    lands once while a genuinely new one always survives. Parents stay first, which
    keeps the order the turn actually observed in."""
    import json

    seen = {
        json.dumps(item, sort_keys=True, default=str) for item in parent_value
    }
    merged = list(parent_value)
    for item in child_value:
        key = json.dumps(item, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            merged.append(item)
    return merged


def build_planner_run_one(
    agent: Any,
    *,
    session_id: str,
    source_context: dict[str, Any] | None,
) -> Callable[[Any, dict[int, Any]], str]:
    """A `run_one(task, done)` that answers one planned request as its own ordinary turn.

    Reusing the whole turn path is the point: every lane the runtime already has -- the
    deterministic price lookups, weather, the tool loop -- answers a planned request exactly as it
    answers a single-request message, so no lane has to learn that multi-part messages exist.

    `planned_subturn` marks the sub-turn so `turn_may_hold_several_requests` refuses to plan it
    again; without that a plan of two would plan each half forever.
    """

    def _run_one(task: Any, _done: dict[int, Any]) -> str:
        from core.agent_runtime.turn_planner import planned_task_slot

        sub_context = dict(source_context or {})
        sub_context["planned_subturn"] = True
        # M3 requirement 9: a child runs on a COPY of this context, so the grounding lifecycle
        # id copies with it and the child's retrieval, binding and observations land on the
        # PARENT's row. The parent is then gated on the merged bytes as a whole -- the only
        # place a claim merged out of two children cannot slip between two separate accounts.
        _grounding_parent = source_context if isinstance(source_context, dict) else None
        # ARCH-TRUTH-R1d: this task's stable slot on the parent turn's chain. Every row the
        # sub-turn writes carries it, so the wave's tasks are distinguishable to the
        # database: distinct tasks execute concurrently, the same task cannot execute twice.
        sub_context["planned_task_slot"] = planned_task_slot(task)
        # The sub-turn must not write its own conversation entry or drive the live status card:
        # the user asked one question and gets one answer, and history full of fragments makes the
        # next turn's follow-up resolution read them as the user's own thread.
        sub_context.pop("runtime_event_stream_id", None)
        # Use the persistence authority's existing opt-out. The old private
        # suppress_conversation_log flag had no reader, so every child was
        # staged as another user exchange under the parent's commit boundary.
        sub_context["persist_memory"] = False
        # A planned sub-turn is a WHOLE turn, not a cheap tool call, so fanning several of them out
        # at once puts several full generations into one local model simultaneously. Measured
        # 2026-08-05 on the three-part BMW message: the first sub-request answered and the other two
        # came back "qwen3:8b ... did not return a usable reply" -- byte for byte the failure
        # core/local_model_admission.py already documents, down to the 60s read timeout:
        #
        #     N=24  17/24 answered, 7 returned "I couldn't get a usable model response" at ~64s
        #     model.call_failed  HTTPConnectionPool(127.0.0.1:11434) Read timed out (timeout=59.99)
        #
        # That module was written to bound exactly this and nothing had ever called it. Holding a
        # slot here makes concurrent sub-turns QUEUE instead of dogpiling: the wave still runs
        # concurrently, but no more of it reaches the model at once than the lane can serve. A
        # saturated lane raises before any socket is opened, so it surfaces as this request's own
        # failure note rather than as a mystery timeout.
        try:
            from core.local_model_admission import LocalModelLaneSaturatedError, local_model_slot
        except Exception:
            local_model_slot = None  # type: ignore[assignment]
            LocalModelLaneSaturatedError = Exception  # type: ignore[assignment]

        def _invoke() -> Any:
            from core.turn_contract import TURN_REQUEST_KEY

            sub_text = str(getattr(task, "request", "") or "")
            # ARCH-TRUTH-R1b: a planned sub-turn is INTERNAL work — one task under
            # the user's single external turn, not another external turn. So it
            # mints NOTHING: it runs under the PARENT's canonical TurnRequest (the
            # one object the ingress minted, carried on the context this hook was
            # built with), and the task's own text rides as the sub-turn's input,
            # typed by `PlannedTask` — the existing task contract — so the parent's
            # byte-exact record of what the user actually typed is never rewritten.
            #
            # Before this, the hook called `TurnRequest.from_ingress` a second time
            # while reusing the parent's request/turn identities: one user turn with
            # two "canonical" requests, the child claiming external-turn status it
            # does not have.
            return agent._run_once_inner(
                sub_text,
                session_id_override=session_id,
                source_context=sub_context,
                turn_request=sub_context.get(TURN_REQUEST_KEY),
            )

        if local_model_slot is None:
            result = _invoke()
        else:
            try:
                with local_model_slot(provider_id="planned_subturn"):
                    result = _invoke()
            except LocalModelLaneSaturatedError as exc:
                # Never silently drop the request: `run_plan` records this against this task alone
                # and `merge_outcomes` names it, which is the whole contract of a multi-part answer.
                raise RuntimeError(str(exc)) from exc
        # R1e — SAME-TURN EVIDENCE PROPAGATION (amended: CUMULATIVE). A sub-turn
        # runs on a shallow COPY of the parent's context, and the observation
        # channels (`runtime_tool_observations`, retrieval receipts) are REBOUND on
        # that copy by their writers — so a turn whose children genuinely fetched
        # weather or quotes still read as "observed nothing" at the parent, and the
        # final-text guard honestly-but-wrongly replaced the merged live values with
        # the no-lookup notice. The parent external turn DID observe, through its
        # units: every child's channel content reaches the parent. The first cut
        # copied a channel only when the parent lacked it — first-writer-wins —
        # which silently DISCARDED every later child's receipts (measured: a
        # weather child's observation survived, the gold child's market_quote
        # observation vanished). The merge is now cumulative with stable
        # deduplication: order-preserving union keyed by each entry's canonical
        # JSON, so the same observation arriving twice lands once and distinct
        # observations from distinct children all survive.
        try:
            parent_context = source_context if isinstance(source_context, dict) else None
            if parent_context is not None:
                for evidence_key in _SUBTURN_EVIDENCE_KEYS:
                    child_value = sub_context.get(evidence_key)
                    if not child_value:
                        continue
                    parent_value = parent_context.get(evidence_key)
                    if parent_value is None:
                        parent_context[evidence_key] = child_value
                    elif isinstance(parent_value, list) and isinstance(child_value, list):
                        parent_context[evidence_key] = _merge_evidence_lists(
                            parent_value, child_value
                        )
        except Exception:
            pass
        # M3 requirement 9: the child reports the lifecycle stage IT reached, on the parent's
        # row. The child's evidence is already on that row (it ran on a copy carrying the same
        # lifecycle id); this is the per-child account, so a parent that merged four answers can
        # be read for the one child whose evidence never bound rather than only in aggregate.
        try:
            from core.grounding_lifecycle import record_child_lifecycle

            record_child_lifecycle(
                _grounding_parent,
                sub_context,
                task_index=int(getattr(task, "index", 0) or 0),
                request=str(getattr(task, "request", "") or ""),
            )
        except Exception:
            pass
        if not isinstance(result, dict):
            return ""
        # CAPABILITY TRUTH — record WHICH lane actually served this demand.
        #
        # The sub-turn knows: it stamps its own `route` / `route_reason`. The parent
        # cannot infer it, and inferring it is how a demand the deterministic
        # registry does not claim ends up filed `capability=unsupported` beside a
        # SUCCEEDED dispatch and an answer receipt — three statements that cannot
        # all be true, and the shape this channel exists to make unwritable.
        # Keyed by the task's index, which `demand_records` already keys outcomes
        # by, so an executor can never be attributed to the wrong demand.
        try:
            from core.turn_contract import TURN_DEMAND_EXECUTORS_KEY

            parent = source_context if isinstance(source_context, dict) else None
            index = getattr(task, "index", None)
            if parent is not None and isinstance(index, int):
                executors = parent.get(TURN_DEMAND_EXECUTORS_KEY)
                if not isinstance(executors, dict):
                    executors = {}
                    parent[TURN_DEMAND_EXECUTORS_KEY] = executors
                executors[index] = {
                    "route": str(result.get("route") or ""),
                    "route_reason": str(result.get("route_reason") or result.get("reason") or ""),
                }
        except Exception:
            pass
        text = str(result.get("response") or result.get("output_text") or "").strip()
        # `task_outcome` ("success"/"pending_approval"/"failed", set deep inside the tool-intent
        # loop) does not survive to this top-level dict -- `action_fast_path_result` computes it
        # and uses it for telemetry/checkpoint status but never returns it. `response_class` DOES
        # survive (core/agent_runtime/response_policy_classification.py:action_response_class maps
        # task_outcome to it 1:1), so that is the field to read here, not task_outcome.
        response_class = str(result.get("response_class") or "").strip()
        if response_class == "approval_required":
            from core.agent_runtime.turn_planner import PlannedTaskNeedsApprovalError

            raise PlannedTaskNeedsApprovalError(text or "Approval required.")
        if response_class in {"task_failed_user_safe", "system_error_user_safe"}:
            raise RuntimeError(text or "the task could not be completed")
        # P0 MIXED-DEMAND (served) — A RUNTIME NOTICE IS NOT AN ANSWER.
        #
        # `turn_reasoning` stamps `runtime_notice_not_an_answer` on the turn that
        # produced one ("`qwen2.5:7b` was blocked by the local memory-safety
        # admission gate ... Retry the turn."). Returned from here it is a
        # non-empty string with no error, so `TaskOutcome.ok` reads True and the
        # demand is filed as EXECUTED.
        #
        # Measured over HTTP with the local lane made unreachable: the file read
        # and the arithmetic were served, the interpretation demand produced that
        # notice, and the turn still certified `demand_satisfied: 3,
        # demand_unanswered: 0, fulfilled_obligations: 3` — a failed demand
        # reported as satisfied, which is the false SUCCEEDED this whole contract
        # exists to forbid, arriving through the one lane that was supposed to
        # prevent it.
        #
        # Read from the runtime's OWN typed marker, never from the notice's
        # prose: pattern-matching failure text would be a recognizer pretending to
        # be a decision, and it would break the moment the wording changed.
        if bool(sub_context.get("runtime_notice_not_an_answer")):
            raise RuntimeError(text or "the runtime could not answer this request")
        # P0 MIXED-DEMAND — AN AMBIGUITY ASK-BACK IS NOT AN ANSWER EITHER. The F45 entity
        # gate serves a clarification INSTEAD of an answer when the question's referent is
        # ambiguous, or when its adjudication did not complete (the same three-state law the
        # gate's docstring carries). Returned as plain text it is non-empty with no error, so
        # `TaskOutcome.ok` reads True and the knowledge demand files as EXECUTED beside a
        # clarification nobody can act on inside a merged multi-part answer. The turn's OWN
        # `reason` stamp is the typed marker -- never the ask-back's prose -- naming exactly
        # the two clarification outcomes (unresolved adjudication, ambiguous verdict).
        if str(result.get("reason") or "") in {
            "ambiguity_adjudication_unresolved",
            "ambiguity_clarification_ask",
        }:
            raise RuntimeError(text or "the question's entity could not be adjudicated")
        return text

    return _run_one


def build_request_is_servable() -> Callable[[str], bool]:
    """Whether a deterministic lane can answer this request on its own.

    Asks the runtime's existing recognisers -- the price/market alias resolution and the live
    weather matcher -- rather than introducing another word list. A request they claim is one that
    answers standalone, which is the whole condition under which splitting a message helps.
    """

    def _servable(request: str) -> bool:
        text = str(request or "").strip()
        if not text:
            return False
        try:
            from core.agent_runtime.fast_live_info_mode_classifier import _looks_like_live_weather_request
            from core.agent_runtime.fast_live_info_price import price_assets_named
            from tools.web.web_research import _looks_like_market_quote_query, _looks_like_price_query
        except Exception:
            return False
        try:
            if price_assets_named(text):
                return True
            if _looks_like_price_query(text):
                return True
            if _looks_like_market_quote_query(text) is not None:
                return True
            if _looks_like_live_weather_request(text.lower()):
                return True
        except Exception:
            return False
        return False

    return _servable
