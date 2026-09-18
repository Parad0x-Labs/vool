from __future__ import annotations

import importlib
import importlib.util
import io
import json
import math
import os
import sys
import warnings
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.adaptation_dataset import build_adaptation_corpus, curate_adaptation_rows, load_adaptation_examples
from core.runtime_paths import data_path
from storage.adaptation_store import (
    append_adaptation_job_event,
    get_adaptation_corpus,
    get_adaptation_job,
    update_adaptation_job,
)
from storage.model_provider_manifest import ModelProviderManifest

_REQUIRED_DEPS = ("torch", "transformers", "peft")


@dataclass
class DependencyStatus:
    ok: bool
    modules: dict[str, bool]
    device: str

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "modules": dict(self.modules), "device": self.device}


class AdaptationDataset:
    def __init__(self, rows: list[dict[str, Any]], tokenizer: Any, cutoff_len: int) -> None:
        self._rows = list(rows)
        self._tokenizer = tokenizer
        self._cutoff_len = max(128, int(cutoff_len))
        self._eos_id = tokenizer.eos_token_id or tokenizer.sep_token_id or tokenizer.pad_token_id
        if self._eos_id is None:
            self._eos_id = 0

    def __len__(self) -> int:
        return len(self._rows)

    def __getitem__(self, index: int) -> dict[str, Any]:
        item = self._rows[index]
        instruction = str(item.get("instruction") or "").strip()
        output = str(item.get("output") or "").strip()
        prompt_text, full_text = _format_prompt_pair(self._tokenizer, instruction, output)
        prompt_ids = self._tokenizer(prompt_text, add_special_tokens=False)["input_ids"]
        full_ids = self._tokenizer(full_text, add_special_tokens=False)["input_ids"]
        if not full_ids or full_ids[-1] != self._eos_id:
            full_ids = [*list(full_ids), self._eos_id]
        if len(full_ids) > self._cutoff_len:
            prompt_keep = min(len(prompt_ids), max(32, self._cutoff_len // 2))
            response_keep = max(32, self._cutoff_len - prompt_keep)
            prompt_ids = prompt_ids[-prompt_keep:]
            full_ids = prompt_ids + full_ids[len(prompt_ids): len(prompt_ids) + response_keep]
            if full_ids[-1] != self._eos_id:
                if len(full_ids) >= self._cutoff_len:
                    full_ids[-1] = self._eos_id
                else:
                    full_ids.append(self._eos_id)
        prompt_prefix = min(len(prompt_ids), len(full_ids))
        labels = [-100] * prompt_prefix + list(full_ids[prompt_prefix:])
        return {
            "input_ids": list(full_ids),
            "attention_mask": [1] * len(full_ids),
            "labels": labels,
        }


class AdaptationCollator:
    def __init__(self, *, pad_token_id: int) -> None:
        self.pad_token_id = int(pad_token_id)

    def __call__(self, batch: list[dict[str, Any]]) -> dict[str, Any]:
        import torch

        max_len = max(len(item["input_ids"]) for item in batch)
        input_ids: list[list[int]] = []
        attention_mask: list[list[int]] = []
        labels: list[list[int]] = []
        for item in batch:
            pad = max_len - len(item["input_ids"])
            input_ids.append(list(item["input_ids"]) + ([self.pad_token_id] * pad))
            attention_mask.append(list(item["attention_mask"]) + ([0] * pad))
            labels.append(list(item["labels"]) + ([-100] * pad))
        return {
            "input_ids": torch.tensor(input_ids, dtype=torch.long),
            "attention_mask": torch.tensor(attention_mask, dtype=torch.long),
            "labels": torch.tensor(labels, dtype=torch.long),
        }


def dependency_status() -> DependencyStatus:
    modules = {name: _dependency_importable(name) for name in _REQUIRED_DEPS}
    return DependencyStatus(ok=all(modules.values()), modules=modules, device=_resolve_device())


def run_adaptation_job(job_id: str, *, promote: bool = False) -> dict[str, Any]:
    job = get_adaptation_job(job_id)
    if not job:
        raise ValueError(f"Unknown adaptation job: {job_id}")
    corpus = get_adaptation_corpus(job["corpus_id"])
    if not corpus:
        raise ValueError(f"Missing adaptation corpus for job: {job['corpus_id']}")

    deps = dependency_status()
    update_adaptation_job(job_id, status="running", device=deps.device, dependency_status=deps.to_dict(), started_at=_utcnow(), error_text="")
    append_adaptation_job_event(job_id, "dependency_check", "Dependency check completed.", deps.to_dict())
    if not deps.ok:
        error_text = "Missing runtime dependencies for LoRA training."
        update_adaptation_job(job_id, status="failed", error_text=error_text, completed_at=_utcnow())
        append_adaptation_job_event(job_id, "job_failed", error_text, deps.to_dict())
        return get_adaptation_job(job_id) or job

    try:
        corpus_path = str(corpus.get("output_path") or "").strip()
        if not corpus_path or not Path(corpus_path).exists():
            built = build_adaptation_corpus(job["corpus_id"])
            corpus_path = built.output_path
            append_adaptation_job_event(job_id, "corpus_built", "Adaptation corpus rebuilt before training.", {"output_path": corpus_path, "example_count": built.example_count})

        rows = load_adaptation_examples(corpus_path)
        if not rows:
            raise RuntimeError("Adaptation corpus is empty.")
        append_adaptation_job_event(job_id, "corpus_loaded", "Adaptation corpus loaded.", {"example_count": len(rows), "path": corpus_path})
        curated = curate_adaptation_rows(rows, filters=dict(corpus.get("filters") or {}))
        if not curated.rows:
            raise RuntimeError("Adaptation corpus has no high-signal rows after curation.")
        if len(curated.rows) != len(rows):
            append_adaptation_job_event(
                job_id,
                "corpus_curated",
                "Dropped low-signal or duplicate rows before training.",
                {
                    "input_examples": len(rows),
                    "curated_examples": len(curated.rows),
                    **dict(curated.details or {}),
                },
            )
        rows = list(curated.rows)
        output_dir = str(job.get("output_dir") or "").strip() or str(data_path("adaptation", "jobs", job["job_id"]))
        curated_path = Path(output_dir) / "curated_training_corpus.jsonl"
        curated_path.parent.mkdir(parents=True, exist_ok=True)
        curated_path.write_text(
            "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in rows),
            encoding="utf-8",
        )
        train_rows, eval_rows, canary_rows = _split_adaptation_rows(rows, config=_merged_training_config(job))
        if not train_rows:
            raise RuntimeError("Adaptation corpus has no trainable rows after holdout split.")
        split_details = _write_adaptation_splits(
            output_dir=output_dir,
            train_rows=train_rows,
            eval_rows=eval_rows,
            canary_rows=canary_rows,
        )
        append_adaptation_job_event(
            job_id,
            "corpus_split",
            "Prepared train/eval/canary splits for adaptation.",
            {
                "train_examples": len(train_rows),
                "eval_examples": len(eval_rows),
                "canary_examples": len(canary_rows),
                "curated_corpus_path": str(curated_path),
                **split_details,
            },
        )

        trained = _train_adapter(job, train_rows, deps.device, output_dir=output_dir)
        manifest_dict = trained["manifest"].model_dump(mode="python")
        update_adaptation_job(
            job_id,
            status="completed",
            output_dir=trained["output_dir"],
            device=deps.device,
            metrics=trained["metrics"],
            metadata={
                "corpus_total_examples": len(rows),
                "train_example_count": len(train_rows),
                "eval_example_count": len(eval_rows),
                "canary_example_count": len(canary_rows),
                **split_details,
            },
            registered_manifest=manifest_dict,
            completed_at=_utcnow(),
        )
        append_adaptation_job_event(job_id, "job_completed", "LoRA training completed.", {"output_dir": trained["output_dir"], **trained["metrics"]})
        if promote:
            promote_adaptation_job(job_id)
        return get_adaptation_job(job_id) or job
    except Exception as exc:
        error_text = str(exc)
        update_adaptation_job(job_id, status="failed", error_text=error_text, completed_at=_utcnow())
        append_adaptation_job_event(job_id, "job_failed", error_text, {})
        return get_adaptation_job(job_id) or job


def promote_adaptation_job(job_id: str) -> dict[str, Any]:
    from core.model_registry import ModelRegistry

    job = get_adaptation_job(job_id)
    if not job:
        raise ValueError(f"Unknown adaptation job: {job_id}")
    manifest_payload = dict(job.get("registered_manifest") or {})
    if not manifest_payload:
        raise RuntimeError("Adaptation job has no registered manifest to promote.")
    manifest_payload["enabled"] = True
    metadata = dict(manifest_payload.get("metadata") or {})
    metadata["adaptation_promoted"] = True
    metadata["adaptation_job_id"] = job_id
    metadata["promotion_ts"] = _utcnow()
    manifest_payload["metadata"] = metadata
    manifest = ModelProviderManifest.model_validate(manifest_payload)
    registry = ModelRegistry()
    registry.register_manifest(manifest)
    update_adaptation_job(
        job_id,
        status="promoted",
        metadata={"adaptation_promoted": True, "promotion_ts": metadata["promotion_ts"]},
        registered_manifest=manifest.model_dump(mode="python"),
        promoted_at=_utcnow(),
    )
    append_adaptation_job_event(job_id, "job_promoted", "Adapted provider manifest promoted into active registry.", {"provider_id": manifest.provider_id})
    return get_adaptation_job(job_id) or job


def _train_adapter(job: dict[str, Any], rows: list[dict[str, Any]], device: str, *, output_dir: str) -> dict[str, Any]:
    import torch
    from torch.utils.data import DataLoader
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from transformers import logging as transformers_logging
    LoraConfig, TaskType, get_peft_model = _import_peft_symbols()
    transformers_logging.set_verbosity_error()

    config = _merged_training_config(job)
    base_model_ref = _normalize_model_ref(str(job.get("base_model_ref") or "").strip())
    if not base_model_ref:
        raise RuntimeError("Adaptation job is missing base_model_ref.")
    adapter_dir = str(Path(output_dir) / "adapter")
    Path(adapter_dir).mkdir(parents=True, exist_ok=True)

    append_adaptation_job_event(job["job_id"], "model_loading", "Loading tokenizer and base model.", {"base_model_ref": base_model_ref})
    tokenizer = AutoTokenizer.from_pretrained(base_model_ref, trust_remote_code=bool(config.get("trust_remote_code", False)))
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token or tokenizer.unk_token
    model = AutoModelForCausalLM.from_pretrained(
        base_model_ref,
        trust_remote_code=bool(config.get("trust_remote_code", False)),
    )
    if bool(config.get("gradient_checkpointing", True)) and hasattr(model, "gradient_checkpointing_enable"):
        model.gradient_checkpointing_enable()
        if hasattr(model, "config") and hasattr(model.config, "use_cache"):
            model.config.use_cache = False

    target_modules = list(config.get("target_modules") or [])
    if not target_modules:
        target_modules = _infer_target_modules(model)
    if not target_modules:
        raise RuntimeError("Could not infer LoRA target modules for base model.")
    append_adaptation_job_event(job["job_id"], "target_modules", "Resolved LoRA target modules.", {"target_modules": target_modules})

    peft_cfg = LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        inference_mode=False,
        r=int(config.get("lora_r", 8)),
        lora_alpha=int(config.get("lora_alpha", 16)),
        lora_dropout=float(config.get("lora_dropout", 0.05)),
        target_modules=target_modules,
        bias="none",
    )
    os.environ.setdefault("BITSANDBYTES_NOWELCOME", "1")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            model = get_peft_model(model, peft_cfg)
    model.to(device)
    model.train()

    dataset = AdaptationDataset(rows, tokenizer, cutoff_len=int(config.get("cutoff_len", 768)))
    collator = AdaptationCollator(pad_token_id=int(tokenizer.pad_token_id or tokenizer.eos_token_id or 0))
    loader = DataLoader(
        dataset,
        batch_size=max(1, int(config.get("batch_size", 1))),
        shuffle=True,
        collate_fn=collator,
    )
    optimizer = torch.optim.AdamW(model.parameters(), lr=float(config.get("learning_rate", 2e-4)), weight_decay=float(config.get("weight_decay", 0.0)))

    grad_accum = max(1, int(config.get("gradient_accumulation_steps", 4)))
    max_steps = max(1, int(config.get("max_steps", 32)))
    epochs = max(1, int(config.get("epochs", 1)))
    global_step = 0
    loss_sum = 0.0
    optimizer.zero_grad(set_to_none=True)

    for epoch in range(epochs):
        for batch in loader:
            global_step += 1
            batch = {name: tensor.to(device) for name, tensor in batch.items()}
            outputs = model(**batch)
            loss = outputs.loss / grad_accum
            loss.backward()
            loss_sum += float(loss.item())
            if global_step % grad_accum == 0 or global_step >= max_steps:
                optimizer.step()
                optimizer.zero_grad(set_to_none=True)
            if global_step == 1 or global_step % max(1, int(config.get("logging_steps", 4))) == 0:
                append_adaptation_job_event(
                    job["job_id"],
                    "training_step",
                    f"Training step {global_step}/{max_steps}",
                    {"epoch": epoch + 1, "loss": float(loss.item() * grad_accum)},
                )
            if global_step >= max_steps:
                break
        if global_step >= max_steps:
            break

    model.save_pretrained(adapter_dir)
    tokenizer.save_pretrained(adapter_dir)
    append_adaptation_job_event(job["job_id"], "adapter_saved", "LoRA adapter saved.", {"adapter_dir": adapter_dir})

    manifest = _build_adapter_manifest(job, config=config, adapter_dir=adapter_dir, rows=rows, target_modules=target_modules)
    from core.model_registry import ModelRegistry

    ModelRegistry().register_manifest(manifest)
    append_adaptation_job_event(job["job_id"], "manifest_registered", "Adapted provider manifest registered disabled by default.", {"provider_id": manifest.provider_id})
    mean_loss = loss_sum / max(1, global_step)
    metrics = {
        "steps_completed": global_step,
        "epochs_completed": min(epochs, math.ceil(global_step / max(1, len(loader)))),
        "mean_loss": round(mean_loss, 6),
        "examples_used": len(rows),
        "target_modules": target_modules,
        "device": device,
    }
    return {"output_dir": str(Path(output_dir)), "adapter_dir": adapter_dir, "metrics": metrics, "manifest": manifest}


def _build_adapter_manifest(
    job: dict[str, Any],
    *,
    config: dict[str, Any],
    adapter_dir: str,
    rows: list[dict[str, Any]],
    target_modules: list[str],
) -> ModelProviderManifest:
    from core.model_registry import ModelRegistry

    registry = ModelRegistry()
    base_manifest = None
    if str(job.get("base_provider_name") or "").strip() and str(job.get("base_model_name") or "").strip():
        base_manifest = registry.get_manifest(str(job.get("base_provider_name") or "").strip(), str(job.get("base_model_name") or "").strip())
    provider_name = str(job.get("adapter_provider_name") or "").strip() or (
        f"{base_manifest.provider_name}-adapted" if base_manifest else "vool-adapted"
    )
    base_model_ref = _normalize_model_ref(str(job.get("base_model_ref") or "").strip())
    model_name = str(job.get("adapter_model_name") or "").strip() or f"{Path(str(base_model_ref or 'model')).name.replace('/', '-')}-lora-{str(job['job_id'])[-8:]}"
    license_name = str(config.get("license_name") or "").strip() or (base_manifest.license_name if base_manifest else None)
    license_reference = str(config.get("license_reference") or "").strip() or (base_manifest.resolved_license_reference if base_manifest else None)
    capabilities = list(config.get("capabilities") or [])
    if not capabilities:
        capabilities = list(base_manifest.capabilities) if base_manifest else ["summarize", "classify", "format"]
    return ModelProviderManifest.model_validate(
        {
            "provider_name": provider_name,
            "model_name": model_name,
            "source_type": "local_path",
            "adapter_type": "peft_lora_adapter",
            "license_name": license_name,
            "license_reference": license_reference,
            "weight_location": "user-supplied",
            "redistribution_allowed": base_manifest.redistribution_allowed if base_manifest else None,
            "runtime_dependency": "transformers+peft",
            "notes": f"LoRA adapter trained by VOOL from chats/Hive corpus ({job['job_id']}).",
            "capabilities": capabilities,
            "runtime_config": {
                "base_model_ref": str(base_model_ref),
                "adapter_path": str(adapter_dir),
                "max_new_tokens": int(config.get("inference_max_new_tokens", 256)),
                "temperature": float(config.get("inference_temperature", 0.6)),
            },
            "metadata": {
                "runtime_family": "transformers",
                "adaptation_job_id": str(job.get("job_id") or ""),
                "adaptation_corpus_id": str(job.get("corpus_id") or ""),
                "adaptation_example_count": len(rows),
                "adaptation_promoted": False,
                "adaptation_target_modules": list(target_modules),
                "base_provider_name": str(job.get("base_provider_name") or "").strip(),
                "base_model_name": str(job.get("base_model_name") or "").strip(),
            },
            "enabled": False,
        }
    )


def _merged_training_config(job: dict[str, Any]) -> dict[str, Any]:
    config = {
        "epochs": 1,
        "max_steps": 32,
        "batch_size": 1,
        "gradient_accumulation_steps": 4,
        "learning_rate": 2e-4,
        "weight_decay": 0.0,
        "cutoff_len": 768,
        "lora_r": 8,
        "lora_alpha": 16,
        "lora_dropout": 0.05,
        "logging_steps": 4,
        "eval_holdout_examples": 0,
        "canary_holdout_examples": 0,
        "min_train_examples_after_holdout": 1,
        "gradient_checkpointing": True,
        "trust_remote_code": False,
    }
    config.update(dict(job.get("training_config") or {}))
    return config


def _split_adaptation_rows(rows: list[dict[str, Any]], *, config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    clean_rows = [dict(item) for item in list(rows or []) if isinstance(item, dict)]
    if not clean_rows:
        return [], [], []
    requested_eval = max(0, int(config.get("eval_holdout_examples", 0) or 0))
    requested_canary = max(0, int(config.get("canary_holdout_examples", 0) or 0))
    min_train = max(1, int(config.get("min_train_examples_after_holdout", 1) or 1))
    max_holdout = max(0, len(clean_rows) - min_train)
    total_holdout = min(max_holdout, requested_eval + requested_canary)
    canary_count = min(requested_canary, total_holdout)
    eval_count = min(requested_eval, max(0, total_holdout - canary_count))
    while len(clean_rows) - (eval_count + canary_count) < min_train and (canary_count > 0 or eval_count > 0):
        if canary_count >= eval_count and canary_count > 0:
            canary_count -= 1
        elif eval_count > 0:
            eval_count -= 1
        else:
            break
    canary_rows = clean_rows[-canary_count:] if canary_count else []
    eval_end = len(clean_rows) - canary_count
    eval_rows = clean_rows[max(0, eval_end - eval_count):eval_end] if eval_count else []
    train_rows = clean_rows[: max(0, len(clean_rows) - eval_count - canary_count)]
    return train_rows, eval_rows, canary_rows


def _write_adaptation_splits(
    *,
    output_dir: str,
    train_rows: list[dict[str, Any]],
    eval_rows: list[dict[str, Any]],
    canary_rows: list[dict[str, Any]],
) -> dict[str, str]:
    split_dir = Path(output_dir) / "splits"
    split_dir.mkdir(parents=True, exist_ok=True)
    train_path = split_dir / "train.jsonl"
    eval_path = split_dir / "eval.jsonl"
    canary_path = split_dir / "canary.jsonl"
    _write_jsonl_rows(train_path, train_rows)
    if eval_rows:
        _write_jsonl_rows(eval_path, eval_rows)
    if canary_rows:
        _write_jsonl_rows(canary_path, canary_rows)
    return {
        "train_output_path": str(train_path),
        "eval_output_path": str(eval_path) if eval_rows else "",
        "canary_output_path": str(canary_path) if canary_rows else "",
    }


def _write_jsonl_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _normalize_model_ref(model_ref: str) -> str:
    candidate = str(model_ref or "").strip()
    if not candidate:
        return ""
    path = Path(candidate)
    if path.is_absolute():
        return str(path)
    if candidate.startswith("./") or candidate.startswith("../") or "/" in candidate:
        return str((Path(__file__).resolve().parents[1] / candidate).resolve())
    return candidate


def _resolve_device() -> str:
    try:
        import torch
    except Exception:
        sys.modules.pop("torch", None)
        return "unavailable"

    if torch.cuda.is_available():
        return "cuda"
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _dependency_importable(module_name: str) -> bool:
    if importlib.util.find_spec(module_name) is None:
        return False
    try:
        importlib.import_module(module_name)
        return True
    except Exception:
        sys.modules.pop(module_name, None)
        return False


def _infer_target_modules(model: Any) -> list[str]:
    import torch

    preferred_suffixes = (
        "c_attn",
        "c_proj",
        "c_fc",
        "q_proj",
        "k_proj",
        "v_proj",
        "o_proj",
        "up_proj",
        "down_proj",
        "gate_proj",
        "query_key_value",
        "dense",
        "fc1",
        "fc2",
    )
    discovered: list[str] = []
    linear_type = torch.nn.Linear
    for name, module in model.named_modules():
        module_type_name = module.__class__.__name__
        if not isinstance(module, linear_type) and module_type_name != "Conv1D":
            continue
        leaf = str(name).split(".")[-1]
        if leaf == "lm_head":
            continue
        if leaf in preferred_suffixes and leaf not in discovered:
            discovered.append(leaf)
    if discovered:
        return discovered
    fallback: list[str] = []
    for name, module in model.named_modules():
        module_type_name = module.__class__.__name__
        if not isinstance(module, linear_type) and module_type_name != "Conv1D":
            continue
        leaf = str(name).split(".")[-1]
        if leaf == "lm_head" or leaf in fallback:
            continue
        fallback.append(leaf)
        if len(fallback) >= 12:
            break
    return fallback


def _format_prompt_pair(tokenizer: Any, instruction: str, output: str) -> tuple[str, str]:
    if hasattr(tokenizer, "apply_chat_template"):
        try:
            prompt_text = tokenizer.apply_chat_template(
                [{"role": "user", "content": instruction}],
                tokenize=False,
                add_generation_prompt=True,
            )
            full_text = tokenizer.apply_chat_template(
                [
                    {"role": "user", "content": instruction},
                    {"role": "assistant", "content": output},
                ],
                tokenize=False,
                add_generation_prompt=False,
            )
            return str(prompt_text), str(full_text)
        except Exception:
            pass
    prompt = f"### Instruction:\n{instruction}\n\n### Response:\n"
    return prompt, prompt + output


def _utcnow() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _import_peft_symbols():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            from peft import LoraConfig, TaskType, get_peft_model

    return LoraConfig, TaskType, get_peft_model
