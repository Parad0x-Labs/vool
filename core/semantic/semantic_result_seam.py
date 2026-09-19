"""Served semantic result admission seam.

One served turn → one accepted semantic result.
Every answer-authoring path in the served turn spine must converge through
this seam before a candidate is accepted as the final semantic result.

This seam OBSERVES/ADMITS existing behavior. It does NOT:
- rewrite semantic interpretation logic
- perform transport serialization
- publish/persist final bytes
- call a model or tool merely to observe admission

The seam records typed operational provenance (source, route, whether
model/tool/memory was used), not reasoning transcripts or giant model traces.
"""
from __future__ import annotations

import enum
import secrets
import threading
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any


class SemanticSource(enum.Enum):
    """Mechanically-distinct source classes for semantic candidates.

    A semantic candidate's source tells downstream consumers what kind of
    authority produced the content, NOT the quality or correctness of the
    answer. These classes are intentionally coarse — a fine-grained 50-value
    enum would obscure the structural question of WHO can author final
    semantics.
    """
    DETERMINISTIC_MECHANISM = "deterministic_mechanism"
    MODEL = "model"
    MEMORY = "memory"
    CACHE = "cache"
    TOOL_DERIVED = "tool_derived"
    FAST_PATH = "fast_path"
    REFUSAL_POLICY = "refusal_policy"
    RECOVERY = "recovery"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SemanticResultRecord:
    """One semantic result admission in the served turn spine.

    Created at the seam when a candidate is admitted as the final semantic
    result for this turn. Contains typed operational provenance only — no
    private chain-of-thought or giant model traces.

    IMMUTABLE: once created, no field may be mutated in-place. If downstream
    needs a transformed representation, it must create a downstream object.

    Fields:
        semantic_result_id: Stable identity for this admitted result.
            Shape: ``sr:<turn_id>:<boot_nonce>-<admission_counter>``.
            Minted by the A2 semantic admission authority at admission time.
        content: The semantic candidate bytes/content (response text).
        source: Which authority class produced this candidate.
        route_id: Which gate/route/producer created this candidate.
        confidence: The producer's stated confidence (0.0–1.0).
        used_model: Whether a model provider was called.
        used_tool: Whether a tool was executed.
        memory_or_cache_authored: Whether memory/cache authored the content.
        accepted: Whether this candidate was accepted as the final result.
        provenance: Immutable extra debug metadata (never goes into receipts).
    """
    semantic_result_id: str
    content: str
    source: SemanticSource
    route_id: str
    confidence: float
    used_model: bool
    used_tool: bool
    memory_or_cache_authored: bool
    accepted: bool = True
    provenance: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "semantic_result_id": self.semantic_result_id,
            "content_length": len(self.content),
            "source": self.source.value,
            "route_id": self.route_id[:200],
            "confidence": self.confidence,
            "used_model": self.used_model,
            "used_tool": self.used_tool,
            "memory_or_cache_authored": self.memory_or_cache_authored,
            "accepted": self.accepted,
        }


