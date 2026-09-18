"""Durable persistence for the live GPU-inference verdict.

After the installer proves (or fails to prove) that a real token can be generated on
the GPU, the outcome is written to ``{runtime_home}/config/gpu_capability.json`` so the
runtime boot path can honour a prior CPU-fallback decision without re-running a warmup
generate on every start. The record is deliberately small and version-stamped
(``vool.gpu_capability.v1``).

Both reads and writes are bounded and fail-safe by construction: a missing file returns
None, a corrupt/partial file returns None, and a write failure is swallowed so it can
never break an install or a chat turn. No wall-clock timestamps are stamped here — the
record must round-trip deterministically, so any time value must be supplied by the
caller rather than read from the system clock.
"""
from __future__ import annotations

import contextlib
import json
import os
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

_SCHEMA = "vool.gpu_capability.v1"
_FILENAME = "gpu_capability.json"


def gpu_capability_path(runtime_home: str | Path) -> Path:
    """Return the on-disk location of the GPU-capability record for a runtime home."""
    return (Path(runtime_home).expanduser() / "config" / _FILENAME).resolve()


def _normalise_verdict(verdict_dict: dict[str, Any]) -> dict[str, Any]:
    """Coerce an arbitrary verdict mapping into the persisted v1 schema shape."""
    source = dict(verdict_dict or {})
    record: dict[str, Any] = {
        "schema": _SCHEMA,
        "outcome": str(source.get("outcome") or "").strip(),
        "model": str(source.get("model") or "").strip(),
        "driver_version": str(source.get("driver_version") or "").strip(),
        "applied_cpu_fallback": bool(source.get("applied_cpu_fallback")),
    }
    return record


def save_gpu_capability(runtime_home: str | Path, verdict_dict: dict[str, Any]) -> None:
    """Persist the GPU-capability verdict. Best-effort; a write failure never raises."""
    with contextlib.suppress(Exception):
        path = gpu_capability_path(runtime_home)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = _normalise_verdict(verdict_dict)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)


def load_gpu_capability(runtime_home: str | Path) -> dict[str, Any] | None:
    """Load the persisted verdict, or None if missing/corrupt (fail safe)."""
    try:
        path = gpu_capability_path(runtime_home)
    except Exception:
        return None
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None  # fail safe: corrupt file -> treat as no prior verdict
    if not isinstance(raw, dict):
        return None
    if str(raw.get("schema") or "").strip() != _SCHEMA:
        return None
    return _normalise_verdict(raw)


def apply_persisted_cpu_fallback(
    runtime_home: str | Path,
    *,
    env: MutableMapping[str, str] | None = None,
) -> bool:
    """Honour a prior CPU-fallback verdict by forcing the Ollama lane to CPU.

    Reads the persisted verdict and, when it says ``applied_cpu_fallback``, sets
    ``VOOL_OLLAMA_NUM_GPU=0`` in the target env (default: ``os.environ``) so the Ollama
    lane runs pure CPU without re-running a warmup generate. Returns True when the flag was
    applied. Never overrides an explicit ``VOOL_OLLAMA_NUM_GPU`` the operator already set,
    and never raises — a missing/corrupt verdict is a no-op.
    """
    target = os.environ if env is None else env
    try:
        if str(target.get("VOOL_OLLAMA_NUM_GPU") or "").strip():
            return False
        record = load_gpu_capability(runtime_home)
        if not record or not record.get("applied_cpu_fallback"):
            return False
        target["VOOL_OLLAMA_NUM_GPU"] = "0"
        return True
    except Exception:
        return False


__all__ = [
    "apply_persisted_cpu_fallback",
    "gpu_capability_path",
    "load_gpu_capability",
    "save_gpu_capability",
]
