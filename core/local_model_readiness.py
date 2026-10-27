"""Plain readiness for local models, the recommended model, and its one-click download.

Settings shows a person one word per installed model instead of the certification machinery
underneath it. The machinery is unchanged: a model is only ``ready`` when its latest tool
certification is ``verified``, and the final-answer authority still refuses every other model.
This module only translates the verdict, and separates "the model cannot do it" from "the check
could not finish" so a slow or stopped Ollama is never blamed on the model.

The download is an explicit person action (a button), limited to the models VOOL recommends and
to an Ollama on this machine. Nothing here runs on boot.
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

# Recommended models, largest first, with the RAM they need to run beside VOOL and the download
# size shown to the person. Both are Qwen3 tags that call tools natively.
RECOMMENDED_LOCAL_MODELS: tuple[tuple[str, float, float], ...] = (
    ("qwen3:8b", 12.0, 5.2),
    ("qwen3:4b", 0.0, 2.5),
)


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


def recommended_local_model(ram_gb: float | None = None) -> dict[str, Any]:
    if ram_gb is None:
        try:
            from core.hardware_tier import _detect_ram_gb

            ram_gb = _detect_ram_gb()
        except Exception:
            ram_gb = 0.0
    for tag, min_ram_gb, download_gb in RECOMMENDED_LOCAL_MODELS:
        if float(ram_gb or 0.0) >= min_ram_gb:
            return {"model_name": tag, "download_gb": download_gb}
    tag, _min, download_gb = RECOMMENDED_LOCAL_MODELS[-1]
    return {"model_name": tag, "download_gb": download_gb}


def is_recommended_tag(model_name: str) -> bool:
    return str(model_name or "").strip() in {tag for tag, _ram, _gb in RECOMMENDED_LOCAL_MODELS}


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
