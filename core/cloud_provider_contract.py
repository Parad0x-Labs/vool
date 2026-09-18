from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from core.enum_compat import StrEnum
from core.provider_execution_boundary import (
    ProviderExecutionBoundaryMeta,
    inherit_provider_execution_boundaries,
    observe_provider_execution_boundary,
    provider_execution_boundary,
)


class PricingState(StrEnum):
    FREE = "free"
    PROMOTIONAL = "promotional"
    PAID = "paid"
    UNKNOWN = "unknown"


class PrivacyClass(StrEnum):
    PUBLIC = "public"
    USER_APPROVED = "user-approved"
    PRIVATE_FILES = "private-files"
    SOURCE_CODE = "source-code"
    PERSISTENT_MEMORY = "persistent-memory"
    SECRETS = "secrets"
    WALLET = "wallet"
    PERSONAL = "personal"
    UNKNOWN = "unknown"


class ProviderErrorKind(StrEnum):
    AUTH = "auth"
    MODEL_REMOVED = "model_removed"
    TEMPORARY = "temporary"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXHAUSTED = "quota_exhausted"
    MALFORMED_RESPONSE = "malformed_response"
    CONTEXT_OVERFLOW = "context_overflow"
    TOOL_UNSUPPORTED = "tool_unsupported"
    NETWORK = "network"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CloudAccountLimits:
    quota_remaining: float | None = None
    rate_limit_remaining: int | None = None
    resets_at: str = ""
    promotional_credit_usd: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def text_only_output(output_modalities: Any) -> bool:
    """The ONE reading of "a text generation model": every declared output modality is text.

    A catalog row that declares no output modalities is a legacy text row and counts as text. A
    row that also emits audio or images (Lyria: "text+image->text+audio") is a generation model
    of another kind, whatever else it lists -- measured live, the free-cloud broker picked
    `google/lyria-3-clip-preview` for a chat turn because "text" appeared in its list.
    """
    modalities = [str(item).strip().lower() for item in list(output_modalities or []) if str(item).strip()]
    return not modalities or all(item == "text" for item in modalities)


def is_text_generation_model(model: CloudModelMetadata) -> bool:
    architecture = dict((model.metadata or {}).get("architecture") or {}) if isinstance(model.metadata, dict) else {}
    return text_only_output(architecture.get("output_modalities"))


@dataclass(frozen=True)
class CloudModelMetadata:
    provider_id: str
    model_id: str
    display_name: str
    pricing_state: PricingState = PricingState.UNKNOWN
    input_usd_per_token: float | None = None
    output_usd_per_token: float | None = None
    request_usd: float | None = None
    context_window: int = 0
    max_output_tokens: int = 0
    capabilities: tuple[str, ...] = ()
    data_policy: dict[str, Any] = field(default_factory=dict)
    discovered_at: str = ""
    expires_at: str = ""
    health_state: str = "unknown"
    latency_ms: float | None = None
    failure_rate: float | None = None
    quota_remaining: float | None = None
    rate_limit_remaining: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def catalog_key(self) -> str:
        return f"{self.provider_id}:{self.model_id}"

    @property
    def verified_zero_price(self) -> bool:
        # The per-token pair is the load-bearing evidence and must be published and zero. A
        # per-request fee is optional: OpenRouter publishes it for no model in the live catalog
        # (0 of 338 measured), so demanding it verifies nothing as free. Absent means no
        # surcharge; published means it must also be zero.
        per_token = (self.input_usd_per_token, self.output_usd_per_token)
        if any(value is None or float(value) != 0.0 for value in per_token):
            return False
        if self.request_usd is not None and float(self.request_usd) != 0.0:
            return False
        return self.pricing_state in {PricingState.FREE, PricingState.PROMOTIONAL}

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["pricing_state"] = self.pricing_state.value
        payload["capabilities"] = list(self.capabilities)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> CloudModelMetadata:
        data = dict(payload or {})
        try:
            data["pricing_state"] = PricingState(str(data.get("pricing_state") or PricingState.UNKNOWN))
        except ValueError:
            data["pricing_state"] = PricingState.UNKNOWN
        data["capabilities"] = tuple(
            dict.fromkeys(str(item).strip().lower() for item in list(data.get("capabilities") or []) if str(item).strip())
        )
        return cls(**data)


@dataclass(frozen=True)
class CloudTaskRequirements:
    min_context_tokens: int = 0
    expected_output_tokens: int = 0
    required_capabilities: tuple[str, ...] = ()
    privacy_class: PrivacyClass = PrivacyClass.UNKNOWN
    data_categories: tuple[str, ...] = ()
    approved_paths: tuple[str, ...] = ()
    prefer_low_latency: bool = False


@dataclass(frozen=True)
class CloudToolDefinition:
    """Provider-neutral native tool definition bound to one runtime intent."""

    intent: str
    name: str
    description: str
    parameters: dict[str, Any]
    strict: bool = True


