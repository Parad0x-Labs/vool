from __future__ import annotations

from adapters.openai_compatible_adapter import OpenAICompatibleAdapter


class LocalQwenProvider(OpenAICompatibleAdapter):
    def estimate_cost_class(self) -> str:
        return "free_local"

    def validate_runtime(self) -> list[str]:
        warnings = super().validate_runtime()
        if not str(self.manifest.runtime_dependency or "").strip():
            warnings.append(f"{self.manifest.provider_id}: runtime_dependency should describe the local runtime (for example lm-studio or openai-compatible-local)")
        return warnings
