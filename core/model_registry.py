from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adapters.base_adapter import ModelAdapter
from adapters.cloud_fallback_provider import CloudFallbackProvider
from adapters.local_model_path_adapter import LocalModelPathAdapter
from adapters.local_qwen_provider import LocalQwenProvider
from adapters.local_subprocess_adapter import LocalSubprocessAdapter
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from adapters.optional_transformers_adapter import OptionalTransformersAdapter
from adapters.peft_lora_adapter import PeftLoRAAdapter
from core.local_model_policy import filter_local_manifests_for_policy
from core.model_selection_policy import ModelSelectionRequest, rank_providers, select_provider
from core.provider_execution_boundary import invoke_provider_execution_boundary
from storage.model_provider_manifest import (
    ModelProviderManifest,
    get_provider_manifest,
    list_provider_manifests,
    load_provider_manifest_file,
    manifests_missing_license_metadata,
    upsert_provider_manifest,
)


@dataclass
class ProviderAuditRow:
    provider_id: str
    source_type: str
    license_name: str | None
    license_reference: str | None
    runtime_dependency: str
    weight_location: str
    weights_bundled: bool
    redistribution_allowed: bool | None
    warnings: list[str]


class ModelRegistry:
    def register_manifest(self, manifest: ModelProviderManifest | dict) -> ModelProviderManifest:
        entry = manifest if isinstance(manifest, ModelProviderManifest) else ModelProviderManifest.model_validate(manifest)
        upsert_provider_manifest(entry)
        return entry

    def register_from_file(self, path: str | Path) -> list[ModelProviderManifest]:
        entries = load_provider_manifest_file(path)
        for entry in entries:
            upsert_provider_manifest(entry)
        return entries

    def list_manifests(self, *, enabled_only: bool = False, limit: int = 256) -> list[ModelProviderManifest]:
        # The one read seam every runtime consumer goes through (ranking, prewarm, health
        # warnings, audit rows, capability truth, requested-model resolution). The store below
        # stays raw — it is the persisted truth an enabled run may return to — but when the
        # canonical LocalModelPolicy is disabled, no local manifest may leave this listing, so
        # nothing downstream can discover, route to, prewarm or fall back onto a local provider,
        # not even rows persisted by an earlier enabled run before a restart.
        return filter_local_manifests_for_policy(
            list_provider_manifests(enabled_only=enabled_only, limit=limit)
        )

    def get_manifest(self, provider_name: str, model_name: str) -> ModelProviderManifest | None:
        return get_provider_manifest(provider_name, model_name)

    def startup_warnings(self) -> list[str]:
        warnings: list[str] = []
        # The license-missing query reads the raw store; route it through the same policy filter
        # as `list_manifests` so a disabled LocalModelPolicy names no local provider here either.
        for manifest in filter_local_manifests_for_policy(
            manifests_missing_license_metadata(enabled_only=False)
        ):
            warnings.append(
                f"{manifest.provider_id}: missing license metadata "
                "(license_name and/or license_reference)"
            )
        for manifest in self.list_manifests(enabled_only=False):
            if manifest.weights_are_bundled:
                warnings.append(
                    f"{manifest.provider_id}: bundled weights declared; keep third-party weights out of VOOL core repo"
                )
            license_name = str(manifest.license_name or "").strip().lower()
            if ("gpl" in license_name or "agpl" in license_name) and manifest.source_type == "local_path":
                warnings.append(
                    f"{manifest.provider_id}: GPL/AGPL-flagged runtime should stay isolated behind subprocess or API boundaries"
                )
            if not str(manifest.runtime_dependency or "").strip():
                warnings.append(f"{manifest.provider_id}: runtime_dependency is missing")
        for manifest in self.list_manifests(enabled_only=True):
            adapter = self.build_adapter(manifest)
            warnings.extend(adapter.validate_runtime())
        return warnings

    def prewarm_enabled_providers(self, *, provider_ids: Iterable[str] | None = None) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        allowed_provider_ids = {
            str(provider_id or "").strip()
            for provider_id in list(provider_ids or ())
            if str(provider_id or "").strip()
        }
        for manifest in self.list_manifests(enabled_only=True):
            if allowed_provider_ids and manifest.provider_id not in allowed_provider_ids:
                continue
            if not dict(manifest.runtime_config.get("prewarm") or {}):
                continue
            adapter = self.build_adapter(manifest)
            try:
                results.append(invoke_provider_execution_boundary(adapter, "prewarm"))
            except Exception as exc:
                results.append(
                    {
                        "ok": False,
                        "provider_id": manifest.provider_id,
                        "status": "error",
                        "error": str(exc),
                    }
                )
        return results

    def provider_audit_rows(self) -> list[ProviderAuditRow]:
        rows: list[ProviderAuditRow] = []
        for manifest in self.list_manifests(enabled_only=False):
            rows.append(
                ProviderAuditRow(
                    provider_id=manifest.provider_id,
                    source_type=manifest.source_type,
                    license_name=manifest.license_name,
                    license_reference=manifest.resolved_license_reference,
                    runtime_dependency=manifest.runtime_dependency,
                    weight_location=manifest.weight_location,
                    weights_bundled=manifest.weights_are_bundled,
                    redistribution_allowed=manifest.redistribution_allowed,
                    warnings=self._warnings_for_manifest(manifest),
                )
            )
        return rows

    def rank_manifests(
        self,
        request: ModelSelectionRequest,
        *,
        exclude_provider_ids: list[str] | None = None,
    ) -> list[ModelProviderManifest]:
        merged_request = ModelSelectionRequest(
            task_kind=request.task_kind,
            output_mode=request.output_mode,
            preferred_provider=request.preferred_provider,
            preferred_model=request.preferred_model,
            preferred_source_types=list(request.preferred_source_types),
            require_license_metadata=request.require_license_metadata,
            forbid_bundled_weights=request.forbid_bundled_weights,
            allow_paid_fallback=request.allow_paid_fallback,
            exclude_provider_ids=list(dict.fromkeys(list(request.exclude_provider_ids) + list(exclude_provider_ids or []))),
            min_trust=request.min_trust,
            # Rebuilt field by field, so anything omitted here is silently dropped rather than
            # inherited. A dropped `local_only` would re-admit the cloud candidates the caller just
            # excluded, which is the one failure this flag cannot survive.
            local_only=request.local_only,
        )
        return rank_providers(self.list_manifests(enabled_only=True), merged_request)

    def select_manifest(self, request: ModelSelectionRequest) -> ModelProviderManifest | None:
        return select_provider(self.list_manifests(enabled_only=True), request)

    def build_adapter(self, manifest: ModelProviderManifest) -> ModelAdapter:
        adapter_type = manifest.adapter_type or _default_adapter_type(manifest)
        if adapter_type == "openai_compatible":
            return OpenAICompatibleAdapter(manifest)
        if adapter_type == "local_qwen_provider":
            return LocalQwenProvider(manifest)
        if adapter_type == "cloud_fallback_provider":
            return CloudFallbackProvider(manifest)
        if adapter_type == "local_subprocess":
            return LocalSubprocessAdapter(manifest)
        if adapter_type == "local_model_path":
            return LocalModelPathAdapter(manifest)
        if adapter_type == "optional_transformers":
            return OptionalTransformersAdapter(manifest)
        if adapter_type == "peft_lora_adapter":
            return PeftLoRAAdapter(manifest)
        if adapter_type == "usepod":
            from adapters.usepod_adapter import UsePodAdapter

            return UsePodAdapter(manifest)
        raise ValueError(f"Unsupported adapter_type: {adapter_type}")

    def _warnings_for_manifest(self, manifest: ModelProviderManifest) -> list[str]:
        warnings: list[str] = []
        if not str(manifest.license_name or "").strip() or not str(manifest.resolved_license_reference or "").strip():
            warnings.append("missing license metadata")
        if manifest.weights_are_bundled:
            warnings.append("bundled weights are not allowed in VOOL core")
        if not str(manifest.runtime_dependency or "").strip():
            warnings.append("missing runtime_dependency")
        return warnings


def _default_adapter_type(manifest: ModelProviderManifest) -> str:
    runtime_family = str(manifest.metadata.get("runtime_family") or "").strip().lower()
    if manifest.adapter_type:
        return manifest.adapter_type
    if manifest.source_type == "http" and "qwen" in manifest.model_name.lower():
        return "local_qwen_provider"
    if manifest.source_type == "http":
        return "openai_compatible"
    if manifest.source_type == "subprocess":
        return "local_subprocess"
    if runtime_family == "transformers":
        return "optional_transformers"
    return "local_model_path"
