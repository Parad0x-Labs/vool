from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from core.cloud_provider_contract import CloudToolCall, CloudToolDefinition
from core.cloud_tool_call_contract import (
    DuplicateToolCallError,
    MalformedToolArgumentsError,
    ToolCallParseError,
    UnknownToolNameError,
    parse_native_tool_calls,
)
from storage.model_provider_manifest import ModelProviderManifest
from storage.model_tool_certification_store import (
    CERTIFICATION_ROUTING_EFFECT,
    begin_certification_run,
    complete_certification_run,
    latest_certification_run,
    list_certification_runs,
)

PROBE_VERSION = "vool.local-tool-certification.v1"
PROBE_SCHEMA_VERSION = "vool.local-tool-certification.schema.v1"
ADAPTER_CONTRACT_VERSION = "openai-compatible-tool-certification.v1"
CERTIFICATION_STATES = frozenset(
    {"unknown", "probing", "verified", "degraded", "incompatible", "stale"}
)
_STAGE_NAMES = (
    "transport_acceptance",
    "model_emission",
    "adapter_translation",
    "result_continuation",
)
_SECRET_RE = re.compile(
    r"(?i)(bearer\s+)[a-z0-9._~+/=-]+|((?:api[_-]?key|token|secret|password)\s*[:=]\s*)\S+"
)
_HOME_PATH_RE = re.compile(r"(?:(?:/Users|/home)/[^/\s]+|[A-Za-z]:\\Users\\[^\\\s]+)")


class CertificationBoundaryError(ValueError):
    pass


@dataclass(frozen=True)
class StageOutcome:
    state: str
    latency_ms: float = 0.0
    failure_code: str = ""
    detail: str = ""
    checks: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["checks"] = list(self.checks)
        return payload


Exchange = Callable[..., dict[str, Any]]


ECHO_TOOL = CloudToolDefinition(
    intent="probe.echo",
    name="vool_probe_echo",
    description="Return a sealed diagnostic token. This tool has no external effects.",
    parameters={
        "type": "object",
        "properties": {
            "token": {
                "type": "string",
                "enum": ["repair-me", "recovered"],
            }
        },
        "required": ["token"],
        "additionalProperties": False,
    },
    strict=True,
)
ADD_TOOL = CloudToolDefinition(
    intent="probe.add",
    name="vool_probe_add",
    description="Add two integers inside a sealed diagnostic. This tool has no external effects.",
    parameters={
        "type": "object",
        "properties": {"left": {"type": "integer"}, "right": {"type": "integer"}},
        "required": ["left", "right"],
        "additionalProperties": False,
    },
    strict=True,
)
NONCE_TOOL = CloudToolDefinition(
    intent="probe.lookup_nonce",
    name="vool_probe_lookup_nonce",
    description="Resolve a probe-scoped nonce. This tool has no external effects or data access.",
    parameters={
        "type": "object",
        "properties": {"key": {"type": "string", "enum": ["alpha"]}},
        "required": ["key"],
        "additionalProperties": False,
    },
    strict=True,
)
PROBE_TOOLS = (ECHO_TOOL, ADD_TOOL, NONCE_TOOL)


#: The adapters whose transport this probe understands. ``LocalQwenProvider`` is the shipped
#: Ollama registration and a strict subclass of the OpenAI-compatible adapter.
_PROBEABLE_ADAPTERS = frozenset({"openai_compatible", "local_qwen_provider"})


def certification_applies(manifest: Any) -> bool:
    """Whether this model identity is one the probe can measure AT ALL.

    The same two conditions `run_local_model_tool_certification` enforces before it will run,
    named once so a caller asking "is this model certified?" and the runner deciding whether it
    may run cannot drift apart. A lane this returns False for can never hold a probe result, so
    "uncertified" would be a statement about the probe's reach rather than about the model --
    which is why `core.final_answer_authorship` reports ``not_applicable`` for those lanes
    instead of treating the absence as a failed measurement.
    """

    adapter_type = str(getattr(manifest, "adapter_type", None) or "openai_compatible")
    if adapter_type not in _PROBEABLE_ADAPTERS:
        return False
    runtime_config = getattr(manifest, "runtime_config", None) or {}
    try:
        base_url = str(runtime_config.get("base_url") or "").strip()
    except AttributeError:
        return False
    return _is_strict_loopback_url(base_url)


