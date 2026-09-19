from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import os
import queue
import re
import threading
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field, replace
from types import SimpleNamespace
from typing import Any

from adapters.base_adapter import ModelAdapter, ModelRequest, ModelResponse
from core import audit_logger, chat_attachments, cloud_escalation_policy, policy_engine, usage_meter
from core import turn_routing as _turn_routing
from core.auto_local_only_mode import (
    CONTEXT_KEY as LOCAL_ONLY_CONTEXT_KEY,
)
from core.auto_local_only_mode import (
    CloudEgressBlockedError,
    assert_manifest_allowed,
    is_auto_selection,
    turn_is_local_only,
)
from core.backend_acceleration_truth import backend_acceleration_proof
from core.cache_freshness_policy import default_ttl_seconds, freshness_score, should_revalidate
from core.candidate_knowledge_lane import build_task_hash, get_exact_candidate, record_candidate_output
from core.cloud_tool_call_contract import (
    DuplicateToolCallError,
    MalformedToolArgumentsError,
    ToolCallParseError,
    UnknownToolNameError,
)
from core.compute_mode import get_active_compute_budget
from core.context_budgeter import available_prompt_tokens
from core.execution_requirements import RequiredToolsNotOfferedError
from core.incomplete_answer import (
    AnswerCompleteness,
    incomplete_answer_notice,
    inspect_provider_completion,
    partial_answer_notice,
)
from core.local_inference_autopilot import build_local_inference_autopilot_plan
from core.local_inference_evidence import hydrate_capability_truth_with_benchmarks
from core.local_model_bundles import model_parameter_billions, model_total_parameter_billions
from core.local_model_policy import (
    LOCAL_MODELS_DISABLED_REASON,
    resolve_local_model_policy,
    ungated_local_manifest_for_request,
)
from core.local_ollama_inventory import is_text_generation_ollama_model
from core.model_health import circuit_is_open, record_provider_failure, record_provider_success, should_probe_health
from core.model_output_contracts import json_schema_for_mode
from core.model_output_guard import reasoning_only_markers, scrub_foreign_markers, strip_reasoning_block
from core.model_registry import ModelRegistry
from core.model_request_policy import REASONING_AUTO, REASONING_DISABLED
from core.model_selection_policy import is_verified_free_cloud_manifest, provider_cost_class, reported_cost_class
from core.model_trust import output_trust_score
from core.normalized_provider_result import (
    ProviderErrorClass,
    classify_error_class,
    is_retryable,
    normalize_cloud_model_response,
    normalize_model_response,
)
from core.ordinary_chat_response_guard import (
    constrain_ordinary_chat_output,
    forgotten_prior_turn_literal_hashes,
    inspect_ordinary_chat_output,
    ordinary_chat_output_policy,
    ordinary_chat_overanswer_failure,
    ordinary_chat_retry_instruction,
    ordinary_chat_safe_fallback,
    prior_turn_literal_hashes,
    recover_bounded_ordinary_chat_output,
    remove_unrequested_prior_turn_literals,
    remove_unsolicited_generic_follow_up,
)
from core.output_validator import validate_provider_output
from core.paid_call_reservation import (
    release_owner_pick_paid_call,
    reserve_owner_pick_paid_call,
    settle_owner_pick_paid_call,
)
from core.presentation_selection import (
    EXPLICIT_COUNTERPART,
    select_presentation,
    selection_repair_acceptance,
)
from core.prompt_budget import PromptBudgetExceededError
from core.prompt_normalizer import normalize_prompt
from core.provider_execution_boundary import invoke_provider_execution_boundary
from core.provider_invocation_gateway import ProviderInvocationValidationError
from core.provider_routing import ProviderRole, provider_capability_truth_for_manifest, rank_provider_candidates
from core.raw_output_contract import (
    apply_raw_output_contract,
    parse_raw_output_contract,
    raw_output_contract_from_metadata,
    raw_output_contract_guidance,
    raw_output_retry_instruction,
)
from core.request_trust import request_is_owner_local
from core.response_constraints import (
    check_response_constraint,
    compress_short_coordinate_phrase,
    constraint_safe_fallback,
    enforce_response_constraint,
    exact_word_retry_contract,
    exact_word_retry_text,
    formatting_retry_instruction,
    parse_response_constraint,
    response_constraint_from_metadata,
    short_answer_needs_grounding_retry,
)
from core.response_language_policy import (
    check_response_language,
    response_language_policy_for_text,
    response_language_provider_instruction,
    response_language_retry_instruction,
    response_language_safe_fallback,
)
from core.retry_policy import RetryPolicy, should_retry
from core.runtime_evidence import PROVENANCE_CONTEXT_KEY
from core.runtime_flags import flag_enabled
from core.runtime_task_events import emit_runtime_event
from core.slm_mux import default_answer_key
from core.task_router import model_execution_profile
from core.token_usage_receipt import measured_call_usage
from core.turn_model_call_ledger import (
    ProviderCallAlreadyTerminalized,
    record_presentation_selection,
    record_provider_call,
    record_provider_call_outcome,
    turn_call_accounting,
)

_STRUCTURED_OUTPUT_MODES = {"json_object", "action_plan", "tool_intent", "summary_block"}
_CHAT_TRUTH_SURFACES = {"channel", "openclaw", "api"}

# Existing provider policy classifies structurally empty responses as retryable.  Keep the live
# execution budget narrower than the generic three-attempt default: one same-model retry, matching
# the already-established bounded retry in the free-cloud broker.  Cross-model fallback remains
# entirely owned by the outer candidate loop.
_EMPTY_RESPONSE_RETRY_POLICY = RetryPolicy(max_attempts=2)

# CPU-only detection for the provider-fallback budget. Cached once per process: hardware does not change
# mid-run, and probe_machine() may shell out to nvidia-smi (bounded), so it must not run on every turn.
_NO_USABLE_GPU_CACHE: bool | None = None


def audit_turn_metadata(source_context: dict[str, Any] | None) -> dict[str, Any]:
    """`{"workspace_audit_turn": True}` when this turn is a workspace audit, else nothing.

    `workspace_audit_evidence_collected` is what `run_workspace_audit` sets on the turn it ran in,
    and it is the only signal that separates an audit from the six other task classes sharing
    `task_kind == "normalization_assist"` (chat_conversation, general_advisory, business_advisory,
    food_nutrition, relationship_advisory, creative_ideation). Keying the adapter on `task_kind`
    would switch thinking off for ordinary chat -- the regression this exists to avoid.

    Returns an empty dict rather than a False value so an ordinary turn's metadata is byte-identical
    to what it was before, and never raises: a malformed context is not an audit.
    """

    try:
        if bool(dict(source_context or {}).get("workspace_audit_evidence_collected")):
            return {"workspace_audit_turn": True}
    except Exception:
        pass
    return {}


def _machine_has_no_usable_gpu() -> bool:
    """True when this host has no GPU lane Ollama can actually use (so inference runs on CPU). Cached.

    Only CUDA and Apple Metal (MPS) are GPU lanes Ollama offloads to; DirectML/Vulkan/other accelerators
    fall to CPU inside Ollama, so those hosts also need the larger CPU budget.
    """
    global _NO_USABLE_GPU_CACHE
    if _NO_USABLE_GPU_CACHE is None:
        try:
            from core.hardware_tier import probe_machine

            accelerator = str(getattr(probe_machine(), "accelerator", "") or "").strip().lower()
            _NO_USABLE_GPU_CACHE = accelerator not in {"cuda", "mps"}
        except Exception:
            _NO_USABLE_GPU_CACHE = False
    return _NO_USABLE_GPU_CACHE


# Free GPU VRAM, cached once per process for the same reason (probe_machine shells to nvidia-smi).
_FREE_VRAM_GB_CACHE: float | None = None
_FREE_VRAM_GB_PROBED = False

# Installed baseline model tag, cached once per process (default_runtime_model_tag probes hardware).
# Feeds the autopilot daily-lane tiebreak so the reported default model actually serves normal turns.
_DEFAULT_MODEL_TAG_CACHE: str | None = None
_DEFAULT_MODEL_TAG_PROBED = False


def _cached_default_model_tag() -> str | None:
    global _DEFAULT_MODEL_TAG_CACHE, _DEFAULT_MODEL_TAG_PROBED
    if not _DEFAULT_MODEL_TAG_PROBED:
        _DEFAULT_MODEL_TAG_PROBED = True
        try:
            from core.runtime_provider_defaults import default_runtime_model_tag

            _DEFAULT_MODEL_TAG_CACHE = str(default_runtime_model_tag() or "").strip() or None
        except Exception:
            _DEFAULT_MODEL_TAG_CACHE = None
    return _DEFAULT_MODEL_TAG_CACHE


# Resident (already-loaded) local model tags. NOT once-per-process: residency changes turn to
# turn, so this is a short-TTL cache -- one best-effort probe (2s hard timeout inside
# resource_governor.resident_models, [] on any error) at most every few seconds, shared by every
# plan built in that window. Feeds the autopilot's verifier pick: on a RAM-starved box a verifier
# that is already in memory reviews with zero new RAM, where the size-ranked pick was refused by
# the model-load gate every time (measured live 2026-08-14, sessions ed890df3/cceda886).
_RESIDENT_MODEL_TAGS_CACHE: tuple[str, ...] = ()
_RESIDENT_MODEL_TAGS_PROBED_AT = 0.0
_RESIDENT_MODEL_TAGS_TTL_SECONDS = 5.0


def _recent_resident_model_tags() -> tuple[str, ...]:
    global _RESIDENT_MODEL_TAGS_CACHE, _RESIDENT_MODEL_TAGS_PROBED_AT
    now = time.monotonic()
    if now - _RESIDENT_MODEL_TAGS_PROBED_AT < _RESIDENT_MODEL_TAGS_TTL_SECONDS and _RESIDENT_MODEL_TAGS_PROBED_AT > 0:
        return _RESIDENT_MODEL_TAGS_CACHE
    _RESIDENT_MODEL_TAGS_PROBED_AT = now
    try:
        from core.resource_governor import resident_models

        _RESIDENT_MODEL_TAGS_CACHE = tuple(
            str(row.get("name") or "").strip() for row in resident_models() if str(row.get("name") or "").strip()
        )
    except Exception:
        # Fail soft to "no preference": the verifier ranking is then exactly what it was before
        # residency awareness existed.
        _RESIDENT_MODEL_TAGS_CACHE = ()
    return _RESIDENT_MODEL_TAGS_CACHE


def _cached_free_vram_gb() -> float | None:
    """Free GPU VRAM in GB, probed once per process. Feeds the autopilot's VRAM-aware deep-lane check
    so a heavy build is not escalated to a model too big to fit the GPU. Returns None when the probe
    fails or does not report free VRAM, which keeps the fit check conservative (no downgrade).
    """
    global _FREE_VRAM_GB_CACHE, _FREE_VRAM_GB_PROBED
    if not _FREE_VRAM_GB_PROBED:
        _FREE_VRAM_GB_PROBED = True
        try:
            from core.hardware_tier import probe_machine

            free = getattr(probe_machine(), "vram_free_gb", None)
            _FREE_VRAM_GB_CACHE = float(free) if free is not None else None
        except Exception:
            _FREE_VRAM_GB_CACHE = None
    return _FREE_VRAM_GB_CACHE


# The minimum per-attempt window the sequential fallback loop will ever grant a candidate,
# whether it is the ONLY candidate attempted (see the pre-existing `max(5.0, ...)` floor at the
# per-call cap site) or one further down a reserve-shrunk chain (see
# _fallback_reserve_seconds). Named here, at its single defining site, so both call sites --
# and the SWITCHBOARD-locked reserve formula -- read from the same value instead of repeating
# the literal. Not a new timeout constant: this is the pre-existing 5.0s floor, named.
_FALLBACK_ATTEMPT_FLOOR_SECONDS = 5.0


def resolve_fallback_budget_seconds(
    lane: str, *, forced_cpu: bool, no_usable_gpu: bool, output_mode: str = ""
) -> float | None:
    """Wall-clock budget for the sequential provider-fallback loop.

    deep/cloud/human are explicit heavy lanes and stay unbounded (None). Ordinary lanes get a finite
    budget: the larger CPU value when the run is CPU-only (``VOOL_OLLAMA_NUM_GPU==0`` or no usable GPU)
    so a slow CPU generation can finish, else the GPU default. Exported so a test can drive the real
    decision rather than a mirror of it.

    ``output_mode == "tool_intent"`` also gets the larger budget on an otherwise-fast lane, on GPU
    included. The 60s "ordinary lane, fail fast" default was sized for plain conversation; a
    tool_intent call always carries the full tool catalog with ``tool_choice: required`` (64 tools,
    ~32KB of schema, ~9,700 prompt tokens measured live 2026-08-04), which is a heavier prompt than
    the lane's speed assumption was built for. Measured on this hardware, warm model, reasoning
    already disabled (see ``reasoning_mode`` on the built request): 72.1s wall clock, of which 70.8s
    is prompt processing and 1.2s is generation -- the bottleneck is the catalog, not chain-of-thought,
    and it does not fit the 60s ordinary-chat budget regardless. Keyed on output_mode, a property of
    THIS call, not on lane or task name, for the same reason `_reasoning_disabled` is keyed on a typed
    request field rather than a task name (core/model_request_policy.py): the property that matters
    is "this call carries a large tool catalog," not "which lane dispatched it."
    """
    if lane in {"deep", "cloud", "human"}:
        return None
    if forced_cpu or no_usable_gpu:
        return float(policy_engine.get("model_orchestration.provider_fallback_budget_seconds_cpu", 180.0))
    if output_mode == "tool_intent":
        return float(policy_engine.get("model_orchestration.provider_fallback_budget_seconds_tool_intent", 180.0))
    return float(policy_engine.get("model_orchestration.provider_fallback_budget_seconds", 60.0))


def _gate_paid_by_cloud_escalation(
    *,
    resolved_allow_paid: bool,
    allow_paid_fallback: bool,
    requested_paid_cloud: bool,
    source_context: dict[str, Any] | None,
) -> tuple[bool, bool]:
    """Gate ALL paid-cloud selection through the BYOK cloud-escalation policy.

    Returns ``(resolved_allow_paid, cloud_escalation_authorized)``. The escalation policy is
    the single authoritative control for spending the user's cloud key:

    * Already-``False`` allow (e.g. local-only mode) -> untouched (nothing to gate).
    * An explicit paid-cloud model request is honored as a deliberate choice ONLY from the
      owner's own local session (:func:`core.request_trust.request_is_owner_local`). A remote
      or non-owner caller cannot bypass the policy by naming a paid model — it falls through
      to the policy gate.
    * The auto path (paid fallback, no explicit request) is governed by the policy: ``off``
      -> local, ``ask`` -> local (real OS consent is not wired yet, so ask never bursts on a
      caller-supplied flag — a forgeable body field must never authorize spend), ``auto`` ->
      cloud while under the daily cap.

    Fail closed by default: with no policy saved the policy loads as ``off`` -> local. Merely
    holding a cloud key (which registers a paid_cloud provider) never authorizes an auto
    burst; the user must explicitly set ``ask``/``auto``. ``cloud_escalation_authorized`` is
    True only when this call authorized a burst, so the caller records a day's cap usage only
    when a paid-cloud provider is actually selected.
    """
    if not resolved_allow_paid:
        return resolved_allow_paid, False

    owner_local = request_is_owner_local(source_context)
    if requested_paid_cloud:
        if owner_local:
            # Deliberate owner-local choice of a specific paid model; honored, not an auto
            # burst against the daily cap.
            return resolved_allow_paid, False
        # A non-owner explicit paid request must not bypass the policy — fall through.
    elif not allow_paid_fallback:
        # No auto paid fallback requested and no explicit paid request: nothing to authorize.
        return resolved_allow_paid, False

    decision = cloud_escalation_policy.decide_escalation(
        cloud_escalation_policy.load_policy(),
        cloud_escalation_policy.used_today(),
    )
    if decision.action == cloud_escalation_policy.ACTION_CLOUD and owner_local:
        # Auto mode authorizes a paid cloud burst against the user's own key ONLY for the owner's
        # local session, exactly like the ask branch below. A non-owner-local caller (a remote
        # channel message, an exposed 0.0.0.0 API) must never trigger an auto spend, even with
        # allow_paid_fallback set -- it falls through to stay local. (Backstopped today because
        # resolved_allow_paid is forced False without a server-built reservation; this keeps the
        # gate correct for when the live paid-authorization path is wired.)
        return True, True
    if decision.action == cloud_escalation_policy.ACTION_ASK and owner_local:
        # Ask mode: a real, injection-proof OS approval for THIS burst, only from the owner's
        # own local session. require_os_user_consent is cross-platform (Windows Hello / macOS
        # Touch ID / Linux polkit), fails closed, and is test-overridable — NOT a caller-supplied
        # source_context flag. A non-owner or non-interactive request never reaches here, so it
        # stays local. (One prompt per hard task that would burst; that is what "ask" means.)
        try:
            from core.os_consent_gate import require_os_user_consent

            if require_os_user_consent("Approve a paid cloud burst for this task (uses your cloud key)"):
                return True, True
        except Exception:
            return False, False
    # off / ask-from-non-owner / at-cap -> stay local.
    return False, False


# Per-turn served-response usage on a thread-local, so the API layer can render a tokens/cost
# footer for the same turn: run_agent resets it at the start of a turn, _decision_from_response
# records the served response's summary, both on the turn's own thread.
_TURN_USAGE = threading.local()


def reset_turn_usage() -> None:
    _TURN_USAGE.summary = None


def record_turn_usage(summary: dict[str, Any] | None) -> None:
    _TURN_USAGE.summary = summary


def get_turn_usage() -> dict[str, Any] | None:
    return getattr(_TURN_USAGE, "summary", None)


def _record_response_usage(manifest: Any, response: Any) -> dict[str, Any] | None:
    """Record a served model response in the token usage meter (fail-soft).

    Extracts prompt tokens (Ollama ``prompt_eval_count`` / OpenAI ``prompt_tokens``) and output
    tokens (``eval_count`` / ``completion_tokens`` / ``output_tokens``) and attributes them to
    the manifest's cost class, so the meter can split free-local from paid-cloud usage. Never
    raises — metering is observability, not correctness.
    """
    try:
        usage = dict(getattr(response, "usage", None) or {})
        # `or 0` cannot tell "the provider reported 0" from "the provider reported nothing" --
        # both look identical once collapsed to an int. Track presence separately (a key with any
        # non-None value counts as reported, including a genuine 0) so usage_meter can record the
        # distinction instead of silently treating an absent field as a confirmed zero.
        _prompt_keys = ("prompt_eval_count", "prompt_tokens", "input_tokens")
        _output_keys = ("eval_count", "completion_tokens", "output_tokens")
        prompt_tokens_reported = any(usage.get(key) is not None for key in _prompt_keys)
        output_tokens_reported = any(usage.get(key) is not None for key in _output_keys)
        prompt_tokens = int(
            usage.get("prompt_eval_count") or usage.get("prompt_tokens") or usage.get("input_tokens") or 0
        )
        output_tokens = int(
            usage.get("eval_count") or usage.get("completion_tokens") or usage.get("output_tokens") or 0
        )
        # Real per-request cost when the provider returns one; None falls back to the blended
        # estimate. OpenRouter returns usage.cost automatically (credits = USD, no request param
        # needed — verified against the current usage-accounting docs). usage.cost is the total
        # charged to the OpenRouter account. cost_details.upstream_inference_cost is charged
        # SEPARATELY to the user's own upstream account and ONLY for a BYOK-upstream key, so it
        # is added on top of usage.cost only when the response is flagged is_byok — otherwise
        # usage.cost alone is the out-of-pocket and adding a stray upstream value would double
        # count. Non-BYOK responses (the common credits-key case) use usage.cost by itself.
        cost = usage.get("cost")
        usd_actual = float(cost) if isinstance(cost, (int, float)) and cost >= 0 else None
        is_byok = bool(usage.get("is_byok") or (usage.get("cost_details") or {}).get("is_byok"))
        if is_byok:
            upstream = (usage.get("cost_details") or {}).get("upstream_inference_cost")
            if isinstance(upstream, (int, float)) and upstream >= 0:
                usd_actual = (usd_actual or 0.0) + float(upstream)
        provider_id = str(getattr(manifest, "provider_id", "") or "")
        model_id = str(getattr(manifest, "model_name", "") or "")
        cost_class = reported_cost_class(manifest)
        usage_meter.record_usage(
            provider_id=provider_id,
            model_id=model_id,
            cost_class=cost_class,
            prompt_tokens=prompt_tokens,
            output_tokens=output_tokens,
            usd_actual=usd_actual,
            prompt_tokens_reported=prompt_tokens_reported,
            output_tokens_reported=output_tokens_reported,
        )
        summary = {
            "provider_id": provider_id,
            "model_id": model_id,
            "cost_class": cost_class,
            "prompt_tokens": prompt_tokens,
            "output_tokens": output_tokens,
            "usd_actual": usd_actual,
            "prompt_tokens_reported": prompt_tokens_reported,
            "output_tokens_reported": output_tokens_reported,
        }
        if cost_class == "paid_cloud":
            # The priced figure a surface can render: the same number the spend ledger settles
            # at, carrying whether it rests on a published price or on the unknown-price ceiling.
            # ``usd_actual`` above stays what the PROVIDER reported (None for the seven slots that
            # report nothing), so the meter's own "actual" column keeps meaning exactly that.
            from core.model_pricing import provider_slot_for_manifest, settlement_usd

            estimate = _adapter_supplied_cost_estimate(manifest, response) or settlement_usd(
                provider_id=provider_slot_for_manifest(manifest), model_id=model_id, usage=usage
            )
            summary["cost_estimate"] = estimate.as_dict()
            summary["usd_priced"] = estimate.usd
            summary["usd_priced_known"] = estimate.known
            summary["usd_priced_note"] = estimate.describe()
        from core.response_usage_details import response_usage_details
        summary["usage_details"] = response_usage_details(response, cost_class=cost_class)
        return summary
    except Exception:  # pragma: no cover - defensive; metering never breaks a turn
        return None


def _adapter_supplied_cost_estimate(manifest: Any, response: Any) -> Any | None:
    """The cost estimate an ADAPTER computed from its own dispatch authorization, when it gives one.

    A dynamic marketplace (UsePod) prices a call against an owner-approved ceiling the adapter
    re-checked immediately before sending; the static table lookup below knows none of that, and its
    ceiling can sit below a real listing. The adapter's figure is used only when well-formed, and it
    keeps the adapter's own ``known``/``basis`` (an upper bound stays an upper bound).
    """
    metadata = getattr(response, "provider_metadata", None)
    raw = metadata.get("cost_estimate") if isinstance(metadata, dict) else None
    if not isinstance(raw, dict):
        return None
    try:
        import math

        from core.model_pricing import CostEstimate

        usd = float(raw.get("usd"))
        if not math.isfinite(usd) or usd < 0:
            return None
        return CostEstimate(
            usd=round(usd, 8),
            known=bool(raw.get("known", False)),
            basis=str(raw.get("basis") or ""),
            source=str(raw.get("source") or "")[:400],
            provider_id=str(raw.get("provider_id") or ""),
            model_id=str(raw.get("model_id") or getattr(manifest, "model_name", "") or ""),
            prompt_tokens=max(0, int(raw.get("prompt_tokens") or 0)),
            completion_tokens=max(0, int(raw.get("completion_tokens") or 0)),
            attempts=max(1, int(raw.get("attempts") or 1)),
        )
    except Exception:
        return None


def _provider_receipt(container: Any) -> dict[str, Any] | None:
    """The bounded, secret-free receipt an adapter attached to a response or a failure, if any.

    An adapter that authorizes and bills against its own evidence (UsePod: the route that served, the
    balance header, the settlement state) places a compact ``receipt`` beside its full evidence. Routing
    events carry the receipt and nothing larger, so an event row never holds a prompt or a credential.
    """
    receipt = container.get("receipt") if isinstance(container, dict) else None
    if not isinstance(receipt, dict):
        return None
    try:
        import json as _json

        from core.secret_redaction import redact_secrets

        text = _json.dumps(receipt, sort_keys=True, default=str)
        if len(text) > 16384:
            return {"state": "receipt_too_large_for_event", "bytes": len(text)}
        return _json.loads(redact_secrets(text))
    except Exception:
        return {"state": "receipt_unrenderable"}


def _response_cost_estimate(manifest: Any, response: Any) -> Any | None:
    """What this response cost, as a :class:`core.model_pricing.CostEstimate` (None if unavailable).

    Settlement used to read ``usage.cost`` and nothing else. Only OpenRouter returns that field;
    Anthropic, OpenAI, Groq, Google, DeepSeek, Moonshot and a custom endpoint return token counts
    only, so every one of those calls settled at $0.00 and the USD caps never moved. The cost now
    comes from the reported token counts priced at the model's published rate, and ``usage.cost``
    is used only where a provider actually supplies it (OpenRouter, where it is authoritative
    because it includes that provider's own margin).

    A model whose price VOOL does not know settles at a conservative ceiling rather than at zero
    -- see :mod:`core.model_pricing` for why the ceiling is the choice over refusing the call.

    An adapter that authorized the call against its own price evidence (UsePod's approved ceiling)
    supplies the estimate itself; that figure wins over the static lookup.
    """
    supplied = _adapter_supplied_cost_estimate(manifest, response)
    if supplied is not None:
        return supplied
    try:
        from core.model_pricing import provider_slot_for_manifest, settlement_usd

        return settlement_usd(
            # The billing provider, not the manifest's composite "provider_name:model_name" id --
            # a BYOK lane is named "anthropic-byok:claude-sonnet-4-5" and would price off nothing.
            provider_id=provider_slot_for_manifest(manifest),
            model_id=str(getattr(manifest, "model_name", "") or ""),
            usage=getattr(response, "usage", None),
        )
    except Exception:  # pragma: no cover - defensive; settlement never breaks a turn
        return None


def _response_actual_usd(manifest: Any, response: Any) -> float:
    """The USD to settle this response's reservation at. See :func:`_response_cost_estimate`.

    Falls back to 0.0 only when pricing itself is unavailable, which is bounded: the reservation's
    own ceiling is what keeps standing against the caps when no number can be named.
    """
    estimate = _response_cost_estimate(manifest, response)
    return float(getattr(estimate, "usd", 0.0) or 0.0)


@dataclass
class ModelExecutionDecision:
    source: str
    task_hash: str
    provider_id: str | None = None
    provider_name: str | None = None
    model_name: str | None = None
    output_text: str | None = None
    structured_output: Any = None
    confidence: float = 0.0
    trust_score: float = 0.0
    used_model: bool = False
    cache_hit: bool = False
    candidate_id: str | None = None
    failover_used: bool = False
    validation_state: str = "not_run"
    details: dict[str, Any] = field(default_factory=dict)
    # Every schema-valid native call the provider returned, in order. `structured_output` above is
    # call #1 re-parsed from `output_text`, so members[1:] are the ones the step loop does NOT
    # execute. They are carried so the turn can SAY they were not run, rather than dropping them
    # with no record - which is what happened before, on every lane.
    #
    # Deliberately NOT an execution queue. The loop re-asks the model after every step with the
    # accumulated results, so a model that meant three reads re-requests the other two with the
    # first result in hand - better informed than when it emitted the batch. Draining a queue
    # instead would execute pre-evidence intent and would break six separate mechanisms: the
    # pending-payload slot is the approval seam and is single, redacted and truncated; the pending
    # branch has no dedup, while routing through dedup ABORTS the turn rather than skipping a
    # member; mutating-tool idempotency is keyed on `step_index`, which a queue shifts; the
    # argument-correction hand-back needs the next iteration to reach the model; a new checkpoint
    # key would never be cleared by `finalize_runtime_checkpoint`'s fixed list; and the default
    # 5-step budget would turn a 3-call batch into a terminal "budget exhausted" failure.
    tool_calls: tuple[Any, ...] = ()

    def as_plan_candidate(self) -> dict[str, Any] | None:
        if not self.output_text:
            return None
        summary = self.output_text.strip().splitlines()[0][:220] if self.output_text.strip() else "Model-generated candidate"
        steps = []
        if isinstance(self.structured_output, dict):
            raw_steps = self.structured_output.get("steps") or []
            if isinstance(raw_steps, list):
                steps = [str(step) for step in raw_steps[:8]]
        return {
            "summary": summary,
            "resolution_pattern": steps,
            "score": self.trust_score or self.confidence,
            "source_type": "model_candidate",
            "source_node_id": self.provider_id,
            "provider_name": self.provider_name,
            "model_name": self.model_name,
            "candidate_id": self.candidate_id,
            "structured_output": self.structured_output,
            "validation_state": self.validation_state,
        }


def _provider_inventory_snapshot(registry: ModelRegistry) -> list[dict[str, Any]]:
    """Compact registry inventory for a brain-offline trace: what is registered and enabled.

    An empty list means seeding never populated the registry; all ``enabled=False`` means the
    manifests were turned off. Either way the trace now names the real gap instead of a generic
    "no live model" message. Best-effort — never raises into the routing decision.
    """
    try:
        return [
            {
                "provider_id": str(m.provider_id),
                "model": str(m.model_name),
                "enabled": bool(m.enabled),
                "source_type": str(m.source_type or ""),
            }
            for m in registry.list_manifests(enabled_only=False)
        ]
    except Exception:
        return []


def _candidate_cache_scope(source_context: dict[str, Any] | None) -> str:
    context = source_context or {}
    session_id = str(context.get("session_id") or context.get("runtime_session_id") or "").strip()
    turn_id = str(context.get("turn_id") or "").strip()
    if not session_id or not turn_id:
        return ""
    return f"{session_id}:{turn_id}"


def _routing_scope_identity(
    source_context: dict[str, Any] | None,
) -> tuple[str, str, str, str]:
    """(turn_id, session_id, conversation_ref, project_ref) the routing plan binds to.

    Reads only server-stamped keys — the canonical dialogue-turn id first, then the turn
    key, then the execution identity the turn door published. All empty means the caller
    bypassed every door; the plan mint refuses that (fail closed before spend).
    """
    context = source_context or {}
    identity = context.get("_execution_identity")
    identity = identity if isinstance(identity, dict) else {}
    turn_id = str(
        context.get("_canonical_user_turn_id")
        or context.get("turn_id")
        or identity.get("execution_id")
        or ""
    ).strip()
    session_id = str(context.get("session_id") or context.get("runtime_session_id") or "").strip()
    conversation_ref = str(context.get("chat_id") or "").strip()
    project_ref = str(context.get("_trusted_project_id") or context.get("project_id") or "").strip()
    return turn_id, session_id, conversation_ref, project_ref


def _mint_routing_plan_for_turn(
    registry: ModelRegistry,
    *,
    source_context: dict[str, Any] | None,
    requested_provider: str | None,
    requested_model: str,
    allow_paid: bool,
    local_only: bool,
    user_text: str,
) -> Any:
    """Mint the turn's routing plan, or None when identity fails closed.

    Temporary routing rules are consumed HERE — once per root turn, never per provider
    call — and may only narrow. A retry marker (the follow-up lane's "retry that exact
    request" dispatch) turns this mint into the next GENERATION of the referenced plan.
    Provenance is recorded durably per turn; it is trace truth, never prompt content.
    """
    turn_id, session_id, conversation_ref, project_ref = _routing_scope_identity(source_context)
    if not turn_id or not session_id:
        return None
    context = source_context or {}
    manifests: list[Any]
    try:
        manifests = list(registry.list_manifests(enabled_only=True))
    except Exception:
        manifests = []
    fences_local_only = bool(local_only)
    fences_allow_paid = bool(allow_paid)
    allowed_provider_ids: tuple[str, ...] | None = None
    allowed_models_fences: tuple[tuple[str, str], ...] | None = None
    applied_rule_ids: tuple[str, ...] = ()
    # ---- VOOL School fences (narrow-only, exactly like temporary rules) ----
    # The server-stamped school policy (reserved context key, validated at the
    # chat ingress) contributes its model allowlist and locality ceiling to the
    # plan mint. A school layer may only NARROW: it can force local-only and
    # remove candidates; it can never add a candidate or widen locality.
    if isinstance(context, dict):
        _school_ctx = context.get("school_policy")
        if isinstance(_school_ctx, dict):
            _school_policy = _school_ctx.get("policy") or {}
            if isinstance(_school_policy, dict):
                if str(_school_policy.get("locality") or "") == "local_only":
                    fences_local_only = True
                if _school_policy.get("model_allowlist_active"):
                    _pairs = []
                    for _entry in (_school_policy.get("allowed_models") or []):
                        _provider, _, _model = str(_entry).partition(":")
                        if _provider and _model:
                            _pairs.append((_provider, _model))
                    if _pairs:
                        allowed_models_fences = tuple(sorted(set(_pairs)))
    if not bool(context.get("planned_subturn")):
        # Root turns consume rule budgets; sub-turns inherit their parent's fences through
        # the copied context and must not burn a second turn of any budget.
        try:
            consumed = _turn_routing.consume_rules_for_mint(
                session_id=session_id,
                conversation_ref=conversation_ref,
                project_ref=project_ref,
            )
        except Exception:
            consumed = []
        if consumed:
            fences = _turn_routing.routing_fences_from_rules(
                consumed, local_only=fences_local_only, allow_paid=fences_allow_paid
            )
            fences_local_only = bool(fences["local_only"])
            fences_allow_paid = bool(fences["allow_paid"])
            allowed_provider_ids = fences.get("allowed_provider_ids")
            applied_rule_ids = tuple(rule.rule_id for rule in consumed)
    retry_marker = context.get(_turn_routing.TURN_ROUTING_RETRY_KEY)
    if isinstance(retry_marker, dict) and retry_marker.get("plan_id"):
        original = _turn_routing.plan_by_id(str(retry_marker["plan_id"]))
        if original is not None:
            retry_plan = _turn_routing.mint_retry_plan(
                original,
                turn_id=turn_id,
                session_id=session_id,
                conversation_ref=conversation_ref,
                manifests=manifests,
                local_only=fences_local_only,
                allow_paid=fences_allow_paid,
                allowed_provider_ids=allowed_provider_ids,
                allowed_models=allowed_models_fences,
                context_identity=f"turnctx-{turn_id}",
                context_reason="canonical_session_transcript",
            )
            with contextlib.suppress(Exception):
                _turn_routing.record_routing_provenance(retry_plan)
            return retry_plan
    try:
        plan = _turn_routing.mint_turn_routing_plan(
            turn_id=turn_id,
            session_id=session_id,
            conversation_ref=conversation_ref,
            manifests=manifests,
            requested_provider=str(requested_provider or ""),
            requested_model=str(requested_model or ""),
            local_only=fences_local_only,
            allow_paid=fences_allow_paid,
            allowed_provider_ids=allowed_provider_ids,
            allowed_models=allowed_models_fences,
            context_identity=f"turnctx-{turn_id}",
            context_reason="canonical_session_transcript",
            applied_rule_ids=applied_rule_ids,
        )
    except _turn_routing.RoutingIdentityError:
        return None
    with contextlib.suppress(Exception):
        _turn_routing.record_routing_provenance(plan)
    return plan


def _operator_cancel_marker_fired(source_context: dict[str, Any] | None) -> bool:
    """The operator's cancel marker only (Event-like `.is_set()` or a callable token); never the
    provider-call deadline, which `_cancel_check` also folds in for the streaming loop."""
    marker = (source_context or {}).get("cancel_event") or (source_context or {}).get("cancellation_token")
    if marker is None:
        return False
    try:
        is_set = getattr(marker, "is_set", None)
        if callable(is_set):
            return bool(is_set())
        return bool(marker()) if callable(marker) else False
    except Exception:
        return False


def _cancel_check(source_context: dict[str, Any] | None):
    from core.provider_call_deadline import deadline_expired

    marker = (source_context or {}).get("cancel_event") or (source_context or {}).get("cancellation_token")
    marker_check = None
    if callable(marker):
        marker_check = marker
    elif marker is not None:
        is_set = getattr(marker, "is_set", None)
        marker_check = is_set if callable(is_set) else None
    has_deadline = deadline_expired(source_context) or bool(
        (source_context or {}).get("_provider_call_deadline_monotonic")
    )
    if marker_check is None and not has_deadline:
        return None

    def _cancelled() -> bool:
        externally_cancelled = False
        if marker_check is not None:
            try:
                externally_cancelled = bool(marker_check())
            except Exception:
                externally_cancelled = False
        return externally_cancelled or deadline_expired(source_context)

    return _cancelled


def _model_call_identity(source_context: dict[str, Any] | None, *, task: Any, model_call_id: str) -> dict[str, str]:
    context = source_context or {}
    return {
        "request_id": str(context.get("request_id") or "").strip(),
        "session_id": str(context.get("runtime_session_id") or context.get("session_id") or "").strip(),
        "queue_id": str(context.get("queue_id") or "").strip(),
        "queue_item_id": str(context.get("queue_item_id") or "").strip(),
        "turn_id": str(context.get("turn_id") or "").strip(),
        "subtask_id": str(context.get("subtask_id") or "").strip(),
        "task_id": str(getattr(task, "task_id", "") or context.get("task_id") or "").strip(),
        "model_call_id": model_call_id,
        # Server-authored role for bounded internal generations.  Empty on ordinary answer calls;
        # never inferred from task_kind, because several callers share normalization_assist.
        "call_role": str(context.get("model_call_role") or "").strip(),
    }


def _interpreted_user_text(interpretation: Any, task: Any) -> str:
    """Return the user-authored turn without replaying generated reference prose."""
    return str(
        getattr(interpretation, "normalized_text", "")
        or getattr(interpretation, "raw_text", "")
        or getattr(interpretation, "reconstructed_text", "")
        or getattr(task, "task_summary", "")
        or ""
    )


def _current_user_text_from_request(request: ModelRequest) -> str:
    """Read the current user message without retaining it in trace metadata."""
    for message in reversed(list(request.messages or [])):
        if str(message.get("role") or "").lower() == "user":
            return str(message.get("content") or "")
    return str(request.prompt or "")


def _model_request_output_ceiling(request: ModelRequest) -> int | None:
    """The explicit output ceiling sealed onto this request, if one exists."""
    direct = getattr(request, "max_output_tokens", None)
    configured = dict(getattr(request, "metadata", None) or {}).get(
        "generation_profile", {}
    )
    candidate = direct
    if candidate is None and isinstance(configured, dict):
        candidate = configured.get("max_output_tokens")
    try:
        parsed = int(candidate)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _provider_response_output_ceiling(
    response: ModelResponse,
    request: ModelRequest,
) -> int | None:
    """Prefer the adapter-attested wire ceiling over the caller's pre-adapter budget."""

    value = getattr(response, "effective_max_output_tokens", None)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 0
    return parsed if parsed > 0 else _model_request_output_ceiling(request)


