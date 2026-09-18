from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from core import policy_engine
from core.model_registry import ModelRegistry
from core.runtime_paths import active_config_home_dir, data_path, project_path
from storage.model_provider_manifest import ModelProviderManifest

_METADATA_FILENAME = "vool_trainable_base.json"
_CURATED_BASES = {
    "qwen-0.5b": {
        "model_id": "Qwen/Qwen2.5-0.5B-Instruct",
        "provider_name": "vool-trainable-base",
        "model_name": "Qwen2.5-0.5B-Instruct",
        "license_name": "Apache-2.0",
        "license_reference": "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct",
        "trust_remote_code": False,
        "recommended_device": "apple_mps_or_cuda",
        "notes": "Default real trainable base for local LoRA on the iMac. Small enough to stage now, real enough to beat the toy fallback.",
    },
}


def stage_trainable_base(
    *,
    model_ref: str = "qwen-0.5b",
    target_root: str | Path | None = None,
    activate: bool = False,
    verify_load: bool = True,
    force_download: bool = False,
    license_name: str = "",
    license_reference: str = "",
    trust_remote_code: bool | None = None,
) -> dict[str, Any]:
    spec = _resolve_base_spec(
        model_ref,
        license_name=license_name,
        license_reference=license_reference,
        trust_remote_code=trust_remote_code,
    )
    root = Path(target_root).expanduser().resolve() if target_root else data_path("trainable_models")
    root.mkdir(parents=True, exist_ok=True)
    model_dir = root / _slugify(spec["model_name"])
    metadata_path = model_dir / _METADATA_FILENAME
    already_present = metadata_path.exists() and _looks_like_model_dir(model_dir)

    if not already_present or force_download:
        _download_model_snapshot(spec=spec, target_dir=model_dir)

    verification = _verify_model_dir(model_dir=model_dir, trust_remote_code=bool(spec["trust_remote_code"])) if verify_load else {}
    metadata = {
        "model_id": spec["model_id"],
        "provider_name": spec["provider_name"],
        "model_name": spec["model_name"],
        "license_name": spec["license_name"],
        "license_reference": spec["license_reference"],
        "trust_remote_code": bool(spec["trust_remote_code"]),
        "runtime_family": "transformers",
        "local_path": str(model_dir),
        "source_kind": "huggingface_snapshot",
        "status": "ready",
        "verification": verification,
        "recommended_device": spec.get("recommended_device", ""),
        "notes": spec.get("notes", ""),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _register_staged_base_manifest(metadata)
    if activate:
        _activate_base_policy(metadata)
        policy_engine.load(force_reload=True)
    return {
        "ok": True,
        "already_present": already_present,
        "activated": activate,
        "model_id": spec["model_id"],
        "model_name": spec["model_name"],
        "local_path": str(model_dir),
        "metadata_path": str(metadata_path),
        "verification": verification,
    }


def list_staged_trainable_bases() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in _trainable_model_roots():
        if not root.exists():
            continue
        for metadata_path in sorted(root.glob(f"*/{_METADATA_FILENAME}")):
            try:
                payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            local_path = str(payload.get("local_path") or metadata_path.parent)
            if local_path in seen:
                continue
            seen.add(local_path)
            payload["metadata_path"] = str(metadata_path)
            payload["exists"] = _looks_like_model_dir(Path(local_path))
            items.append(payload)
    return items


def trainable_base_status() -> dict[str, Any]:
    cfg = dict(policy_engine.get("adaptation", {}) or {})
    return {
        "generated_at": _utcnow(),
        "active_policy": {
            "base_model_ref": str(cfg.get("base_model_ref") or ""),
            "base_provider_name": str(cfg.get("base_provider_name") or ""),
            "base_model_name": str(cfg.get("base_model_name") or ""),
            "license_name": str(cfg.get("license_name") or ""),
            "license_reference": str(cfg.get("license_reference") or ""),
        },
        "staged_bases": list_staged_trainable_bases(),
    }


def best_staged_trainable_base() -> dict[str, Any] | None:
    items = [item for item in list_staged_trainable_bases() if bool(item.get("exists"))]
    if not items:
        return None
    items.sort(key=lambda item: (0 if "qwen" in str(item.get("model_name") or "").lower() else 1, str(item.get("model_name") or "")))
    return items[0]


def _resolve_base_spec(
    model_ref: str,
    *,
    license_name: str,
    license_reference: str,
    trust_remote_code: bool | None,
) -> dict[str, Any]:
    clean = str(model_ref or "").strip() or "qwen-0.5b"
    if clean in _CURATED_BASES:
        spec = dict(_CURATED_BASES[clean])
    else:
        if "/" not in clean:
            raise ValueError("Unknown trainable base alias. Use a curated alias like 'qwen-0.5b' or provide a full HF repo id.")
        spec = {
            "model_id": clean,
            "provider_name": "vool-trainable-base",
            "model_name": clean.split("/")[-1],
            "license_name": str(license_name or "").strip(),
            "license_reference": str(license_reference or "").strip(),
            "trust_remote_code": bool(trust_remote_code),
            "recommended_device": "apple_mps_or_cuda",
            "notes": "User-specified trainable base.",
        }
        if not spec["license_name"] or not spec["license_reference"]:
            raise ValueError("Custom trainable bases require explicit license_name and license_reference.")
    if trust_remote_code is not None:
        spec["trust_remote_code"] = bool(trust_remote_code)
    return spec


def _download_model_snapshot(*, spec: dict[str, Any], target_dir: Path) -> None:
    try:
        from huggingface_hub import snapshot_download
    except Exception as exc:
        raise RuntimeError("huggingface_hub is required to stage a real trainable base.") from exc

    target_dir.mkdir(parents=True, exist_ok=True)
    allow_patterns = [
        "*.json",
        "*.safetensors",
        "*.bin",
        "*.model",
        "*.txt",
        "*.py",
        "*.tiktoken",
        "tokenizer*",
        "spiece.model",
        "LICENSE*",
        "README*",
    ]
    try:
        snapshot_download(
            repo_id=str(spec["model_id"]),
            local_dir=str(target_dir),
            local_dir_use_symlinks=False,
            allow_patterns=allow_patterns,
            resume_download=True,
        )
    except TypeError:
        snapshot_download(
            repo_id=str(spec["model_id"]),
            local_dir=str(target_dir),
            allow_patterns=allow_patterns,
            resume_download=True,
        )


def _verify_model_dir(*, model_dir: Path, trust_remote_code: bool) -> dict[str, Any]:
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(str(model_dir), local_files_only=True, trust_remote_code=trust_remote_code)
    model = AutoModelForCausalLM.from_pretrained(str(model_dir), local_files_only=True, trust_remote_code=trust_remote_code)
    try:
        param_count = int(sum(int(param.numel()) for param in model.parameters()))
    finally:
        del model
    return {
        "tokenizer_class": tokenizer.__class__.__name__,
        "parameter_count": param_count,
    }


def _looks_like_model_dir(model_dir: Path) -> bool:
    if not (model_dir / "config.json").exists():
        return False
    return any(any(model_dir.glob(pattern)) for pattern in ("*.safetensors", "*.bin"))


def _register_staged_base_manifest(metadata: dict[str, Any]) -> None:
    manifest = ModelProviderManifest(
        provider_name=str(metadata["provider_name"]),
        model_name=str(metadata["model_name"]),
        source_type="local_path",
        adapter_type="optional_transformers",
        license_name=str(metadata["license_name"]),
        license_reference=str(metadata["license_reference"]),
        weight_location="user-supplied",
        runtime_dependency="transformers",
        capabilities=["summarize", "classify", "format"],
        runtime_config={
            "model_path": str(metadata["local_path"]),
            "local_files_only": True,
            "trust_remote_code": bool(metadata.get("trust_remote_code")),
        },
        metadata={
            "runtime_family": "transformers",
            "trainable_base_staged": True,
            "model_id": str(metadata["model_id"]),
            "recommended_device": str(metadata.get("recommended_device") or ""),
        },
        enabled=False,
    )
    ModelRegistry().register_manifest(manifest)


def _default_policy_path() -> Path:
    # Call-time authority (2026-09-02): runtime state resolves beneath the active config home,
    # never the import-time source-root constant (a read-only bundle must stay byte-identical).
    return active_config_home_dir() / "default_policy.yaml"


def _activate_base_policy(metadata: dict[str, Any]) -> None:
    config_file = _default_policy_path()
    config_file.parent.mkdir(parents=True, exist_ok=True)
    current: dict[str, Any] = {}
    if config_file.exists():
        loaded = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            current = loaded
    adaptation = dict(current.get("adaptation") or {})
    adaptation.update(
        {
            "base_model_ref": str(metadata["local_path"]),
            "base_provider_name": str(metadata["provider_name"]),
            "base_model_name": str(metadata["model_name"]),
            "license_name": str(metadata["license_name"]),
            "license_reference": str(metadata["license_reference"]),
        }
    )
    current["adaptation"] = adaptation
    config_file.write_text(yaml.safe_dump(current, sort_keys=False), encoding="utf-8")


def _trainable_model_roots() -> tuple[Path, ...]:
    return (
        Path(data_path("trainable_models")),
        project_path("data", "trainable_models"),
    )


def _slugify(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip())
    return clean.strip("-._") or "trainable-base"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
