"""Plain readiness for local models, the recommended model, and its one-click download.

Settings shows a person one word per installed model instead of the certification machinery
underneath it. The machinery is unchanged: a model is only ``ready`` when its latest tool
certification is ``verified``, and the final-answer authority still refuses every other model.
This module only translates the verdict, and separates "the model cannot do it" from "the check
could not finish" so a slow or stopped Ollama is never blamed on the model.

The download is an explicit person action (a button), limited to the models VOOL recommends and
to an Ollama on this machine. The one thing that runs on boot is ``ready_local_models``: a profile
that never checked any model (every profile before 0.7.7, and a fresh install) would otherwise
refuse every model answer, so boot checks the installed local models itself until one is ready.
"""
from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any

READY = "ready"
NOT_CHECKED = "not_checked"
CHECKING = "checking"
NEEDS_RECHECK = "needs_recheck"
NOT_SUITABLE = "not_suitable"
CHECK_UNFINISHED = "check_unfinished"

# A failure with one of these codes, or a refused request, says nothing about the model's skill:
# Ollama was down, busy or slow. The person is told to try again, not to pick another model.
_UNFINISHED_CODES = frozenset({"timeout", "probe_error"})

def plain_readiness(status: Mapping[str, Any] | None) -> str:
    """One of the plain states above for a certification status or run record."""
    if not isinstance(status, Mapping):
        return NOT_CHECKED
    state = str(status.get("state") or "").strip().lower()
    if state == "verified":
        return READY
    if state == "probing":
        return CHECKING
    if state == "stale":
        return NEEDS_RECHECK
    if state == "incompatible":
        return NOT_SUITABLE
    if state != "degraded":
        return NOT_CHECKED
    stages = status.get("stages")
    failed = [
        (name, stage)
        for name, stage in (stages.items() if isinstance(stages, Mapping) else ())
        if isinstance(stage, Mapping) and stage.get("state") == "failed"
    ]
    if not failed:
        return CHECK_UNFINISHED
    for name, stage in failed:
        if name == "transport_acceptance" or str(stage.get("failure_code") or "") in _UNFINISHED_CODES:
            return CHECK_UNFINISHED
    return NOT_SUITABLE


def _local_ollama_version(timeout_seconds: float = 2.0) -> str:
    """The local Ollama's own version string, or "" when it cannot be read (never blocks long).

    Only a loopback Ollama is asked; the read goes through the local Ollama inventory's own
    transport, so this module opens no connection of its own."""
    from urllib.parse import urlparse

    try:
        from core.local_ollama_inventory import local_ollama_version
        from core.runtime_guard import is_loopback_host
        from core.web.api.runtime import ollama_base_url

        base = str(ollama_base_url() or "").rstrip("/")
        if not base or not is_loopback_host(str(urlparse(base).hostname or "")):
            return ""
        return local_ollama_version(base_url=base, timeout_seconds=timeout_seconds)
    except Exception:
        return ""


def _model_disk_free_gb() -> float | None:
    try:
        from core.runtime_install_profiles import _disk_free_gb, _nearest_existing_path, default_ollama_models_path

        return float(_disk_free_gb(_nearest_existing_path(default_ollama_models_path())))
    except Exception:
        return None


