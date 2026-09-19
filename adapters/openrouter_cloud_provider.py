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
from core.cloud_providers import credential_env_for, key_env_names, slot_for
from core.cloud_tool_call_contract import (
    canonical_tool_call_text,
    openai_tool_payload,
    parse_native_tool_calls,
)
from core.execution_requirements import assert_envelope_carries_tools
from core.provider_execution_boundary import invoke_provider_execution_boundary
from core.provider_invocation_gateway import seal_provider_invocation


def _content_is_the_reasoning(content: str, reasoning: str) -> bool:
    """True when ``content`` is the model's reasoning rather than an answer.

    Byte-equal after whitespace folding, or -- for a completion cut off mid-thought -- one is a
    prefix of the other and the shorter is long enough (200+ chars) that a coincidental prefix is
    not plausible. A short answer that merely opens with the same words as the reasoning is NOT
    the reasoning and is returned as the answer.
    """
    folded_content = " ".join(str(content or "").split())
    folded_reasoning = " ".join(str(reasoning or "").split())
    if not folded_content or not folded_reasoning:
        return False
    if folded_content == folded_reasoning:
        return True
    shorter, longer = sorted((folded_content, folded_reasoning), key=len)
    return len(shorter) >= 200 and longer.startswith(shorter)


class OpenRouterCloudProvider(CloudProviderAdapter):
    provider_id = "openrouter"
    base_url = "https://openrouter.ai/api/v1"
    # Credential vocabulary is DERIVED from the one canonical table
    # (core.cloud_providers) — never restated here, so a slot/env rename is a
    # single-file edit and the broker path and `/cloud key` can never disagree.
    credential_name = slot_for("openrouter")
    credential_env = credential_env_for("openrouter")

    def __init__(self, *, credential_broker: CloudCredentialBroker | None = None, catalog_ttl_seconds: int = 3600) -> None:
        self._credentials = credential_broker or CloudCredentialBroker()
        self._catalog_ttl_seconds = max(60, int(catalog_ttl_seconds))

    def _resolved_credential_env(self) -> str:
        """The OpenRouter env alias that currently holds a key — from the ONE table.

        Every served transport call must hand the broker the FULL canonical
        alias set's present member, not just ``env_names[0]``: a host keyed
        through the compatibility alias would otherwise fail with
        ``cloud_credentials_missing`` here while status/routing report PRESENT.
        Falls back to the canonical primary when no alias is set (store/session
        fallthrough then applies unchanged).
        """
        import os

        for name in key_env_names(self.provider_id):
            if str(os.environ.get(name) or "").strip():
                return name
        return self.credential_env

    @staticmethod
    def _request_headers() -> dict[str, str]:
        from core.runtime_provider_defaults import openrouter_attribution_headers

        return openrouter_attribution_headers()

    def validate_credentials(self, transport: PolicyBoundTransport) -> tuple[bool, str]:
        try:
            status, headers, payload = transport.request_json(
                method="GET",
                url=f"{self.base_url}/auth/key",
                headers=self._request_headers(),
                credential_name=self.credential_name,
                credential_env=self._resolved_credential_env(),
            )
            require_success(status, headers, payload)
            return True, "ok"
        except Exception as exc:
            return False, self.classify_provider_error(exc).kind.value

    def discover_models(self, transport: PolicyBoundTransport) -> tuple[CloudModelMetadata, ...]:
        status, headers, payload = transport.request_json(
            method="GET",
            url=f"{self.base_url}/models",
            headers=self._request_headers(),
            credential_name=self.credential_name,
            credential_env=self._resolved_credential_env(),
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
        architecture = dict(payload.get("architecture") or {})
        return CloudModelMetadata(
            provider_id=self.provider_id,
            model_id=model_id,
            display_name=str(payload.get("name") or model_id),
            pricing_state=state,
            input_usd_per_token=input_price,
            output_usd_per_token=output_price,
            request_usd=request_price,
            context_window=max(0, int(payload.get("context_length") or 0)),
            max_output_tokens=max(0, int(payload.get("top_provider", {}).get("max_completion_tokens") or 0)),
            capabilities=normalized_capabilities(payload),
            data_policy={"provider": "openrouter", "source": "provider_catalog"},
            discovered_at=discovered_at,
            expires_at=expires_at,
            health_state="ready",
            metadata={"architecture": architecture},
        )

    def get_account_limits(self, transport: PolicyBoundTransport) -> CloudAccountLimits:
        status, headers, payload = transport.request_json(
            method="GET",
            url=f"{self.base_url}/auth/key",
            headers=self._request_headers(),
            credential_name=self.credential_name,
            credential_env=self._resolved_credential_env(),
        )
        require_success(status, headers, payload)
        data = dict((payload or {}).get("data") or {})
        remaining = data.get("limit_remaining")
        return CloudAccountLimits(
            quota_remaining=float(remaining) if isinstance(remaining, (int, float)) else None,
            metadata={"is_free_tier": bool(data.get("is_free_tier", False))},
        )

    def estimate_request_cost(self, model: CloudModelMetadata, *, input_tokens: int, output_tokens: int) -> float | None:
        return estimate_cost(model, input_tokens=input_tokens, output_tokens=output_tokens)

    def check_model_health(self, transport: PolicyBoundTransport, model: CloudModelMetadata) -> dict[str, Any]:
        models = invoke_provider_execution_boundary(self, "discover_models", transport)
        return {"ok": any(item.model_id == model.model_id for item in models), "provider_id": self.provider_id}

    def send_request(self, transport: PolicyBoundTransport, request: CloudModelRequest) -> CloudModelResponse:
        body: dict[str, Any] = {
            "model": request.model_id,
            "messages": list(request.messages),
            "max_tokens": request.max_output_tokens,
            "stream": False,
            "usage": {"include": True},
        }
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.tools:
            body["tools"] = [openai_tool_payload(tool) for tool in request.tools]
            body["tool_choice"] = request.tool_choice or "required"
        # Final pre-invocation boundary -- see the identical guard in openai_compatible_adapter.py.
        # This adapter never builds a structured-output fallback, so only the native path counts.
        assert_envelope_carries_tools(
            tools_required=bool(request.tools_required),
            offered_tool_count=len(request.tools or ()),
            native_tools_in_envelope=bool(body.get("tools")),
            structured_fallback_in_envelope=False,
            lane_name=self.provider_id,
        )
        request_headers = self._request_headers()
        permit = seal_provider_invocation(
            request=request,
            provider_id=self.provider_id,
            model_id=request.model_id,
            operation="chat",
            payload=body,
            header_names=(*request_headers, "Authorization", "Content-Type"),
        )
        status, headers, payload = transport.request_json(
            method="POST",
            url=f"{self.base_url}/chat/completions",
            headers=request_headers,
            body=permit.consume(),
            credential_name=self.credential_name,
            credential_env=self._resolved_credential_env(),
            timeout_seconds=180.0,
        )
        require_success(status, headers, payload)
        choices = list((payload or {}).get("choices") or [])
        message = dict(choices[0].get("message") or {}) if choices and isinstance(choices[0], dict) else {}
        finish_reason = (
            str((choices[0].get("finish_reason") if choices and isinstance(choices[0], dict) else "") or "")
            .strip()
            .lower()
        )
        raw_tool_calls = message.get("tool_calls")
        tool_calls = ()
        output_text = str(message.get("content") or "")
        # OpenRouter surfaces a reasoning model's thinking in ``message.reasoning`` (and
        # ``reasoning_details``). Some upstream providers ALSO stream that thinking into
        # ``content`` until the answer starts, so a completion cut off by ``max_tokens`` arrives
        # with content == reasoning and no answer at all. Measured live 2026-09-06 against
        # ``nvidia/nemotron-3.5-lightning:free``: ``max_tokens: 520`` -> ``finish_reason: length``,
        # 2066 chars of "Here's a thinking process..." in BOTH fields; the same request at 2048
        # returned a 1063-char sourced answer in ``content`` with the reasoning kept separate.
        # Content that is the reasoning is not an answer, whatever the finish reason says.
        reasoning_field = message.get("reasoning")
        reasoning_text = reasoning_field if isinstance(reasoning_field, str) else ""
        if (
            raw_tool_calls is None
            and output_text.strip()
            and reasoning_text.strip()
            and _content_is_the_reasoning(output_text, reasoning_text)
        ):
            usage_probe = self.parse_usage(payload)
            raise RuntimeError(
                "malformed provider response: reasoning-only content -- the message carried its "
                "reasoning as content and no answer text "
                f"(finish_reason={finish_reason or 'unknown'}, "
                f"completion_tokens={usage_probe.get('completion_tokens', 'unknown')}, "
                f"max_tokens={request.max_output_tokens})"
            )
        if request.tools and raw_tool_calls is None:
            raise RuntimeError("malformed provider response: required native tool call is missing")
        if raw_tool_calls is not None:
            if not request.tools:
                raise RuntimeError("malformed provider response: unexpected native tool call")
            try:
                tool_calls = parse_native_tool_calls(raw_tool_calls, definitions=request.tools)
            except ValueError as exc:
                raise RuntimeError(f"malformed provider response: {exc}") from exc
            # The runtime executes one observed step at a time. Some OpenRouter models batch
            # several valid calls despite that contract; preserve the validated batch for proof,
            # but expose only its first call to the step loop. After that observation the model can
            # choose the next call again, so side effects are never dispatched as an unreviewed batch.
            output_text = canonical_tool_call_text(tool_calls[0])
        elif not output_text.strip():
            # No tool call was required/returned, and the provider gave back nothing in `content`
            # either -- an HTTP 200 with empty/absent `choices` or an empty message. Returning ""
            # here silently would make a malformed body indistinguishable from "the model chose to
            # say nothing", which contract validation then passes as an ordinary empty answer
            # (see core/model_output_contracts.py). classify_error reads "malformed"/"json" in this
            # message into ProviderErrorKind.MALFORMED_RESPONSE, so the broker fails closed instead.
            raise RuntimeError("malformed provider response: no content in any choice and no tool call")
        return CloudModelResponse(
            output_text=output_text,
            usage=self.parse_usage(payload),
            raw_response=payload,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
        )

    def parse_usage(self, payload: Any) -> dict[str, Any]:
        return dict(payload.get("usage") or {}) if isinstance(payload, dict) else {}

    def classify_provider_error(self, error: Any) -> ProviderError:
        return classify_error(error)

    def revoke_or_clear_session_credentials(self) -> None:
        self._credentials.clear_session_credential(self.credential_name)


__all__ = ["OpenRouterCloudProvider"]
