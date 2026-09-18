from __future__ import annotations

from typing import Any

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


class CloudflareWorkersAIProvider(CloudProviderAdapter):
    provider_id = "cloudflare-workers-ai"
    api_host = "api.cloudflare.com"
    credential_name = "llm.cloud.cloudflare"
    credential_env = "CLOUDFLARE_API_TOKEN"

    def __init__(
        self,
        *,
        account_id: str,
        credential_broker: CloudCredentialBroker | None = None,
        catalog_ttl_seconds: int = 3600,
    ) -> None:
        self.account_id = str(account_id or "").strip()
        if not self.account_id or not all(char.isalnum() or char in {"-", "_"} for char in self.account_id):
            raise ValueError("Cloudflare account_id is required")
        self.base_url = f"https://{self.api_host}/client/v4/accounts/{self.account_id}/ai"
        self._credentials = credential_broker or CloudCredentialBroker()
        self._catalog_ttl_seconds = max(60, int(catalog_ttl_seconds))

    def validate_credentials(self, transport: PolicyBoundTransport) -> tuple[bool, str]:
        try:
            status, headers, payload = transport.request_json(
                method="GET",
                url=f"{self.base_url}/models/search",
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
            url=f"{self.base_url}/models/search",
            credential_name=self.credential_name,
            credential_env=self.credential_env,
        )
        require_success(status, headers, payload)
        result = (payload or {}).get("result") or []
        items = list(result.get("models") or []) if isinstance(result, dict) else list(result)
        discovered_at, _ = discovery_window(ttl_seconds=self._catalog_ttl_seconds)
        return tuple(
            model
            for item in items
            if isinstance(item, dict)
            for model in [self.normalize_model_metadata(item, discovered_at=discovered_at)]
            if model is not None
        )

    def normalize_model_metadata(self, payload: dict[str, Any], *, discovered_at: str) -> CloudModelMetadata | None:
        model_id = str(payload.get("name") or payload.get("id") or "").strip()
        if not model_id:
            return None
        _, expires_at = discovery_window(ttl_seconds=self._catalog_ttl_seconds)
        state, input_price, output_price, request_price = normalized_pricing(payload)
        task = dict(payload.get("task") or {})
        normalized = dict(payload)
        normalized["supported_parameters"] = list(payload.get("supported_parameters") or task.get("parameters") or [])
        return CloudModelMetadata(
            provider_id=self.provider_id,
            model_id=model_id,
            display_name=str(payload.get("description") or model_id),
            pricing_state=state,
            input_usd_per_token=input_price,
            output_usd_per_token=output_price,
            request_usd=request_price,
            context_window=max(0, int(payload.get("context_length") or payload.get("context_window") or 0)),
            max_output_tokens=max(0, int(payload.get("max_output_tokens") or 0)),
            capabilities=normalized_capabilities(normalized),
            data_policy={"provider": "cloudflare", "source": "provider_catalog"},
            discovered_at=discovered_at,
            expires_at=expires_at,
            health_state="ready",
        )

    def get_account_limits(self, transport: PolicyBoundTransport) -> CloudAccountLimits:
        return CloudAccountLimits(metadata={"status": "not_reported_by_catalog"})

    def estimate_request_cost(self, model: CloudModelMetadata, *, input_tokens: int, output_tokens: int) -> float | None:
        return estimate_cost(model, input_tokens=input_tokens, output_tokens=output_tokens)

    def check_model_health(self, transport: PolicyBoundTransport, model: CloudModelMetadata) -> dict[str, Any]:
        models = invoke_provider_execution_boundary(self, "discover_models", transport)
        return {"ok": any(item.model_id == model.model_id for item in models)}

    def send_request(self, transport: PolicyBoundTransport, request: CloudModelRequest) -> CloudModelResponse:
        if request.tools:
            # This adapter speaks Cloudflare Workers AI's raw `/run/{model}` shape, which this
            # class never learned to carry a `tools` payload in or read `tool_calls` back out of
            # (see `body` below: no `tools` key is ever set). Silently dropping the catalog and
            # sending a toolless request would let a tool-required turn "succeed" as an ungrounded
            # prose answer with nothing in the response shape to say tool-calling never happened.
            # `classify_error` reads this exact phrasing ("tool" + "not support") into
            # ProviderErrorKind.TOOL_UNSUPPORTED, so the broker fails this call closed and lets
            # ranking route to a lane that can actually satisfy the tool contract.
            raise RuntimeError(
                f"{self.provider_id} does not support required tools: this adapter cannot carry a tool catalog"
            )
        body: dict[str, Any] = {
            "messages": list(request.messages),
            "max_tokens": request.max_output_tokens,
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
            url=f"{self.base_url}/run/{request.model_id}",
            body=permit.consume(),
            credential_name=self.credential_name,
            credential_env=self.credential_env,
            timeout_seconds=180.0,
        )
        require_success(status, headers, payload)
        result = dict((payload or {}).get("result") or {})
        output = str(result.get("response") or result.get("result") or "").strip()
        if not output:
            # An HTTP 200 with an empty/absent `result.response` must not read as a normal, empty
            # answer -- that is indistinguishable downstream from "the model said nothing" and
            # passes contract validation silently. classify_error reads "malformed"/"json" in this
            # message into ProviderErrorKind.MALFORMED_RESPONSE.
            raise RuntimeError("malformed provider response: no content in result")
        return CloudModelResponse(output, self.parse_usage(payload), payload)

    def parse_usage(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        result = dict(payload.get("result") or {})
        return dict(result.get("usage") or payload.get("usage") or {})

    def classify_provider_error(self, error: Any) -> ProviderError:
        return classify_error(error)

    def revoke_or_clear_session_credentials(self) -> None:
        self._credentials.clear_session_credential(self.credential_name)


__all__ = ["CloudflareWorkersAIProvider"]