def recommended_local_model(
    ram_gb: float | None = None,
    *,
    probe: Any = None,
    free_disk_gb: float | None = None,
    ollama_version: str | None = None,
) -> dict[str, Any]:
    """The model the Download button offers on this computer, from the one suggestion table.

    ``ram_gb`` alone sizes a computer without a usable GPU (kept for callers that only know RAM);
    otherwise the live hardware probe decides Apple / NVIDIA / CPU sizing.
    """
    from core.local_model_suggestions import download_gb_for, suggest_local_models

    if probe is None:
        if ram_gb is not None:
            probe = {"ram_gb": float(ram_gb or 0.0), "accelerator": "cpu"}
        else:
            try:
                from core.hardware_tier import probe_machine

                probe = probe_machine()
            except Exception:
                probe = {"ram_gb": 0.0, "accelerator": "cpu"}
    if free_disk_gb is None and ram_gb is None:
        free_disk_gb = _model_disk_free_gb()
    if ollama_version is None:
        ollama_version = "" if ram_gb is not None else _local_ollama_version()
    suggestion = suggest_local_models(probe, free_disk_gb=free_disk_gb, ollama_version=ollama_version)
    result: dict[str, Any] = {
        "model_name": suggestion.daily,
        "download_gb": download_gb_for(suggestion.daily, suggestion.machine_kind),
        "machine_kind": suggestion.machine_kind,
        "sized_by_gb": round(suggestion.sized_by_gb, 1),
    }
    if suggestion.deep:
        result["bigger"] = {
            "model_name": suggestion.deep,
            "download_gb": download_gb_for(suggestion.deep, suggestion.machine_kind),
        }
    if suggestion.fast:
        result["faster"] = {
            "model_name": suggestion.fast,
            "download_gb": download_gb_for(suggestion.fast, suggestion.machine_kind),
        }
    if suggestion.ollama_update_unlocks:
        result["ollama_update_unlocks"] = suggestion.ollama_update_unlocks
    return result


def is_recommended_tag(model_name: str) -> bool:
    from core.local_model_suggestions import suggested_tags

    return str(model_name or "").strip() in suggested_tags()


def boot_adoptable_tags(*, probe: Any = None) -> tuple[str, ...]:
    """Suggested tags boot may register and check when no registered model is ready.

    This computer's daily pick first, then every other tag the table can suggest (current and
    previous generation), smallest download first: boot needs one model that can answer, and a
    smaller one loads on a busy machine. Only these tags: boot never adopts an arbitrary model.
    """
    from core.local_model_suggestions import download_gb, suggested_tags

    try:
        daily = str(recommended_local_model(probe=probe, free_disk_gb=0.0, ollama_version="").get("model_name") or "")
    except Exception:
        daily = ""
    rest = sorted((tag for tag in suggested_tags() if tag != daily), key=lambda tag: (download_gb(tag), tag))
    return tuple(([daily] if daily else []) + rest)


_PULLS: dict[str, Any] = {}
_PULLS_LOCK = threading.Lock()


def pull_status(model_name: str) -> dict[str, Any]:
    with _PULLS_LOCK:
        progress = _PULLS.get(str(model_name or "").strip())
    if progress is None:
        return {"model": str(model_name or ""), "status": "idle", "percent": 0}
    public = dict(progress.public)
    public.pop("detail", None)
    return public


def start_recommended_pull(model_name: str) -> dict[str, Any]:
    """Start (or join) the download of one recommended model and return its live status."""
    from core.web.api.runtime import ModelPullProgress, ensure_ollama_model

    tag = str(model_name or "").strip()
    if not is_recommended_tag(tag):
        raise ValueError("only a recommended model can be downloaded here")
    with _PULLS_LOCK:
        running = _PULLS.get(tag)
        joined = running is not None and running.status in {"starting", "pulling"}
        if not joined:
            progress = ModelPullProgress(tag)
            _PULLS[tag] = progress
    if joined:
        return pull_status(tag)

    def _run() -> None:
        try:
            ensure_ollama_model(tag, progress=progress)
        except Exception:
            progress.update(status="failed")
        finally:
            progress.finish()

    threading.Thread(target=_run, name=f"vool-model-pull-{tag}", daemon=True).start()
    return pull_status(tag)


# States boot checks on its own: never checked, or checked under an older Ollama/model build.
# A model that already failed is not re-run on every boot; the person can press Try again.
_BOOT_CHECK_STATES = frozenset({"unknown", "stale"})