def classify_source_from_result(result: dict[str, Any]) -> SemanticSource:
    """Classify the semantic source from result metadata.

    Examines the result dict for route_reason, reason, confidence, and other
    fields to determine the most likely SemanticSource. Falls back to UNKNOWN
    when insufficient evidence is available.
    """
    route = str(result.get("route_reason") or result.get("reason") or "").strip().lower()
    if not route:
        return SemanticSource.UNKNOWN

    # Deterministic mechanism patterns: regex contracts, math evaluators,
    # grammar-based interpreters that do not call a model.
    _deterministic_prefixes = (
        "exact_literal", "stable_", "underspecified", "closed_world",
        "hypothetical_currency", "pure_arithmetic", "arithmetic",
        "startup_sequence", "heartbeat", "direct_math", "si_unit",
        "currency", "date_time", "category_error", "acronym", "metaphor",
        "type_mismatch", "file_format", "exact_semantic", "term_output",
        "file_location", "empty_session_action",
    )
    if any(route.startswith(p) for p in _deterministic_prefixes):
        return SemanticSource.DETERMINISTIC_MECHANISM

    # Refusal / policy block patterns.
    if any(p in route for p in ("heavy_model_blocked", "refused", "forbidden",
                                 "policy", "no_commands", "action_honesty")):
        return SemanticSource.REFUSAL_POLICY

    # Memory / cache patterns.
    if "cache" in route and any(p in route for p in ("hit", "exact", "candidate")):
        return SemanticSource.CACHE
    if route.startswith("memory") or "_memory_" in route:
        return SemanticSource.MEMORY

    # Fast path patterns (mechanism-like but not a semantic interpreter).
    if route == "empty_turn_fast_path":
        return SemanticSource.FAST_PATH

    # Recovery / fallback patterns.
    if any(p in route for p in ("resume_missing", "recovery", "fallback",
                                 "interrupted", "missing_resume")):
        return SemanticSource.RECOVERY

    # Tool-derived patterns.
    if any(p in route for p in ("conductor", "live_data", "planned_turn",
                                 "research", "tool_executed", "route_skips")):
        return SemanticSource.TOOL_DERIVED

    # Model lane patterns.
    if any(p in route for p in ("model", "provider", "grounded_turn",
                                 "advice_only", "model_lane")):
        return SemanticSource.MODEL

    return SemanticSource.UNKNOWN


def classify_tool_and_model_from_result(result: dict[str, Any]) -> tuple[bool, bool]:
    """Extract used_model and used_tool flags from a result dict.

    Reads the existing metadata rather than calling any provider or tool.
    """
    used_model = False
    used_tool = False

    # Check mode/mode_override for tool execution.
    mode = str(result.get("mode") or result.get("mode_override") or "").strip().lower()
    if mode == "tool_executed":
        used_tool = True

    # Check model_calls or used_model in result.
    mc = result.get("model_calls")
    if mc is not None:
        used_model = int(mc) > 0
    um = result.get("used_model")
    if um is not None:
        used_model = bool(um)

    # Check action_honesty_validator for tool indications.
    ahv = result.get("action_honesty_validator") or {}
    if ahv.get("applied"):
        pass  # applied means action was blocked, not executed

    # Check source_context for tool execution indicators.
    source_context = result.get("source_context") or {}
    if isinstance(source_context, dict):
        action_policy = source_context.get("action_policy", "")
        if str(action_policy or "").strip().lower() == "forbidden":
            used_tool = False  # tools were forbidden
        executed = source_context.get("_executed_tools")
        if executed:
            used_tool = True

    return used_model, used_tool


def classify_memory_or_cache_from_result(result: dict[str, Any]) -> bool:
    """Determine whether memory or cache authored this result."""
    route = str(result.get("route_reason") or result.get("reason") or "").strip().lower()
    if route.startswith("memory") or route.startswith("exact_cache") or route.startswith("candidate_cache"):
        return True
    source = str(result.get("source") or "").strip().lower()
    if source in ("memory_hit", "exact_cache_hit", "candidate_cache_hit"):
        return True
    check = result.get("_memory_or_cache_authored")
    if check is not None:
        return bool(check)
    return False


# ---------------------------------------------------------------------------
# Per-turn admission tracking
# ---------------------------------------------------------------------------
# Admission state lives in a ContextVar whose default is None: each execution
# context gets its OWN fresh _AdmissionState on first access (see
# _get_admission_state). A shared mutable default object created at import
# time would be visible to every thread/context that never calls .set(),
# letting one thread's reset_admission() clear another thread's admission.
# Reset at the start of each turn via reset_admission().

class _AdmissionState:
    """Tracks whether a semantic result has already been admitted this turn."""
    def __init__(self) -> None:
        self.accepted_record: SemanticResultRecord | None = None
        self._lock = threading.Lock()

    def admit(self, record: SemanticResultRecord) -> bool:
        """Try to admit this record. Returns False if already admitted."""
        with self._lock:
            if self.accepted_record is not None:
                return False
            self.accepted_record = record
            return True

    @property
    def admitted(self) -> bool:
        return self.accepted_record is not None


