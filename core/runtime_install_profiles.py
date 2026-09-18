from __future__ import annotations

import json
import os
import platform
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from core.hardware_tier import MachineProbe, QwenTier, probe_machine, select_qwen_tier
from core.local_model_bundles import (
    bundle_spec,
    installed_ollama_role_for_model,
    model_storage_gb,
    resolve_local_bundle_recommendation,
    safe_disk_floor_gb,
)
from core.local_ollama_inventory import env_flag_enabled, installed_ollama_model_names
from core.local_specialist_lane import (
    DEFAULT_SECONDARY_LOCAL_BACKEND,
    secondary_local_model,
    secondary_local_model_path,
)
from core.local_specialist_lane import (
    secondary_local_provider_id as preferred_secondary_local_provider_id,
)
from core.provider_env import merge_provider_env
from core.provider_routing import ProviderCapabilityTruth

INSTALL_PROFILE_CHOICES = (
    "auto-recommended",
    "local-only",
    "local-max",
    "goblin-stack",
    "hybrid-kimi",
    "hybrid-tether",
    "hybrid-fallback",
    "full-orchestrated",
)
PUBLIC_INSTALL_PROFILE_CHOICES = (
    "auto-recommended",
    "local-only",
    "local-max",
)

_PROFILE_IDS = set(INSTALL_PROFILE_CHOICES)
_PROFILE_ALIASES = {
    "auto": "auto-recommended",
    "recommended": "auto-recommended",
    "ollama-only": "local-only",
    "ollama_only": "local-only",
    "local_only": "local-only",
    "ollama-max": "local-max",
    "ollama_max": "local-max",
    "local_max": "local-max",
    "goblin": "goblin-stack",
    "goblin_stack": "goblin-stack",
    "goblin-stack": "goblin-stack",
    "ollama+kimi": "hybrid-kimi",
    "ollama-kimi": "hybrid-kimi",
    "ollama_kimi": "hybrid-kimi",
    "hybrid_kimi": "hybrid-kimi",
    "ollama+tether": "hybrid-tether",
    "ollama-tether": "hybrid-tether",
    "ollama_tether": "hybrid-tether",
    "hybrid_tether": "hybrid-tether",
    "hybrid_fallback": "hybrid-fallback",
    "full_orchestrated": "full-orchestrated",
}
_PROFILE_DISPLAY_IDS = {
    "local-only": "ollama-only",
    "local-max": "ollama-max",
    "goblin-stack": "goblin_stack",
    "hybrid-kimi": "ollama+kimi",
    "hybrid-tether": "ollama+tether",
}
_LOCAL_ONLY_PROFILE_IDS = frozenset({"local-only", "local-max", "goblin-stack"})

_MODEL_SIZE_GB = {
    "qwen2.5:0.5b": 1.0,
    "qwen2.5:3b": 3.5,
    "qwen2.5:7b": 8.0,
    "qwen2.5:14b": 16.0,
    "qwen2.5:14b-gguf": 18.0,
    "qwen2.5:32b": 36.0,
    "qwen2.5:72b": 80.0,
}
_INSTALL_PROFILE_RECORD_RELATIVE_PATH = Path("config") / "install-profile.json"
_KIMI_API_KEY_ENV_KEYS = ("KIMI_API_KEY", "MOONSHOT_API_KEY", "VOOL_KIMI_API_KEY")
_KIMI_API_KEY_REASON = "KIMI_API_KEY or MOONSHOT_API_KEY"
_GENERIC_REMOTE_API_KEY_ENV_KEYS = ("OPENAI_API_KEY", "VOOL_REMOTE_API_KEY", "VOOL_CLOUD_API_KEY")
_GENERIC_REMOTE_API_KEY_REASON = "OPENAI_API_KEY, VOOL_REMOTE_API_KEY, or VOOL_CLOUD_API_KEY"
_TETHER_API_KEY_ENV_KEYS = ("TETHER_API_KEY", "VOOL_TETHER_API_KEY")
_TETHER_BASE_URL_ENV_KEYS = ("TETHER_BASE_URL", "VOOL_TETHER_BASE_URL")
_TETHER_CONFIG_REASON = "TETHER_API_KEY and TETHER_BASE_URL"
_INSTALLED_OLLAMA_MODELS_ENV_KEY = "VOOL_INSTALLED_OLLAMA_MODELS"


@dataclass(frozen=True)
class InstallProfileProvider:
    provider_id: str
    role: str
    locality: str
    required: bool
    api_key_envs: tuple[str, ...] = ()
    configured: bool = True
    availability_state: str = "unregistered"
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "role": self.role,
            "locality": self.locality,
            "required": self.required,
            "api_key_envs": list(self.api_key_envs),
            "configured": self.configured,
            "availability_state": self.availability_state,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class InstallProfileVolumeCheck:
    volume_id: str
    labels: tuple[str, ...]
    path: str
    required_gb: float
    free_gb: float
    ok: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "volume_id": self.volume_id,
            "labels": list(self.labels),
            "path": self.path,
            "required_gb": self.required_gb,
            "free_gb": self.free_gb,
            "ok": self.ok,
        }


@dataclass(frozen=True)
class InstallProfileTruth:
    profile_id: str
    label: str
    summary: str
    selection_source: str
    selected_model: str
    provider_mix: tuple[InstallProfileProvider, ...]
    estimated_download_gb: float
    estimated_disk_footprint_gb: float
    minimum_free_space_gb: float
    ram_expectation_gb: float
    vram_expectation_gb: float
    ready: bool
    degraded: bool
    single_volume_ready: bool
    reasons: tuple[str, ...]
    volume_checks: tuple[InstallProfileVolumeCheck, ...]
    selected_models: tuple[str, ...] = ()
    optional_models: tuple[str, ...] = ()
    selected_model_roles: tuple[tuple[str, str], ...] = ()
    capacity_bucket: str = ""
    bundle_id: str = ""
    bundle_kind: str = ""
    advanced_optional_profile: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "vool.install_profile.v1",
            "profile_id": self.profile_id,
            "label": self.label,
            "summary": self.summary,
            "selection_source": self.selection_source,
            "selected_model": self.selected_model,
            "selected_models": list(self.selected_models),
            "optional_models": list(self.optional_models),
            "selected_model_roles": [
                {"role": role, "model": model} for role, model in self.selected_model_roles
            ],
            "capacity_bucket": self.capacity_bucket,
            "bundle_id": self.bundle_id,
            "bundle_kind": self.bundle_kind,
            "advanced_optional_profile": self.advanced_optional_profile,
            "provider_mix": [item.to_dict() for item in self.provider_mix],
            "estimated_download_gb": self.estimated_download_gb,
            "estimated_disk_footprint_gb": self.estimated_disk_footprint_gb,
            "minimum_free_space_gb": self.minimum_free_space_gb,
            "ram_expectation_gb": self.ram_expectation_gb,
            "vram_expectation_gb": self.vram_expectation_gb,
            "ready": self.ready,
            "degraded": self.degraded,
            "single_volume_ready": self.single_volume_ready,
            "reasons": list(self.reasons),
            "volume_checks": [item.to_dict() for item in self.volume_checks],
        }

    def display_summary(self) -> str:
        provider_roles = ", ".join(f"{item.role}:{item.provider_id}" for item in self.provider_mix)
        models_label = ", ".join(self.selected_models) if self.selected_models else self.selected_model
        return (
            f"{format_install_profile_id(self.profile_id)} -> {models_label} "
            f"({provider_roles}; download~{self.estimated_download_gb:.1f} GB; "
            f"disk~{self.estimated_disk_footprint_gb:.1f} GB)"
        )


