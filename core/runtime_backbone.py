from __future__ import annotations

import contextlib
import os
import threading
from dataclasses import dataclass
from typing import Any

_TASK_POLL_STARTED = False
_TASK_POLL_LOCK = threading.Lock()


def run_update_check_once() -> None:
    """Run the update check through the ONE updater authority (core.updater).

    The legacy GitHub-releases checker this function used to call is retired from
    production (2026-09-01 amendment): there is one signed-manifest authority now.
    This seam remains so the existing background loop keeps a periodic pulse, and it
    mirrors the new authority's result into the legacy availability cache the in-chat
    offer reads. Best-effort and fail-safe, exactly as before.
    """
    import contextlib

    with contextlib.suppress(Exception):
        from core.updater.runtime import get_update_subsystem

        subsystem = get_update_subsystem()
        if subsystem is None or not subsystem.configured:
            return  # honestly unavailable: no key/feed configured — nothing to check
        subsystem.trigger_check()
        from core.self_update_state import clear_available, save_available

        availability = _legacy_availability_from_subsystem(subsystem)
        if availability is not None and availability.available:
            save_available(availability)
        else:
            clear_available()


def _legacy_availability_from_subsystem(subsystem):
    """Project the new authority's current offer into the legacy UpdateAvailability
    shape the in-chat offer consumes (a projection, never a second decision)."""
    from core.self_update_check import UpdateAvailability

    service = getattr(subsystem, "service", None)
    offer = service.current_offer() if service is not None else None
    if offer is None:
        return UpdateAvailability(installed_version=subsystem.installed_version, reason="no release information available")
    artifact = offer.decision.artifact
    return UpdateAvailability(
        installed_version=subsystem.installed_version,
        available=True,
        target_version=offer.manifest.version,
        changelog=[line for line in offer.manifest.notes.splitlines() if line.strip()][:8],
        asset_url=str(artifact.url) if artifact is not None else "",
        sha256_url="",
        reason="update available",
    )


def _web0_task_poll_loop() -> None:
    """Daemon thread: poll the order book and auto-claim tasks when capacity allows.

    Also hosts the periodic self-update check (24h-gated inside), so no extra thread is
    needed — it runs shortly after boot and roughly hourly thereafter.
    """
    import time
    loops = 0
    while True:
        time.sleep(30)
        loops += 1
        if loops % 120 == 1:  # ~ every hour; the network call itself is 24h-gated
            with contextlib.suppress(Exception):
                run_update_check_once()
        try:
            from core.helper_scheduler import HelperScheduler
            from core.order_book import global_order_book
            from network.signer import get_local_peer_id
            from storage.task_offer_store import claim_task_offer
            scheduler = HelperScheduler()
            if not scheduler.can_accept_mesh_task():
                continue
            offer = global_order_book.pop_best_offer()
            if offer is None:
                continue
            task_id = str(offer.offer_dict.get("task_id") or "").strip()
            if task_id:
                claim_task_offer(task_id, get_local_peer_id())
        except Exception:
            pass


def start_web0_background_workers() -> None:
    """Start background daemons once (idempotent)."""
    global _TASK_POLL_STARTED
    with _TASK_POLL_LOCK:
        if _TASK_POLL_STARTED:
            return
        _TASK_POLL_STARTED = True
    t = threading.Thread(target=_web0_task_poll_loop, daemon=True, name="web0-task-poll")
    t.start()

from core.backend_manager import BackendManager
from core.hardware_tier import MachineProbe, QwenTier, probe_machine, select_qwen_tier, tier_summary
from core.local_model_policy import local_models_enabled
from core.local_ollama_inventory import env_flag_enabled, installed_ollama_model_names, is_text_generation_ollama_model
from core.model_registry import ModelRegistry, ProviderAuditRow
from core.provider_env import merge_provider_env
from core.provider_routing import ProviderCapabilityTruth, provider_capability_truth_for_manifest
from core.runtime_bootstrap import BootstrappedRuntime, bootstrap_runtime_mode
from core.runtime_install_profiles import (
    InstallProfileTruth,
    active_install_profile_id,
    build_install_profile_truth,
    install_profile_runs_local_only,
)
from core.runtime_provider_defaults import ensure_default_runtime_providers


