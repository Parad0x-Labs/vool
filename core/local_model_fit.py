"""Will a local model make this computer lag? One answer for every place a person picks a model.

Owner's 24 GB iMac, 2026-11-27: VOOL used qwen3.8:27b (18 GB) for the small checks on every
message and macOS swapped 8 GB. The automatic choice is fixed where it is made (the daily lane in
core/local_inference_autopilot.py). This module is for the other side: a person choosing a model
by hand in the first-run picker, in Settings or in the chat's model menu. VOOL may offer a model
bigger than the one it would pick, but then it says plainly that the computer may lag.

No new size rule lives here. A model may lag when:
  * it does not fit this machine on its own: ``provider_hardware_fit_rejection`` with the resident
    size from ``estimated_local_model_resident_gb`` (the router's own gate) says so;
  * it does not fit next to the models already loaded: the same gate, asked with their resident
    sizes added, because Ollama keeps them in the same memory;
  * it is one the daily lane never keeps resident: 24B or more and not a no-thinking build, the
    exact test ``prioritize_daily_residency_capabilities`` applies.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

#: Why a model may make this computer lag ("" means it should not).
LAG_TOO_BIG = "too_big"
LAG_BESIDE_LOADED = "beside_loaded"
LAG_HEAVY = "heavy"


def _probe(probe: Any) -> Any:
    if probe is not None:
        return probe
    try:
        from core.hardware_tier import probe_machine

        return probe_machine()
    except Exception:
        return None


def _capability(tag: str, resident_gb: float):
    from core.local_model_bundles import model_metadata
    from core.provider_routing import ProviderCapabilityTruth

    metadata = model_metadata(tag)
    return ProviderCapabilityTruth(
        provider_id=f"ollama-local:{tag}",
        model_id=tag,
        role_fit=str(metadata.get("role") or "general").strip() or "general",
        context_window=int(metadata.get("max_context") or 4096),
        tool_support=("structured_json",),
        structured_output_support=True,
        tokens_per_second=0.0,
        ram_budget_gb=resident_gb,
        vram_budget_gb=resident_gb,
        quantization=str(metadata.get("quantization") or ""),
        locality="local",
        privacy_class="local_private",
        queue_depth=0,
        max_safe_concurrency=1,
        availability_state="ready",
        measurement_source="installed_inventory",
    )


def resident_gb(tag: str, size_gb: float = 0.0) -> float:
    from core.provider_routing import estimated_local_model_resident_gb

    return float(estimated_local_model_resident_gb(tag, artifact_size_gb=max(0.0, float(size_gb or 0.0))))


def daily_lane_keeps_resident(tag: str) -> bool:
    """The daily lane's own size test (``prioritize_daily_residency_capabilities``)."""
    from core.local_inference_autopilot import _is_fast_nothink_default
    from core.provider_routing import model_parameter_billions

    return model_parameter_billions(tag) < 24.0 or _is_fast_nothink_default(tag)


def local_model_lag(
    tag: str,
    *,
    size_gb: float = 0.0,
    probe: Any = None,
    loaded: Mapping[str, float] | None = None,
) -> str:
    """Why ``tag`` may make this computer lag, or "" when it should run smoothly.

    ``size_gb`` is the model's download or installed size (0 = unknown: the parameter estimate is
    used). ``loaded`` maps the models Ollama holds in memory now to their sizes; the model itself
    is left out of that sum. An unreadable machine never invents a warning: the gate reports an
    unknown capacity, and only a 24B-or-bigger model is still flagged by the daily-lane test.
    """
    from core.provider_routing import provider_hardware_fit_rejection

    clean = str(tag or "").strip()
    if not clean:
        return ""
    machine = _probe(probe)
    own = resident_gb(clean, size_gb)
    if machine is not None:
        if provider_hardware_fit_rejection(_capability(clean, own), probe=machine) == "model_exceeds_hardware_budget":
            return LAG_TOO_BIG
        others = sum(
            resident_gb(name, size)
            for name, size in dict(loaded or {}).items()
            if str(name or "").strip() and str(name).strip() != clean
        )
        if others > 0.0 and provider_hardware_fit_rejection(
            _capability(clean, own + others), probe=machine
        ) == "model_exceeds_hardware_budget":
            return LAG_BESIDE_LOADED
    if not daily_lane_keeps_resident(clean):
        return LAG_HEAVY
    return ""


def loaded_model_sizes(timeout_seconds: float = 2.0) -> dict[str, float]:
    """The models Ollama holds in memory now, with their installed sizes in GB (read only)."""
    try:
        from core.local_ollama_inventory import loaded_ollama_model_names
        from core.web.api.runtime import installed_ollama_model_inventory

        loaded = set(loaded_ollama_model_names(timeout_seconds=timeout_seconds))
        if not loaded:
            return {}
        sizes = {item.name: float(item.size_bytes or 0) / 1e9 for item in installed_ollama_model_inventory(timeout_seconds=timeout_seconds)}
        return {name: sizes.get(name, 0.0) for name in loaded}
    except Exception:
        return {}


def _installed_sizes(timeout_seconds: float = 3.0) -> dict[str, float]:
    try:
        from core.web.api.runtime import installed_ollama_model_inventory

        return {item.name: float(item.size_bytes or 0) / 1e9 for item in installed_ollama_model_inventory(timeout_seconds=timeout_seconds)}
    except Exception:
        return {}