def build_install_profile_truth(
    *,
    requested_profile: str | None = None,
    probe: MachineProbe | None = None,
    tier: QwenTier | None = None,
    selected_model: str | None = None,
    provider_capability_truth: tuple[ProviderCapabilityTruth, ...] = (),
    runtime_home: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> InstallProfileTruth:
    env_map = merge_provider_env(runtime_home, env=os.environ if env is None else env)
    active_probe = probe or probe_machine()
    active_tier = tier or select_qwen_tier(active_probe)
    installed_record = _installed_profile_record(runtime_home)
    requested_arg_raw = str(requested_profile or "").strip().lower()
    requested_env_raw = str(env_map.get("VOOL_INSTALL_PROFILE") or "").strip().lower()
    requested_raw = requested_arg_raw or requested_env_raw
    requested = normalize_install_profile_id(requested_raw, allow_auto=True)
    requested_source = "env_override" if requested else ""
    if not requested:
        requested = str(installed_record.get("profile_id") or "")
        if requested:
            requested_source = "installed_record"
    model_store_path = default_ollama_models_path(env_map)
    bundle_recommendation = resolve_local_bundle_recommendation(
        probe=active_probe,
        free_disk_gb=_disk_free_gb(_nearest_existing_path(model_store_path)),
        secondary_local_model_name=secondary_local_model(env_map),
        selected_model=str(selected_model or installed_record.get("selected_model") or "").strip(),
    )
    installed_ollama_models = _installed_ollama_model_tags(
        provider_capability_truth=provider_capability_truth,
        env=env_map,
    )
    auto_reasons: list[str] = []
    if requested == "auto-recommended":
        auto_reasons.append("Install profile requested auto-recommended; applying hardware/provider auto selection.")
        requested = ""
    elif requested_raw and not requested:
        auto_reasons.append(f"Unknown install profile `{requested_raw}`. Falling back to auto-recommended.")
        requested = ""

    if requested:
        if requested_source == "installed_record":
            selection_source = "installed_default"
            selection_reasons = [_installed_profile_reason(requested)]
        else:
            selection_source = "env_override"
            selection_reasons = [_requested_profile_reason(requested_raw, requested, explicit_request=bool(requested_arg_raw))]
        return _compose_install_profile_truth(
            profile_id=requested,
            selection_source=selection_source,
            selection_reasons=selection_reasons,
            bundle_recommendation=bundle_recommendation,
            tier=active_tier,
            probe=active_probe,
            provider_capability_truth=provider_capability_truth,
            runtime_home=runtime_home,
            env=env_map,
            installed_ollama_models=installed_ollama_models,
        )

    candidates = _auto_profile_candidates(
        probe=active_probe,
        tier=active_tier,
        env=env_map,
        provider_capability_truth=provider_capability_truth,
    )
    evaluated: list[InstallProfileTruth] = []
    for candidate in candidates:
        evaluated.append(
            _compose_install_profile_truth(
                profile_id=candidate,
                selection_source="auto",
                selection_reasons=[*auto_reasons, _auto_selection_reason(candidate)],
                bundle_recommendation=bundle_recommendation,
                tier=active_tier,
                probe=active_probe,
                provider_capability_truth=provider_capability_truth,
                runtime_home=runtime_home,
                env=env_map,
                installed_ollama_models=installed_ollama_models,
            )
        )

    chosen = next((profile for profile in evaluated if profile.ready and not profile.degraded), None)
    if chosen is None:
        chosen = next((profile for profile in evaluated if profile.ready), evaluated[0])
    chosen_index = evaluated.index(chosen)
    if chosen_index == 0:
        return chosen

    fallback_reasons = [
        f"Auto-fell back from `{previous.profile_id}` because {_primary_profile_blocker(previous)}."
        for previous in evaluated[:chosen_index]
    ]
    return replace(
        chosen,
        reasons=tuple(
            dict.fromkeys(
                reason.strip()
                for reason in [*chosen.reasons, *fallback_reasons]
                if reason and reason.strip()
            )
        ),
    )


def default_ollama_models_path(env: Mapping[str, str] | None = None) -> Path:
    env_map = os.environ if env is None else env
    home = Path.home()
    system = platform.system().lower()
    candidates: list[Path] = []
    try:
        from core.bundle_manifest import bundle_model_store_path

        bundle_store = bundle_model_store_path(env=env_map)
    except Exception:
        bundle_store = None
    if bundle_store is not None:
        candidates.append(bundle_store)
    override = str(env_map.get("OLLAMA_MODELS") or "").strip()
    if override:
        candidates.append(Path(override).expanduser())
    if system == "windows":
        candidates.extend(_windows_ollama_models_env_paths())
        for drive in "DEFGHIJKLMNOPQRSTUVWXYZC":
            candidates.append(Path(f"{drive}:\\Ollama\\models"))
    candidates.append(home / ".ollama" / "models")
    deduped: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate.absolute()
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(resolved)
    for candidate in deduped:
        if _path_target_available(candidate):
            return candidate
    return deduped[0] if deduped else (home / ".ollama" / "models").resolve()


def normalize_install_profile_id(profile_id: str | None, *, allow_auto: bool = True) -> str:
    normalized = str(profile_id or "").strip().lower()
    if not normalized:
        return ""
    normalized = _PROFILE_ALIASES.get(normalized, normalized)
    if normalized not in _PROFILE_IDS:
        return ""
    if not allow_auto and normalized == "auto-recommended":
        return ""
    return normalized


def preferred_install_profile_id(profile_id: str | None, *, allow_auto: bool = True) -> str:
    normalized = normalize_install_profile_id(profile_id, allow_auto=allow_auto)
    if not normalized:
        return ""
    return _PROFILE_DISPLAY_IDS.get(normalized, normalized)


def format_install_profile_id(profile_id: str | None, *, allow_auto: bool = True) -> str:
    normalized = normalize_install_profile_id(profile_id, allow_auto=allow_auto)
    if not normalized:
        return ""
    preferred = preferred_install_profile_id(normalized, allow_auto=allow_auto)
    if preferred == normalized:
        return normalized
    return f"{preferred} ({normalized})"


def install_profile_display_choices(*, include_legacy: bool = False) -> tuple[str, ...]:
    choices = INSTALL_PROFILE_CHOICES if include_legacy else PUBLIC_INSTALL_PROFILE_CHOICES
    return tuple(format_install_profile_id(choice) for choice in choices)


def installed_profile_id(runtime_home: str | Path | None) -> str:
    return _installed_profile_id(runtime_home)


def installed_profile_selected_model(runtime_home: str | Path | None) -> str:
    payload = _installed_profile_record(runtime_home)
    return str(payload.get("selected_model") or "").strip()


def installed_capacity_bucket(runtime_home: str | Path | None) -> str:
    """The capacity bucket (A-E) recorded at install time, read cheaply from the persisted
    install-profile record. Empty string if unknown. Used to size context windows to the
    machine WITHOUT re-running a hardware probe at provider-registration time."""
    payload = _installed_profile_record(runtime_home)
    bucket = str(payload.get("capacity_bucket") or "").strip().upper()
    return bucket if bucket in {"A", "B", "C", "D", "E"} else ""


def active_install_profile_id(
    *,
    runtime_home: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    allow_auto: bool = False,
) -> str:
    env_map = os.environ if env is None else env
    requested = normalize_install_profile_id(env_map.get("VOOL_INSTALL_PROFILE"), allow_auto=allow_auto)
    if requested:
        return requested
    return normalize_install_profile_id(_installed_profile_id(runtime_home), allow_auto=allow_auto)


def install_profile_runs_local_only(profile_id: str | None) -> bool:
    normalized = normalize_install_profile_id(profile_id, allow_auto=False)
    return normalized in _LOCAL_ONLY_PROFILE_IDS


def persist_install_profile_record(
    runtime_home: str | Path,
    profile_id: str,
    *,
    selected_model: str = "",
    selected_models: tuple[str, ...] = (),
    bundle_id: str = "",
    bundle_kind: str = "",
) -> Path:
    runtime_root = Path(runtime_home).expanduser().resolve()
    target = runtime_root / _INSTALL_PROFILE_RECORD_RELATIVE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "vool.install_profile_record.v1",
        "profile_id": str(profile_id or "").strip().lower(),
        "selected_model": str(selected_model or "").strip(),
        "selected_models": [str(item).strip() for item in selected_models if str(item).strip()],
        "bundle_id": str(bundle_id or "").strip(),
        "bundle_kind": str(bundle_kind or "").strip(),
    }
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def _installed_profile_id(runtime_home: str | Path | None) -> str:
    payload = _installed_profile_record(runtime_home)
    profile_id = str(payload.get("profile_id") or "").strip().lower()
    if profile_id in _PROFILE_IDS:
        return profile_id
    return ""


