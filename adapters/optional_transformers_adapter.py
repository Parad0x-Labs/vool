from __future__ import annotations

import importlib.util
from pathlib import Path

from adapters.base_adapter import ModelAdapter, ModelRequest, ModelResponse
from core.provider_invocation_gateway import seal_provider_invocation


class OptionalTransformersAdapter(ModelAdapter):
    def validate_runtime(self) -> list[str]:
        warnings: list[str] = []
        if importlib.util.find_spec("transformers") is None:
            warnings.append(f"{self.manifest.provider_id}: optional dependency 'transformers' is not installed")
        model_path = str(self.manifest.runtime_config.get("model_path") or "").strip()
        if not model_path:
            warnings.append(f"{self.manifest.provider_id}: missing runtime_config.model_path")
        return warnings

    def invoke(self, request: ModelRequest) -> ModelResponse:
        if importlib.util.find_spec("transformers") is None:
            raise RuntimeError(f"{self.manifest.provider_id}: optional dependency 'transformers' is not installed")
        model_path = str(self.manifest.runtime_config.get("model_path") or "").strip()
        if not model_path:
            raise RuntimeError(f"{self.manifest.provider_id}: missing runtime_config.model_path")
        if not Path(model_path).exists():
            raise RuntimeError(f"{self.manifest.provider_id}: model path does not exist: {model_path}")
        from transformers import pipeline  # type: ignore

        task = str(self.manifest.runtime_config.get("task") or "text-generation")
        invocation_payload = {
            "task": task,
            "prompt": request.prompt,
            "max_new_tokens": int(request.max_output_tokens or 128),
        }
        permit = seal_provider_invocation(
            request=request,
            provider_id=self.manifest.provider_id,
            model_id=self.manifest.model_name,
            operation="local_generate",
            payload=invocation_payload,
        )
        prepared = permit.consume()
        model = pipeline(task, model=model_path)
        raw = model(
            prepared["prompt"],
            max_new_tokens=int(prepared["max_new_tokens"]),
        )
        output_text = _extract_output_text(raw)
        return ModelResponse(output_text=output_text, confidence=0.6, raw_response=raw)

    def health_check(self) -> dict[str, object]:
        if importlib.util.find_spec("transformers") is None:
            return {"ok": False, "provider_id": self.manifest.provider_id, "error": "transformers_missing"}
        model_path = str(self.manifest.runtime_config.get("model_path") or "").strip()
        if not model_path or not Path(model_path).exists():
            return {"ok": False, "provider_id": self.manifest.provider_id, "error": "missing_model_path"}
        return {"ok": True, "provider_id": self.manifest.provider_id}


def _extract_output_text(raw: object) -> str:
    if isinstance(raw, list) and raw:
        item = raw[0]
        if isinstance(item, dict):
            return str(item.get("generated_text") or item.get("summary_text") or item.get("text") or "")
    return str(raw)
