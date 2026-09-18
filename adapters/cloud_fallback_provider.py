from __future__ import annotations

from adapters.openai_compatible_adapter import OpenAICompatibleAdapter


class CloudFallbackProvider(OpenAICompatibleAdapter):
    def estimate_cost_class(self) -> str:
        return "paid_cloud"

    def validate_runtime(self) -> list[str]:
        warnings = super().validate_runtime()
        if not str(self.manifest.runtime_config.get("api_key_env") or "").strip():
            warnings.append(f"{self.manifest.provider_id}: cloud fallback should declare runtime_config.api_key_env")
        return warnings