def _installed_profile_record(runtime_home: str | Path | None) -> dict[str, Any]:
    if runtime_home is None:
        return {}
    try:
        record_path = Path(runtime_home).expanduser().resolve() / _INSTALL_PROFILE_RECORD_RELATIVE_PATH
    except Exception:
        return {}
    try:
        payload = json.loads(record_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _requested_profile_reason(requested_raw: str, requested: str, *, explicit_request: bool) -> str:
    if explicit_request:
        if requested_raw != requested:
            return f"Install profile was requested explicitly as `{requested_raw}` and resolved to `{requested}`."
        return f"Install profile was requested explicitly as `{requested}`."
    if requested_raw != requested:
        return f"Install profile came from VOOL_INSTALL_PROFILE={requested_raw} and resolved to `{requested}`."
    return f"Install profile came from VOOL_INSTALL_PROFILE={requested}."


def _installed_profile_reason(profile_id: str) -> str:
    preferred = preferred_install_profile_id(profile_id, allow_auto=False)
    if preferred != profile_id:
        return (
            f"Install profile came from the installed runtime profile `{profile_id}` "
            f"(operator lane `{preferred}`)."
        )
    return f"Install profile came from the installed runtime profile `{profile_id}`."


def _compose_install_profile_truth(
    *,
    profile_id: str,
    selection_source: str,
    selection_reasons: list[str],
    bundle_recommendation: Any,
    tier: QwenTier,
    probe: MachineProbe,
    provider_capability_truth: tuple[ProviderCapabilityTruth, ...],
    runtime_home: str | Path | None,
    env: Mapping[str, str],
    installed_ollama_models: set[str],
) -> InstallProfileTruth:
    bundle_recommendation = _with_profile_bundle(profile_id, bundle_recommendation)
    recommended_bundle = bundle_recommendation.recommended_bundle
    model_tag = recommended_bundle.primary_model
    selected_role_models = _selected_role_models_for_profile(
        profile_id=profile_id,
        recommended_bundle=recommended_bundle,
        legacy_mode=bool(getattr(bundle_recommendation, "legacy_mode", False)),
    )
    estimates = _profile_estimates(
        profile_id=profile_id,
        bundle_recommendation=bundle_recommendation,
        tier=tier,
        probe=probe,
        installed_ollama_models=installed_ollama_models,
        provider_capability_truth=provider_capability_truth,
        env=env,
        runtime_home=runtime_home,
    )
    provider_mix, provider_reasons = _provider_mix(
        profile_id=profile_id,
        bundle_recommendation=bundle_recommendation,
        provider_capability_truth=provider_capability_truth,
        env=env,
    )
    volume_checks = _volume_checks(
        runtime_home=runtime_home,
        env=env,
        runtime_required_gb=estimates["runtime_required_gb"],
        ollama_required_gb=estimates["model_required_gb"],
    )
    single_volume_ready = all(item.ok for item in volume_checks)
    reasons = list(selection_reasons)
    reasons.extend(provider_reasons)
    required_provider_mix = tuple(item for item in provider_mix if item.required)
    blocked_provider_mix = tuple(
        item for item in required_provider_mix if item.availability_state in {"blocked", "unregistered"}
    )
    degraded_provider_mix = tuple(item for item in required_provider_mix if item.availability_state == "degraded")
    if not single_volume_ready:
        reasons.append(
            "No single target volume currently has enough free space for the selected runtime + model footprint."
        )
    if blocked_provider_mix:
        for item in blocked_provider_mix:
            reasons.append(
                f"Required provider lane `{item.provider_id}` is {item.availability_state} and cannot be treated as beta-ready."
            )
    if degraded_provider_mix:
        for item in degraded_provider_mix:
            reasons.append(
                f"Required provider lane `{item.provider_id}` is degraded and may still work, but the profile is not fully healthy."
            )
    ready = (
        single_volume_ready
        and all(item.configured for item in required_provider_mix)
        and not blocked_provider_mix
    )
    degraded = bool(degraded_provider_mix)
    if not ready and all(item.configured for item in required_provider_mix) and single_volume_ready and not blocked_provider_mix:
        reasons.append("Profile is selected but not fully ready.")
    return InstallProfileTruth(
        profile_id=profile_id,
        label=_profile_label(profile_id),
        summary=_profile_summary(profile_id),
        selection_source=selection_source,
        selected_model=model_tag,
        selected_models=recommended_bundle.models,
        optional_models=_optional_models_for_profile(profile_id),
        selected_model_roles=tuple((item.role, item.model) for item in selected_role_models),
        capacity_bucket=str(bundle_recommendation.capacity_bucket),
        bundle_id=recommended_bundle.bundle_id,
        bundle_kind=recommended_bundle.kind,
        advanced_optional_profile=str(bundle_recommendation.advanced_optional_profile or ""),
        provider_mix=provider_mix,
        estimated_download_gb=estimates["estimated_download_gb"],
        estimated_disk_footprint_gb=estimates["estimated_disk_footprint_gb"],
        minimum_free_space_gb=estimates["minimum_free_space_gb"],
        ram_expectation_gb=estimates["ram_expectation_gb"],
        vram_expectation_gb=estimates["vram_expectation_gb"],
        ready=ready,
        degraded=degraded,
        single_volume_ready=single_volume_ready,
        reasons=tuple(dict.fromkeys(reason.strip() for reason in reasons if reason.strip())),
        volume_checks=volume_checks,
    )


def _auto_profile_candidates(
    *,
    probe: MachineProbe,
    tier: QwenTier,
    env: Mapping[str, str],
    provider_capability_truth: tuple[ProviderCapabilityTruth, ...] = (),
) -> tuple[str, ...]:
    del probe, tier, env, provider_capability_truth
    return ("local-only",)


def _auto_selection_reason(profile_id: str) -> str:
    if profile_id == "local-max":
        return "Auto-selected local-max because this machine can hold a stronger fully local lane."
    return "Auto-selected local-only to keep the default runtime local-first, latency-safe, and subscription-free."


def _primary_profile_blocker(profile: InstallProfileTruth) -> str:
    ignored_prefixes = (
        "Install profile requested auto-recommended",
        "Unknown install profile",
        "Auto-selected ",
        "Install profile came from ",
        "Auto-fell back from ",
    )
    for reason in profile.reasons:
        if reason.startswith(ignored_prefixes):
            continue
        return reason
    if profile.degraded:
        return "it was degraded"
    if not profile.ready:
        return "it was not ready on this machine/runtime"
    return "a safer install profile was chosen"


def _profile_estimates(
    *,
    profile_id: str,
    bundle_recommendation: Any,
    tier: QwenTier,
    probe: MachineProbe,
    installed_ollama_models: set[str],
    provider_capability_truth: tuple[ProviderCapabilityTruth, ...],
    env: Mapping[str, str],
    runtime_home: str | Path | None,
) -> dict[str, float]:
    required_local_models = _required_ollama_models(
        profile_id=profile_id,
        bundle_recommendation=bundle_recommendation,
    )
    missing_local_models = tuple(
        model_name for model_name in required_local_models if model_name.lower() not in installed_ollama_models
    )
    missing_model_gb = sum(model_storage_gb(model_name) for model_name in missing_local_models)
    secondary_local_model_name = secondary_local_model(env)
    secondary_local_provider = preferred_secondary_local_provider_id(env)
    secondary_local_available = any(
        str(item.provider_id or "").strip().lower() == secondary_local_provider.lower()
        for item in provider_capability_truth
    )
    secondary_local_path = secondary_local_model_path(env)
    secondary_local_installed = secondary_local_available or (
        bool(secondary_local_path)
        and Path(secondary_local_path).expanduser().exists()
    )
    missing_secondary_local_gb = 0.0
    if profile_id in {"local-max", "full-orchestrated"} and not secondary_local_installed:
        missing_secondary_local_gb = model_storage_gb(secondary_local_model_name)
    runtime_required_gb = 2.5

    if profile_id in {"local-max", "full-orchestrated"}:
        runtime_required_gb += 1.0
    if profile_id in {"hybrid-kimi", "hybrid-tether", "hybrid-fallback", "full-orchestrated"}:
        runtime_required_gb += 0.5

    model_buffer_gb = 1.5 if (missing_local_models or missing_secondary_local_gb > 0.0) else 0.0
    total_missing_model_gb = missing_model_gb + missing_secondary_local_gb
    estimated_download_gb = round(total_missing_model_gb, 1)
    model_required_gb = round(total_missing_model_gb + model_buffer_gb, 1)
    minimum_free_space_gb = round(runtime_required_gb + model_required_gb, 1)
    bucket = str(bundle_recommendation.capacity_bucket or "")
    bucket_ram_floor = {
        "A": 8.0,
        "B": 16.0,
        "C": 24.0,
        "D": 32.0,
        "E": 48.0,
    }.get(bucket, max(tier.min_ram_gb, 6.0))
    bucket_vram_floor = {
        "A": 0.0,
        "B": 6.0,
        "C": 10.0,
        "D": 16.0,
        "E": 24.0,
    }.get(bucket, max(tier.min_vram_gb, 0.0))
    return {
        "estimated_download_gb": estimated_download_gb,
        "estimated_disk_footprint_gb": minimum_free_space_gb,
        "minimum_free_space_gb": minimum_free_space_gb,
        "runtime_required_gb": round(runtime_required_gb, 1),
        "model_required_gb": model_required_gb,
        "ram_expectation_gb": float(max(bucket_ram_floor, tier.min_ram_gb, 6.0, float(probe.ram_gb or 0.0) if bucket == "A" else 0.0)),
        "vram_expectation_gb": float(max(bucket_vram_floor, tier.min_vram_gb, 0.0)),
    }


def _required_ollama_models(
    *,
    profile_id: str,
    bundle_recommendation: Any,
) -> tuple[str, ...]:
    if profile_id == "goblin-stack":
        return bundle_spec("goblin_stack").models
    return tuple(
        str(model_name).strip()
        for model_name in bundle_recommendation.recommended_bundle.models
        if str(model_name).strip()
    )


def required_ollama_models_for_profile(
    *,
    profile_id: str,
    model_tag: str,
    probe: MachineProbe | None = None,
    runtime_home: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    normalized_profile = normalize_install_profile_id(profile_id, allow_auto=False)
    active_probe = probe or probe_machine()
    env_map = os.environ if env is None else env
    model_store_path = default_ollama_models_path(env_map)
    recommendation = resolve_local_bundle_recommendation(
        probe=active_probe,
        free_disk_gb=_disk_free_gb(_nearest_existing_path(model_store_path)),
        secondary_local_model_name=secondary_local_model(env_map),
        selected_model=str(model_tag or "").strip(),
    )
    return _required_ollama_models(profile_id=normalized_profile, bundle_recommendation=recommendation)


def _provider_mix(
    *,
    profile_id: str,
    bundle_recommendation: Any,
    provider_capability_truth: tuple[ProviderCapabilityTruth, ...],
    env: Mapping[str, str],
) -> tuple[tuple[InstallProfileProvider, ...], list[str]]:
    truth_index = _provider_truth_index(provider_capability_truth)
    legacy_mode = bool(getattr(bundle_recommendation, "legacy_mode", False))
    role_models = _selected_role_models_for_profile(
        profile_id=profile_id,
        recommended_bundle=bundle_recommendation.recommended_bundle,
        legacy_mode=legacy_mode,
    )
    primary_local_model = bundle_recommendation.recommended_bundle.primary_model
    local_provider_id = _find_primary_local_provider_id(provider_capability_truth, model_tag=primary_local_model)
    secondary_local_provider_id = _find_secondary_local_provider_id(
        provider_capability_truth,
        primary_provider_id=local_provider_id,
        env=env,
    )
    kimi_provider_id = _find_remote_provider_id(provider_capability_truth, hint="kimi")
    tether_provider_id = _find_remote_provider_id(provider_capability_truth, hint="tether")
    fallback_provider_id = _find_remote_provider_id(provider_capability_truth, hint=None, exclude={kimi_provider_id})
    secondary_local_availability = _provider_availability_state(secondary_local_provider_id, truth_index)
    kimi_availability = _provider_availability_state(kimi_provider_id, truth_index)
    tether_availability = _provider_availability_state(tether_provider_id, truth_index)
    fallback_availability = _provider_availability_state(fallback_provider_id, truth_index)
    providers: list[InstallProfileProvider] = []
    reasons: list[str] = []

    for role_model in role_models:
        provider_role = _provider_mix_role_for_bundle_role(
            role_model.role,
            profile_id=profile_id,
            legacy_mode=legacy_mode,
        )
        provider_id = _find_local_provider_id_for_model(
            provider_capability_truth,
            model_name=role_model.model,
            fallback_prefix="ollama-local",
        )
        providers.append(
            InstallProfileProvider(
                provider_id=provider_id,
                role=provider_role,
                locality="local",
                required=True,
                configured=True,
                availability_state=_provider_availability_state(provider_id, truth_index),
                notes=f"Required local Ollama `{provider_role}` lane.",
            )
        )
    if _expose_installed_ollama_lanes(env):
        _append_installed_ollama_lanes(
            providers,
            provider_capability_truth=provider_capability_truth,
            primary_local_model=primary_local_model,
            env=env,
        )
    if profile_id in {"local-max", "full-orchestrated"}:
        distinct_secondary = bool(secondary_local_provider_id) and secondary_local_provider_id != local_provider_id
        verifier_provider_id = secondary_local_provider_id or preferred_secondary_local_provider_id(env)
        providers.append(
            InstallProfileProvider(
                provider_id=verifier_provider_id,
                role="verifier",
                locality="local",
                required=True,
                configured=distinct_secondary,
                availability_state=secondary_local_availability if distinct_secondary else "unregistered",
                notes=(
                    f"Secondary local verification lane, with {DEFAULT_SECONDARY_LOCAL_BACKEND} required for the stronger dual-local profile."
                    if distinct_secondary
                    else f"Distinct {DEFAULT_SECONDARY_LOCAL_BACKEND} verifier lane required before this profile is ready."
                ),
            )
        )
        if not distinct_secondary:
            reasons.append(
                f"{profile_id} needs a distinct {DEFAULT_SECONDARY_LOCAL_BACKEND} local verifier lane before it can be treated as ready."
            )
        # When a dual llamacpp setup is active (fast 8B + deep 14B), the fast lane
        # is the verifier's peer — expose it too so routing can use both.
        fast_llamacpp_model = str(
            env.get("VOOL_LLAMACPP_MODEL") or env.get("LLAMACPP_MODEL") or ""
        ).strip()
        if fast_llamacpp_model:
            fast_llamacpp_provider_id = f"llamacpp-local:{fast_llamacpp_model}"
            already_listed = {p.provider_id for p in providers}
            if fast_llamacpp_provider_id not in already_listed:
                fast_availability = _provider_availability_state(fast_llamacpp_provider_id, truth_index)
                if _availability_rank(fast_availability) >= _availability_rank("degraded"):
                    providers.append(
                        InstallProfileProvider(
                            provider_id=fast_llamacpp_provider_id,
                            role="fast",
                            locality="local",
                            required=False,
                            configured=True,
                            availability_state=fast_availability,
                            notes="Fast local llamacpp lane (primary model), sibling to the deep verifier lane.",
                        )
                    )
    if profile_id == "hybrid-kimi":
        configured = _has_any_env(env, *_KIMI_API_KEY_ENV_KEYS) or kimi_availability != "unregistered"
        providers.append(
            InstallProfileProvider(
                provider_id=kimi_provider_id,
                role="queen",
                locality="remote",
                required=True,
                api_key_envs=_KIMI_API_KEY_ENV_KEYS,
                configured=configured,
                availability_state=kimi_availability,
                notes="Remote reasoning/synthesis lane.",
            )
        )
        if not configured:
            reasons.append(f"hybrid-kimi needs {_KIMI_API_KEY_REASON} before the remote queen lane is usable.")
    elif profile_id == "hybrid-tether":
        configured = (
            _has_any_env(env, *_TETHER_API_KEY_ENV_KEYS) and _has_any_env(env, *_TETHER_BASE_URL_ENV_KEYS)
        ) or tether_availability != "unregistered"
        providers.append(
            InstallProfileProvider(
                provider_id=tether_provider_id,
                role="queen",
                locality="remote",
                required=True,
                api_key_envs=(*_TETHER_API_KEY_ENV_KEYS, *_TETHER_BASE_URL_ENV_KEYS),
                configured=configured,
                availability_state=tether_availability,
                notes="Remote reasoning/synthesis lane via a user-managed Tether endpoint.",
            )
        )
        if not configured:
            reasons.append(f"hybrid-tether needs {_TETHER_CONFIG_REASON} before the remote queen lane is usable.")
    elif profile_id == "hybrid-fallback":
        configured = _has_any_env(env, *_GENERIC_REMOTE_API_KEY_ENV_KEYS) or fallback_availability != "unregistered"
        providers.append(
            InstallProfileProvider(
                provider_id=fallback_provider_id,
                role="queen",
                locality="remote",
                required=True,
                api_key_envs=_GENERIC_REMOTE_API_KEY_ENV_KEYS,
                configured=configured,
                availability_state=fallback_availability,
                notes="Remote fallback lane for when local quality or availability is insufficient.",
            )
        )
        if not configured:
            reasons.append(f"hybrid-fallback needs {_GENERIC_REMOTE_API_KEY_REASON}.")
    elif profile_id == "full-orchestrated":
        kimi_configured = _has_any_env(env, *_KIMI_API_KEY_ENV_KEYS) or kimi_availability != "unregistered"
        fallback_configured = _has_any_env(env, *_GENERIC_REMOTE_API_KEY_ENV_KEYS) or fallback_availability != "unregistered"
        providers.extend(
            [
                InstallProfileProvider(
                    provider_id=kimi_provider_id,
                    role="queen",
                    locality="remote",
                    required=True,
                    api_key_envs=_KIMI_API_KEY_ENV_KEYS,
                    configured=kimi_configured,
                    availability_state=kimi_availability,
                    notes="Primary remote synthesis lane.",
                ),
                InstallProfileProvider(
                    provider_id=fallback_provider_id,
                    role="researcher",
                    locality="remote",
                    required=True,
                    api_key_envs=_GENERIC_REMOTE_API_KEY_ENV_KEYS,
                    configured=fallback_configured,
                    availability_state=fallback_availability,
                    notes="Remote fallback/research lane.",
                ),
            ]
        )
        if not kimi_configured:
            reasons.append(f"full-orchestrated needs {_KIMI_API_KEY_REASON} for the queen lane.")
        if not fallback_configured:
            reasons.append(f"full-orchestrated needs {_GENERIC_REMOTE_API_KEY_REASON} for the remote fallback lane.")

    return tuple(providers), reasons


def _expose_installed_ollama_lanes(env: Mapping[str, str]) -> bool:
    return env_flag_enabled(env, "VOOL_REGISTER_INSTALLED_OLLAMA_MODELS", default=False)


def _explicit_installed_ollama_override_tags(env: Mapping[str, str]) -> set[str] | None:
    if _INSTALLED_OLLAMA_MODELS_ENV_KEY not in env:
        return None
    raw_override = str(env.get(_INSTALLED_OLLAMA_MODELS_ENV_KEY) or "").strip()
    if raw_override.startswith("["):
        try:
            payload = json.loads(raw_override)
        except Exception:
            payload = []
        if isinstance(payload, list):
            return {str(item).strip().lower() for item in payload if str(item).strip()}
        return set()
    return {part.strip().lower() for part in raw_override.split(",") if part.strip()}


def _append_installed_ollama_lanes(
    providers: list[InstallProfileProvider],
    *,
    provider_capability_truth: tuple[ProviderCapabilityTruth, ...],
    primary_local_model: str,
    env: Mapping[str, str],
) -> None:
    explicit_tags = _explicit_installed_ollama_override_tags(env)
    seen = {str(item.provider_id or "").strip().lower() for item in providers if str(item.provider_id or "").strip()}
    for item in provider_capability_truth:
        provider_id = str(item.provider_id or "").strip()
        if not provider_id or provider_id.lower() in seen:
            continue
        if item.locality != "local" or not provider_id.lower().startswith("ollama-local:"):
            continue
        model_tag = str(item.model_id or provider_id.split(":", 1)[1]).strip().lower()
        if explicit_tags is not None and model_tag not in explicit_tags:
            continue
        role = installed_ollama_role_for_model(
            model_name=str(item.model_id or ""),
            primary_model=primary_local_model,
        )
        providers.append(
            InstallProfileProvider(
                provider_id=provider_id,
                role=role,
                locality="local",
                required=False,
                configured=True,
                availability_state=_provider_availability_state(
                    provider_id,
                    {entry.provider_id: entry for entry in provider_capability_truth if entry.provider_id},
                ),
                notes="Optional installed Ollama lane exposed for local model orchestration.",
            )
        )
        seen.add(provider_id.lower())


def _find_primary_local_provider_id(
    provider_capability_truth: tuple[ProviderCapabilityTruth, ...],
    *,
    model_tag: str,
) -> str:
    candidates = [item for item in provider_capability_truth if item.locality == "local"]
    if not candidates:
        return f"ollama-local:{model_tag}"
    ollama_candidates = [item for item in candidates if item.provider_id.lower().startswith("ollama-local:")]
    if ollama_candidates:
        ollama_candidates.sort(
            key=lambda item: (
                _availability_rank(item.availability_state),
                1 if str(item.model_id or "").strip().lower() == str(model_tag or "").strip().lower() else 0,
                1 if item.role_fit == "coder" else 0,
                -float(item.queue_depth) / float(max(1, item.max_safe_concurrency)),
            ),
            reverse=True,
        )
        return ollama_candidates[0].provider_id
    candidates.sort(
        key=lambda item: (
            _availability_rank(item.availability_state),
            1 if item.role_fit == "coder" else 0,
            -float(item.queue_depth) / float(max(1, item.max_safe_concurrency)),
        ),
        reverse=True,
    )
    return candidates[0].provider_id


def _selected_role_models_for_profile(
    *,
    profile_id: str,
    recommended_bundle: Any,
    legacy_mode: bool,
) -> tuple[Any, ...]:
    role_models = tuple(recommended_bundle.role_models)
    if not role_models:
        return tuple()
    if legacy_mode and profile_id in {"local-max", "full-orchestrated"}:
        return (type(role_models[0])(role="coding", model=recommended_bundle.primary_model),)
    return role_models


def _with_profile_bundle(profile_id: str, bundle_recommendation: Any) -> Any:
    if profile_id != "goblin-stack":
        return bundle_recommendation
    goblin = bundle_spec("goblin_stack")
    return replace(
        bundle_recommendation,
        recommended_bundle=goblin,
        safe_disk_floor_gb=safe_disk_floor_gb(goblin.models),
    )


def _optional_models_for_profile(profile_id: str) -> tuple[str, ...]:
    if profile_id == "goblin-stack":
        return ("qwen3:30b-a3b", "qwen3:14b")
    return tuple()


def _provider_mix_role_for_bundle_role(
    bundle_role: str,
    *,
    profile_id: str,
    legacy_mode: bool,
) -> str:
    clean_role = str(bundle_role or "").strip().lower()
    if legacy_mode and profile_id in {"local-max", "full-orchestrated"} and clean_role == "coding":
        return "coder"
    return clean_role


def _find_local_provider_id_for_model(
    provider_capability_truth: tuple[ProviderCapabilityTruth, ...],
    *,
    model_name: str,
    fallback_prefix: str,
) -> str:
    clean_model = str(model_name or "").strip().lower()
    for item in provider_capability_truth:
        if item.locality != "local":
            continue
        if str(item.model_id or "").strip().lower() != clean_model:
            continue
        return item.provider_id
    clean_prefix = str(fallback_prefix or "").strip()
    return f"{clean_prefix}:{model_name}" if clean_prefix else clean_model


def _find_secondary_local_provider_id(
    provider_capability_truth: tuple[ProviderCapabilityTruth, ...],
    *,
    primary_provider_id: str,
    env: Mapping[str, str],
) -> str:
    primary_capability = next(
        (item for item in provider_capability_truth if item.provider_id == primary_provider_id),
        None,
    )
    preferred_provider_id = preferred_secondary_local_provider_id(env)
    candidates = [
        item
        for item in provider_capability_truth
        if item.locality == "local"
        and item.provider_id != primary_provider_id
        and item.provider_id.lower() == preferred_provider_id.lower()
    ]
    if not candidates:
        candidates = [
            item for item in provider_capability_truth if item.locality == "local" and item.provider_id != primary_provider_id
        ]
    if not candidates:
        return ""
    candidates.sort(
        key=lambda item: (
            _availability_rank(item.availability_state),
            1 if item.role_fit == "verifier" else 0,
            2
            if item.provider_id.lower().startswith("llamacpp-local:")
            else 1
            if item.provider_id.lower().startswith("vllm-local:")
            else 0,
            -float(item.queue_depth) / float(max(1, item.max_safe_concurrency)),
        ),
        reverse=True,
    )
    best_candidate = candidates[0]
    if primary_capability is not None and _availability_rank(best_candidate.availability_state) < _availability_rank(
        primary_capability.availability_state
    ):
        return ""
    if _availability_rank(best_candidate.availability_state) < _availability_rank("degraded"):
        return ""
    return best_candidate.provider_id


def _find_remote_provider_id(
    provider_capability_truth: tuple[ProviderCapabilityTruth, ...],
    *,
    hint: str | None,
    exclude: set[str | None] | None = None,
) -> str:
    excluded = {item for item in list(exclude or set()) if item}
    remote_candidates = [
        item
        for item in provider_capability_truth
        if item.locality == "remote" and item.provider_id not in excluded
    ]
    if hint:
        hinted = [item for item in remote_candidates if hint in item.provider_id.lower()]
        if hinted:
            hinted.sort(
                key=lambda item: (
                    _availability_rank(item.availability_state),
                    1 if item.role_fit == "queen" else 0,
                    -float(item.queue_depth) / float(max(1, item.max_safe_concurrency)),
                ),
                reverse=True,
            )
            return hinted[0].provider_id
        if hint == "kimi":
            return "kimi-remote"
        if hint == "tether":
            return "tether-remote"
    if remote_candidates:
        remote_candidates.sort(
            key=lambda item: (
                _availability_rank(item.availability_state),
                1 if item.role_fit == "queen" else 0,
                -float(item.queue_depth) / float(max(1, item.max_safe_concurrency)),
            ),
            reverse=True,
        )
        return remote_candidates[0].provider_id
    return "openai-compatible-remote"


def _provider_prefix_registered(
    provider_capability_truth: tuple[ProviderCapabilityTruth, ...],
    prefix: str,
) -> bool:
    lowered = str(prefix or "").strip().lower()
    if not lowered:
        return False
    return any(str(item.provider_id).lower().startswith(lowered) for item in provider_capability_truth)


def _provider_truth_index(
    provider_capability_truth: tuple[ProviderCapabilityTruth, ...],
) -> dict[str, ProviderCapabilityTruth]:
    return {item.provider_id: item for item in provider_capability_truth if item.provider_id}


def _provider_availability_state(
    provider_id: str,
    truth_index: Mapping[str, ProviderCapabilityTruth],
) -> str:
    if not provider_id:
        return "unregistered"
    capability = truth_index.get(provider_id)
    if capability is None:
        return "unregistered"
    return str(capability.availability_state or "unregistered")


def _availability_rank(state: str) -> int:
    return {
        "ready": 3,
        "degraded": 2,
        "blocked": 1,
        "unregistered": 0,
    }.get(str(state or "").strip().lower(), 0)


def _volume_checks(
    *,
    runtime_home: str | Path | None,
    env: Mapping[str, str],
    runtime_required_gb: float,
    ollama_required_gb: float,
) -> tuple[InstallProfileVolumeCheck, ...]:
    runtime_target = Path(runtime_home).expanduser().resolve() if runtime_home else (Path.home() / ".vool_runtime").resolve()
    ollama_target = default_ollama_models_path(env)
    allocations = [
        ("runtime_home", runtime_target, runtime_required_gb),
        ("ollama_models", ollama_target, ollama_required_gb),
    ]
    grouped: dict[str, dict[str, Any]] = {}
    for label, path, required_gb in allocations:
        existing_path = _nearest_existing_path(path)
        volume_id = _volume_id(existing_path)
        entry = grouped.setdefault(
            volume_id,
            {
                "labels": [],
                "path": str(existing_path),
                "required_gb": 0.0,
                "free_gb": _disk_free_gb(existing_path),
            },
        )
        entry["labels"].append(label)
        entry["required_gb"] += float(required_gb)
    checks = [
        InstallProfileVolumeCheck(
            volume_id=volume_id,
            labels=tuple(sorted(entry["labels"])),
            path=str(entry["path"]),
            required_gb=round(float(entry["required_gb"]), 1),
            free_gb=round(float(entry["free_gb"]), 1),
            ok=float(entry["free_gb"]) >= float(entry["required_gb"]),
        )
        for volume_id, entry in sorted(grouped.items())
    ]
    return tuple(checks)


def _disk_free_gb(path: Path) -> float:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return 0.0
    return float(usage.free) / (1024.0 ** 3)


def _nearest_existing_path(path: Path) -> Path:
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    if current.exists():
        return current.resolve()
    return Path.home().resolve()


def _path_target_available(path: Path) -> bool:
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    return current.exists()


def _windows_ollama_models_env_paths() -> tuple[Path, ...]:
    if platform.system().lower() != "windows":
        return ()
    try:
        import winreg  # type: ignore
    except Exception:
        return ()
    paths: list[Path] = []
    locations = (
        (winreg.HKEY_CURRENT_USER, "Environment"),
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
    )
    for hive, subkey in locations:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                raw, _value_type = winreg.QueryValueEx(key, "OLLAMA_MODELS")
        except OSError:
            continue
        value = str(raw or "").strip()
        if value:
            paths.append(Path(value).expanduser())
    return tuple(paths)


def _volume_id(path: Path) -> str:
    anchor = path.anchor.lower() or "/"
    try:
        stat = path.stat()
        return f"{anchor}:{stat.st_dev}"
    except OSError:
        return anchor


def _profile_label(profile_id: str) -> str:
    return {
        "auto-recommended": "Auto recommended",
        "local-only": "Local only",
        "local-max": "Local max",
        "goblin-stack": "Goblin stack",
        "hybrid-kimi": "Hybrid Kimi",
        "hybrid-tether": "Hybrid Tether",
        "hybrid-fallback": "Hybrid fallback",
        "full-orchestrated": "Full orchestrated",
    }[profile_id]


def _profile_summary(profile_id: str) -> str:
    return {
        "auto-recommended": "Choose the strongest honest profile from current hardware and configured providers.",
        "local-only": "Pure local Ollama bundle with no remote provider dependency; may be single, dual, or triple depending on hardware.",
        "local-max": "Local Ollama bundle plus an explicit llama.cpp verifier/coding lane.",
        "goblin-stack": "Qwen3-family local stack with tiny routing, daily workhorse, and hybrid-MoE deep lane.",
        "hybrid-kimi": "Local coding lane plus a remote Kimi synthesis lane.",
        "hybrid-tether": "Local coding lane plus a remote Tether synthesis lane.",
        "hybrid-fallback": "Local coding lane plus a generic remote fallback lane.",
        "full-orchestrated": "Local coding/verifier lanes plus remote synthesis and fallback lanes.",
    }[profile_id]


def _estimate_model_storage_gb(model_tag: str) -> float:
    clean = str(model_tag or "").strip().lower()
    if clean in _MODEL_SIZE_GB:
        return _MODEL_SIZE_GB[clean]
    return model_storage_gb(clean)


def _has_any_env(env: Mapping[str, str], *keys: str) -> bool:
    return any(str(env.get(key) or "").strip() for key in keys)


def _installed_ollama_model_tags(
    *,
    provider_capability_truth: tuple[ProviderCapabilityTruth, ...],
    env: Mapping[str, str],
) -> set[str]:
    tags: set[str] = set()
    override_present = _INSTALLED_OLLAMA_MODELS_ENV_KEY in env
    raw_override = str(env.get(_INSTALLED_OLLAMA_MODELS_ENV_KEY) or "").strip()
    if override_present:
        if raw_override.startswith("["):
            try:
                payload = json.loads(raw_override)
            except Exception:
                payload = []
            if isinstance(payload, list):
                tags.update(str(item).strip().lower() for item in payload if str(item).strip())
        elif raw_override:
            tags.update(part.strip().lower() for part in raw_override.split(",") if part.strip())
    for item in provider_capability_truth:
        provider_id = str(item.provider_id or "").strip()
        if provider_id.lower().startswith("ollama-local:"):
            tags.add(provider_id.split(":", 1)[1].strip().lower())
            model_id = str(item.model_id or "").strip()
            if model_id:
                tags.add(model_id.lower())
    manifest_root = (default_ollama_models_path(env) / "manifests").resolve()
    if manifest_root.exists():
        for manifest_path in manifest_root.glob("**/*"):
            if not manifest_path.is_file():
                continue
            try:
                relative = manifest_path.relative_to(manifest_root)
            except Exception:
                continue
            parts = relative.parts
            if len(parts) < 2:
                continue
            tags.add(f"{parts[-2]}:{parts[-1]}".lower())
        if tags:
            return tags
    # Ask Ollama's HTTP API for installed models instead of shelling out to `ollama list`.
    # `subprocess.run([ollama, "list"], capture_output=True, timeout=15)` DEADLOCKS on Windows
    # whenever the long-running Ollama server has inherited the child's stdout pipe handle: the
    # `ollama list` child exits, but communicate() blocks FOREVER joining the pipe-reader threads
    # because the pipe never reaches EOF (the server still holds the write end). The `timeout`
    # only bounds the child process, not the pipe drain — so install-time hardware detection froze
    # for hours here (confirmed live via py-spy at step 6). The /api/tags probe is wall-clock
    # bounded (2s) and inherits no pipe, so it can never hang.
    try:
        model_names = installed_ollama_model_names(env=env)
    except Exception:
        return tags  # best-effort inventory: an unreachable Ollama just means "no extra tags"
    for model_name in model_names:
        clean = str(model_name or "").strip().lower()
        if clean:
            tags.add(clean)
    return tags


__all__ = [
    "INSTALL_PROFILE_CHOICES",
    "PUBLIC_INSTALL_PROFILE_CHOICES",
    "InstallProfileProvider",
    "InstallProfileTruth",
    "InstallProfileVolumeCheck",
    "active_install_profile_id",
    "build_install_profile_truth",
    "default_ollama_models_path",
    "format_install_profile_id",
    "install_profile_display_choices",
    "install_profile_runs_local_only",
    "installed_profile_id",
    "installed_profile_selected_model",
    "normalize_install_profile_id",
    "persist_install_profile_record",
    "preferred_install_profile_id",
    "required_ollama_models_for_profile",
]
