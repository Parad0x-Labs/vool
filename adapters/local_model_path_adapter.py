from __future__ import annotations

from pathlib import Path

from adapters.base_adapter import ModelRequest, ModelResponse
from adapters.local_subprocess_adapter import LocalSubprocessAdapter


class LocalModelPathAdapter(LocalSubprocessAdapter):
    def validate_runtime(self) -> list[str]:
        warnings = super().validate_runtime()
        model_path = self._model_path()
        if not model_path:
            warnings.append(f"{self.manifest.provider_id}: missing runtime_config.model_path")
        return warnings

    def invoke(self, request: ModelRequest) -> ModelResponse:
        model_path = self._model_path()
        if not model_path:
            raise RuntimeError(f"{self.manifest.provider_id}: missing runtime_config.model_path")
        if not Path(model_path).exists():
            raise RuntimeError(f"{self.manifest.provider_id}: model path does not exist: {model_path}")
        return self._invoke_with_env(request, extra_env={"VOOL_MODEL_PATH": model_path})

    def health_check(self) -> dict[str, object]:
        model_path = self._model_path()
        if not model_path:
            return {"ok": False, "provider_id": self.manifest.provider_id, "error": "missing_model_path"}
        if not Path(model_path).exists():
            return {"ok": False, "provider_id": self.manifest.provider_id, "error": f"missing_model_path:{model_path}"}
        status = super().health_check()
        status["model_path"] = model_path
        return status

    def _model_path(self) -> str:
        return str(self.manifest.runtime_config.get("model_path") or "").strip()
