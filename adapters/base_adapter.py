from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from core.provider_execution_boundary import (
    ProviderExecutionBoundaryMeta,
    inherit_provider_execution_boundaries,
    invoke_provider_execution_boundary,
    observe_provider_execution_boundary,
    provider_execution_boundary,
)
from storage.model_provider_manifest import ModelProviderManifest


@dataclass
class ModelRequest:
    task_kind: str
    prompt: str
    system_prompt: str | None = None
    context: dict[str, Any] = field(default_factory=dict)
    temperature: float | None = None
    max_output_tokens: int | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    output_mode: str = "plain_text"
    # "auto" | "disabled" | "required" — see core/model_request_policy.py. A request-level contract
    # rather than another per-task metadata flag, so a bounded artifact call declares "do not spend
    # my output budget reasoning" once and every adapter reads the same field.
    reasoning_mode: str = "auto"
    trace_id: str | None = None
    contract: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    attachments: list[dict[str, Any]] = field(default_factory=list)
    tools: tuple[Any, ...] = ()
    tool_choice: str | dict[str, Any] | None = None
    # True when the turn's own contract (ExecutionRequirements.tools_required, surfaced to this
    # router as output_mode=="tool_intent") demands a real tool call -- not merely that `tools` is
    # non-empty. Read at the final invocation boundary, inside the adapter, right before the wire
    # payload is sent: ranking already checked the MANIFEST claims tool support, this checks the
    # ACTUAL built envelope still carries it. See core/execution_requirements.py.
    tools_required: bool = False
    model_call_id: str = ""
    response_id: str = ""
    cancel_check: Callable[[], bool] | None = field(default=None, repr=False, compare=False)
    # Some model calls produce disposable routing artifacts rather than user-visible answers.
    # Retrying a truncated planner artifact as prose can cost more than the final answer and still
    # cannot make an invalid plan authoritative. Those callers fail open through their ordinary
    # lane instead. Answer-producing calls keep the existing one-repair default.
    allow_response_control_retry: bool = True
    # Transport/structural provider retries are a separate budget. Disposable auxiliary calls
    # may permit neither kind; ordinary structured answer calls retain the existing default.
    allow_provider_retry: bool = True

    def is_cancelled(self) -> bool:
        return bool(self.cancel_check and self.cancel_check())


@dataclass
class ModelResponse:
    output_text: str
    confidence: float = 0.5
    raw_response: Any = None
    usage: dict[str, Any] = field(default_factory=dict)
    structured_output: Any = None
    provider_id: str = ""
    model_name: str = ""
    output_mode: str = "plain_text"
    error: str | None = None
    model_call_id: str = ""
    response_id: str = ""
    constraint_result: dict[str, Any] = field(default_factory=dict)
    # Schema-validated provider-native calls. The normalized text still carries the first
    # call for existing single-step consumers; the unified executor uses this tuple to retain
    # safe read-only batch members instead of silently discarding them.
    tool_calls: tuple[Any, ...] = ()
    # The model identifier the PROVIDER'S OWN RESPONSE BODY claims (e.g. an OpenAI-compatible
    # chat completion's top-level "model" field, or Ollama's /api/chat "model" field) -- distinct
    # from `model_name` above, which is always the runtime's own selection echoed back
    # (self.manifest.model_name), never independent response evidence. None when the provider's
    # response carried no model identity at all; never fabricated from `model_name` or any other
    # request-side value. See core/normalized_provider_result.py for the three-way provenance
    # contract this feeds (requested / runtime-selected / provider-attested).
    provider_attested_model: str | None = None
    # Provider-authored completion terminator (for example OpenAI ``finish_reason`` or Ollama
    # ``done_reason``). Empty when the provider did not report one; never inferred from usage or
    # request budgets here. Response control combines this independent evidence with token counts
    # and output shape before deciding whether an answer was cut off.
    finish_reason: str = ""
    # The actual completion ceiling sealed into the provider payload. This may differ from the
    # caller's answer budget when an adapter adds a reasoning reserve. Response control must compare
    # provider-reported usage to this wire value, not to the pre-adapter request value.
    effective_max_output_tokens: int | None = None
    # Provider evidence the adapter read from the RESPONSE and from its own pre-dispatch authorization
    # (UsePod: the route that served, the price bound the call was held to, usage-bounded cost). Never
    # secrets and never prompt content. Empty for adapters that record none; receipts read it as-is.
    provider_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelStreamChunk:
    delta_text: str
    raw_event: Any = None
    done: bool = False
    # Usage totals parsed from the provider's terminal stream chunk (OpenAI/OpenRouter final
    # ``usage`` block, or Ollama's ``done`` frame). Carried on the final chunk so a streamed turn
    # can be metered like a buffered one; None when the provider sent no usage.
    usage: dict[str, Any] | None = None
    # Same contract as ModelResponse.provider_attested_model above. A stream's frames typically
    # carry the provider's model identity only on the first frame (or every frame, identically) --
    # captured once the first time it is seen and carried on every chunk yielded from then on,
    # including the terminal one. None on every chunk if the provider never sent one.
    provider_attested_model: str | None = None
    # Same provider-authored terminator contract as ModelResponse.finish_reason. Streaming
    # adapters carry the last non-empty value onto the terminal chunk so stream assembly cannot
    # silently turn ``length`` into an assumed success.
    finish_reason: str = ""
    # Same contract as ModelResponse.provider_metadata. Carried on the terminal chunk only.
    provider_metadata: dict[str, Any] | None = None


