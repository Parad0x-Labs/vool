from __future__ import annotations

import importlib.util
import io
import warnings
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from adapters.base_adapter import ModelAdapter, ModelRequest, ModelResponse
from core.provider_invocation_gateway import seal_provider_invocation


class PeftLoRAAdapter(ModelAdapter):
    def validate_runtime(self) -> list[str]:
        warnings: list[str] = []
        for dep in ("transformers", "peft", "torch"):
            if importlib.util.find_spec(dep) is None:
                warnings.append(f"{self.manifest.provider_id}: optional dependency '{dep}' is not installed")
        base_model_ref = self._base_model_ref()
        adapter_path = self._adapter_path()
        if not base_model_ref:
            warnings.append(f"{self.manifest.provider_id}: missing runtime_config.base_model_ref")
        if not adapter_path:
            warnings.append(f"{self.manifest.provider_id}: missing runtime_config.adapter_path")
        elif not Path(adapter_path).exists():
            warnings.append(f"{self.manifest.provider_id}: adapter path does not exist: {adapter_path}")
        return warnings

    def invoke(self, request: ModelRequest) -> ModelResponse:
        if importlib.util.find_spec("transformers") is None or importlib.util.find_spec("peft") is None or importlib.util.find_spec("torch") is None:
            raise RuntimeError(f"{self.manifest.provider_id}: required dependencies 'torch', 'transformers', and 'peft' are not installed")
        base_model_ref = self._base_model_ref()
        adapter_path = self._adapter_path()
        if not base_model_ref:
            raise RuntimeError(f"{self.manifest.provider_id}: missing runtime_config.base_model_ref")
        if not adapter_path:
            raise RuntimeError(f"{self.manifest.provider_id}: missing runtime_config.adapter_path")
        if not Path(adapter_path).exists():
            raise RuntimeError(f"{self.manifest.provider_id}: adapter path does not exist: {adapter_path}")

        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        PeftModel = _import_peft_model()

        device = self._resolve_device()
        tokenizer = AutoTokenizer.from_pretrained(adapter_path)
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token or tokenizer.unk_token
        model = AutoModelForCausalLM.from_pretrained(base_model_ref)
        model = PeftModel.from_pretrained(model, adapter_path)
        model.to(device)
        model.eval()

        prompt = self._build_prompt(tokenizer, request)
        max_new_tokens = int(self.manifest.runtime_config.get("max_new_tokens") or request.max_output_tokens or 256)
        temperature = float(self.manifest.runtime_config.get("temperature") or request.temperature or 0.6)
        permit = seal_provider_invocation(
            request=request,
            provider_id=self.manifest.provider_id,
            model_id=self.manifest.model_name,
            operation="local_generate",
            payload={
                "prompt": prompt,
                "max_new_tokens": max(1, max_new_tokens),
                "temperature": max(0.01, temperature),
            },
        )
        prepared = permit.consume()
        encoded = tokenizer(
            prepared["prompt"],
            return_tensors="pt",
        )
        encoded = {
            name: tensor.to(device)
            for name, tensor in encoded.items()
        }
        with torch.no_grad():
            output_ids = model.generate(
                **encoded,
                max_new_tokens=int(prepared["max_new_tokens"]),
                do_sample=temperature > 0.0,
                temperature=float(prepared["temperature"]),
                pad_token_id=int(tokenizer.pad_token_id or tokenizer.eos_token_id or 0),
                eos_token_id=int(tokenizer.eos_token_id or tokenizer.pad_token_id or 0),
            )
        generated = output_ids[0][encoded["input_ids"].shape[1]:]
        output_text = tokenizer.decode(generated, skip_special_tokens=True).strip()
        return ModelResponse(
            output_text=output_text,
            confidence=0.72,
            raw_response={"output_ids": output_ids[0].tolist()},
            provider_id=self.manifest.provider_id,
            model_name=self.manifest.model_name,
            output_mode=request.output_mode,
        )

    def health_check(self) -> dict[str, object]:
        warnings = self.validate_runtime()
        if warnings:
            return {"ok": False, "provider_id": self.manifest.provider_id, "warnings": warnings}
        return {
            "ok": True,
            "provider_id": self.manifest.provider_id,
            "base_model_ref": self._base_model_ref(),
            "adapter_path": self._adapter_path(),
        }

    def _base_model_ref(self) -> str:
        return str(self.manifest.runtime_config.get("base_model_ref") or "").strip()

    def _adapter_path(self) -> str:
        return str(self.manifest.runtime_config.get("adapter_path") or "").strip()

    def _resolve_device(self) -> str:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        mps = getattr(torch.backends, "mps", None)
        if mps is not None and torch.backends.mps.is_available():
            return "mps"
        return "cpu"

    def _build_prompt(self, tokenizer: object, request: ModelRequest) -> str:
        if hasattr(tokenizer, "apply_chat_template"):
            try:
                messages = []
                if request.system_prompt:
                    messages.append({"role": "system", "content": str(request.system_prompt)})
                messages.append({"role": "user", "content": str(request.prompt)})
                return str(tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True))
            except Exception:
                pass
        if request.system_prompt:
            return f"System:\n{request.system_prompt}\n\nUser:\n{request.prompt}\n\nAssistant:\n"
        return f"### Instruction:\n{request.prompt}\n\n### Response:\n"


def _import_peft_model():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            from peft import PeftModel

    return PeftModel