def _response_constraint_retry_messages(
    request: ModelRequest,
    *,
    failed_draft: str,
    instruction: str,
) -> list[dict[str, Any]]:
    """Keep a format repair scoped to the current turn and failed draft."""
    messages: list[dict[str, Any]] = []
    if request.system_prompt:
        messages.append({"role": "system", "content": request.system_prompt})
    current_user_text = _current_user_text_from_request(request).strip()
    if current_user_text:
        messages.append({"role": "user", "content": current_user_text})
    messages.extend(
        [
            {"role": "assistant", "content": str(failed_draft or "")},
            {"role": "user", "content": instruction},
        ]
    )
    return messages


def _response_constraint_guidance(constraint: Any = None) -> str:
    """Keep short shape requests grounded in the user's actual question."""
    text = (
        "First answer the user's actual question, then fit that answer to the requested output "
        "shape. For one-word or very short answers, choose the most natural ordinary word or "
        "phrase that directly answers the question; do not use a technical synonym, meta-label, "
        "or unrelated word merely to satisfy the count."
    )
    presentation_format = str(getattr(constraint, "presentation_format", "") or "")
    if presentation_format and str(getattr(constraint, "origin", "") or "") == "automatic":
        # C19 slice 2: an election-derived contract speaks honestly about who
        # asked. The election read the answer's own content, never the prompt,
        # so this guidance must never claim the user requested the shape.
        text += (
            f" An automatic presentation election chose {presentation_format} from the shape "
            f"of this answer's own content: present the answer as a {presentation_format} "
            "using only the values already in your answer; never invent a value, and keep "
            "every citation and receipt verbatim."
        )
    elif presentation_format in {"mermaid", "chart"} or getattr(constraint, "presentation_formats", ()):
        text += " " + formatting_retry_instruction(constraint)
    elif presentation_format:
        text += (
            f" The user explicitly requested a {presentation_format} presentation: present the "
            "answer as a " + presentation_format + ". Reuse only the facts already in your "
            "answer; never invent a value, and keep every citation and receipt verbatim."
        )
    return text


def _model_request_cancelled(request: Any) -> bool:
    checker = getattr(request, "is_cancelled", None)
    if not callable(checker):
        return False
    try:
        return checker() is True
    except Exception:
        return False


def _merge_provider_usage(
    first: dict[str, Any] | None,
    second: dict[str, Any] | None,
) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for key in set(first or {}) | set(second or {}):
        left = (first or {}).get(key)
        right = (second or {}).get(key)
        if (
            isinstance(left, (int, float))
            and not isinstance(left, bool)
            and isinstance(right, (int, float))
            and not isinstance(right, bool)
        ):
            merged[key] = left + right
        elif right is not None:
            merged[key] = right
        elif left is not None:
            merged[key] = left
    return merged


def _paid_call_authorization(source_context: dict[str, Any] | None, *, manifest: Any, task: Any) -> Any | None:
    authorization = (source_context or {}).get("authorized_paid_call")
    escalation = getattr(authorization, "escalation", None)
    capsule = getattr(escalation, "capsule", None)
    reservation = getattr(authorization, "reservation", None)
    if not authorization or not escalation or not capsule or not reservation:
        return None
    # The reservation names the ONE lane it was made for. A lane from another provider serving a
    # model with the same name is a different destination under different terms, so the model name
    # alone never carries the authority there; an authorization without a provider identity binds
    # nothing and authorizes nothing.
    provider_id = str(getattr(manifest, "provider_id", "") or "")
    if not provider_id or str(getattr(authorization, "provider_id", "") or "") != provider_id:
        return None
    if str(getattr(escalation, "model_id", "") or "") != str(getattr(manifest, "model_name", "") or ""):
        return None
    if str(getattr(capsule, "task_id", "") or "") != str(getattr(task, "task_id", "") or ""):
        return None
    if str(getattr(reservation, "status", "") or "") != "reserved":
        return None
    if str(getattr(reservation, "model_call_id", "") or "") != str(getattr(authorization, "model_call_id", "") or ""):
        return None
    return authorization


def _paid_terms(manifest: Any) -> tuple[str, str, str, bool]:
    """The economic terms a paid reservation is granted under, as the paid gate reads them: the route
    identity the reservation stamps (``provider_id``), the pinned model, the lane's cost class and
    whether the catalog prices the model at zero."""
    return (
        str(getattr(manifest, "provider_id", "") or ""),
        str(getattr(manifest, "model_name", "") or ""),
        provider_cost_class(manifest),
        bool(is_verified_free_cloud_manifest(manifest)),
    )


def _release_undispatched_pin_reservation(
    authorized_paid_call: Any, *, source_context: dict[str, Any] | None
) -> None:
    """Hand back a reservation that was granted but whose pinned model was never dispatched -- and
    ONLY then. A reservation the invoke loop has already terminalized (released as ``call_failed``,
    which records the unpriced attempt, or settled) is left untouched: releasing it again as
    ``pin_not_dispatched`` would run ``forget_paid_attempts`` and erase the accounting for a paid
    call that did reach the provider. Guarded on the ledger's own ``reserved`` status, so it is a
    no-op on any reservation that already moved to a terminal state.
    """
    if authorized_paid_call is None:
        return
    model_call_id = str(getattr(authorized_paid_call, "model_call_id", "") or "")
    if not model_call_id:
        return
    try:
        from core.model_spend_ledger import get_spend_reservation

        reservation = get_spend_reservation(model_call_id)
    except Exception:
        return
    if reservation is None or str(getattr(reservation, "status", "") or "") != "reserved":
        return
    release_owner_pick_paid_call(
        authorized_paid_call,
        reason="pin_not_dispatched",
        source_context=source_context,
        call_role=str((source_context or {}).get("model_call_role") or ""),
    )


def _emergency_model_can_answer(*, model_name: str, lane: str) -> bool:
    parameter_billions = model_parameter_billions(str(model_name or ""))
    return str(lane or "").strip().lower() == "tiny" or parameter_billions <= 0.0 or parameter_billions > 1.0


def _openrouter_key_present() -> bool:
    """Return whether OpenRouter has a non-empty key, without exposing the value.

    ``_resolve_key`` scans the FULL canonical alias tuple from
    ``core.cloud_providers`` (env aliases, then the credential store), so no
    local alias list is restated here.
    """
    try:
        from core.cloud_connection_state import _resolve_key

        return bool(_resolve_key("openrouter"))
    except Exception:
        return False


def _inject_free_cloud_requirements(
    source_context: dict[str, Any] | None,
    *,
    context_result: Any,
    output_mode: str,
) -> dict[str, Any] | None:
    """Build broker requirements for the real owner-local, key-present chat path.

    Tests and trusted callers may provide a richer requirement object themselves. Normal
    chat must not depend on that test-only injection, so the router supplies a conservative
    public-chat contract when the user has enabled the verified-free cloud lane.
    """
    context = dict(source_context or {})
    if "cloud_task_requirements" in context:
        return context
    policy = cloud_escalation_policy.load_policy()
    if not policy.free_cloud_enabled or not _openrouter_key_present():
        return source_context
    # Local Only has no free arm. A verified-free OpenRouter model costs nothing and still sends the
    # turn's prompt to a third party, which is the thing the mode exists to prevent — so it is
    # blocked here, at the point the broker requirements are built, rather than left to be declined
    # further down where a partially-built cloud request already exists.
    if turn_is_local_only(context):
        return source_context
    if not request_is_owner_local(context):
        return source_context
    try:
        from core.cloud_provider_contract import CloudTaskRequirements, PrivacyClass

        report = getattr(context_result, "report", None)
        token_count = 0
        if report is not None:
            total_tokens_used = getattr(report, "total_tokens_used", None)
            if callable(total_tokens_used):
                token_count = max(0, int(total_tokens_used() or 0))
        context["cloud_task_requirements"] = CloudTaskRequirements(
            min_context_tokens=token_count,
            expected_output_tokens=0,
            required_capabilities=(),
            privacy_class=PrivacyClass.PUBLIC,
            data_categories=(),
            approved_paths=(),
        )
        context["cloud_auto_source"] = "memory_first_router"
        context["cloud_output_mode"] = str(output_mode or "plain_text")
        return context
    except Exception:
        return source_context