class ModelAdapter(ABC, metaclass=ProviderExecutionBoundaryMeta):
    def __getattribute__(self, name: str) -> Any:
        active = super().__getattribute__(name)
        return observe_provider_execution_boundary(self, name, active)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        inherit_provider_execution_boundaries(cls)

    def __init__(self, manifest: ModelProviderManifest) -> None:
        self.manifest = manifest

    def validate_runtime(self) -> list[str]:
        return []

    @provider_execution_boundary
    def health_check(self) -> dict[str, Any]:
        return {"ok": True, "provider_id": self.manifest.provider_id}

    @provider_execution_boundary
    def prewarm(self) -> dict[str, Any]:
        return {
            "ok": True,
            "provider_id": self.manifest.provider_id,
            "status": "skipped",
            "reason": "not_supported",
        }

    def list_capabilities(self) -> list[str]:
        return list(self.manifest.capabilities)

    def supports_streaming(self) -> bool:
        return False

    def estimate_cost_class(self) -> str:
        base_url = str(self.manifest.runtime_config.get("base_url") or "")
        if self.manifest.adapter_type == "cloud_fallback_provider":
            return "paid_cloud"
        if self.manifest.source_type in {"local_path", "subprocess"}:
            return "free_local"
        if base_url.startswith("http://127.0.0.1") or base_url.startswith("http://localhost"):
            return "free_local"
        return "remote_unknown"

    def get_license_metadata(self) -> dict[str, Any]:
        return {
            "provider_name": self.manifest.provider_name,
            "model_name": self.manifest.model_name,
            "license_name": self.manifest.license_name,
            "license_reference": self.manifest.resolved_license_reference,
            "weights_bundled": self.manifest.weights_are_bundled,
            "redistribution_allowed": self.manifest.redistribution_allowed,
            "runtime_dependency": self.manifest.runtime_dependency,
        }

    @provider_execution_boundary
    def run_text_task(self, request: ModelRequest) -> ModelResponse:
        return invoke_provider_execution_boundary(self, "invoke", request)

    @provider_execution_boundary
    def run_structured_task(self, request: ModelRequest) -> ModelResponse:
        return invoke_provider_execution_boundary(self, "invoke", request)

    @provider_execution_boundary
    def stream_text_task(self, request: ModelRequest) -> Iterable[ModelStreamChunk]:
        response = invoke_provider_execution_boundary(self, "run_text_task", request)
        yield ModelStreamChunk(delta_text=response.output_text, raw_event=response.raw_response, done=True)

    @abstractmethod
    @provider_execution_boundary
    def invoke(self, request: ModelRequest) -> ModelResponse:
        raise NotImplementedError