def _runs_here_at_all(tag: str, size_gb: float, machine: Any) -> bool:
    """A model bigger than all of this machine's memory is never offered, warning or not."""
    if machine is None:
        return True
    ram = float(getattr(machine, "ram_gb", 0.0) or 0.0) if not isinstance(machine, Mapping) else float(machine.get("ram_gb") or 0.0)
    vram = float(getattr(machine, "vram_gb", 0.0) or 0.0) if not isinstance(machine, Mapping) else float(machine.get("vram_gb") or 0.0)
    room = max(ram, vram)
    return room <= 0.0 or resident_gb(tag, size_gb) <= room


def first_run_choices(
    *,
    probe: Any = None,
    free_disk_gb: float | None = None,
    ollama_version: str | None = None,
    installed: Mapping[str, float] | None = None,
    loaded: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """The free local models the first-run picker offers on this computer.

    Every model comes from the one suggestion table (core/local_model_suggestions.py). The model
    VOOL would pick for daily use comes first and is ticked, with the small helper that keeps the
    quick checks on every message off the big model. Smaller models are offered for older
    computers, bigger ones with the lag reason from ``local_model_lag``. A model that cannot land
    on the free disk, or is bigger than all of this machine's memory, is not offered. Reading this
    downloads nothing: a download starts only from the person's own click.
    """
    from core.local_model_readiness import _local_ollama_version, _model_disk_free_gb
    from core.local_model_suggestions import (
        PREVIOUS_GENERATION_DOWNLOAD_GB,
        SUGGESTED_MODELS,
        TINY_HELPER_MODEL,
        download_gb_for,
        ollama_too_old,
        suggest_local_models,
    )

    machine = _probe(probe)
    if free_disk_gb is None:
        free_disk_gb = _model_disk_free_gb()
    if ollama_version is None:
        ollama_version = _local_ollama_version()
    have = dict(_installed_sizes() if installed is None else installed)
    in_memory = dict(loaded_model_sizes() if loaded is None else loaded)
    suggestion = suggest_local_models(
        machine if machine is not None else {"ram_gb": 0.0, "accelerator": "cpu"},
        free_disk_gb=free_disk_gb,
        ollama_version=ollama_version or "",
    )
    kind = suggestion.machine_kind
    old = ollama_too_old(ollama_version or "")

    def size_of(tag: str) -> float:
        if tag in have and have[tag] > 0.0:
            return round(have[tag], 1)
        if tag in SUGGESTED_MODELS:
            return download_gb_for(tag, kind)
        return PREVIOUS_GENERATION_DOWNLOAD_GB.get(tag, 0.0)

    def fits_disk(tag: str) -> bool:
        if tag in have or free_disk_gb is None or free_disk_gb <= 0.0:
            return True
        return size_of(tag) + 4.0 <= float(free_disk_gb)

    tags: list[str] = []
    for tag in SUGGESTED_MODELS:
        offered = SUGGESTED_MODELS[tag].previous_generation if old else tag
        if offered not in tags:
            tags.append(offered)
    best = suggestion.daily
    if best not in tags:
        tags.append(best)
    tags.sort(key=lambda t: (size_of(t), t))
    best_size = size_of(best)
    options: list[dict[str, Any]] = []

    def option(tag: str, role: str, chosen: bool) -> dict[str, Any]:
        gb = size_of(tag)
        return {
            "model_name": tag,
            "download_gb": gb,
            "installed": tag in have,
            "role": role,
            "chosen": chosen and tag not in have,
            "lag": local_model_lag(tag, size_gb=gb, probe=machine, loaded=in_memory),
        }

    options.append(option(best, "best", True))
    helper = TINY_HELPER_MODEL
    if helper != best:
        options.append(option(helper, "helper", True))
    for tag in tags:
        if tag in {best, helper} or not fits_disk(tag) or not _runs_here_at_all(tag, size_of(tag), machine):
            continue
        entry = option(tag, "smaller", False)
        # Bigger, or heavier to run (a dense model can be smaller on disk and still lag).
        if size_of(tag) >= best_size or entry["lag"]:
            entry["role"] = "bigger"
        options.append(entry)
    return {
        "options": options,
        "machine_kind": kind,
        "sized_by_gb": round(suggestion.sized_by_gb, 1),
        "ollama_update_unlocks": suggestion.ollama_update_unlocks,
    }


def lag_for_listed_models(models: Iterable[Mapping[str, Any]], *, probe: Any = None, loaded: Mapping[str, float] | None = None) -> dict[str, str]:
    """``model_name -> lag reason`` for installed rows (the chat's model menu, Settings)."""
    machine = _probe(probe)
    in_memory = dict(loaded or {})
    out: dict[str, str] = {}
    for row in models:
        name = str(row.get("model_name") or "").strip()
        if name:
            out[name] = local_model_lag(name, size_gb=float(row.get("size_gb") or 0.0), probe=machine, loaded=in_memory)
    return out


__all__ = [
    "LAG_BESIDE_LOADED",
    "LAG_HEAVY",
    "LAG_TOO_BIG",
    "daily_lane_keeps_resident",
    "first_run_choices",
    "lag_for_listed_models",
    "loaded_model_sizes",
    "local_model_lag",
    "resident_gb",
]
