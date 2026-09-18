from __future__ import annotations

import os
import re
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from core.context_capsule_v2 import resolve_kv_quant, resolve_num_ctx
from core.hardware_tier import probe_machine, select_qwen_tier
from core.local_model_bundles import (
    installed_ollama_role_for_model,
    manifest_profile_for_model,
    model_metadata,
    resolve_local_bundle_recommendation,
)
from core.local_model_policy import resolve_local_model_policy
from core.local_ollama_inventory import env_flag_enabled, installed_ollama_model_names, is_text_generation_ollama_model
from core.local_specialist_lane import secondary_local_model
from core.model_registry import ModelRegistry
from core.ollama_endpoint import ollama_base_url
from core.runtime_install_profiles import (
    installed_capacity_bucket,
    normalize_install_profile_id,
    required_ollama_models_for_profile,
)
from storage.model_provider_manifest import ModelProviderManifest

_DEFAULT_KIMI_BASE_URL = "https://api.moonshot.ai/v1"
_DEFAULT_KIMI_MODEL = "kimi-k2"
_KIMI_API_KEY_ENV_NAMES = ("KIMI_API_KEY", "MOONSHOT_API_KEY", "VOOL_KIMI_API_KEY")
_KIMI_BASE_URL_ENV_NAMES = ("KIMI_BASE_URL", "VOOL_KIMI_BASE_URL", "MOONSHOT_BASE_URL")
_KIMI_MODEL_ENV_NAMES = ("KIMI_MODEL", "VOOL_KIMI_MODEL", "MOONSHOT_MODEL")
_DEFAULT_GENERIC_REMOTE_BASE_URL = "https://api.openai.com/v1"
_DEFAULT_GENERIC_REMOTE_MODEL = "gpt-4.1-mini"
_GENERIC_REMOTE_API_KEY_ENV_NAMES = ("OPENAI_API_KEY", "VOOL_REMOTE_API_KEY", "VOOL_CLOUD_API_KEY")
_GENERIC_REMOTE_BASE_URL_ENV_NAMES = ("VOOL_REMOTE_BASE_URL", "OPENAI_BASE_URL")
# OpenRouter bring-your-own-key burst lane: one OpenAI-compatible endpoint fronting many
# cloud models. The key may come from the environment or the encrypted credential store.
_DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_DEFAULT_OPENROUTER_MODEL = "openai/gpt-4.1-mini"
# Full priority-ordered alias set DERIVED from the ONE canonical table — never restated.
from core.cloud_providers import key_env_names as _key_env_names

_OPENROUTER_API_KEY_ENV_NAMES = _key_env_names("openrouter")
_OPENROUTER_BASE_URL_ENV_NAMES = ("OPENROUTER_BASE_URL", "VOOL_OPENROUTER_BASE_URL")
_OPENROUTER_MODEL_ENV_NAMES = ("OPENROUTER_MODEL", "VOOL_OPENROUTER_MODEL")
_OPENROUTER_CREDENTIAL_KEY = "llm.cloud.openrouter"
# OpenRouter app-attribution headers: outbound calls carry these so OpenRouter credits the
# traffic to the VOOL app page / rankings entry. The env override NAMES stay VOOL_-prefixed on
# purpose (internal identifiers are frozen — see VOOL-DELIVERY/VOOL_MIGRATION.md §1); only the
# product-name VALUES flip to VOOL. Attribution only — no user data is added.
_OPENROUTER_REFERER_ENV_NAMES = ("VOOL_OPENROUTER_REFERER", "OPENROUTER_REFERER", "OPENROUTER_HTTP_REFERER")
_OPENROUTER_TITLE_ENV_NAMES = ("VOOL_OPENROUTER_TITLE", "OPENROUTER_TITLE", "OPENROUTER_X_TITLE")
_OPENROUTER_CATEGORIES_ENV_NAMES = ("VOOL_OPENROUTER_CATEGORIES", "OPENROUTER_CATEGORIES")
# VOOL attribution values (VOOL_MIGRATION.md §2.2, verified against OpenRouter's live docs).
# Exactly https://vool.dev — no www., path, version, or campaign suffix; identical on every platform.
_DEFAULT_OPENROUTER_REFERER = "https://vool.dev"
_DEFAULT_OPENROUTER_TITLE = "VOOL"
# OpenRouter marketplace categories (comma-separated). VOOL is a local-first personal agent + coding
# app, so it claims those two. OpenRouter documents at most TWO categories per request.
_DEFAULT_OPENROUTER_CATEGORIES = "personal-agent,programming-app"


