"""The most memory each local model has been seen to hold on this machine, and the everyday share Auto may give one.

A model's weights are not what it costs. Ollama's MLX engine grows qwen3.5:9b by 0.1-0.2 GB on every request and never
gives it back: on the 24 GB gate iMac it went from 8.2 GB to 16.4 GB in one session, and turns slowed to 71 s while the
machine swapped (speed thread report, 2026-11-27; CUT-LIST 66). Auto picked it by its size on disk every time.

So Ollama's own reading of what a loaded model holds (`/api/ps`, read by `core.memory_first_router`) is kept here as a
per-model peak, and Auto counts a model at the most it has held. A model that has held more than the everyday share
of this machine's memory is not Auto's pick while another local model is; the person can still pick it by name. Any
engine, any model, any machine size: a 64 GB Mac keeps a model that settles at 16 GB, a 24 GB Mac does not.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

_log = logging.getLogger(__name__)

#: Auto's everyday model may hold at most this share of the machine's memory: the rest is macOS, the apps the person
#: has open, VOOL itself and the judgement model beside it.
EVERYDAY_SHARE_OF_RAM = 0.5
#: A peak is written again only when it rises by this much, so the file is not rewritten on every reading.
_MIN_RISE_GB = 0.1
_FILENAME = "local_model_footprints.json"
_lock = threading.Lock()
_cache: dict[str, dict] | None = None


def _path() -> Path:
    from core.runtime_paths import active_data_dir

    return active_data_dir() / _FILENAME


def _key(tag: str) -> str:
    key = str(tag or "").strip().lower()
    return key.removesuffix(":latest")


def _load() -> dict[str, dict]:
    global _cache
    if _cache is None:
        try:
            data = json.loads(_path().read_text(encoding="utf-8"))
            _cache = {str(k): dict(v) for k, v in (data.get("models") or {}).items() if isinstance(v, dict)}
        except (OSError, ValueError, AttributeError):
            _cache = {}
    return _cache


def record_resident(rows: list[dict] | tuple[dict, ...]) -> None:
    """Keep each loaded model's peak from Ollama's readings ([{name, size_gb}]). Never raises."""
    try:
        with _lock:
            models = _load()
            changed = False
            for row in rows or ():
                key = _key(row.get("name"))
                size = float(row.get("size_gb") or 0.0)
                if not key or size <= 0.0:
                    continue
                held = models.get(key) or {}
                if size >= float(held.get("peak_gb") or 0.0) + _MIN_RISE_GB:
                    models[key] = {"peak_gb": round(size, 1), "seen_at": int(time.time())}
                    changed = True
            if changed:
                path = _path()
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps({"models": models}, indent=1, sort_keys=True), encoding="utf-8")
                tmp.replace(path)
    except Exception as exc:
        _log.info("model footprint record not written (%s)", type(exc).__name__)


def peak_resident_gb(tag: str) -> float:
    """The most memory this model has been seen to hold here, 0.0 when never seen loaded."""
    try:
        with _lock:
            return float((_load().get(_key(tag)) or {}).get("peak_gb") or 0.0)
    except Exception:
        return 0.0


def everyday_budget_gb() -> float | None:
    """The most memory Auto's everyday model may hold on this machine; None when the machine's memory is unknown."""
    try:
        from core.system_resources import snapshot

        total = float(snapshot().total_ram_gb)
    except Exception:
        return None
    return total * EVERYDAY_SHARE_OF_RAM if total > 0 else None


def outgrows_everyday_memory(tag: str, *, budget_gb: float | None = None) -> bool:
    budget = everyday_budget_gb() if budget_gb is None else budget_gb
    peak = peak_resident_gb(tag)
    return bool(budget) and peak > float(budget)


def _reset_for_tests() -> None:
    global _cache
    with _lock:
        _cache = None