def normalized_base_identity(base_url: str) -> str:
    parsed = urlparse(str(base_url or "").strip())
    scheme = (parsed.scheme or "http").lower()
    host = (parsed.hostname or "").lower()
    port = parsed.port
    path = "/" + str(parsed.path or "").strip("/") if str(parsed.path or "").strip("/") else ""
    authority = f"{host}:{port}" if port is not None else host
    return f"{scheme}://{authority}{path}"


def certification_fingerprint_payload(
    manifest: ModelProviderManifest,
    *,
    backend_version: str = "",
    runtime_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = dict(manifest.metadata or {})
    runtime = dict(manifest.runtime_config or {})
    measured = dict(runtime_identity or {})
    template_hash = str(
        measured.get("template_hash")
        or metadata.get("chat_template_hash")
        or metadata.get("template_hash")
        or runtime.get("chat_template_hash")
        or ""
    ).strip()
    if not template_hash:
        template = metadata.get("chat_template") or runtime.get("chat_template")
        if template:
            template_hash = hashlib.sha256(str(template).encode("utf-8")).hexdigest()
    return {
        "adapter_type": str(manifest.adapter_type or "openai_compatible"),
        "adapter_version": ADAPTER_CONTRACT_VERSION,
        "backend_version": str(
            backend_version
            or measured.get("backend_version")
            or metadata.get("backend_version")
            or runtime.get("backend_version")
            or "unknown"
        ),
        "base_identity": normalized_base_identity(str(runtime.get("base_url") or "")),
        "model_name": str(manifest.model_name),
        "model_digest": str(
            measured.get("model_digest")
            or metadata.get("model_digest")
            or metadata.get("digest")
            or runtime.get("model_digest")
            or "unknown"
        ),
        "template_hash": template_hash or "unknown",
        "quantization": str(
            measured.get("quantization")
            or metadata.get("quantization")
            or runtime.get("quantization")
            or "unknown"
        ),
        "schema_version": PROBE_SCHEMA_VERSION,
        "probe_version": PROBE_VERSION,
    }


def certification_fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def certification_status(
    manifest: ModelProviderManifest,
    *,
    backend_version: str = "",
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    runtime_identity: dict[str, Any] = {}
    if not backend_version and str(manifest.adapter_type or "openai_compatible") in {
        "openai_compatible",
        "local_qwen_provider",
    }:
        # A cheap backend identity read is not a model/tool probe. Without it, a run fingerprinted
        # against Ollama 0.21 would be reported stale immediately because the status path compared
        # it to the literal string "unknown".
        try:
            from adapters.openai_compatible_adapter import OpenAICompatibleAdapter

            runtime_identity = OpenAICompatibleAdapter(manifest).tool_certification_runtime_identity()
            backend_version = str(runtime_identity.get("backend_version") or "unknown")
        except Exception:
            backend_version = "unknown"
    fingerprint_payload = certification_fingerprint_payload(
        manifest,
        backend_version=backend_version,
        runtime_identity=runtime_identity,
    )
    fingerprint = certification_fingerprint(fingerprint_payload)
    latest = latest_certification_run(
        provider_name=manifest.provider_name,
        model_name=manifest.model_name,
        db_path=db_path,
    )
    if latest is None:
        return {
            "state": "unknown",
            "provider_name": manifest.provider_name,
            "model_name": manifest.model_name,
            "fingerprint": fingerprint,
            "fingerprint_components": fingerprint_payload,
            "observe_only": False,
            "routing_effect": CERTIFICATION_ROUTING_EFFECT,
        }
    if latest.get("fingerprint") != fingerprint and latest.get("state") != "probing":
        latest = dict(latest)
        latest["state"] = "stale"
        latest["current_fingerprint"] = fingerprint
        latest["current_fingerprint_components"] = fingerprint_payload
    return latest


def certification_history(*, limit: int = 100, db_path: str | Path | None = None) -> list[dict[str, Any]]:
    return list_certification_runs(limit=limit, db_path=db_path)


def run_local_model_tool_certification(
    manifest: ModelProviderManifest,
    *,
    exchange: Exchange | None = None,
    backend_version: str = "",
    db_path: str | Path | None = None,
    timeout_seconds: float = 90.0,
) -> dict[str, Any]:
    """Run an explicit, sealed, observe-only diagnostic against one loopback model.

    No production tool catalog, workspace context, credentials, routing state, or user text enters
    the probe. The only executable operations are the three in-memory functions below.
    """

    adapter_type = str(manifest.adapter_type or "openai_compatible")
    # ``LocalQwenProvider`` is the shipped Ollama registration and is a strict subclass of the
    # OpenAI-compatible adapter. Refusing its manifest made the explicit probe unusable against the
    # app's real primary local model even though the transport/exchange boundary was identical.
    if adapter_type not in _PROBEABLE_ADAPTERS:
        raise CertificationBoundaryError("tool certification requires an OpenAI-compatible local adapter")
    base_url = str(manifest.runtime_config.get("base_url") or "").strip()
    if not _is_strict_loopback_url(base_url):
        raise CertificationBoundaryError("tool certification is restricted to a loopback model endpoint")
    # Both conditions above are exactly `certification_applies`; asserted here so the two can
    # never drift into disagreeing about which lanes are measurable.
    assert certification_applies(manifest)

    if exchange is None:
        from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
        from core.model_registry import ModelRegistry

        adapter = ModelRegistry().build_adapter(manifest)
        if not isinstance(adapter, OpenAICompatibleAdapter):
            raise CertificationBoundaryError(
                "tool certification requires an OpenAI-compatible local adapter"
            )
        runtime_identity = adapter.tool_certification_runtime_identity()
        backend_version = backend_version or str(runtime_identity.get("backend_version") or "unknown")
        exchange = adapter.tool_certification_exchange
    else:
        runtime_identity = {}

    fingerprint_payload = certification_fingerprint_payload(
        manifest,
        backend_version=backend_version,
        runtime_identity=runtime_identity,
    )
    fingerprint = certification_fingerprint(fingerprint_payload)
    run_id = f"tool-cert-{uuid.uuid4().hex}"
    begin_certification_run(
        run_id=run_id,
        fingerprint=fingerprint,
        fingerprint_payload=fingerprint_payload,
        provider_name=manifest.provider_name,
        model_name=manifest.model_name,
        adapter_type=str(manifest.adapter_type or "openai_compatible"),
        db_path=db_path,
    )

    started = time.perf_counter()
    stages = {name: StageOutcome(state="not_run").to_dict() for name in _STAGE_NAMES}
    evidence: dict[str, Any] = {"exchanges": [], "synthetic_tools": [tool.intent for tool in PROBE_TOOLS]}
    try:
        parallel_messages = [
            {
                "role": "system",
                "content": "This is a sealed local diagnostic. Call only the supplied synthetic tools.",
            },
            {
                "role": "user",
                "content": (
                    "Call both tools in the same response: add left=19 and right=23, and look up "
                    "nonce key alpha. Do not answer in prose yet."
                ),
            },
        ]
        first, first_calls, repair_checks = _exchange_until_valid_calls(
            exchange,
            messages=parallel_messages,
            tools=(ADD_TOOL, NONCE_TOOL),
            timeout_seconds=timeout_seconds,
            evidence=evidence,
        )
        transport_latency = sum(float(item.get("latency_ms") or 0) for item in evidence["exchanges"])
        stages["transport_acceptance"] = StageOutcome(
            state="passed", latency_ms=transport_latency, checks=("loopback_only", "tools_payload_accepted")
        ).to_dict()
        _assert_expected_parallel_calls(first_calls)
        stages["model_emission"] = StageOutcome(
            state="passed",
            checks=("parallel_calls", "unique_call_ids_or_signatures", *repair_checks),
        ).to_dict()
        stages["adapter_translation"] = StageOutcome(
            state="passed",
            checks=("canonical_names", "schema_valid_arguments", "duplicate_batch_refused"),
        ).to_dict()

        nonce = hashlib.sha256(f"{run_id}:alpha".encode()).hexdigest()[:12]
        tool_results = _sealed_dispatch(first_calls, nonce=nonce)
        continuation_messages = [
            *parallel_messages,
            _assistant_message(first),
            *[_tool_result_message(call, tool_results[call.call_id or call.name], dialect=str(first.get("dialect") or "")) for call in first_calls],
        ]
        continuation = exchange(
            messages=continuation_messages,
            tools=(),
            tool_choice=None,
            max_output_tokens=160,
            timeout_seconds=timeout_seconds,
        )
        evidence["exchanges"].append(_bounded_exchange_evidence("result_continuation", continuation))
        _assert_transport_accepted(continuation)
        content = _assistant_content(dict(continuation.get("body") or {}))
        if "42" not in content or nonce not in content:
            raise _StageFailureError("result_continuation", "wrong_continuation", "final answer lost a sealed tool result")

        recovery_checks = _run_recovery_case(
            exchange,
            timeout_seconds=timeout_seconds,
            evidence=evidence,
        )
        stages["result_continuation"] = StageOutcome(
            state="passed",
            checks=("same_model_tool_result_continuation", "sum_preserved", "nonce_preserved", *recovery_checks),
        ).to_dict()
        state = "verified"
        successful = True
    except _TransportRejectedError as exc:
        stages["transport_acceptance"] = StageOutcome(
            state="failed", failure_code=exc.code, detail=exc.safe_detail
        ).to_dict()
        state = "incompatible" if exc.status_code in {400, 422} else "degraded"
        successful = False
    except _StageFailureError as exc:
        if stages.get("transport_acceptance", {}).get("state") == "not_run":
            stages["transport_acceptance"] = StageOutcome(state="passed").to_dict()
        stages[exc.stage] = StageOutcome(
            state="failed", failure_code=exc.code, detail=exc.safe_detail
        ).to_dict()
        state = "degraded"
        successful = False
    except Exception as exc:
        code = "timeout" if "timeout" in type(exc).__name__.lower() or "timed out" in str(exc).lower() else "probe_error"
        pending_stage = next((name for name in _STAGE_NAMES if stages[name]["state"] == "not_run"), "transport_acceptance")
        stages[pending_stage] = StageOutcome(
            state="failed", failure_code=code, detail=_redact_text(str(exc))
        ).to_dict()
        state = "degraded"
        successful = False

    total_latency_ms = (time.perf_counter() - started) * 1000.0
    evidence["exchange_count"] = len(evidence["exchanges"])
    evidence["raw_payloads_persisted"] = False
    record = complete_certification_run(
        run_id=run_id,
        fingerprint=fingerprint,
        state=state,
        successful=successful,
        stages=stages,
        evidence=evidence,
        latency_ms=total_latency_ms,
        db_path=db_path,
    )
    # A new measured verdict supersedes any cached eligibility verdict for this model identity: the
    # final-answer authority decides the next call on THIS run instead of serving the one it replaced
    # for the rest of its cache lifetime (revision-5 review R2 follow-up). Invalidated after the record
    # is written, so a decision racing this call re-reads the new run.
    from core.final_answer_authorship import invalidate_author_certification

    invalidate_author_certification(manifest)
    return record


class _StageFailureError(RuntimeError):
    def __init__(self, stage: str, code: str, safe_detail: str):
        super().__init__(safe_detail)
        self.stage = stage
        self.code = code
        self.safe_detail = _redact_text(safe_detail)


class _TransportRejectedError(_StageFailureError):
    def __init__(self, status_code: int, code: str, safe_detail: str):
        super().__init__("transport_acceptance", code, safe_detail)
        self.status_code = int(status_code)


def _exchange_until_valid_calls(
    exchange: Exchange,
    *,
    messages: list[dict[str, Any]],
    tools: tuple[CloudToolDefinition, ...],
    timeout_seconds: float,
    evidence: dict[str, Any],
) -> tuple[dict[str, Any], tuple[CloudToolCall, ...], tuple[str, ...]]:
    response = exchange(
        messages=messages,
        tools=tools,
        tool_choice="required",
        max_output_tokens=384,
        timeout_seconds=timeout_seconds,
    )
    evidence["exchanges"].append(_bounded_exchange_evidence("model_emission", response))
    _assert_transport_accepted(response)
    checks: tuple[str, ...] = ()
    try:
        return response, _translated_calls(response, definitions=tools), checks
    except (ToolCallParseError, ValueError) as exc:
        # One bounded retry measures recovery without executing any malformed call. The malformed
        # provider envelope is never replayed as an assistant tool message.
        repaired = exchange(
            messages=[
                *messages,
                {
                    "role": "user",
                    "content": (
                        "Your previous synthetic tool call was rejected by schema validation "
                        f"({type(exc).__name__}). Emit the required calls once with valid arguments."
                    ),
                },
            ],
            tools=tools,
            tool_choice="required",
            max_output_tokens=384,
            timeout_seconds=timeout_seconds,
        )
        evidence["exchanges"].append(_bounded_exchange_evidence("invalid_call_recovery", repaired))
        _assert_transport_accepted(repaired)
        try:
            calls = _translated_calls(repaired, definitions=tools)
        except (ToolCallParseError, ValueError) as second:
            raise _StageFailureError("adapter_translation", _translation_failure_code(second), str(second)) from second
        return repaired, calls, ("invalid_call_recovered",)


def _run_recovery_case(
    exchange: Exchange,
    *,
    timeout_seconds: float,
    evidence: dict[str, Any],
) -> tuple[str, ...]:
    messages = [
        {"role": "system", "content": "This is a sealed local diagnostic. Use only probe.echo."},
        {"role": "user", "content": "Call probe.echo with token repair-me. Do not answer in prose."},
    ]
    initial = exchange(
        messages=messages,
        tools=(ECHO_TOOL,),
        tool_choice="required",
        max_output_tokens=256,
        timeout_seconds=timeout_seconds,
    )
    evidence["exchanges"].append(_bounded_exchange_evidence("recovery_error_setup", initial))
    _assert_transport_accepted(initial)
    calls = _translated_calls(initial, definitions=(ECHO_TOOL,), stage="result_continuation")
    if len(calls) != 1 or calls[0].arguments != {"token": "repair-me"}:
        raise _StageFailureError("result_continuation", "recovery_setup_wrong_call", "echo recovery setup was not followed")
    call = calls[0]
    error_message = _tool_result_message(
        call,
        {"ok": False, "error": "synthetic_validation_error", "required_token": "recovered"},
        dialect=str(initial.get("dialect") or ""),
    )
    retry = exchange(
        messages=[*messages, _assistant_message(initial), error_message],
        tools=(ECHO_TOOL,),
        tool_choice="required",
        max_output_tokens=256,
        timeout_seconds=timeout_seconds,
    )
    evidence["exchanges"].append(_bounded_exchange_evidence("recovery_retry", retry))
    _assert_transport_accepted(retry)
    # The recovery case owns its stage: a no-calls answer HERE is a result-continuation failure
    # (the model would not repair a rejected call), never a fresh emission verdict -- emission
    # already passed before this case runs.
    retried_calls = _translated_calls(retry, definitions=(ECHO_TOOL,), stage="result_continuation")
    if len(retried_calls) != 1 or retried_calls[0].arguments != {"token": "recovered"}:
        raise _StageFailureError("result_continuation", "invalid_call_not_recovered", "model did not repair a rejected synthetic call")
    return ("synthetic_error_recovered",)


def _translated_calls(
    response: dict[str, Any],
    *,
    definitions: tuple[CloudToolDefinition, ...],
    stage: str = "model_emission",
) -> tuple[CloudToolCall, ...]:
    raw_calls = _raw_tool_calls(dict(response.get("body") or {}))
    if not raw_calls:
        raise _StageFailureError(stage, "missing_tool_calls", "model emitted no tool calls")
    return parse_native_tool_calls(raw_calls, definitions=definitions)


def _raw_tool_calls(body: dict[str, Any]) -> Any:
    message = body.get("message")
    if isinstance(message, dict):
        return message.get("tool_calls")
    choices = body.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict):
            return message.get("tool_calls")
    return None


def _assistant_message(response: dict[str, Any]) -> dict[str, Any]:
    body = dict(response.get("body") or {})
    message = body.get("message")
    if not isinstance(message, dict):
        choices = body.get("choices")
        message = choices[0].get("message") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise _StageFailureError("adapter_translation", "missing_assistant_message", "provider response has no assistant message")
    safe = {"role": "assistant", "content": message.get("content") or ""}
    if isinstance(message.get("tool_calls"), list):
        safe["tool_calls"] = message["tool_calls"]
    return safe


def _assistant_content(body: dict[str, Any]) -> str:
    message = body.get("message")
    if not isinstance(message, dict):
        choices = body.get("choices")
        message = choices[0].get("message") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
    return str(message.get("content") or "") if isinstance(message, dict) else ""


def _tool_result_message(call: CloudToolCall, result: dict[str, Any], *, dialect: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "role": "tool",
        "content": json.dumps(result, sort_keys=True, separators=(",", ":")),
    }
    if call.call_id:
        payload["tool_call_id"] = call.call_id
    if dialect == "ollama":
        payload["tool_name"] = call.name
    else:
        payload["name"] = call.name
    return payload


def _sealed_dispatch(calls: tuple[CloudToolCall, ...], *, nonce: str) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for call in calls:
        key = call.call_id or f"{call.name}:{json.dumps(call.arguments, sort_keys=True)}"
        if key in seen:
            raise _StageFailureError("adapter_translation", "duplicate_dispatch_blocked", "duplicate synthetic call reached dispatch")
        seen.add(key)
        if call.intent == "probe.add":
            value = int(call.arguments["left"]) + int(call.arguments["right"])
            result = {"ok": True, "sum": value}
        elif call.intent == "probe.lookup_nonce":
            result = {"ok": True, "nonce": nonce}
        elif call.intent == "probe.echo":
            result = {"ok": True, "token": str(call.arguments["token"])}
        else:
            raise _StageFailureError("adapter_translation", "foreign_probe_tool", "non-probe tool reached sealed dispatch")
        results[call.call_id or call.name] = result
    return results


def _assert_expected_parallel_calls(calls: tuple[CloudToolCall, ...]) -> None:
    if len(calls) != 2:
        raise _StageFailureError("model_emission", "parallel_call_count", "expected exactly two parallel synthetic calls")
    by_intent = {call.intent: call for call in calls}
    if set(by_intent) != {"probe.add", "probe.lookup_nonce"}:
        raise _StageFailureError("model_emission", "wrong_parallel_tools", "parallel response selected the wrong synthetic tools")
    if by_intent["probe.add"].arguments != {"left": 19, "right": 23}:
        raise _StageFailureError("model_emission", "wrong_arguments", "add arguments did not match the diagnostic request")
    if by_intent["probe.lookup_nonce"].arguments != {"key": "alpha"}:
        raise _StageFailureError("model_emission", "wrong_arguments", "nonce arguments did not match the diagnostic request")


def _assert_transport_accepted(response: dict[str, Any]) -> None:
    status = int(response.get("status_code") or 0)
    if 200 <= status < 300:
        return
    if status in {400, 422}:
        raise _TransportRejectedError(status, "tools_payload_rejected", f"local endpoint rejected tools payload with HTTP {status}")
    raise _TransportRejectedError(status, "transport_error", f"local endpoint returned HTTP {status or 'unknown'}")


def _translation_failure_code(exc: Exception) -> str:
    if isinstance(exc, MalformedToolArgumentsError):
        return "malformed_arguments"
    if isinstance(exc, DuplicateToolCallError):
        return "duplicate_tool_call"
    if isinstance(exc, UnknownToolNameError):
        return "unknown_tool_name"
    return "schema_validation_failed"


def _bounded_exchange_evidence(label: str, response: dict[str, Any]) -> dict[str, Any]:
    body = response.get("body")
    body_hash = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()
    finish_reason = ""
    if isinstance(body, dict):
        choices = body.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            finish_reason = str(choices[0].get("finish_reason") or "")
        else:
            finish_reason = str(body.get("done_reason") or "")
    return {
        "label": label,
        "status_code": int(response.get("status_code") or 0),
        "latency_ms": max(0.0, float(response.get("latency_ms") or 0.0)),
        "dialect": str(response.get("dialect") or "unknown")[:32],
        "body_hash": body_hash,
        "finish_reason": _redact_text(finish_reason)[:64],
        "raw_body_persisted": False,
    }


def _redact_text(value: str) -> str:
    text = _SECRET_RE.sub(lambda match: f"{match.group(1) or match.group(2) or ''}[redacted]", str(value or ""))
    text = _HOME_PATH_RE.sub("[home]", text)
    return " ".join(text.split())[:240]


def _is_strict_loopback_url(base_url: str) -> bool:
    try:
        parsed = urlparse(str(base_url or "").strip())
        host = str(parsed.hostname or "").strip().lower()
        return parsed.scheme in {"http", "https"} and host in {"127.0.0.1", "localhost", "::1"}
    except (TypeError, ValueError):
        return False


__all__ = [
    "ADAPTER_CONTRACT_VERSION",
    "CERTIFICATION_ROUTING_EFFECT",
    "CERTIFICATION_STATES",
    "PROBE_SCHEMA_VERSION",
    "PROBE_TOOLS",
    "PROBE_VERSION",
    "CertificationBoundaryError",
    "certification_applies",
    "certification_fingerprint",
    "certification_fingerprint_payload",
    "certification_history",
    "certification_status",
    "normalized_base_identity",
    "run_local_model_tool_certification",
]