def openrouter_attribution_headers(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """The canonical OpenRouter app-attribution headers (VOOL) — the ONE source every OpenRouter
    request path uses, so no call can leave without them (VOOL_MIGRATION.md §2.4). The VALUES are the
    VOOL product name; the env override NAMES stay ``VOOL_``-prefixed on purpose (frozen internal
    identifiers, §1). Fixed attribution headers only — never any user data.
    """
    source = env if env is not None else os.environ
    headers: dict[str, str] = {
        "HTTP-Referer": _env_first(source, *_OPENROUTER_REFERER_ENV_NAMES) or _DEFAULT_OPENROUTER_REFERER,
        "X-OpenRouter-Title": _env_first(source, *_OPENROUTER_TITLE_ENV_NAMES) or _DEFAULT_OPENROUTER_TITLE,
    }
    # OpenRouter documents at most TWO categories per request; cap to the first two so an env
    # override (or a stale multi-value default) can never send more than the API accepts.
    raw_categories = _env_first(source, *_OPENROUTER_CATEGORIES_ENV_NAMES) or _DEFAULT_OPENROUTER_CATEGORIES
    headers["X-OpenRouter-Categories"] = ",".join(
        [part.strip() for part in str(raw_categories).split(",") if part.strip()][:2]
    )
    return headers


def apply_openrouter_attribution_headers(
    headers: Mapping[str, str] | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return request headers with the fixed VOOL attribution applied last.

    Callers may add ordinary request headers, but attribution is owned by this shared factory.
    Applying it last prevents a manifest, adapter, or other request caller from replacing the
    values on the wire. The optional environment mapping is retained for the frozen VOOL_*
    deployment overrides; it is never read from a per-request header.
    """
    merged = {str(key): str(value) for key, value in dict(headers or {}).items()}
    merged.update(openrouter_attribution_headers(env))
    return merged


_GENERIC_REMOTE_MODEL_ENV_NAMES = ("VOOL_REMOTE_MODEL", "OPENAI_MODEL")
_DEFAULT_TETHER_MODEL = "tether-sonic"
_TETHER_API_KEY_ENV_NAMES = ("TETHER_API_KEY", "VOOL_TETHER_API_KEY")
_TETHER_BASE_URL_ENV_NAMES = ("TETHER_BASE_URL", "VOOL_TETHER_BASE_URL")
_TETHER_MODEL_ENV_NAMES = ("TETHER_MODEL", "VOOL_TETHER_MODEL")
_DEFAULT_VLLM_BASE_URL = "http://127.0.0.1:8000/v1"
_DEFAULT_VLLM_CONTEXT_WINDOW = 131072
_DEFAULT_LLAMACPP_BASE_URL = "http://127.0.0.1:8080/v1"
_DEFAULT_LLAMACPP_CONTEXT_WINDOW = 32768
_DEFAULT_MLX_BASE_URL = "http://127.0.0.1:8096/v1"
_DEFAULT_MLX_CONTEXT_WINDOW = 32768
_DEFAULT_MLX_MODEL = "mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit"
_FAST_LOCAL_DEFAULT_MODEL = "vool-qwen3-30b-a3b:nothink"


# The vool-30b-a3b default is ~19 GB of weights; it only belongs as the default on a machine with
# real headroom above that. On a 24 GB Mac it crowds out the OS + daemon (+ any second model) and
# thrashes, so below this RAM we fall through to the hardware-tier recommendation, which picks a model
# that actually fits (e.g. qwen3:8b). Override with VOOL_FAST_DEFAULT_MIN_RAM_GB.
_FAST_LOCAL_DEFAULT_MIN_RAM_GB = 32.0


def _fast_default_fits_hardware(*, env: Mapping[str, str] | None = None) -> bool:
    env_map = os.environ if env is None else env
    raw = str(env_map.get("VOOL_FAST_DEFAULT_MIN_RAM_GB") or "").strip()
    try:
        threshold = float(raw) if raw else _FAST_LOCAL_DEFAULT_MIN_RAM_GB
    except ValueError:
        threshold = _FAST_LOCAL_DEFAULT_MIN_RAM_GB
    try:
        from core.hardware_tier import probe_machine

        ram_gb = float(getattr(probe_machine(), "ram_gb", 0.0) or 0.0)
    except Exception:
        return True  # unknown hardware: keep prior behavior rather than over-restrict
    return ram_gb <= 0.0 or ram_gb >= threshold


def preferred_fast_local_model(*, env: Mapping[str, str] | None = None) -> str:
    env_map = os.environ if env is None else env
    explicit_inventory = str(env_map.get("VOOL_INSTALLED_OLLAMA_MODELS") or "").strip()
    if not explicit_inventory and not env_flag_enabled(env_map, "VOOL_ALLOW_OLLAMA_TAGS_FOR_DEFAULT", default=False):
        return ""
    installed = {item.strip().lower() for item in installed_ollama_model_names(env=env_map)}
    if _FAST_LOCAL_DEFAULT_MODEL.lower() not in installed:
        return ""
    # A big default that does not fit this machine is worse than none: fall through to the
    # hardware-aware recommendation instead of pinning a model that thrashes.
    if not _fast_default_fits_hardware(env=env_map):
        return ""
    return _FAST_LOCAL_DEFAULT_MODEL


def _installed_alternative_to(primary_model: str, *, env: Mapping[str, str]) -> str:
    """This machine's tier model when it is installed and the bundle's pick is a poorer fit.

    The bundle names the model it would like; it does not know what is on the disk. Pulling its
    pick while an equally hardware-fit model is already installed costs a multi-gigabyte download
    on first run and delays the first answer by minutes.

    A thinking model is passed over for an installed non-thinking one even when it is present. It
    cannot answer inside the chat output budget: measured on qwen3:4b at a 200-token budget, the
    whole budget went to reasoning and `content` came back empty, and widening it enough to fit
    both costs ~50s a turn against ~2s for the non-thinking model -- a chat assistant that either
    says nothing or takes a minute to say it.

    An empty inventory means a fresh box with nothing to prefer, so the bundle wins.
    """
    # Inventory lookup is opt-in unless the caller supplied an explicit snapshot. This keeps
    # startup and offline diagnostics from probing a live Ollama daemon merely to choose a model;
    # the bundle manifest or installer inventory remains the source of truth.
    if not str(env.get("VOOL_INSTALLED_OLLAMA_MODELS") or "").strip() and not env_flag_enabled(
        env, "VOOL_ALLOW_OLLAMA_TAGS_FOR_DEFAULT", default=False
    ):
        return ""
    try:
        installed = {name.strip().lower() for name in installed_ollama_model_names(env=env) if name.strip()}
    except Exception:
        return ""
    if not installed:
        return ""
    already_installed = primary_model.strip().lower() in installed
    if already_installed and not _is_thinking_capable_model(primary_model):
        return ""
    try:
        tier_tag = str(select_qwen_tier(probe_machine()).ollama_tag or "").strip()
    except Exception:
        return ""
    if not tier_tag or tier_tag.lower() not in installed:
        return ""
    if _is_thinking_capable_model(tier_tag):
        return ""
    return tier_tag


def default_runtime_model_tag(*, env: Mapping[str, str] | None = None) -> str:
    env_map = os.environ if env is None else env
    try:
        from core.bundle_manifest import selected_bundle_model

        bundled_model = selected_bundle_model(env=env_map)
    except Exception:
        bundled_model = ""
    if bundled_model:
        return bundled_model
    fast_model = preferred_fast_local_model(env=env_map)
    if fast_model:
        return fast_model
    try:
        recommendation = resolve_local_bundle_recommendation(
            probe=probe_machine(),
            free_disk_gb=_default_free_disk_gb(),
            secondary_local_model_name=secondary_local_model(env_map),
        )
        primary_model = str(recommendation.recommended_bundle.primary_model or "").strip()
        if primary_model:
            return _installed_alternative_to(primary_model, env=env_map) or primary_model
    except Exception:
        pass
    # Last-resort default if the tier tag resolves empty: qwen2.5:7b fits 7-8GB GPUs and CPU;
    # never qwen3:8b, which partial-offloads on 6-10GB VRAM (~47s/turn). Reachable paths above
    # already resolve correctly (guarded by test_runtime_provider_defaults_gpu_gating).
    return str(select_qwen_tier(probe_machine()).ollama_tag or "").strip() or "qwen2.5:7b"


def _is_thinking_capable_model(model_tag: str) -> bool:
    name = str(model_tag or "").strip().lower()
    return "qwen3" in name and "nothink" not in name and "no-think" not in name


def _thinking_runtime_config(model_tag: str) -> dict[str, object]:
    """The `think` entry for a model's manifest, or nothing when the key must be absent.

    Deep reasoning is OFF by default (fast local chat); a thinking model otherwise over-thinks
    trivial messages and times out the turn. The user can switch it ON (`core.reasoning_mode`),
    which is read here and by the adapter at call time so the toggle applies without a restart.
    Non-thinking models must omit the key entirely -- qwen2.5:7b answers HTTP 400 when present.
    """
    if not _is_thinking_capable_model(model_tag):
        return {}
    from core.reasoning_mode import deep_reasoning_enabled

    return {"think": deep_reasoning_enabled()}


def _bundle_role_for_model(required_models: tuple[str, ...], *, primary_model: str, model_name: str) -> str:
    clean = str(model_name or "").strip().lower()
    if clean == str(primary_model or "").strip().lower():
        return "general"
    if required_models and clean == str(required_models[-1]).strip().lower() and len(required_models) > 1:
        return "reasoning"
    return installed_ollama_role_for_model(model_name=clean, primary_model=primary_model)


def ensure_default_runtime_providers(
    registry: ModelRegistry,
    *,
    model_tag: str | None = None,
    env: Mapping[str, str] | None = None,
    install_profile: str | None = None,
    runtime_home: str | None = None,
) -> tuple[str, ...]:
    env_map = os.environ if env is None else env
    changed: list[str] = []
    # The canonical LocalModelPolicy owns this decision. Disabled means this function registers
    # NO local lane at all — not the bundle's `ollama-local:<tag>` default (the defect:
    # VOOL_REGISTER_INSTALLED_OLLAMA_MODELS=0 only ever suppressed the EXTRA installed lanes),
    # not the installed-model lanes, and not the llama.cpp/vLLM/MLX aux lanes — and it performs
    # no installed-model discovery to feed any of them. Cloud lanes below are untouched.
    local_policy = resolve_local_model_policy(env=env_map, runtime_home=runtime_home)
    local_model = "" if local_policy.local_models_disabled else (
        str(model_tag or "").strip() or default_runtime_model_tag(env=env_map)
    )
    active_profile = normalize_install_profile_id(install_profile, allow_auto=False)
    required_models: tuple[str, ...] = tuple()
    if local_policy.local_models_enabled:
        required_models = required_ollama_models_for_profile(
            profile_id=active_profile or "local-only",
            model_tag=local_model,
            runtime_home=runtime_home,
            env=env_map,
        )
    primary_model = required_models[0] if required_models else local_model
    provider_models = (
        _runtime_provider_model_roles(
            required_models=required_models or (local_model,),
            primary_model=primary_model,
            env=env_map,
        )
        if local_policy.local_models_enabled
        else tuple()
    )
    # Adaptive sizing is automatic only for a persisted capacity bucket. Read it cheaply — never
    # re-probe hardware here (that path is what froze install step 6).
    try:
        adaptive_bucket = installed_capacity_bucket(runtime_home) if runtime_home else ""
    except Exception:
        adaptive_bucket = ""
    adaptive_kv = resolve_kv_quant(env_map)
    for bundle_model, role in provider_models:
        if _ensure_local_ollama_provider(
            registry,
            model_tag=bundle_model,
            bundle_role=role,
            bucket=adaptive_bucket,
            kv_quant=adaptive_kv,
            env=env_map,
        ):
            changed.append(f"ollama-local:{bundle_model}")
    if local_policy.local_models_enabled and _profile_allows_aux_local_providers(
        active_profile, runtime_home=runtime_home
    ):
        llamacpp_provider_id = _ensure_llamacpp_provider(registry, model_name=local_model, env=env_map)
        if llamacpp_provider_id:
            changed.append(llamacpp_provider_id)
        llamacpp_deep_provider_id = _ensure_llamacpp_deep_provider(registry, env=env_map)
        if llamacpp_deep_provider_id:
            changed.append(llamacpp_deep_provider_id)
        vllm_provider_id = _ensure_vllm_provider(registry, model_name=local_model, env=env_map)
        if vllm_provider_id:
            changed.append(vllm_provider_id)
        mlx_provider_id = _ensure_mlx_provider(registry, model_name=local_model, env=env_map)
        if mlx_provider_id:
            changed.append(mlx_provider_id)
    if _profile_allows_kimi_provider(active_profile):
        kimi_provider_id = _ensure_kimi_provider(registry, env=env_map)
        if kimi_provider_id:
            changed.append(kimi_provider_id)
    if _profile_allows_generic_remote_provider(active_profile):
        generic_remote_provider_id = _ensure_generic_remote_provider(registry, env=env_map)
        if generic_remote_provider_id:
            changed.append(generic_remote_provider_id)
        openrouter_provider_id = _ensure_openrouter_byok_provider(registry, env=env_map)
        if openrouter_provider_id:
            changed.append(openrouter_provider_id)
        # Direct-provider BYOK (OpenAI/Anthropic/Groq/Gemini/DeepSeek/Kimi/custom): register the
        # ACTIVE provider's lane at boot when it is keyed (v1 = one active cloud provider), so a
        # direct-provider choice survives a restart. Live switches go through activate_provider_byok.
        try:
            from core.cloud_providers import active_provider

            _active_direct = active_provider()
        except Exception:
            _active_direct = ""
        if _active_direct and _active_direct != "openrouter":
            from core.credential_intelligence.store import StorageConflictError, StorageUnavailableError

            try:
                direct_provider_id = _ensure_provider_byok_provider(registry, provider_id=_active_direct, env=env_map)
            except (StorageConflictError, StorageUnavailableError):
                # A binding the credential store cannot decide right now (a pending, unreadable or
                # incoherent custom pair) leaves this lane dormant. It never aborts boot (revision-5
                # review R3): the pending operation keeps recovery ownership and every consumer of the
                # pair keeps refusing with the store's typed reason.
                direct_provider_id = ""
            if direct_provider_id:
                changed.append(direct_provider_id)
        # Enforce one-active-provider at boot: retire any OTHER provider's still-enabled BYOK lane
        # (e.g. a legacy openrouter lane left up while a direct provider is now active), so a restart
        # never leaves two paid lanes competing for a burst.
        if _active_direct:
            retire_nonactive_provider_lanes(_active_direct)
    tether_provider_id = _ensure_tether_provider(registry, env=env_map)
    if tether_provider_id:
        changed.append(tether_provider_id)
    return tuple(changed)


def _runtime_provider_model_roles(
    *,
    required_models: tuple[str, ...],
    primary_model: str,
    env: Mapping[str, str],
) -> tuple[tuple[str, str], ...]:
    model_roles: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(model_name: str, role: str) -> None:
        clean_model = str(model_name or "").strip()
        if not clean_model:
            return
        key = clean_model.lower()
        if key in seen:
            return
        seen.add(key)
        model_roles.append((clean_model, str(role or "general").strip() or "general"))

    for bundle_model in required_models:
        add(bundle_model, _bundle_role_for_model(required_models, primary_model=primary_model, model_name=bundle_model))

    if not env_flag_enabled(env, "VOOL_REGISTER_INSTALLED_OLLAMA_MODELS", default=False):
        return tuple(model_roles)

    for installed_model in installed_ollama_model_names(env=env):
        if not is_text_generation_ollama_model(installed_model):
            continue
        add(
            installed_model,
            installed_ollama_role_for_model(model_name=installed_model, primary_model=primary_model),
        )
    return tuple(model_roles)


_ADAPTIVE_CONTEXT_ENV = "VOOL_ADAPTIVE_CONTEXT"
_CONTEXT_WINDOW_OVERRIDE_ENV = "VOOL_OLLAMA_CONTEXT_WINDOW"
_BASELINE_CONTEXT_WINDOW = 4096
_MIN_CTX = 1024
_HARDWARE_CONTEXT_CEILING = {"A": 4096, "B": 8192, "C": 16384, "D": 32768, "E": 32768}
_FALSE_ENV_VALUES = frozenset({"0", "false", "no", "off", "disabled"})
# Stands in for a model whose size cannot be proven, so an unrecognized tag is bounded by the
# largest-model row rather than by no row at all.
_UNPROVEN_MODEL_BILLIONS = 25.0


def _hardware_context_bucket(*, env: Mapping[str, str] | None = None) -> str:
    """Derive the A-E capacity bucket from this machine's RAM when none is persisted, so a capable box
    is not silently pinned to the weakest 4K context (which drops earlier turns from a long chat once
    it switches back to the local model). Override with VOOL_CONTEXT_BUCKET.
    """
    env_map = os.environ if env is None else env
    forced = str(env_map.get("VOOL_CONTEXT_BUCKET") or "").strip().upper()
    if forced in _HARDWARE_CONTEXT_CEILING:
        return forced
    try:
        from core.hardware_tier import probe_machine

        ram_gb = float(getattr(probe_machine(), "ram_gb", 0.0) or 0.0)
    except Exception:
        return "A"
    if ram_gb >= 30:
        return "E"
    if ram_gb >= 20:
        return "D"
    if ram_gb >= 14:
        return "C"
    if ram_gb >= 9:
        return "B"
    return "A"


def _flat_ollama_context_window(bundle_role: str) -> int:
    """Conservative context used whenever adaptive sizing cannot be proven safe."""
    del bundle_role
    return _BASELINE_CONTEXT_WINDOW


def _known_model_parameter_billions(model_tag: str) -> float | None:
    """Return a model's total parameter count only when the tag or registry proves it."""
    clean = str(model_tag or "").strip().lower()
    if not clean:
        return None
    raw_count = str(model_metadata(clean).get("parameter_count") or "").strip().lower().rstrip("b")
    if raw_count:
        try:
            value = float(raw_count)
            return value if value > 0 else None
        except ValueError:
            return None
    # An MoE tag such as mixtral:8x7b describes expert count × expert size, not
    # total parameters. Parsing its trailing 7b as the model size would grant an
    # unsafe <=8B context ceiling. Fail closed unless registry metadata above
    # provides the actual total.
    if re.search(r"\d+(?:\.\d+)?x\d+(?:\.\d+)?b", clean):
        return None
    match = re.search(r"(?:^|[^0-9.])(\d+(?:\.\d+)?)b(?:[^a-z0-9]|$)", clean)
    if not match:
        return None
    value = float(match.group(1))
    return value if value > 0 else None


def _model_context_ceiling(*, bucket: str, parameter_billions: float) -> int:
    if parameter_billions <= 8:
        return {"A": 4096, "B": 8192, "C": 16384, "D": 32768, "E": 32768}[bucket]
    if parameter_billions <= 14:
        return {"A": 4096, "B": 4096, "C": 8192, "D": 16384, "E": 16384}[bucket]
    if parameter_billions <= 24:
        return {"A": 4096, "B": 4096, "C": 4096, "D": 8192, "E": 16384}[bucket]
    return {"A": 4096, "B": 4096, "C": 4096, "D": 8192, "E": 8192}[bucket]


def _ollama_context_sizing(
    bundle_role: str,
    *,
    model_tag: str = "",
    bucket: str = "",
    kv_quant: str = "fp16",
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    env_map = os.environ if env is None else env
    selected = _flat_ollama_context_window(bundle_role)
    clean_bucket = str(bucket or "").strip().upper()
    if clean_bucket not in _HARDWARE_CONTEXT_CEILING:
        # No persisted bucket (a fresh install has no capacity file yet): size from this machine's
        # actual RAM instead of pinning the weakest 4K, which is what dropped earlier chat turns once
        # a conversation routed back to the local model.
        clean_bucket = _hardware_context_bucket(env=env_map)
    known_bucket = clean_bucket in _HARDWARE_CONTEXT_CEILING
    parameter_billions = _known_model_parameter_billions(model_tag)
    result: dict[str, object] = {
        "context_sizing_policy": "flat_unverified_bucket",
        "capacity_bucket": clean_bucket if known_bucket else "",
        "model_parameter_billions": parameter_billions,
        "selected_num_ctx": selected,
    }
    # Both ceilings are always computable: an unpersisted bucket falls back to the weakest row and an
    # unrecognized model tag to the largest-model row, so every path below is bounded by a proven
    # number rather than by no bound at all.
    ceiling_bucket = clean_bucket if known_bucket else "A"
    hardware_ceiling = _HARDWARE_CONTEXT_CEILING[ceiling_bucket]
    model_ceiling = _model_context_ceiling(
        bucket=ceiling_bucket,
        parameter_billions=parameter_billions if parameter_billions is not None else _UNPROVEN_MODEL_BILLIONS,
    )
    safe_ceiling = min(hardware_ceiling, model_ceiling)
    result["hardware_context_ceiling"] = hardware_ceiling
    result["model_context_ceiling"] = model_ceiling

    # VOOL_OLLAMA_CONTEXT_WINDOW is the documented per-machine escape hatch, so it is read on every
    # path -- including an explicit adaptive opt-out, an unpersisted bucket and an unrecognized model
    # tag. Reading it only inside the adaptive branch left a supported control silently doing nothing
    # in exactly those three cases. It stays clamped to the ceilings above, and may also size DOWN to
    # _MIN_CTX, which is how a constrained card gives back VRAM.
    override_raw = str(env_map.get(_CONTEXT_WINDOW_OVERRIDE_ENV) or "").strip()
    if override_raw:
        try:
            override_value = int(override_raw)
        except (TypeError, ValueError):
            result["context_sizing_policy"] = "flat_invalid_override"
            return result
        result["selected_num_ctx"] = max(_MIN_CTX, min(override_value, safe_ceiling))
        result["context_sizing_policy"] = (
            "user_override" if known_bucket and parameter_billions is not None else "user_override_capped_unproven"
        )
        return result

    adaptive_raw = str(env_map.get(_ADAPTIVE_CONTEXT_ENV) or "").strip().lower()
    if adaptive_raw in _FALSE_ENV_VALUES:
        result["context_sizing_policy"] = "flat_explicit_opt_out"
        return result
    if not known_bucket:
        return result
    if parameter_billions is None:
        result["context_sizing_policy"] = "flat_unknown_model_size"
        return result
    try:
        effective_kv = "q8_0" if (kv_quant == "q8_0" or resolve_kv_quant(env_map) == "q8_0") else "fp16"
        adaptive = int(resolve_num_ctx(bucket=clean_bucket, role=bundle_role, kv_quant=effective_kv))
        result["selected_num_ctx"] = min(max(adaptive, _BASELINE_CONTEXT_WINDOW), safe_ceiling)
        result["context_sizing_policy"] = "adaptive_persisted_bucket"
        return result
    except Exception:
        result["context_sizing_policy"] = "flat_sizing_error"
        result["selected_num_ctx"] = _BASELINE_CONTEXT_WINDOW
        return result


def _ollama_context_window_for_bundle_role(
    bundle_role: str,
    *,
    model_tag: str = "",
    bucket: str = "",
    kv_quant: str = "fp16",
    env: Mapping[str, str] | None = None,
) -> int:
    """Return the safe Ollama num_ctx for the persisted hardware bucket and model tag."""
    return int(
        _ollama_context_sizing(
            bundle_role,
            model_tag=model_tag,
            bucket=bucket,
            kv_quant=kv_quant,
            env=env,
        )["selected_num_ctx"]
    )


def default_local_provider_base_url(env: Mapping[str, str] | None = None) -> str:
    """The base_url written into the auto-registered local provider's manifest. Resolved from the environment,
    so the certification probe -- which reads the manifest -- follows the same fence as every other client."""
    return ollama_base_url(env)


def register_installed_local_model(
    registry: ModelRegistry,
    *,
    model_tag: str,
    env: Mapping[str, str] | None = None,
) -> ModelProviderManifest | None:
    """Register ONE installed Ollama model as an `ollama-local` lane, or None if it isn't.

    The user-facing door behind Settings → Models (`/api/models/local/register`). Boot
    registration stays curated (the bundle's models plus, only when the operator opted in,
    the installed inventory); this seam exists for the model that is installed but not in
    that set, so registering it is a deliberate act rather than a silent boot side effect.
    The manifest factory is the same one boot uses, so license metadata, context sizing,
    the think flag and prewarm come out identical to a bundled lane.

    Registering is not certifying: `core.local_model_tool_certification` still decides
    whether this lane may take the final-answer author role.
    """

    clean = str(model_tag or "").strip()
    if not clean:
        return None
    env_map = os.environ if env is None else env
    from core.local_model_bundles import installed_ollama_role_for_model

    role = installed_ollama_role_for_model(
        model_name=clean,
        primary_model=default_runtime_model_tag(env=env_map),
    )
    _ensure_local_ollama_provider(
        registry,
        model_tag=clean,
        bundle_role=role,
        env=env_map,
    )
    return registry.get_manifest("ollama-local", clean)


def _ensure_local_ollama_provider(
    registry: ModelRegistry,
    *,
    model_tag: str,
    bundle_role: str,
    bucket: str = "",
    kv_quant: str = "fp16",
    env: Mapping[str, str] | None = None,
) -> bool:
    env_map = os.environ if env is None else env
    existing = registry.get_manifest("ollama-local", model_tag)
    if existing is not None and not isinstance(existing, ModelProviderManifest):
        existing = None
    context_sizing = _ollama_context_sizing(
        bundle_role,
        model_tag=model_tag,
        bucket=bucket,
        kv_quant=kv_quant,
        env=env,
    )
    context_window = int(context_sizing["selected_num_ctx"])
    forced_num_gpu = _resolve_ollama_num_gpu(env_map)
    manifest_profile = manifest_profile_for_model(model_name=model_tag, bundle_role=bundle_role)
    has_license = bool(
        str(getattr(existing, "license_name", None) or "").strip()
        and str(getattr(existing, "resolved_license_reference", None) or "").strip()
    )
    expected_role = (
        str((getattr(existing, "metadata", {}) or {}).get("bundle_role") or "").strip().lower() if existing else ""
    )
    existing_runtime_config = dict(getattr(existing, "runtime_config", {}) or {}) if existing else {}
    existing_metadata = dict(getattr(existing, "metadata", {}) or {}) if existing else {}
    existing_prewarm = dict(existing_runtime_config.get("prewarm") or {})
    existing_prewarm_options = dict(existing_prewarm.get("options") or {})
    uses_native_ollama_chat = not str(existing_runtime_config.get("api_path") or "").strip()
    # Compared against the value this build would write, not against a fixed False: a manifest
    # persisted by an older build carries think:false, which leaks a thinking model's monologue
    # into the answer. Treating that as satisfying the contract would leave the manifest unwritten
    # and the defect live for every existing install.
    thinking_matches = existing_runtime_config.get("think") == _thinking_runtime_config(model_tag).get("think")
    # num_gpu is only present when VOOL_OLLAMA_NUM_GPU forced it; None means the
    # key must be absent from the persisted manifest. 0 is a legitimate forced
    # value (pure CPU), so compare against a sentinel that distinguishes unset
    # from 0 — otherwise an in-place upgrade never rewrites the manifest and the
    # forced num_gpu silently never lands (existing manifests are preserved).
    _UNSET = object()
    existing_runtime_num_gpu = existing_runtime_config.get("num_gpu", _UNSET)
    existing_prewarm_num_gpu = existing_prewarm_options.get("num_gpu", _UNSET)
    has_num_gpu_contract = _num_gpu_matches(
        existing_runtime_num_gpu, forced_num_gpu, unset=_UNSET
    ) and _num_gpu_matches(existing_prewarm_num_gpu, forced_num_gpu, unset=_UNSET)
    has_context_contract = (
        int(existing_runtime_config.get("context_window") or existing_metadata.get("context_window") or 0)
        == context_window
        and int(existing_prewarm_options.get("num_ctx") or 0) == context_window
        and uses_native_ollama_chat
        and thinking_matches
        and has_num_gpu_contract
        and bool(existing_runtime_config.get("supports_json_schema", False))
    )
    expected_tps = float(manifest_profile.get("tokens_per_second") or 0.0)
    has_measurement_contract = (
        expected_tps <= 0 or float(existing_metadata.get("tokens_per_second") or 0.0) == expected_tps
    )
    has_sizing_contract = all(existing_metadata.get(key) == value for key, value in context_sizing.items())
    if (
        existing
        and existing.enabled
        and has_license
        and expected_role == str(bundle_role or "").strip().lower()
        and has_context_contract
        and has_measurement_contract
        and has_sizing_contract
    ):
        return False
    parameter_size = str(manifest_profile.get("parameter_count") or parameter_size_for_model(model_tag))
    license_reference = str(manifest_profile.get("license_reference") or "user-managed")
    runtime_config: dict[str, object] = {
        "base_url": default_local_provider_base_url(),
        "health_path": "/v1/models",
        "timeout_seconds": 180,
        "health_timeout_seconds": 10,
        "temperature": 0.7,
        **_thinking_runtime_config(model_tag),
        "supports_json_mode": False,
        "supports_json_schema": True,
        "context_window": context_window,
        "prewarm": {
            "strategy": "ollama_chat",
            "keep_alive": "15m",
            "message": " ",
            "timeout_seconds": 45,
            "options": {
                "num_ctx": context_window,
                "num_predict": 1,
            },
        },
    }
    # When VOOL_OLLAMA_NUM_GPU is set, thread num_gpu so it reaches BOTH the
    # live chat call (top-level runtime_config, read by _build_ollama_payload)
    # AND the prewarm call (prewarm.options, copied verbatim by the adapter).
    # Unset => key absent => Ollama decides layers (current behavior).
    if forced_num_gpu is not None:
        runtime_config["num_gpu"] = forced_num_gpu
        prewarm_options = runtime_config["prewarm"]["options"]  # type: ignore[index]
        prewarm_options["num_gpu"] = forced_num_gpu
    manifest = ModelProviderManifest(
        provider_name="ollama-local",
        model_name=model_tag,
        source_type="http",
        adapter_type="local_qwen_provider",
        license_name=str(manifest_profile.get("license_name") or "user-managed"),
        license_reference=license_reference,
        license_url_or_reference=license_reference,
        weight_location="external",
        runtime_dependency="ollama",
        notes=f"{manifest_profile.get('notes') or 'Local Ollama lane.'} ({parameter_size}) — auto-registered by VOOL runtime",
        capabilities=list(manifest_profile.get("capabilities") or ()),
        runtime_config=runtime_config,
        metadata={
            "runtime_family": "ollama",
            "confidence_baseline": float(manifest_profile.get("confidence_baseline") or 0.65),
            "parameter_count": parameter_size,
            "tokens_per_second": float(manifest_profile.get("tokens_per_second") or 0.0),
            "quantization": str(manifest_profile.get("quantization") or "").strip(),
            "orchestration_role": str(manifest_profile.get("orchestration_role") or "drone"),
            "bundle_role": str(manifest_profile.get("bundle_role") or bundle_role or "general"),
            "deployment_class": "local",
            "context_window": context_window,
            **context_sizing,
            "tool_support": list(manifest_profile.get("tool_support") or ()),
            "max_safe_concurrency": 1,
        },
        enabled=True,
    )
    registry.register_manifest(manifest)
    return True


def _ensure_kimi_provider(
    registry: ModelRegistry,
    *,
    env: Mapping[str, str],
) -> str:
    api_key = _env_first(env, *_KIMI_API_KEY_ENV_NAMES)
    if not api_key:
        return ""
    api_key_env = next((name for name in _KIMI_API_KEY_ENV_NAMES if str(env.get(name) or "").strip()), "KIMI_API_KEY")
    model_name = _env_first(env, *_KIMI_MODEL_ENV_NAMES) or _DEFAULT_KIMI_MODEL
    existing = registry.get_manifest("kimi-remote", model_name)
    has_base_url = bool(str(getattr(existing, "runtime_config", {}).get("base_url") or "").strip()) if existing else False
    tagged_paid = str((getattr(existing, "metadata", {}) or {}).get("cost_class") or "") == "paid_cloud" if existing else False
    if existing and existing.enabled and has_base_url and tagged_paid:
        return existing.provider_id
    base_url = _env_first(env, *_KIMI_BASE_URL_ENV_NAMES) or _DEFAULT_KIMI_BASE_URL
    manifest = ModelProviderManifest(
        provider_name="kimi-remote",
        model_name=model_name,
        source_type="http",
        adapter_type="openai_compatible",
        license_name="Provider",
        license_reference="user-managed",
        license_url_or_reference="user-managed",
        weight_location="external",
        redistribution_allowed=False,
        runtime_dependency="remote-openai-compatible-provider",
        notes="Kimi via Moonshot OpenAI-compatible API — auto-registered when a Kimi/Moonshot API key is configured.",
        capabilities=["summarize", "classify", "format", "extract", "code_basic", "code_complex", "structured_json", "long_context"],
        runtime_config={
            "base_url": base_url,
            "api_path": "/chat/completions",
            "health_path": "/models",
            "timeout_seconds": 180,
            "health_timeout_seconds": 10,
            "temperature": 0.3,
            "supports_json_mode": True,
            "api_key_env": api_key_env,
        },
        metadata={
            "runtime_family": "openai-compatible",
            "confidence_baseline": 0.78,
            "orchestration_role": "queen",
            "deployment_class": "remote",
            # Paid remote lane (spends the user's Moonshot/Kimi credits). Tag it explicitly so
            # provider_cost_class returns paid_cloud instead of defaulting to remote_unknown —
            # remote_unknown is not excluded by the paid-fallback gate, so an untagged paid
            # remote would be selectable even when paid fallback is off.
            "cost_class": "paid_cloud",
            "context_window": 128000,
            "tool_support": ["structured_json", "code_complex"],
            "max_safe_concurrency": 2,
        },
        enabled=True,
    )
    registry.register_manifest(manifest)
    return manifest.provider_id


def _ensure_openrouter_byok_provider(
    registry: ModelRegistry,
    *,
    env: Mapping[str, str],
) -> str:
    """Register a bring-your-own-key OpenRouter burst lane.

    OpenRouter is a single OpenAI-compatible endpoint fronting many cloud models, so one
    user key gives VOOL a paid escalation lane for tasks the local model cannot handle. It
    is registered ONLY when a key is actually available — from the environment
    (``OPENROUTER_API_KEY``) or the encrypted credential store (``llm.cloud.openrouter``) —
    so it stays dormant and cannot be ranked or selected until the user has brought a key.
    The key itself is never written into the manifest; the adapter resolves it at call time
    (environment first, then the vault via ``credential_key``).
    """
    api_key_env = next((name for name in _OPENROUTER_API_KEY_ENV_NAMES if str(env.get(name) or "").strip()), "")
    has_vault_key = False
    try:
        from core import credential_store

        has_vault_key = bool(credential_store.has_credential(_OPENROUTER_CREDENTIAL_KEY))
    except Exception:
        has_vault_key = False
    if not api_key_env and not has_vault_key:
        return ""  # dormant until the user brings a key

    _restore_selected_chat_models(registry, provider_id="openrouter", env=env, api_key_env=api_key_env)

    # Model precedence: env override -> the user's persisted `cloud model` choice -> default.
    policy_model = ""
    try:
        from core.cloud_escalation_policy import load_policy as _load_cloud_policy

        policy_model = str(_load_cloud_policy().model or "").strip()
    except Exception:
        policy_model = ""
    model_name = _env_first(env, *_OPENROUTER_MODEL_ENV_NAMES) or policy_model or _DEFAULT_OPENROUTER_MODEL

    # `auto` = VOOL follows the live free catalog: the best free general model plus the best free
    # coding model, as TWO lanes with split capabilities, so the capability-driven selection policy
    # routes chat to one and programming to the other. Cache-only here — provider registration runs
    # at boot and must never block on the network; the chat commands do the fetching.
    if model_name.strip().lower() == "auto":
        picks: dict[str, str] = {}
        try:
            from core.openrouter_catalog import pick_auto_free_models

            picks = pick_auto_free_models(allow_network=False)
        except Exception:
            picks = {}
        if picks:
            _retire_openrouter_lanes(keep=set(picks.values()))
            # tool_intent: a general-purpose instruction-following cloud model can genuinely select
            # a tool from a schema catalogue -- this is not a ranking hack, it is the capability the
            # local Ollama manifests already declare (see `manifest_profile["capabilities"]` in
            # `_ensure_local_ollama_provider`). Without it, `capability_score` never credits an
            # OpenRouter lane for tool_intent/output_mode=="tool_intent" work regardless of how
            # capable the underlying model actually is (Finding, 2026-08-04).
            general_caps = ["summarize", "classify", "format", "extract", "code_basic", "structured_json", "long_context", "tool_intent"]
            coding_caps = ["code_basic", "code_complex", "structured_json", "format", "extract", "long_context", "tool_intent"]
            provider_id = ""
            if "coding" in picks:
                provider_id = _register_openrouter_manifest(
                    registry, model_name=picks["coding"], env=env, api_key_env=api_key_env, capabilities=coding_caps
                ) or provider_id
            if "general" in picks:
                provider_id = _register_openrouter_manifest(
                    registry, model_name=picks["general"], env=env, api_key_env=api_key_env, capabilities=general_caps
                ) or provider_id
            return provider_id
        # No verified-free pick available (no catalog cached yet, or nothing in it is free). `auto`
        # means "follow the live FREE catalog", so there is no lane to register: standing in the
        # paid default here points an explicitly-free choice at a paid model. Retire the existing
        # lanes too — one of them may be a paid default from before the switch to auto, and leaving
        # it enabled keeps a paid lane live under a free-only policy. The lane comes back on the
        # next successful catalog fetch.
        _retire_openrouter_lanes(keep=set())
        return ""

    # A model switch must retire the previous lane, or selection can keep routing to the old model.
    _retire_openrouter_lanes(keep={model_name})
    return _register_openrouter_manifest(
        registry,
        model_name=model_name,
        env=env,
        api_key_env=api_key_env,
        # tool_intent included for the same reason as the auto-lane caps above: a capable
        # instruction-following OpenRouter model can genuinely select a tool, and every local
        # Ollama manifest already declares this capability -- omitting it here is what made
        # capability_score() rank a free, capable cloud model 1.2 points below qwen3:8b for
        # every tool_intent turn regardless of the free-cloud ranking boost (Finding, 2026-08-04).
        capabilities=["summarize", "classify", "format", "extract", "code_basic", "code_complex", "structured_json", "long_context", "tool_intent"],
    )


def _retire_openrouter_lanes(*, keep: set[str]) -> None:
    """Disable every enabled openrouter-byok manifest whose model is not in ``keep``."""
    try:
        from storage.model_provider_manifest import list_provider_manifests, upsert_provider_manifest

        for stale in list_provider_manifests(enabled_only=True):
            if stale.provider_name == "openrouter-byok" and stale.model_name not in keep:
                upsert_provider_manifest(_retired_or_chat_only(stale))
    except Exception:  # pragma: no cover - best-effort retirement; the new lane still registers
        pass


def _register_openrouter_manifest(
    registry: ModelRegistry,
    *,
    model_name: str,
    env: Mapping[str, str],
    api_key_env: str,
    capabilities: list[str],
    chat_selection_only: bool = False,
) -> str:
    existing = registry.get_manifest("openrouter-byok", model_name)
    has_base_url = bool(str(getattr(existing, "runtime_config", {}).get("base_url") or "").strip()) if existing else False
    # What the provider's own catalog already published about this model, cache-only: registering
    # a lane runs at boot and on key activation and must never block on the network. The facts
    # stamped here (supported_parameters — where OpenRouter publishes the `reasoning` parameter —
    # plus the real context length and completion cap) are what the lane's output-budget
    # resolution reads; without them the persisted manifest declares nothing and a reasoning
    # model's 240–520-token chat ceiling is spent entirely on reasoning (measured 2026-09-16:
    # empty content, finish_reason length, no answer published).
    catalog_facts = _openrouter_catalog_facts(model_name)
    if (
        existing
        and existing.enabled
        and has_base_url
        and list(existing.capabilities or []) == capabilities
        and bool((getattr(existing, "runtime_config", {}) or {}).get("supports_json_schema", False))
        and (chat_selection_only or not existing.metadata.get("chat_selection_only"))
        and _catalog_facts_stamped(existing, catalog_facts)
    ):
        return existing.provider_id
    base_url = _env_first(env, *_OPENROUTER_BASE_URL_ENV_NAMES) or _DEFAULT_OPENROUTER_BASE_URL
    runtime_config: dict[str, object] = {
        "base_url": base_url,
        "api_path": "/chat/completions",
        "health_path": "/models",
        "timeout_seconds": 180,
        "health_timeout_seconds": 10,
        "temperature": 0.3,
        "supports_json_mode": True,
        "supports_json_schema": True,
        "credential_key": _OPENROUTER_CREDENTIAL_KEY,
    }
    if api_key_env:
        runtime_config["api_key_env"] = api_key_env
    # App-attribution headers from the single canonical source (openrouter_attribution_headers) so
    # OpenRouter credits the traffic to the VOOL app page + leaderboard. HTTP-Referer creates the app
    # page, the Title sets the display name, Categories claims rankings. Env-overridable; names frozen (§1).
    runtime_config["headers"] = openrouter_attribution_headers(env)
    manifest = ModelProviderManifest(
        provider_name="openrouter-byok",
        model_name=model_name,
        source_type="http",
        adapter_type="openai_compatible",
        license_name="Provider",
        license_reference="user-managed",
        license_url_or_reference="user-managed",
        weight_location="external",
        redistribution_allowed=False,
        runtime_dependency="remote-openai-compatible-provider",
        notes="OpenRouter (bring-your-own-key) burst lane — auto-registered when an OpenRouter key is in the environment or the encrypted credential store.",
        capabilities=capabilities,
        runtime_config=runtime_config,
        metadata={
            "chat_selection_only": chat_selection_only,
            "runtime_family": "openai-compatible",
            "confidence_baseline": 0.78,
            "orchestration_role": "queen",
            "deployment_class": "remote",
            # Paid BYOK lane: spends the user's cloud credits, so it must be metered by the
            # daily cap and gated by the paid-fallback exclusion like any paid_cloud provider,
            # even though it uses the generic openai_compatible adapter.
            "cost_class": "paid_cloud",
            # OpenRouter's :free tag is the live catalog's explicit zero-price
            # lane. It remains a cloud usage lane, but does not need a paid
            # spend reservation before an owner-local explicit selection runs.
            "verified_free": model_name.strip().lower().endswith(":free"),
            "context_window": catalog_facts.get("context_window") or 128000,
            "tool_support": ["structured_json", "code_complex"],
            "max_safe_concurrency": 2,
            **(
                {
                    "supported_parameters": list(catalog_facts.get("supported_parameters") or ()),
                    "max_output_tokens": int(catalog_facts.get("max_output_tokens") or 0),
                }
                if catalog_facts
                else {}
            ),
        },
        enabled=True,
    )
    registry.register_manifest(manifest)
    return manifest.provider_id


def _openrouter_catalog_facts(model_name: str) -> dict[str, Any]:
    """The cached catalog row's facts for one byok model, or {} when nothing is known.

    Cache-only by construction (`cached_catalog_row` never refreshes), so a boot-time or
    key-activation-time registration cannot block on the network. A model the cache does not
    know yields {} — the manifest then declares nothing, exactly as before, and the adapter's
    per-call fallback reads the same cache again once it has been filled.
    """
    try:
        from core.openrouter_catalog import cached_catalog_row

        row = cached_catalog_row(model_name)
    except Exception:
        return {}
    if row is None:
        return {}
    facts: dict[str, Any] = {"supported_parameters": tuple(row.supported_parameters)}
    if int(row.context_length or 0) > 0:
        facts["context_window"] = int(row.context_length)
    if int(row.max_output_tokens or 0) > 0:
        facts["max_output_tokens"] = int(row.max_output_tokens)
    return facts


def _catalog_facts_stamped(existing: Any, catalog_facts: dict[str, Any]) -> bool:
    """Whether an already-registered manifest carries the catalog facts it should.

    Only the keys the stamp writes are compared, and only when the catalog actually knows the
    model: with no facts there is nothing to refresh and the historical early-return behaviour
    stands; with facts, a manifest that predates the stamp (or was written from an older cache)
    is re-registered so the persisted lane carries the provider's own declaration.
    """
    metadata = dict(getattr(existing, "metadata", None) or {})
    stamped_parameters = tuple(
        str(item) for item in list(metadata.get("supported_parameters") or ())
    )
    if stamped_parameters != tuple(catalog_facts.get("supported_parameters") or ()):
        return False
    stamped_window = int(metadata.get("context_window") or 0)
    catalog_window = int(catalog_facts.get("context_window") or 0)
    if catalog_window > 0:
        if stamped_window != catalog_window:
            return False
    stamped_cap = int(metadata.get("max_output_tokens") or 0)
    catalog_cap = int(catalog_facts.get("max_output_tokens") or 0)
    return not (catalog_cap > 0 and stamped_cap != catalog_cap)


def default_openrouter_model_name(env: Mapping[str, str] | None = None) -> str:
    """The model the OpenRouter lane uses when the user has not picked one (env-overridable)."""
    env_map: Mapping[str, str] = env if env is not None else dict(os.environ)
    return _env_first(env_map, *_OPENROUTER_MODEL_ENV_NAMES) or _DEFAULT_OPENROUTER_MODEL


def activate_openrouter_byok(env: Mapping[str, str] | None = None) -> str:
    """Register (or refresh) the OpenRouter burst lane NOW, without a restart.

    The manifest store is shared, so a fresh ModelRegistry sees the same rows as the running
    server — calling this right after the user hands over a key (``cloud key ...``) or picks a
    model (``cloud model ...``) makes the lane live immediately instead of after the next boot.
    Returns the provider id, or "" when no key is available (lane stays dormant).
    """
    from core.model_registry import ModelRegistry

    env_map: Mapping[str, str] = env if env is not None else dict(os.environ)
    try:
        return _ensure_openrouter_byok_provider(ModelRegistry(), env=env_map)
    except Exception:
        return ""


def deactivate_openrouter_byok() -> int:
    """Disable every OpenRouter burst-lane manifest (used when the key is forgotten).

    Without this, a stale enabled manifest survives the key deletion and selection can still
    route to a lane that can no longer authenticate. Returns how many manifests were disabled.
    """
    disabled = 0
    try:
        from storage.model_provider_manifest import list_provider_manifests, upsert_provider_manifest

        for manifest in list_provider_manifests(enabled_only=True):
            if manifest.provider_name == "openrouter-byok":
                upsert_provider_manifest(manifest.model_copy(update={"enabled": False}))
                disabled += 1
    except Exception:
        return disabled
    return disabled


# Capabilities for a direct-provider BYOK lane (mirrors OpenRouter's single-lane default set).
_DIRECT_BYOK_CAPS = ["summarize", "classify", "format", "extract", "code_basic", "code_complex", "structured_json", "long_context"]


def _register_provider_byok_manifest(
    registry: ModelRegistry,
    *,
    provider_id: str,
    model_name: str,
    env: Mapping[str, str],
    api_key_env: str,
    capabilities: list[str],
    chat_selection_only: bool = False,
) -> str:
    """Register a BYOK burst lane for a DIRECT provider (not openrouter — that has its own
    attribution-aware registrar). Built entirely from ``core.cloud_providers`` so base URL, slot,
    probe path and any static headers come from one table. cost_class stays paid_cloud so the
    daily cap + paid-fallback gate bind exactly as they do for OpenRouter."""
    from core.cloud_providers import config_for

    cfg = config_for(provider_id)
    if cfg is None or provider_id == "openrouter":
        return ""
    if cfg.auth_placement == "url_path_token":
        return _register_path_token_manifest(
            registry, cfg=cfg, model_name=model_name, capabilities=capabilities, chat_selection_only=chat_selection_only
        )
    provider_name = f"{provider_id}-byok"
    if provider_id == "custom":
        # The lane's destination comes from the ONE pair authority (review F3): the committed
        # endpoint+key read as a single admitted fact, so a lane is only ever registered
        # against a binding the store actually decided. An unresolved or incoherent pair
        # raises here and the lane stays un-registered (dormant) — the adapter would refuse
        # dispatch anyway; registering a lane over an undecided pair would be the mixed
        # binding this whole contract exists to prevent.
        from core.cloud_providers import resolved_custom_pair

        base_url = resolved_custom_pair()[0]
    else:
        base_url = (_env_first(env, *cfg.base_url_env_names) if cfg.base_url_env_names else "") or cfg.base_url
    if not base_url:
        return ""  # e.g. a custom endpoint whose base URL is not configured yet -> dormant
    existing = registry.get_manifest(provider_name, model_name)
    existing_base_url = str(getattr(existing, "runtime_config", {}).get("base_url") or "").strip() if existing else ""
    # Re-register when the RESOLVED base_url changed (e.g. a custom endpoint re-pointed to a new
    # host): a stale early-return would keep pointing the lane — and the freshly-entered key — at
    # the previous host. Only skip when the existing lane already matches this base_url + caps.
    if existing and existing.enabled and existing_base_url.rstrip("/") == base_url.rstrip("/") and list(existing.capabilities or []) == capabilities and (chat_selection_only or not existing.metadata.get("chat_selection_only")):
        return existing.provider_id
    runtime_config: dict[str, object] = {
        "base_url": base_url,
        "api_path": "/chat/completions",
        "health_path": cfg.probe_path,
        "timeout_seconds": 180,
        "health_timeout_seconds": 10,
        "temperature": 0.3,
        "supports_json_mode": True,
        "credential_key": cfg.credential_slot,
    }
    if api_key_env:
        runtime_config["api_key_env"] = api_key_env
    if cfg.extra_headers:
        runtime_config["headers"] = dict(cfg.extra_headers)
    manifest = ModelProviderManifest(
        provider_name=provider_name,
        model_name=model_name,
        source_type="http",
        adapter_type="openai_compatible",
        license_name="Provider",
        license_reference="user-managed",
        license_url_or_reference="user-managed",
        weight_location="external",
        redistribution_allowed=False,
        runtime_dependency="remote-openai-compatible-provider",
        notes=f"{cfg.label} (bring-your-own-key) burst lane — auto-registered when a {cfg.label} key is present.",
        capabilities=capabilities,
        runtime_config=runtime_config,
        metadata={
            "chat_selection_only": chat_selection_only,
            "runtime_family": "openai-compatible",
            "confidence_baseline": 0.78,
            "orchestration_role": "queen",
            "deployment_class": "remote",
            "cost_class": "paid_cloud",
            "context_window": 128000,
            "tool_support": ["structured_json", "code_complex"],
            "max_safe_concurrency": 2,
        },
        enabled=True,
    )
    registry.register_manifest(manifest)
    return manifest.provider_id


def _register_path_token_manifest(
    registry: ModelRegistry,
    *,
    cfg,
    model_name: str,
    capabilities: list[str],
    chat_selection_only: bool = False,
) -> str:
    """Register the lane for a provider whose credential is a URL path segment (UsePod).

    The manifest table persists ``runtime_config`` as plain JSON, so this manifest carries the ORIGIN
    and the credential SLOT name only; the adapter reads the token from the credential store at
    dispatch time. The dialect and payment transport come from the owner's lane preference. A price
    bound is approved per model elsewhere and is never implied by a lane existing.
    """
    from core.cloud_providers import resolved_base_url
    from core.usepod.descriptor import protocol_path
    from core.usepod.lane import load_lane_preference

    origin = resolved_base_url(cfg.provider_id)
    if not origin or not model_name:
        return ""
    lane, _lane_error = load_lane_preference()
    provider_name = f"{cfg.provider_id}-byok"
    lane_capabilities = list(capabilities) + (["tool_intent"] if "tool_intent" not in capabilities else [])
    runtime_config: dict[str, object] = {
        "base_url": origin,
        "api_path": protocol_path(lane.protocol),
        "protocol": lane.protocol,
        "transport_mode": lane.transport_mode,
        "timeout_seconds": 180,
        "health_timeout_seconds": 10,
        "temperature": 0.3,
        "supports_json_mode": False,
        "tool_dialect": "native",
        "credential_key": cfg.credential_slot,
    }
    existing = registry.get_manifest(provider_name, model_name)
    if (
        existing
        and existing.enabled
        and dict(existing.runtime_config or {}) == runtime_config
        and list(existing.capabilities or []) == lane_capabilities
        and (chat_selection_only or not existing.metadata.get("chat_selection_only"))
    ):
        return existing.provider_id
    manifest = ModelProviderManifest(
        provider_name=provider_name,
        model_name=model_name,
        source_type="http",
        adapter_type="usepod",
        license_name="Provider",
        license_reference="user-managed",
        license_url_or_reference="user-managed",
        weight_location="external",
        redistribution_allowed=False,
        runtime_dependency="remote-usepod-inference-marketplace",
        notes=f"{cfg.label} lane — routes and price bounds are approved per model; the token lives only in the credential store.",
        capabilities=lane_capabilities,
        runtime_config=runtime_config,
        metadata={
            "chat_selection_only": chat_selection_only,
            "runtime_family": "openai-compatible",
            "confidence_baseline": 0.75,
            "orchestration_role": "queen",
            "deployment_class": "remote",
            "cost_class": "paid_cloud",
            # Zero means unknown: the marketplace feed publishes no context window per model.
            "context_window": 0,
            "tool_support": ["structured_json", "code_complex", "tool_calls"],
            "max_safe_concurrency": 2,
        },
        enabled=True,
    )
    registry.register_manifest(manifest)
    return manifest.provider_id


def refresh_path_token_lanes(provider_id: str) -> list[str]:
    """Rewrite every enabled lane of a path-token provider with the owner's current lane preference.

    The dialect and payment transport live in each manifest's ``runtime_config``. After the owner changes
    them, existing lanes are re-registered in place (same provider name and model) so the next dispatch
    speaks the chosen protocol. Approved price bounds are untouched: they belong to the route store.
    """
    from core.cloud_providers import config_for
    from storage.model_provider_manifest import list_provider_manifests

    cfg = config_for(provider_id)
    if cfg is None or cfg.auth_placement != "url_path_token":
        return []
    registry = ModelRegistry()
    refreshed: list[str] = []
    for manifest in list_provider_manifests(enabled_only=True):
        if manifest.provider_name != f"{cfg.provider_id}-byok":
            continue
        registered = _register_path_token_manifest(
            registry,
            cfg=cfg,
            model_name=manifest.model_name,
            capabilities=[item for item in manifest.capabilities if item != "tool_intent"],
            chat_selection_only=bool(manifest.metadata.get("chat_selection_only")),
        )
        if registered:
            refreshed.append(registered)
    return refreshed


def _retired_or_chat_only(manifest: ModelProviderManifest) -> ModelProviderManifest:
    from core.cloud_escalation_policy import chat_model_is_selected

    if chat_model_is_selected(manifest.provider_name.removesuffix("-byok"), manifest.model_name):
        return manifest.model_copy(update={"metadata": {**manifest.metadata, "chat_selection_only": True}})
    return manifest.model_copy(update={"enabled": False})


def _retire_lanes(provider_name: str, *, keep: set[str]) -> None:
    """Disable every enabled manifest for ``provider_name`` whose model is not in ``keep``."""
    try:
        from storage.model_provider_manifest import list_provider_manifests, upsert_provider_manifest

        for stale in list_provider_manifests(enabled_only=True):
            if stale.provider_name == provider_name and stale.model_name not in keep:
                upsert_provider_manifest(_retired_or_chat_only(stale))
    except Exception:  # pragma: no cover - best-effort; the new lane still registers
        pass


def retire_nonactive_provider_lanes(active_provider_id: str) -> int:
    """Enforce the v1 "one active cloud provider" invariant: disable every BYOK burst lane that is
    NOT the active provider's. Without this, switching from provider A to B leaves A's lane enabled,
    so a stale or shared model id could route a paid burst to A while the policy/pill say B. Only
    ``*-byok`` lanes for KNOWN providers are touched — local/other manifests are never disabled.
    Returns the count disabled.
    """
    from core.cloud_providers import PROVIDERS

    keep_name = f"{str(active_provider_id or '').strip().lower()}-byok"
    byok_names = {f"{pid}-byok" for pid in PROVIDERS}
    disabled = 0
    try:
        from storage.model_provider_manifest import list_provider_manifests, upsert_provider_manifest

        for m in list_provider_manifests(enabled_only=True):
            if m.provider_name in byok_names and m.provider_name != keep_name:
                updated = _retired_or_chat_only(m)
                upsert_provider_manifest(updated)
                disabled += int(not updated.enabled)
    except Exception:  # pragma: no cover - best-effort; the active lane still registers
        return disabled
    return disabled


def _ensure_provider_byok_provider(registry: ModelRegistry, *, provider_id: str, env: Mapping[str, str]) -> str:
    """Register a BYOK burst lane for any configured provider. openrouter delegates to its own
    attribution-aware path (unchanged); every other provider uses the generic manifest above.
    Dormant until a key exists (env for the provider, or its encrypted-store slot)."""
    if provider_id == "openrouter":
        return _ensure_openrouter_byok_provider(registry, env=env)
    from core.cloud_providers import config_for

    cfg = config_for(provider_id)
    if cfg is None:
        return ""
    api_key_env = next((name for name in cfg.env_names if str(env.get(name) or "").strip()), "")
    has_vault_key = False
    try:
        from core import credential_store

        has_vault_key = bool(credential_store.has_credential(cfg.credential_slot))
    except Exception:
        has_vault_key = False
    if not api_key_env and not has_vault_key:
        return ""  # dormant until the user brings a key
    _restore_selected_chat_models(registry, provider_id=provider_id, env=env, api_key_env=api_key_env)
    # Model precedence: env override -> the persisted `cloud model` choice IF it is for THIS
    # provider -> the provider's default model.
    model_name = ""
    try:
        from core.cloud_escalation_policy import load_policy as _load_cloud_policy

        pol = _load_cloud_policy()
        if str(pol.provider or "").strip().lower() == provider_id and str(pol.model or "").strip():
            model_name = str(pol.model).strip()
    except Exception:
        model_name = ""
    model_name = model_name or cfg.default_model
    if not model_name:
        return ""  # nothing to register (e.g. custom without a default model)
    _retire_lanes(f"{provider_id}-byok", keep={model_name})
    return _register_provider_byok_manifest(
        registry, provider_id=provider_id, model_name=model_name, env=env, api_key_env=api_key_env, capabilities=_DIRECT_BYOK_CAPS,
    )


def _restore_selected_chat_models(registry: ModelRegistry, *, provider_id: str, env: Mapping[str, str], api_key_env: str) -> None:
    """Restore only saved chat choices after credential availability has been established.

    A key deletion disables manifests but intentionally retains chat preferences. Re-verification
    and startup must rebuild those preferences as explicit-only lanes, not automatic candidates.
    The ordinary execution gates still resolve credentials and reserve spend on every request.
    """
    from core.cloud_escalation_policy import selected_chat_models

    for model in selected_chat_models(provider_id):
        if provider_id == "openrouter":
            _register_openrouter_manifest(
                registry, model_name=model, env=env, api_key_env=api_key_env,
                capabilities=[*_DIRECT_BYOK_CAPS, "tool_intent"], chat_selection_only=True,
            )
        else:
            _register_provider_byok_manifest(
                registry, provider_id=provider_id, model_name=model, env=env, api_key_env=api_key_env,
                capabilities=_DIRECT_BYOK_CAPS, chat_selection_only=True,
            )


def register_chat_cloud_model(provider_id: str, model_name: str) -> str:
    """Make an explicit chat choice resolvable, without changing any default or retiring lanes.

    Registration performs no inference. Credential and spend gates remain at execution.
    A newly added lane is excluded from automatic ranking unless explicitly requested.
    """
    from core.cloud_providers import config_for

    cfg = config_for(provider_id)
    if cfg is None:
        raise ValueError("Unknown cloud provider")
    env = dict(os.environ)
    api_key_env = next((name for name in cfg.env_names if env.get(name)), "")
    registry = ModelRegistry()
    existing = registry.get_manifest(f"{provider_id}-byok", model_name)
    if existing and existing.enabled:
        return existing.provider_id
    if provider_id == "openrouter":
        return _register_openrouter_manifest(
            registry, model_name=model_name, env=env, api_key_env=api_key_env,
            capabilities=[*_DIRECT_BYOK_CAPS, "tool_intent"], chat_selection_only=True,
        )
    return _register_provider_byok_manifest(
        registry, provider_id=provider_id, model_name=model_name, env=env,
        api_key_env=api_key_env, capabilities=_DIRECT_BYOK_CAPS, chat_selection_only=True,
    )


def activate_provider_byok(provider_id: str, env: Mapping[str, str] | None = None) -> str:
    """Register (or refresh) a provider's BYOK burst lane NOW, without a restart. openrouter uses
    the existing proven path; other providers use the generic registrar. Returns the provider id
    or "" when no key is available (lane stays dormant)."""
    if provider_id == "openrouter":
        return activate_openrouter_byok(env)
    from core.model_registry import ModelRegistry

    env_map: Mapping[str, str] = env if env is not None else dict(os.environ)
    try:
        return _ensure_provider_byok_provider(ModelRegistry(), provider_id=provider_id, env=env_map)
    except Exception:
        return ""


def deactivate_provider_byok(provider_id: str) -> int:
    """Disable every BYOK burst-lane manifest for a provider (used when its key is forgotten).
    Returns how many manifests were disabled."""
    if provider_id == "openrouter":
        return deactivate_openrouter_byok()
    disabled = 0
    try:
        from storage.model_provider_manifest import list_provider_manifests, upsert_provider_manifest

        target = f"{provider_id}-byok"
        for manifest in list_provider_manifests(enabled_only=True):
            if manifest.provider_name == target:
                upsert_provider_manifest(manifest.model_copy(update={"enabled": False}))
                disabled += 1
    except Exception:
        return disabled
    return disabled


def _ensure_vllm_provider(
    registry: ModelRegistry,
    *,
    model_name: str,
    env: Mapping[str, str],
) -> str:
    base_url = _env_first(env, "VLLM_BASE_URL", "VOOL_VLLM_BASE_URL")
    if not base_url:
        return ""
    resolved_model_name = _env_first(env, "VLLM_MODEL", "VOOL_VLLM_MODEL") or model_name or default_runtime_model_tag(env=env)
    existing = registry.get_manifest("vllm-local", resolved_model_name)
    has_base_url = bool(str(getattr(existing, "runtime_config", {}).get("base_url") or "").strip()) if existing else False
    if existing and existing.enabled and has_base_url:
        return existing.provider_id
    context_window = _env_int(
        env,
        "VLLM_CONTEXT_WINDOW",
        "VOOL_VLLM_CONTEXT_WINDOW",
        default=_DEFAULT_VLLM_CONTEXT_WINDOW,
    )
    max_safe_concurrency = _env_int(
        env,
        "VLLM_MAX_SAFE_CONCURRENCY",
        "VOOL_VLLM_MAX_SAFE_CONCURRENCY",
        default=2,
    )
    manifest = ModelProviderManifest(
        provider_name="vllm-local",
        model_name=resolved_model_name,
        source_type="http",
        adapter_type="openai_compatible",
        license_name="User-managed",
        license_reference="user-managed",
        license_url_or_reference="user-managed",
        weight_location="external",
        runtime_dependency="vllm",
        notes="Local vLLM OpenAI-compatible lane — auto-registered when VLLM_BASE_URL is configured.",
        capabilities=["summarize", "classify", "format", "extract", "code_basic", "code_complex", "structured_json", "long_context"],
        runtime_config={
            "base_url": base_url,
            "api_path": "/chat/completions",
            "health_path": "/models",
            "timeout_seconds": 180,
            "health_timeout_seconds": 10,
            "temperature": 0.4,
            "supports_json_mode": True,
            "context_window": context_window,
        },
        metadata={
            "runtime_family": "openai-compatible",
            "confidence_baseline": 0.74,
            "orchestration_role": "queen",
            "deployment_class": "local",
            "context_window": context_window,
            "tool_support": ["structured_json", "tool_calls", "code_complex"],
            "max_safe_concurrency": max_safe_concurrency,
        },
        enabled=True,
    )
    registry.register_manifest(manifest)
    return manifest.provider_id


def _ensure_generic_remote_provider(
    registry: ModelRegistry,
    *,
    env: Mapping[str, str],
) -> str:
    api_key = _env_first(env, *_GENERIC_REMOTE_API_KEY_ENV_NAMES)
    if not api_key:
        return ""
    api_key_env = next(
        (name for name in _GENERIC_REMOTE_API_KEY_ENV_NAMES if str(env.get(name) or "").strip()),
        "OPENAI_API_KEY",
    )
    model_name = _env_first(env, *_GENERIC_REMOTE_MODEL_ENV_NAMES) or _DEFAULT_GENERIC_REMOTE_MODEL
    existing = registry.get_manifest("openai-compatible-remote", model_name)
    has_base_url = bool(str(getattr(existing, "runtime_config", {}).get("base_url") or "").strip()) if existing else False
    if existing and existing.enabled and has_base_url:
        return existing.provider_id
    base_url = _env_first(env, *_GENERIC_REMOTE_BASE_URL_ENV_NAMES) or _DEFAULT_GENERIC_REMOTE_BASE_URL
    manifest = ModelProviderManifest(
        provider_name="openai-compatible-remote",
        model_name=model_name,
        source_type="http",
        adapter_type="cloud_fallback_provider",
        license_name="Provider",
        license_reference="user-managed",
        license_url_or_reference="user-managed",
        weight_location="external",
        redistribution_allowed=False,
        runtime_dependency="remote-openai-compatible-provider",
        notes=(
            "Generic remote OpenAI-compatible fallback lane — auto-registered when "
            "OPENAI_API_KEY or VOOL_REMOTE_API_KEY is configured."
        ),
        capabilities=["summarize", "classify", "format", "extract", "code_basic", "code_complex", "structured_json", "long_context"],
        runtime_config={
            "base_url": base_url,
            "api_path": "/chat/completions",
            "health_path": "/models",
            "timeout_seconds": 180,
            "health_timeout_seconds": 10,
            "temperature": 0.3,
            "supports_json_mode": True,
            "api_key_env": api_key_env,
        },
        metadata={
            "runtime_family": "openai-compatible",
            "confidence_baseline": 0.75,
            "orchestration_role": "queen",
            "deployment_class": "cloud",
            "context_window": 128000,
            "tool_support": ["structured_json", "code_complex"],
            "max_safe_concurrency": 2,
        },
        enabled=True,
    )
    registry.register_manifest(manifest)
    return manifest.provider_id


def _ensure_tether_provider(
    registry: ModelRegistry,
    *,
    env: Mapping[str, str],
) -> str:
    api_key = _env_first(env, *_TETHER_API_KEY_ENV_NAMES)
    base_url = _env_first(env, *_TETHER_BASE_URL_ENV_NAMES)
    if not api_key or not base_url:
        return ""
    api_key_env = next((name for name in _TETHER_API_KEY_ENV_NAMES if str(env.get(name) or "").strip()), "TETHER_API_KEY")
    model_name = _env_first(env, *_TETHER_MODEL_ENV_NAMES) or _DEFAULT_TETHER_MODEL
    existing = registry.get_manifest("tether-remote", model_name)
    has_base_url = bool(str(getattr(existing, "runtime_config", {}).get("base_url") or "").strip()) if existing else False
    tagged_paid = str((getattr(existing, "metadata", {}) or {}).get("cost_class") or "") == "paid_cloud" if existing else False
    if existing and existing.enabled and has_base_url and tagged_paid:
        return existing.provider_id
    manifest = ModelProviderManifest(
        provider_name="tether-remote",
        model_name=model_name,
        source_type="http",
        adapter_type="openai_compatible",
        license_name="Provider",
        license_reference="user-managed",
        license_url_or_reference="user-managed",
        weight_location="external",
        redistribution_allowed=False,
        runtime_dependency="remote-openai-compatible-provider",
        notes="Tether remote lane via a user-managed OpenAI-compatible endpoint — auto-registered when TETHER_API_KEY and TETHER_BASE_URL are configured.",
        capabilities=["summarize", "classify", "format", "extract", "code_basic", "code_complex", "structured_json", "long_context"],
        runtime_config={
            "base_url": base_url,
            "api_path": "/chat/completions",
            "health_path": "/models",
            "timeout_seconds": 180,
            "health_timeout_seconds": 10,
            "temperature": 0.3,
            "supports_json_mode": True,
            "api_key_env": api_key_env,
        },
        metadata={
            "runtime_family": "openai-compatible",
            "confidence_baseline": 0.76,
            "orchestration_role": "queen",
            "deployment_class": "remote",
            # Paid remote lane — tag explicitly so the paid-fallback gate excludes it and the
            # meter attributes it as paid (not remote_unknown, which the gate lets through).
            "cost_class": "paid_cloud",
            "context_window": 128000,
            "tool_support": ["structured_json", "code_complex"],
            "max_safe_concurrency": 2,
        },
        enabled=True,
    )
    registry.register_manifest(manifest)
    return manifest.provider_id


def _ensure_llamacpp_provider(
    registry: ModelRegistry,
    *,
    model_name: str,
    env: Mapping[str, str],
) -> str:
    base_url = _env_first(
        env,
        "LLAMACPP_BASE_URL",
        "VOOL_LLAMACPP_BASE_URL",
        "LLAMA_CPP_BASE_URL",
        "VOOL_LLAMA_CPP_BASE_URL",
    )
    if not base_url:
        return ""
    resolved_model_name = _env_first(
        env,
        "LLAMACPP_MODEL",
        "VOOL_LLAMACPP_MODEL",
        "LLAMA_CPP_MODEL",
        "VOOL_LLAMA_CPP_MODEL",
    ) or model_name or default_runtime_model_tag(env=env)
    context_window = _env_int(
        env,
        "LLAMACPP_CONTEXT_WINDOW",
        "VOOL_LLAMACPP_CONTEXT_WINDOW",
        "LLAMA_CPP_CONTEXT_WINDOW",
        "VOOL_LLAMA_CPP_CONTEXT_WINDOW",
        default=_DEFAULT_LLAMACPP_CONTEXT_WINDOW,
    )
    max_safe_concurrency = _env_int(
        env,
        "LLAMACPP_MAX_SAFE_CONCURRENCY",
        "VOOL_LLAMACPP_MAX_SAFE_CONCURRENCY",
        "LLAMA_CPP_MAX_SAFE_CONCURRENCY",
        "VOOL_LLAMA_CPP_MAX_SAFE_CONCURRENCY",
        default=1,
    )
    existing = registry.get_manifest("llamacpp-local", resolved_model_name)
    existing_runtime_config = dict(getattr(existing, "runtime_config", {}) or {}) if existing else {}
    existing_metadata = dict(getattr(existing, "metadata", {}) or {}) if existing else {}
    existing_base_url = str(existing_runtime_config.get("base_url") or "").strip()
    if (
        existing
        and existing.enabled
        and existing_base_url == base_url
        and int(existing_runtime_config.get("context_window") or existing_metadata.get("context_window") or 0) == context_window
        and int(existing_metadata.get("max_safe_concurrency") or 0) == max_safe_concurrency
    ):
        return existing.provider_id
    manifest = ModelProviderManifest(
        provider_name="llamacpp-local",
        model_name=resolved_model_name,
        source_type="http",
        adapter_type="openai_compatible",
        license_name="User-managed",
        license_reference="user-managed",
        license_url_or_reference="user-managed",
        weight_location="external",
        runtime_dependency="llama.cpp",
        notes="Local llama.cpp OpenAI-compatible verifier/coding lane — auto-registered when LLAMACPP_BASE_URL is configured.",
        capabilities=["summarize", "classify", "format", "extract", "code_basic", "code_complex", "structured_json", "long_context"],
        runtime_config={
            "base_url": base_url,
            "api_path": "/chat/completions",
            "health_path": "/models",
            "timeout_seconds": 180,
            "health_timeout_seconds": 10,
            "temperature": 0.4,
            "supports_json_mode": True,
            "context_window": context_window,
        },
        metadata={
            "runtime_family": "openai-compatible",
            "confidence_baseline": 0.7,
            "orchestration_role": "drone",
            "deployment_class": "local",
            "context_window": context_window,
            "tool_support": ["structured_json", "code_complex"],
            "max_safe_concurrency": max_safe_concurrency,
        },
        enabled=True,
    )
    registry.register_manifest(manifest)
    return manifest.provider_id


def _ensure_llamacpp_deep_provider(
    registry: ModelRegistry,
    *,
    env: Mapping[str, str],
) -> str:
    base_url = _env_first(env, "VOOL_LLAMACPP_DEEP_BASE_URL", "LLAMACPP_DEEP_BASE_URL")
    if not base_url:
        return ""
    model_name = _env_first(env, "VOOL_LLAMACPP_DEEP_MODEL", "LLAMACPP_DEEP_MODEL")
    if not model_name:
        return ""
    context_window = _env_int(
        env,
        "VOOL_LLAMACPP_DEEP_CONTEXT_WINDOW",
        "LLAMACPP_DEEP_CONTEXT_WINDOW",
        default=4096,
    )
    max_safe_concurrency = _env_int(
        env,
        "VOOL_LLAMACPP_DEEP_MAX_SAFE_CONCURRENCY",
        "LLAMACPP_DEEP_MAX_SAFE_CONCURRENCY",
        default=1,
    )
    existing = registry.get_manifest("llamacpp-local", model_name)
    if existing and existing.enabled:
        return existing.provider_id
    manifest = ModelProviderManifest(
        provider_name="llamacpp-local",
        model_name=model_name,
        source_type="http",
        adapter_type="openai_compatible",
        license_name="User-managed",
        license_reference="user-managed",
        license_url_or_reference="user-managed",
        weight_location="external",
        runtime_dependency="llama.cpp",
        notes="Local llama.cpp deep/quality lane — auto-registered when VOOL_LLAMACPP_DEEP_BASE_URL is configured.",
        capabilities=["summarize", "classify", "format", "extract", "code_basic", "code_complex", "structured_json", "long_context"],
        runtime_config={
            "base_url": base_url,
            "api_path": "/chat/completions",
            "health_path": "/models",
            "timeout_seconds": 180,
            "health_timeout_seconds": 10,
            "temperature": 0.4,
            "supports_json_mode": True,
            "context_window": context_window,
        },
        metadata={
            "runtime_family": "openai-compatible",
            "confidence_baseline": 0.75,
            "orchestration_role": "queen",
            "deployment_class": "local",
            "context_window": context_window,
            "tool_support": ["structured_json", "code_complex"],
            "max_safe_concurrency": max_safe_concurrency,
        },
        enabled=True,
    )
    registry.register_manifest(manifest)
    return manifest.provider_id


def _ensure_mlx_provider(
    registry: ModelRegistry,
    *,
    model_name: str = "",
    env: Mapping[str, str],
) -> str:
    base_url = _env_first(env, "MLX_BASE_URL", "VOOL_MLX_BASE_URL")
    if not base_url:
        return ""
    resolved_model_name = (
        _env_first(env, "VOOL_MLX_MODEL", "MLX_MODEL") or model_name or _DEFAULT_MLX_MODEL
    )
    context_window = _env_int(
        env,
        "VOOL_MLX_CONTEXT_WINDOW",
        "MLX_CONTEXT_WINDOW",
        default=_DEFAULT_MLX_CONTEXT_WINDOW,
    )
    max_safe_concurrency = _env_int(
        env,
        "VOOL_MLX_MAX_SAFE_CONCURRENCY",
        "MLX_MAX_SAFE_CONCURRENCY",
        default=1,
    )
    existing = registry.get_manifest("mlx-local", resolved_model_name)
    existing_runtime_config = dict(getattr(existing, "runtime_config", {}) or {}) if existing else {}
    existing_metadata = dict(getattr(existing, "metadata", {}) or {}) if existing else {}
    existing_base_url = str(existing_runtime_config.get("base_url") or "").strip()
    if (
        existing
        and existing.enabled
        and existing_base_url == base_url
        and int(existing_runtime_config.get("context_window") or existing_metadata.get("context_window") or 0) == context_window
        and int(existing_metadata.get("max_safe_concurrency") or 0) == max_safe_concurrency
    ):
        return existing.provider_id
    manifest = ModelProviderManifest(
        provider_name="mlx-local",
        model_name=resolved_model_name,
        source_type="http",
        adapter_type="openai_compatible",
        license_name="User-managed",
        license_reference="user-managed",
        license_url_or_reference="user-managed",
        weight_location="external",
        runtime_dependency="mlx-lm",
        notes="Local MLX OpenAI-compatible lane — auto-registered when MLX_BASE_URL is configured.",
        capabilities=["summarize", "classify", "format", "extract", "code_basic", "code_complex", "structured_json", "long_context"],
        runtime_config={
            "base_url": base_url,
            "api_path": "/chat/completions",
            "health_path": "/models",
            "timeout_seconds": 180,
            "health_timeout_seconds": 10,
            "temperature": 0.4,
            "supports_json_mode": True,
            "context_window": context_window,
        },
        metadata={
            "runtime_family": "openai-compatible",
            "confidence_baseline": 0.75,
            "orchestration_role": "queen",
            "deployment_class": "local",
            "context_window": context_window,
            "tool_support": ["structured_json", "code_complex"],
            "max_safe_concurrency": max_safe_concurrency,
        },
        enabled=True,
    )
    registry.register_manifest(manifest)
    return manifest.provider_id


def parameter_size_for_model(model_tag: str) -> str:
    model_name = str(model_tag or "").strip().split("/", 1)[-1]
    if ":" not in model_name:
        return "7B"
    _, size = model_name.split(":", 1)
    return size.upper()


def _env_first(env: Mapping[str, str], *names: str) -> str:
    for name in names:
        value = str(env.get(name) or "").strip()
        if value:
            return value
    return ""


def _env_int(env: Mapping[str, str], *names: str, default: int) -> int:
    for name in names:
        value = str(env.get(name) or "").strip()
        if not value:
            continue
        try:
            return max(1, int(value))
        except Exception:
            continue
    return max(1, int(default))


def _resolve_ollama_num_gpu(env: Mapping[str, str]) -> int | None:
    """Resolve the forced Ollama GPU-layer count from VOOL_OLLAMA_NUM_GPU.

    Returns None when the env var is unset or unparseable so the manifest omits
    num_gpu entirely and Ollama decides layers as before (current behavior).
    A value of 0 means pure CPU and is preserved verbatim — do NOT gate this on
    truthiness, since 0 is the proven CPU-fallback lever on GPUs that crash.
    Negative values clamp to 0.
    """
    raw = str(env.get("VOOL_OLLAMA_NUM_GPU") or "").strip()
    if not raw:
        return None
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return None


def _num_gpu_matches(existing: object, expected: int | None, *, unset: object) -> bool:
    """Idempotency check for the persisted num_gpu against the forced value.

    expected None => the key must be absent (existing is the unset sentinel).
    expected int  => the persisted value must equal it. 0 must compare equal to
    0, never be treated as "unset".
    """
    if expected is None:
        return existing is unset
    if existing is unset:
        return False
    try:
        return int(existing) == expected  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def _default_free_disk_gb() -> float:
    try:
        usage = shutil.disk_usage(Path.home())
    except Exception:
        return 0.0
    return round(float(usage.free) / float(1024**3), 1)


def _profile_allows_aux_local_providers(profile_id: str, *, runtime_home: str | None = None) -> bool:
    if not profile_id:
        return True
    if profile_id in {"local-max", "full-orchestrated"}:
        return True
    if not runtime_home:
        return False
    # A live-verified llama.cpp GPU backend (core.llamacpp_capability_probe) unlocks the
    # aux local provider lane regardless of install profile — a measured speedup, not a
    # profile choice, is what earns this. Import kept local to avoid a hard dependency
    # for every runtime_provider_defaults caller that never touches GPU acceleration.
    try:
        from core.llamacpp_capability_probe import has_any_verified_gpu_backend

        return has_any_verified_gpu_backend(runtime_home)
    except Exception:
        return False


def _profile_allows_kimi_provider(profile_id: str) -> bool:
    if not profile_id:
        return True
    return profile_id in {"hybrid-kimi", "full-orchestrated"}


def _profile_allows_generic_remote_provider(profile_id: str) -> bool:
    if not profile_id:
        return True
    return profile_id in {"hybrid-fallback", "full-orchestrated"}


__all__ = [
    "default_runtime_model_tag",
    "ensure_default_runtime_providers",
    "preferred_fast_local_model",
]
