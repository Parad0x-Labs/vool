from __future__ import annotations

import importlib.util
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from core import policy_engine
from core.backend_acceleration_truth import backend_acceleration_proof
from core.feature_flags import flag_map
from core.hardware_tier import probe_machine, select_qwen_tier
from core.install_recommendations import build_install_recommendation_truth
from core.local_inference_autopilot import build_prefix_cache_plan
from core.local_inference_evidence import hydrate_capability_truth_with_benchmarks
from core.provider_env import merge_provider_env
from core.runtime_backbone import build_provider_registry_snapshot
from core.runtime_context import RuntimeContext, build_runtime_context
from core.runtime_install_profiles import build_install_profile_truth, install_profile_runs_local_only
from core.runtime_provider_defaults import default_runtime_model_tag, preferred_fast_local_model


@dataclass(frozen=True)
class RuntimeCapabilityStatus:
    name: str
    state: str
    category: str
    reason: str


def runtime_capability_statuses(context: RuntimeContext | None = None) -> list[RuntimeCapabilityStatus]:
    runtime = context or build_runtime_context(mode="runtime_capabilities")
    flags = flag_map()

    helper_mesh_state = "implemented" if runtime.feature_flags.helper_mesh_enabled else "disabled_by_policy"
    helper_mesh_reason = (
        "Helper coordination lanes are enabled for this runtime."
        if runtime.feature_flags.helper_mesh_enabled
        else "Helper coordination is disabled by runtime policy for this process."
    )
    public_hive_state = "implemented" if runtime.feature_flags.public_hive_enabled else "disabled_by_policy"
    public_hive_reason = (
        "Public/operator Hive surfaces are enabled for this runtime. Write auth truth is exposed separately."
        if runtime.feature_flags.public_hive_enabled
        else "Public Hive surfaces are disabled by runtime policy for this process."
    )
    workspace_write_state = "implemented" if runtime.feature_flags.allow_workspace_writes else "disabled_by_policy"
    sandbox_state = "implemented" if runtime.feature_flags.allow_sandbox_execution else "disabled_by_policy"
    remote_only_state = "implemented" if runtime.feature_flags.allow_remote_only_without_backend else "disabled_by_policy"

    return [
        RuntimeCapabilityStatus(
            name="local_runtime",
            state="implemented",
            category="core",
            reason=flags["LOCAL_STANDALONE"].reason,
        ),
        RuntimeCapabilityStatus(
            name="memory_and_tools",
            state="implemented",
            category="core",
            reason="VOOL can keep context, route tool intent, and execute bounded local/runtime tools.",
        ),
        RuntimeCapabilityStatus(
            name="helper_mesh",
            state=helper_mesh_state,
            category="helper_network",
            reason=helper_mesh_reason,
        ),
        RuntimeCapabilityStatus(
            name="public_hive_surface",
            state=public_hive_state,
            category="surface",
            reason=public_hive_reason,
        ),
        RuntimeCapabilityStatus(
            name="workspace_write_tools",
            state=workspace_write_state,
            category="tooling",
            reason=(
                "Workspace write tools are enabled by runtime policy."
                if runtime.feature_flags.allow_workspace_writes
                else "Workspace write tools are disabled by runtime policy."
            ),
        ),
        RuntimeCapabilityStatus(
            name="sandbox_execution",
            state=sandbox_state,
            category="tooling",
            reason=(
                "Sandboxed command execution is enabled by runtime policy."
                if runtime.feature_flags.allow_sandbox_execution
                else "Sandboxed command execution is disabled by runtime policy."
            ),
        ),
        RuntimeCapabilityStatus(
            name="remote_only_backend_fallback",
            state=remote_only_state,
            category="model_backend",
            reason=(
                "Runtime may stay alive without a healthy local backend and fall back to remote-only behavior."
                if runtime.feature_flags.allow_remote_only_without_backend
                else "Runtime requires a healthy backend and will fail closed if none is available."
            ),
        ),
        RuntimeCapabilityStatus(
            name="simulated_payments",
            state=flags["SIMULATED_PAYMENTS"].state,
            category="future_extension",
            reason=flags["SIMULATED_PAYMENTS"].reason,
        ),
        RuntimeCapabilityStatus(
            name="wan_public_mesh",
            state=flags["EXPERIMENTAL_WAN"].state,
            category="future_extension",
            reason=flags["EXPERIMENTAL_WAN"].reason,
        ),
    ]