@dataclass(frozen=True)
class CloudToolCall:
    """Validated native provider tool call resolved back to a runtime intent."""

    call_id: str
    intent: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class CloudModelRequest:
    task_id: str
    turn_id: str
    subtask_id: str
    model_call_id: str
    model_id: str
    messages: tuple[dict[str, Any], ...]
    max_output_tokens: int
    temperature: float | None = None
    stream: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    tools: tuple[CloudToolDefinition, ...] = ()
    tool_choice: str | dict[str, Any] | None = None
    # Same meaning as ModelRequest.tools_required (adapters/base_adapter.py): the turn's own
    # contract demands a real tool call, checked at the final invocation boundary against the
    # ACTUAL built envelope, not just against what ranking assumed the manifest could do.
    tools_required: bool = False


@dataclass(frozen=True)
class CloudModelResponse:
    output_text: str
    usage: dict[str, Any] = field(default_factory=dict)
    raw_response: Any = None
    tool_calls: tuple[CloudToolCall, ...] = ()
    #: The provider's own termination signal for the choice ``output_text`` came from
    #: (``"stop"``, ``"length"``, ``"tool_calls"`` ...), lower-cased; ``""`` when the adapter did
    #: not read one. ``"length"`` means the text ended AT ``max_output_tokens`` and is not a
    #: finished answer -- measured live 2026-09-06: a reasoning model handed 520 tokens spent all
    #: of them thinking and returned its half-written monologue as ``content``. The router's
    #: validator refuses a ``"length"`` completion instead of shipping that draft.
    finish_reason: str = ""


@dataclass(frozen=True)
class ProviderError:
    kind: ProviderErrorKind
    safe_message: str
    retry_after_seconds: float | None = None
    billing_ambiguous: bool = False


class PolicyBoundTransport(Protocol):
    def request_json(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        credential_name: str = "",
        credential_env: str = "",
        credential_scheme: str = "bearer",
        timeout_seconds: float = 30.0,
    ) -> tuple[int, dict[str, str], Any]: ...


class CloudProviderAdapter(ABC, metaclass=ProviderExecutionBoundaryMeta):
    provider_id: str

    def __getattribute__(self, name: str) -> Any:
        active = super().__getattribute__(name)
        return observe_provider_execution_boundary(self, name, active)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        inherit_provider_execution_boundaries(cls)

    @abstractmethod
    @provider_execution_boundary
    def validate_credentials(self, transport: PolicyBoundTransport) -> tuple[bool, str]:
        raise NotImplementedError

    @abstractmethod
    @provider_execution_boundary
    def discover_models(self, transport: PolicyBoundTransport) -> tuple[CloudModelMetadata, ...]:
        raise NotImplementedError

    @abstractmethod
    def normalize_model_metadata(self, payload: dict[str, Any], *, discovered_at: str) -> CloudModelMetadata | None:
        raise NotImplementedError

    @abstractmethod
    @provider_execution_boundary
    def get_account_limits(self, transport: PolicyBoundTransport) -> CloudAccountLimits:
        raise NotImplementedError

    @abstractmethod
    def estimate_request_cost(
        self,
        model: CloudModelMetadata,
        *,
        input_tokens: int,
        output_tokens: int,
    ) -> float | None:
        raise NotImplementedError

    @abstractmethod
    @provider_execution_boundary
    def check_model_health(self, transport: PolicyBoundTransport, model: CloudModelMetadata) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    @provider_execution_boundary
    def send_request(self, transport: PolicyBoundTransport, request: CloudModelRequest) -> CloudModelResponse:
        raise NotImplementedError

    @abstractmethod
    def parse_usage(self, payload: Any) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def classify_provider_error(self, error: Any) -> ProviderError:
        raise NotImplementedError

    @abstractmethod
    def revoke_or_clear_session_credentials(self) -> None:
        raise NotImplementedError


def normalized_price(value: Any) -> float | None:
    """Parse a provider price without turning missing/malformed data into a free price."""
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return None
    return parsed


def pricing_state_for_prices(
    input_usd_per_token: float | None,
    output_usd_per_token: float | None,
    request_usd: float | None,
    *,
    promotional: bool = False,
) -> PricingState:
    # An unpublished per-token rate is a genuinely unreadable price and stays UNKNOWN, which the
    # router rejects outright. An unpublished per-request fee is not: OpenRouter publishes one for
    # no model at all, so requiring it classed the whole live catalog UNKNOWN (measured 338 of 338,
    # including every ":free" id) and no free route could ever be selected. Absent = no surcharge,
    # read that way only once the per-token pair is present.
    if input_usd_per_token is None or output_usd_per_token is None:
        return PricingState.UNKNOWN
    prices = (input_usd_per_token, output_usd_per_token, request_usd)
    if all(float(value) == 0.0 for value in prices if value is not None):
        return PricingState.PROMOTIONAL if promotional else PricingState.FREE
    return PricingState.PAID


__all__ = [
    "CloudAccountLimits",
    "CloudModelMetadata",
    "CloudModelRequest",
    "CloudModelResponse",
    "CloudProviderAdapter",
    "CloudTaskRequirements",
    "CloudToolCall",
    "CloudToolDefinition",
    "PolicyBoundTransport",
    "PricingState",
    "PrivacyClass",
    "ProviderError",
    "ProviderErrorKind",
    "normalized_price",
    "pricing_state_for_prices",
]
