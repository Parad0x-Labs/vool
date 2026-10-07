"""Whether a local model can actually answer on this machine right now.

`core.local_model_policy` answers whether local models are PERMITTED. This answers whether one
is RUNNING: a machine with no Ollama (an old Intel Mac, a small Windows laptop, a cloud-only
install) is permitted to run local models and still has none. Before this existed, Auto routed
those turns to a registered-but-dead local lane and refused, and nothing could tell the user
"no local model is running, choose your cloud model".

Running means: local models are enabled, and at least one local endpoint answers its model
listing with a chat model -- Ollama's ``/api/tags`` with a non-embedding model, or a registered
local OpenAI-compatible lane's ``/models``. The probe is short and cached, so callers on the
chat path pay at most one bounded check per ``_TTL_SECONDS``. Any probe failure reads as "not
running" for that endpoint; it never raises.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from dataclasses import dataclass

_TTL_SECONDS = 30.0
_PROBE_TIMEOUT_SECONDS = 0.75

_lock = threading.Lock()
_cached: tuple[float, LocalModelPresence] | None = None


@dataclass(frozen=True)
class LocalModelPresence:
    running: bool
    reason: str  # "running" | "local_models_disabled" | "no_local_endpoint_answered"
    endpoint: str = ""

    def to_dict(self) -> dict[str, object]:
        return {"running": self.running, "reason": self.reason, "endpoint": self.endpoint}


def _get_json(url: str) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=_PROBE_TIMEOUT_SECONDS) as response:
            data = json.loads(response.read().decode("utf-8") or "{}")
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _listed_chat_models(data: dict) -> list[str] | None:
    """The chat models in an Ollama ``/api/tags`` listing, or None when the listing is malformed.

    Every level is checked: ``models`` is a list, each row a mapping with a string name, ``details``
    (when present) a mapping, and its ``families`` (when present) a list of strings. A listing that
    breaks any of these is not trusted at all: a row with the wrong shape says nothing reliable
    about what this endpoint can run.
    """
    models = data.get("models")
    if models is None:
        return []
    if not isinstance(models, list):
        return None
    chat: list[str] = []
    for row in models:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            return None
        details = row.get("details")
        if details is None:
            details = {}
        if not isinstance(details, dict):
            return None
        families = details.get("families")
        if families is None:
            families = []
        if not isinstance(families, list) or not all(isinstance(item, str) for item in families):
            return None
        name = row["name"].strip().lower()
        if name and "embed" not in name and not any("bert" in family.lower() for family in families):
            chat.append(name)
    return chat


def _ollama_has_chat_model(base_url: str) -> bool:
    data = _get_json(f"{base_url}/api/tags")
    if not data:
        return False
    return bool(_listed_chat_models(data))  # a malformed listing (None) reads as unavailable


def _lane_lists_models(data: dict | None) -> bool:
    """An OpenAI-compatible ``/models`` reply that lists at least one model, by its documented shape.

    Every row must name its model by a nonblank id; a blank id names nothing a turn could be sent to,
    so a listing carrying one reads as malformed, the same as a row with no id.
    """
    rows = (data or {}).get("data")
    return isinstance(rows, list) and bool(rows) and all(
        isinstance(row, dict) and isinstance(row.get("id"), str) and bool(row["id"].strip()) for row in rows
    )


def _local_lane_endpoints() -> list[str]:
    try:
        from core.local_model_policy import manifest_is_local
        from storage.model_provider_manifest import list_provider_manifests

        manifests = list_provider_manifests(enabled_only=True)
    except Exception:
        return []
    endpoints: list[str] = []
    for manifest in manifests:
        if manifest.provider_name.startswith("ollama") or not manifest_is_local(manifest):
            continue
        base_url = str((manifest.runtime_config or {}).get("base_url") or "").strip().rstrip("/")
        if base_url and base_url not in endpoints:
            endpoints.append(base_url)
    return endpoints


def _probe() -> LocalModelPresence:
    from core.local_model_policy import local_models_enabled
    from core.ollama_endpoint import ollama_base_url

    if not local_models_enabled():
        return LocalModelPresence(running=False, reason="local_models_disabled")
    base_url = ollama_base_url()
    if _ollama_has_chat_model(base_url):
        return LocalModelPresence(running=True, reason="running", endpoint=base_url)
    for endpoint in _local_lane_endpoints():
        if _lane_lists_models(_get_json(f"{endpoint}/models")):
            return LocalModelPresence(running=True, reason="running", endpoint=endpoint)
    return LocalModelPresence(running=False, reason="no_local_endpoint_answered")


def local_model_presence(*, refresh: bool = False) -> LocalModelPresence:
    global _cached
    now = time.monotonic()
    with _lock:
        if not refresh and _cached is not None and now - _cached[0] < _TTL_SECONDS:
            return _cached[1]
    presence = _probe()
    with _lock:
        _cached = (time.monotonic(), presence)
    return presence


def local_model_running(*, refresh: bool = False) -> bool:
    return local_model_presence(refresh=refresh).running


def reset_cache() -> None:
    global _cached
    with _lock:
        _cached = None


__all__ = ["LocalModelPresence", "local_model_presence", "local_model_running", "reset_cache"]