class MemoryFirstRouter:
    def __init__(self, registry: ModelRegistry | None = None, cloud_broker: Any | None = None) -> None:
        self.registry = registry or ModelRegistry()
        self._cloud_broker = cloud_broker
        self._broker_injected = cloud_broker is not None  # an injected broker is never auto-rebuilt
        self._cloud_broker_epoch = -1  # forces a build on first use (unless one was injected)

    def _try_free_cloud_boost(
        self,
        *,
        request: ModelRequest,
        task: Any,
        task_hash: str,
        output_mode: str,
        source_context: dict[str, Any] | None,
        route_reason: str = "free_cloud_boost_after_local_failure",
    ) -> ModelExecutionDecision | None:
        from core.cloud_privacy_policy import CloudPrivacyGrant
        from core.cloud_provider_contract import CloudModelRequest, CloudTaskRequirements
        from core.cloud_routing import CloudRouteMode
        from core.cloud_tool_call_contract import build_cloud_tool_definitions
        from core.output_validator import validate_provider_output
        from core.request_trust import request_is_owner_local

        context = source_context or {}
        policy = cloud_escalation_policy.load_policy()
        requirements = context.get("cloud_task_requirements")
        grant = context.get("cloud_privacy_grant")
        # The free cloud broker is still cloud. Same rule as the requirements builder above,
        # restated at the dispatch seam so neither one alone is load-bearing. Named, not silent:
        # a turn that then ends "no lane available" has a row saying which gate kept the cloud
        # lane from even being consulted.
        skipped = (
            "free_cloud_disabled" if not policy.free_cloud_enabled
            else "turn_local_only" if turn_is_local_only(context)
            else "not_owner_local" if not request_is_owner_local(context)
            else "no_cloud_task_requirements" if not isinstance(requirements, CloudTaskRequirements)
            else "invalid_privacy_grant" if (grant is not None and not isinstance(grant, CloudPrivacyGrant))
            else ""
        )
        if skipped:
            _emit_model_routing_event(
                source_context,
                "cloud_route_declined",
                f"Cloud route not consulted: {skipped}.",
                route_reason=route_reason,
                fallback_reason=skipped,
                attachment_count=len(list(request.attachments or [])),
            )
            return None
        from core.cloud_runtime import build_default_cloud_broker, cloud_broker_epoch

        # Rebuild the cached broker when a key was added/removed since it was built (the broker
        # snapshots the key set at build time). An injected broker (tests) is never auto-rebuilt.
        if self._cloud_broker is None or (not self._broker_injected and self._cloud_broker_epoch != cloud_broker_epoch()):
            self._cloud_broker = build_default_cloud_broker()
            self._cloud_broker_epoch = cloud_broker_epoch()

        authorized_paid_call = context.get("authorized_paid_call")
        reservation = getattr(authorized_paid_call, "reservation", None)
        paid_budget = float(getattr(reservation, "reserved_usd", 0.0) or 0.0)
        mode = CloudRouteMode.LOCAL_FREE_PAID if authorized_paid_call is not None else CloudRouteMode.LOCAL_FREE
        native_tools = ()
        tool_choice = None
        if output_mode == "tool_intent":
            from core.capability_graph import model_visible_specs

            native_tools = tuple(request.tools) or build_cloud_tool_definitions(
                model_visible_specs()
            )
            tool_choice = request.tool_choice or "required"
            requirements = replace(
                requirements,
                required_capabilities=tuple(
                    dict.fromkeys((*requirements.required_capabilities, "tool_calling"))
                ),
            )
        auto_free_model = str(getattr(policy, "auto_free_model", "auto") or "auto").strip()
        preferred_auto_model = "" if auto_free_model.lower() == "auto" else auto_free_model
        # The turn's attachments ride the broker lane too. An image attachment makes image input a
        # REQUIRED capability, so the broker only ever picks a model the catalog says can read it;
        # the parts are then rendered with that guarantee. Text attachments are inlined as data and
        # join the privacy evaluation below, exactly like the prompt they accompany.
        cloud_messages = [dict(message) for message in request.messages]
        attachment_texts: tuple[str, ...] = ()
        if request.attachments:
            from core.chat_attachments import apply_to_provider_messages

            if any(str(item.get("kind") or "").lower() == "image" for item in request.attachments if isinstance(item, dict)):
                requirements = replace(
                    requirements,
                    required_capabilities=tuple(
                        dict.fromkeys((*requirements.required_capabilities, "image_input"))
                    ),
                )
            cloud_messages, cloud_delivery = apply_to_provider_messages(
                cloud_messages, request.attachments, supports_images=True
            )
            if cloud_delivery:
                request.metadata["attachment_delivery"] = cloud_delivery
            attachment_texts = tuple(
                str(item.get("text") or "")
                for item in request.attachments
                if isinstance(item, dict) and str(item.get("kind") or "").lower() == "text" and item.get("text")
            )
        cloud_request = CloudModelRequest(
            task_id=str(getattr(task, "task_id", "") or context.get("task_id") or ""),
            turn_id=str(context.get("turn_id") or ""),
            subtask_id=str(context.get("subtask_id") or ""),
            model_call_id=str(getattr(authorized_paid_call, "model_call_id", "") or ""),
            model_id=preferred_auto_model,
            messages=tuple(cloud_messages),
            max_output_tokens=max(1, int(request.max_output_tokens or requirements.expected_output_tokens or 1)),
            temperature=request.temperature,
            metadata={
                "session_id": str(
                    context.get("runtime_session_id")
                    or context.get("session_id")
                    or "local"
                ),
                "context_manifest": dict(request.context or {}),
                # A11 receipt provenance: this lane IS the Auto free-cloud boost, so
                # these are mechanically known here. Empty stays empty otherwise.
                "requested_model": preferred_auto_model,
                "selection_mode": "auto",
            },
            tools=native_tools,
            tool_choice=tool_choice,
            tools_required=(output_mode == "tool_intent"),
        )
        payload_texts = tuple(
            str(value)
            for value in (
                request.system_prompt,
                request.prompt,
                *(message.get("content") for message in request.messages),
                *attachment_texts,
            )
            if isinstance(value, str) and value
        )

        def _validate(response: Any) -> tuple[bool, str]:
            # A completion the provider ended at the ceiling is a draft cut mid-sentence, not a
            # candidate answer -- validating its bytes as prose would ship the fragment (or, on a
            # reasoning model, the half-written monologue) to the gate as if it were the reply.
            # The budget is set from the model's real capability in the broker; if it STILL ran
            # out, the fix is a higher ceiling, never a second call on the same input.
            finish_reason = str(getattr(response, "finish_reason", "") or "").strip().lower()
            if finish_reason == "length":
                usage = dict(getattr(response, "usage", None) or {})
                details = usage.get("completion_tokens_details")
                reasoning_tokens = (
                    dict(details).get("reasoning_tokens", "unreported") if isinstance(details, dict) else "unreported"
                )
                return False, (
                    "output_truncated:finish_reason=length"
                    f":completion_tokens={usage.get('completion_tokens', 'unknown')}"
                    f":reasoning_tokens={reasoning_tokens}"
                    f":max_output_tokens={getattr(response, 'effective_max_output_tokens', None) or 'unknown'}"
                )
            result = validate_provider_output(
                provider_id="cloud-broker",
                output_mode=_effective_output_mode(response, output_mode),
                raw_text=str(response.output_text or ""),
                trace_id=str(getattr(task, "task_id", "") or ""),
            )
            return result.ok, str(result.error or "")

        # The broker's calls are provider invocations of THIS turn and must be recorded on the
        # same turn ledger the adapter lane records on (`record_provider_call` at entry, its
        # outcome at exit). The ledger's entry seam also files the call on the turn's grounding
        # lifecycle with the evidence set its prompt carried -- without that record the
        # publication gate reads a bound-and-prefetched evidence set as "never reached the model
        # call that wrote the answer" and refuses a correctly grounded synthesis. Measured live
        # 2026-09-06 (private profile, real provider): Brave returned 4 sources, the binding
        # event proved prompt entry, the synthesis finished `stop`, and the gate refused at
        # bound_to_synthesis because this lane had filed no call.
        _ledger_call_ids: dict[str, str] = {}
        _terminalized_calls: list[str] = []
        _pricing_cost_class = {"free": "free_cloud", "promotional": "free_cloud", "paid": "paid_cloud"}
        def _event(event_type: str, details: dict[str, Any]) -> None:
            event_details = {"locality": "remote", "active_inference": True, **dict(details or {})}
            _emit_model_routing_event(
                source_context,
                event_type,
                f"Cloud model route {event_type.rsplit('.', 1)[-1]}.",
                **event_details,
            )
            broker_call_id = str(details.get("model_call_id") or "")
            if event_type == "model.call_started":
                ledger_call_id = record_provider_call(
                    source_context,
                    provider_id=str(details.get("provider_id") or ""),
                    model_id=str(details.get("model_id") or ""),
                    cost_class=_pricing_cost_class.get(str(details.get("pricing_state") or ""), "remote_unknown"),
                    model_call_id=broker_call_id,
                    call_role=str((source_context or {}).get("model_call_role") or ""),
                )
                if ledger_call_id:
                    _ledger_call_ids[broker_call_id] = ledger_call_id
            elif event_type in ("model.call_completed", "model.call_failed"):
                ledger_call_id = _ledger_call_ids.pop(broker_call_id, "")
                if ledger_call_id:
                    completed = event_type == "model.call_completed"
                    recorded = record_provider_call_outcome(
                        source_context,
                        ledger_call_id,
                        outcome="completed" if completed else "failed",
                        **({} if completed else {"error_class": str(details.get("reason") or "")}),
                    )
                    if not recorded:
                        _terminalized_calls.append(ledger_call_id)

        result = self._cloud_broker.execute(
            cloud_request,
            requirements=requirements,
            mode=mode,
            privacy_grant=grant,
            payload_texts=payload_texts,
            paths=requirements.approved_paths,
            authorized_paid_call=authorized_paid_call,
            paid_budget_remaining_usd=paid_budget,
            response_validator=_validate,
            event_sink=_event,
        )
        if _terminalized_calls:
            # Same law as the adapter lane: a provider call that ended after its enclosing turn
            # was terminalized may not become that turn's answer.
            raise ProviderCallAlreadyTerminalized(
                "provider call ended after its enclosing turn was terminalized"
            )
        if not result.used_cloud or result.response is None:
            # The broker's own reason for staying off the cloud (privacy, no eligible model,
            # provider exhaustion) was swallowed here, so a turn that ended "no lane available"
            # left no row saying WHY the cloud lane declined. Measured live with a pasted
            # document: three drives ended in that message with nothing in Activity to read.
            _emit_model_routing_event(
                source_context,
                "cloud_route_declined",
                f"Cloud route declined: {result.fallback_reason or 'no reason recorded'}.",
                route_reason=route_reason,
                fallback_reason=str(result.fallback_reason or ""),
                attempts=int(result.attempts or 0),
                requested_model=preferred_auto_model,
                attachment_count=len(list(request.attachments or [])),
                errors=[str(item) for item in tuple(result.errors or ())],
                error_details=[str(item) for item in tuple(getattr(result, "error_details", ()) or ())],
            )
            return None
        # The broker lane rendered the attachments itself (above); the receipt names the provider
        # and model that actually answered, from the broker's own result.
        _record_attachment_delivery(
            source_context,
            request,
            manifest=SimpleNamespace(
                provider_id=str(result.provider_id or ""),
                model_name=str(getattr(result.response, "model_name", "") or ""),
            ),
        )
        # The RESPONSE's mode, not the request's. The adapter repairs a prose tool call back into a
        # dispatchable intent, and when it does so on a turn that asked for plain text it says so by
        # flipping this field. Validating such a reply as plain text would hand the operator the
        # tool-call JSON as their answer — the failure this whole guard exists to remove.
        validation = validate_provider_output(
            provider_id=result.provider_id,
            output_mode=_effective_output_mode(result.response, output_mode),
            raw_text=result.response.output_text,
            trace_id=str(getattr(task, "task_id", "") or ""),
        )
        if not validation.ok:
            return None
        answer_text = str(validation.normalized_text or "")
        constraint_result: dict[str, Any] | None = None
        if output_mode == "plain_text" and answer_text.strip():
            # C19 on THIS lane, through the same two authorities the adapter lane uses: the
            # automatic selector records its provenance (standing down when the turn carries an
            # explicit contract), then the explicit contract -- parsed once by _build_request into
            # request.metadata -- is enforced deterministically. No model retry here: the bounded
            # repair call is a free-local privilege by law, so a cloud answer that misses the
            # requested shape lands on the same honest fallback the adapter lane uses.
            request_metadata = dict(request.metadata or {})
            response_constraint = response_constraint_from_metadata(request_metadata)
            if request_metadata.get("x_editorial_turn"):
                response_constraint = None
            _select_and_record_presentation(
                answer_text,
                _presentation_selection_context(source_context, request_metadata),
                source_context,
            )
            if response_constraint is not None:
                applied = enforce_response_constraint(answer_text, response_constraint)
                remaining_violations = list(applied.violations)
                if short_answer_needs_grounding_retry(
                    applied.text,
                    response_constraint,
                    _current_user_text_from_request(request),
                ):
                    remaining_violations.append("low_information_short_answer")
                constraint_compliant = applied.compliant and not any(
                    violation == "low_information_short_answer" for violation in remaining_violations
                )
                answer_text = applied.text
                constraint_result = {
                    "requested": response_constraint.to_dict(),
                    "lane": "cloud_broker",
                    "retry_attempted": False,
                    "structurally_trimmed": applied.structurally_trimmed,
                    "compliant": constraint_compliant,
                    "remaining_violations": remaining_violations,
                }
                if not constraint_compliant:
                    answer_text = constraint_safe_fallback(response_constraint)
                    constraint_result["fallback_applied"] = True
                _emit_model_routing_event(
                    source_context,
                    "model.response_constraint_enforced",
                    "Cloud model answer checked against the requested response shape.",
                    provider_id=str(result.provider_id or ""),
                    model_id=str(result.model_id or ""),
                    requested_format=str(response_constraint.presentation_format or ""),
                    compliant=constraint_compliant,
                    violations=remaining_violations,
                    fallback_applied=not constraint_compliant,
                )
        # The single normalized boundary both response types convert through (System A goes
        # through the identical function in _decision_from_response). `.tool_calls` below is
        # sourced from THIS, not re-derived by hand a second time -- that hand-derivation is
        # exactly what silently dropped call #2 once before (see core/normalized_provider_result.py).
        normalized = normalize_cloud_model_response(
            result.response,
            requested_model=str(context.get("requested_model") or "").strip(),
            resolved_provider=result.provider_id,
            resolved_model=result.model_id,
            actual_provider=result.provider_id,
            actual_model=result.model_id,
            provider_request_id=result.model_call_id,
        )
        # This lane used to build its ModelExecutionDecision without ever recording usage --
        # _record_response_usage was called only from the System A path (_decision_from_response),
        # so every free-cloud-boost response was invisible to the usage_meter ledger and the
        # Activity token/cost totals. Recorded here from the SAME normalized fields the decision
        # itself uses, so "what the decision says happened" and "what the ledger recorded" cannot
        # independently drift.
        cost_class = {
            "free": usage_meter.COST_FREE_CLOUD,
            "promotional": usage_meter.COST_FREE_CLOUD,
            "paid": usage_meter.COST_PAID_CLOUD,
        }.get(str(result.pricing_state or "").strip().lower(), usage_meter.COST_REMOTE_UNKNOWN)
        usage_meter.record_usage(
            provider_id=result.provider_id,
            model_id=result.model_id,
            cost_class=cost_class,
            prompt_tokens=normalized.usage_input,
            output_tokens=normalized.usage_output,
            usd_actual=float(result.actual_usd or 0.0) if result.actual_usd else None,
            prompt_tokens_reported=normalized.usage_input_reported,
            output_tokens_reported=normalized.usage_output_reported,
        )
        return ModelExecutionDecision(
            source="free_cloud_boost",
            task_hash=task_hash,
            provider_id=result.provider_id,
            provider_name=result.provider_id,
            model_name=result.model_id,
            output_text=answer_text,
            structured_output=validation.structured_output,
            confidence=0.5,
            trust_score=0.5,
            used_model=True,
            failover_used=True,
            validation_state="valid",
            tool_calls=normalized.tool_calls,
            details={
                "model_call_id": result.model_call_id,
                "pricing_state": result.pricing_state,
                "actual_usd": result.actual_usd,
                "attempts": result.attempts,
                "route_reason": route_reason,
                "locality": "remote",
                "active_inference": True,
                "usage_input_reported": normalized.usage_input_reported,
                "usage_output_reported": normalized.usage_output_reported,
                "finish_reason": str(getattr(result.response, "finish_reason", "") or ""),
                **({"constraint_result": constraint_result} if constraint_result else {}),
            },
        )

    def resolve(
        self,
        *,
        task: Any,
        classification: dict[str, Any],
        interpretation: Any,
        context_result: Any,
        persona: Any,
        force_model: bool = False,
        allow_provider_inference: bool = True,
        surface: str = "cli",
        source_context: dict[str, Any] | None = None,
    ) -> ModelExecutionDecision:
        effective_force_model = bool(force_model) and bool(allow_provider_inference)
        force_model = _force_model_on_chat_surface(
            force_model=effective_force_model,
            surface=surface,
            source_context=source_context,
        )
        # A chat-surface turn is free-form prose. Derive the execution profile with the same
        # chat-surface awareness model_routing_profile already applied upstream, so a plain
        # conversational/creative answer is validated as plain_text and not against a JSON
        # action_plan/summary_block contract. Without this, resolve() re-derived the profile from
        # the base mapping (chat_surface=False), so a class such as system_design / debugging /
        # config / dependency_resolution / file_inspection / shell_guidance demanded JSON and marked
        # normal prose contract_failed ("I couldn't get a usable model response"). An explicit
        # planner-style request still keeps the structured contract; non-chat (CLI/worker) surfaces
        # and tool_intent are unaffected.
        chat_surface = (
            str(surface or "").strip().lower() in _CHAT_TRUTH_SURFACES
            or str((source_context or {}).get("surface", "") or "").strip().lower() in _CHAT_TRUTH_SURFACES
        )
        profile = model_execution_profile(
            str(classification.get("task_class", "unknown")),
            chat_surface=chat_surface,
            planner_style_requested=bool(classification.get("planner_style_requested", False)),
        )
        task_kind = str(profile["task_kind"])
        output_mode = str(profile["output_mode"])
        normalized_input = _interpreted_user_text(interpretation, task)
        cache_scope = _candidate_cache_scope(source_context)
        task_hash = build_task_hash(
            normalized_input=normalized_input,
            task_class=str(classification.get("task_class", "unknown")),
            output_mode=output_mode,
            scope_id=cache_scope,
        )

        if not force_model:
            if cache_scope:
                cached = get_exact_candidate(task_hash, output_mode=output_mode)
                if cached and not should_revalidate(cached) and float(cached.get("trust_score") or 0.0) >= 0.56:
                    # `tool_calls` stays empty here ON PURPOSE. The candidate row stores
                    # `structured_output` and has no call-shaped column, and persisting the batch
                    # would be worse than omitting it: `build_task_hash` keys on
                    # scope + task_class + output_mode + normalized_input, so the same phrasing
                    # would replay a stored batch of tool calls THIS turn's model never chose.
                    # A replayed decision therefore reports one call, which is what it can honestly
                    # account for.
                    return ModelExecutionDecision(
                        source="exact_cache_hit",
                        task_hash=task_hash,
                        provider_id=f"{cached['provider_name']}:{cached['model_name']}",
                        provider_name=cached["provider_name"],
                        model_name=cached["model_name"],
                        output_text=str(cached.get("normalized_output") or ""),
                        structured_output=cached.get("structured_output"),
                        confidence=float(cached.get("confidence") or 0.0),
                        trust_score=float(cached.get("trust_score") or 0.0),
                        cache_hit=True,
                        used_model=False,
                        candidate_id=cached["candidate_id"],
                        validation_state=str(cached.get("validation_state") or "cached"),
                        details={"reason": "fresh_exact_candidate_cache"},
                    )

            if _memory_is_good_enough(context_result, classification):
                return ModelExecutionDecision(
                    source="memory_hit",
                    task_hash=task_hash,
                    used_model=False,
                    details={"reason": "relevant_local_memory_sufficient"},
                )

            if not allow_provider_inference:
                return ModelExecutionDecision(
                    source="no_cached_or_memory_answer",
                    task_hash=task_hash,
                    used_model=False,
                    details={"reason": "provider_inference_disabled"},
                )

        routed_context = _inject_free_cloud_requirements(
            source_context,
            context_result=context_result,
            output_mode=output_mode,
        )
        return self._execute_provider_task(
            task=task,
            classification=classification,
            interpretation=interpretation,
            context_result=context_result,
            persona=persona,
            task_hash=task_hash,
            task_kind=task_kind,
            output_mode=output_mode,
            allow_paid_fallback=bool(profile.get("allow_paid_fallback", False)),
            provider_role=_provider_role_for_request(profile.get("provider_role")),
            surface=surface,
            source_context=routed_context,
        )

    @staticmethod
    def tool_intent_task_hash(
        *,
        task: Any,
        classification: dict[str, Any],
        interpretation: Any,
        source_context: dict[str, Any] | None = None,
    ) -> str:
        """The cache key `resolve_tool_intent` will use for this turn.

        Exposed so a caller that discovers the turn FAILED can invalidate the decision that
        produced it. Without a way to name the key, a well-formed but wrong tool choice is served
        from cache forever — measured on the deployed build 2026-07-28.
        """

        normalized_input = _interpreted_user_text(interpretation, task)
        return build_task_hash(
            normalized_input=normalized_input,
            task_class=str(classification.get("task_class", "unknown")),
            output_mode="tool_intent",
            scope_id=_candidate_cache_scope(source_context),
        )

    def resolve_tool_intent(
        self,
        *,
        task: Any,
        classification: dict[str, Any],
        interpretation: Any,
        context_result: Any,
        persona: Any,
        surface: str = "cli",
        source_context: dict[str, Any] | None = None,
    ) -> ModelExecutionDecision:
        task_hash = self.tool_intent_task_hash(
            task=task,
            classification=classification,
            interpretation=interpretation,
            source_context=source_context,
        )
        routed_context = _inject_free_cloud_requirements(
            source_context,
            context_result=context_result,
            output_mode="tool_intent",
        )
        return self._execute_provider_task(
            task=task,
            classification=classification,
            interpretation=interpretation,
            context_result=context_result,
            persona=persona,
            task_hash=task_hash,
            task_kind="tool_intent",
            output_mode="tool_intent",
            allow_paid_fallback=False,
            provider_role="drone",
            surface=surface,
            source_context=routed_context,
        )

    def _build_round_request(
        self,
        *,
        task: Any,
        classification: dict[str, Any],
        interpretation: Any,
        context_result: Any,
        persona: Any,
        output_mode: str,
        task_kind: str,
        surface: str,
        source_context: dict[str, Any] | None,
    ) -> ModelRequest:
        """One model round's request, as _execute_provider_task sends it.

        In tool_intent mode the round's offer is materialized once, before the prompt is built, and rendered
        twice from that same object: as the prompt's text catalog and as the native tool definitions.
        """
        round_offer = None
        round_family_hint = None
        if output_mode == "tool_intent":
            from core.capability_graph import family_hint_from_task_class
            from core.execution_requirements import requirements_for
            from core.tool_offer_assembly import assemble_tool_offer
            from core.tool_offer_state import followup_inherited_families

            round_family_hint = family_hint_from_task_class(
                str(classification.get("task_class", "") or "")
            )
            user_text_for_offer = _interpreted_user_text(interpretation, task)
            tool_requirements = requirements_for(
                user_text_for_offer,
                task_class=str(classification.get("task_class") or "unknown"),
                source_context=source_context,
            )
            # ONE offer per model round (core.tool_offer_assembly), materialized here BEFORE the
            # prompt is built and rendered from this same object twice: as the prompt's text catalog
            # (normalize_prompt, via _build_request below) and as the native tool definitions. One
            # build means one read of the turn's navigation state, one policy/availability pass and
            # one skill match, so the tools the model reads about are the tools it can call. The
            # next round builds its own offer: a same-turn `capability.expand_family` stays seated
            # while policy and availability are re-read every round.
            round_offer = assemble_tool_offer(
                family_hint=round_family_hint,
                # Native skill selection is typed on the SAME classification the
                # family hint came from (core.native_skill_library).
                task_class=str(classification.get("task_class") or ""),
                # A contextual follow-up ("so?") classifies as plain conversation
                # with no family of its own; it inherits the previous turn's
                # families instead of dropping to nothing (census: follow-ups
                # carried zero tools). Only follow-up-shaped text inherits — a
                # new demand resolves its own families.
                family_hints=followup_inherited_families(user_text_for_offer, source_context),
                toolset_hints=tuple(
                    str(h).strip() for h in tool_requirements.allowed_toolsets
                    if isinstance(h, str)
                ),
                # The offer is adaptive per round: the user's own words carry
                # explicit intents and every required family (mixed demands),
                # and a same-turn `capability.expand_family` call reseats the
                # family it named for THIS round. Still bounded at 8 seats.
                user_text=user_text_for_offer,
                source_context=source_context,
            )
        request = self._build_request(
            task=task,
            classification=classification,
            interpretation=interpretation,
            context_result=context_result,
            persona=persona,
            output_mode=output_mode,
            task_kind=task_kind,
            surface=surface,
            source_context=source_context,
            tool_offer=round_offer,
        )
        if output_mode == "tool_intent":
            from core.cloud_tool_call_contract import build_cloud_tool_definitions
            from core.tool_offer_state import note_family_offer

            offer = round_offer
            offer_specs = offer.specs
            if offer.skill_guidance.skills:
                # Audit trail: exactly which skills entered this turn's context, with their
                # origin and version, so an executed turn can be answered for its guidance.
                from core.tool_offer_assembly import (
                    skill_provenance_rows as _skill_provenance_rows,
                )

                emit_runtime_event(
                    source_context if isinstance(source_context, dict) else {},
                    event_type="tool_offer_skills",
                    message="skill guidance joined the turn's context",
                    details={
                        "skills": _skill_provenance_rows(offer.skill_guidance.skills)
                    },
                )
            request.tools = build_cloud_tool_definitions(offer_specs)
            request.tool_choice = "required"
            # "required", not "auto" below: only a tool_intent turn's contract is non-negotiable --
            # the plain_text/auto catalog is a capability offer, not a requirement, and must not
            # trip the final pre-invocation gate just because a conversational turn didn't call one.
            request.tools_required = True
            # Which offer this request carries: the same digest the prompt catalog's offer has.
            request.metadata["tool_offer_fingerprint"] = offer.fingerprint
            # A later contextual follow-up in the same session inherits this
            # turn's families instead of receiving zero tools.
            note_family_offer(
                str(
                    (source_context or {}).get("runtime_session_id")
                    or (source_context or {}).get("session_id")
                    or ""
                ),
                (round_family_hint,) if round_family_hint else (),
            )
        elif flag_enabled("plain_text_tool_catalog"):
            # A capable model gets the catalog on an ORDINARY turn too, with tool_choice "auto" so it
            # may call a tool rather than must. Measured 2026-07-29: a contextual follow-up ("so?",
            # "audit the skills in there") classifies as plain_text, which supplied NO tools, and the
            # cloud model then invented a call shape of its own — {"tool":"workspace","action":
            # "list","path":"."} — which the runtime correctly refused. Refusing an invented shape is
            # right; sending the turn with no tools to invent from is what produced it.
            #
            # This is the capability rule the product is built on: the tools are VOOL's and every
            # model gets them. Selecting a cloud model changes who reasons, not what VOOL can do.
            #
            # "auto" not "required": a plain_text turn is usually conversation, and forcing a call
            # would make every greeting reach for a tool.
            from core.capability_graph import family_hint_from_task_class, model_visible_specs
            from core.cloud_tool_call_contract import build_cloud_tool_definitions

            family_hint = family_hint_from_task_class(
                str(classification.get("task_class", "") or "")
            )
            request.tools = build_cloud_tool_definitions(
                model_visible_specs(
                    family_hint=family_hint,
                )
            )
            request.tool_choice = "auto"
        return request

    def _build_request(
        self,
        *,
        task: Any,
        classification: dict[str, Any],
        interpretation: Any,
        context_result: Any,
        persona: Any,
        output_mode: str,
        task_kind: str,
        surface: str,
        source_context: dict[str, Any] | None,
        tool_offer: Any | None = None,
    ) -> ModelRequest:
        internal_request = normalize_prompt(
            task=task,
            classification=classification,
            interpretation=interpretation,
            context_result=context_result,
            persona=persona,
            output_mode=output_mode,
            task_kind=task_kind,
            trace_id=str(getattr(task, "task_id", "")),
            surface=surface,
            source_context=source_context,
            tool_offer=tool_offer,
        )
        response_constraint = (
            parse_response_constraint(
                str(
                    getattr(interpretation, "raw_text", "")
                    or _interpreted_user_text(interpretation, task)
                )
            )
            if output_mode == "plain_text"
            else None
        )
        structured_contract_payload = dict(
            (source_context or {}).get("raw_output_contract") or {}
        )
        structured_labels = tuple(
            str(label)
            for label in structured_contract_payload.get("structured_labels") or ()
            if str(label).strip()
        )
        if structured_labels and output_mode == "plain_text":
            # The source-context contract was derived from the authoritative raw turn before any
            # normalization. Some downstream interpretation objects carry only the collapsed
            # effective text, where "single words" looks like a global one-word limit. Never let a
            # lossy reparse override the already-bound row structure.
            from core.turn_ir import ResponseConstraint

            response_constraint = ResponseConstraint(
                list_items=len(structured_labels),
                one_item_per_line=True,
            )
        raw_output_contract = (
            parse_raw_output_contract(
                str(
                    getattr(interpretation, "raw_text", "")
                    or _interpreted_user_text(interpretation, task)
                )
            )
            if output_mode == "plain_text"
            else None
        )
        if structured_labels and output_mode == "plain_text":
            # Same authority rule as the response shape above. A normalized interpretation no
            # longer contains the line-leading markers or trailing empty scaffold, so reparsing it
            # cannot reconstruct the batch contract. Restore the already-derived typed metadata;
            # otherwise only the looser list constraint retries and the final A/B/C binder never
            # gets a chance to repair missing labels.
            raw_output_contract = raw_output_contract_from_metadata(
                {"raw_output_contract": structured_contract_payload}
            )
        prompt_profile = str(
            dict(internal_request.metadata or {}).get("system_prompt_profile")
            or "unknown"
        )
        # One canonical typed derivation: explicit output request > scoped operator preference
        # > sufficiently evidenced input language. Identity comes from the request's own
        # principal/session resolution, never from caller-supplied metadata.
        from core.operator_profile import principal_for_request

        language_policy = (
            response_language_policy_for_text(
                str(
                    getattr(interpretation, "raw_text", "")
                    or getattr(interpretation, "user_text", "")
                    or getattr(interpretation, "normalized_text", "")
                    or _interpreted_user_text(interpretation, task)
                ),
                principal=principal_for_request(source_context),
                session_id=str(
                    (source_context or {}).get("session_id")
                    or (source_context or {}).get("runtime_session_id")
                    or getattr(task, "session_id", "")
                    or ""
                ),
            ).to_dict()
            if output_mode == "plain_text"
            else {}
        )
        if isinstance(source_context, dict):
            source_context["response_language_policy"] = language_policy
        chat_truth = dict(
            dict(internal_request.metadata or {}).get("chat_truth_prompt") or {}
        )
        current_user_text = str(
            getattr(interpretation, "raw_text", "")
            or getattr(interpretation, "user_text", "")
            or getattr(interpretation, "normalized_text", "")
            or _interpreted_user_text(interpretation, task)
        )
        output_policy = ordinary_chat_output_policy(
            prompt_profile=prompt_profile,
            output_mode=output_mode,
            creative_medium=chat_truth.get("creative_medium"),
            user_text=current_user_text,
            prior_turn_literal_hashes=prior_turn_literal_hashes(
                [
                    {"role": message.role, "content": message.content}
                    for message in getattr(internal_request, "messages", ())
                    if message.role == "user"
                ],
                current_user_text=current_user_text,
            ),
            forgotten_prior_turn_literal_hashes=forgotten_prior_turn_literal_hashes(
                [
                    {"role": message.role, "content": message.content}
                    for message in internal_request.messages
                    if message.role == "user"
                ],
            ),
        )
        if isinstance(source_context, dict):
            source_context["ordinary_chat_output_policy"] = output_policy
        system_prompt = internal_request.system_prompt()
        # ---- VOOL School assistance directive (machine-stamped) ------------
        # Generated from the FROZEN school policy on the context, never from
        # conversation text; the publication gate below this prompt layer is
        # the mechanical backstop. Fail-soft: a school-context read failure
        # never costs the turn its prompt.
        try:
            _school_ctx_directive = (source_context or {}).get("school_policy")
            _school_policy_directive = (
                (_school_ctx_directive or {}).get("policy") or {}
                if isinstance(_school_ctx_directive, dict)
                else {}
            )
            if isinstance(_school_policy_directive, dict) and _school_policy_directive:
                from core.school.assistance import directive_block

                _school_directive = directive_block(
                    int(_school_policy_directive.get("max_assistance") or 10),
                    assessment=bool(_school_policy_directive.get("assessment")),
                )
                if _school_directive:
                    system_prompt = f"{system_prompt}\n\n{_school_directive}"
        except Exception:
            pass
        if response_constraint is not None:
            system_prompt = (
                f"{system_prompt}\n\n{_response_constraint_guidance(response_constraint)}"
            )
        if raw_output_contract is not None:
            system_prompt = f"{system_prompt}\n\n{raw_output_contract_guidance(raw_output_contract)}"
        # C18 PROVIDER-BOUND POLICY: a typed non-English expectation states itself on the FIRST
        # provider call, in the same bounded guidance channel as the contracts above — the
        # output-language decision must reach the wire, not only the post-hoc validator. English
        # keeps its proven post-hoc guard + one bounded repair and adds no prompt bytes.
        _language_provider_instruction = response_language_provider_instruction(language_policy)
        if _language_provider_instruction:
            system_prompt = f"{system_prompt}\n\n{_language_provider_instruction}"
        # PB01 hook 1 — LEARNED GUIDANCE DELIVERY: the validated steps/preconditions of the
        # turn's ranked reusable procedures ride the provider system prompt here, in the same
        # bounded channel the response constraints use. Marked `learned_guidance` with
        # procedure_id provenance; the block itself states it is data, not permission. Fail-soft
        # by design: a learning-store failure must never cost the turn its prompt.
        try:
            from core.learning_integration import (
                bounded_guidance_entries,
                learned_guidance_prompt_block,
            )

            _envelope_inputs = dict(
                dict((source_context or {}).get("task_envelope") or {}).get("inputs") or {}
            )
            _reused = list(_envelope_inputs.get("reused_procedures") or [])
            if not _reused:
                # The tool-intent lane carries no routing envelope on its context; derive the
                # same ranked procedures the router itself would, from the same inputs — one
                # ranker, one relevance law, both lanes.
                from core.task_router import _reused_procedure_inputs

                _reused = list(
                    _reused_procedure_inputs(
                        task_class=str(
                            (source_context or {}).get("task_class")
                            or classification.get("task_class")
                            or ""
                        ),
                        user_input=current_user_text,
                    ).get("reused_procedures")
                    or []
                )
            _guidance_block = learned_guidance_prompt_block(bounded_guidance_entries(_reused))
            if _guidance_block:
                system_prompt = f"{system_prompt}\n\n{_guidance_block}"
                with contextlib.suppress(Exception):
                    from core.runtime_task_events import emit_runtime_event as _emit_learning

                    _emit_learning(
                        source_context,
                        event_type="learning_guidance_delivered",
                        message="Learned procedure guidance delivered to the provider context.",
                        details={
                            "procedures": [
                                str(item.get("procedure_id") or "")
                                for item in bounded_guidance_entries(_reused)
                            ],
                            "source": "routing_envelope" if _envelope_inputs.get("reused_procedures") else "ranker",
                            "task_kind": str(task_kind or ""),
                        },
                    )
        except Exception:
            pass
        provider_messages = internal_request.as_openai_messages()
        # The wire carries the MESSAGES, and the chat lane always arrives with them built — a
        # system prompt appended above (response constraints OR learned guidance) must also be
        # written back into the system message, or the model never sees it.
        if system_prompt != internal_request.system_prompt():
            for index, message in enumerate(provider_messages):
                if str(message.get("role") or "").lower() == "system":
                    provider_messages[index] = {
                        **message,
                        "content": system_prompt,
                    }
                    break
        provider_prompt = internal_request.user_prompt()
        # A9 CURRENT-TURN PAYLOAD LAW: what reaches the provider as the current user message must
        # be the literal turn bytes wherever an authoritative raw form exists — literal
        # identifiers, URLs and quoted text own this turn, not a normalized paraphrase of them.
        # History/context keep their bounded representations; only the final user message is
        # restored, mirroring the structured-batch restoration below and at :1344.
        raw_current_turn = str(getattr(interpretation, "raw_text", "") or "").strip()
        if raw_current_turn:
            for index in range(len(provider_messages) - 1, -1, -1):
                if str(provider_messages[index].get("role") or "").casefold() == "user":
                    if str(provider_messages[index].get("content") or "") != raw_current_turn:
                        provider_messages[index] = {
                            **provider_messages[index],
                            "content": raw_current_turn,
                        }
                    break
            provider_prompt = raw_current_turn
        if raw_output_contract is not None and raw_output_contract.structured_labels:
            # Marker shape and newlines are semantic input for a structured batch. The ordinary
            # normalized prompt collapses them before the provider sees them, undoing the parser's
            # span-preserving work. Replace only the current (last) user message; history and
            # context keep their existing bounded representations.
            raw_batch_prompt = str(getattr(interpretation, "raw_text", "") or "")
            for index in range(len(provider_messages) - 1, -1, -1):
                if str(provider_messages[index].get("role") or "").casefold() == "user":
                    provider_messages[index] = {
                        **provider_messages[index],
                        "content": raw_batch_prompt,
                    }
                    break
            provider_prompt = raw_batch_prompt
        return ModelRequest(
            task_kind=task_kind,
            prompt=provider_prompt,
            system_prompt=system_prompt,
            context=internal_request.context_summary,
            temperature=internal_request.temperature,
            max_output_tokens=internal_request.max_output_tokens,
            messages=provider_messages,
            output_mode=output_mode,
            # A `tool_intent` call must return one structured artifact: which tool, which
            # arguments. Chain-of-thought spent inside its output budget is the artifact not
            # arriving, exactly the property `reasoning_mode` exists for (core/model_request_policy.py)
            # -- the same class of defect stepped_audit.py already measured and fixed for its own
            # bounded calls (8x slower, 0 usable tokens, with thinking on). Measured live
            # 2026-08-03/04: a plain tool-selection call to qwen3:8b against a realistic multi-file
            # workspace prompt timed out at the 60s read timeout on 3 of 3 consecutive drives, warm
            # model included, because thinking was never turned off for this output_mode -- zero
            # tool calls ever executed, and the turn silently fell back to a tool-less plain-text
            # answer. Keyed on output_mode, a property of THIS call, not on task_kind or task_class
            # (the exact keying stepped_audit.py's own comment warns against, because it would flip
            # thinking off for ordinary chat too).
            reasoning_mode=REASONING_DISABLED if output_mode == "tool_intent" else REASONING_AUTO,
            trace_id=internal_request.trace_id,
            contract={
                "mode": output_mode,
                **(
                    {"json_schema": json_schema_for_mode(output_mode)}
                    if json_schema_for_mode(output_mode) is not None
                    else {}
                ),
            },
            metadata={
                **dict(internal_request.metadata or {}),
                # Which turn this is, for the one adapter decision that depends on it. An audit
                # carries its whole evidence report in the prompt, so a local thinking model spends
                # its entire read timeout reasoning before it starts answering -- measured, that is
                # what timed out qwen3:8b and qwen3:14b at 180s on three consecutive audits.
                **audit_turn_metadata(source_context),
                **(
                    {
                        "response_constraint": response_constraint.to_dict(),
                        # Do not stream a shape-constrained answer before the
                        # runtime has validated and, at most once, corrected it.
                        "defer_stream_until_verified": True,
                    }
                    if response_constraint is not None
                    else {}
                ),
                **(
                    {
                        "raw_output_contract": raw_output_contract.to_dict(),
                        "defer_stream_until_verified": True,
                    }
                    if raw_output_contract is not None
                    else {}
                ),
                "ordinary_chat_output_policy": output_policy,
                "response_language_policy": language_policy,
                # Ordinary chat can require a bounded repair after the first
                # provider draft. Do not paint unretractable tokens before
                # that final validation has finished. An X-editorial turn is
                # the same: the response edge validates the XDraft envelope
                # and must not stream past it.
                "defer_stream_until_verified": bool(response_constraint) or bool(raw_output_contract) or (
                    output_policy.get("mode") == "ordinary_chat"
                ) or bool(dict(internal_request.metadata or {}).get("x_editorial_turn")),
                **({"task_envelope": dict((source_context or {}).get("task_envelope") or {})} if (source_context or {}).get("task_envelope") else {}),
                **({"task_role": str((source_context or {}).get("task_role") or "")} if (source_context or {}).get("task_role") else {}),
            },
            # The turn's staged attachments, read from the ONE authority by the turn id the ingress
            # stamped -- never from a client-supplied item, never from a path. Empty for a text-only
            # turn, which keeps every adapter payload byte-identical to a build without attachments.
            attachments=list(internal_request.attachments or [])
            + chat_attachments.model_attachments_from_source_context(
                source_context, question=_interpreted_user_text(interpretation, task)
            ),
            cancel_check=_cancel_check(source_context),
        )

    def _resolve_dispatch_manifest(self, manifest: Any) -> Any | None:
        """The manifest that will ACTUALLY be dispatched, resolved BEFORE any eligibility decision
        (revision-5 review R2; replaces the post-gate re-resolution added for F3).

        Every pre-call gate — Local Only, the turn's routing plan, final-answer authorship, the paid
        reservation — decides on the manifest it is shown. Deciding on a lane whose frozen destination
        the credential transaction has since replaced, and only then re-registering and dispatching the
        fresh lane, let the destination actually called skip every one of those decisions.

        Only a lane whose destination is the store-committed custom pair can go stale; every other
        manifest is its own dispatch target and returns unchanged with no I/O and no adapter, so a
        candidate the gates refuse still costs nothing. For a committed-pair lane the committed
        destination is read from the ONE pair authority; a stale freeze re-registers through the
        existing BYOK registrar and the fresh manifest is returned for the gates to decide —
        registration is not authorization. None means the lane cannot be coherently resolved right now
        (an unresolved, unreadable or incoherent pair, or no registered lane at the committed
        destination); the caller then refuses it as stale without contacting anything."""
        from core.cloud_providers import manifest_uses_committed_custom_pair, resolved_custom_pair

        if not manifest_uses_committed_custom_pair(manifest):
            return manifest
        try:
            committed_base = str(resolved_custom_pair()[0] or "").strip().rstrip("/")
        except Exception:
            return None
        if not committed_base:
            return None
        frozen_base = str((getattr(manifest, "runtime_config", None) or {}).get("base_url") or "").strip().rstrip("/")
        if frozen_base == committed_base:
            return manifest
        provider_name = str(getattr(manifest, "provider_name", "") or "")
        model_name = str(getattr(manifest, "model_name", "") or "")
        try:
            from core.runtime_provider_defaults import activate_provider_byok

            activate_provider_byok(provider_name.removesuffix("-byok"))
        except Exception:
            return None
        fresh = self.registry.get_manifest(provider_name, model_name)
        if fresh is None or not getattr(fresh, "enabled", False):
            return None
        fresh_base = str((getattr(fresh, "runtime_config", None) or {}).get("base_url") or "").strip().rstrip("/")
        if fresh_base != committed_base:
            # The registrar did not produce a lane at the committed destination (for example the
            # held model is not the provider's active model): nothing coherent to dispatch.
            return None
        return fresh

    @staticmethod
    def _dispatch_binding_is_current(*, adapter: Any, manifest: Any) -> bool:
        """No-network preflight of the binding the gates decided on, right before dispatch. False when
        the committed pair no longer matches the lane (a Save raced this call) or cannot be admitted."""
        from core.cloud_providers import manifest_uses_committed_custom_pair

        if not manifest_uses_committed_custom_pair(manifest):
            return True
        verifier = getattr(adapter, "verify_dispatch_binding", None)
        if verifier is None:
            return True
        try:
            verifier()
        except Exception:
            return False
        return True

    def _invoke_manifest(
        self,
        *,
        manifest: Any,
        request: ModelRequest,
        output_mode: str,
        task: Any,
        source_context: dict[str, Any] | None,
        lane_receipted: bool = False,
        task_kind: str = "",
    ) -> tuple[ModelAdapter | None, ModelResponse | None, str | None]:
        # `task_kind` is the ModelSelectionRequest's kind the routing request ranked this manifest
        # under. It rides the completed-call event so the model-sufficiency writer at turn
        # finalize keys the observation by the kind the selection actually used — never inferred
        # after the fact. Callers that did not run a selection (legacy lanes) leave it empty and
        # the observation stays `unknown`: inspectable, never eligible for ranking.
        from core.paid_call_reservation import _int_attr  # local, as the retry path already does
        # THE local-only backstop, and the reason it sits here rather than only in ranking. Ranking
        # is one of several ways a manifest reaches a provider: the escalation path, the local/remote
        # race in `_maybe_mux_manifests`, the conductor and the tool planner all arrive at this
        # method, and they arrive on worker threads. `source_context` is the thing that crosses that
        # hop (it is a stamped dict, not a ContextVar), so this is the one seam that sees every
        # invocation with the mode still attached.
        #
        # Checked BEFORE the paid gate, because a verified-free cloud manifest is exempt from that
        # gate and would otherwise walk straight through: Local Only is about where the call goes,
        # not what it costs.
        if _operator_cancel_marker_fired(source_context):
            # The operator cancelled the turn: no provider call starts after that. The streaming
            # loop already honoured the marker mid-call; a call that had not begun did not consult
            # it (measured 2026-09-06: a cancel acknowledged during retrieval was followed by the
            # model call and a published answer). Only the operator's marker counts here -- an
            # expired provider deadline is the failover budget's business, not a cancellation.
            return None, None, "turn_cancelled"
        # DISPATCH RESOLUTION FIRST (revision-5 review R2). Every gate below decides on the manifest it
        # is shown, so it is shown the lane that will actually be dispatched: a caller holding a lane
        # whose frozen destination the credential transaction has since replaced now meets exactly the
        # gates a direct invocation of the current lane meets. No adapter is built and nothing is read
        # for a manifest whose destination cannot go stale. A lane that cannot be resolved meets the
        # gates as given and then refuses as stale below, before any request.
        requested_manifest = manifest
        resolved_manifest = self._resolve_dispatch_manifest(manifest)
        dispatch_resolved = resolved_manifest is not None
        if dispatch_resolved:
            manifest = resolved_manifest
        try:
            assert_manifest_allowed(manifest, source_context=source_context)
        except CloudEgressBlockedError:
            return None, None, "auto_local_only_blocked_cloud_manifest"
        # A9 P0 — the broker side of the ONE eligibility decision. The plan minted before
        # ranking is enforced here, at the single seam every provider call crosses (ranking,
        # escalation, the mux/race, the verifier and the tool planner all arrive through this
        # method), BEFORE an adapter is built or a health check runs: a candidate the plan
        # refuses costs nothing. No plan in the context is a legacy caller and passes — the
        # fence only ever binds turns that minted one.
        _routing_plan_refusal = _turn_routing.broker_refusal_for_manifest(manifest, source_context)
        if _routing_plan_refusal:
            return None, None, _routing_plan_refusal
        # THE AUTHORSHIP FENCE, and the reason it is HERE and not only at publication.
        # `core.final_answer_authorship` already refuses uncertified bytes at
        # `core.finalization`, which is correct and stays. But refusing there means the call was
        # made: the input is spent, the wall clock is spent, and a full answer is generated in
        # order to be thrown away. So the same authority is asked BEFORE the adapter is built --
        # at this seam, for the same reason the Local Only backstop above is here, because
        # ranking, escalation, the local/remote race, the conductor and the tool planner all
        # arrive through this one method, several of them on worker threads.
        #
        # One candidate at a time, deliberately: refusing this one returns a typed error and the
        # caller's ranked loop tries the next, which IS the escalation to a certified author,
        # performed by the code that owns candidate order rather than duplicated here.
        from core import final_answer_authorship as _authorship

        _author_verdict = None
        try:
            _author_verdict = _authorship.precall_author_verdict(
                manifest=manifest,
                source_context=source_context,
                request_metadata=getattr(request, "metadata", None),
                output_mode=output_mode,
                request_text=str(getattr(request, "prompt", "") or ""),
            )
        except Exception:  # pragma: no cover - the fence must not break a working turn
            _author_verdict = None
        if _author_verdict is not None and not _author_verdict.eligible:
            with contextlib.suppress(Exception):
                _authorship.record_authorship_decision(
                    source_context,
                    _author_verdict,
                    blocked_model=str(getattr(manifest, "provider_id", "") or ""),
                )
            return None, None, "author_not_certified_for_final_answer"
        if manifest is not requested_manifest and _paid_terms(manifest) != _paid_terms(requested_manifest):
            # The re-registered lane does not carry the economic terms the standing reservation was
            # granted under. Registration is not a new authorization: the reservation is handed back —
            # nothing was sent, so nothing is owed — and this candidate refuses. Authority is never
            # carried silently onto different terms, and no second reservation is made (review R2).
            standing = _paid_call_authorization(source_context, manifest=requested_manifest, task=task)
            if standing is not None:
                release_owner_pick_paid_call(
                    standing,
                    reason="dispatch_terms_changed",
                    source_context=source_context,
                    call_role=str((source_context or {}).get("model_call_role") or ""),
                )
            return None, None, "paid_call_terms_changed"
        paid_authorization = _paid_call_authorization(source_context, manifest=manifest, task=task)
        if (
            provider_cost_class(manifest) == "paid_cloud"
            and paid_authorization is None
            and not is_verified_free_cloud_manifest(manifest)
        ):
            return None, None, "paid_call_not_authorized_or_reserved"
        model_call_id = str(getattr(paid_authorization, "model_call_id", "") or f"model-call-{uuid.uuid4().hex}")
        if isinstance(request, ModelRequest):
            # `replace()` is SHALLOW: without the dict copy, `call_request.metadata IS
            # request.metadata`, so every candidate shares one dict with the caller and with each
            # other. That is not theoretical — `_maybe_mux_manifests` and the local/remote race
            # start concurrent threads that all reach this seam, and the adapter writes
            # `request.metadata["prompt_budget"]` from inside each one. The `pop` below was added to
            # scrub the PREVIOUS candidate's telemetry out of the shared dict, which is the same
            # aliasing seen from the other side. A per-candidate copy removes the race instead of
            # cleaning up after it, and is a prerequisite for anything that stores per-lane
            # decisions on the request.
            call_request = replace(
                request,
                model_call_id=model_call_id,
                response_id="",
                metadata={**(request.metadata or {})},
            )
        else:
            # Not a dataclass: this branch mutates the caller's object in place and cannot be
            # isolated the same way. Left as-is deliberately; it is not the failover/mux path.
            call_request = request
            call_request.model_call_id = model_call_id
            call_request.response_id = ""
        if isinstance(getattr(call_request, "metadata", None), dict):
            from core.provider_call_deadline import copy_deadline_to_request_metadata

            call_request.metadata = copy_deadline_to_request_metadata(
                call_request.metadata,
                source_context,
            )
            # Redundant for the copied path above, kept for the shared-object branch.
            call_request.metadata.pop("prompt_budget", None)
            # Carry the mode down to the adapter. The adapter is handed a ModelRequest and never a
            # source context, so without this the transport-level guard would have nothing to read
            # and the deepest seam would be the only unenforced one. Same mechanism as everywhere
            # else here: a value in a dict that travels with the request, not ambient state.
            if turn_is_local_only(source_context):
                call_request.metadata[LOCAL_ONLY_CONTEXT_KEY] = True
        identity = _model_call_identity(source_context, task=task, model_call_id=model_call_id)
        if lane_receipted:
            # The caller wraps this call in its own lane receipts (`model_lane_*` /
            # `model_lane_verifier_*`), emitted around this method for the SAME physical call.
            # Both shapes are real, distinct records with their own consumers (the lane timeline
            # vs the per-adapter call ledger read by turn_trace/turn_failure_stage), so neither
            # emission is dropped -- instead the source states, per event, that a lane receipt
            # covers this call's running/answered display. The chat page renders the lane pair as
            # THE "Model running"/"Model answered" rows and skips this call's own started/
            # completed rows; failure events are never skipped (their reason is the evidence).
            # Unwrapped calls (classifier, race, mux) carry no flag and keep rendering.
            identity = {**identity, "lane_receipted": True}
        context_manifest_id = str(
            dict(getattr(call_request, "context", None) or {}).get(
                "context_manifest_id"
            )
            or (source_context or {}).get("context_manifest_id")
            or ""
        ).strip()
        context_manifest_trace_id = str(
            dict(getattr(call_request, "context", None) or {}).get(
                "context_manifest_trace_id"
            )
            or (source_context or {}).get("context_manifest_trace_id")
            or ""
        ).strip()
        _emit_model_routing_event(
            source_context,
            "model.call_started",
            f"Model call started with {manifest.provider_id}.",
            **identity,
            provider_id=manifest.provider_id,
            model_id=manifest.model_name,
            locality=_manifest_locality(manifest),
            active_inference=True,
            cost_class=reported_cost_class(manifest),
            context_manifest_id=context_manifest_id,
            context_manifest_trace_id=context_manifest_trace_id,
        )
        if circuit_is_open(manifest.provider_id):
            _emit_model_routing_event(
                source_context,
                "model.call_failed",
                f"Model call failed with {manifest.provider_id}.",
                **identity,
                provider_id=manifest.provider_id,
                model_id=manifest.model_name,
                locality=_manifest_locality(manifest),
                active_inference=True,
                reason="circuit_open",
            )
            if paid_authorization is not None:
                release_owner_pick_paid_call(
                    paid_authorization,
                    reason="circuit_open",
                    source_context=source_context,
                    call_role=str((source_context or {}).get("model_call_role") or ""),
                )
            return None, None, "circuit_open"

        adapter = self.registry.build_adapter(manifest)
        # DISPATCH BINDING PREFLIGHT (reviews F3/R2). The lane the gates decided on was resolved
        # against the committed credential binding before them; it is preflighted once more, with NO
        # network, right before anything is sent. A lane that could not be resolved, or a Save that
        # committed a different pair since (a race), refuses here as stale: the current key never
        # travels to a destination the gates did not decide on, and a decided request is never
        # redirected afterwards. The ranked loop tries the next candidate, as for any typed refusal.
        if not dispatch_resolved or not self._dispatch_binding_is_current(adapter=adapter, manifest=manifest):
            _emit_model_routing_event(
                source_context,
                "model.call_failed",
                f"Model call failed with {manifest.provider_id}.",
                **identity,
                provider_id=manifest.provider_id,
                model_id=manifest.model_name,
                locality=_manifest_locality(manifest),
                active_inference=True,
                reason="stale_provider_binding",
            )
            if paid_authorization is not None:
                release_owner_pick_paid_call(
                    paid_authorization,
                    reason="stale_provider_binding",
                    source_context=source_context,
                    call_role=str((source_context or {}).get("model_call_role") or ""),
                )
            return adapter, None, "stale_provider_binding"
        if should_probe_health(manifest.provider_id):
            health = invoke_provider_execution_boundary(adapter, "health_check")
            if not bool(health.get("ok")):
                record_provider_failure(manifest.provider_id, error=str(health.get("error") or "health_check_failed"))
                audit_logger.log(
                    "model_provider_unhealthy",
                    target_id=manifest.provider_id,
                    target_type="model_provider",
                    trace_id=getattr(task, "task_id", None),
                    details={"health": health},
                )
                _emit_model_routing_event(
                    source_context,
                    "model.call_failed",
                    f"Model call failed with {manifest.provider_id}.",
                    **identity,
                    provider_id=manifest.provider_id,
                    model_id=manifest.model_name,
                    locality=_manifest_locality(manifest),
                    active_inference=True,
                    reason=str(health.get("error") or "health_check_failed"),
                )
                if paid_authorization is not None:
                    release_owner_pick_paid_call(
                        paid_authorization,
                        reason="provider_unhealthy",
                        source_context=source_context,
                        call_role=str((source_context or {}).get("model_call_role") or ""),
                    )
                return adapter, None, str(health.get("error") or "health_check_failed")

        try:
            if _model_request_cancelled(call_request):
                raise RuntimeError("model_call_cancelled")
            # REACH: the real invocation seam. Recorded BEFORE the call, because a provider that was
            # reached and then failed was still reached -- and because the previous design derived
            # "a model ran" from the turn result's `model_calls`, which is orchestration telemetry a
            # hostile review showed can read 0 while a provider really was invoked. Observation only:
            # returns nothing, cannot raise, no-op when nothing is observing.
            from core.semantic import reach as semantic_reach
            from core.turn_model_call_ledger import (
                record_provider_call,
                record_provider_call_outcome,
            )

            def _note_attempt() -> str:
                semantic_reach.note_provider_call_attempt(
                    provider_id=str(getattr(manifest, "provider_id", "") or ""),
                    model_name=str(getattr(manifest, "model_name", "") or ""),
                )
                # The turn's OWN count of this call, recorded at the same instant and under the
                # same rule (entered the adapter method; retries and failures each count once).
                # Separate from the REACH note on purpose: REACH is observation the runtime may
                # never read back, and `model_calls` is a number the turn has to report. Two
                # recorders, one seam, so they cannot drift.
                #
                # The manifest's identity travels with the count because a call that FAILS returns
                # no usage to name itself with, and a failed paid-cloud call is precisely the one
                # the turn must still be able to describe. Read off the manifest that is about to
                # be called, never defaulted.
                return record_provider_call(
                    source_context,
                    provider_id=str(getattr(manifest, "provider_id", "") or ""),
                    model_id=str(getattr(manifest, "model_name", "") or ""),
                    cost_class=reported_cost_class(manifest),
                    model_call_id=model_call_id,
                    call_role=str((source_context or {}).get("model_call_role") or ""),
                )

            def _identity_receipt(call_id, response, outcome):
                from core.provider_verification import build_verification_receipt
                return build_verification_receipt(
                    call_id=call_id, request_id=str((source_context or {}).get("request_id") or ""),
                    requested_model=str((source_context or {}).get("requested_model") or manifest.model_name),
                    selected_model=manifest.model_name, provider_id=manifest.provider_id,
                    response=response, outcome=outcome,
                )

            def _call_adapter_task(method_name: str, task_request: ModelRequest) -> ModelResponse:
                # Resolve the method first. A hostile adapter descriptor can raise here, before any
                # task method is entered; that must remain a truthful false. Once resolved, record
                # immediately before the call instruction. Every primary and retry call uses this
                # one seam, so a later retry cannot silently skip observation.
                call_id = ""
                call_started = 0.0
                from core.response_usage_details import response_usage_details
                from core.runtime_active_clock import monotonic as active_monotonic

                def _record_entry() -> None:
                    nonlocal call_id, call_started
                    call_id = _note_attempt()
                    call_started = active_monotonic()

                try:
                    response = invoke_provider_execution_boundary(
                        adapter,
                        method_name,
                        task_request,
                        before_call=_record_entry,
                    )
                except Exception as exc:
                    if call_id:
                        error_class = classify_error_class(exc)
                        recorded = record_provider_call_outcome(
                            source_context,
                            call_id,
                            outcome="failed",
                            verification_receipt=_identity_receipt(call_id, exc, "failed"),
                            error_class=error_class.value if error_class else "",
                            usage_details={"cost_state": "free" if reported_cost_class(manifest) in {"free_local", "free_cloud"} else "unreported"},
                        )
                        if not recorded:
                            raise ProviderCallAlreadyTerminalized(
                                "provider call ended after its enclosing turn was terminalized"
                            ) from exc
                    raise
                response.provider_metadata = dict(response.provider_metadata or {})
                response.provider_metadata["vool_call_seconds"] = max(0.0, active_monotonic() - call_started)
                if call_id:
                    recorded = record_provider_call_outcome(
                        source_context,
                        call_id,
                        outcome="completed",
                        usage_details=response_usage_details(response, cost_class=reported_cost_class(manifest)),
                        verification_receipt=_identity_receipt(call_id, response, "completed"),
                    )
                    if not recorded:
                        raise ProviderCallAlreadyTerminalized(
                            "provider call completed after its enclosing turn was terminalized"
                        )
                return response

            def _empty_reply_exhaustion(exc: Exception) -> bool:
                """Whether the empty reply's own evidence says reasoning spent the ceiling.

                The typed diagnostics ride the exception (the buffered path's
                ``empty_reply_diagnostics`` and the streamed path's terminal-fact block): a
                length-finish with reasoning present is ``output_budget_exhausted``. This is
                the one empty shape a SECOND attempt can genuinely fix -- the model was not
                broken, it was not finished -- and it is the shape a paid reasoning model
                produces when the ceiling fits its monologue but not the answer after it.
                """
                diagnostics = getattr(exc, "diagnostics", None)
                if not isinstance(diagnostics, dict):
                    return False
                try:
                    from core.normalized_provider_result import (
                        EMPTY_REPLY_OUTPUT_BUDGET_EXHAUSTED,
                        classify_empty_reply,
                    )

                    exhausted = (
                        classify_empty_reply(diagnostics) == EMPTY_REPLY_OUTPUT_BUDGET_EXHAUSTED
                        and bool(diagnostics.get("reasoning_present"))
                    )
                    if exhausted:
                        # The provider's own terminal facts say THIS model reasons: record it so
                        # the next sizing for this exact lane/model carries the thinking reserve
                        # even on feeds that publish no capability (UsePod lists pricing only).
                        from core.output_budget_policy import note_observed_reasoning

                        note_observed_reasoning(
                            str(getattr(manifest, "provider_id", "") or ""),
                            str(getattr(manifest, "model_name", "") or ""),
                        )
                    return exhausted
                except Exception:
                    return False

            class _WideningRefused:
                """Sentinel: the re-ask itself was refused by the money authority. The caller
                must RAISE, never fall through to a same-request retry — the exhaustion is not
                recoverable at the authority this turn holds."""

            _WIDENING_REFUSED = _WideningRefused()

            def _exhaustion_widened_request(
                exc: Exception, task_request: ModelRequest, attempt: int
            ) -> ModelRequest | None:
                # MONEY DISCIPLINE: the widened re-ask runs under the SAME reservation, so its
                # projected cost must still fit that reservation's held ceiling. The first
                # attempt's charge-or-unknown is already accounted by the reservation's own
                # multi-attempt machinery (unpriced attempts are charged at the settling
                # attempt's rate); widening beyond what was held would spend authority nobody
                # granted, so the re-ask refuses BEFORE dispatch when it does not fit.
                try:
                    from core.model_pricing import estimate_call_usd
                    from core.model_spend_ledger import get_spend_reservation
                    from core.paid_call_reservation import _int_attr

                    authorization = (source_context or {}).get("authorized_paid_call")
                    model_call_id = str(getattr(authorization, "model_call_id", "") or "")
                    if model_call_id:
                        held = get_spend_reservation(model_call_id)
                        if held is not None and str(held.status) == "reserved":
                            widened_tokens = int(getattr(task_request, "max_output_tokens", 0) or 0) + 2048
                            estimate = estimate_call_usd(
                                provider_id=str(manifest.provider_id or ""),
                                model_id=str(manifest.model_name or ""),
                                prompt_tokens=_int_attr(task, "prompt_tokens"),
                                completion_tokens=widened_tokens,
                            )
                            projected = float(getattr(estimate, "usd", 0.0) or 0.0)
                            if projected > float(held.reserved_usd or 0.0) + 1e-9:
                                _emit_model_routing_event(
                                    source_context,
                                    "model.call_failed",
                                    "Widened retry would exceed the held paid reservation; refusing before dispatch.",
                                    **identity,
                                    provider_id=manifest.provider_id,
                                    model_id=manifest.model_name,
                                    reason="widened_retry_exceeds_reservation",
                                    error_class=ProviderErrorClass.EMPTY_PROVIDER_RESPONSE.value,
                                    projected_usd=round(projected, 8),
                                    reserved_usd=float(held.reserved_usd or 0.0),
                                )
                                return _WIDENING_REFUSED
                except Exception:
                    pass
                """The ONE widened re-ask for an exhaustion-shaped empty, or None.

                Shared by the buffered and streamed arms. The ceiling grows by the same
                thinking reserve the first sizing used; anything else about the request is
                unchanged. The re-ask is a real, recorded spend under the reservation's own
                multi-attempt accounting, bounded to one widening, and emitted as a
                ``model.call_retrying`` row so the operator sees it.
                """
                if attempt != 1:
                    return None
                if classify_error_class(exc) != ProviderErrorClass.EMPTY_PROVIDER_RESPONSE:
                    return None
                if not _empty_reply_exhaustion(exc):
                    return None
                widened = int(getattr(task_request, "max_output_tokens", 0) or 0) + 2048
                # THE RETRY'S TRANSPORT BUDGET MUST MATCH ITS WIDENED CEILING. The first attempt's
                # transport timeout sized the FIRST attempt's token budget; inheriting it on a
                # +2048-token retry had the transfer watchdog kill the retry at exactly the old
                # budget (measured 2026-09-18: 129s attempt for the ceiling, then the retry died
                # at a flat 180s) while the enclosing turn deadline still had room. The override
                # only widens transport; `effective_timeout_seconds` still clamps it to the
                # enclosing absolute deadline, so turn authority is never extended.
                try:
                    _configured_transport = float(
                        (getattr(manifest, "runtime_config", None) or {}).get("timeout_seconds") or 30.0
                    )
                except Exception:
                    _configured_transport = 30.0
                _widened_transport = min(2.0 * _configured_transport, 600.0)
                widened_request = replace(
                    task_request,
                    max_output_tokens=widened,
                    model_call_id=str(getattr(task_request, "model_call_id", "") or ""),
                    metadata={
                        **(getattr(task_request, "metadata", None) or {}),
                        "_provider_transport_timeout_seconds": _widened_transport,
                    },
                )
                _emit_model_routing_event(
                    source_context,
                    "model.call_retrying",
                    "Reasoning spent the output ceiling before the answer; retrying "
                    "the same model once with a widened ceiling.",
                    **identity,
                    provider_id=manifest.provider_id,
                    model_id=manifest.model_name,
                    error_class=ProviderErrorClass.EMPTY_PROVIDER_RESPONSE.value,
                    retry_index=attempt,
                    retry_policy="same_model_widened_output_budget",
                    output_ceiling=widened,
                    transport_timeout_seconds=round(_widened_transport, 1),
                )
                return widened_request

            def _call_adapter_task_with_empty_retry(
                method_name: str,
                task_request: ModelRequest,
            ) -> ModelResponse:
                from core.output_budget_policy import manifest_declares_reasoning

                attempt = 1
                while True:
                    try:
                        return _call_adapter_task(method_name, task_request)
                    except Exception as exc:
                        error_class = classify_error_class(exc)
                        exhausted = (
                            error_class == ProviderErrorClass.EMPTY_PROVIDER_RESPONSE
                            and _empty_reply_exhaustion(exc)
                            and (
                                manifest_declares_reasoning(manifest)
                                # The exhaustion's own typed diagnostics carry reasoning_present,
                                # which IS the capability evidence for lanes whose feed publishes
                                # none -- the widened re-ask does not depend on a declaration.
                                or bool((getattr(exc, "diagnostics", None) or {}).get("reasoning_present"))
                            )
                        )
                        widened_request = (
                            _exhaustion_widened_request(exc, task_request, attempt)
                            if exhausted
                            else None
                        )
                        if widened_request is _WIDENING_REFUSED:
                            raise
                        if widened_request is not None:
                            attempt += 1
                            task_request = widened_request
                            continue
                        retry_allowed = bool(
                            getattr(task_request, "allow_provider_retry", True)
                        ) and (
                            exhausted
                            or (
                                error_class == ProviderErrorClass.EMPTY_PROVIDER_RESPONSE
                                and is_retryable(error_class)
                                and (
                                    provider_cost_class(manifest) == "free_local"
                                    or is_verified_free_cloud_manifest(manifest)
                                )
                            )
                        ) and should_retry(attempt, _EMPTY_RESPONSE_RETRY_POLICY) and not _model_request_cancelled(task_request)
                        if not retry_allowed:
                            raise
                        _emit_model_routing_event(
                            source_context,
                            "model.call_retrying",
                            "Structurally empty provider response; retrying the same model once.",
                            **identity,
                            provider_id=manifest.provider_id,
                            model_id=manifest.model_name,
                            error_class=ProviderErrorClass.EMPTY_PROVIDER_RESPONSE.value,
                            retry_index=attempt,
                            retry_policy="same_provider_model",
                        )
                        attempt += 1

            # Recorded PER ARM, immediately before the adapter method is entered -- not once above
            # the branch. `provider_call_attempted` claims the adapter task method was actually
            # entered, and a marker set before the dispatch decision claims it for a turn that
            # raised while choosing (a streaming probe, a metadata read) without entering anything.
            if output_mode in _STRUCTURED_OUTPUT_MODES:
                response = _call_adapter_task_with_empty_retry(
                    "run_structured_task", call_request
                )
            elif (
                not bool(call_request.metadata.get("defer_stream_until_verified"))
                and _streaming_requested(source_context, output_mode=output_mode)
                and adapter.supports_streaming()
            ):
                stream_call_id = ""
                stream_started = 0.0
                from core.response_usage_details import response_usage_details
                from core.runtime_active_clock import monotonic as active_monotonic

                def _record_stream_entry() -> None:
                    nonlocal stream_call_id, stream_started
                    stream_call_id = _note_attempt()
                    stream_started = active_monotonic()

                # The streamed twin of `_call_adapter_task_with_empty_retry`: an exhaustion-shaped
                # empty (the typed diagnostics the stream's own terminal facts produce) gets ONE
                # widened re-stream on any cost class when the manifest declares reasoning. The
                # buffered arm below has owned this since the 2026-09-17 incident; the streamed
                # arm raised the same shape UNTYPED and unrecoverable.
                from core.output_budget_policy import manifest_declares_reasoning as _stream_declares_reasoning

                stream_attempt = 1
                while True:
                    try:
                        response = self._stream_response(
                            adapter=adapter,
                            manifest=manifest,
                            request=call_request,
                            source_context=source_context,
                            note_attempt=_record_stream_entry,
                        )
                        break
                    except Exception as exc:
                        if stream_call_id:
                            error_class = classify_error_class(exc)
                            recorded = record_provider_call_outcome(
                                source_context,
                                stream_call_id,
                                outcome="failed",
                                verification_receipt=_identity_receipt(stream_call_id, exc, "failed"),
                                error_class=error_class.value if error_class else "",
                                usage_details={"cost_state": "free" if reported_cost_class(manifest) in {"free_local", "free_cloud"} else "unreported"},
                            )
                            if not recorded:
                                raise ProviderCallAlreadyTerminalized(
                                    "provider stream ended after its enclosing turn was terminalized"
                                ) from exc
                        _widened_stream_request = _exhaustion_widened_request(exc, call_request, stream_attempt)
                        _stream_reasoning_evidence = _stream_declares_reasoning(manifest) or bool(
                            (getattr(exc, "diagnostics", None) or {}).get("reasoning_present")
                        )
                        if _widened_stream_request is _WIDENING_REFUSED:
                            raise
                        if _widened_stream_request is not None and _stream_reasoning_evidence:
                            call_request = _widened_stream_request
                            stream_attempt += 1
                            continue
                        raise
                response.provider_metadata = dict(response.provider_metadata or {})
                response.provider_metadata["vool_call_seconds"] = max(0.0, active_monotonic() - stream_started)
                if stream_call_id:
                    recorded = record_provider_call_outcome(
                        source_context,
                        stream_call_id,
                        outcome="completed",
                        usage_details=response_usage_details(response, cost_class=reported_cost_class(manifest)),
                        verification_receipt=_identity_receipt(stream_call_id, response, "completed"),
                    )
                    if not recorded:
                        raise ProviderCallAlreadyTerminalized(
                            "provider stream completed after its enclosing turn was terminalized"
                        )
            else:
                response = _call_adapter_task_with_empty_retry(
                    "run_text_task", call_request
                )
            response_request = call_request
            # Keep the LAST selected provider call's termination evidence separate from merged
            # usage. Repair calls aggregate usage for billing, but an aggregate token count cannot
            # prove whether the final attempt itself reached its ceiling.
            response_completion_usage = dict(response.usage or {})
            response_finish_reason = str(getattr(response, "finish_reason", "") or "")
            response_output_ceiling = _provider_response_output_ceiling(
                response,
                call_request,
            )
            response_constraint = response_constraint_from_metadata(
                getattr(call_request, "metadata", None)
            )
            # An X-editorial turn's answer policy is the XDraft validation edge, not the
            # prose shape constraints: neutralize before the enforcement branch below.
            if dict(getattr(call_request, "metadata", None) or {}).get("x_editorial_turn"):
                response_constraint = None
            # C19 AUTOMATIC PRESENTATION SELECTION — one deterministic pass over the
            # candidate bytes and the typed turn context (never the prompt). It stands
            # down whenever an explicit authority owns the turn, ratifies an observed
            # shape, elects against prose, or defaults to prose; the record is
            # provenance only and never enters answer bytes. On the free-local lane an
            # election may flow ONE derived contract (origin="automatic") through the
            # very same bounded repair below — no second repair path.
            _selection_context = _presentation_selection_context(
                source_context, getattr(call_request, "metadata", None)
            )
            _presentation_selection = _select_and_record_presentation(
                response.output_text, _selection_context, source_context
            )
            _automatic_repair: dict[str, Any] | None = None
            if (
                response_constraint is None
                and _presentation_selection.get("gap_detected")
                and EXPLICIT_COUNTERPART.get(str(_presentation_selection.get("elected") or ""))
                and provider_cost_class(manifest) == "free_local"
                and not _model_request_cancelled(call_request)
            ):
                from core.turn_ir import ResponseConstraint as _DerivedConstraint

                _elected_format = str(_presentation_selection["elected"])
                _automatic_repair = {
                    "elected": _elected_format,
                    "original_text": str(response.output_text),
                }
                response_constraint = _DerivedConstraint(
                    presentation_format=EXPLICIT_COUNTERPART[_elected_format],
                    origin="automatic",
                )
            if response_constraint is not None:
                initial_response = response
                if _automatic_repair is not None:
                    # Keep the initial call's completion facts; a failed derived
                    # repair must restore them alongside the original bytes so the
                    # restored prose is never judged by the repair call's ceiling.
                    _automatic_repair["initial"] = (
                        initial_response,
                        dict(response_completion_usage or {}),
                        response_finish_reason,
                        response_output_ceiling,
                    )
                response.output_text = compress_short_coordinate_phrase(
                    response.output_text,
                    response_constraint,
                )
                initial_check = check_response_constraint(
                    response.output_text,
                    response_constraint,
                )
                short_answer_retry_needed = short_answer_needs_grounding_retry(
                    response.output_text,
                    response_constraint,
                    _current_user_text_from_request(call_request),
                )
                initial_violations = list(initial_check.violations)
                if short_answer_retry_needed:
                    initial_violations.append("low_information_short_answer")
                initial_application = enforce_response_constraint(
                    response.output_text,
                    response_constraint,
                )
                initial_candidate_compliant = (
                    initial_application.compliant
                    and not short_answer_needs_grounding_retry(
                        initial_application.text,
                        response_constraint,
                        _current_user_text_from_request(call_request),
                    )
                )
                retry_attempted = False
                retry_succeeded = False
                retry_error = ""
                selected_candidate = "initial"
                if (
                    (not initial_check.compliant or short_answer_retry_needed)
                    and (provider_cost_class(manifest) == "free_local" or (
                        is_verified_free_cloud_manifest(manifest)
                        and response_constraint.presentation_format
                        and response_constraint.origin != "automatic"
                    ))
                    and not _model_request_cancelled(call_request)
                ):
                    retry_attempted = True
                    retry_contract = exact_word_retry_contract(
                        response_constraint
                    )
                    instruction = formatting_retry_instruction(
                        response_constraint,
                        # Name what the failed draft actually lacked (measured a651de73: the
                        # generic list produced the same ```json chart twice), never the whole
                        # request again.
                        missing_formats=tuple(
                            getattr(initial_check, "missing_formats", ()) or ()
                        ),
                    )
                    if retry_contract is not None:
                        instruction = (
                            "Rewrite the original answer as one grammatical, factual "
                            "answer to the original request. Expand with relevant facts "
                            "from the supplied context when the draft has too few words. "
                            "Return only a JSON object whose words array contains exactly "
                            f"{response_constraint.exact_words} non-empty, substantive, "
                            "one-word strings in reading order. Preserve names and "
                            "requested facts. Never use blank strings, whitespace, or "
                            "punctuation-only items. Do not include commentary or any key "
                            "besides words."
                        )
                    retry_messages = _response_constraint_retry_messages(
                        call_request,
                        failed_draft=response.output_text,
                        instruction=instruction,
                    )
                    retry_limit = max(16, int(call_request.max_output_tokens or 512))
                    requested_words = response_constraint.max_words or response_constraint.exact_words
                    if requested_words:
                        retry_limit = max(16, min(retry_limit, int(requested_words) * 4))
                    if _automatic_repair is not None:
                        # Selection-driven budget: the degenerate max_words-keyed
                        # formula below keys on counts an election-derived contract
                        # does not carry. The repair must be able to restate the
                        # whole answer twice over, bounded by the lane ceiling.
                        retry_limit = max(
                            64,
                            min(
                                int(call_request.max_output_tokens or 512),
                                ((len(_automatic_repair["original_text"]) + 3) // 4) * 2,
                            ),
                        )
                    elif retry_contract is not None:
                        retry_limit = max(64, retry_limit)
                    retry_request = replace(
                        call_request,
                        prompt=instruction,
                        messages=retry_messages,
                        context=dict(call_request.context or {}),
                        attachments=[],
                        temperature=0.0,
                        max_output_tokens=retry_limit,
                        output_mode=(
                            "json_object"
                            if retry_contract is not None
                            else call_request.output_mode
                        ),
                        contract=(
                            retry_contract
                            if retry_contract is not None
                            else dict(call_request.contract or {})
                        ),
                        metadata={
                            **dict(call_request.metadata or {}),
                            # A repair must not retrieve a second context pack
                            # against its formatting instruction. The current
                            # turn and failed draft are already explicit above.
                            "memory_prompt": {"enabled": False},
                            "response_constraint_retry": 1,
                            "defer_stream_until_verified": True,
                            **(
                                {"response_constraint_origin": "automatic"}
                                if _automatic_repair is not None
                                else {}
                            ),
                        },
                    )
                    try:
                        if retry_contract is not None:
                            retry_response = _call_adapter_task(
                                "run_structured_task", retry_request
                            )
                            structured_text = exact_word_retry_text(
                                retry_response.output_text,
                                response_constraint,
                            )
                            retry_response.output_text = structured_text or ""
                            retry_response.output_mode = call_request.output_mode
                        else:
                            retry_response = _call_adapter_task(
                                "run_text_task", retry_request
                            )
                        retry_completion_usage = dict(retry_response.usage or {})
                        retry_finish_reason = str(
                            getattr(retry_response, "finish_reason", "") or ""
                        )
                        retry_response.usage = _merge_provider_usage(
                            response.usage,
                            retry_response.usage,
                        )
                        retry_application = enforce_response_constraint(
                            retry_response.output_text,
                            response_constraint,
                        )
                        retry_short_answer_needed = (
                            short_answer_needs_grounding_retry(
                                retry_application.text,
                                response_constraint,
                                _current_user_text_from_request(call_request),
                            )
                        )
                        retry_succeeded = (
                            retry_application.compliant
                            and not retry_short_answer_needed
                        )
                        if retry_succeeded and _automatic_repair is not None:
                            # The election-derived repair is accepted only when the
                            # new shape carries every citation, number, unit and
                            # receipt the prose held. Anything less and the original
                            # ships unchanged — a reworded quantity must never buy a
                            # prettier shape (and would only die at the M3 gate).
                            retry_succeeded = selection_repair_acceptance(
                                _automatic_repair["original_text"],
                                retry_application.text,
                                _automatic_repair["elected"],
                            )
                        if retry_succeeded:
                            response = retry_response
                            response_request = retry_request
                            response_completion_usage = retry_completion_usage
                            response_finish_reason = retry_finish_reason
                            response_output_ceiling = _model_request_output_ceiling(
                                retry_request
                            )
                            selected_candidate = "retry"
                        elif initial_candidate_compliant:
                            initial_response.usage = retry_response.usage
                            response = initial_response
                        else:
                            response = retry_response
                            response_request = retry_request
                            response_completion_usage = retry_completion_usage
                            response_finish_reason = retry_finish_reason
                            response_output_ceiling = _model_request_output_ceiling(
                                retry_request
                            )
                            selected_candidate = "retry_failed"
                        _emit_model_routing_event(
                            source_context,
                            "model.response_constraint_retry",
                            "Model response shape required one bounded retry.",
                            **identity,
                            provider_id=manifest.provider_id,
                            model_id=manifest.model_name,
                            violations=list(initial_check.violations),
                            retry_mode=(
                                "exact_word_schema"
                                if retry_contract is not None
                                else "text"
                            ),
                        )
                    except Exception as exc:
                        retry_error = str(exc)
                        audit_logger.log(
                            "model_response_constraint_retry_failed",
                            target_id=manifest.provider_id,
                            target_type="model_provider",
                            trace_id=getattr(task, "task_id", None),
                            details={
                                "violations": list(
                                    initial_check.violations
                                ),
                                "error": retry_error,
                            },
                        )
                if _automatic_repair is not None:
                    if retry_succeeded:
                        # The repaired bytes carry the elected shape now: the
                        # record describes a ratification, not a gap.
                        _presentation_selection = {
                            **_presentation_selection,
                            "gap_detected": False,
                        }
                    else:
                        # The one bounded repair failed (or never passed
                        # acceptance): the ORIGINAL prose ships unchanged
                        # (fallback="prose_default") and the derived contract
                        # stands down. The canned fallback below must never
                        # overwrite an honest answer with a shape complaint
                        # about a shape nobody requested.
                        (
                            _initial_response,
                            _initial_usage,
                            _initial_finish,
                            _initial_ceiling,
                        ) = _automatic_repair["initial"]
                        _merged_repair_usage = dict(response.usage or {})
                        response = _initial_response
                        response.usage = _merge_provider_usage(
                            response.usage, _merged_repair_usage
                        )
                        response.output_text = _automatic_repair["original_text"]
                        response_completion_usage = _initial_usage
                        response_finish_reason = _initial_finish
                        response_output_ceiling = _initial_ceiling
                        response_constraint = None
                        _presentation_selection = {
                            **_presentation_selection,
                            "fallback": "prose_default",
                        }
                    if isinstance(source_context, dict):
                        source_context["presentation_selection"] = (
                            _presentation_selection
                        )
                    record_presentation_selection(source_context, _presentation_selection)
                if response_constraint is not None:
                    applied = enforce_response_constraint(
                        response.output_text,
                        response_constraint,
                    )
                    response.output_text = applied.text
                    remaining_violations = list(applied.violations)
                    if short_answer_needs_grounding_retry(
                        response.output_text,
                        response_constraint,
                        _current_user_text_from_request(call_request),
                    ):
                        remaining_violations.append("low_information_short_answer")
                    constraint_compliant = applied.compliant and not any(
                        violation == "low_information_short_answer"
                        for violation in remaining_violations
                    )
                    response.constraint_result = {
                        "requested": response_constraint.to_dict(),
                        "initial_violations": initial_violations,
                        "initial_missing_formats": list(
                            getattr(initial_check, "missing_formats", ()) or ()
                        ),
                        "retry_attempted": retry_attempted,
                        "retry_succeeded": retry_succeeded,
                        "retry_error": retry_error,
                        "selected_candidate": selected_candidate,
                        "structurally_trimmed": (
                            applied.structurally_trimmed
                        ),
                        "fences_normalized": bool(
                            getattr(applied, "fences_normalized", False)
                        ),
                        "compliant": constraint_compliant,
                        "remaining_violations": remaining_violations,
                    }
                    if not constraint_compliant:
                        response.output_text = constraint_safe_fallback(
                            response_constraint
                        )
                        response.constraint_result["fallback_applied"] = True
            ordinary_policy = dict(
                call_request.metadata.get("ordinary_chat_output_policy") or {}
            )
            # Automatic brevity is a writing preference, never authority to discard a paid
            # answer or spend another call. Explicit response constraints and safety checks
            # remain separate and are enforced before/after this style inspection.
            ordinary_policy["brevity_advisory"] = True
            # The X editorial studio's response edge owns this turn's answer policy: the
            # XDraft envelope is parsed, validated and rendered by the deterministic engine
            # (core.x_editorial), so the prose-shaping authorities below — the ordinary-chat
            # shaper and the raw-output contract — must not trim or reshape the envelope
            # before the engine sees it. (The response constraint was already neutralized
            # where its metadata is read, before its enforcement branch.)
            _x_editorial_turn = dict(call_request.metadata.get("x_editorial_turn") or {})
            raw_contract = raw_output_contract_from_metadata(call_request.metadata)
            if _x_editorial_turn:
                ordinary_policy = {}
                raw_contract = None
            raw_application = None
            raw_changed = False
            raw_rejected = False
            raw_actions: list[str] = []
            control_failed_draft = response.output_text
            if raw_contract is not None:
                raw_failed_draft = response.output_text
                raw_application = apply_raw_output_contract(
                    response.output_text,
                    raw_contract,
                )
                response.output_text = raw_application.text
                raw_changed = raw_changed or raw_application.changed
                raw_rejected = raw_rejected or raw_application.rejected
                raw_actions.extend(
                    action
                    for action in raw_application.actions
                    if action not in raw_actions
                )
                control_failed_draft = (
                    response.output_text
                    if raw_application.compliant
                    else raw_failed_draft
                )
            language_policy = dict(
                call_request.metadata.get("response_language_policy") or {}
            )
            current_user_text = _current_user_text_from_request(call_request)
            ordinary_check = inspect_ordinary_chat_output(
                response.output_text,
                ordinary_policy,
                current_user_text=current_user_text,
            )
            language_check = check_response_language(
                response.output_text,
                language_policy,
            )
            provider_completion = inspect_provider_completion(
                response.output_text,
                finish_reason=response_finish_reason,
                usage=response_completion_usage,
                max_output_tokens=response_output_ceiling,
            )
            initial_provider_completion = provider_completion
            if provider_completion.incomplete and any(
                "finish_reason:length" in str(reason) for reason in provider_completion.reasons
            ):
                # Terminal fact: the provider stopped at OUR ceiling with reasoning tokens in
                # the usage block. Record it so the NEXT sizing for this exact lane/model
                # carries the thinking reserve -- the UsePod feed publishes no capability, and
                # this is how a truncated first answer teaches the second call its real shape.
                try:
                    _completion_details = dict(response_completion_usage or {}).get(
                        "completion_tokens_details"
                    )
                    if isinstance(_completion_details, dict) and _completion_details.get(
                        "reasoning_tokens"
                    ):
                        from core.output_budget_policy import note_observed_reasoning

                        note_observed_reasoning(
                            str(getattr(manifest, "provider_id", "") or ""),
                            str(getattr(manifest, "model_name", "") or ""),
                        )
                except Exception:
                    pass
            control_retry_attempted = False
            control_retry_succeeded = False
            control_retry_error = ""
            bounded_recovery_applied = False
            control_violations = [
                *ordinary_check.reasons,
                *language_check.violations,
                *(
                    f"provider_output_incomplete:{reason}"
                    for reason in provider_completion.reasons
                ),
                *(
                    f"raw_output:{violation}"
                    for violation in (
                        raw_application.violations
                        if raw_application is not None
                        else ()
                    )
                ),
            ]
            _paid_control_retry_permitted = False
            if reported_cost_class(manifest) == "paid_cloud":
                # A truncated PAID answer may take the same one bounded repair, but only under a
                # monetary authority this turn already holds — the legacy ledger reservation, or
                # (the UsePod lane) an ACTIVE money-law prepaid grant whose allowlist covers this
                # model. The retry's ceiling is projected against the legacy reservation where one
                # exists; the grant path authorizes by its own allowlist, exactly as the pick did.
                # A lane with neither refuses before dispatch: no new authority is created mid-turn.
                try:
                    from core.model_pricing import estimate_call_usd
                    from core.model_spend_ledger import get_spend_reservation

                    _authorization = (source_context or {}).get("authorized_paid_call")
                    _held_id = str(getattr(_authorization, "model_call_id", "") or "")
                    _held = get_spend_reservation(_held_id) if _held_id else None
                    if _held is not None and str(_held.status) in {"reserved", "billing_ambiguous"}:
                        _current_ceiling = int(call_request.max_output_tokens or 512)
                        _retry_ceiling = max(_current_ceiling + 128, 512)
                        _estimate = estimate_call_usd(
                            provider_id=str(manifest.provider_id or ""),
                            model_id=str(manifest.model_name or ""),
                            prompt_tokens=_int_attr(task, "prompt_tokens"),
                            completion_tokens=_retry_ceiling,
                        )
                        _projected = float(getattr(_estimate, "usd", 0.0) or 0.0)
                        _held_ceiling = float(
                            getattr(_held, "reserved_usd", 0.0)
                            or getattr(_held, "actual_usd", 0.0)
                            or 0.0
                        )
                        _paid_control_retry_permitted = _projected <= _held_ceiling + 1e-9
                except Exception:
                    _paid_control_retry_permitted = False
                if not _paid_control_retry_permitted and str(
                    getattr(manifest, "provider_id", "") or ""
                ).startswith("usepod"):
                    try:
                        from core.usepod import money_law

                        _model_id = str(getattr(manifest, "model_name", "") or "")
                        _grants = money_law.active_prepaid_grants()
                        _paid_control_retry_permitted = any(
                            not (row["spec"].get("models") or ())
                            or _model_id in tuple(row["spec"].get("models") or ())
                            for row in _grants
                        )
                    except Exception:
                        _paid_control_retry_permitted = False
            if (
                control_violations
                and bool(getattr(call_request, "allow_response_control_retry", True))
                and (
                    reported_cost_class(manifest) in {"free_local", "free_cloud"}
                    or _paid_control_retry_permitted
                )
                and not _model_request_cancelled(call_request)
                and not (
                    provider_completion.incomplete
                    and bool(
                        dict(getattr(response, "constraint_result", {}) or {}).get(
                            "retry_attempted"
                        )
                    )
                )
            ):
                control_retry_attempted = True
                instructions: list[str] = []
                if not ordinary_check.allowed:
                    instructions.append(ordinary_chat_retry_instruction(ordinary_policy))
                if not language_check.compliant:
                    instructions.append(response_language_retry_instruction(language_policy))
                if raw_application is not None and not raw_application.compliant:
                    instructions.append(raw_output_retry_instruction(raw_contract))
                if provider_completion.incomplete:
                    instructions.append(
                        "The previous answer was cut off at the provider output boundary. Return "
                        "one complete replacement answer to the original request, not merely a "
                        "continuation fragment. Finish every sentence, list item, delimiter, and "
                        "code fence. Preserve the requested output shape and include no preamble."
                    )
                instruction = "\n".join(instructions)
                retry_messages = [
                    dict(message)
                    for message in list(call_request.messages or [])
                ]
                if not retry_messages:
                    if call_request.system_prompt:
                        retry_messages.append(
                            {
                                "role": "system",
                                "content": call_request.system_prompt,
                            }
                        )
                    retry_messages.append(
                        {
                            "role": "user",
                            "content": call_request.prompt,
                        }
                    )
                retry_messages.extend(
                    [
                        {"role": "assistant", "content": control_failed_draft},
                        {"role": "user", "content": instruction},
                    ]
                )
                retry_output_ceiling = max(
                    64,
                    min(int(call_request.max_output_tokens or 512), 512),
                )
                # The truncation facts decide the repair's room. A length-finish on a lane the
                # terminal facts/registry say REASONS means the ceiling fit neither the monologue
                # nor the answer; +128 repaired nothing and the repair truncated again (measured
                # live on the UsePod lane, 2026-09-17: two length-finishes, one per attempt).
                # The repair then carries the same 2,048 thinking reserve the first sizing
                # would have used had the feed published the capability, and does NOT disable
                # reasoning on a lane observed to mandate it -- a disabled policy there is the
                # typed 400 this runtime already repaired once today.
                _repair_reasoning = False
                if provider_completion.incomplete:
                    current_ceiling = int(call_request.max_output_tokens or 512)
                    try:
                        from core.output_budget_policy import manifest_declares_reasoning

                        _repair_reasoning = manifest_declares_reasoning(manifest) or bool(
                            (
                                dict(response_completion_usage or {})
                                .get("completion_tokens_details")
                                or {}
                            ).get("reasoning_tokens")
                        )
                    except Exception:
                        _repair_reasoning = False
                    # The repair asks for the COMPLETE replacement, so its room is the first
                    # ceiling plus one thinking reserve -- on every lane, not only where
                    # reasoning is declared. A length-finish is the provider's own fact that the
                    # ceiling was too small for THIS model's answer whatever it spent the tokens
                    # on, and +128 repaired nothing twice on lanes whose feeds publish no
                    # capability (measured live 2026-09-17: a capability-less UsePod lane and a
                    # declared OpenRouter lane both truncated their repairs). The money gate
                    # above already refuses a paid repair whose projection exceeds the held
                    # reservation, so the room never spends authority nobody granted.
                    retry_output_ceiling = max(
                        retry_output_ceiling,
                        current_ceiling + 2048,
                        512,
                        current_ceiling,
                    )
                retry_request = replace(
                    call_request,
                    prompt=instruction,
                    messages=retry_messages,
                    temperature=0.0,
                    # OpenRouter accepts this typed policy and turns it into
                    # `reasoning: {enabled: false}`. Without it, the adapter adds a 2,048-token
                    # reasoning reserve and the nominal 440-token repair leaves the wire at 2,488.
                    # A bounded rewrite must enforce its answer ceiling exactly -- EXCEPT on a
                    # lane whose provider mandates reasoning, where disabling is the 400.
                    reasoning_mode=("auto" if _repair_reasoning else REASONING_DISABLED),
                    max_output_tokens=retry_output_ceiling,
                    metadata={
                        **dict(call_request.metadata or {}),
                        "response_control_retry": 1,
                        **(
                            {"raw_output_contract_retry": 1}
                            if raw_application is not None
                            and not raw_application.compliant
                            else {}
                        ),
                        "defer_stream_until_verified": True,
                    },
                )
                try:
                    retry_response = _call_adapter_task("run_text_task", retry_request)
                    retry_completion_usage = dict(retry_response.usage or {})
                    retry_finish_reason = str(
                        getattr(retry_response, "finish_reason", "") or ""
                    )
                    retry_response.usage = _merge_provider_usage(
                        response.usage,
                        retry_response.usage,
                    )
                    response = retry_response
                    response_request = retry_request
                    if raw_contract is not None:
                        raw_application = apply_raw_output_contract(
                            response.output_text,
                            raw_contract,
                        )
                        response.output_text = raw_application.text
                        raw_changed = raw_changed or raw_application.changed
                        raw_rejected = raw_rejected or raw_application.rejected
                        raw_actions.extend(
                            action
                            for action in raw_application.actions
                            if action not in raw_actions
                        )
                    provider_completion = inspect_provider_completion(
                        response.output_text,
                        finish_reason=retry_finish_reason,
                        usage=retry_completion_usage,
                        max_output_tokens=_provider_response_output_ceiling(
                            retry_response,
                            retry_request,
                        ),
                    )
                    ordinary_check = inspect_ordinary_chat_output(
                        response.output_text,
                        ordinary_policy,
                        current_user_text=current_user_text,
                    )
                    language_check = check_response_language(
                        response.output_text,
                        language_policy,
                    )
                    control_retry_succeeded = bool(
                        ordinary_check.allowed
                        and language_check.compliant
                        and (
                            raw_application is None
                            or raw_application.compliant
                        )
                        and not provider_completion.incomplete
                    )
                    _emit_model_routing_event(
                        source_context,
                        "model.response_control_retry",
                        "Model response required one bounded output repair.",
                        **identity,
                        provider_id=manifest.provider_id,
                        model_id=manifest.model_name,
                        violations=control_violations,
                    )
                except Exception as exc:
                    control_retry_error = str(exc)
                    audit_logger.log(
                        "model_response_control_retry_failed",
                        target_id=manifest.provider_id,
                        target_type="model_provider",
                        trace_id=getattr(task, "task_id", None),
                        details={
                            "violations": control_violations,
                            "error": control_retry_error,
                        },
                    )
            if not ordinary_check.allowed:
                if ordinary_check.reasons == ("unrequested_prior_turn_literal",):
                    trimmed = remove_unrequested_prior_turn_literals(
                        response.output_text,
                        ordinary_policy,
                        current_user_text=current_user_text,
                    )
                    trimmed_check = inspect_ordinary_chat_output(
                        trimmed,
                        ordinary_policy,
                        current_user_text=current_user_text,
                    )
                    if trimmed and trimmed_check.allowed:
                        response.output_text = trimmed
                        ordinary_check = trimmed_check
                    else:
                        response.output_text = ordinary_chat_safe_fallback()
                elif ordinary_check.reasons == ("unsolicited_generic_follow_up",):
                    trimmed = remove_unsolicited_generic_follow_up(response.output_text)
                    trimmed_check = inspect_ordinary_chat_output(
                        trimmed,
                        ordinary_policy,
                        current_user_text=current_user_text,
                    )
                    if trimmed and trimmed_check.allowed:
                        response.output_text = trimmed
                        ordinary_check = trimmed_check
                    else:
                        response.output_text = ordinary_chat_safe_fallback()
                elif ordinary_check.reasons == ("ordinary_response_too_long",):
                    response.output_text = constrain_ordinary_chat_output(
                        response.output_text,
                        ordinary_policy,
                    )
                    ordinary_check = inspect_ordinary_chat_output(
                        response.output_text,
                        ordinary_policy,
                        current_user_text=current_user_text,
                    )
                elif ordinary_check.reasons == ("boilerplate_overanswer",):
                    # Free local and verified-free cloud lanes already received exactly one
                    # same-adapter rewrite above.  If that still overanswers (or this is a paid
                    # lane, where a second call is not authorized), only remove recognized
                    # unsolicited boilerplate. A prefix passing a style guard is not evidence
                    # that the task survived trimming. Never discard lists or later sentences
                    # to manufacture a passing candidate; failure remains explicit.
                    for candidate in (response.output_text, control_failed_draft):
                        recovered = recover_bounded_ordinary_chat_output(
                            candidate,
                            ordinary_policy,
                            current_user_text=current_user_text,
                        )
                        if not recovered:
                            continue
                        response.output_text = recovered
                        ordinary_check = inspect_ordinary_chat_output(
                            recovered,
                            ordinary_policy,
                            current_user_text=current_user_text,
                        )
                        bounded_recovery_applied = True
                        break
                    if not ordinary_check.allowed:
                        response.output_text = ordinary_chat_overanswer_failure(
                            manifest.model_name
                        )
                else:
                    response.output_text = ordinary_chat_safe_fallback()
            elif not language_check.compliant:
                response.output_text = response_language_safe_fallback()
            if response_constraint is not None:
                constraint_fallback_applied = bool(
                    dict(getattr(response, "constraint_result", {}) or {}).get(
                        "fallback_applied"
                    )
                )
                if constraint_fallback_applied:
                    final_application = None
                else:
                    final_application = enforce_response_constraint(
                        response.output_text,
                        response_constraint,
                    )
                if final_application is not None:
                    response.output_text = final_application.text
                response.constraint_result = {
                    **dict(getattr(response, "constraint_result", {}) or {}),
                    "final_compliant": (
                        final_application.compliant
                        if final_application is not None
                        else False
                    ),
                    "final_violations": list(
                        final_application.violations
                        if final_application is not None
                        else dict(
                            getattr(response, "constraint_result", {}) or {}
                        ).get("remaining_violations", [])
                    ),
                }
            if raw_contract is not None:
                # Response-constraint and ordinary-chat fallbacks run after the first raw check.
                # Re-bind the exact bytes at the terminal model boundary so a later fallback can
                # never reintroduce a wrapper, wrong marker, or runtime-authored explanation.
                raw_application = apply_raw_output_contract(
                    response.output_text,
                    raw_contract,
                )
                response.output_text = raw_application.text
                raw_changed = raw_changed or raw_application.changed
                raw_rejected = raw_rejected or raw_application.rejected
                raw_actions.extend(
                    action
                    for action in raw_application.actions
                    if action not in raw_actions
                )
            unresolved_provider_completion = provider_completion.incomplete
            if unresolved_provider_completion:
                incomplete = AnswerCompleteness(
                    incomplete=True,
                    reasons=provider_completion.reasons,
                    has_content=provider_completion.has_content,
                )
                if raw_contract is not None or output_mode in _STRUCTURED_OUTPUT_MODES:
                    # Exact/raw contracts forbid a runtime-authored wrapper. Empty is the only
                    # honest fail-closed result after the one repair was exhausted.
                    response.output_text = ""
                elif provider_completion.has_content:
                    response.output_text = (
                        f"{response.output_text.rstrip()}\n\n{partial_answer_notice(incomplete)}"
                    )
                else:
                    response.output_text = incomplete_answer_notice(incomplete)
                if response_constraint is not None:
                    constraint_state = dict(response.constraint_result or {})
                    final_violations = list(constraint_state.get("final_violations") or [])
                    if "provider_output_incomplete" not in final_violations:
                        final_violations.append("provider_output_incomplete")
                    response.constraint_result = {
                        **constraint_state,
                        "final_compliant": False,
                        "final_violations": final_violations,
                    }
            # The X editorial studio's response edge (C20): when the prompt carried the
            # skill's turn state, the provider's XDraft envelope is validated by the
            # deterministic engine (platform truth, claim grounding, sludge, privacy) and a
            # clean draft is served as EXACTLY its copy-ready text. Any finding withholds
            # the clean copy and names itself — a hostile provider cannot slip an
            # unsupported number, quotation or URL through unnoticed.
            _x_editorial_turn = dict(call_request.metadata.get("x_editorial_turn") or {})
            if _x_editorial_turn:
                from core.x_editorial import apply_x_editorial_output

                _x_application = apply_x_editorial_output(
                    response.output_text,
                    user_text=_current_user_text_from_request(call_request),
                    state=_x_editorial_turn,
                )
                response.output_text = _x_application.text
                response.constraint_result = {
                    **dict(getattr(response, "constraint_result", {}) or {}),
                    "x_editorial": _x_application.record,
                }
                emit_runtime_event(
                    source_context if isinstance(source_context, dict) else {},
                    event_type="x_editorial_validation",
                    message="xdraft validated at the response edge",
                    details=_x_application.record,
                )
            _constraint_stage = dict(getattr(response, "constraint_result", {}) or {})
            response_control = {
                "ordinary_chat_output": ordinary_check.to_dict(),
                "response_language": language_check.to_dict(),
                # The response-CONTROL retry (ordinary-chat / language / raw / completion).
                # The response-constraint stage keeps its own record below so a trace can
                # never read "retry_attempted=false" beside a constraint-retry event again.
                "retry_attempted": control_retry_attempted,
                "retry_succeeded": control_retry_succeeded,
                "retry_error": control_retry_error,
                "response_constraint": {
                    key: _constraint_stage.get(key)
                    for key in (
                        "requested",
                        "initial_violations",
                        "initial_missing_formats",
                        "retry_attempted",
                        "retry_succeeded",
                        "retry_error",
                        "selected_candidate",
                        "fences_normalized",
                        "compliant",
                        "remaining_violations",
                        "fallback_applied",
                    )
                    if key in _constraint_stage
                },
                "bounded_recovery_applied": bounded_recovery_applied,
                "provider_completion": {
                    "initial": initial_provider_completion.as_dict(),
                    "final": provider_completion.as_dict(),
                },
                "fallback_applied": (
                    not ordinary_check.allowed
                    or not language_check.compliant
                    or bool(
                        dict(getattr(response, "constraint_result", {}) or {}).get(
                            "fallback_applied"
                        )
                    )
                    or bool(
                        raw_application is not None
                        and not raw_application.compliant
                    )
                    or unresolved_provider_completion
                ),
                "constraint_violations": list(
                    dict(getattr(response, "constraint_result", {}) or {}).get(
                        "final_violations", []
                    )
                ),
                **(
                    {
                        "raw_output": {
                            "changed": raw_changed,
                            "rejected": raw_rejected,
                            "compliant": raw_application.compliant,
                            "violations": list(raw_application.violations),
                            "actions": raw_actions,
                        }
                    }
                    if raw_application is not None
                    else {}
                ),
            }
            if response_control["fallback_applied"]:
                from core.runtime_task_outcome import output_validation_outcome

                response_control["fulfillment_outcome"] = (
                    output_validation_outcome(response_control) or {}
                )
            response.constraint_result = {
                **dict(getattr(response, "constraint_result", {}) or {}),
                "response_control": response_control,
            }
            if isinstance(source_context, dict):
                source_context["response_control"] = response_control
            response_id = f"response-{uuid.uuid4().hex}"
            response.model_call_id = model_call_id
            response.response_id = response_id
            served_context = dict(
                getattr(response_request, "context", None) or {}
            )
            served_context_manifest_id = str(
                served_context.get("context_manifest_id")
                or context_manifest_id
                or ""
            ).strip()
            served_context_manifest_trace_id = str(
                served_context.get("context_manifest_trace_id")
                or context_manifest_trace_id
                or ""
            ).strip()
            provider_manifest_id = str(
                dict(getattr(response_request, "metadata", None) or {}).get(
                    "provider_manifest_id"
                )
                or ""
            ).strip()
            provider_payload_hash = str(
                dict(getattr(response_request, "metadata", None) or {}).get(
                    "provider_payload_hash"
                )
                or ""
            ).strip()
            if isinstance(source_context, dict) and provider_manifest_id:
                provider_links = source_context.setdefault(
                    "provider_manifest_links", []
                )
                link = {
                    "context_manifest_id": served_context_manifest_id,
                    "context_manifest_trace_id": (
                        served_context_manifest_trace_id
                    ),
                    "provider_manifest_id": provider_manifest_id,
                    "payload_hash": provider_payload_hash,
                    # These identifiers come from the selected manifest rather
                    # than the caller, so the terminal trace can prove which
                    # provider/model produced the visible response without
                    # persisting prompt content or credentials.
                    "provider_id": str(manifest.provider_id or ""),
                    "model_id": str(manifest.model_name or ""),
                }
                if isinstance(provider_links, list) and link not in provider_links:
                    provider_links.append(link)
            record_provider_success(manifest.provider_id)
            _record_attachment_delivery(source_context, response_request, manifest=manifest)
            _emit_model_routing_event(
                source_context,
                "model.call_completed",
                f"Model call completed with {manifest.provider_id}.",
                **identity,
                response_id=response_id,
                provider_id=manifest.provider_id,
                model_id=manifest.model_name,
                # PB01 hook 3: the ROUTING task_kind rides the completed-call event so the
                # sufficiency writer at the turn-finalize seam can key observations by the task
                # kind the ModelSelectionRequest actually selected for — never inferred later.
                # The envelope's kind (when the context carries one) is the fallback; neither is
                # invented when absent, and an empty kind degrades to `unknown` in the writer,
                # which the selector never matches.
                task_kind=str(
                    task_kind
                    or dict((source_context or {}).get("task_envelope") or {}).get("inputs", {}).get("task_kind")
                    or ""
                ),
                locality=_manifest_locality(manifest),
                active_inference=True,
                cost_class=reported_cost_class(manifest),
                prompt_budget=_request_prompt_budget(response_request),
                tool_call_resolution=_request_tool_call_resolution(response_request),
                # `prompt_budget` is the FITTER's telemetry and it is legitimately EMPTY on every
                # cloud call: `_build_openai_payload` passes `context_window=0` for any non-Ollama
                # runtime family, so `_request_messages_with_memory` returns before it writes any.
                # Queried on 2026-08-01 the field was `{}` on every `openrouter-byok` row, which is
                # what left an operator asking the MODEL for its own token count. The provider
                # reported the real one on the same response the whole time.
                token_usage=measured_call_usage(
                    response.usage,
                    provider_id=manifest.provider_id,
                    model_id=manifest.model_name,
                ),
                finish_reason=str(getattr(response, "finish_reason", "") or ""),
                context_manifest_id=served_context_manifest_id,
                context_manifest_trace_id=served_context_manifest_trace_id,
                provider_manifest_id=provider_manifest_id,
                provider_payload_hash=provider_payload_hash,
                response_control=response_control,
                provider_receipt=_provider_receipt(getattr(response, "provider_metadata", None)),
            )
            _emit_grounding_dropped_event(
                source_context,
                manifest=manifest,
                request=call_request,
                identity=identity,
            )
            if paid_authorization is not None:
                # Close the reservation at what the call really cost -- tokens priced at the
                # model's published rate, plus any earlier attempt billed under the same
                # reservation -- so the standing ceiling stops counting against the
                # per-task/day/month caps and the real charge starts counting toward them.
                settle_owner_pick_paid_call(
                    paid_authorization,
                    actual_usd=_response_actual_usd(manifest, response),
                    source_context=source_context,
                    call_role=str((source_context or {}).get("model_call_role") or ""),
                )
            return adapter, response, None
        except PromptBudgetExceededError as exc:
            # A prompt too large for this window is a property of the prompt, not of the
            # provider: record_provider_failure() here would open the circuit on a provider
            # that is answering normally. Remaining candidates are still tried, since one
            # with a larger context window can legitimately accept the same prompt.
            audit_logger.log(
                "model_prompt_budget_rejected",
                target_id=manifest.provider_id,
                target_type="model_provider",
                trace_id=getattr(task, "task_id", None),
                details={"error": str(exc), "prompt_budget": dict(getattr(exc, "telemetry", {}) or {})},
            )
            _emit_model_routing_event(
                source_context,
                "model.call_failed",
                f"Prompt did not fit {manifest.provider_id}'s context window.",
                **identity,
                provider_id=manifest.provider_id,
                model_id=manifest.model_name,
                reason="prompt_budget_exceeded",
                error_kind="prompt_shape",
                provider_health_recorded=False,
                prompt_budget=_request_prompt_budget(call_request),
            )
            if paid_authorization is not None:
                release_owner_pick_paid_call(
                    paid_authorization,
                    reason="prompt_budget_exceeded",
                    source_context=source_context,
                    call_role=str((source_context or {}).get("model_call_role") or ""),
                )
            return adapter, None, f"prompt_budget_exceeded:{exc}"
        except ProviderInvocationValidationError as exc:
            # Manifest sealing failed before any network I/O.  This is a local
            # context-contract defect, not provider evidence, so preserve the
            # fail-closed result without poisoning provider health.
            audit_logger.log(
                "model_context_manifest_rejected",
                target_id=manifest.provider_id,
                target_type="model_provider",
                trace_id=getattr(task, "task_id", None),
                details={"error": str(exc)},
            )
            _emit_model_routing_event(
                source_context,
                "model.call_failed",
                "Model request was rejected before provider invocation.",
                **identity,
                provider_id=manifest.provider_id,
                model_id=manifest.model_name,
                reason="context_manifest_invalid",
                error_kind="request_validation",
                provider_health_recorded=False,
                prompt_budget=_request_prompt_budget(call_request),
            )
            if paid_authorization is not None:
                release_owner_pick_paid_call(
                    paid_authorization,
                    reason="context_manifest_invalid",
                    source_context=source_context,
                    call_role=str((source_context or {}).get("model_call_role") or ""),
                )
            return adapter, None, f"context_manifest_invalid:{exc}"
        except RequiredToolsNotOfferedError as exc:
            # The final pre-invocation boundary caught it: this specific manifest, right as it was
            # about to send, could not actually carry the tool contract the turn requires -- a
            # stale/incorrect manifest, an empty schema builder, or an adapter bug. This is a
            # CAPABILITY mismatch for this candidate, not evidence the provider itself is
            # unhealthy, so provider health stays untouched (same reasoning as the two branches
            # above) and the caller's fallback loop is free to try a different, actually-capable
            # candidate rather than open this one's circuit breaker over it.
            audit_logger.log(
                "model_required_tools_not_offered",
                target_id=manifest.provider_id,
                target_type="model_provider",
                trace_id=getattr(task, "task_id", None),
                details={"error": str(exc)},
            )
            _emit_model_routing_event(
                source_context,
                "model.call_failed",
                "Tools-required turn reached a lane that could not carry the tool contract.",
                **identity,
                provider_id=manifest.provider_id,
                model_id=manifest.model_name,
                reason="required_tools_not_offered",
                error_kind="required_tools_not_offered",
                error_class=ProviderErrorClass.REQUIRED_TOOLS_NOT_OFFERED.value,
                retryable=is_retryable(ProviderErrorClass.REQUIRED_TOOLS_NOT_OFFERED),
                provider_health_recorded=False,
                prompt_budget=_request_prompt_budget(call_request),
            )
            if paid_authorization is not None:
                release_owner_pick_paid_call(
                    paid_authorization,
                    reason="required_tools_not_offered",
                    source_context=source_context,
                    call_role=str((source_context or {}).get("model_call_role") or ""),
                )
            return adapter, None, f"required_tools_not_offered:{exc}"
        except ToolCallParseError as exc:
            # A specific, NAMED tool-call defect (unknown name, malformed JSON arguments, or a
            # duplicate call) caught by core.cloud_tool_call_contract's parser, on either provider
            # lane. This is model-output evidence -- the provider transported the reply fine, the
            # MODEL produced a bad call -- so it is worth tracking as a real signal about this
            # provider/model pairing, unlike RequiredToolsNotOfferedError above (which is a runtime
            # capability mismatch, not model behavior). Never executed: the exception unwound
            # before any tool dispatch could see it.
            error_kind = {
                UnknownToolNameError: "unknown_tool_name",
                MalformedToolArgumentsError: "malformed_tool_arguments",
                DuplicateToolCallError: "duplicate_tool_call",
            }.get(type(exc), "malformed_tool_call")
            # The tool loop decides what a zero-executed-steps turn becomes AFTER this frame has
            # unwound, and "the model expressed a call the runtime refused" must stay
            # distinguishable there from "the provider never answered" — otherwise the refused
            # call ends as a SUCCEEDED plain-chat turn narrating a plan that never ran (measured
            # live 2026-09-01, drive B). source_context is the bridge both layers already share
            # (same pattern as _record_model_provenance).
            if isinstance(source_context, dict):
                source_context["last_tool_call_rejection"] = {
                    "error_kind": error_kind,
                    "provider_id": str(manifest.provider_id or ""),
                    "resolution": _request_tool_call_resolution(call_request) or {},
                }
            record_provider_failure(manifest.provider_id, error=str(exc), timeout=False)
            audit_logger.log(
                "model_tool_call_parse_failed",
                target_id=manifest.provider_id,
                target_type="model_provider",
                trace_id=getattr(task, "task_id", None),
                details={"error": str(exc), "error_kind": error_kind},
            )
            _emit_model_routing_event(
                source_context,
                "model.call_failed",
                "Provider returned a tool call this runtime refused to execute.",
                **identity,
                provider_id=manifest.provider_id,
                model_id=manifest.model_name,
                reason=error_kind,
                error_kind=error_kind,
                error_class=ProviderErrorClass.MALFORMED_TOOL_CALL.value,
                retryable=is_retryable(ProviderErrorClass.MALFORMED_TOOL_CALL),
                provider_health_recorded=True,
                prompt_budget=_request_prompt_budget(call_request),
                tool_call_resolution=_request_tool_call_resolution(call_request),
                provider_receipt=_provider_receipt(getattr(exc, "provider_evidence", None)),
            )
            if paid_authorization is not None:
                release_owner_pick_paid_call(
                    paid_authorization,
                    reason=error_kind,
                    source_context=source_context,
                    call_role=str((source_context or {}).get("model_call_role") or ""),
                )
            return adapter, None, f"{error_kind}:{exc}"
        except ProviderCallAlreadyTerminalized as exc:
            # The API already emitted the one terminal failure receipt before the turn terminal
            # trace.  A worker that escaped the scheduler deadline may finish later, but it has no
            # authority to append a contradictory second terminal event to a closed turn.
            if paid_authorization is not None:
                release_owner_pick_paid_call(
                    paid_authorization,
                    reason="turn_already_terminalized",
                    source_context=source_context,
                    call_role=str((source_context or {}).get("model_call_role") or ""),
                )
            return adapter, None, str(exc)
        except Exception as exc:
            # Typed classification (core/normalized_provider_result.py) FIRST, so provider-health
            # recording can be gated on it: a lane that cannot carry the tool contract at all
            # (Cloudflare/GenericOpenAICloudProvider's own "does not support required tools"
            # RuntimeError, classified REQUIRED_TOOLS_NOT_OFFERED here exactly like the dedicated
            # RequiredToolsNotOfferedError branch above) is a fixed CAPABILITY fact about this
            # lane, not evidence it is unhealthy right now -- penalizing health for it would open
            # the circuit breaker over something retrying will never fix.
            #
            # SWITCHBOARD final micro-repair (N1): `generic_error_class != REQUIRED_TOOLS_NOT_
            # OFFERED` is ALSO true when `generic_error_class` is None -- an exception classify_
            # error_class could not recognize AT ALL (a local bug: TypeError, AttributeError,
            # KeyError, an unmatched RuntimeError -- nothing about the PROVIDER). `None` is not
            # "not REQUIRED_TOOLS_NOT_OFFERED", it is "no claim about the provider either way";
            # only a POSITIVELY classified, non-capability-mismatch class is provider evidence.
            # Reproduced live before this fix: 5 repeated local TypeErrors opened the circuit
            # breaker (consecutive_failures=5, circuit_open=true) for a provider that was never
            # actually unhealthy.
            generic_error_class = classify_error_class(exc)
            is_provider_evidence = (
                generic_error_class is not None
                and generic_error_class != ProviderErrorClass.REQUIRED_TOOLS_NOT_OFFERED
            )
            if is_provider_evidence:
                record_provider_failure(
                    manifest.provider_id,
                    error=str(exc),
                    timeout="timeout" in str(exc).lower(),
                )
            audit_logger.log(
                "model_provider_execution_failed",
                target_id=manifest.provider_id,
                target_type="model_provider",
                trace_id=getattr(task, "task_id", None),
                details={"error": str(exc)},
            )
            # The adapter's own typed evidence for this failure (a pre-send refusal carries its
            # route facts: saved maxima, current prices, and the violating axes in exact human
            # units). Stamped per provider/model so the turn's terminal decision — built later,
            # with only the reason string — can still name the actual axes instead of pointing
            # generically at Settings. Redaction-safe by construction (ids and prices only).
            failure_evidence = getattr(exc, "provider_evidence", None)
            if isinstance(failure_evidence, dict) and isinstance(source_context, dict):
                usepod_body = failure_evidence.get("usepod")
                route_evidence = (
                    usepod_body.get("route_evidence")
                    if isinstance(usepod_body, dict)
                    else None
                )
                if isinstance(route_evidence, dict) and route_evidence:
                    source_context.setdefault("_pin_failure_route_evidence", {})[
                        f"{manifest.provider_id}|{manifest.model_name}"
                    ] = dict(route_evidence)
            _emit_model_routing_event(
                source_context,
                "model.call_failed",
                f"Model call failed with {manifest.provider_id}.",
                **identity,
                provider_id=manifest.provider_id,
                model_id=manifest.model_name,
                reason=str(exc),
                # The exception's TYPE, which `str(exc)` throws away. This branch is the one the
                # local lane dies down, and `classify_error_class` returns None for anything it
                # cannot place -- so a local model call could reach the Activity panel as the bare
                # text "'NoneType' object is not subscriptable" with error_class empty, and nothing
                # anywhere recorded whether that was a transport failure or a defect in this
                # runtime. The class name separates the two in one token. The traceback stays out
                # of the event on purpose: this is a UI-facing row, not a crash dump.
                exception_class=type(exc).__name__,
                error_class=generic_error_class.value if generic_error_class else "",
                retryable=is_retryable(generic_error_class),
                provider_health_recorded=is_provider_evidence,
                prompt_budget=_request_prompt_budget(call_request),
                provider_receipt=_provider_receipt(getattr(exc, "provider_evidence", None)),
                # An empty reply's own evidence and what it says (core.normalized_provider_result):
                # the ceiling ran out, the model reasoned and never answered, text sat in an unread
                # field, nothing was generated -- or the evidence is silent and it stays
                # unclassified. Absent on every other failure.
                empty_reply=_empty_reply_record(exc),
                empty_reply_class=_empty_reply_class(exc),
            )
            if paid_authorization is not None:
                release_owner_pick_paid_call(
                    paid_authorization,
                    reason="call_failed",
                    source_context=source_context,
                    call_role=str((source_context or {}).get("model_call_role") or ""),
                )
            return adapter, None, str(exc)

    def _verify_primary_response(
        self,
        *,
        primary_manifest: Any,
        primary_request: ModelRequest,
        primary_response: ModelResponse,
        ranked_manifests: list[Any],
        autopilot_plan: dict[str, Any],
        task: Any,
        classification: dict[str, Any],
        task_kind: str,
        output_mode: str,
        source_context: dict[str, Any] | None,
        failed_provider_ids: set[str] | None = None,
    ) -> str:
        if not bool(autopilot_plan.get("verifier_required")):
            return "not_required"
        verifier_provider_id = str(autopilot_plan.get("verifier_provider_id") or "").strip()
        if not verifier_provider_id:
            _emit_model_routing_event(
                source_context,
                "model_lane_verifier_blocked",
                "Verifier was required, but no verifier lane was available.",
                lane="verifier",
                lane_type="verifier",
                phase="blocked",
                verifier_status="blocked",
                primary_provider_id=getattr(primary_manifest, "provider_id", ""),
                primary_model_id=getattr(primary_manifest, "model_name", ""),
            )
            return "blocked"
        if verifier_provider_id == str(getattr(primary_manifest, "provider_id", "") or "").strip():
            _emit_model_routing_event(
                source_context,
                "model_lane_verifier_degraded",
                "Verifier required but selected the same model as the primary lane.",
                lane="verifier",
                lane_type="verifier",
                phase="blocked",
                verifier_status="degraded_same_model",
                primary_provider_id=getattr(primary_manifest, "provider_id", ""),
                primary_model_id=getattr(primary_manifest, "model_name", ""),
                verifier_provider_id=verifier_provider_id,
                verifier_model_id=str(autopilot_plan.get("verifier_model") or "").strip(),
            )
            return "degraded_same_model"
        if verifier_provider_id in set(failed_provider_ids or set()):
            _emit_model_routing_event(
                source_context,
                "model_lane_verifier_blocked",
                "Verifier was required, but the selected verifier lane already failed earlier in this turn.",
                lane="verifier",
                lane_type="verifier",
                phase="blocked",
                verifier_status="blocked_failed_lane",
                primary_provider_id=getattr(primary_manifest, "provider_id", ""),
                primary_model_id=getattr(primary_manifest, "model_name", ""),
                verifier_provider_id=verifier_provider_id,
            )
            return "blocked_failed_lane"
        verifier_manifest = next(
            (manifest for manifest in ranked_manifests if manifest.provider_id == verifier_provider_id),
            None,
        )
        if verifier_manifest is None:
            _emit_model_routing_event(
                source_context,
                "model_lane_verifier_blocked",
                "Verifier was required, but the selected verifier manifest was not ranked for execution.",
                lane="verifier",
                lane_type="verifier",
                phase="blocked",
                verifier_status="blocked",
                primary_provider_id=getattr(primary_manifest, "provider_id", ""),
                primary_model_id=getattr(primary_manifest, "model_name", ""),
                verifier_provider_id=verifier_provider_id,
            )
            return "blocked"

        primary_context = dict(primary_request.context or {})
        verifier_context = {
            key: primary_context[key]
            for key in (
                "chat_id",
                "project_id",
                "capsule_version",
                "input_tokens",
                "reserved_output_tokens",
                "redaction_markers",
                "trimming_decisions",
            )
            if key in primary_context
        }
        verifier_context["items_included"] = [
            dict(item)
            for item in list(primary_context.get("items_included") or [])
            if isinstance(item, dict)
        ]
        verifier_context["items_excluded"] = [
            dict(item)
            for item in list(primary_context.get("items_excluded") or [])
            if isinstance(item, dict)
        ]
        primary_output = str(primary_response.output_text or "")
        primary_output_hash = hashlib.sha256(
            primary_output.encode("utf-8")
        ).hexdigest()
        primary_response_id = str(
            primary_response.response_id
            or primary_response.model_call_id
            or primary_request.response_id
            or primary_request.model_call_id
            or f"response-sha256:{primary_output_hash}"
        )
        verifier_context["items_included"].append(
            {
                "item_id": primary_response_id,
                "source_type": "derived_provider_response",
                "scope": "chat",
                "source_id": primary_response_id,
                "content_hash": primary_output_hash,
                "reason": "verifier_primary_response",
                "status": "active",
                "source_class": "provider_response",
                "source": "primary_provider",
                "provenance_kind": "derived_model_output",
            }
        )

        verifier_request = ModelRequest(
            task_kind="verification",
            # The verdict must be grounded in the QUESTION. Before 2026-08-15 this prompt carried
            # only the response plus routing labels, so PASS/FAIL on correctness was structurally
            # blind -- the verifier could not know "Yellow" was wrong for a puzzle it never saw,
            # and the MF-19 escalation would have spent its extra model call on that blind verdict.
            prompt=(
                "Verify the primary model response against the user's request for correctness, "
                "safety, and missing caveats.\n\n"
                "User request:\n"
                f"{str(getattr(task, 'task_summary', '') or '')[:4000]}\n\n"
                f"Task class: {classification.get('task_class', 'unknown')}\n"
                f"Task kind: {task_kind}\n"
                f"Output mode: {output_mode}\n"
                f"Primary provider: {getattr(primary_manifest, 'provider_id', '')}\n"
                f"Primary model: {getattr(primary_manifest, 'model_name', '')}\n\n"
                "Primary response:\n"
                f"{str(primary_response.output_text or '')[:6000]}\n\n"
                "Start your reply with 'VERDICT: PASS' if the response correctly answers the "
                "user's request and is safe, or 'VERDICT: FAIL' if it is wrong for the request, "
                "unsafe, or missing a critical caveat, then a one-line reason."
            ),
            system_prompt="You are VOOL's verifier lane. Be strict, concise, and do not rewrite the answer.",
            context=verifier_context,
            temperature=0.0,
            max_output_tokens=512,
            messages=[],
            output_mode="plain_text",
            trace_id=str(getattr(task, "task_id", "")),
            contract={"mode": "verifier"},
            metadata={
                "task_role": "verifier",
                "defer_stream_until_verified": True,
                "primary_provider_id": str(getattr(primary_manifest, "provider_id", "") or ""),
                "primary_model_id": str(getattr(primary_manifest, "model_name", "") or ""),
            },
        )
        _emit_model_routing_event(
            source_context,
            "model_lane_verifier_started",
            f"Verifier lane started with {verifier_manifest.provider_id}.",
            lane="verifier",
            lane_type="verifier",
            phase="started",
            verifier_status="running",
            primary_provider_id=getattr(primary_manifest, "provider_id", ""),
            primary_model_id=getattr(primary_manifest, "model_name", ""),
            verifier_provider_id=verifier_manifest.provider_id,
            verifier_model_id=verifier_manifest.model_name,
        )
        adapter, verifier_response, error = self._invoke_manifest(
            manifest=verifier_manifest,
            request=verifier_request,
            output_mode="plain_text",
            task=task,
            source_context=source_context,
            # This call is wrapped by model_lane_verifier_started/completed/failed above and
            # below -- the lane pair is the displayed row; see _invoke_manifest.
            lane_receipted=True,
            # The verifier ranked its own manifest under the verification task kind; the
            # observation must carry the kind the selection actually used.
            task_kind="verification",
        )
        if error or adapter is None or verifier_response is None:
            _emit_model_routing_event(
                source_context,
                "model_lane_verifier_failed",
                f"Verifier lane failed with {verifier_manifest.provider_id}.",
                lane="verifier",
                lane_type="verifier",
                phase="failed",
                verifier_status="independent_failed",
                primary_provider_id=getattr(primary_manifest, "provider_id", ""),
                primary_model_id=getattr(primary_manifest, "model_name", ""),
                verifier_provider_id=verifier_manifest.provider_id,
                verifier_model_id=verifier_manifest.model_name,
                fallback_reason=str(error or "no_response"),
            )
            return "independent_failed"
        verdict = _verifier_verdict(str(verifier_response.output_text or ""))
        if verdict == "invalid":
            _emit_model_routing_event(
                source_context,
                "model_lane_verifier_failed",
                f"Verifier lane returned no valid verdict ({verifier_manifest.provider_id}).",
                lane="verifier",
                lane_type="verifier",
                phase="failed",
                verifier_status="independent_malformed",
                primary_provider_id=getattr(primary_manifest, "provider_id", ""),
                primary_model_id=getattr(primary_manifest, "model_name", ""),
                verifier_provider_id=verifier_manifest.provider_id,
                verifier_model_id=verifier_manifest.model_name,
                fallback_reason="malformed_verifier_verdict",
                verifier_output_preview=str(verifier_response.output_text or "")[:280],
            )
            return "independent_malformed"
        status = "flagged" if verdict == "fail" else "independent_completed"
        _emit_model_routing_event(
            source_context,
            "model_lane_verifier_flagged" if status == "flagged" else "model_lane_verifier_completed",
            (
                f"Verifier lane flagged the primary answer ({verifier_manifest.provider_id})."
                if status == "flagged"
                else f"Verifier lane completed with {verifier_manifest.provider_id}."
            ),
            lane="verifier",
            lane_type="verifier",
            phase="completed",
            verifier_status=status,
            primary_provider_id=getattr(primary_manifest, "provider_id", ""),
            primary_model_id=getattr(primary_manifest, "model_name", ""),
            verifier_provider_id=verifier_manifest.provider_id,
            verifier_model_id=verifier_manifest.model_name,
            verifier_output_preview=str(verifier_response.output_text or "")[:280],
        )
        return status

    def _stream_response(
        self,
        *,
        adapter: ModelAdapter,
        manifest: Any,
        request: ModelRequest,
        source_context: dict[str, Any] | None,
        note_attempt: Any | None = None,
    ) -> ModelResponse:
        emitted_chunks: list[str] = []
        raw_events: list[Any] = []
        stream_usage: dict[str, Any] = {}
        # SWITCHBOARD F1: adapters.openai_compatible_adapter.py's streaming paths already capture
        # this correctly per-chunk (ModelStreamChunk.provider_attested_model, sourced only from the
        # provider's own response frames) -- this loop just failed to forward it when assembling
        # the final ModelResponse, so a genuinely provider-attested identity was silently dropped
        # back to None here. Same pattern as stream_usage just above: take the LAST truthy value
        # seen across the stream (a later frame providing identity for the first time, case C in
        # the operator's matrix, is handled for free by this since the adapter's own chunks are
        # monotonic once the value is known). Never derived from requested_model/resolved_model/
        # manifest.model_name/actual_model -- ONLY from what a chunk itself carried.
        stream_attested_model: str | None = None
        stream_finish_reason = ""
        # An adapter's own evidence rides its terminal chunk (ModelStreamChunk.provider_metadata); the
        # response assembled below carries it exactly as the buffered path does.
        stream_provider_metadata: dict[str, Any] = {}
        stream_context = _ephemeral_stream_context(source_context)
        # ``supports_streaming`` and every other arm-selection probe have already succeeded. Mark
        # only now, immediately before entering the stream task method.
        stream = invoke_provider_execution_boundary(
            adapter,
            "stream_text_task",
            request,
            before_call=note_attempt if callable(note_attempt) else None,
        )
        for chunk in stream:
            if chunk.delta_text:
                emitted_chunks.append(chunk.delta_text)
                emit_runtime_event(
                    stream_context,
                    event_type="model_output_chunk",
                    message=chunk.delta_text,
                    details={
                        "provider_id": manifest.provider_id,
                        "model_name": manifest.model_name,
                    },
                )
            if chunk.raw_event is not None:
                raw_events.append(chunk.raw_event)
            if getattr(chunk, "usage", None):
                # Terminal chunk carries the token/cost totals so a streamed turn is metered too.
                stream_usage = dict(chunk.usage)
            if getattr(chunk, "provider_attested_model", None):
                stream_attested_model = chunk.provider_attested_model
            if getattr(chunk, "finish_reason", None):
                stream_finish_reason = str(chunk.finish_reason)
            if isinstance(getattr(chunk, "provider_metadata", None), dict) and chunk.provider_metadata:
                stream_provider_metadata = dict(chunk.provider_metadata)
        joined_text = strip_reasoning_block("".join(emitted_chunks))
        if not str(joined_text or "").strip():
            # A7 W3 (LAW 7): a provider stream that ended with zero content is
            # NOT silently reclassified as a successful semantic answer. It is
            # failure/no-answer truth — raised so the turn terminalizes typed
            # instead of persisting empty bytes under confidence 0.65.
            from core.normalized_provider_result import EmptyProviderResponseError

            empty_stream = EmptyProviderResponseError(
                f"provider stream produced no content "
                f"(provider_id={manifest.provider_id} model={manifest.model_name})"
            )
            # The stream's own facts ride the error in the SAME diagnostic shape the buffered
            # path attaches (`empty_reply_diagnostics`), built from what a stream can know: the
            # terminal finish reason and the usage block (reasoning tokens when the provider
            # reports them). Without this, a reasoning model that streamed its whole ceiling as
            # monologue arrived UNTYPED -- indistinguishable from an upstream that said nothing
            # -- and the exhaustion recovery could not see it.
            try:
                usage = dict(stream_usage or {})
                details = dict(usage.get("completion_tokens_details") or {})
                reasoning_tokens = details.get("reasoning_tokens")
                empty_stream.diagnostics = {  # type: ignore[attr-defined]
                    "finish_reason": stream_finish_reason,
                    "native_finish_reason": "",
                    "completion_tokens": usage.get("completion_tokens"),
                    "reasoning_tokens": reasoning_tokens,
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "total_tokens": usage.get("total_tokens"),
                    "max_tokens_sent": (getattr(request, "max_output_tokens", None) or None),
                    "content_present": False,
                    "content_chars": 0,
                    "reasoning_present": bool(reasoning_tokens),
                    "reasoning_chars": 0,
                    "tool_calls_present": False,
                    "tool_call_count": 0,
                    "legacy_text_present": False,
                    "refusal_present": False,
                }
            except Exception:
                pass
            if stream_provider_metadata:
                # The call was served and settled by the adapter; its receipt outlives the empty answer.
                empty_stream.provider_evidence = stream_provider_metadata  # type: ignore[attr-defined]
            raise empty_stream
        return ModelResponse(
            # The adapter cannot strip a `<think>` block per NDJSON frame (the tags span frames),
            # but joined, the full text is in hand — strip the leading block here so a streamed
            # turn's buffered text matches the non-stream path, and matches what the web release
            # gate lets on the wire.
            output_text=joined_text,
            confidence=float(manifest.metadata.get("confidence_baseline") or 0.65),
            raw_response=raw_events,
            usage=stream_usage,
            # No `tool_calls`: the router builds this response itself from text chunks, and the
            # streaming path reads only `content` - a streamed reply carrying native calls loses
            # them before any adapter question arises. So the batch record is a NON-STREAMING
            # capability today. Stated here rather than left as an empty tuple that reads like
            # "the provider sent none".
            provider_id=manifest.provider_id,
            model_name=manifest.model_name,
            output_mode=request.output_mode,
            provider_attested_model=stream_attested_model,
            finish_reason=stream_finish_reason,
            provider_metadata=stream_provider_metadata,
        )

    def _maybe_race_manifests(
        self,
        *,
        ranked_manifests: list[Any],
        request: ModelRequest,
        output_mode: str,
        allow_paid_fallback: bool,
        task: Any,
        source_context: dict[str, Any] | None,
        task_kind: str = "",
    ) -> tuple[Any | None, ModelAdapter | None, ModelResponse | None, list[str], bool]:
        if _streaming_requested(source_context, output_mode=output_mode):
            return None, None, None, [], False
        if not allow_paid_fallback:
            return None, None, None, [], False
        if not ranked_manifests or _manifest_locality(ranked_manifests[0]) != "local":
            return None, None, None, [], False
        budget = get_active_compute_budget()
        if not source_context and str(getattr(budget, "mode", "") or "").strip() != "max_push":
            return None, None, None, [], False
        if int(budget.worker_pool_cap) < 2:
            return None, None, None, [], False
        race_pair = _local_remote_race_pair(ranked_manifests)
        if not race_pair:
            return None, None, None, [], False

        local_manifest, remote_manifest = race_pair
        attempted: list[str] = []
        result_queue: queue.Queue[tuple[Any, ModelAdapter | None, ModelResponse | None, str | None]] = queue.Queue()

        def _worker(manifest: Any) -> None:
            adapter, response, error = self._invoke_manifest(
                manifest=manifest,
                request=request,
                output_mode=output_mode,
                task=task,
                source_context=source_context,
                task_kind=task_kind,
            )
            result_queue.put((manifest, adapter, response, error))

        # `threading.Thread` starts with an EMPTY context, so a ContextVar set for this turn is
        # invisible inside the worker -- the turn's reach recorder among them, which meant a
        # provider call made on this path was never recorded and the receipt read "none attempted"
        # for a turn that really did call one. `copy_context()` carries the turn in with the work.
        for manifest in (local_manifest, remote_manifest):
            thread = threading.Thread(
                target=contextvars.copy_context().run,
                args=(_worker, manifest),
                name=f"vool-provider-race-{manifest.provider_name}",
                daemon=True,
            )
            thread.start()

        remaining = 2
        while remaining > 0:
            manifest, adapter, response, error = result_queue.get()
            remaining -= 1
            if error or response is None:
                attempted.append(manifest.provider_id)
                continue
            return manifest, adapter, response, attempted, True
        for manifest in (local_manifest, remote_manifest):
            if manifest.provider_id not in attempted:
                attempted.append(manifest.provider_id)
        return None, None, None, attempted, True

    def _maybe_mux_manifests(
        self,
        *,
        ranked_manifests: list[Any],
        request: ModelRequest,
        output_mode: str,
        task: Any,
        source_context: dict[str, Any] | None,
        preferred_provider: str,
        preferred_model: str,
        task_kind: str = "",
    ) -> tuple[Any | None, ModelAdapter | None, ModelResponse | None, list[str], bool]:
        """Opt-in "think harder" multi-model mux: sample the top-N distinct LOCAL models concurrently and
        return the answer they most agree on (self-consistency). Off unless VOOL_SLM_MUX_MODE is set;
        returns a no-op tuple (mux_used=False) whenever any guard fails, so the caller falls through to
        the normal race/sequential path completely unchanged."""
        from core.agent_runtime.think_harder import mux_sample_count, think_harder_enabled

        if not think_harder_enabled():
            return None, None, None, [], False
        # Never mux a streaming or structured-output turn, and never override an explicit user pick.
        if _streaming_requested(source_context, output_mode=output_mode):
            return None, None, None, [], False
        if output_mode in _STRUCTURED_OUTPUT_MODES:
            return None, None, None, [], False
        if str(preferred_model or "").strip() or str(preferred_provider or "").strip():
            return None, None, None, [], False

        # Top-N distinct-model local candidates, in rank order.
        picks: list[Any] = []
        seen_models: set[str] = set()
        for manifest in ranked_manifests:
            if _manifest_locality(manifest) != "local":
                continue
            model_key = str(getattr(manifest, "model_name", "") or "")
            if model_key in seen_models:
                continue
            seen_models.add(model_key)
            picks.append(manifest)
            if len(picks) >= mux_sample_count():
                break
        if len(picks) < 2:
            return None, None, None, [], False  # nothing to vote across -> normal path

        rank_index = {manifest.provider_id: i for i, manifest in enumerate(ranked_manifests)}
        result_queue: queue.Queue[tuple[Any, ModelAdapter | None, ModelResponse | None, str | None]] = queue.Queue()

        def _worker(manifest: Any) -> None:
            try:
                adapter, response, error = self._invoke_manifest(
                    manifest=manifest,
                    request=request,
                    output_mode=output_mode,
                    task=task,
                    source_context=source_context,
                    task_kind=task_kind,
                )
            except Exception as exc:  # _invoke_manifest can raise before its own try (build_adapter/health_check)
                adapter, response, error = None, None, str(exc)
            result_queue.put((manifest, adapter, response, error))  # always enqueue -> the collector can't hang

        for manifest in picks:
            # Same reason as the race path: an empty context loses the turn's reach recorder.
            threading.Thread(
                target=contextvars.copy_context().run,
                args=(_worker, manifest),
                name=f"vool-mux-{manifest.provider_id}",
                daemon=True,
            ).start()

        attempted: list[str] = []
        succeeded: list[tuple[Any, ModelAdapter, ModelResponse]] = []
        remaining = len(picks)
        while remaining > 0:
            manifest, adapter, response, error = result_queue.get()
            remaining -= 1
            if error or adapter is None or response is None:
                attempted.append(manifest.provider_id)
                continue
            succeeded.append((manifest, adapter, response))

        if not succeeded:
            return None, None, None, attempted, True  # all mux samples failed -> normal fallback
        winner = _mux_vote(succeeded, rank_index)
        # Providers that ran but lost the vote still count as attempted for the proof trail.
        for manifest, _adapter, _response in succeeded:
            if manifest.provider_id != winner[0].provider_id and manifest.provider_id not in attempted:
                attempted.append(manifest.provider_id)
        return winner[0], winner[1], winner[2], attempted, True

    def _decision_from_response(
        self,
        *,
        manifest: Any,
        adapter: ModelAdapter,
        response: ModelResponse,
        task_hash: str,
        task: Any,
        classification: dict[str, Any],
        context_result: Any,
        task_kind: str,
        output_mode: str,
        provider_role: ProviderRole,
        ranked_manifests: list[Any],
        attempted: list[str],
        failover_used: bool,
        source: str,
        autopilot_plan: dict[str, Any] | None = None,
        lane_proof: dict[str, Any] | None = None,
        cloud_escalation_authorized: bool = False,
        source_context: dict[str, Any] | None = None,
        attempt_timings: list[dict[str, Any]] | None = None,
    ) -> ModelExecutionDecision:
        # Publish this turn's model provenance for `core.runtime_evidence` at the same single funnel
        # the paid-cloud meter below uses, and for the same reason: it is the one place every served
        # response passes through, whatever lane reached it (direct selection, race, mux, failover)
        # and whether or not it was streamed. Placed BEFORE the meter so an exception in metering
        # cannot leave the turn with an answer and no provenance.
        _record_model_provenance(source_context, manifest=manifest, response=response)
        # Meter a BYOK cloud burst against the daily cap at the single funnel every served
        # response passes through, so a paid lane reached by direct selection, race, mux, or
        # failover is counted once per real API response (a response here means the call
        # already happened and the user's key was spent) — and only when the auto policy
        # authorized it, never an explicit paid request (which sets the flag False).
        if cloud_escalation_authorized and provider_cost_class(manifest) == "paid_cloud":
            cloud_escalation_policy.record_escalation()
        try:
            from core.context_retrieval import get_last_retrieval_telemetry, update_retrieval_telemetry

            usage = dict(response.usage or {})
            telemetry = get_last_retrieval_telemetry()
            prompt_eval_count = int(usage.get("prompt_eval_count") or 0)
            update_retrieval_telemetry(
                model_prompt_tokens=prompt_eval_count,
                prompt_eval_count=prompt_eval_count,
                model_calls=int(telemetry.get("model_calls") or 0) + 1,
            )
        except Exception:
            pass
        # Record this served response in the token usage meter (free-local vs paid-cloud), then
        # surface the same numbers as a live runtime event so the trace rail / any streaming
        # client can show per-turn tokens + cost and which lane (local vs cloud) was billed.
        usage_summary = _record_response_usage(manifest, response)
        if usage_summary is not None:
            # Stash for the API layer's per-turn tokens/cost footer (buffered + streamed paths).
            record_turn_usage(usage_summary)
            # The same numbers, on the turn's own ledger rather than on this THREAD. `record_turn_usage`
            # writes a `threading.local()`, and the conductor and the tool planner both run provider
            # calls on a `ThreadPoolExecutor` -- so the API thread that renders the footer read None
            # and the footer invented a lane. Measured 2026-08-12: a conductor turn whose Activity
            # ledger showed a cloud Nemotron call rendered `local | model | tokens unreported`.
            from core.turn_model_call_ledger import record_served_usage

            record_served_usage(
                source_context,
                usage_summary,
                # A tool-intent response is a tool CHOICE, not served bytes: metered, not authored.
                authored=str(output_mode or "") != "tool_intent",
            )
        if (
            usage_summary is not None
            and source_context is not None
            and (usage_summary["prompt_tokens"] or usage_summary["output_tokens"])
        ):
            total_tokens = int(usage_summary["prompt_tokens"]) + int(usage_summary["output_tokens"])
            _emit_model_routing_event(
                source_context,
                "model_usage",
                f"{usage_summary['provider_id']} used {total_tokens} tokens.",
                provider_id=usage_summary["provider_id"],
                model_id=usage_summary["model_id"],
                cost_class=usage_summary["cost_class"],
                prompt_tokens=usage_summary["prompt_tokens"],
                output_tokens=usage_summary["output_tokens"],
                usd_actual=usage_summary["usd_actual"],
                turn_usage_details=turn_call_accounting(source_context).get("usage_details", []),
                model_call_id=response.model_call_id,
                response_id=response.response_id,
            )
        # The single normalized boundary both response types convert through (System B's
        # _try_free_cloud_boost goes through the identical function for CloudModelResponse).
        # `.tool_calls` below is sourced from THIS, not read off `response` directly a second way.
        normalized = normalize_model_response(
            response,
            requested_model=str((source_context or {}).get("requested_model") or "").strip(),
            resolved_provider=manifest.provider_id,
            resolved_model=manifest.model_name,
        )
        # The RESPONSE's mode, not the request's. The adapter repairs a prose tool call back into a
        # dispatchable intent, and when it does so on a turn that asked for plain text it says so by
        # flipping this field. Validating such a reply as plain text would hand the operator the
        # tool-call JSON as their answer — the failure this whole guard exists to remove.
        validation = validate_provider_output(
            provider_id=manifest.provider_id,
            output_mode=_effective_output_mode(response, output_mode),
            raw_text=response.output_text,
            trace_id=str(getattr(task, "task_id", "")),
        )
        freshness = freshness_score(None, None)
        trust = output_trust_score(
            manifest=manifest,
            raw_confidence=float(response.confidence or 0.5),
            contract_ok=validation.ok,
            trust_penalty=validation.trust_penalty,
            freshness_score=freshness,
            reviewed=False,
            agreement_score=min(1.0, float(context_result.retrieval_confidence_score or 0.0)),
        )
        # A9: fail the cache scope CLOSED on write, mirroring the read-side guard —
        # `_candidate_cache_scope` returns "" when session/turn identity is absent. Recording an
        # unscooped candidate under a phrasing-only task_hash would place this answer in a shared
        # cross-chat namespace no future scoped reader can safely distinguish.
        candidate_id: str | None = None
        if _candidate_cache_scope(source_context):
            candidate_id = record_candidate_output(
                task_hash=task_hash,
                task_id=str(getattr(task, "task_id", "")),
                trace_id=str(getattr(task, "task_id", "")),
                task_class=str(classification.get("task_class", "unknown")),
                task_kind=task_kind,
                output_mode=output_mode,
                provider_name=manifest.provider_name,
                model_name=manifest.model_name,
                raw_output=response.output_text,
                normalized_output=validation.normalized_text,
                structured_output=validation.structured_output,
                confidence=float(response.confidence or 0.5),
                trust_score=trust,
                validation_state="valid" if validation.ok else "contract_failed",
                metadata={
                    "cost_class": reported_cost_class(manifest),
                    "warnings": validation.warnings,
                    "context_retrieval_confidence": context_result.report.retrieval_confidence,
                    "response_constraint": dict(
                        response.constraint_result or {}
                    ),
                },
                provenance={
                    **adapter.get_license_metadata(),
                    "provider_id": manifest.provider_id,
                    "output_mode": output_mode,
                },
                ttl_seconds=default_ttl_seconds(task_kind=task_kind, output_mode=output_mode),
            )
        if candidate_id:
            audit_logger.log(
            "model_candidate_recorded",
            target_id=candidate_id,
            target_type="candidate_knowledge",
            trace_id=str(getattr(task, "task_id", "")),
            details={
                "provider_id": manifest.provider_id,
                "task_kind": task_kind,
                "output_mode": output_mode,
                "validation_ok": validation.ok,
                "trust_score": trust,
                "execution_source": source,
            },
        )
        return ModelExecutionDecision(
            source=source,
            task_hash=task_hash,
            provider_id=manifest.provider_id,
            provider_name=manifest.provider_name,
            model_name=manifest.model_name,
            # The guard's block path used to be undone one line later: on a HARD contract failure
            # normalized_text is "" by design (the reply was purely a foreign tool call), and the
            # bare `or` then handed the RAW leaked text straight back. Scrub the fallback so the
            # rejection actually holds -- a pure-leak turn yields "" and degrades, as intended.
            output_text=validation.normalized_text or scrub_foreign_markers(response.output_text),
            structured_output=validation.structured_output,
            confidence=float(response.confidence or 0.5),
            trust_score=trust,
            used_model=True,
            candidate_id=candidate_id,
            failover_used=failover_used,
            validation_state="valid" if validation.ok else "contract_failed",
            # The single funnel for every served provider response, so this one line covers the
            # mux winner, the provider race winner and ordinary execution alike. `response.tool_calls`
            # was filled by every adapter and read by nothing until now.
            tool_calls=normalized.tool_calls,
            details={
                "model_call_id": response.model_call_id,
                "response_id": response.response_id,
                # Carried on the DECISION, not read back out of the `_TURN_USAGE` thread-local:
                # `_invoke_manifest` runs candidates on concurrent threads (`_maybe_mux_manifests`,
                # the local/remote race), so a thread-local is the wrong carrier for "what did the
                # call that produced THIS answer cost". The answering path holds this object.
                "token_usage": measured_call_usage(
                    response.usage,
                    provider_id=manifest.provider_id,
                    model_id=manifest.model_name,
                ),
                "warnings": validation.warnings,
                "contract_error": validation.error,
                "response_constraint": dict(
                    response.constraint_result or {}
                ),
                "provider_role": provider_role,
                "locality": _manifest_locality(manifest),
                "active_inference": True,
                "ranked_candidates": [entry.provider_id for entry in ranked_manifests],
                "attempted": attempted,
                # What the turn spent, per call. Present on the ANSWERING decision too, not just on
                # the failures: a turn that answered after two slow attempts is exactly the case
                # the smoke run could not diagnose.
                **(
                    {
                        "attempt_timings": list(attempt_timings),
                        "attempt_timing_summary": summarize_attempt_timings(attempt_timings),
                    }
                    if attempt_timings
                    else {}
                ),
                **({"autopilot_plan": autopilot_plan} if autopilot_plan else {}),
                **({"lane_proof": lane_proof} if lane_proof else {}),
            },
        )

    def _execute_provider_task(
        self,
        *,
        task: Any,
        classification: dict[str, Any],
        interpretation: Any,
        context_result: Any,
        persona: Any,
        task_hash: str,
        task_kind: str,
        output_mode: str,
        allow_paid_fallback: bool,
        provider_role: ProviderRole,
        surface: str,
        source_context: dict[str, Any] | None,
    ) -> ModelExecutionDecision:
        preferred_provider, preferred_model = self._requested_model_preferences(source_context)
        requested_manifest = self._requested_model_manifest(source_context)
        requested_model_raw = str((source_context or {}).get("requested_model") or "").strip()
        # An Auto lane is not a model id. `vool-local-only` in particular must not be looked up as
        # a manifest and then fail closed as MODEL_UNAVAILABLE — it is a routing mode, and the
        # runtime is meant to choose a local model under it.
        requested_model_explicit = bool(requested_model_raw) and not is_auto_selection(requested_model_raw)
        if (
            requested_model_explicit
            and requested_manifest is None
            and str((source_context or {}).get("model_selection") or "").strip().lower() == "sticky"
        ):
            # A sticky Auto preference is not an operator pin (MF-22). The composer showed Auto;
            # the concrete id rode along from the chat's last cloud model, and when a catalog
            # refresh made that name unresolvable, this turned into the pinned refusal -- eight
            # refused turns in two minutes on a run where the model itself was healthy. Under Auto
            # the user's instruction IS "route for me": drop the unresolvable preference, continue
            # to ranking, and say so in the receipts.
            _emit_model_routing_event(
                source_context,
                "model_routing_started",
                f"Sticky model '{requested_model_raw}' did not resolve; continuing on auto routing.",
                rejection_reason="sticky_model_unresolved_degraded_to_auto",
                requested_model=requested_model_raw,
                provider_role=provider_role,
            )
            requested_model_explicit = False
            preferred_provider = ""
            preferred_model = ""
        if requested_model_explicit and requested_manifest is None:
            # An explicit pick that does not resolve to exactly one live manifest (unknown name, a
            # real provider paired with an unknown/removed model, or a model_name shared by more
            # than one manifest) must never fall through to ranking. `rank_providers`' relaxed pass
            # treats an unresolved `preferred_model` as a scoring bonus only, not a requirement, so
            # ANY eligible candidate can silently win the turn: the user asks for X and gets Y with
            # no signal a substitution happened. Fail closed instead -- MODEL_UNAVAILABLE, never an
            # answer from a different model.
            # Under the canonical LocalModelPolicy's disabled mode the registry listing shows no
            # local manifests, so an explicit LOCAL pin lands here too. The ungated lookup below
            # classifies it — a name that WOULD have been a local manifest refuses with the typed
            # reason `local_models_disabled` instead of the generic unresolvable-model refusal, so
            # the operator sees the true cause. Both refusals are terminal; neither substitutes.
            local_disable_policy = resolve_local_model_policy()
            if local_disable_policy.local_models_disabled and ungated_local_manifest_for_request(
                requested_model_raw
            ) is not None:
                return self._local_model_disabled_decision(
                    requested_model_raw,
                    task=task,
                    classification=classification,
                    task_kind=task_kind,
                    output_mode=output_mode,
                    provider_role=provider_role,
                    source_context=source_context,
                    task_hash=task_hash,
                )
            return self._model_unavailable_decision(
                requested_model_raw,
                task=task,
                classification=classification,
                task_kind=task_kind,
                output_mode=output_mode,
                provider_role=provider_role,
                source_context=source_context,
                task_hash=task_hash,
            )
        # An EXPLICIT PIN is a concrete non-auto model the user actively selected for THIS turn --
        # a composer pin or a persisted pin, both arriving as `requested_model` with a
        # `model_selection` that is anything but "sticky" (the ingress reads "pin" by default; see
        # core/web/api/service.py). A "sticky" preference is Auto's per-chat stickiness (MF-22), NOT
        # a pin, so it keeps Auto's fallback -- including the free-cloud boost. `requested_model_
        # explicit` is already False for an Auto token and for a sticky id that did not resolve
        # (degraded above); the extra `!= "sticky"` also excludes a sticky id that DID resolve. Only
        # an explicit pin is held to the invariant "X runs, or the turn says why X could not run;
        # no other model silently answers instead."
        pin_selection_mode = str((source_context or {}).get("model_selection") or "").strip().lower()
        explicit_pin_selected = requested_model_explicit and pin_selection_mode != "sticky"
        requested_paid_cloud = requested_manifest is not None and provider_cost_class(requested_manifest) == "paid_cloud"
        # `turn_is_local_only` subsumes `policy_engine.local_only_mode()` and adds the per-turn lane,
        # so the composer's Local Only selection binds the paid arm exactly as the machine-wide
        # setting always has — including against `requested_paid_cloud`, which is otherwise the one
        # input that can turn the paid arm on by itself.
        resolved_allow_paid = (bool(allow_paid_fallback) or requested_paid_cloud) and not turn_is_local_only(source_context)
        # The BYOK cloud-escalation policy governs only the AUTO burst: off keeps it local,
        # ask requires a per-call permission signal, auto allows it up to a daily cap. An
        # explicit paid-cloud request by the user is honored regardless.
        resolved_allow_paid, cloud_escalation_authorized = _gate_paid_by_cloud_escalation(
            resolved_allow_paid=resolved_allow_paid,
            allow_paid_fallback=bool(allow_paid_fallback),
            requested_paid_cloud=requested_paid_cloud,
            source_context=source_context,
        )
        # A paid cloud lane executes ONLY against a reservation the server built for THIS turn.
        # The caller can never supply one (``authorized_paid_call`` is in RESERVED_TRUST_KEYS and
        # is stripped from every inbound body), so it is built here instead, from server-held
        # state only: the manifest the explicit pick resolved to in this process's own registry,
        # the server-stamped owner-local flag, the persisted daily cap, and the spend ledger.
        # ``requested_manifest`` is passed only when the turn was EXPLICITLY pointed at a paid
        # model, so an auto fallback gets no reservation no matter what the policy says — and no
        # reservation means resolved_allow_paid stays False and every paid manifest is excluded.
        pin_reservation_denial: dict[str, str] = {}
        authorized_paid_call = reserve_owner_pick_paid_call(
            manifest=requested_manifest if requested_paid_cloud else None,
            task=task,
            source_context=source_context,
            task_kind=task_kind,
            call_role="answer_generation",
            denial=pin_reservation_denial,
        )
        # AN INTERNAL ROUTING CALL IS NOT THE OWNER'S PICK EXECUTING (the reservation's own
        # law: "The pick authorizes the answering lane, not the turn's internal
        # classification calls"). Before this, a tool_intent step under an explicit paid pin
        # took that by-design refusal into `_selected_model_blocked_decision` and the ledger
        # narrated "Selected model 'X' could not run (internal_tool_intent_call)" -- a
        # provider failure that never happened, over a model that answers the turn fine.
        # For THIS internal call the pin simply does not apply: selection falls to the
        # runtime's ordinary lane, the pin keeps answering the turn, and no substitution of
        # the owner's pick occurred anywhere.
        if (
            authorized_paid_call is None
            and requested_paid_cloud
            and str(pin_reservation_denial.get("reason") or "") == "internal_tool_intent_call"
            and str(task_kind or "").strip().lower() == "tool_intent"
        ):
            _emit_model_routing_event(
                source_context,
                "model_routing_started",
                "Internal tool-selection step: routed on the runtime lane; the owner's model pick answers the turn.",
                task_kind=task_kind,
                output_mode=output_mode,
                provider_role=provider_role,
                lane="internal_routing",
                lane_type="internal_routing",
                phase="routing",
                requested_model=str(requested_model_raw or ""),
            )
            requested_manifest = None
            requested_paid_cloud = False
            explicit_pin_selected = False
        # The free-cloud boost is the fallback for when no ranked candidate served, and it selects
        # a model of its own. The reservation authorizes the ONE model the owner picked, so it is
        # deliberately withheld here: the boost keeps routing free-only, exactly as before.
        free_boost_context = source_context
        if authorized_paid_call is None:
            resolved_allow_paid = False
        else:
            # Attach server-side, on a copy, so the reservation reaches the gates below and the
            # invocation of the picked manifest without being written back into the caller's
            # context.
            source_context = {
                **(source_context or {}),
                "authorized_paid_call": authorized_paid_call,
                # Server-authored only after a real reservation exists. This role joins the
                # model.call_* pair to the reservation and terminal spend receipts.
                "model_call_role": "answer_generation",
            }
        # A9 P0 — mint the turn's ONE routing plan BEFORE ranking (and therefore before any
        # provider spend). One typed TurnRoutingPlan carries the requested/selected identity,
        # the locality/privacy ceilings, the paid fence, the fallback ladder and the context
        # identity; the ranking below is then FENCED by it (same eligibility decision), and
        # `_invoke_manifest` enforces it at the one seam every provider call crosses. A turn
        # without routing identity fails closed here — no plan, no spend.
        routing_user_text = _interpreted_user_text(interpretation, task)
        routing_plan = _mint_routing_plan_for_turn(
            self.registry,
            source_context=source_context,
            requested_provider=preferred_provider,
            requested_model=preferred_model if requested_model_explicit else "",
            allow_paid=resolved_allow_paid,
            local_only=turn_is_local_only(source_context),
            user_text=routing_user_text,
        )
        if routing_plan is None:
            # The reservation was built (before this gate) and this turn will never invoke the
            # picked model, so hand back a still-standing reservation rather than leaving it against
            # the caps. Guarded: a no-op if it was somehow already terminalized.
            _release_undispatched_pin_reservation(authorized_paid_call, source_context=source_context)
            return ModelExecutionDecision(
                source="routing_identity_missing",
                task_hash=task_hash,
                used_model=False,
                failover_used=False,
                details={
                    "attempted": [],
                    "reason": "routing_identity_missing",
                    "provider_role": provider_role,
                    "requested_model": str((source_context or {}).get("requested_model") or "").strip(),
                    "ranked_candidates": [],
                    # The plan mint is the fail-closed identity gate: an unnamed turn is
                    # refused BEFORE ranking, so `attempted` stays empty by construction.
                    "provider_inventory": _provider_inventory_snapshot(self.registry),
                },
            )
        # The plan rides a COPY of the context (same law as the paid reservation above): the
        # caller's dict is never mutated, so nothing sticky outlives this method's frames.
        source_context = {
            **(source_context or {}),
            _turn_routing.TURN_ROUTING_PLAN_KEY: routing_plan,
        }
        ranked_manifests = rank_provider_candidates(
            self.registry,
            task_kind=task_kind,
            output_mode=output_mode,
            role=provider_role,
            preferred_provider=preferred_provider,
            preferred_model=preferred_model,
            allow_paid_fallback=resolved_allow_paid,
            swarm_size=4,
            min_trust=0.45,
            enforce_hardware_fit=True,
            local_only=turn_is_local_only(source_context),
        )
        ranked_manifests = [
            manifest
            for manifest in ranked_manifests
            # A vision/multimodal or embedding model (moondream, llava, nomic-embed, ...) can never
            # answer a text turn — keep it out of the chat candidate pool so a contract-failed
            # escalation never falls through to it and returns garbage. Cloud text models pass.
            if is_text_generation_ollama_model(manifest.model_name)
            and (
                provider_cost_class(manifest) != "paid_cloud"
                or is_verified_free_cloud_manifest(manifest)
                or _paid_call_authorization(source_context, manifest=manifest, task=task) is not None
            )
        ]
        # A composer/model-menu pick is a strict execution contract, not a ranking hint.  Falling
        # through to another local or cloud model after the selected adapter fails makes the UI lie
        # and produces the visible local/cloud ping-pong.  Auto routing still keeps its normal
        # fallback behavior because it has no resolved requested_manifest.
        if requested_manifest is not None:
            pinned = [
                manifest
                for manifest in ranked_manifests
                if manifest.provider_id == requested_manifest.provider_id
            ]
            # AUTHORSHIP ESCALATION FOR THE PINNED CONTRACT. The pin above is the
            # composer/model-menu contract and stays authoritative — UNLESS every pinned
            # candidate is an uncertified local final-answer author the precall fence
            # will refuse (e.g. the auto-registered default model after a home seeded
            # with a certified stub). Refusing to widen here made the turn attempt one
            # provider and fail (measured), when a certified local author was available.
            # Keeping the pre-pin ordering (which ranks certified local authors first)
            # is the escalation; the fence at the call remains the authority.
            from core.final_answer_authorship import local_manifest_authorship_certified

            _pinned_certified_local = any(
                m is not None and not _manifest_is_local(m) or local_manifest_authorship_certified(m)
                for m in pinned
            ) if pinned else True
            if pinned and not _pinned_certified_local:
                _widened_certified = [
                    manifest
                    for manifest in ranked_manifests
                    if _manifest_is_local(manifest)
                    and local_manifest_authorship_certified(manifest)
                ]
                if _widened_certified:
                    ranked_manifests = pinned + _widened_certified
                else:
                    ranked_manifests = pinned
            else:
                ranked_manifests = pinned
        # A9 P0 — the plan fence: ranking is intersected with the plan's ONE eligibility
        # decision AND its fallback ladder, BEFORE capability truth and the autopilot see
        # the candidate set, so no downstream reorder can resurrect a candidate the plan
        # refused. Same decision the broker enforces at `_invoke_manifest` — ranker and
        # broker consume one truth.
        _ladder_members = set(routing_plan.fallback_ladder)
        ranked_manifests = [
            manifest
            for manifest in ranked_manifests
            if manifest.provider_id in _ladder_members
            and (row := routing_plan.permits(manifest.provider_id, manifest.model_name)) is not None
            and row.allowed
        ]
        capability_truth = hydrate_capability_truth_with_benchmarks(
            tuple(provider_capability_truth_for_manifest(entry) for entry in ranked_manifests)
        )
        capability_by_provider = {item.provider_id: item for item in capability_truth}
        _llamacpp_provider_id = next(
            (m.provider_id for m in ranked_manifests if "llamacpp" in m.provider_id.lower()),
            "",
        )
        _accel = backend_acceleration_proof(
            provider_id=_llamacpp_provider_id,
            backend="llama.cpp" if _llamacpp_provider_id else "",
            probe=False,
        )
        _eagle3_active = _accel.eagle_status in {"active", "configured_not_proven"}
        autopilot = build_local_inference_autopilot_plan(
            user_text=_interpreted_user_text(interpretation, task),
            # The user's own words, when the interpretation was built from a composed prompt:
            # heavy-demand truth must come from what the person said, never from numbers in the
            # runtime's own observations scaffolding (spliced into normalized_text).
            user_demand_text=(
                str(getattr(interpretation, "user_demand_text", "") or "").strip() or None
            ),
            task_kind=task_kind,
            output_mode=output_mode,
            provider_role=provider_role,
            capability_truth=capability_truth,
            source_context=source_context,
            free_vram_gb=_cached_free_vram_gb(),
            eagle3_active=_eagle3_active,
            default_model_tag=_cached_default_model_tag(),
            resident_model_tags=_recent_resident_model_tags(),
        )
        autopilot_plan = autopilot.to_dict()
        emergency_rejected = False
        if autopilot.lane != "tiny":
            eligible = [
                manifest
                for manifest in ranked_manifests
                if _emergency_model_can_answer(model_name=manifest.model_name, lane=autopilot.lane)
            ]
            emergency_rejected = bool(ranked_manifests) and not eligible
            ranked_manifests = eligible
            eligible_provider_ids = {manifest.provider_id for manifest in eligible}
            capability_truth = tuple(
                item for item in capability_truth if item.provider_id in eligible_provider_ids
            )
            capability_by_provider = {item.provider_id: item for item in capability_truth}
        if autopilot.selected_provider_id and _can_prioritize_autopilot_selection(
            ranked_manifests,
            autopilot.selected_provider_id,
            allow_paid_fallback=resolved_allow_paid,
        ):
            ranked_manifests = _prioritize_autopilot_selection(ranked_manifests, autopilot.selected_provider_id)
        attempted: list[str] = []
        # Every non-empty entry mirrors `attempted` 1:1. Lets the terminal "all failed" state say
        # REQUIRED_TOOLS_NOT_OFFERED instead of the generic all_ranked_providers_failed when that is
        # what actually happened to every candidate -- an empty catalog or a stale/incorrect
        # manifest is a distinct, fixable defect from "the provider was unreachable".
        attempted_error_reasons: list[str] = []
        # The TYPED failure kind of each attempt, mirrored 1:1 with `attempted`
        # (`core.turn_routing.classify_provider_error`, the same vocabulary the routing-failure
        # record uses). The wording surface reads PER-ATTEMPT identity from this instead of
        # re-deriving one story from a joined error string: an early timeout followed by a local
        # credential refusal is two facts about two attempts, and "nothing was sent" is only
        # ever a claim about the attempts that were actually refused before the wire.
        attempt_kinds: list[str] = []
        # One entry per provider call this turn actually made, with what it cost in wall clock.
        # The v0.5.0 smoke run reported severe latency on three different turns (QA-050-009/011/022)
        # and the trace could not say where it went: every routing event named a provider and a
        # reason, none of them named a duration, and `grep -n "elapsed\|duration_ms\|latency" ` over
        # this module found nothing. A chain that is slow because one candidate hung is a different
        # defect from one that is slow because three candidates each answered slowly, and until this
        # list exists the two are indistinguishable from the outside.
        attempt_timings: list[dict[str, Any]] = []
        failover_used = False
        # Bound total wall-clock time across the sequential fallback loop below.
        # Each candidate's own per-call timeout_seconds is 180s (see manifest
        # runtime_config in runtime_provider_defaults.py), and up to swarm_size=4
        # candidates can be tried — with no cap that's up to 720s for a single
        # ordinary chat turn if candidates are slow/hung rather than cleanly
        # erroring. "deep"/"cloud"/"human" lanes are explicit, user-signaled heavy
        # requests where a long wait is expected and acceptable; "tiny"/"daily"
        # lanes are ordinary conversational turns that should fail fast and fall
        # through to an honest response instead of silently accumulating minutes
        # of sequential timeouts behind the scenes.
        #
        # The finite budget is compute-aware: a full agent turn on pure CPU
        # cannot finish inside the 60s GPU default, so a CPU-only run gets the
        # larger provider_fallback_budget_seconds_cpu (default 180s, matching the
        # per-manifest timeout_seconds) so a slow CPU generation can complete
        # instead of being cut off. A run is treated as CPU-only when the Ollama
        # lane is forced to CPU (VOOL_OLLAMA_NUM_GPU == "0") or the machine has
        # no usable GPU accelerator (probed accelerator is cpu/absent). The budget
        # decision lives in resolve_fallback_budget_seconds so it is unit-tested
        # directly. deep/cloud/human keep the unbounded None path unchanged. The
        # gpu_memory_fraction compute-daemon signal is NOT used here: it is 0.0 by
        # default on Windows (daemon disabled), which would wrongly stretch the
        # budget to the CPU value on a healthy GPU box.
        _forced_cpu = str(os.environ.get("VOOL_OLLAMA_NUM_GPU", "")).strip() == "0"
        _fallback_budget_seconds = resolve_fallback_budget_seconds(
            autopilot.lane,
            forced_cpu=_forced_cpu,
            no_usable_gpu=(not _forced_cpu and _machine_has_no_usable_gpu()),
            output_mode=output_mode,
        )
        _fallback_deadline: float | None = None

        def _start_fallback_deadline() -> float | None:
            """Start the budget at the first possible provider invocation, not during planning."""
            nonlocal _fallback_deadline
            if _fallback_deadline is None and _fallback_budget_seconds is not None:
                _fallback_deadline = time.monotonic() + _fallback_budget_seconds
            return _fallback_deadline
        autopilot_block_reason = _autopilot_block_reason(autopilot_plan)
        heavy_lane_manifest = None
        if autopilot_block_reason == "explicit_heavy_lane_unavailable":
            # The autopilot's lane view is the local inference fleet. A heavy request is unservable
            # only when NO ranked manifest is heavy. A pinned heavy remote model IS the heavy lane,
            # and its own size is what raised the flag: measured 2026-09-08, the operator's pin
            # `google/gemma-4-26b-a4b-it:free` sat alone in ranked_candidates while this block
            # refused the turn before any adapter ran, over a "heavy request" only that pin made.
            heavy_lane_manifest = _ranked_heavy_manifest(ranked_manifests)
            if heavy_lane_manifest is not None:
                autopilot_block_reason = ""
        planned_manifest = next(
            (manifest for manifest in ranked_manifests if manifest.provider_id == autopilot.selected_provider_id),
            None,
        )
        if (
            not autopilot_block_reason
            and autopilot.selected_provider_id
            and planned_manifest is None
            # Total parameters, not the MoE-active count: this gate decides whether the pinned
            # model may use the heavy lane, and the autopilot's explicit_heavy flag already read
            # the total from the same name. Re-deriving it with the inference-cost function put
            # "gemma-4-26b-a4b" at 4B against a flag raised for 26B, and the pinned model was
            # refused before any adapter ran (measured 2026-08-19).
            and model_total_parameter_billions(str(autopilot_plan.get("selected_model") or "")) >= 24.0
        ):
            autopilot_block_reason = "explicit_heavy_lane_unavailable"
        selected_manifest = (
            None
            if autopilot_block_reason
            else (planned_manifest or heavy_lane_manifest or (ranked_manifests[0] if ranked_manifests else None))
        )
        _emit_model_routing_event(
            source_context,
            "model_routing_started",
            f"Autopilot routed {task_kind} through the {autopilot.lane} lane.",
            task_kind=task_kind,
            output_mode=output_mode,
            provider_role=provider_role,
            lane=autopilot.lane,
            lane_type=autopilot.lane,
            phase="routing",
            ranked_candidates=[entry.provider_id for entry in ranked_manifests],
            autopilot_plan=autopilot_plan,
        )
        if selected_manifest is not None:
            _emit_model_routing_event(
                source_context,
                "model_lane_selected",
                f"Selected {selected_manifest.provider_id} for the {autopilot.lane} lane.",
                **_lane_proof_payload(
                    source_context=source_context,
                    task=task,
                    classification=classification,
                    task_kind=task_kind,
                    output_mode=output_mode,
                    provider_role=provider_role,
                    autopilot_plan=autopilot_plan,
                    manifest=selected_manifest,
                    capability=capability_by_provider.get(selected_manifest.provider_id),
                    phase="selected",
                    attempted=attempted,
                    failover_used=failover_used,
                ),
            )

        if autopilot_block_reason:
            # The pin's reservation (if any) was built before this gate and the adapter never runs,
            # so hand back a still-standing reservation instead of stranding it against the caps.
            _release_undispatched_pin_reservation(authorized_paid_call, source_context=source_context)
            proof = _lane_proof_payload(
                source_context=source_context,
                task=task,
                classification=classification,
                task_kind=task_kind,
                output_mode=output_mode,
                provider_role=provider_role,
                autopilot_plan=autopilot_plan,
                manifest=None,
                capability=None,
                phase="blocked",
                attempted=attempted,
                failover_used=failover_used,
                fallback_reason=autopilot_block_reason,
            )
            _emit_model_routing_event(
                source_context,
                "model_routing_failed",
                "Autopilot blocked model execution before adapter invocation.",
                task_kind=task_kind,
                output_mode=output_mode,
                provider_role=provider_role,
                lane=autopilot.lane,
                lane_type=autopilot.lane,
                phase="blocked",
                ranked_candidates=[entry.provider_id for entry in ranked_manifests],
                rejection_reason=autopilot_block_reason,
            )
            _emit_model_routing_event(
                source_context,
                "model_lane_proof",
                "Autopilot blocked this lane before adapter invocation.",
                **proof,
            )
            return ModelExecutionDecision(
                source="autopilot_blocked",
                task_hash=task_hash,
                used_model=False,
                failover_used=failover_used,
                details={
                    "attempted": attempted,
                    "reason": autopilot_block_reason,
                    "provider_role": provider_role,
                    "requested_model": str((source_context or {}).get("requested_model") or "").strip(),
                    "ranked_candidates": [entry.provider_id for entry in ranked_manifests],
                    "autopilot_plan": autopilot_plan,
                    "lane_proof": proof,
                },
            )

        request = self._build_round_request(
            task=task,
            classification=classification,
            interpretation=interpretation,
            context_result=context_result,
            persona=persona,
            output_mode=output_mode,
            task_kind=task_kind,
            surface=surface,
            source_context=source_context,
        )
        if output_mode == "tool_intent":
            from core.capability_graph import family_hint_from_task_class
            from core.cloud_tool_call_contract import build_cloud_tool_definitions
            from core.execution_requirements import requirements_for
            from core.tool_offer_assembly import assemble_tool_offer
            from core.tool_offer_state import followup_inherited_families, note_family_offer

            family_hint = family_hint_from_task_class(
                str(classification.get("task_class", "") or "")
            )
            user_text_for_offer = _interpreted_user_text(interpretation, task)
            tool_requirements = requirements_for(
                user_text_for_offer,
                task_class=str(classification.get("task_class") or "unknown"),
                source_context=source_context,
            )
            # ONE offer seam for the native definitions and the prompt catalog
            # (core.tool_offer_assembly): same bounded adaptive specs, and the
            # matched SKILL.md guidance rides the system prompt the normalizer built.
            offer = assemble_tool_offer(
                family_hint=family_hint,
                # Native skill selection is typed on the SAME classification the
                # family hint came from (core.native_skill_library).
                task_class=str(classification.get("task_class") or ""),
                # A contextual follow-up ("so?") classifies as plain conversation
                # with no family of its own; it inherits the previous turn's
                # families instead of dropping to nothing (census: follow-ups
                # carried zero tools). Only follow-up-shaped text inherits — a
                # new demand resolves its own families.
                family_hints=followup_inherited_families(user_text_for_offer, source_context),
                toolset_hints=tuple(
                    str(h).strip() for h in tool_requirements.allowed_toolsets
                    if isinstance(h, str)
                ),
                # The offer is adaptive per round: the user's own words carry
                # explicit intents and every required family (mixed demands),
                # and a same-turn `capability.expand_family` call reseats the
                # family it named for THIS round. Still bounded at 8 seats.
                user_text=user_text_for_offer,
                source_context=source_context,
            )
            offer_specs = offer.specs
            if offer.skill_guidance.skills:
                # Audit trail: exactly which skills entered this turn's context, with their
                # origin and version, so an executed turn can be answered for its guidance.
                from core.tool_offer_assembly import (
                    skill_provenance_rows as _skill_provenance_rows,
                )

                emit_runtime_event(
                    source_context if isinstance(source_context, dict) else {},
                    event_type="tool_offer_skills",
                    message="skill guidance joined the turn's context",
                    details={
                        "skills": _skill_provenance_rows(offer.skill_guidance.skills)
                    },
                )
            request.tools = build_cloud_tool_definitions(offer_specs)
            request.tool_choice = "required"
            # "required", not "auto" below: only a tool_intent turn's contract is non-negotiable --
            # the plain_text/auto catalog is a capability offer, not a requirement, and must not
            # trip the final pre-invocation gate just because a conversational turn didn't call one.
            request.tools_required = True
            # A later contextual follow-up in the same session inherits this
            # turn's families instead of receiving zero tools. The recorded set is what the
            # offer ACTUALLY seated (demand-signal and control-plane-session families
            # included), so a repository or coding workflow's follow-up keeps its tools
            # instead of dropping to a toolless scaffold the moment the task class carries
            # no family hint.
            note_family_offer(
                str(
                    (source_context or {}).get("runtime_session_id")
                    or (source_context or {}).get("session_id")
                    or ""
                ),
                tuple(offer.families),
            )
        elif flag_enabled("plain_text_tool_catalog"):
            # A capable model gets the catalog on an ORDINARY turn too, with tool_choice "auto" so it
            # may call a tool rather than must. Measured 2026-07-29: a contextual follow-up ("so?",
            # "audit the skills in there") classifies as plain_text, which supplied NO tools, and the
            # cloud model then invented a call shape of its own — {"tool":"workspace","action":
            # "list","path":"."} — which the runtime correctly refused. Refusing an invented shape is
            # right; sending the turn with no tools to invent from is what produced it.
            #
            # This is the capability rule the product is built on: the tools are VOOL's and every
            # model gets them. Selecting a cloud model changes who reasons, not what VOOL can do.
            #
            # "auto" not "required": a plain_text turn is usually conversation, and forcing a call
            # would make every greeting reach for a tool.
            from core.capability_graph import family_hint_from_task_class, model_visible_specs
            from core.cloud_tool_call_contract import build_cloud_tool_definitions

            family_hint = family_hint_from_task_class(
                str(classification.get("task_class", "") or "")
            )
            request.tools = build_cloud_tool_definitions(
                model_visible_specs(
                    family_hint=family_hint,
                )
            )
            request.tool_choice = "auto"
        if bool(autopilot_plan.get("verifier_required")):
            request.metadata["defer_stream_until_verified"] = True

        if not ranked_manifests:
            if explicit_pin_selected:
                # The user pinned a concrete model and its lane is empty before any adapter ran
                # (its paid reservation was refused, or a locality/plan/emergency gate excluded it).
                # The free-cloud boost must NOT answer in its place. Name the model and the actual
                # gate; carry the reservation's own rejection reason when that is what happened.
                block_reason = (
                    pin_reservation_denial.get("reason", "")
                    if (requested_paid_cloud and authorized_paid_call is None)
                    else ""
                ) or "selected_provider_excluded_before_invocation"
                return self._selected_model_blocked_decision(
                    requested_model_raw,
                    block_reason=block_reason,
                    authorized_paid_call=authorized_paid_call,
                    task=task,
                    classification=classification,
                    task_kind=task_kind,
                    output_mode=output_mode,
                    provider_role=provider_role,
                    autopilot_plan=autopilot_plan,
                    attempted=attempted,
                    failover_used=failover_used,
                    source_context=source_context,
                    task_hash=task_hash,
                )
            cloud_decision = self._try_free_cloud_boost(
                request=request,
                task=task,
                task_hash=task_hash,
                output_mode=output_mode,
                source_context=free_boost_context,
                route_reason="free_cloud_boost_no_local_route",
            )
            if cloud_decision is not None:
                return cloud_decision
            no_provider_reason = "emergency_lane_insufficient" if emergency_rejected else "no_ranked_provider"
            proof = _lane_proof_payload(
                source_context=source_context,
                task=task,
                classification=classification,
                task_kind=task_kind,
                output_mode=output_mode,
                provider_role=provider_role,
                autopilot_plan=autopilot_plan,
                manifest=None,
                capability=None,
                phase="blocked",
                attempted=attempted,
                failover_used=failover_used,
                fallback_reason=no_provider_reason,
            )
            _emit_model_routing_event(
                source_context,
                "model_routing_failed",
                "No local/provider lane is available for this request.",
                task_kind=task_kind,
                output_mode=output_mode,
                provider_role=provider_role,
                lane=autopilot.lane,
                lane_type=autopilot.lane,
                phase="rejected",
                ranked_candidates=[],
                rejection_reason=no_provider_reason,
            )
            _emit_model_routing_event(
                source_context,
                "model_lane_proof",
                "No local/provider lane was available.",
                **proof,
            )
            return ModelExecutionDecision(
                source="no_provider_available",
                task_hash=task_hash,
                used_model=False,
                failover_used=failover_used,
                details={
                    "attempted": attempted,
                    "reason": no_provider_reason,
                    "provider_role": provider_role,
                    "requested_model": str((source_context or {}).get("requested_model") or "").strip(),
                    "ranked_candidates": [],
                    # Self-diagnosing: with the local-first self-heal, an empty ranking now means a
                    # genuine gap (no manifest, all disabled, or a compliance/capability exclusion),
                    # not just a tag mismatch. Surface the registry inventory so the trace names it.
                    "provider_inventory": _provider_inventory_snapshot(self.registry),
                    "autopilot_plan": autopilot_plan,
                    "lane_proof": proof,
                },
            )

        _start_fallback_deadline()
        muxed_manifest, muxed_adapter, muxed_response, muxed_attempted, mux_used = self._maybe_mux_manifests(
            ranked_manifests=ranked_manifests,
            request=request,
            output_mode=output_mode,
            task=task,
            source_context=source_context,
            preferred_provider=preferred_provider,
            preferred_model=preferred_model,
            task_kind=task_kind,
        )
        if mux_used:
            attempted.extend(muxed_attempted)
            failover_used = True
            if muxed_manifest is not None and muxed_adapter is not None and muxed_response is not None:
                verifier_status = self._verify_primary_response(
                    primary_manifest=muxed_manifest,
                    primary_request=request,
                    primary_response=muxed_response,
                    ranked_manifests=ranked_manifests,
                    autopilot_plan=autopilot_plan,
                    task=task,
                    classification=classification,
                    task_kind=task_kind,
                    output_mode=output_mode,
                    source_context=source_context,
                    failed_provider_ids=set(attempted),
                )
                proof = _lane_proof_payload(
                    source_context=source_context,
                    task=task,
                    classification=classification,
                    task_kind=task_kind,
                    output_mode=output_mode,
                    provider_role=provider_role,
                    autopilot_plan=autopilot_plan,
                    manifest=muxed_manifest,
                    capability=capability_by_provider.get(muxed_manifest.provider_id),
                    phase="completed",
                    attempted=attempted,
                    failover_used=failover_used,
                    fallback_reason="think_harder_mux_winner",
                )
                proof["verifier_status"] = verifier_status
                decision = self._decision_from_response(
                    manifest=muxed_manifest,
                    adapter=muxed_adapter,
                    response=muxed_response,
                    task_hash=task_hash,
                    task=task,
                    classification=classification,
                    context_result=context_result,
                    task_kind=task_kind,
                    output_mode=output_mode,
                    provider_role=provider_role,
                    ranked_manifests=ranked_manifests,
                    attempted=attempted,
                    failover_used=failover_used,
                    source="think_harder_mux_winner",
                    autopilot_plan=autopilot_plan,
                    lane_proof=proof,
                    cloud_escalation_authorized=cloud_escalation_authorized,
                    source_context=source_context,
                )
                _apply_verifier_gate(decision, str(proof.get("verifier_status") or ""))
                _emit_model_routing_event(
                    source_context,
                    "model_lane_proof",
                    f"{muxed_manifest.provider_id} won the think-harder mux with runtime proof.",
                    **proof,
                )
                return decision

        _start_fallback_deadline()
        raced_manifest, raced_adapter, raced_response, raced_attempted, race_used = self._maybe_race_manifests(
            ranked_manifests=ranked_manifests,
            request=request,
            output_mode=output_mode,
            allow_paid_fallback=resolved_allow_paid,
            task=task,
            source_context=source_context,
            task_kind=task_kind,
        )
        if race_used:
            attempted.extend(raced_attempted)
            failover_used = True
            if raced_manifest is not None and raced_adapter is not None and raced_response is not None:
                verifier_status = self._verify_primary_response(
                    primary_manifest=raced_manifest,
                    primary_request=request,
                    primary_response=raced_response,
                    ranked_manifests=ranked_manifests,
                    autopilot_plan=autopilot_plan,
                    task=task,
                    classification=classification,
                    task_kind=task_kind,
                    output_mode=output_mode,
                    source_context=source_context,
                    failed_provider_ids=set(attempted),
                )
                proof = _lane_proof_payload(
                    source_context=source_context,
                    task=task,
                    classification=classification,
                    task_kind=task_kind,
                    output_mode=output_mode,
                    provider_role=provider_role,
                    autopilot_plan=autopilot_plan,
                    manifest=raced_manifest,
                    capability=capability_by_provider.get(raced_manifest.provider_id),
                    phase="completed",
                    attempted=attempted,
                    failover_used=failover_used,
                    fallback_reason="provider_race_winner" if raced_manifest.provider_id != autopilot.selected_provider_id else "",
                )
                proof["verifier_status"] = verifier_status
                decision = self._decision_from_response(
                    manifest=raced_manifest,
                    adapter=raced_adapter,
                    response=raced_response,
                    task_hash=task_hash,
                    task=task,
                    classification=classification,
                    context_result=context_result,
                    task_kind=task_kind,
                    output_mode=output_mode,
                    provider_role=provider_role,
                    ranked_manifests=ranked_manifests,
                    attempted=attempted,
                    failover_used=failover_used,
                    source="provider_race_winner",
                    autopilot_plan=autopilot_plan,
                    lane_proof=proof,
                    cloud_escalation_authorized=cloud_escalation_authorized,
                    source_context=source_context,
                )
                _apply_verifier_gate(decision, str(proof.get("verifier_status") or ""))
                _emit_model_routing_event(
                    source_context,
                    "model_lane_proof",
                    f"{raced_manifest.provider_id} completed with runtime proof.",
                    **proof,
                )
                return decision

        skipped_provider_ids = {manifest.provider_id for manifest in ranked_manifests if manifest.provider_id in attempted}
        contract_failed_decision: ModelExecutionDecision | None = None
        # MF-19: a verifier-FLAGGED answer escalates ONCE to the next ranked candidate instead of
        # shipping. Kept beside the contract-failed fallback so the turn still answers (with the
        # draft caveat _apply_verifier_gate already attached) when every candidate is flagged.
        verifier_flagged_decision: ModelExecutionDecision | None = None
        _start_fallback_deadline()
        for _candidate_index, manifest in enumerate(ranked_manifests):
            if manifest.provider_id in skipped_provider_ids:
                continue
            if _fallback_deadline is not None and time.monotonic() >= _fallback_deadline:
                budget_proof = _lane_proof_payload(
                    source_context=source_context,
                    task=task,
                    classification=classification,
                    task_kind=task_kind,
                    output_mode=output_mode,
                    provider_role=provider_role,
                    autopilot_plan=autopilot_plan,
                    manifest=None,
                    capability=None,
                    phase="failed",
                    attempted=attempted,
                    failover_used=failover_used,
                    fallback_reason="provider_fallback_budget_exceeded",
                )
                _emit_model_routing_event(
                    source_context,
                    "model_routing_failed",
                    f"Provider fallback budget ({_fallback_budget_seconds:.0f}s) exceeded after {len(attempted)} attempt(s); stopping instead of trying remaining candidates.",
                    task_kind=task_kind,
                    output_mode=output_mode,
                    provider_role=provider_role,
                    lane=autopilot.lane,
                    lane_type=autopilot.lane,
                    phase="failed",
                    ranked_candidates=[entry.provider_id for entry in ranked_manifests],
                    attempted=attempted,
                    rejection_reason="provider_fallback_budget_exceeded",
                    attempt_timings=list(attempt_timings),
                    **summarize_attempt_timings(attempt_timings),
                )
                _emit_attempt_chain_timing(
                    source_context,
                    attempt_timings=attempt_timings,
                    fallback_budget_seconds=_fallback_budget_seconds,
                    outcome="provider_fallback_budget_exceeded",
                )
                return ModelExecutionDecision(
                    source="provider_fallback_budget_exceeded",
                    task_hash=task_hash,
                    used_model=False,
                    failover_used=failover_used,
                    details={
                        "attempted": attempted,
                        "reason": "provider_fallback_budget_exceeded",
                        "provider_role": provider_role,
                        "requested_model": str((source_context or {}).get("requested_model") or "").strip(),
                        "ranked_candidates": [entry.provider_id for entry in ranked_manifests],
                        "attempt_timings": list(attempt_timings),
                        "attempt_timing_summary": summarize_attempt_timings(attempt_timings),
                        "autopilot_plan": autopilot_plan,
                        "lane_proof": budget_proof,
                    },
                )
            _emit_model_routing_event(
                source_context,
                "model_lane_started",
                f"Using {manifest.provider_id}.",
                task_kind=task_kind,
                output_mode=output_mode,
                provider_role=provider_role,
                lane=autopilot.lane,
                lane_type=autopilot.lane,
                role=provider_role,
                phase="running",
                selected_provider_id=manifest.provider_id,
                provider_id=manifest.provider_id,
                selected_model=manifest.model_name,
                model_id=manifest.model_name,
                cost_class=reported_cost_class(manifest),
                tokens_per_second=getattr(capability_by_provider.get(manifest.provider_id), "tokens_per_second", 0.0),
                queue_depth=getattr(capability_by_provider.get(manifest.provider_id), "queue_depth", 0),
                attempted=attempted,
            )
            invoke_manifest = manifest
            if _fallback_deadline is not None:
                # Cap this individual call's own timeout to whatever's left of the
                # overall budget. Without this, a single slow/hanging call can burn
                # its full per-manifest timeout_seconds (180s) on its own, and the
                # deadline check above — which only runs BETWEEN attempts — never
                # gets a chance to stop it (this is exactly how the 155-180s
                # hallucinated-answer bug happened: one in-flight call consumed the
                # entire duration, so the between-attempts budget check never fired).
                #
                # SWITCHBOARD-locked policy (D1): the cap above alone still lets the FIRST
                # candidate tried claim (almost) the entire remaining budget as its own ceiling --
                # confirmed structurally in tests/test_fallback_budget_characterization.py, a hang
                # that uses the whole of it leaves nothing for a genuinely reachable successor,
                # even though the between-attempts check above is working exactly as designed.
                # `reserve` withholds enough of THIS candidate's own window to guarantee each
                # still-reachable successor keeps at least the floor -- see
                # _fallback_reserve_seconds for the exact rule (zero when there is no successor,
                # or when this candidate's own failure would end the loop outright regardless of
                # what remains ranked behind it). The floor is applied AFTER subtracting the
                # reserve, not before: applying it first would let the reserve silently claw back
                # into the floor itself instead of coming only out of genuine slack.
                raw_remaining_seconds = _fallback_deadline - time.monotonic()
                floored_remaining_seconds = max(_FALLBACK_ATTEMPT_FLOOR_SECONDS, raw_remaining_seconds)
                existing_timeout = float((manifest.runtime_config or {}).get("timeout_seconds") or floored_remaining_seconds)
                reserve_seconds = _fallback_reserve_seconds(
                    ranked_manifests=ranked_manifests,
                    skipped_provider_ids=skipped_provider_ids,
                    current_index=_candidate_index,
                    autopilot_plan=autopilot_plan,
                    manifest=manifest,
                )
                capped_timeout = min(
                    existing_timeout,
                    max(_FALLBACK_ATTEMPT_FLOOR_SECONDS, raw_remaining_seconds - reserve_seconds),
                )
                # Written whenever it binds -- including when the manifest carries NO timeout of its
                # own. Measured 2026-09-07 (fallback budget characterization, ten reds): a manifest
                # without `timeout_seconds` kept none, the adapter fell back to its own default (the
                # 180s the comment above describes), and one hanging candidate could outlive the whole
                # budget -- the exact hang the cap exists to bound.
                if capped_timeout < existing_timeout or "timeout_seconds" not in (manifest.runtime_config or {}):
                    invoke_manifest = manifest.model_copy(
                        update={"runtime_config": {**(manifest.runtime_config or {}), "timeout_seconds": capped_timeout}}
                    )
            _attempt_started = time.monotonic()
            adapter, response, error = self._invoke_manifest(
                manifest=invoke_manifest,
                request=request,
                output_mode=output_mode,
                task=task,
                source_context=source_context,
                # Wrapped by the model_lane_started/failed/completed receipts this loop emits
                # around each attempt -- the lane pair is the displayed row; see _invoke_manifest.
                lane_receipted=True,
                # The kind THIS ranking request selected under — the observation's eligibility key.
                task_kind=task_kind,
            )
            _record_attempt_timing(
                attempt_timings,
                manifest=manifest,
                seconds=time.monotonic() - _attempt_started,
                outcome="failed" if (error or adapter is None or response is None) else "answered",
                error=str(error or "") if (error or adapter is None or response is None) else "",
                timeout_seconds=float(
                    (invoke_manifest.runtime_config or {}).get("timeout_seconds") or 0.0
                ),
            )
            if error or adapter is None or response is None:
                attempted.append(manifest.provider_id)
                attempted_error_reasons.append(str(error or "no_response"))
                _attempt_kind = _turn_routing.classify_provider_error(error or "no_response").value
                attempt_kinds.append(_attempt_kind)
                failover_used = True
                # A9 P0 — the typed failed execution this turn will be asked about. Recorded
                # at the loop that owns candidate order (not inside _invoke_manifest, which
                # several lanes share per call), once per failed candidate, with the failure
                # KIND kept distinct: timeout ≠ unavailable ≠ refused ≠ cancelled ≠ partial.
                with contextlib.suppress(Exception):
                    _turn_routing.record_routing_failure(
                        turn_id=routing_plan.turn_id,
                        session_id=routing_plan.session_id,
                        provider_id=manifest.provider_id,
                        model_id=manifest.model_name,
                        kind=_attempt_kind,
                        stage="provider_call",
                        detail=str(error or "no_response"),
                        plan_id=routing_plan.plan_id,
                        plan_digest=routing_plan.digest(),
                        user_text=str(routing_user_text or ""),
                    )
                failed_proof = _lane_proof_payload(
                    source_context=source_context,
                    task=task,
                    classification=classification,
                    task_kind=task_kind,
                    output_mode=output_mode,
                    provider_role=provider_role,
                    autopilot_plan=autopilot_plan,
                    manifest=manifest,
                    capability=capability_by_provider.get(manifest.provider_id),
                    phase="failed",
                    attempted=attempted,
                    failover_used=failover_used,
                    fallback_reason=str(error or "no_response"),
                )
                _emit_model_routing_event(
                    source_context,
                    "model_lane_failed",
                    (
                        f"{manifest.provider_id} failed; no smaller fallback is allowed for this explicit heavy lane."
                        if _planned_heavy_manifest_failed(autopilot_plan, manifest)
                        else f"{manifest.provider_id} failed; trying fallback if available."
                    ),
                    task_kind=task_kind,
                    output_mode=output_mode,
                    provider_role=provider_role,
                    lane=autopilot.lane,
                    lane_type=autopilot.lane,
                    role=provider_role,
                    phase="failed",
                    selected_provider_id=manifest.provider_id,
                    provider_id=manifest.provider_id,
                    selected_model=manifest.model_name,
                    model_id=manifest.model_name,
                    tokens_per_second=getattr(capability_by_provider.get(manifest.provider_id), "tokens_per_second", 0.0),
                    queue_depth=getattr(capability_by_provider.get(manifest.provider_id), "queue_depth", 0),
                    attempted=attempted,
                    error=str(error or "no_response"),
                    fallback_reason=str(error or "no_response"),
                    lane_proof=failed_proof,
                    # What this candidate actually cost in wall clock, next to the reason it
                    # failed. Without it a chain like the v0.5.0 smoke run's (QA-050-019/022 --
                    # a 14B local model, then a pinned cloud model, then an 8B local model) is
                    # only readable as "slow", with no way to tell a hung candidate from three
                    # honest ones or to see which timeout was the one that bound.
                    attempt_seconds=attempt_timings[-1]["seconds"] if attempt_timings else 0.0,
                    attempt_timings=list(attempt_timings),
                    fallback_budget_seconds=_fallback_budget_seconds,
                )
                if _planned_heavy_manifest_failed(autopilot_plan, manifest):
                    final_proof = dict(failed_proof)
                    final_proof["phase"] = "failed"
                    final_proof["fallback_reason"] = f"explicit_heavy_lane_failed:{error or 'no_response'!s}"
                    final_proof["verifier_status"] = "not_run_primary_failed"
                    _emit_model_routing_event(
                        source_context,
                        "model_lane_proof",
                        "Explicit heavy lane failed; no smaller fallback was used.",
                        **final_proof,
                    )
                    return ModelExecutionDecision(
                        source="explicit_heavy_lane_failed",
                        task_hash=task_hash,
                        used_model=False,
                        failover_used=failover_used,
                        details={
                            "attempted": attempted,
                            "reason": final_proof["fallback_reason"],
                            "provider_role": provider_role,
                            "requested_model": str((source_context or {}).get("requested_model") or "").strip(),
                            "ranked_candidates": [entry.provider_id for entry in ranked_manifests],
                            "autopilot_plan": autopilot_plan,
                            "lane_proof": final_proof,
                        },
                    )
                continue
            proof = _lane_proof_payload(
                source_context=source_context,
                task=task,
                classification=classification,
                task_kind=task_kind,
                output_mode=output_mode,
                provider_role=provider_role,
                autopilot_plan=autopilot_plan,
                manifest=manifest,
                capability=capability_by_provider.get(manifest.provider_id),
                phase="completed",
                attempted=attempted,
                failover_used=failover_used,
                fallback_reason="fallback_after_failed_lane" if failover_used else "",
            )
            proof["verifier_status"] = self._verify_primary_response(
                primary_manifest=manifest,
                primary_request=request,
                primary_response=response,
                ranked_manifests=ranked_manifests,
                autopilot_plan=autopilot_plan,
                task=task,
                classification=classification,
                task_kind=task_kind,
                output_mode=output_mode,
                source_context=source_context,
                failed_provider_ids=set(attempted),
            )
            _emit_model_routing_event(
                source_context,
                "model_lane_completed",
                f"{manifest.provider_id} completed.",
                task_kind=task_kind,
                output_mode=output_mode,
                provider_role=provider_role,
                lane=autopilot.lane,
                lane_type=autopilot.lane,
                role=provider_role,
                phase="completed",
                selected_provider_id=manifest.provider_id,
                provider_id=manifest.provider_id,
                selected_model=manifest.model_name,
                model_id=manifest.model_name,
                cost_class=reported_cost_class(manifest),
                tokens_per_second=getattr(capability_by_provider.get(manifest.provider_id), "tokens_per_second", 0.0),
                queue_depth=getattr(capability_by_provider.get(manifest.provider_id), "queue_depth", 0),
                attempted=attempted,
                failover_used=failover_used,
                lane_proof=proof,
                # This row is the one the chat page displays for the call (the call's own lane-receipted
                # completion row is skipped there), so the call's bounded provider receipt rides it too:
                # the same response object, never a pairing guess made by the page.
                provider_receipt=_provider_receipt(getattr(response, "provider_metadata", None)),
            )
            decision = self._decision_from_response(
                manifest=manifest,
                adapter=adapter,
                response=response,
                task_hash=task_hash,
                task=task,
                classification=classification,
                context_result=context_result,
                task_kind=task_kind,
                output_mode=output_mode,
                provider_role=provider_role,
                ranked_manifests=ranked_manifests,
                attempted=attempted,
                failover_used=failover_used,
                source="provider_execution",
                autopilot_plan=autopilot_plan,
                lane_proof=proof,
                cloud_escalation_authorized=cloud_escalation_authorized,
                source_context=source_context,
                attempt_timings=attempt_timings,
            )
            _emit_attempt_chain_timing(
                source_context,
                attempt_timings=attempt_timings,
                fallback_budget_seconds=_fallback_budget_seconds,
                outcome="answered",
            )
            _apply_verifier_gate(decision, str(proof.get("verifier_status") or ""))
            # MF-19: the verifier flagged this answer as WRONG, and until now the runtime shipped
            # it anyway — measured live (color-cascade turn, receipts 11:52:48):
            # MODEL_LANE_VERIFIER_FLAGGED and "Completed task with final response: Yellow" in the
            # same second, correct answer Purple, no signal reaching the user because the
            # exact-shape branch suppresses even the caveat. A flag now buys ONE escalation to the
            # next ranked candidate (which gets its own verifier pass); if every candidate is
            # flagged, the FIRST flagged answer ships with its caveat/trust-cap — degrade honestly,
            # never silently.
            if (
                str(proof.get("verifier_status") or "") == "flagged"
                and decision.validation_state != "contract_failed"
                and verifier_flagged_decision is None
            ):
                verifier_flagged_decision = decision
                has_next = any(
                    m.provider_id not in attempted and m.provider_id != manifest.provider_id
                    for m in ranked_manifests
                )
                within_budget = _fallback_deadline is None or time.monotonic() < _fallback_deadline
                if has_next and within_budget:
                    if manifest.provider_id not in attempted:
                        attempted.append(manifest.provider_id)
                    failover_used = True
                    _emit_model_routing_event(
                        source_context,
                        "model_lane_verifier_escalation",
                        f"Verifier flagged {manifest.provider_id}'s answer; escalating to the next candidate.",
                        task_kind=task_kind,
                        output_mode=output_mode,
                        provider_role=provider_role,
                        lane=autopilot.lane,
                        phase="verifier_escalation",
                        selected_provider_id=manifest.provider_id,
                        provider_id=manifest.provider_id,
                        attempted=attempted,
                    )
                    continue
            # A lane that returned nothing usable is a FAILED lane, not a successful turn — an empty
            # completion, a structurally empty one (`[]`), or a reasoning monologue with no answer
            # in it. `_soft_failure_details` holds the whole classification and the measurements
            # behind each shape; all three take the same escalation path below.
            _answer = str(getattr(decision, "output_text", "") or "").strip()
            _soft_failure = (
                None
                if decision.validation_state == "contract_failed"
                else _soft_failure_details(_answer)
            )
            if _soft_failure is not None:
                decision.validation_state = "contract_failed"
                decision.details = {
                    **dict(getattr(decision, "details", None) or {}),
                    **_soft_failure,
                }
            if decision.validation_state == "contract_failed":
                # Schema/contract failure is a SOFT failure: escalate to the next
                # ranked candidate rather than delivering malformed output. Keep the
                # first contract-failed decision so we still answer (annotated) if
                # every candidate fails validation.
                if contract_failed_decision is None:
                    contract_failed_decision = decision
                has_next = any(
                    m.provider_id not in attempted and m.provider_id != manifest.provider_id
                    for m in ranked_manifests
                )
                within_budget = _fallback_deadline is None or time.monotonic() < _fallback_deadline
                if has_next and within_budget:
                    if manifest.provider_id not in attempted:
                        attempted.append(manifest.provider_id)
                    failover_used = True
                    _emit_model_routing_event(
                        source_context,
                        "model_lane_contract_failed",
                        f"{manifest.provider_id} output failed contract validation; escalating to the next candidate.",
                        task_kind=task_kind,
                        output_mode=output_mode,
                        provider_role=provider_role,
                        lane=autopilot.lane,
                        phase="contract_failed",
                        selected_provider_id=manifest.provider_id,
                        provider_id=manifest.provider_id,
                        model_id=manifest.model_name,
                        attempted=attempted,
                        # WHICH contract failed. The decision already holds it -- `contract_error`
                        # from `_validate_contract` for a hard rejection, and the `reason` that
                        # `_soft_failure_details` merged in for the three soft shapes (no content,
                        # structurally empty, reasoning monologue) -- and none of it reached the
                        # event, so the row read "Model output rejected" over a payload that knew
                        # exactly which of five different defects had just occurred.
                        **_contract_failure_details(decision),
                    )
                    continue
            _emit_model_routing_event(
                source_context,
                "model_lane_proof",
                f"{manifest.provider_id} completed with runtime proof.",
                **proof,
            )
            return decision

        # The free-cloud boost picks a model of its OWN, so it may run only for Auto/sticky routing.
        # An explicit pin that got this far (its lane ran and failed) must not be answered by a
        # different free model -- skip the boost and fall through to the pin's own outputs below,
        # then to the honest selected-model-blocked terminal.
        if not explicit_pin_selected:
            cloud_decision = self._try_free_cloud_boost(
                request=request,
                task=task,
                task_hash=task_hash,
                output_mode=output_mode,
                source_context=free_boost_context,
            )
            if cloud_decision is not None:
                return cloud_decision

        # The escalated candidate never produced a better answer: ship the first verifier-flagged
        # decision, which carries needs_review + the draft caveat — a well-formed flagged answer
        # beats a contract-failed one, and both beat silence. For a pin these are the SELECTED
        # model's own output, so they ship exactly as before -- the invariant bans a DIFFERENT
        # model, not the pinned model's own annotated answer.
        if verifier_flagged_decision is not None:
            return verifier_flagged_decision

        # Every ranked candidate that produced output failed contract validation:
        # deliver the first contract-failed answer (honestly annotated) rather than
        # nothing, so the turn still responds when cloud boost is off or unavailable.
        if contract_failed_decision is not None:
            return contract_failed_decision

        if explicit_pin_selected:
            # The pinned model was tried and produced no usable output; the free-cloud boost must
            # not answer in its place. Carry the actual attempt failure as the visible cause.
            return self._selected_model_blocked_decision(
                requested_model_raw,
                block_reason=(attempted_error_reasons[0] if attempted_error_reasons else "selected_model_call_failed"),
                authorized_paid_call=authorized_paid_call,
                task=task,
                classification=classification,
                task_kind=task_kind,
                output_mode=output_mode,
                provider_role=provider_role,
                autopilot_plan=autopilot_plan,
                attempted=attempted,
                failover_used=failover_used,
                source_context=source_context,
                task_hash=task_hash,
                dispatched=True,
            )

        # Every candidate that was actually tried failed for the SAME reason: the tool contract
        # could not be carried (an empty schema builder, a stale manifest claiming support it does
        # not have, or an adapter that silently drops tools). That is a distinct, fixable defect
        # from "the provider was unreachable" and must say so rather than collapse into the
        # generic all_ranked_providers_failed label -- see core/execution_requirements.py.
        all_failures_are_tools_gate = bool(attempted_error_reasons) and all(
            reason.startswith("required_tools_not_offered:") for reason in attempted_error_reasons
        )
        terminal_source = "required_tools_not_offered" if all_failures_are_tools_gate else "no_provider_available"
        terminal_reason = "required_tools_not_offered" if all_failures_are_tools_gate else "all_ranked_providers_failed"
        proof = _lane_proof_payload(
            source_context=source_context,
            task=task,
            classification=classification,
            task_kind=task_kind,
            output_mode=output_mode,
            provider_role=provider_role,
            autopilot_plan=autopilot_plan,
            manifest=None,
            capability=None,
            phase="failed",
            attempted=attempted,
            failover_used=failover_used,
            fallback_reason=terminal_reason,
        )
        _emit_model_routing_event(
            source_context,
            "model_routing_failed",
            "Every ranked candidate could not carry the required tool contract."
            if all_failures_are_tools_gate
            else "All ranked provider lanes failed.",
            task_kind=task_kind,
            output_mode=output_mode,
            provider_role=provider_role,
            lane=autopilot.lane,
            lane_type=autopilot.lane,
            phase="failed",
            ranked_candidates=[entry.provider_id for entry in ranked_manifests],
            attempted=attempted,
            rejection_reason=terminal_reason,
            attempt_timings=list(attempt_timings),
            **summarize_attempt_timings(attempt_timings),
        )
        _emit_attempt_chain_timing(
            source_context,
            attempt_timings=attempt_timings,
            fallback_budget_seconds=_fallback_budget_seconds,
            outcome=terminal_reason,
        )
        _emit_model_routing_event(
            source_context,
            "model_lane_proof",
            "All ranked provider lanes failed.",
            **proof,
        )
        # The turn's authorship record must carry what ACTUALLY happened: these candidates
        # were called and failed. Without it the publication refusal describes the runtime's
        # options ("no certified model was available and permitted") over the attempt truth --
        # measured live 2026-09-08 with an expired cloud key: the free-cloud authors were
        # called, failed, and the served refusal claimed none were tried.
        with contextlib.suppress(Exception):
            from core.final_answer_authorship import record_authorship_attempts

            record_authorship_attempts(source_context, attempted)
        return ModelExecutionDecision(
            source=terminal_source,
            task_hash=task_hash,
            used_model=False,
            failover_used=failover_used,
            details={
                "attempted": attempted,
                "attempted_error_reasons": attempted_error_reasons,
                "attempt_kinds": attempt_kinds,
                # Distinct from "no_ranked_provider": here providers DID rank but every call failed
                # (e.g. Ollama unreachable or the model still loading). The reason + inventory let the
                # trace tell "nothing registered" apart from "the model failed to respond".
                "reason": terminal_reason,
                "provider_role": provider_role,
                "requested_model": str((source_context or {}).get("requested_model") or "").strip(),
                "ranked_candidates": [entry.provider_id for entry in ranked_manifests],
                "attempt_timings": list(attempt_timings),
                "attempt_timing_summary": summarize_attempt_timings(attempt_timings),
                "provider_inventory": _provider_inventory_snapshot(self.registry),
                "autopilot_plan": autopilot_plan,
                "lane_proof": proof,
            },
        )

    def _local_model_disabled_decision(
        self,
        requested_model: str,
        *,
        task: Any,
        classification: dict[str, Any],
        task_kind: str,
        output_mode: str,
        provider_role: ProviderRole,
        source_context: dict[str, Any] | None,
        task_hash: str,
    ) -> ModelExecutionDecision:
        """Terminal typed refusal for an explicit LOCAL pick under a disabled LocalModelPolicy.

        Mirrors `_model_unavailable_decision` (called before ranking, no pool to report) but names
        the true cause: the policy disabled local models, so the pick was filtered from the
        registry listing — it is not "unknown", and re-asking or re-installing cannot change the
        answer while the policy stands.
        """
        policy = resolve_local_model_policy()

        proof = _lane_proof_payload(
            source_context=source_context,
            task=task,
            classification=classification,
            task_kind=task_kind,
            output_mode=output_mode,
            provider_role=provider_role,
            autopilot_plan={},
            manifest=None,
            capability=None,
            phase="blocked",
            attempted=[],
            failover_used=False,
            fallback_reason=LOCAL_MODELS_DISABLED_REASON,
        )
        _emit_model_routing_event(
            source_context,
            "model_routing_failed",
            f"Requested model '{requested_model}' is local and local models are disabled on this runtime.",
            task_kind=task_kind,
            output_mode=output_mode,
            provider_role=provider_role,
            lane="model_unavailable",
            lane_type="model_unavailable",
            phase="rejected",
            ranked_candidates=[],
            rejection_reason=LOCAL_MODELS_DISABLED_REASON,
            requested_model=requested_model,
        )
        _emit_model_routing_event(
            source_context,
            "model_lane_proof",
            "Requested local model refused: the LocalModelPolicy is disabled; no substitution was attempted.",
            **proof,
        )
        return ModelExecutionDecision(
            source="model_unavailable",
            task_hash=task_hash,
            used_model=False,
            failover_used=False,
            details={
                "reason": LOCAL_MODELS_DISABLED_REASON,
                "requested_model": requested_model,
                "provider_role": provider_role,
                "policy": policy.to_dict(),
                "ranked_candidates": [],
                "provider_inventory": _provider_inventory_snapshot(self.registry),
                "lane_proof": proof,
            },
        )

    def _model_unavailable_decision(
        self,
        requested_model: str,
        *,
        task: Any,
        classification: dict[str, Any],
        task_kind: str,
        output_mode: str,
        provider_role: ProviderRole,
        source_context: dict[str, Any] | None,
        task_hash: str,
    ) -> ModelExecutionDecision:
        """Terminal MODEL_UNAVAILABLE state for an explicit pick that resolved to no live manifest.

        Called before ranking/autopilot run, so there is no ranked pool or autopilot plan to report
        -- `_lane_proof_payload` accepts that (the existing `no_provider_available` path already
        calls it with `manifest=None`); an empty `autopilot_plan` degrades every `.get(...)` in it
        to its documented default rather than raising.
        """

        proof = _lane_proof_payload(
            source_context=source_context,
            task=task,
            classification=classification,
            task_kind=task_kind,
            output_mode=output_mode,
            provider_role=provider_role,
            autopilot_plan={},
            manifest=None,
            capability=None,
            phase="blocked",
            attempted=[],
            failover_used=False,
            fallback_reason="requested_model_unresolvable",
        )
        _emit_model_routing_event(
            source_context,
            "model_routing_failed",
            f"Requested model '{requested_model}' is not available on this runtime.",
            task_kind=task_kind,
            output_mode=output_mode,
            provider_role=provider_role,
            lane="model_unavailable",
            lane_type="model_unavailable",
            phase="rejected",
            ranked_candidates=[],
            rejection_reason="requested_model_unresolvable",
            requested_model=requested_model,
        )
        _emit_model_routing_event(
            source_context,
            "model_lane_proof",
            "Requested model could not be resolved; no substitution was attempted.",
            **proof,
        )
        return ModelExecutionDecision(
            source="model_unavailable",
            task_hash=task_hash,
            used_model=False,
            failover_used=False,
            details={
                "reason": "requested_model_unresolvable",
                "requested_model": requested_model,
                "provider_role": provider_role,
                "ranked_candidates": [],
                "provider_inventory": _provider_inventory_snapshot(self.registry),
                "lane_proof": proof,
            },
        )

    def _selected_model_blocked_decision(
        self,
        requested_model: str,
        *,
        block_reason: str,
        authorized_paid_call: Any,
        task: Any,
        classification: dict[str, Any],
        task_kind: str,
        output_mode: str,
        provider_role: ProviderRole,
        autopilot_plan: dict[str, Any],
        attempted: list[str],
        failover_used: bool,
        source_context: dict[str, Any] | None,
        task_hash: str,
        dispatched: bool = False,
    ) -> ModelExecutionDecision:
        """Terminal for an EXPLICIT PIN that could not run: the selected model does not get answered
        for by a different model. The free-cloud boost is deliberately NOT consulted (that is the
        whole point -- no silent substitution). The ACTUAL blocking gate's reason (carried from the
        authority that rejected it) rides the decision so the user-facing surface
        (core/agent_runtime/memory_runtime.chat_surface_honest_degraded_response) can name both the
        model and the cause without exposing secrets.

        ``dispatched`` says whether the pin's own adapter was invoked. When it was NOT (the pin was
        excluded before invocation), a granted reservation is handed back here; when it WAS, the
        invoke loop already owns the reservation lifecycle and recorded any unpriced attempt, so this
        terminal must not touch it -- releasing an already-terminalized reservation would erase that
        attempt accounting.
        """
        if not dispatched:
            _release_undispatched_pin_reservation(authorized_paid_call, source_context=source_context)
        reason = str(block_reason or "").strip() or "selected_model_unavailable"
        proof = _lane_proof_payload(
            source_context=source_context,
            task=task,
            classification=classification,
            task_kind=task_kind,
            output_mode=output_mode,
            provider_role=provider_role,
            autopilot_plan=autopilot_plan or {},
            manifest=None,
            capability=None,
            phase="blocked",
            attempted=attempted,
            failover_used=failover_used,
            fallback_reason=reason,
        )
        _emit_model_routing_event(
            source_context,
            "model_routing_failed",
            f"Selected model '{requested_model}' could not run ({reason}); no other model was substituted.",
            task_kind=task_kind,
            output_mode=output_mode,
            provider_role=provider_role,
            lane="selected_model_blocked",
            lane_type="selected_model_blocked",
            phase="rejected",
            ranked_candidates=[],
            rejection_reason=reason,
            requested_model=requested_model,
        )
        _emit_model_routing_event(
            source_context,
            "model_lane_proof",
            "Selected model could not run; no substitution was attempted.",
            **proof,
        )
        pin_route_evidence = {}
        if isinstance(source_context, dict):
            for key, entry in dict(source_context.get("_pin_failure_route_evidence") or {}).items():
                _, _, model_id = str(key).partition("|")
                if str(requested_model).endswith(f":{model_id}") or str(requested_model) == str(model_id):
                    pin_route_evidence = dict(entry)
                    break
        return ModelExecutionDecision(
            source="selected_model_blocked",
            task_hash=task_hash,
            used_model=False,
            failover_used=failover_used,
            details={
                "attempted": attempted,
                "reason": reason,
                "block_reason": reason,
                "block_route_evidence": pin_route_evidence,
                "model_was_attempted": bool(dispatched),
                "requested_model": requested_model,
                "provider_role": provider_role,
                "ranked_candidates": [],
                "provider_inventory": _provider_inventory_snapshot(self.registry),
                "autopilot_plan": autopilot_plan or {},
                "lane_proof": proof,
            },
        )

    def _requested_model_preferences(self, source_context: dict[str, Any] | None) -> tuple[str | None, str | None]:
        requested_model = str((source_context or {}).get("requested_model") or "").strip()
        if not requested_model:
            return None, None
        if is_auto_selection(requested_model):
            return None, None

        manifests = self.registry.list_manifests(enabled_only=True)
        for manifest in manifests:
            if requested_model == manifest.provider_id:
                return manifest.provider_name, manifest.model_name
        for manifest in manifests:
            if requested_model == manifest.model_name:
                return (manifest.provider_name if manifest.metadata.get("chat_selection_only") else None), manifest.model_name

        provider_hint, separator, model_hint = requested_model.partition(":")
        if separator and provider_hint and model_hint:
            for manifest in manifests:
                if provider_hint in {manifest.provider_name, manifest.provider_name.removesuffix("-byok")}:
                    return manifest.provider_name, model_hint
        return None, requested_model

    def _requested_model_manifest(self, source_context: dict[str, Any] | None) -> Any | None:
        requested_model = str((source_context or {}).get("requested_model") or "").strip()
        if not requested_model:
            return None
        if is_auto_selection(requested_model):
            return None

        manifests = self.registry.list_manifests(enabled_only=True)
        for manifest in manifests:
            if requested_model == manifest.provider_id:
                return manifest

        model_matches = [manifest for manifest in manifests if requested_model == manifest.model_name]
        if len(model_matches) == 1:
            return model_matches[0]

        # Vendor-prefix tolerance (MF-22): the catalog names models "nvidia/nemotron-3.5-...:free"
        # while selectors carry the bare "nemotron-3.5-...:free"; a catalog refresh flipped the
        # prefix and the SAME selector string stopped resolving while the model kept serving
        # turns. A unique basename match IS the requested model, so accepting it preserves the
        # never-substitute contract; two or more matches stay ambiguous and fail closed.
        lowered = requested_model.lower()
        suffix_matches = [
            manifest
            for manifest in manifests
            if str(manifest.model_name or "").lower().rpartition("/")[2] == lowered
        ]
        if len(suffix_matches) == 1:
            return suffix_matches[0]

        provider_hint, separator, model_hint = requested_model.partition(":")
        if separator and provider_hint and model_hint:
            for manifest in manifests:
                if provider_hint in {manifest.provider_name, manifest.provider_name.removesuffix("-byok")} and model_hint == manifest.model_name:
                    return manifest
        return None

    def planned_context_window_tokens(
        self,
        *,
        classification: dict[str, Any],
        source_context: dict[str, Any] | None = None,
    ) -> int:
        """Context window (tokens) of the model expected to answer this turn, or 0 when unknown.

        The context loader must size its layer budgets BEFORE `resolve` picks the answering model,
        so this pre-runs the same ranking `resolve` will run and reads the window off the result
        instead of waiting for it. Two deliberate approximations keep the guess safe:

        * An explicit owner pick is trusted outright. The pick is a strict execution contract
          (`_execute_provider_task` filters the ranked pool to that provider), so its manifest
          window IS the turn's window, paid or local.
        * The auto lane takes the MINIMUM positive window across the ranked swarm, because the
          failover loop may land on any candidate in it. Sizing to the smallest keeps the loaded
          context inside whichever candidate answers, and `scale_budget_to_window` never shrinks,
          so a low guess can only mean less growth -- never a worse turn than the fixed budgets.

        Paid manifests are excluded from the auto-lane ranking: an auto fallback never holds a
        paid-call reservation, so a paid candidate can never be the one that answers -- and
        planning must not build a reservation just to read a number off a manifest.

        Returns 0 ("do not scale") when there are no candidates or any candidate hides its window.
        """

        requested_manifest = self._requested_model_manifest(source_context)
        if requested_manifest is not None:
            return max(0, int(provider_capability_truth_for_manifest(requested_manifest).context_window))

        chat_surface = (
            str((source_context or {}).get("surface", "") or "").strip().lower() in _CHAT_TRUTH_SURFACES
        )
        profile = model_execution_profile(
            str(classification.get("task_class", "unknown")),
            chat_surface=chat_surface,
            planner_style_requested=bool(classification.get("planner_style_requested", False)),
        )
        preferred_provider, preferred_model = self._requested_model_preferences(source_context)
        ranked_manifests = rank_provider_candidates(
            self.registry,
            task_kind=str(profile["task_kind"]),
            output_mode=str(profile["output_mode"]),
            role=_provider_role_for_request(profile.get("provider_role")),
            preferred_provider=preferred_provider,
            preferred_model=preferred_model,
            allow_paid_fallback=False,
            swarm_size=4,
            min_trust=0.45,
            enforce_hardware_fit=True,
            local_only=turn_is_local_only(source_context),
        )
        ranked_manifests = [
            manifest
            for manifest in ranked_manifests
            if is_text_generation_ollama_model(manifest.model_name)
        ]
        if not ranked_manifests:
            return 0
        windows = [
            int(provider_capability_truth_for_manifest(manifest).context_window)
            for manifest in ranked_manifests
        ]
        if any(window <= 0 for window in windows):
            return 0
        return min(windows)

    def stamp_planned_context_window(
        self,
        source_context: dict[str, Any] | None,
        *,
        classification: dict[str, Any],
    ) -> dict[str, Any]:
        """A copy of `source_context` carrying `model_context_window` for the context loader.

        A window the turn already carries is kept as-is -- the caller-supplied value is closer to
        the wire than any plan. The plan is an optimization, never a gate: if pre-resolution
        fails for any reason the copy is returned unstamped and the loader keeps the declared
        budgets, exactly as before this seam existed.
        """

        ctx = dict(source_context or {})
        if available_prompt_tokens(ctx) > 0:
            return ctx
        try:
            window = self.planned_context_window_tokens(
                classification=classification,
                source_context=ctx,
            )
        except Exception:
            return ctx
        if window > 0:
            ctx["model_context_window"] = window
        return ctx


def _memory_is_good_enough(context_result: Any, classification: dict[str, Any]) -> bool:
    if getattr(context_result, "local_candidates", None):
        top = float(context_result.local_candidates[0].get("score") or 0.0)
        if top >= 0.64:
            return True
    retrieval_confidence = float(getattr(context_result, "retrieval_confidence_score", 0.0) or 0.0)
    task_class = str(classification.get("task_class", "unknown"))
    if task_class in {"shell_guidance", "file_inspection"} and retrieval_confidence >= 0.45:
        return True
    return retrieval_confidence >= 0.72


def _force_model_on_chat_surface(
    *,
    force_model: bool,
    surface: str,
    source_context: dict[str, Any] | None,
) -> bool:
    if force_model:
        return True
    normalized_surface = str(surface or "").strip().lower()
    if normalized_surface in _CHAT_TRUTH_SURFACES:
        return True
    source_surface = str((source_context or {}).get("surface", "") or "").strip().lower()
    return source_surface in _CHAT_TRUTH_SURFACES


def _provider_role_for_request(role: object) -> ProviderRole:
    candidate = str(role or "auto").strip().lower()
    if candidate in {"drone", "queen"}:
        return candidate
    return "auto"


def _request_prompt_budget(request: Any) -> dict[str, Any]:
    metadata = getattr(request, "metadata", None)
    if not isinstance(metadata, dict):
        return {}
    prompt_budget = metadata.get("prompt_budget")
    return dict(prompt_budget) if isinstance(prompt_budget, dict) else {}


def _request_tool_call_resolution(request: Any) -> dict[str, Any] | None:
    """The adapter-stamped tool-call recovery state, read back for the durable routing events.

    Same read-back pattern as `_request_prompt_budget`: the adapter writes `request.metadata`
    and this router copies it into the model routing event so the typed state
    (parsed / repaired / rejected) survives the turn in `runtime_session_events`. Returns None —
    not an empty dict — when no resolution was stamped, so `_emit_model_routing_event`'s
    None-filter keeps non-tool events unchanged."""

    metadata = getattr(request, "metadata", None)
    if not isinstance(metadata, dict):
        return None
    resolution = metadata.get("tool_call_resolution")
    return dict(resolution) if isinstance(resolution, dict) and resolution else None


def _emit_grounding_dropped_event(
    source_context: dict[str, Any] | None,
    *,
    manifest: Any,
    request: Any,
    identity: dict[str, Any],
) -> None:
    """Announce that the answer just produced was NOT grounded in the retrieved context.

    The budget guard shed the retrieved block to fit the window, so the model answered the
    turn without the material that was fetched to answer it. Telemetry alone records this
    inside the call event's payload; this raises it to its own bus event so a trace or
    receipt names it rather than leaving it buried in a nested dict.
    """
    prompt_budget = _request_prompt_budget(request)
    if not prompt_budget.get("grounding_dropped"):
        return
    _emit_model_routing_event(
        source_context,
        "model_lane_grounding_dropped",
        "Retrieved context did not fit the model's context window and was dropped; "
        "this answer is not grounded in it.",
        **identity,
        provider_id=getattr(manifest, "provider_id", ""),
        model_id=getattr(manifest, "model_name", ""),
        num_ctx=prompt_budget.get("num_ctx"),
        dropped_retrieved_messages=prompt_budget.get("dropped_retrieved_messages"),
        estimated_prompt_tokens_before=prompt_budget.get("estimated_prompt_tokens_before"),
        available_prompt_tokens=prompt_budget.get("available_prompt_tokens"),
        prompt_budget=prompt_budget,
    )


_PRESENTATION_CONTEXT_KEYS = (
    "x_editorial_turn",
    "ordinary_chat_output_policy",
    # The constraint the router enforces lives on the request metadata; the selector reads the
    # same record through the one closed door (response_constraint_from_metadata).
    "response_constraint",
    "raw_output_contract",
)


def _presentation_selection_context(
    source_context: dict[str, Any] | None,
    request_metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    """The typed turn context the C19 selector reads: source_context plus the request-metadata
    records that stand the selection down. One builder for every lane that runs the selector."""
    selection_context = dict(source_context or {})
    metadata = dict(request_metadata or {})
    for key in _PRESENTATION_CONTEXT_KEYS:
        if metadata.get(key):
            selection_context.setdefault(key, metadata[key])
    return selection_context


def _select_and_record_presentation(
    response_text: str,
    selection_context: dict[str, Any],
    source_context: dict[str, Any] | None,
) -> dict[str, Any]:
    """C19 AUTOMATIC PRESENTATION SELECTION -- one deterministic pass over the candidate bytes
    and the typed turn context (never the prompt), filed on the turn ledger.

    The router's source_context is a per-call copy on some lanes; the ledger is the channel
    that survives those copies and hands the record to the response.commit assembly."""
    record = select_presentation(str(response_text or ""), selection_context)
    if isinstance(source_context, dict):
        source_context["presentation_selection"] = record
    record_presentation_selection(source_context, record)
    return record


def _effective_output_mode(response: Any, requested_mode: str) -> str:
    """Which contract a provider reply is validated against.

    Normally the requested mode. The one exception is an UPGRADE: the adapter repairs a prose tool
    call into a dispatchable intent, and when it does so on a turn that asked for plain text it says
    so by setting `tool_intent` on the response. Validating that reply as plain text would hand the
    operator the tool-call JSON as their answer.

    Only an upgrade is honoured, never a downgrade. `ModelResponse.output_mode` defaults to
    `plain_text`, so any adapter or test double that does not set it would otherwise silently
    downgrade a genuine tool_intent turn into a contract failure — which is exactly what happened
    when this first read the field directly.
    """

    declared = str(getattr(response, "output_mode", "") or "").strip()
    return "tool_intent" if declared == "tool_intent" else str(requested_mode)


# A reply that is only these characters is empty at a structural layer rather than a textual one.
# `"[]".strip()` is truthy, so without naming them a model that answered with a bare empty JSON
# literal passed as a successful turn and the operator was shown `[]` as the reply.
_STRUCTURALLY_EMPTY_ANSWERS = frozenset(
    {"", "[]", "{}", "null", "none", "nil", '""', "''", "[ ]", "{ }"}
)


_CONTRACT_FAILURE_TEXT_LIMIT = 400


def _contract_failure_details(decision: Any) -> dict[str, Any]:
    """The recorded cause of a contract rejection, ready to ride on its ledger event.

    Reads only what the decision already carries; nothing is derived. Returns an empty dict when
    the decision recorded no cause, so an unexplained rejection stays visibly unexplained instead
    of acquiring an invented one. Bounded, and never a traceback: this reaches the Activity panel.
    """

    details = dict(getattr(decision, "details", None) or {})

    def _text(value: Any) -> str:
        return " ".join(str(value or "").split()).strip()[:_CONTRACT_FAILURE_TEXT_LIMIT]

    out: dict[str, Any] = {}
    # The soft shapes name themselves in `reason`; a hard rejection names itself in
    # `contract_error`. Both are the same question ("why was this output refused?"), so they land
    # on the same key and whichever is present answers it.
    reason = _text(details.get("reason")) or _text(details.get("contract_error"))
    if reason:
        out["reason"] = reason
    if details.get("empty_completion"):
        out["error_kind"] = "empty_completion"
    elif details.get("reasoning_only_completion"):
        out["error_kind"] = "reasoning_only_completion"
    elif _text(details.get("contract_error")):
        out["error_kind"] = "contract_validation"
    warnings = details.get("warnings")
    if isinstance(warnings, (list, tuple)) and warnings:
        # Bounded twice over -- count and length. Measured before choosing these numbers: a
        # failure-shaped row already costs ~1.5 KB of details_json, and an unbounded warning list
        # is the one field here that could multiply that rather than add to it.
        out["contract_warnings"] = [_text(item)[:160] for item in list(warnings)[:4]]
    return out


def _soft_failure_details(answer: str) -> dict[str, Any] | None:
    """Why a COMPLETED lane must still be treated as failed, or None when the answer is real.

    `_validate_contract` reports ok=True for all of these — there are no foreign markers in nothing,
    and none in a monologue either — so each was built valid/used_model=True and returned
    immediately: no retry, no next candidate.

    Three shapes, one soft-failure path:

    * NO CONTENT. Measured 2026-07-28: a 550B reasoning model on plain_text turns at max_tokens
      284/440 spent the budget reasoning and returned nothing. The user got "I couldn't get a usable
      model response" on "Hey", and on one turn a blank bubble.
    * STRUCTURALLY EMPTY. Measured 2026-07-31 on poolside/laguna-s-2.1:free, which returned `[]`
      fifteen times in a row on one build request. Empty at a different layer, same failure.
    * REASONING ONLY. Measured live 2026-08-01: a hard question came back as 7,786 tokens of "Let me
      look at... But wait... Actually", cut off mid-sentence, with no answer in it and two explicit
      instructions never addressed. Handing that to the user is worse than either empty case — it
      LOOKS like output. It is not shown and it is not silently blanked; it escalates.

    The cost of being wrong here is one retry on another model, never a wrong answer delivered — and
    `reasoning_only_markers` is built to make that trade safely: a reply that merely discusses
    reasoning, or corrects itself mid-answer, is not a monologue.
    """

    text = str(answer or "").strip()
    if text in _STRUCTURALLY_EMPTY_ANSWERS:
        return {
            "empty_completion": True,
            "reason": (
                "provider returned no content"
                if not text
                else f"provider returned a structurally empty answer ({text})"
            ),
        }
    markers = reasoning_only_markers(text)
    if markers:
        return {
            "reasoning_only_completion": True,
            "reasoning_markers": markers,
            "reason": "provider returned its reasoning monologue with no answer in it",
        }
    return None


def _emit_attempt_chain_timing(
    source_context: dict[str, Any] | None,
    *,
    attempt_timings: list[dict[str, Any]],
    fallback_budget_seconds: float | None,
    outcome: str,
) -> None:
    """Emit the whole fallback chain's timing once, when more than one call was made.

    A single-attempt turn already carries its duration on the decision; a CHAIN is the shape the
    v0.5.0 smoke run could not read, so it gets its own event rather than being reconstructed from
    per-lane events by whoever reads the trace next.
    """
    if len(attempt_timings or []) < 2:
        return
    summary = summarize_attempt_timings(attempt_timings)
    _emit_model_routing_event(
        source_context,
        "model.fallback_chain_timing",
        (
            f"{summary['attempts']} provider attempts took {summary['total_seconds']:.1f}s; "
            f"{summary['slowest_provider_id']} was slowest at {summary['slowest_seconds']:.1f}s."
        ),
        outcome=outcome,
        attempt_timings=list(attempt_timings),
        fallback_budget_seconds=fallback_budget_seconds,
        **summary,
    )


def _record_attempt_timing(
    timings: list[dict[str, Any]],
    *,
    manifest: Any,
    seconds: float,
    outcome: str,
    error: str = "",
    timeout_seconds: float = 0.0,
) -> dict[str, Any]:
    """Append what one provider attempt cost, and return the entry.

    Rounded to milliseconds because this travels into turn truth and a full float there is noise.
    `timeout_seconds` is the ceiling this attempt actually ran under -- the fallback budget caps a
    candidate's own timeout, so the manifest's configured value is not what bound it.
    """
    entry = {
        "provider_id": str(getattr(manifest, "provider_id", "") or ""),
        "model_id": str(getattr(manifest, "model_name", "") or ""),
        "seconds": round(max(0.0, float(seconds)), 3),
        "outcome": str(outcome or ""),
        "timeout_seconds": round(max(0.0, float(timeout_seconds)), 3),
        **({"error": error} if error else {}),
    }
    timings.append(entry)
    return entry


def summarize_attempt_timings(timings: list[dict[str, Any]]) -> dict[str, Any]:
    """The chain in one line: how many calls, how long in total, and which one dominated."""
    entries = [entry for entry in timings or [] if isinstance(entry, dict)]
    if not entries:
        return {"attempts": 0, "total_seconds": 0.0, "slowest_provider_id": "", "slowest_seconds": 0.0}
    slowest = max(entries, key=lambda entry: float(entry.get("seconds") or 0.0))
    return {
        "attempts": len(entries),
        "total_seconds": round(sum(float(entry.get("seconds") or 0.0) for entry in entries), 3),
        "slowest_provider_id": str(slowest.get("provider_id") or ""),
        "slowest_seconds": float(slowest.get("seconds") or 0.0),
    }


_ATTACHMENT_OUTCOME_EVENTS = {"read": "attachment_read", "sent": "attachment_sent", "omitted": "attachment_omitted"}


def _record_attachment_delivery(
    source_context: dict[str, Any] | None,
    request: Any,
    *,
    manifest: Any,
) -> None:
    """What THIS provider call did with each attachment, on the authority and in Activity.

    Read from the wire payload's own receipt (`request.metadata["attachment_delivery"]`, written by
    the adapter at the moment it rendered the messages) -- never re-derived here, so the row can
    only say what the payload actually carried. Names, kinds, ids and reasons; never contents.
    """
    delivery = list(dict(getattr(request, "metadata", None) or {}).get("attachment_delivery") or [])
    if not delivery:
        return
    context = source_context if isinstance(source_context, dict) else {}
    session_id = str(context.get("runtime_session_id") or context.get("session_id") or "").strip()
    turn_id = str(context.get("attachment_turn_id") or "").strip()
    if session_id and turn_id:
        with contextlib.suppress(Exception):
            chat_attachments.record_delivery(session_id=session_id, turn_id=turn_id, receipts=delivery)
    if isinstance(source_context, dict):
        source_context["attachment_delivery"] = [dict(item) for item in delivery if isinstance(item, dict)]
    provider_id = str(getattr(manifest, "provider_id", "") or "")
    model_id = str(getattr(manifest, "model_name", "") or "")
    for item in delivery:
        if not isinstance(item, dict):
            continue
        outcome = str(item.get("outcome") or "")
        name = str(item.get("name") or "attachment")
        verb = {"read": "read by", "sent": "sent to", "omitted": "not read by"}.get(outcome, "not used by")
        _emit_model_routing_event(
            source_context,
            _ATTACHMENT_OUTCOME_EVENTS.get(outcome, "attachment_ignored"),
            f"{name}: {verb} {provider_id or 'the model'}",
            attachment_id=str(item.get("attachment_id") or ""),
            attachment_name=name,
            kind=str(item.get("kind") or ""),
            outcome=outcome,
            reason=str(item.get("reason") or ""),
            provider_id=provider_id,
            model_id=model_id,
        )


def _empty_reply_record(exc: BaseException) -> dict[str, Any] | None:
    facts = getattr(exc, "diagnostics", None)
    return dict(facts) if isinstance(facts, dict) and facts else None


def _empty_reply_class(exc: BaseException) -> str:
    facts = _empty_reply_record(exc)
    if facts is None:
        return ""
    try:
        from core.normalized_provider_result import classify_empty_reply

        return classify_empty_reply(facts)
    except Exception:
        return ""


def _emit_model_routing_event(
    source_context: dict[str, Any] | None,
    event_type: str,
    message: str,
    **details: Any,
) -> None:
    if source_context is None:
        return
    if event_type == "model_lane_proof":
        visible_details = {key: value for key, value in details.items() if value is not None}
    else:
        visible_details = {key: value for key, value in details.items() if value is not None and value != ""}
    emit_runtime_event(
        source_context,
        event_type=event_type,
        message=message,
        details=visible_details,
    )


def _record_model_provenance(
    source_context: dict[str, Any] | None,
    *,
    manifest: Any,
    response: Any,
) -> None:
    """Publish `source_context["model_provenance"]` for `core.runtime_evidence` to read.

    INTEGRATION BRIDGE (Harbourmaster, reliability wave). Relay produces
    `ModelResponse.provider_attested_model` -- set by an adapter only from genuine provider
    response-body evidence, and left `None` when the provider's reply carried no model identity.
    Ledger consumes a per-turn dict and requires BOTH an attested model and a named source before
    it will call anything an attestation (`ModelProvenance.has_provider_attestation`). Neither lane
    owned the join; this function is it.

    The one rule that matters: `attestation_source` is written ONLY when the provider actually
    attested. It is never derived from `requested_model`, `resolved_model`, `actual_model`, the
    manifest, or the runtime's own selection. Copying the selected model in would manufacture a
    second, independent-looking witness out of the first one, which is precisely the laundering the
    ledger's two-field rule exists to prevent -- so an omission stays an omission, and a provider
    that contradicts the model the runtime selected keeps that contradiction visible rather than
    having it reconciled away.

    `runtime_selected_model` is the runtime's own claim about itself and is read from the manifest;
    it is deliberately kept in a different field from the attested one for the same reason.

    Not raised on: a caller with no `source_context` has nowhere to publish to, which is a normal
    non-HTTP/internal call, not an error.
    """
    if source_context is None:
        return
    attested_raw = getattr(response, "provider_attested_model", None)
    attested = str(attested_raw).strip() if attested_raw else ""
    source_context[PROVENANCE_CONTEXT_KEY] = {
        "requested_model": str(source_context.get("requested_model") or "").strip(),
        "runtime_selected_model": str(getattr(manifest, "model_name", "") or "").strip(),
        "provider_attested_model": attested,
        # Empty unless the line above is non-empty. This conditional IS the contract.
        "attestation_source": "relay.provider_response" if attested else "",
    }


def _lane_proof_payload(
    *,
    source_context: dict[str, Any] | None,
    task: Any,
    classification: dict[str, Any],
    task_kind: str,
    output_mode: str,
    provider_role: ProviderRole,
    autopilot_plan: dict[str, Any],
    manifest: Any | None,
    capability: Any | None,
    phase: str,
    attempted: list[str],
    failover_used: bool,
    fallback_reason: str = "",
) -> dict[str, Any]:
    planned_provider = str(autopilot_plan.get("selected_provider_id") or "").strip()
    planned_model = str(autopilot_plan.get("selected_model") or "").strip()
    actual_provider = str(getattr(manifest, "provider_id", "") or "").strip()
    actual_model = str(getattr(manifest, "model_name", "") or "").strip()
    mismatch = bool(actual_provider and planned_provider and actual_provider != planned_provider)
    visible_phase = "failed" if mismatch and phase == "completed" else phase
    normalized_fallback = fallback_reason or _lane_fallback_reason(
        lane=str(autopilot_plan.get("lane") or ""),
        actual_model=actual_model,
        mismatch=mismatch,
    )
    backend = _backend_for_provider(actual_provider or planned_provider)
    acceleration = backend_acceleration_proof(
        provider_id=actual_provider or planned_provider,
        model_id=actual_model or planned_model,
        backend=backend,
        probe=True,
    )
    return {
        "schema": "vool.model_lane_proof.v1",
        "turn_id": str((source_context or {}).get("turn_id") or ""),
        "session_id": str((source_context or {}).get("runtime_session_id") or (source_context or {}).get("session_id") or ""),
        "task_class": str(classification.get("task_class", "unknown") or "unknown"),
        "task_kind": str(task_kind or "unknown"),
        "output_mode": str(output_mode or "plain_text"),
        "complexity": _complexity_for_lane(str(autopilot_plan.get("lane") or "")),
        "lane": str(autopilot_plan.get("lane") or "unknown"),
        "lane_type": str(autopilot_plan.get("lane") or "unknown"),
        "phase": visible_phase,
        "provider_role": provider_role,
        "role": provider_role,
        "planned_provider_id": planned_provider,
        "planned_model_id": planned_model,
        "provider_id": actual_provider or planned_provider,
        "model_id": actual_model or planned_model,
        "actual_adapter_provider_id": actual_provider,
        "actual_adapter_model_id": actual_model,
        "backend": backend,
        "tokens_per_second": float(getattr(capability, "tokens_per_second", 0.0) or 0.0),
        "measurement_source": str(getattr(capability, "measurement_source", "") or "unknown"),
        "queue_depth": int(getattr(capability, "queue_depth", 0) or 0),
        "fallback_reason": normalized_fallback,
        "verifier_status": _verifier_status(autopilot_plan),
        "verifier_provider_id": str(autopilot_plan.get("verifier_provider_id") or "").strip(),
        "verifier_model_id": str(autopilot_plan.get("verifier_model") or "").strip(),
        "kv_cache_status": acceleration.kv_cache_status or _kv_cache_status(autopilot_plan),
        "backend_cache_proof": acceleration.backend_cache_proof,
        "speculative_status": acceleration.speculative_status,
        "speculative_proof": acceleration.speculative_proof,
        "eagle_status": acceleration.eagle_status,
        "eagle_proof": acceleration.eagle_proof,
        "attempted": list(attempted),
        "failover_used": bool(failover_used),
        "mismatch": mismatch,
        "failure_reason": "planned_adapter_mismatch" if mismatch else "",
    }


def _complexity_for_lane(lane: str) -> str:
    return {
        "tiny": "trivial",
        "daily": "medium",
        "deep": "hard",
        "cloud": "remote",
        "human": "blocked",
    }.get(str(lane or "").strip().lower(), "unknown")


def _backend_for_provider(provider_id: str) -> str:
    lowered = str(provider_id or "").strip().lower()
    if "llamacpp" in lowered or "llama.cpp" in lowered:
        return "llama.cpp"
    if "mlx" in lowered:
        return "mlx"
    if "vllm" in lowered:
        return "vllm"
    if "ollama" in lowered:
        return "ollama"
    return "unknown"


def _lane_fallback_reason(*, lane: str, actual_model: str, mismatch: bool) -> str:
    if mismatch:
        return "planned_adapter_mismatch"
    if str(lane or "").strip().lower() == "tiny" and model_parameter_billions(actual_model) > 4.0:
        return "tiny_lane_unavailable"
    return ""


def _ranked_heavy_manifest(ranked_manifests: Any) -> Any | None:
    """The first ranked manifest that can serve a heavy request, or None.

    Same total-parameter reading as the explicit_heavy flag (>= 24B, total rather than MoE-active),
    so the pin that raised the flag is recognised as the lane that satisfies it.
    """
    for manifest in list(ranked_manifests or ()):
        if model_total_parameter_billions(str(getattr(manifest, "model_name", "") or "")) >= 24.0:
            return manifest
    return None


def _autopilot_block_reason(autopilot_plan: dict[str, Any]) -> str:
    warnings = {str(item).strip() for item in list(autopilot_plan.get("warnings") or []) if str(item).strip()}
    if "explicit_heavy_lane_unavailable" in warnings:
        return "explicit_heavy_lane_unavailable"
    return ""


def _planned_heavy_manifest_failed(autopilot_plan: dict[str, Any], manifest: Any) -> bool:
    # ARGUS-confirmed defect: this used to fire on model-id size alone, with no check of whether
    # THIS turn actually asked for a heavy model. Auto routing can pick a large model purely on
    # ranking merit (a free cloud candidate winning normal scoring) with no such request ever
    # made -- and that alone used to be enough to abort the entire fallback loop, skipping every
    # remaining ranked candidate including a healthy local one, over a request the user never
    # made. `explicit_heavy` (core.local_inference_autopilot._explicit_heavy_requested, threaded
    # onto the plan) is the runtime's own truth for "did this turn actually ask for a heavy
    # model" -- gate on that, not on re-deriving another size heuristic.
    if not bool(autopilot_plan.get("explicit_heavy")):
        return False
    planned_provider = str(autopilot_plan.get("selected_provider_id") or "").strip()
    if not planned_provider or planned_provider != str(getattr(manifest, "provider_id", "") or "").strip():
        return False
    # Same total-parameter reading the explicit_heavy flag used: re-deriving with the
    # MoE-active-count function made the two disagree for one pinned name (measured 2026-08-19).
    return model_total_parameter_billions(str(autopilot_plan.get("selected_model") or getattr(manifest, "model_name", "") or "")) >= 24.0


def _fallback_reserve_seconds(
    *,
    ranked_manifests: list[Any],
    skipped_provider_ids: set[str],
    current_index: int,
    autopilot_plan: dict[str, Any],
    manifest: Any,
    floor_seconds: float = _FALLBACK_ATTEMPT_FLOOR_SECONDS,
) -> float:
    """SWITCHBOARD-locked policy (D1): the amount of THIS candidate's own remaining-budget window
    to withhold, so each genuinely reachable successor still queued behind it keeps at least
    ``floor_seconds`` of its own. Without this, the first candidate tried can be capped to
    (almost) the ENTIRE fallback budget, and a hang that uses the whole of it leaves nothing for
    the between-attempts deadline check to protect -- later ranked candidates are never tried
    even though they were eligible.

        reserve = floor_seconds * n_after

    where ``n_after`` is the count of ranked candidates strictly after this one in iteration
    order that were not already skipped before this loop started (a genuine future attempt, not
    a stale re-count of ones already tried in an earlier phase).

    Two cases collapse ``n_after`` to 0, so the caller need not special-case them:

    * No successor exists at all (the last candidate in the ranked list, or an explicit model pin
      that already narrowed ranking to exactly one provider before this loop even started) --
      there is nothing to reserve time FOR.
    * This candidate's own failure would trip ``_planned_heavy_manifest_failed`` and terminate
      the fallback loop outright, regardless of what remains ranked behind it. Reserving time for
      candidates the explicit-heavy policy will refuse to try anyway would only needlessly shrink
      THIS candidate's own window for no protective benefit -- the loop was never going to reach
      them.
    """
    if _planned_heavy_manifest_failed(autopilot_plan, manifest):
        return 0.0
    n_after = sum(
        1
        for later in ranked_manifests[current_index + 1 :]
        if later.provider_id not in skipped_provider_ids
    )
    return floor_seconds * n_after


_VERDICT_RE = re.compile(r"verdict\s*[:=]\s*[\"']?(pass|fail)\b", re.IGNORECASE)
_VERIFIER_FAIL_HEAD_RE = re.compile(r"^\s*fail\b", re.IGNORECASE)


def _verifier_verdict(text: str) -> str:
    """Parse the verifier lane's reply for a blocking verdict, structured-first:
      1. a JSON object with a "verdict" field wins;
      2. else the LAST explicit 'VERDICT: PASS|FAIL' token wins, so negated prose
         ("FAIL is not the right call. VERDICT: PASS") resolves to pass;
      3. else a reply that opens with 'fail' is a fail;
      4. else invalid. A malformed verifier reply is unavailable evidence, never
         silently upgraded to an independent PASS.
    """
    raw = str(text or "")
    stripped = raw.strip()
    if stripped[:1] in "{[":
        try:
            payload = json.loads(stripped)
            if isinstance(payload, dict):
                verdict = str(payload.get("verdict") or "").strip().lower()
                if verdict in {"pass", "fail"}:
                    return verdict
        except Exception:
            pass
    explicit = [m.group(1).lower() for m in _VERDICT_RE.finditer(raw[:1000])]
    if explicit:
        return explicit[-1]
    head = " ".join(stripped.splitlines()[:1])[:200]
    if _VERIFIER_FAIL_HEAD_RE.match(head):
        return "fail"
    return "invalid"


# Only explicit verifier failures warrant a warning that a verification pass flagged
# the answer. Unavailable states are neutral: no independent verdict exists.
_VERIFIER_FAILED = {
    "flagged",
}
_VERIFIER_UNAVAILABLE = {
    "blocked",
    "blocked_failed_lane",
    "degraded_same_model",
    "independent_failed",
    "independent_malformed",
    "not_run_primary_failed",
}


def _apply_verifier_gate(decision: ModelExecutionDecision, verifier_status: str) -> None:
    """When the verifier lane ran and FAILED the primary answer, mark the decision
    needs-review and cap its trust, so an unverified answer is not delivered as a
    confident 'done'. Verifier-unavailable states (blocked/degraded) are left as
    annotations only — they do not newly gate the answer."""
    if verifier_status in _VERIFIER_FAILED:
        details = dict(decision.details or {})
        details["needs_review"] = True
        details["verifier_gate"] = verifier_status
        decision.details = details
        decision.trust_score = min(float(decision.trust_score or 0.0), 0.5)
    elif verifier_status in _VERIFIER_UNAVAILABLE:
        details = dict(decision.details or {})
        details["verifier_gate"] = "unverified"
        details["verifier_status"] = verifier_status
        decision.details = details


VERIFIER_DRAFT_CAVEAT = (
    "Heads up: an independent verification pass flagged this answer, so treat it as a "
    "draft and double-check it before relying on it."
)


def apply_verifier_draft_caveat(
    response_text: str,
    decision: Any,
    *,
    preserve_exact_shape: bool = False,
    user_text: str = "",
    source_context: dict[str, Any] | None = None,
) -> str:
    """Prepend a short, non-blocking draft caveat to the user-facing reply when the
    verifier flagged the answer (decision.details['needs_review'], set only on an
    explicit verifier FAIL). The answer is not blocked — just marked a draft.
    Idempotent, and a no-op for a passing/verifier-unavailable/empty answer. A candidate that
    independently validates against an exact-output seal keeps its canonical bytes and records
    the suppressed decoration in typed response-control metadata."""
    text = str(response_text or "")
    if not text.strip():
        return response_text
    seal = None
    if user_text:
        from core.exact_output_seal import validated_exact_output_seal

        seal = validated_exact_output_seal(user_text, text)
    if seal is not None:
        receipt = {
            **seal.to_dict(),
            "advisory_suppressed": bool(
                (getattr(decision, "details", None) or {}).get("needs_review")
            ),
            # No review_flagged here: the suppression block below records it for BOTH suppressed
            # paths, and a sabotage run proved a copy here is dead weight the block masks.
        }
        details = dict(getattr(decision, "details", None) or {})
        details["exact_output_seal"] = receipt
        with contextlib.suppress(AttributeError, TypeError):
            decision.details = details
        if isinstance(source_context, dict):
            control = dict(source_context.get("response_control") or {})
            control["verifier_presentation"] = receipt
            source_context["response_control"] = control
    if preserve_exact_shape or seal is not None:
        # Suppressing the INLINE caveat must never suppress the VERDICT. Measured: a verifier-
        # flagged one-word answer ("Banana", correct value Cherry) shipped as a bare word with no
        # signal anywhere in the reply -- the seal branch above recorded the suppression, but this
        # branch (preserve_exact_shape with no seal) recorded nothing, and the page rendered
        # nothing either way. The flag rides in response_control so the surface can badge it
        # OUTSIDE the canonical bytes the contract owns.
        if (getattr(decision, "details", None) or {}).get("needs_review") and isinstance(
            source_context, dict
        ):
            control = dict(source_context.get("response_control") or {})
            presentation = dict(control.get("verifier_presentation") or {})
            presentation["advisory_suppressed"] = True
            presentation["review_flagged"] = True
            control["verifier_presentation"] = presentation
            source_context["response_control"] = control
        return response_text
    if not (getattr(decision, "details", None) or {}).get("needs_review"):
        return response_text
    if VERIFIER_DRAFT_CAVEAT in text:
        return response_text
    return f"{VERIFIER_DRAFT_CAVEAT}\n\n{text}"


def _verifier_status(autopilot_plan: dict[str, Any]) -> str:
    if not bool(autopilot_plan.get("verifier_required")):
        return "not_required"
    verifier_provider = str(autopilot_plan.get("verifier_provider_id") or "").strip()
    selected_provider = str(autopilot_plan.get("selected_provider_id") or "").strip()
    if not selected_provider:
        return "blocked"
    if not verifier_provider:
        return "blocked"
    if verifier_provider == selected_provider:
        return "degraded_same_model"
    return "independent"


def _kv_cache_status(autopilot_plan: dict[str, Any]) -> str:
    prefix_cache = autopilot_plan.get("prefix_cache")
    if not isinstance(prefix_cache, dict):
        return "unsupported"
    backend = str(prefix_cache.get("backend") or "").strip()
    if backend == "ollama":
        return "ollama=not_supported_keep_alive_only"
    if bool(prefix_cache.get("supported")):
        return f"{backend}=supported_not_active"
    return f"{backend or 'unknown'}=unsupported"


def _streaming_requested(source_context: dict[str, Any] | None, *, output_mode: str) -> bool:
    if output_mode != "plain_text":
        return False
    return bool(str((source_context or {}).get("runtime_event_stream_id") or "").strip())


def _ephemeral_stream_context(source_context: dict[str, Any] | None) -> dict[str, Any]:
    stream_id = str((source_context or {}).get("runtime_event_stream_id") or "").strip()
    if not stream_id:
        return {}
    return {"runtime_event_stream_id": stream_id}


def _manifest_locality(manifest: Any) -> str:
    deployment_class = str(getattr(manifest, "metadata", {}).get("deployment_class") or "").strip().lower()
    if deployment_class in {"local", "remote"}:
        return deployment_class
    base_url = str(getattr(manifest, "runtime_config", {}).get("base_url") or "").strip().lower()
    if base_url.startswith("http://127.0.0.1") or base_url.startswith("http://localhost"):
        return "local"
    return "remote"


def execution_visibility(decision: ModelExecutionDecision) -> dict[str, Any]:
    """Describe where the answer resides and whether a model is actively running.

    Cache and memory answers are local-resident but do not perform active inference. A served
    provider answer is active inference, and its locality comes from the provider manifest or the
    broker's explicit remote decision. Unknown stays unknown instead of being guessed from a name.
    """
    details = dict(getattr(decision, "details", {}) or {})
    source = str(getattr(decision, "source", "") or "").strip().lower()
    used_model = bool(getattr(decision, "used_model", False))
    locality = str(details.get("locality") or "").strip().lower()
    if locality not in {"local", "remote"}:
        if source in {"memory_hit", "exact_cache_hit", "candidate_cache_hit"} or not used_model:
            locality = "local"
        else:
            locality = "unknown"
    active_inference = bool(details.get("active_inference", used_model)) if used_model else False
    return {
        "known": locality != "unknown",
        "residency": locality,
        "active_inference": active_inference,
        "provider_id": getattr(decision, "provider_id", None),
        "model_id": getattr(decision, "model_name", None),
    }


def _mux_vote(
    succeeded: list[tuple[Any, ModelAdapter, ModelResponse]],
    rank_index: dict[str, int],
) -> tuple[Any, ModelAdapter, ModelResponse]:
    """Pick the (manifest, adapter, response) whose answer the most models agree on. Agreement is by
    slm_mux.default_answer_key over the response text; ties break toward the higher-ranked model."""
    ordered = sorted(succeeded, key=lambda item: rank_index.get(item[0].provider_id, 1_000))
    keys = [default_answer_key(str(item[2].output_text or "")) for item in ordered]
    top_key, _count = Counter(keys).most_common(1)[0]
    for item, key in zip(ordered, keys, strict=False):
        if key == top_key:
            return item
    return ordered[0]


def _local_remote_race_pair(ranked_manifests: list[Any]) -> tuple[Any, Any] | None:
    local_manifest = next((manifest for manifest in ranked_manifests if _manifest_locality(manifest) == "local"), None)
    remote_manifest = next((manifest for manifest in ranked_manifests if _manifest_locality(manifest) == "remote"), None)
    if local_manifest is None or remote_manifest is None:
        return None
    if local_manifest.provider_id == remote_manifest.provider_id:
        return None
    return local_manifest, remote_manifest


def _can_prioritize_autopilot_selection(
    ranked_manifests: list[Any],
    selected_provider_id: str,
    *,
    allow_paid_fallback: bool,
) -> bool:
    selected = next((manifest for manifest in ranked_manifests if manifest.provider_id == selected_provider_id), None)
    if selected is None:
        return False
    if not ranked_manifests or not allow_paid_fallback:
        return True
    if _manifest_locality(ranked_manifests[0]) == "remote" and _manifest_locality(selected) == "local":
        return False
    if _manifest_locality(ranked_manifests[0]) != "local" or _manifest_locality(selected) != "remote":
        return True
    return _local_remote_race_pair(ranked_manifests) is None


def _prioritize_autopilot_selection(ranked_manifests: list[Any], selected_provider_id: str) -> list[Any]:
    return sorted(
        ranked_manifests,
        key=lambda manifest: 0 if manifest.provider_id == selected_provider_id else 1,
    )