_ADMISSION: ContextVar[_AdmissionState | None] = ContextVar(
    "semantic_result_admission", default=None
)

# Process-global admission identity, owned by A2.
# The per-turn attempt counter alone is NOT durable uniqueness: sequential
# turns reusing the same external turn_id, or unrelated turns falling back to
# "turn-unknown", would re-mint identical sr:<turn_id>:<seq> ids for different
# accepted content.
#
# PROCESS-LIFETIME UNIQUENESS MODEL: each boot mints a fresh 128-bit
# cryptographic nonce (_BOOT_NONCE = secrets.token_hex(16)); the per-id
# suffix is "<nonce>-<counter>" where <counter> increments under a lock and
# never resets. Two boots therefore occupy DISJOINT id ranges unless their
# 128-bit nonces are equal — a birthday collision across N boots at
# probability ~N^2 / 2^129 (UUID4-equivalent strength). There is NO
# additive-numeric-base scheme here: nearby nonces cannot produce overlapping
# sequence ranges, because the counter lives in its own segment AFTER the
# nonce; range overlap requires exact nonce equality, not numeric proximity.
# Within a process uniqueness is mathematical (serialized increments).
# Across boots it is randomized at UUID4-equivalent strength — sufficient,
# not mathematically impossible, because semantic_result_id is consumed
# solely as an opaque durable correlation key (A6/A7), never as an ordering
# or encoded structure. A persisted durable allocator was rejected: it would
# force this observe-only seam to take a storage dependency and write at
# mint time, expanding A2 jurisdiction into persistence.
_BOOT_NONCE = secrets.token_hex(16)
_ADMISSION_COUNTER_LOCK = threading.Lock()
_ADMISSION_COUNTER = 0


def _next_identity_suffix() -> str:
    """Return this boot's next admission identity suffix ('<nonce>-<n>')."""
    global _ADMISSION_COUNTER
    with _ADMISSION_COUNTER_LOCK:
        _ADMISSION_COUNTER += 1
        n = _ADMISSION_COUNTER
    return f"{_BOOT_NONCE}-{n}"


def _get_admission_state() -> _AdmissionState:
    """Return the calling execution context's own admission state.

    The ContextVar default is None. On first access in a context that has no
    state yet, a fresh _AdmissionState is created and set into THAT context
    only — threads/tasks never share one mutable default object.
    """
    state = _ADMISSION.get()
    if state is None:
        state = _AdmissionState()
        _ADMISSION.set(state)
    return state


def _derive_turn_id(result: dict[str, Any]) -> str:
    """Best-effort turn identity for the semantic result id.

    Reads the canonical user turn id first (set by the intake spine), then
    falls back to checkpoint/cancel turn identity, then to the session id.
    Downstream never fabricates this — the A2 semantic admission authority
    mints the final ``sr:<turn_id>:<sequence>`` id itself.
    """
    source_context = result.get("source_context") or {}
    if isinstance(source_context, dict):
        for key in ("_canonical_user_turn_id", "cancel_turn_id", "runtime_checkpoint_id"):
            value = str(source_context.get(key) or "").strip()
            if value:
                return value
        session_id = str(source_context.get("runtime_session_id") or "").strip()
        if session_id:
            return session_id
    return "turn-unknown"


def reset_admission() -> None:
    """Reset the admission state for a new turn. Call once at turn start.

    Installs a FRESH state object into the calling execution context only:
    concurrent turns on other threads/tasks are unaffected, and a context
    that inherited its state via context copy does not clear the origin
    context's record (mutation of an inherited shared object would).
    """
    _ADMISSION.set(_AdmissionState())