def runtime_capability_snapshot(context: RuntimeContext | None = None) -> dict[str, Any]:
    runtime = context or build_runtime_context(mode="runtime_capabilities")
    probe = probe_machine()
    tier = select_qwen_tier(probe)
    provider_snapshot = build_provider_registry_snapshot(
        runtime_home=str(runtime.paths.runtime_home),
        requested_profile=str(os.environ.get("VOOL_INSTALL_PROFILE") or ""),
        honor_install_profile=True,
    )
    provider_env = merge_provider_env(runtime.paths.runtime_home, env=os.environ)
    provider_capability_truth = hydrate_capability_truth_with_benchmarks(provider_snapshot.capability_truth)
    install_profile = build_install_profile_truth(
        probe=probe,
        tier=tier,
        provider_capability_truth=provider_capability_truth,
        runtime_home=runtime.paths.runtime_home,
    )
    install_recommendation = build_install_recommendation_truth(
        probe=probe,
        tier=tier,
        selected_model=install_profile.selected_model,
        runtime_home=runtime.paths.runtime_home,
    )
    effective_local_only_mode = bool(runtime.feature_flags.local_only_mode) or install_profile_runs_local_only(
        install_profile.profile_id
    )
    statuses = runtime_capability_statuses(runtime)
    active_remote_fallback_available = (
        bool(runtime.feature_flags.allow_remote_only_without_backend)
        and not effective_local_only_mode
        and any(
            item.locality == "remote" for item in provider_capability_truth
        )
    )
    capabilities_payload = [asdict(item) for item in statuses]
    if not active_remote_fallback_available:
        for row in capabilities_payload:
            if str(row.get("name") or "").strip() != "remote_only_backend_fallback":
                continue
            if str(row.get("state") or "").strip() == "disabled_by_policy":
                break
            row["state"] = "disabled_by_profile"
            row["reason"] = "Current install profile exposes no active remote model lane, so remote-only fallback is unavailable."
            break
    return {
        "mode": runtime.mode,
        "runtime_home": str(runtime.paths.runtime_home),
        "workspace_root": str(runtime.paths.workspace_root),
        "feature_flags": {
            "local_only_mode": effective_local_only_mode,
            "public_hive_enabled": runtime.feature_flags.public_hive_enabled,
            "helper_mesh_enabled": runtime.feature_flags.helper_mesh_enabled,
            "allow_workspace_writes": runtime.feature_flags.allow_workspace_writes,
            "allow_sandbox_execution": runtime.feature_flags.allow_sandbox_execution,
            "allow_remote_only_without_backend": active_remote_fallback_available,
        },
        "provider_capability_truth": [item.to_dict() for item in provider_capability_truth],
        "install_profile": install_profile.to_dict(),
        "install_recommendation": install_recommendation.to_dict(),
        "gpu_driver_compat": _gpu_driver_compat_capability(probe),
        "browser_tools": _browser_tools_capability(),
        "workspace_access": _workspace_access_capability(runtime),
        "compaction_effective_config": _compaction_effective_config(),
        "backend_kv_cache": _backend_kv_cache_capability(provider_capability_truth, provider_env),
        "speculative_decoding": _speculative_decoding_capability(provider_capability_truth, provider_env),
        "eagle_status": _eagle_capability(provider_capability_truth, provider_env),
        "model_lane_defaults": _model_lane_defaults_capability(),
        "capabilities": capabilities_payload,
    }


def _gpu_driver_compat_capability(probe: Any) -> dict[str, Any]:
    """Static NVIDIA driver / compute-capability verdict for the CUDA lane (surfaced in /status)."""
    from core.gpu_driver_compat import evaluate_driver_compat_for_probe, gpu_driver_compat_advisory

    verdict = evaluate_driver_compat_for_probe(probe)
    record = verdict.to_dict()
    record["advisory"] = gpu_driver_compat_advisory(verdict)
    return record


_WEB_OPT_IN_REASON = (
    "Live web lookup is opt-in and OFF in the local-only profile. "
    "To enable it, run a non-local-only profile (and/or set VOOL_ENABLE_WEB=1 when not local-only). "
    "In local-only the no-egress guarantee overrides the opt-in, so VOOL_ENABLE_WEB=1 alone will not turn it on."
)