@dataclass(frozen=True)
class ProviderRegistrySnapshot:
    warnings: tuple[str, ...]
    audit_rows: tuple[ProviderAuditRow, ...]
    capability_truth: tuple[ProviderCapabilityTruth, ...]
    prewarm_results: tuple[dict[str, Any], ...] = tuple()


@dataclass(frozen=True)
class LocalModelProfile:
    probe: MachineProbe
    tier: QwenTier
    summary: dict[str, Any]


@dataclass(frozen=True)
class RuntimeBackbone:
    boot: BootstrappedRuntime
    local_model_profile: LocalModelProfile
    provider_snapshot: ProviderRegistrySnapshot
    install_profile: InstallProfileTruth


def build_provider_registry_snapshot(
    registry: ModelRegistry | None = None,
    *,
    model_tag: str | None = None,
    runtime_home: str | None = None,
    requested_profile: str | None = None,
    honor_install_profile: bool = False,
    run_prewarm: bool = False,
    env: dict[str, str] | None = None,
) -> ProviderRegistrySnapshot:
    active_registry = registry or ModelRegistry()
    env_map = merge_provider_env(runtime_home, env=os.environ if env is None else env)
    install_profile = ""
    if honor_install_profile:
        install_profile = (
            str(requested_profile or "").strip()
            or active_install_profile_id(runtime_home=runtime_home, env=env_map)
        )
    ensure_default_runtime_providers(
        active_registry,
        model_tag=model_tag,
        env=env_map,
        install_profile=install_profile,
        runtime_home=runtime_home,
    )
    manifests: tuple[Any, ...]
    try:
        manifests = tuple(active_registry.list_manifests(enabled_only=True))
    except Exception:
        manifests = tuple()
    warnings = tuple(active_registry.startup_warnings())
    audit_rows = tuple(active_registry.provider_audit_rows())
    capability_truth = tuple(provider_capability_truth_for_manifest(manifest) for manifest in manifests)
    manifests, audit_rows, capability_truth = _filter_snapshot_to_installed_ollama_inventory(
        manifests=manifests,
        audit_rows=audit_rows,
        capability_truth=capability_truth,
        env=env_map,
        runtime_home=runtime_home,
    )
    visible_provider_ids: tuple[str, ...] = tuple()
    if honor_install_profile:
        visible_provider_ids = _visible_provider_ids_for_install_profile(
            capability_truth=capability_truth,
            requested_profile=install_profile or None,
            selected_model=model_tag,
            runtime_home=runtime_home,
            env=env_map,
        )
        manifests, audit_rows, capability_truth = _filter_snapshot_to_provider_ids(
            manifests=manifests,
            audit_rows=audit_rows,
            capability_truth=capability_truth,
            provider_ids=visible_provider_ids,
        )
    prewarm_results: tuple[dict[str, Any], ...] = tuple()
    if run_prewarm:
        try:
            prewarm_results = tuple(active_registry.prewarm_enabled_providers(provider_ids=visible_provider_ids or None))
        except Exception:
            prewarm_results = tuple()
    # Web0 background workers (task poll loop) — start once at boot
    import contextlib
    with contextlib.suppress(Exception):
        start_web0_background_workers()
    # Web0 capability announce — fire-and-forget, no-op when gate is off
    try:
        from core.web0_capability_broadcast import (
            DEFAULT_PRICE_PER_TOKEN,
            announce_from_env,
            build_manifest,
            resolve_announced_price_usdc,
            resolve_privacy_mode,
        )
        top = max(capability_truth, key=lambda c: float(c.tokens_per_second or 0), default=None)
        _manifest = build_manifest(
            worker_id=str(env_map.get("VOOL_WORKER_ID") or "vool"),
            provider_ids=tuple(c.provider_id for c in capability_truth),
            top_tps=float(top.tokens_per_second or 0) if top else 0.0,
            top_tier=str(top.role_fit or "drone") if top else "drone",
            context_window=int(top.context_window or 32768) if top else 32768,
            tools=tuple(str(t) for t in (top.tool_support or ())) if top else (),
            # Advertise the node's REAL price + privacy posture, not the default.
            price_per_token_usdc=resolve_announced_price_usdc(env_map, default=DEFAULT_PRICE_PER_TOKEN),
            privacy_mode=resolve_privacy_mode(env_map),
        )
        announce_from_env(_manifest, env=env_map)
    except Exception:
        pass
    return ProviderRegistrySnapshot(
        warnings=warnings,
        audit_rows=audit_rows,
        capability_truth=capability_truth,
        prewarm_results=prewarm_results,
    )


