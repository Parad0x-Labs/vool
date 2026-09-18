from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from adapters.cloud_provider_common import (
    classify_error,
    discovery_window,
    estimate_cost,
    normalized_capabilities,
    normalized_pricing,
    require_success,
)
from core.cloud_credential_broker import CloudCredentialBroker
from core.cloud_provider_contract import (
    CloudAccountLimits,
    CloudModelMetadata,
    CloudModelRequest,
    CloudModelResponse,
    CloudProviderAdapter,
    PolicyBoundTransport,
    ProviderError,
)
from core.provider_execution_boundary import invoke_provider_execution_boundary
from core.provider_invocation_gateway import seal_provider_invocation


class GenericOpenAICloudProvider(CloudProviderAdapter):
    def __init__(
        self,
        *,
        provider_id: str,
        base_url: str,
        credential_name: str,
        credential_env: str = "",
        credential_broker: CloudCredentialBroker | None = None,
        catalog_ttl_seconds: int = 3600,
    ) -> None:
        provider = str(provider_id or "").strip().lower()
        clean_base = str(base_url or "").rstrip("/")
        parsed = urlparse(clean_base)
        if not provider or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("generic cloud provider requires an id and absolute HTTPS base_url")
        self.provider_id = provider
        self.base_url = clean_base
        self.credential_name = str(credential_name or "").strip()
        self.credential_env = str(credential_env or "").strip()
        self._credentials = credential_broker or CloudCredentialBroker()
        self._catalog_ttl_seconds = max(60, int(catalog_ttl_seconds))

    def validate_credentials(self, transport: PolicyBoundTransport) -> tuple[bool, str]:
        try:
            status, headers, payload = transport.request_json(
                method="GET",
                url=f"{self.base_url}/models",
                credential_name=self.credential_name,
                credential_env=self.credential_env,
            )
            require_success(status, headers, payload)
            return True, "ok"
        except Exception as exc:
            return False, self.classify_provider_error(exc).kind.value

    def discover_models(self, transport: PolicyBoundTransport) -> tuple[CloudModelMetadata, ...]:
        status, headers, payload = transport.request_json(
            method="GET",
            url=f"{self.base_url}/models",
            credential_name=self.credential_name,
            credential_env=self.credential_env,
        )
        require_success(status, headers, payload)
        discovered_at, _ = discovery_window(ttl_seconds=self._catalog_ttl_seconds)
        return tuple(
            model
            for item in list((payload or {}).get("data") or [])
            if isinstance(item, dict)
            for model in [self.normalize_model_metadata(item, discovered_at=discovered_at)]
            if model is not None
        )

    def normalize_model_metadata(self, payload: dict[str, Any], *, discovered_at: str) -> CloudModelMetadata | None:
        model_id = str(payload.get("id") or "").strip()
        if not model_id:
            return None
        _, expires_at = discovery_window(ttl_seconds=self._catalog_ttl_seconds)
        state, input_price, output_price, request_price = normalized_pricing(payload)
        return CloudModelMetadata(
            provider_id=self.provider_id,
            model_id=model_id,
            display_name=str(payload.get("name") or model_id),
            pricing_state=state,
            input_usd_per_token=input_price,
            output_usd_per_token=output_price,
            request_usd=request_price,
            context_window=max(0, int(payload.get("context_length") or 0)),
            max_output_tokens=max(0, int(payload.get("max_output_tokens") or 0)),
            capabilities=normalized_capabilities(payload),
            data_policy={"provider": self.provider_id, "source": "provider_catalog", "custom_endpoint": True},
            discovered_at=discovered_at,
            expires_at=expires_at,
            health_state="ready",
        )

    def get_account_limits(self, transport: PolicyBoundTransport) -> CloudAccountLimits:
        return CloudAccountLimits(metadata={"status": "not_reported"})

    def estimate_request_cost(self, model: CloudModelMetadata, *, input_tokens: int, output_tokens: int) -> float | None:
        return estimate_cost(model, input_tokens=input_tokens, output_tokens=output_tokens)

    def check_model_health(self, transport: PolicyBoundTransport, model: CloudModelMetadata) -> dict[str, Any]:
        models = invoke_provider_execution_boundary(self, "discover_models", transport)
        return {"ok": any(item.model_id == model.model_id for item in models)}

    def send_request(self, transport: PolicyBoundTransport, request: CloudModelRequest) -> CloudModelResponse:
        if request.tools:
            # Same gap as CloudflareWorkersAIProvider: this adapter's `body` below never carries a
            # `tools`/`tool_choice` field and its response parsing never reads `tool_calls` -- a
            # declarative BYOK endpoint registered through this class silently answers a
            # tool-required turn with ungrounded prose instead. Fail closed instead of pretending
            # the tool contract was honored; `classify_error` maps this message to
            # ProviderErrorKind.TOOL_UNSUPPORTED so the broker can route elsewhere.
            raise RuntimeError(
                f"{self.provider_id} does not support required tools: this adapter cannot carry a tool catalog"
            )
        # F1-B ADAPTER-IS-PROJECTION: the canonical egress gate projects the
        # payload at this cloud boundary — LOCAL_ONLY/SECRET segments are
        # dropped, unclassified segment stamps are refused (fail closed).
        from core.egress_gate import project_messages_for_destination

        wire_messages = project_messages_for_destination(
            list(request.messages), destination_class="cloud_provider"
        )
        body: dict[str, Any] = {
            "model": request.model_id,
            "messages": wire_messages,
            "max_tokens": request.max_output_tokens,
            "stream": False,
        }
        if request.temperature is not None:
            body["temperature"] = request.temperature
        permit = seal_provider_invocation(
            request=request,
            provider_id=self.provider_id,
            model_id=request.model_id,
            operation="chat",
            payload=body,
            header_names=("Authorization", "Content-Type"),
        )
        status, headers, payload = transport.request_json(
            method="POST",
            url=f"{self.base_url}/chat/completions",
            body=permit.consume(),
            credential_name=self.credential_name,
            credential_env=self.credential_env,
            timeout_seconds=180.0,
        )
        require_success(status, headers, payload)
        choices = list((payload or {}).get("choices") or [])
        message = dict(choices[0].get("message") or {}) if choices and isinstance(choices[0], dict) else {}
        content = str(message.get("content") or "")
        if not content.strip():
            # An HTTP 200 with empty/absent `choices` or an empty message must not read as a
            # normal, empty-but-valid answer -- that is indistinguishable downstream from "the
            # model said nothing" and passes contract validation silently. classify_error reads
            # "malformed"/"json" in this message into ProviderErrorKind.MALFORMED_RESPONSE.
            raise RuntimeError("malformed provider response: no content in any choice")
        return CloudModelResponse(content, self.parse_usage(payload), payload)

    def parse_usage(self, payload: Any) -> dict[str, Any]:
        return dict(payload.get("usage") or {}) if isinstance(payload, dict) else {}

    def classify_provider_error(self, error: Any) -> ProviderError:
        return classify_error(error)

    def revoke_or_clear_session_credentials(self) -> None:
        self._credentials.clear_session_credential(self.credential_name)


__all__ = ["GenericOpenAICloudProvider"]
