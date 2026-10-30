"""The owner's answer to "no local model runs here -- answer with your cloud model?", asked once.

Cloud use is opt-in in this runtime, and stays opt-in: nothing here routes a turn to a cloud
model the owner did not name. What was missing is the place to name it. On a machine with no
local model (no Ollama, or local models switched off), Auto had nothing to answer with and every
new chat refused until the owner found the model selector, chat by chat. A pasted key alone
even ticked the setup step while Auto kept refusing.

This module owns ``data/cloud_only_default.json`` and nothing else:

* ``model``    -- the registered cloud provider id the owner chose (e.g.
                  ``openai-compatible-remote:gpt-4.1-mini``), or empty;
* ``decision`` -- ``use_cloud`` | ``not_now`` | empty (never asked).

`default_for_auto_turn` is the one reader on the chat path: it returns the saved model only for
an owner-local turn on Auto, only while no local model is running, and only while that model is
still registered. When a local model comes back, Auto is local again with no further change.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
import time
from typing import Any

from core.runtime_paths import active_data_dir

SCHEMA_VERSION = 1
FILENAME = "cloud_only_default.json"
DECISION_USE_CLOUD = "use_cloud"
DECISION_NOT_NOW = "not_now"
_DECISIONS = {DECISION_USE_CLOUD, DECISION_NOT_NOW}

_lock = threading.RLock()


class CloudOnlyDefaultError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _path():
    return active_data_dir() / FILENAME


def _fresh() -> dict[str, Any]:
    return {"version": SCHEMA_VERSION, "model": "", "decision": "", "decided_at": ""}


def load() -> dict[str, Any]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except Exception:
        return _fresh()
    if not isinstance(data, dict) or data.get("version") != SCHEMA_VERSION:
        return _fresh()
    decision = str(data.get("decision") or "")
    model = str(data.get("model") or "").strip()
    if decision not in _DECISIONS or (decision == DECISION_USE_CLOUD and not model):
        return _fresh()
    return {
        "version": SCHEMA_VERSION,
        "model": model if decision == DECISION_USE_CLOUD else "",
        "decision": decision,
        "decided_at": str(data.get("decided_at") or ""),
    }


def _write(data: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".cloud_only_default-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def cloud_model_candidates() -> list[dict[str, str]]:
    """Registered, enabled, non-local chat providers -- the ones a turn can name by id."""
    try:
        from core.local_model_policy import manifest_is_local
        from storage.model_provider_manifest import list_provider_manifests

        manifests = list_provider_manifests(enabled_only=True)
    except Exception:
        return []
    rows: list[dict[str, str]] = []
    for manifest in manifests:
        if manifest_is_local(manifest):
            continue
        rows.append({"id": manifest.provider_id, "label": manifest.model_name, "provider": manifest.provider_name})
    return rows


def choose(model: str) -> dict[str, Any]:
    clean = str(model or "").strip()
    if not clean:
        raise CloudOnlyDefaultError("model_required", "choose a cloud model")
    if clean not in {row["id"] for row in cloud_model_candidates()}:
        raise CloudOnlyDefaultError(
            "model_not_registered",
            f"{clean} is not a registered cloud model on this runtime; add its key in Settings first",
        )
    data = {
        "version": SCHEMA_VERSION,
        "model": clean,
        "decision": DECISION_USE_CLOUD,
        "decided_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with _lock:
        _write(data)
    return data


def decline() -> dict[str, Any]:
    data = {
        "version": SCHEMA_VERSION,
        "model": "",
        "decision": DECISION_NOT_NOW,
        "decided_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with _lock:
        _write(data)
    return data


def clear() -> dict[str, Any]:
    """Settings "review setup": forget the answer so the question is asked again."""
    with _lock:
        with contextlib.suppress(FileNotFoundError):
            _path().unlink()
    return _fresh()


def snapshot(*, refresh: bool = False) -> dict[str, Any]:
    from core.local_model_presence import local_model_presence

    presence = local_model_presence(refresh=refresh)
    state = load()
    candidates = cloud_model_candidates()
    return {
        "local_model_running": presence.running,
        "local_model_reason": presence.reason,
        "model": state["model"],
        "decision": state["decision"],
        "decided_at": state["decided_at"],
        "candidates": candidates,
        # Ask once: only when nothing local can answer, the owner has not answered yet, and
        # there is at least one cloud model to offer (with none, the setup step asks for a key).
        "needs_choice": (not presence.running) and not state["decision"] and bool(candidates),
    }


def default_for_auto_turn(*, owner_local: bool) -> str:
    """The saved cloud model for an owner-local Auto turn while no local model runs, else ""."""
    if not owner_local:
        return ""
    state = load()
    if state["decision"] != DECISION_USE_CLOUD or not state["model"]:
        return ""
    from core.local_model_presence import local_model_running

    if local_model_running():
        return ""
    if state["model"] not in {row["id"] for row in cloud_model_candidates()}:
        return ""
    return state["model"]


def no_route_hint() -> str:
    """Why a turn found no model, when the reason is "nothing local runs here", else "".

    Shown after the runtime's own "I couldn't get a live model response in this run" lead, which
    the history authority already recognises as a failure notice.
    """
    try:
        from core.local_model_presence import local_model_running

        if local_model_running():
            return ""
        has_cloud = bool(cloud_model_candidates())
    except Exception:
        return ""
    if has_cloud:
        return (
            "no local model is running on this computer, and cloud models only answer once you "
            "choose one. Pick a cloud model in the model selector, or save one in Setup under "
            "\u201cWhere should it think?\u201d so Auto uses it while no local model runs."
        )
    return (
        "no local model is running on this computer and no cloud model is set up. Add a cloud "
        "key in Settings \u2192 API Keys, or start a local model."
    )


def resolve_auto_turn_model(requested_model: str, *, auto_aliases: set[str], owner_local: bool) -> tuple[str, bool]:
    """The model a chat turn should name, and whether the saved cloud default supplied it.

    A turn that names a concrete model (a pin, or Auto's per-chat sticky model) is returned
    untouched. Only an Auto turn (no model, or one of the served Auto aliases) can receive the
    saved default, under `default_for_auto_turn`'s conditions.
    """
    clean = str(requested_model or "").strip()
    if clean and clean not in auto_aliases:
        return clean, False
    saved = default_for_auto_turn(owner_local=owner_local)
    if saved:
        return saved, True
    return clean, False


__all__ = [
    "CloudOnlyDefaultError",
    "choose",
    "clear",
    "cloud_model_candidates",
    "decline",
    "default_for_auto_turn",
    "load",
    "no_route_hint",
    "resolve_auto_turn_model",
    "snapshot",
]