def _filter_snapshot_to_installed_ollama_inventory(
    *,
    manifests: tuple[Any, ...],
    audit_rows: tuple[ProviderAuditRow, ...],
    capability_truth: tuple[ProviderCapabilityTruth, ...],
    env: dict[str, str],
    runtime_home: str | None = None,
) -> tuple[tuple[Any, ...], tuple[ProviderAuditRow, ...], tuple[ProviderCapabilityTruth, ...]]:
    if not env_flag_enabled(env, "VOOL_REGISTER_INSTALLED_OLLAMA_MODELS", default=False):
        return manifests, audit_rows, capability_truth
    # The canonical LocalModelPolicy: disabled means no Ollama discovery either — not even the
    # inventory read this filter exists to apply. Registration already emitted no local rows, so
    # there is nothing to filter; probing `/api/tags` would violate the policy for no gain.
    if not local_models_enabled(env=env, runtime_home=runtime_home):
        return manifests, audit_rows, capability_truth
    installed_tags = {tag.lower() for tag in installed_ollama_model_names(env=env)}
    if not installed_tags:
        return manifests, audit_rows, capability_truth
    visible_provider_ids = {
        item.provider_id
        for item in capability_truth
        if (
            not _is_local_ollama_capability(item)
            or (
                str(item.model_id or "").strip().lower() in installed_tags
                and is_text_generation_ollama_model(str(item.model_id or ""))
            )
        )
    }
    return _filter_snapshot_to_provider_ids(
        manifests=manifests,
        audit_rows=audit_rows,
        capability_truth=capability_truth,
        provider_ids=tuple(visible_provider_ids),
    )


def _visible_provider_ids_for_install_profile(
    *,
    capability_truth: tuple[ProviderCapabilityTruth, ...],
    requested_profile: str | None,
    selected_model: str | None = None,
    runtime_home: str | None,
    env: dict[str, str],
) -> tuple[str, ...]:
    if not capability_truth:
        return tuple()
    install_profile = build_install_profile_truth(
        requested_profile=requested_profile,
        selected_model=selected_model,
        provider_capability_truth=capability_truth,
        runtime_home=runtime_home,
        env=env,
    )
    profile_provider_ids = tuple(
        str(item.provider_id or "").strip()
        for item in install_profile.provider_mix
        if str(item.provider_id or "").strip()
    )
    if not install_profile_runs_local_only(install_profile.profile_id):
        return profile_provider_ids

    # A local-only profile is a remote-egress gate, but it must not erase the local
    # capability truth it is supposed to leave available. Registration and profile
    # resolution are separate passes; persisted bundle selection or a transiently
    # different hardware probe can make the profile name local model ids that are not
    # in the just-built registry snapshot. Keep the profile's narrower local mix when
    # it intersects reality. If it does not intersect at all, fall back to the actual
    # registered local rows. Remote rows are never admitted by either branch.
    capability_by_id = {
        str(item.provider_id or "").strip(): item
        for item in capability_truth
        if str(item.provider_id or "").strip()
    }
    matched_local_ids = tuple(
        provider_id
        for provider_id in profile_provider_ids
        if provider_id in capability_by_id and capability_by_id[provider_id].locality == "local"
    )
    if matched_local_ids:
        return matched_local_ids
    return tuple(
        str(item.provider_id or "").strip()
        for item in capability_truth
        if item.locality == "local" and str(item.provider_id or "").strip()
    )