def _browser_tools_capability() -> dict[str, Any]:
    # EFFECTIVE, not ambient. `allow_web_fallback` is a machine-wide default; it is
    # not the answer to "can this turn reach the web?". Under Local Only the
    # canonical door refuses the socket whatever that flag says, so reporting the
    # flag told the operator they had web access the runtime would refuse. The
    # reason string below has always said so -- this is the code catching up to it.
    from core.remote_fetch_policy import effective_web_available

    web_enabled = bool(effective_web_available())
    playwright_enabled = bool(policy_engine.playwright_enabled()) or str(os.environ.get("PLAYWRIGHT_ENABLED", "")).lower() in {"1", "true", "yes"}
    playwright_installed = importlib.util.find_spec("playwright") is not None
    browser_status = (
        "disabled_by_policy"
        if not web_enabled or not playwright_enabled
        else "ok"
        if playwright_installed
        else "missing_dependency"
    )
    web_fetch: dict[str, Any] = {
        "supported": web_enabled,
        "status": "ok" if web_enabled else "disabled_by_policy",
        "policy_flag": "system.allow_web_fallback",
        "max_fetch_bytes": policy_engine.max_fetch_bytes(),
    }
    browser_render: dict[str, Any] = {
        "supported": bool(policy_engine.browser_runtime_enabled()),
        "status": browser_status,
        "policy_flag": "web.playwright_enabled",
        "engine": policy_engine.browser_engine(),
        "playwright_installed": playwright_installed,
    }
    if not web_enabled:
        web_fetch["unsupported_reason"] = _WEB_OPT_IN_REASON
        browser_render["unsupported_reason"] = _WEB_OPT_IN_REASON
    return {
        "web_fetch": web_fetch,
        "browser_render": browser_render,
    }


def _workspace_access_capability(runtime: RuntimeContext) -> dict[str, Any]:
    return {
        "workspace_tools": {
            "status": "ok" if runtime.feature_flags.allow_workspace_writes or policy_engine.get("filesystem.allow_read_workspace", True) else "disabled_by_policy",
            "read": bool(policy_engine.get("filesystem.allow_read_workspace", True)),
            "write": bool(runtime.feature_flags.allow_workspace_writes),
            "workspace_root": str(runtime.paths.workspace_root),
        },
        "machine_tools": {
            "status": "restricted",
            "read_roots": ["~/Desktop", "~/Downloads", "~/Documents"],
            "note": "Host machine reads stay restricted; repo/project paths use workspace tools.",
        },
    }


def _compaction_effective_config() -> dict[str, Any]:
    path = Path(os.environ.get("OPENCLAW_CONFIG_PATH") or Path.home() / ".openclaw" / "openclaw.json")
    base = {
        "status": "missing",
        "config_source": str(path),
        "reserveTokensFloor": None,
        "keepRecentTokens": None,
        "mode": "",
        "can_recover": False,
    }
    if not path.exists():
        return base
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        compaction = (
            dict(payload.get("agents") or {})
            .get("defaults", {})
            .get("compaction", {})
        )
        if not isinstance(compaction, dict):
            return {**base, "status": "not_configured"}
        reserve = compaction.get("reserveTokensFloor")
        keep_recent = compaction.get("keepRecentTokens")
        reserve_int = _optional_int(reserve)
        keep_recent_int = _optional_int(keep_recent)
        return {
            **base,
            "status": "configured",
            "reserveTokensFloor": reserve_int if reserve_int is not None else reserve,
            "keepRecentTokens": keep_recent_int if keep_recent_int is not None else keep_recent,
            "mode": str(compaction.get("mode") or "").strip(),
            "can_recover": int(reserve_int or 0) >= 20_000,
        }
    except Exception as exc:
        return {**base, "status": "unreadable", "error": str(exc)}


def _optional_int(value: object) -> int | None:
    text = str(value or "").strip()
    if not text.isdigit():
        return None
    return int(text)


def _backend_family(provider_id: str, model_id: str) -> str:
    lowered = f"{provider_id}:{model_id}".lower()
    if "llama" in lowered and "cpp" in lowered:
        return "llama.cpp"
    if "mlx" in lowered:
        return "mlx"
    if "vllm" in lowered:
        return "vllm"
    if "ollama" in lowered:
        return "ollama"
    return "unknown"


