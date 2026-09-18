"""THE canonical policy for whether local (on-device) model providers may exist at all.

Before this module, "no local models" was three unrelated facts that each gated one seam and
nothing else: `VOOL_REGISTER_INSTALLED_OLLAMA_MODELS=0` only suppressed registering EXTRA
installed models (the bundle's `ollama-local:<tag>` lane registered regardless), and the
`config/local_models_disabled` marker file only killed boot prewarm and the intent arbiter. A
cloud-only launch therefore still auto-registered `ollama-local` as the default provider, and an
unpinned turn could silently run local Qwen. Discovery, registration, default alias, prewarm,
health probes and fallback each made their own decision.

Now they make ONE decision, here. Disabled means:

* no Ollama discovery (no `/api/tags`, `/api/ps` or installed-inventory read),
* no local provider registration (bundle lane, installed lanes, llama.cpp/vLLM/MLX aux lanes),
* no local manifest visible through the registry — including rows persisted by an earlier
  enabled run, so a restart cannot resurrect them (resident models never imply permission),
* no prewarm, no health probe, no routing candidate, no fallback target,
* an explicit local selection is refused with the typed reason `local_models_disabled` — never
  silently substituted, never quietly served.

Enabled preserves the prior behavior exactly.

Resolution order (fail-closed toward disabled):

1. the marker file `data/config/local_models_disabled` in the active VOOL home — the operator's
   standing hard kill-switch (OOM protection, 2026-08-28); it WINS over an enabling env value;
2. `VOOL_LOCAL_MODELS_ENABLED` with normalized boolean parsing — `0`, `false`, `off`, `no`,
   `disabled` disable; `1`, `true`, `on`, `yes`, `enabled` enable;
3. default: enabled.
"""
from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from storage.model_provider_manifest import (
    ModelProviderManifest,
    list_provider_manifests,
)

# The canonical env name. Internal identifiers stay VOOL_-prefixed (frozen contract,
# VOOL-DELIVERY/VOOL_MIGRATION.md §1).
LOCAL_MODELS_ENABLED_ENV = "VOOL_LOCAL_MODELS_ENABLED"
# Relative to the active VOOL home (the same path the two legacy checks used:
# `active_data_dir() / "config" / "local_models_disabled"`).
_MARKER_RELATIVE_PATH = Path("data") / "config" / "local_models_disabled"

LOCAL_MODELS_DISABLED_REASON = "local_models_disabled"

_DISABLE_VALUES = frozenset({"0", "false", "off", "no", "disabled"})


@dataclass(frozen=True)
class LocalModelPolicy:
    """The one decision every local-model seam reads. Immutable; resolve, never construct."""

    local_models_enabled: bool
    decided_by: str

    @property
    def local_models_disabled(self) -> bool:
        return not self.local_models_enabled

    def to_dict(self) -> dict[str, Any]:
        return {
            "local_models_enabled": bool(self.local_models_enabled),
            "decided_by": str(self.decided_by or "default"),
        }

    def refusal(self) -> dict[str, Any]:
        """The typed refusal payload every disabled-local seam reports, restated nowhere."""
        return {
            "reason": LOCAL_MODELS_DISABLED_REASON,
            "message": (
                "Local models are disabled on this runtime; "
                "no local provider may be discovered, registered or executed."
            ),
            "policy": self.to_dict(),
        }


def _marker_path(runtime_home: str | Path | None) -> Path | None:
    if runtime_home is not None:
        return (Path(runtime_home) / _MARKER_RELATIVE_PATH).resolve()
    try:
        from core.runtime_paths import active_vool_home

        return (active_vool_home() / _MARKER_RELATIVE_PATH).resolve()
    except Exception:
        return None


def _marker_key(marker: Path | None) -> tuple[str, ...]:
    if marker is None:
        return ("unknown",)
    try:
        stat = marker.stat()
    except OSError:
        return ("absent",)
    return ("present", str(stat.st_mtime_ns), str(stat.st_size))


def _persisted_env_raw(runtime_home: str | Path | None) -> str:
    """The value a persisted `config/provider-env.sh` carries for the policy env name.

    Boot merges that file into every env it hands the registration path (`merge_provider_env`);
    a policy that ignored it here would reach one verdict at registration and a different one at
    the registry listing, which is exactly the split this module exists to end. Process env wins
    over the persisted file, same as the merge.
    """
    try:
        from core.provider_env import load_provider_env_overrides

        home = runtime_home
        if home is None:
            from core.runtime_paths import active_vool_home

            home = active_vool_home()
        return str(load_provider_env_overrides(home).get(LOCAL_MODELS_ENABLED_ENV) or "")
    except Exception:
        return ""


def _resolve_uncached(
    *,
    env_raw: str,
    persisted_raw: str,
    home_key: str,
    marker_key: tuple[str, ...],
) -> LocalModelPolicy:
    if marker_key[0] == "present":
        return LocalModelPolicy(
            local_models_enabled=False,
            decided_by=f"marker:{_MARKER_RELATIVE_PATH.as_posix()}",
        )
    effective = env_raw.strip() or persisted_raw.strip()
    lowered = effective.lower()
    if lowered in _DISABLE_VALUES:
        return LocalModelPolicy(
            local_models_enabled=False,
            decided_by=f"env:{LOCAL_MODELS_ENABLED_ENV}={effective}",
        )
    if lowered in {"1", "true", "yes", "on", "enabled"}:
        return LocalModelPolicy(
            local_models_enabled=True,
            decided_by=f"env:{LOCAL_MODELS_ENABLED_ENV}={effective}",
        )
    # Unset, or an unrecognized value: fail-open to the prior default, never a silent disable.
    return LocalModelPolicy(local_models_enabled=True, decided_by="default")