def _check_ran_out_of_time(status: Mapping[str, Any] | None) -> bool:
    """The last check of this model stopped on a timeout or probe error, so it never judged the model.

    Measured 2026-11-13 on the Mac (test VOOL on f50e52eb, fresh home): the first boot check of qwen3:8b
    ran out of time on its last stage while other jobs shared the machine (emission alone took 118 s).
    Later boots skipped it as "already failed", so every model answer on that home was refused with
    "qwen3:8b hasn't passed VOOL's answer check yet" until someone pressed Try again.
    """
    if not isinstance(status, Mapping) or str(status.get("state") or "").strip().lower() != "degraded":
        return False
    stages = status.get("stages")
    return any(
        isinstance(stage, Mapping)
        and stage.get("state") == "failed"
        and str(stage.get("failure_code") or "") in _UNFINISHED_CODES
        for stage in (stages.values() if isinstance(stages, Mapping) else ())
    )
# At most this many models are checked per boot (each check takes up to a couple of minutes).
_BOOT_CHECK_LIMIT = 3


def ready_local_models(
    manifests: Any,
    *,
    primary_model: str = "",
    status_of: Any = None,
    run_check: Any = None,
    limit: int = _BOOT_CHECK_LIMIT,
    ready_now: Any = None,
    fits: Any = None,
) -> list[dict[str, str]]:
    """Make sure one installed local model is ready to answer, without asking the person.

    Nothing runs when a local model is already ready. Otherwise the boot primary is checked
    first, then the other local models, stopping at the first one that turns ready. Only models
    the check can reach (an Ollama on this computer) are considered, and the check itself is the
    same sealed one the Use this model button runs, so the final-answer rule is unchanged. Returns
    one ``{"model", "readiness"}`` row per model checked.
    """

    from core.local_model_tool_certification import (
        certification_applies,
        certification_status,
        run_local_model_tool_certification,
    )

    status_of = status_of or certification_status
    run_check = run_check or (lambda m: run_local_model_tool_certification(m, timeout_seconds=150.0))
    primary = str(primary_model or "").strip().lower()
    candidates: list[tuple[Any, str]] = []
    seen: set[str] = set()
    for manifest in manifests or ():
        name = str(getattr(manifest, "model_name", "") or "").strip().lower()
        if not name or name in seen or not certification_applies(manifest):
            continue
        seen.add(name)
        try:
            status = status_of(manifest) or {}
            state = str(status.get("state") or "unknown").strip().lower()
        except Exception:
            continue
        if state == "verified":
            return []
        if name == primary and _check_ran_out_of_time(status):
            # The boot primary's check never finished: run it again, as a first check would.
            state = "unknown"
        candidates.append((manifest, state))
    candidates.sort(key=lambda item: 0 if str(item[0].model_name).strip().lower() == primary else 1)
    checked: list[dict[str, str]] = []
    for manifest, state in candidates:
        if len(checked) >= max(0, int(limit)):
            break
        if state not in _BOOT_CHECK_STATES:
            continue
        # A model became ready meanwhile (the person pressed Use this model, or setup certified
        # one): stop -- every further check only loads a model nobody needs. And never check a
        # model this machine cannot hold: measured on the 0.7.8 sweep (2026-11-12, block 1b), boot
        # certified a 19 GB qwen3.8:27b on a 24 GB Mac while qwen3:8b was already being set up.
        try:
            if ready_now is not None and ready_now():
                break
            if fits is not None and not fits(manifest):
                checked.append({"model": str(manifest.model_name), "readiness": "too_large_for_this_computer"})
                continue
        except Exception:
            pass
        try:
            result = run_check(manifest)
        except Exception:
            result = {"state": "degraded"}
        readiness = plain_readiness(result)
        checked.append({"model": str(manifest.model_name), "readiness": readiness})
        if readiness == READY:
            break
    return checked