def _backend_kv_cache_capability(provider_capability_truth: tuple[Any, ...], provider_env: dict[str, str] | None = None) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in provider_capability_truth:
        provider_id = str(getattr(item, "provider_id", "") or "").strip()
        model_id = str(getattr(item, "model_id", "") or "").strip()
        backend = _backend_family(provider_id, model_id)
        if backend in seen:
            continue
        seen.add(backend)
        plan = build_prefix_cache_plan(stable_prefix_hash="runtime-capability", backend=backend)
        acceleration = backend_acceleration_proof(
            provider_id=provider_id,
            model_id=model_id,
            backend=backend,
            env=provider_env,
            probe=backend == "llama.cpp",
        )
        state = "not_active"
        if backend == "ollama":
            reason = "ollama=not_supported_keep_alive_only"
        elif backend == "llama.cpp":
            state = "active" if acceleration.kv_cache_status.endswith("cache_active") else "supported_not_active"
            reason = acceleration.kv_cache_status
        elif plan.supported:
            state = "supported_not_active"
            reason = f"{backend}={plan.action}"
        else:
            reason = f"{backend}=unsupported"
        rows.append(
            {
                "backend": backend,
                "status": state,
                "reason": reason,
                "provider_id": provider_id,
                "proof": acceleration.backend_cache_proof,
            }
        )
    if not rows:
        rows.append({"backend": "unknown", "status": "unsupported", "reason": "unknown=unsupported", "provider_id": ""})
    status = "active" if any(str(row.get("status") or "") == "active" for row in rows) else "not_active"
    return {"status": status, "rows": rows}


def _speculative_decoding_capability(provider_capability_truth: tuple[Any, ...], provider_env: dict[str, str] | None = None) -> dict[str, Any]:
    for item in provider_capability_truth:
        provider_id = str(getattr(item, "provider_id", "") or "").strip()
        model_id = str(getattr(item, "model_id", "") or "").strip()
        backend = _backend_family(provider_id, model_id)
        if backend != "llama.cpp":
            continue
        acceleration = backend_acceleration_proof(
            provider_id=provider_id,
            model_id=model_id,
            backend=backend,
            env=provider_env,
            probe=True,
        )
        if acceleration.speculative_status == "active":
            return {
                "status": "active",
                "backend": "llama.cpp",
                "provider_id": provider_id,
                "proof": acceleration.speculative_proof,
            }
        return {
            "status": acceleration.speculative_status,
            "backend": "llama.cpp",
            "provider_id": provider_id,
            "proof": acceleration.speculative_proof,
            "reason": acceleration.speculative_proof.get("reason"),
        }
    supported_backend = any(
        _backend_family(str(getattr(item, "provider_id", "") or ""), str(getattr(item, "model_id", "") or ""))
        in {"llama.cpp", "vllm"}
        for item in provider_capability_truth
    )
    if supported_backend:
        return {"status": "supported_not_configured", "reason": "Supported backend is present, but no proven draft-model/speculative config is active."}
    return {"status": "inactive", "reason": "No configured backend has proven draft-model/speculative decoding activation."}


def _eagle_capability(provider_capability_truth: tuple[Any, ...], provider_env: dict[str, str] | None = None) -> dict[str, Any]:
    for item in provider_capability_truth:
        provider_id = str(getattr(item, "provider_id", "") or "").strip()
        model_id = str(getattr(item, "model_id", "") or "").strip()
        backend = _backend_family(provider_id, model_id)
        if backend != "llama.cpp":
            continue
        acceleration = backend_acceleration_proof(
            provider_id=provider_id,
            model_id=model_id,
            backend=backend,
            env=provider_env,
            probe=False,
        )
        return {
            "status": acceleration.eagle_status,
            "backend": backend,
            "provider_id": provider_id,
            "proof": acceleration.eagle_proof,
        }
    return {
        "status": "unsupported_by_backend",
        "reason": "No configured backend has proven an EAGLE draft-model lane.",
    }


def _model_lane_defaults_capability() -> dict[str, Any]:
    fast_model = preferred_fast_local_model()
    default_model = default_runtime_model_tag()
    return {
        "default_model": default_model,
        "fast_local_preferred_model": fast_model,
        "fast_local_installed": bool(fast_model),
        "no_think_default": True,
        "explicit_only_models": [
            {
                "model": "qwen3.5:35b-a3b",
                "reason": "blocked_as_default_due_to_measured_local_latency",
            }
        ],
    }