# Resolved per call site in hot paths (registry listing runs on every ranked turn); the cache
# key carries every input — env raw value, home, marker stat, persisted-override content — so a
# monkeypatched env or a freshly written/deleted marker or provider-env.sh can never serve a
# stale verdict.
_POLICY_CACHE: dict[tuple[str, str, tuple[str, ...], str], LocalModelPolicy] = {}


def resolve_local_model_policy(
    *,
    env: dict[str, str] | Mapping | None = None,
    runtime_home: str | Path | None = None,
) -> LocalModelPolicy:
    env_map: dict[str, str] | Mapping = os.environ if env is None else env
    raw = str(env_map.get(LOCAL_MODELS_ENABLED_ENV) or "")
    marker = _marker_path(runtime_home)
    home_key = str(runtime_home or "")
    marker_stat = _marker_key(marker)
    # An explicitly passed env already carries the merged view its caller built; only the
    # no-argument call sites (the registry listing, the arbiter) consult the persisted file.
    persisted = "" if env is not None else _persisted_env_raw(runtime_home)
    key = (raw.strip().lower(), home_key, marker_stat, persisted.strip().lower())
    cached = _POLICY_CACHE.get(key)
    if cached is None:
        cached = _resolve_uncached(
            env_raw=raw,
            persisted_raw=persisted,
            home_key=home_key,
            marker_key=marker_stat,
        )
        _POLICY_CACHE[key] = cached
    return cached


def local_models_enabled(
    *,
    env: dict[str, str] | Mapping | None = None,
    runtime_home: str | Path | None = None,
) -> bool:
    return resolve_local_model_policy(env=env, runtime_home=runtime_home).local_models_enabled


# --------------------------------------------------------------------------------------
# Locality truth. This module owns the predicate so the registry (which cannot import
# `core.provider_routing` — that module imports the registry) and the routing layer share ONE
# derivation instead of two that can drift.
# --------------------------------------------------------------------------------------


def is_local_http_base_url(base_url: str) -> bool:
    """True when a manifest base_url points at this machine (loopback/unspecified/localhost)."""
    clean = str(base_url or "").strip().lower()
    if not clean:
        return False
    try:
        hostname = str(urlparse(clean).hostname or "").strip().lower()
    except Exception:
        return False
    if not hostname:
        return False
    if hostname == "localhost":
        return True
    try:
        return ip_address(hostname).is_loopback or ip_address(hostname).is_unspecified
    except ValueError:
        return False


def manifest_is_local(manifest: ModelProviderManifest) -> bool:
    """The ONE derivation of "this manifest runs on this machine".

    Same rule `provider_capability_truth_for_manifest` always applied — source type, loopback
    HTTP endpoint, or an explicit `deployment_class: local` — now stated once and imported by
    the routing layer, the registry view and this policy's filters.
    """
    deployment_class = str((manifest.metadata or {}).get("deployment_class") or "").strip().lower()
    return (
        manifest.source_type in {"local_path", "subprocess"}
        or is_local_http_base_url(str((manifest.runtime_config or {}).get("base_url") or ""))
        or deployment_class == "local"
    )


def filter_local_manifests_for_policy(
    manifests: Iterable[ModelProviderManifest],
    *,
    env: dict[str, str] | Mapping | None = None,
    runtime_home: str | Path | None = None,
) -> list[ModelProviderManifest]:
    """The registry's read seam: when disabled, no local manifest may leave this filter."""
    entries = list(manifests)
    if local_models_enabled(env=env, runtime_home=runtime_home):
        return entries
    return [manifest for manifest in entries if not manifest_is_local(manifest)]


def ungated_local_manifest_for_request(
    requested_model: str,
) -> ModelProviderManifest | None:
    """Resolve a requested-model name against the RAW persisted store, ignoring the policy.

    Used only to CLASSIFY a refusal: when the policy is disabled, the registry listing shows no
    local manifests, so an explicit local pin resolves to nothing. This lookup answers "would
    that name have been a local manifest?" so the router can refuse with the typed reason
    `local_models_disabled` instead of the generic unresolvable-model refusal. It grants
    nothing and executes nothing.
    """
    clean = str(requested_model or "").strip()
    if not clean:
        return None
    try:
        manifests = list_provider_manifests(enabled_only=False)
    except Exception:
        return None
    lowered = clean.lower()
    for manifest in manifests:
        if not manifest_is_local(manifest):
            continue
        if lowered == str(manifest.provider_id or "").lower():
            return manifest
    model_matches = [
        manifest
        for manifest in manifests
        if manifest_is_local(manifest) and lowered == str(manifest.model_name or "").strip().lower()
    ]
    if len(model_matches) == 1:
        return model_matches[0]
    provider_hint, separator, model_hint = clean.partition(":")
    if separator and provider_hint and model_hint:
        hint_lower = provider_hint.strip().lower()
        model_lower = model_hint.strip().lower()
        for manifest in manifests:
            if not manifest_is_local(manifest):
                continue
            if hint_lower == str(manifest.provider_name or "").lower() and model_lower == str(
                manifest.model_name or ""
            ).strip().lower():
                return manifest
    return None


__all__ = [
    "LOCAL_MODELS_DISABLED_REASON",
    "LOCAL_MODELS_ENABLED_ENV",
    "LocalModelPolicy",
    "filter_local_manifests_for_policy",
    "is_local_http_base_url",
    "local_models_enabled",
    "manifest_is_local",
    "resolve_local_model_policy",
    "ungated_local_manifest_for_request",
]