# --- the boot readiness check in progress -------------------------------------------------------
#: Set while `ready_a_local_model_on_boot` is checking models. Measured 2026-11-12 (0.7.8 sweep, fresh
#: profile): the first answer was refused as written by an unchecked qwen3:4b while the boot check
#: was still certifying qwen3:8b a minute away. A turn that finds no checked writer while this is
#: set waits for the check, bounded, instead of refusing.
import threading as _threading

_BOOT_CHECK_RUNNING = _threading.Event()


def _model_being_checked() -> str:
    """The local model whose readiness check is running now, or ``""``."""
    try:
        from core.local_model_policy import manifest_is_local
        from core.local_model_tool_certification import certification_applies, certification_status
        from core.model_registry import ModelRegistry

        for manifest in ModelRegistry().list_manifests(enabled_only=True):
            if manifest_is_local(manifest) and certification_applies(manifest):
                if str(certification_status(manifest).get("state") or "") == "probing":
                    return str(manifest.model_name)
    except Exception:
        pass
    return ""


def model_in_use(boot_model: str) -> str:
    """The local model VOOL answers with now, for what it tells the person.

    Boot names its primary before the boot check has run; when that model is not ready and
    another local model is, turns answer with the ready one, so that is the one to name.
    It reads the certification store directly (no cache): a label read at boot must never leave a
    "not ready" verdict behind for the first turns after the model was certified.
    """

    tag = str(boot_model or "").strip()
    try:
        from core.final_answer_authorship import author_certification, unready_local_model_names
        from core.local_model_policy import manifest_is_local
        from core.model_registry import ModelRegistry

        manifests = [m for m in ModelRegistry().list_manifests(enabled_only=True) if manifest_is_local(m)]
        if not any(author_certification(m, use_cache=False).certified for m in manifests):
            # Nothing is ready yet. While boot (or the button) is making a model ready, that is the
            # model VOOL is about to use, not the boot guess the check will replace.
            return _model_being_checked() or tag
        if tag.lower() not in unready_local_model_names(manifests):
            return tag
        for manifest in manifests:
            if author_certification(manifest, use_cache=False).certified:
                return str(manifest.model_name)
    except Exception:
        pass
    return tag


def local_model_for_work(preferred: str) -> str:
    """The local model a background job (the website/app builder) should generate with.

    ``default_runtime_model_tag`` is the machine's recommendation, not what is installed: on the
    cloud re-audit boxes it named qwen3:4b, which was never pulled, and every "make a one page site"
    failed in three seconds with "the model did not return a usable file list" (10 of 10). The
    ready model goes first (the one the person pressed Use this model on), then the recommendation
    if it is a registered local model, then any registered local model; the recommendation only
    when nothing local is registered at all.
    """

    tag = str(preferred or "").strip()
    try:
        from core.final_answer_authorship import author_certification
        from core.local_model_policy import manifest_is_local
        from core.model_registry import ModelRegistry

        manifests = [
            m
            for m in ModelRegistry().list_manifests(enabled_only=True)
            if manifest_is_local(m) and str(getattr(m, "provider_name", "")).startswith("ollama")
        ]
        names = [str(m.model_name) for m in manifests]
        ready = [str(m.model_name) for m in manifests if author_certification(m, use_cache=False).certified]
        for pool in (ready, names):
            if not pool:
                continue
            for name in pool:
                if name.lower() == tag.lower():
                    return name
            return pool[0]
    except Exception:
        pass
    return tag


def mark_boot_readiness_check(running: bool) -> None:
    if running:
        _BOOT_CHECK_RUNNING.set()
    else:
        _BOOT_CHECK_RUNNING.clear()


def boot_readiness_check_running() -> bool:
    return _BOOT_CHECK_RUNNING.is_set()


def wait_for_boot_readiness_check(timeout_seconds: float) -> bool:
    """Wait until the boot check ends. True when it is no longer running."""
    import time

    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    while _BOOT_CHECK_RUNNING.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(1.0, remaining))
    return True