def _is_local_ollama_capability(item: ProviderCapabilityTruth) -> bool:
    return item.locality == "local" and str(item.provider_id or "").strip().lower().startswith("ollama-local:")


def _filter_snapshot_to_provider_ids(
    *,
    manifests: tuple[Any, ...],
    audit_rows: tuple[ProviderAuditRow, ...],
    capability_truth: tuple[ProviderCapabilityTruth, ...],
    provider_ids: tuple[str, ...],
) -> tuple[tuple[Any, ...], tuple[ProviderAuditRow, ...], tuple[ProviderCapabilityTruth, ...]]:
    visible_provider_ids = {str(provider_id or "").strip() for provider_id in provider_ids if str(provider_id or "").strip()}
    if not visible_provider_ids:
        return tuple(), tuple(), tuple()
    filtered_manifests = tuple(item for item in manifests if str(getattr(item, "provider_id", "") or "").strip() in visible_provider_ids)
    filtered_audit_rows = tuple(item for item in audit_rows if str(item.provider_id or "").strip() in visible_provider_ids)
    filtered_capability_truth = tuple(item for item in capability_truth if str(item.provider_id or "").strip() in visible_provider_ids)
    return filtered_manifests, filtered_audit_rows, filtered_capability_truth


def build_runtime_backbone(
    *,
    mode: str,
    workspace_root: str | None = None,
    db_path: str | None = None,
    force_policy_reload: bool = False,
    configure_logging: bool = False,
    resolve_backend: bool = False,
    manager: BackendManager | None = None,
    allow_remote_only: bool | None = None,
    registry: ModelRegistry | None = None,
    machine_probe: MachineProbe | None = None,
) -> RuntimeBackbone:
    boot = bootstrap_runtime_mode(
        mode=mode,
        workspace_root=workspace_root,
        db_path=db_path,
        force_policy_reload=force_policy_reload,
        configure_logging=configure_logging,
        resolve_backend=resolve_backend,
        manager=manager,
        allow_remote_only=allow_remote_only,
    )
    probe = machine_probe or probe_machine()
    tier = select_qwen_tier(probe)
    summary = dict(tier_summary(probe))
    if boot.backend_selection is not None:
        summary["backend_name"] = boot.backend_selection.backend_name
        summary["backend_device"] = boot.backend_selection.device
        summary["backend_reason"] = boot.backend_selection.reason
    provider_snapshot = build_provider_registry_snapshot(
        registry,
        runtime_home=str(getattr(getattr(boot, "context", None), "paths", None).runtime_home)
        if getattr(getattr(boot, "context", None), "paths", None) is not None
        else None,
        honor_install_profile=True,
        run_prewarm=True,
    )
    install_profile = build_install_profile_truth(
        probe=probe,
        tier=tier,
        provider_capability_truth=provider_snapshot.capability_truth,
        runtime_home=getattr(getattr(boot, "context", None), "paths", None).runtime_home
        if getattr(getattr(boot, "context", None), "paths", None) is not None
        else None,
    )
    return RuntimeBackbone(
        boot=boot,
        local_model_profile=LocalModelProfile(
            probe=probe,
            tier=tier,
            summary=summary,
        ),
        provider_snapshot=provider_snapshot,
        install_profile=install_profile,
    )


__all__ = [
    "LocalModelProfile",
    "ProviderRegistrySnapshot",
    "RuntimeBackbone",
    "build_provider_registry_snapshot",
    "build_runtime_backbone",
    "run_update_check_once",
    "start_web0_background_workers",
]
