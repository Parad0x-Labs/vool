"""One normalized provider result, at the router boundary, for both response types.

`adapters/base_adapter.ModelResponse` (System A: the primary `ModelAdapter` hierarchy -- Ollama
and most cloud lanes) and `core/cloud_provider_contract.CloudModelResponse` (System B: the
`CloudProviderAdapter` hierarchy behind the free/paid cloud-escalation broker) are structurally
different dataclasses with no shared interface. `core/memory_first_router.py` hand-converts each
into `ModelExecutionDecision` through two independent code paths -- and that duplication has
already caused one real, shipped regression: `_try_free_cloud_boost`'s own inline comment records
that the free-cloud lane silently dropped every tool call after the first ("dropping call #2")
until a line was added by hand to re-derive it, one lane at a time, from a difference the two
hand-written conversions had drifted into.

This module is deliberately NOT a rewrite of either adapter hierarchy (that is a larger, separate
architectural change). It is one narrow seam: a single, tested function per source type that both
paths convert through before building anything else, so a field neither knows to carry forward
(tool_calls, usage presence, error classification) is wrong in exactly one place if it is ever
wrong at all.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ProviderErrorClass(str, Enum):
    """Exhaustive, named failure classes for a provider call. Replaces the previous practice of
    collapsing every non-success outcome into a single `model.call_failed` event with a free-text
    reason string -- that string was suitable for a human reading a log, not for a caller deciding
    whether retrying the SAME provider/model is worth attempting."""

    PROVIDER_CONNECTION_ERROR = "PROVIDER_CONNECTION_ERROR"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PROVIDER_RATE_LIMIT = "PROVIDER_RATE_LIMIT"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    EMPTY_PROVIDER_RESPONSE = "EMPTY_PROVIDER_RESPONSE"
    MALFORMED_PROVIDER_RESPONSE = "MALFORMED_PROVIDER_RESPONSE"
    MALFORMED_TOOL_CALL = "MALFORMED_TOOL_CALL"
    CONTEXT_LENGTH_EXCEEDED = "CONTEXT_LENGTH_EXCEEDED"
    REQUIRED_TOOLS_NOT_OFFERED = "REQUIRED_TOOLS_NOT_OFFERED"
    PROVIDER_INTERNAL_ERROR = "PROVIDER_INTERNAL_ERROR"


class EmptyProviderResponseError(RuntimeError):
    """The provider returned a syntactically readable response with no answer candidate.

    This type is the stable contract.  Its message is diagnostic only: retry policy must not
    depend on one provider adapter preserving one English sentence forever.
    """


class MalformedProviderResponseError(RuntimeError):
    """The provider payload had a structurally invalid shape and must not be retried as empty."""


#: Finish reasons that say the provider stopped at the output ceiling (OpenAI and Anthropic dialects).
_LENGTH_FINISH_REASONS = frozenset({"length", "max_tokens", "max_output_tokens"})

EMPTY_REPLY_OUTPUT_BUDGET_EXHAUSTED = "output_budget_exhausted"
EMPTY_REPLY_UPSTREAM_EMPTY = "upstream_empty"
EMPTY_REPLY_REASONING_ONLY = "reasoning_only_no_answer"
EMPTY_REPLY_PARSING_LOSS = "text_outside_read_fields"
EMPTY_REPLY_UNCLASSIFIED = "unclassified"


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _text_size(value: Any) -> int:
    """Characters of text in a content field of any dialect (a string, or text parts), never the text."""
    if isinstance(value, str):
        return len(value.strip())
    if isinstance(value, list):
        return sum(
            len(str(part.get("text") or "").strip())
            for part in value
            if isinstance(part, dict) and str(part.get("type") or "text") in {"text", "output_text"}
        )
    return 0


def empty_reply_diagnostics(data: Any, *, max_tokens_sent: Any = None) -> dict[str, Any]:
    """Safe facts about a chat-completion body that carried no usable answer.

    Identifiers, the finish reason, reported usage (reasoning tokens when the provider reports
    them), the ceiling the request carried, and the PRESENCE and SIZE of the content, reasoning,
    tool-call and legacy text fields -- never their text, never a header, never a credential.
    Measured 2026-09-16 on candidate 035dea9b: five empty replies from a pinned cloud model were
    recorded as "provider response has no usable text and no tool call" with nothing else, so
    whether the model returned nothing, spent its whole budget reasoning, or answered in a field
    the reader does not read could not be told from the record.
    """
    body = data if isinstance(data, dict) else {}
    choices = [item for item in list(body.get("choices") or []) if isinstance(item, dict)]
    first = choices[0] if choices else {}
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    details = usage.get("completion_tokens_details") if isinstance(usage.get("completion_tokens_details"), dict) else {}
    reasoning_size = max(
        _text_size(message.get("reasoning")),
        _text_size(message.get("reasoning_content")),
        _text_size(first.get("reasoning")),
    )
    tool_calls = message.get("tool_calls") if isinstance(message.get("tool_calls"), list) else []
    return {
        "provider_response_id": str(body.get("id") or "")[:128],
        "provider_model": str(body.get("model") or "")[:128],
        "provider_object": str(body.get("object") or "")[:64],
        "choices": len(choices),
        "finish_reason": str(first.get("finish_reason") or first.get("stop_reason") or "")[:64],
        "native_finish_reason": str(first.get("native_finish_reason") or "")[:64],
        "prompt_tokens": _int_or_none(usage.get("prompt_tokens")),
        "completion_tokens": _int_or_none(usage.get("completion_tokens")),
        "total_tokens": _int_or_none(usage.get("total_tokens")),
        "reasoning_tokens": _int_or_none(details.get("reasoning_tokens")),
        "max_tokens_sent": _int_or_none(max_tokens_sent),
        "content_present": _text_size(message.get("content")) > 0,
        "content_chars": _text_size(message.get("content")),
        "reasoning_present": reasoning_size > 0,
        "reasoning_chars": reasoning_size,
        "tool_calls_present": bool(tool_calls),
        "tool_call_count": len(tool_calls),
        "legacy_text_present": _text_size(first.get("text")) > 0,
        "refusal_present": _text_size(message.get("refusal")) > 0,
        "error_present": isinstance(body.get("error"), (dict, str)) and bool(body.get("error")),
    }


def classify_empty_reply(diagnostics: dict[str, Any] | None) -> str:
    """What an empty reply's own evidence says it was -- or that it says nothing.

    ``output_budget_exhausted``: the provider stopped at the ceiling; the answer never started (a
    reasoning model that spent the ceiling thinking, or a ceiling below what the answer needed).
    ``reasoning_only_no_answer``: it stopped on its own with reasoning text and no content.
    ``text_outside_read_fields``: text arrived in a field the reader does not read (a legacy
    ``choices[0].text``): a parsing loss, not an empty model. ``upstream_empty``: a normal stop with
    nothing generated. Anything else stays ``unclassified``; absent evidence is not a verdict.
    """
    facts = dict(diagnostics or {})
    if not facts:
        return EMPTY_REPLY_UNCLASSIFIED
    finish = str(facts.get("finish_reason") or "").strip().casefold()
    if finish in _LENGTH_FINISH_REASONS:
        return EMPTY_REPLY_OUTPUT_BUDGET_EXHAUSTED
    if facts.get("legacy_text_present") and not facts.get("content_present"):
        return EMPTY_REPLY_PARSING_LOSS
    completion = facts.get("completion_tokens")
    reasoning_tokens = facts.get("reasoning_tokens")
    if finish in {"stop", "end_turn", "eos"}:
        if facts.get("reasoning_present") or (reasoning_tokens is not None and reasoning_tokens > 0):
            return EMPTY_REPLY_REASONING_ONLY
        if completion is not None and completion == 0:
            return EMPTY_REPLY_UPSTREAM_EMPTY
    return EMPTY_REPLY_UNCLASSIFIED


# Retrying the IDENTICAL provider/model is a reasonable thing to try automatically (the failure
# looks transient). The rest name a defect in the request, the model's output, or a genuine
# unavailability -- retrying the same call would just reproduce the same failure.
RETRYABLE_ERROR_CLASSES = frozenset(
    {
        ProviderErrorClass.PROVIDER_CONNECTION_ERROR,
        ProviderErrorClass.PROVIDER_TIMEOUT,
        ProviderErrorClass.PROVIDER_RATE_LIMIT,
        ProviderErrorClass.PROVIDER_INTERNAL_ERROR,
        ProviderErrorClass.EMPTY_PROVIDER_RESPONSE,
    }
)


def is_retryable(error_class: ProviderErrorClass | None) -> bool:
    return error_class is not None and error_class in RETRYABLE_ERROR_CLASSES


# Ordered so a more specific signal wins over a more generic one that might also match (e.g. a
# timeout message often also contains "provider" or "connection"-adjacent words).
_ERROR_CLASS_MARKERS: tuple[tuple[str, ProviderErrorClass], ...] = (
    ("required_tools_not_offered", ProviderErrorClass.REQUIRED_TOOLS_NOT_OFFERED),
    ("does not support required tools", ProviderErrorClass.REQUIRED_TOOLS_NOT_OFFERED),
    ("unknown_tool_name", ProviderErrorClass.MALFORMED_TOOL_CALL),
    ("malformed_tool_arguments", ProviderErrorClass.MALFORMED_TOOL_CALL),
    ("duplicate_tool_call", ProviderErrorClass.MALFORMED_TOOL_CALL),
    # adapters/openai_compatible_adapter.py::_extract_native_openai_tool_result's own bare
    # ValueError when tool_calls is absent AND no prose fallback could be recovered: tools were
    # offered and required, the provider answered, and produced no usable call at all -- a
    # tool-call defect (SWITCHBOARD Repair 3), not a generic transport/format problem.
    ("required native tool call is missing", ProviderErrorClass.MALFORMED_TOOL_CALL),
    ("native tool call required but the response produced no usable content", ProviderErrorClass.MALFORMED_TOOL_CALL),
    # These three MUST be checked before the generic "malformed provider response" marker just
    # below: adapters/{cloudflare_workers_ai,openrouter_cloud,generic_openai_cloud}_provider.py
    # all raise "malformed provider response: no content in ..." for exactly this case, and the
    # more specific, more informative EMPTY_PROVIDER_RESPONSE class must win over the generic one
    # a substring match on the shared prefix would otherwise pick first.
    ("no content in any choice", ProviderErrorClass.EMPTY_PROVIDER_RESPONSE),
    ("no content in result", ProviderErrorClass.EMPTY_PROVIDER_RESPONSE),
    ("did not include choices", ProviderErrorClass.EMPTY_PROVIDER_RESPONSE),
    ("no usable text and no tool call", ProviderErrorClass.EMPTY_PROVIDER_RESPONSE),
    # SWITCHBOARD final micro-repair (N2): adapters/openai_compatible_adapter.py::
    # _extract_openai_text's own bare RuntimeError when `message.content` is present but is
    # neither a string nor a list (a wrong-TYPE structural defect, distinct from "did not include
    # choices" above -- an entirely absent choices array -- and from the EMPTY_PROVIDER_RESPONSE
    # cases, all of which have a syntactically valid but empty/missing content). This was
    # previously unclassified (classify_error_class returned None for it).
    ("did not include textual content", ProviderErrorClass.MALFORMED_PROVIDER_RESPONSE),
    ("malformed provider response", ProviderErrorClass.MALFORMED_PROVIDER_RESPONSE),
    ("model_unavailable", ProviderErrorClass.MODEL_UNAVAILABLE),
    ("prompt_budget_exceeded", ProviderErrorClass.CONTEXT_LENGTH_EXCEEDED),
    ("context limit", ProviderErrorClass.CONTEXT_LENGTH_EXCEEDED),
    ("context_overflow", ProviderErrorClass.CONTEXT_LENGTH_EXCEEDED),
    ("rate limit", ProviderErrorClass.PROVIDER_RATE_LIMIT),
    ("rate_limited", ProviderErrorClass.PROVIDER_RATE_LIMIT),
    ("quota", ProviderErrorClass.PROVIDER_RATE_LIMIT),
    ("timeout", ProviderErrorClass.PROVIDER_TIMEOUT),
    ("timed out", ProviderErrorClass.PROVIDER_TIMEOUT),
    ("connection", ProviderErrorClass.PROVIDER_CONNECTION_ERROR),
    ("network", ProviderErrorClass.PROVIDER_CONNECTION_ERROR),
    ("dns", ProviderErrorClass.PROVIDER_CONNECTION_ERROR),
    ("circuit_open", ProviderErrorClass.PROVIDER_CONNECTION_ERROR),
    # A genuine positive signal for PROVIDER_INTERNAL_ERROR -- the provider itself reported a
    # server-side fault. Deliberately narrow and checked LAST: this class must never be reached
    # by falling off the end of the marker list (see classify_error_class's own final `return
    # None`), only by actually matching one of these.
    ("internal server error", ProviderErrorClass.PROVIDER_INTERNAL_ERROR),
    ("server error", ProviderErrorClass.PROVIDER_INTERNAL_ERROR),
    ("http_500", ProviderErrorClass.PROVIDER_INTERNAL_ERROR),
    ("http_502", ProviderErrorClass.PROVIDER_INTERNAL_ERROR),
    ("http_503", ProviderErrorClass.PROVIDER_INTERNAL_ERROR),
)


def _classify_by_exception_type(error: Exception) -> ProviderErrorClass | None:
    """Precise, message-independent classification for exception types this codebase raises or
    encounters directly. Checked BEFORE string-matching: a JSONDecodeError's message ("Expecting
    value: line 1 column 1 (char 0)") contains no recognizable marker at all, and a typed
    tool-call defect must classify the same way regardless of its exact wording. Lazy imports --
    this module is a shared, low-level seam and must not create an import-time coupling to the
    modules that raise these.
    """

    import json as _json

    from core.cloud_tool_call_contract import ToolCallParseError
    from core.execution_requirements import RequiredToolsNotOfferedError

    if isinstance(error, EmptyProviderResponseError):
        return ProviderErrorClass.EMPTY_PROVIDER_RESPONSE
    if isinstance(error, MalformedProviderResponseError):
        return ProviderErrorClass.MALFORMED_PROVIDER_RESPONSE
    if isinstance(error, RequiredToolsNotOfferedError):
        return ProviderErrorClass.REQUIRED_TOOLS_NOT_OFFERED
    if isinstance(error, ToolCallParseError):
        return ProviderErrorClass.MALFORMED_TOOL_CALL
    if isinstance(error, _json.JSONDecodeError):
        return ProviderErrorClass.MALFORMED_PROVIDER_RESPONSE
    if isinstance(error, TimeoutError):
        return ProviderErrorClass.PROVIDER_TIMEOUT
    if isinstance(error, ConnectionError):
        return ProviderErrorClass.PROVIDER_CONNECTION_ERROR
    try:
        import requests.exceptions as _requests_exceptions
    except ImportError:
        return None
    if isinstance(error, _requests_exceptions.Timeout):
        return ProviderErrorClass.PROVIDER_TIMEOUT
    if isinstance(error, _requests_exceptions.ConnectionError):
        return ProviderErrorClass.PROVIDER_CONNECTION_ERROR
    return None


def classify_error_class(error: str | Exception | None) -> ProviderErrorClass | None:
    """Best-effort classification from an error string or exception -- used where the raising
    site did not already attach a typed class. Returns None for a genuinely unclassifiable
    message rather than guessing PROVIDER_INTERNAL_ERROR: an arbitrary, unrecognized exception
    (a local bug, an exception type this function has never seen) is NOT evidence the provider
    itself had an internal fault, and `is_retryable(None)` is already False, so leaving it
    unclassified fails closed rather than inventing a specific, wrong, RETRYABLE reason to hand
    back to a caller that will act on it.
    """

    if error is None:
        return None
    if isinstance(error, Exception):
        type_class = _classify_by_exception_type(error)
        if type_class is not None:
            return type_class
    message = str(error).strip().lower()
    if not message:
        return None
    for marker, error_class in _ERROR_CLASS_MARKERS:
        if marker in message:
            return error_class
    return None


_PROMPT_TOKEN_KEYS = ("prompt_eval_count", "prompt_tokens", "input_tokens")
_OUTPUT_TOKEN_KEYS = ("eval_count", "completion_tokens", "output_tokens")


def _usage_tokens(usage: dict[str, Any], keys: tuple[str, ...]) -> tuple[int, bool]:
    """(value, reported) -- reported is True iff ANY key in `keys` carries a non-None value,
    which is what distinguishes "the provider told us 0" from "the provider told us nothing"
    (see core/usage_meter.py's prompt_tokens_reported/output_tokens_reported columns).

    `reported` is presence-based only -- a key carrying "not-a-number" still counts as reported,
    since the provider did send something for that field. SWITCHBOARD repair: the numeric
    conversion below used to run unguarded, so that exact shape (a non-numeric usage value) raised
    ValueError out of a function whose job is normalizing a response that already arrived
    successfully -- turning a malformed metering field into a crash of the entire response path.
    A value that cannot be parsed falls back to 0, same as an absent one; `reported` still tells a
    caller the field was not simply missing."""

    reported = any(usage.get(key) is not None for key in keys)
    raw = usage.get(keys[0]) or usage.get(keys[1]) or usage.get(keys[2]) or 0
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 0
    return value, reported


@dataclass(frozen=True)
class NormalizedProviderResult:
    """The one shape downstream routing, Activity, and usage accounting should read -- not
    `ModelResponse`, not `CloudModelResponse`, regardless of which adapter hierarchy answered."""

    requested_provider: str
    requested_model: str
    resolved_provider: str
    resolved_model: str
    actual_provider: str
    actual_model: str
    # The model identifier the PROVIDER'S OWN RESPONSE claims -- response-side evidence, never
    # copied from `requested_model`/`resolved_model`/`actual_model` (all three of which are
    # runtime-side facts: what was asked for, what the router picked, what the adapter was
    # configured to send). None when the provider's response carried no model identity at all;
    # that is a valid, honest outcome, not something to fill in with a guess. A mismatch between
    # `actual_model` and `provider_attested_model` is preserved as-is, not silently reconciled --
    # see adapters/openai_compatible_adapter.py, the only adapter this field is currently sourced
    # from (CloudProviderAdapter/CloudModelResponse -- System B -- carries no response-side model
    # identity of its own and always normalizes this to None; out of scope for this contract).
    # Legacy field name: a returned model label is a claim, not independent attestation.
    provider_attested_model: str | None
    substitution_authorized: bool
    text: str
    tool_calls: tuple[Any, ...]
    finish_reason: str
    usage_input: int
    usage_output: int
    usage_input_reported: bool
    usage_output_reported: bool
    latency: float
    provider_request_id: str
    error_class: ProviderErrorClass | None
    retryable: bool
    bounded_diagnostic: str
    raw_usage: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)


_DIAGNOSTIC_BOUND = 500


def _bounded(text: str) -> str:
    text = str(text or "")
    return text if len(text) <= _DIAGNOSTIC_BOUND else text[:_DIAGNOSTIC_BOUND] + "…"


def normalize_model_response(
    response: Any,
    *,
    requested_provider: str = "",
    requested_model: str = "",
    resolved_provider: str = "",
    resolved_model: str = "",
    substitution_authorized: bool = True,
    latency: float = 0.0,
    error: str | Exception | None = None,
) -> NormalizedProviderResult:
    """Normalize an `adapters.base_adapter.ModelResponse` (System A)."""

    usage = dict(getattr(response, "usage", None) or {})
    usage_input, input_reported = _usage_tokens(usage, _PROMPT_TOKEN_KEYS)
    usage_output, output_reported = _usage_tokens(usage, _OUTPUT_TOKEN_KEYS)
    actual_provider = str(getattr(response, "provider_id", "") or resolved_provider)
    actual_model = str(getattr(response, "model_name", "") or resolved_model)
    # Read directly off the response, never derived from actual_model/resolved_model/requested_
    # model: `ModelResponse.provider_attested_model` is only ever set by an adapter from genuine
    # response-body evidence (adapters/openai_compatible_adapter.py), or left None.
    raw_attested_model = getattr(response, "provider_attested_model", None)
    provider_attested_model = str(raw_attested_model) if raw_attested_model else None
    error_text = str(error) if error is not None else str(getattr(response, "error", "") or "")
    error_class = classify_error_class(error_text) if error_text else None
    return NormalizedProviderResult(
        requested_provider=requested_provider,
        requested_model=requested_model,
        resolved_provider=resolved_provider,
        resolved_model=resolved_model,
        actual_provider=actual_provider,
        actual_model=actual_model,
        provider_attested_model=provider_attested_model,
        substitution_authorized=substitution_authorized,
        text=str(getattr(response, "output_text", "") or ""),
        tool_calls=tuple(getattr(response, "tool_calls", ()) or ()),
        finish_reason=(
            "error"
            if error_text
            else str(getattr(response, "finish_reason", "") or "stop")
        ),
        usage_input=usage_input,
        usage_output=usage_output,
        usage_input_reported=input_reported,
        usage_output_reported=output_reported,
        latency=float(latency or 0.0),
        provider_request_id=str(getattr(response, "response_id", "") or getattr(response, "model_call_id", "") or ""),
        error_class=error_class,
        retryable=is_retryable(error_class),
        bounded_diagnostic=_bounded(error_text or str(getattr(response, "output_text", "") or "")),
        raw_usage=usage,
    )


def normalize_cloud_model_response(
    response: Any,
    *,
    requested_provider: str = "",
    requested_model: str = "",
    resolved_provider: str = "",
    resolved_model: str = "",
    actual_provider: str = "",
    actual_model: str = "",
    substitution_authorized: bool = True,
    latency: float = 0.0,
    provider_request_id: str = "",
    error: str | Exception | None = None,
) -> NormalizedProviderResult:
    """Normalize a `core.cloud_provider_contract.CloudModelResponse` (System B). Unlike
    `ModelResponse`, this type carries no provider/model identity of its own -- the caller (the
    broker, which already resolved the route) supplies it."""

    usage = dict(getattr(response, "usage", None) or {})
    usage_input, input_reported = _usage_tokens(usage, _PROMPT_TOKEN_KEYS)
    usage_output, output_reported = _usage_tokens(usage, _OUTPUT_TOKEN_KEYS)
    error_text = str(error) if error is not None else ""
    error_class = classify_error_class(error_text) if error_text else None
    return NormalizedProviderResult(
        requested_provider=requested_provider,
        requested_model=requested_model,
        resolved_provider=resolved_provider,
        resolved_model=resolved_model,
        actual_provider=actual_provider or resolved_provider,
        actual_model=actual_model or resolved_model,
        # System B (CloudProviderAdapter/CloudModelResponse) carries no response-side model
        # identity of its own -- out of scope for this contract (see the field's own docstring on
        # NormalizedProviderResult). Never fabricated from actual_model/resolved_model here.
        provider_attested_model=None,
        substitution_authorized=substitution_authorized,
        text=str(getattr(response, "output_text", "") or ""),
        tool_calls=tuple(getattr(response, "tool_calls", ()) or ()),
        finish_reason="error" if error_text else "stop",
        usage_input=usage_input,
        usage_output=usage_output,
        usage_input_reported=input_reported,
        usage_output_reported=output_reported,
        latency=float(latency or 0.0),
        provider_request_id=str(provider_request_id or usage.get("response_id") or ""),
        error_class=error_class,
        retryable=is_retryable(error_class),
        bounded_diagnostic=_bounded(error_text or str(getattr(response, "output_text", "") or "")),
        raw_usage=usage,
    )


__all__ = [
    "RETRYABLE_ERROR_CLASSES",
    "EmptyProviderResponseError",
    "MalformedProviderResponseError",
    "NormalizedProviderResult",
    "ProviderErrorClass",
    "classify_error_class",
    "is_retryable",
    "normalize_cloud_model_response",
    "normalize_model_response",
]