def admit_semantic_result(
    result: dict[str, Any],
    *,
    source_override: SemanticSource | None = None,
    route_id_override: str = "",
    turn_id: str = "",
) -> dict[str, Any]:
    """Admit a semantic result candidate through the seam.

    Records the admission with typed operational provenance. Returns the
    result dict unchanged — this seam observes/admits, it does not modify
    the candidate.

    When a second admission is attempted for the same turn, the record
    shows accepted=False on the duplicate (but the result dict is still
    returned unchanged for backward compatibility).

    This function:
    - makes zero model/tool calls
    - does not serialize transport
    - does not persist final bytes
    - does not modify the candidate's content
    """
    response = str(result.get("response") or "")
    if not response:
        return result

    route_id = route_id_override or str(
        result.get("route_reason") or result.get("reason") or ""
    )
    confidence = float(result.get("confidence") or 0.0)
    source = source_override or classify_source_from_result(result)
    used_model, used_tool = classify_tool_and_model_from_result(result)
    memory_cache = classify_memory_or_cache_from_result(result)

    state = _get_admission_state()
    identity_suffix = _next_identity_suffix()
    resolved_turn_id = turn_id or _derive_turn_id(result)
    # K-04: the durable referent feeds from req: when A0 bound one — a
    # request-seeded turn id replaces the "turn-unknown" orphan namespace.
    try:
        from core.semantic.semantic_admissions import current_request_id

        bound_request_id = current_request_id()
    except Exception:
        bound_request_id = ""
    if resolved_turn_id == "turn-unknown" and bound_request_id:
        resolved_turn_id = f"req-turn:{bound_request_id}"
    semantic_result_id = f"sr:{resolved_turn_id}:{identity_suffix}"

    record = SemanticResultRecord(
        semantic_result_id=semantic_result_id,
        content=response,
        source=source,
        route_id=route_id,
        confidence=confidence,
        used_model=used_model,
        used_tool=used_tool,
        memory_or_cache_authored=memory_cache,
        accepted=True,
        provenance=MappingProxyType({"source_override": source_override is not None}),
    )
    accepted = state.admit(record)

    # K-04: durable insert-once referent — admission precedes reference.
    # Fail loud: A7 refuses any citation whose referent row could not be written.
    from core.conductor import obligation_ledger as _ob_for_version
    from core.semantic.semantic_admissions import record_admission

    _active_obset = _ob_for_version.active_set()
    record_admission(
        record.semantic_result_id,
        source_class=source.value,
        accepted=accepted,
        obligation_set_version=_active_obset[1] if _active_obset else "",
    )

    # Attach a lightweight telemetry stub to the result for downstream
    # consumers (receipts, UI). This is NOT the canonical record — it is
    # a summary for visibility. The canonical record lives in the
    # ContextVar and is retrievable via current_admission().
    #
    # The stub always carries the AUTHORITATIVE semantic identity: the FIRST
    # admitted record's id. A rejected duplicate must never expose a fresh
    # candidate id in-band — that would put two contradictory
    # semantic_result_id values into circulation for one logical turn.
    # The stub always carries the AUTHORITATIVE semantic identity: the FIRST
    # admitted record's id. A rejected duplicate must never expose a fresh
    # candidate id in-band — that would put two contradictory
    # semantic_result_id values into circulation for one logical turn.
    result["_semantic_admission"] = {
        "semantic_result_id": (
            state.accepted_record.semantic_result_id
            if state.accepted_record is not None
            else semantic_result_id
        ),
        "source": source.value,
        "route_id": route_id[:200],
        "accepted": accepted,
    }

    if not accepted:
        result["_semantic_admission"]["duplicate"] = True
        result["_semantic_admission"]["previous_source"] = (
            state.accepted_record.source.value if state.accepted_record else "unknown"
        )

    return result


def current_admission() -> SemanticResultRecord | None:
    """Retrieve the admitted result for the current turn, or None."""
    try:
        state = _ADMISSION.get()
        return state.accepted_record if state is not None else None
    except Exception:
        return None


def has_admitted() -> bool:
    """Whether a semantic result has been admitted this turn."""
    try:
        state = _ADMISSION.get()
        return bool(state and state.admitted)
    except Exception:
        return False


__all__ = [
    "SemanticResultRecord",
    "SemanticSource",
    "admit_semantic_result",
    "classify_source_from_result",
    "current_admission",
    "has_admitted",
    "reset_admission",
]
