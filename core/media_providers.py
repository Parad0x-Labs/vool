"""Pluggable media-generation providers (image + video) for the credential-backed media tools.

One small, parameterized adapter describes how to talk to a provider: the auth scheme, whether the
prompt is wrapped in an ``input`` object, and how a media reference is pulled out of the response.
Built-ins cover the common shapes (a generic/OpenAI-style endpoint, RunPod serverless, Replicate,
Fal); a new provider is one ``register_media_provider`` call, mirroring the WalletProvider seam in
``core/wallet_spend_tools.py``. Credentials live in the encrypted store as
``image.api.<account>`` / ``video.api.<account>`` = {endpoint, api_key, provider?, params?}.

The request/response shaping here is best-effort and deterministic (unit-tested), but each real
provider's exact fields and any async job polling need a live smoke test with real keys before you
rely on them in production.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Keys that, when present in a response, carry the media reference (checked in order).
_REF_KEYS = ("url", "image_url", "video_url", "audio_url", "output", "ref", "result", "media_url")


@dataclass(frozen=True)
class MediaProvider:
    """How to build a request for a provider and read the media ref back.

    ``auth_scheme`` is the Authorization prefix (Bearer/Token/Key). ``wrap_input`` puts the payload
    under an ``input`` object (RunPod/Replicate) vs. at the top level (generic/Fal). ``extra_headers``
    are merged in. Parsing is shared via ``extract_media_ref`` so any provider that returns a URL (or
    an async poll URL under ``urls.get``) works without a bespoke parser.
    """
    name: str
    auth_scheme: str = "Bearer"
    wrap_input: bool = False
    extra_headers: dict[str, str] = field(default_factory=dict)

    def headers(self, api_key: str) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if api_key:
            h["Authorization"] = f"{self.auth_scheme} {api_key}".strip()
        h.update(self.extra_headers)
        return h

    def body(self, prompt: str, extra: dict | None = None) -> dict:
        payload: dict[str, Any] = {"prompt": str(prompt)}
        if isinstance(extra, dict):
            payload.update({k: v for k, v in extra.items() if k not in ("version",)})
        if self.wrap_input:
            wrapped: dict[str, Any] = {"input": payload}
            if isinstance(extra, dict) and extra.get("version"):
                wrapped["version"] = extra["version"]   # Replicate-style model version at top level
            return wrapped
        return payload


_BUILTINS: dict[str, MediaProvider] = {
    "generic": MediaProvider("generic", "Bearer", wrap_input=False),
    "openai": MediaProvider("openai", "Bearer", wrap_input=False),
    "runpod": MediaProvider("runpod", "Bearer", wrap_input=True),
    "replicate": MediaProvider("replicate", "Token", wrap_input=True),
    "fal": MediaProvider("fal", "Key", wrap_input=False),
}
_REGISTRY: dict[str, MediaProvider] = dict(_BUILTINS)


def register_media_provider(provider: MediaProvider) -> None:
    """Register (or override) a provider adapter by name. Lets a deployment add a provider without
    touching the media tools -- same pattern as register_wallet_provider."""
    _REGISTRY[provider.name.strip().lower()] = provider


def get_media_provider(name: str) -> MediaProvider:
    """Resolve a provider by name, falling back to the generic OpenAI-style adapter."""
    return _REGISTRY.get(str(name or "generic").strip().lower(), _BUILTINS["generic"])


def provider_names() -> list[str]:
    return sorted(_REGISTRY)


def extract_media_ref(payload: Any, *, _depth: int = 0, _keyed: bool = False) -> str:
    """Best-effort pull of a media URL (or async poll URL) out of a provider response.

    Values under a known ref key are accepted as-is (a URL or an id); values found by deep scan must
    look like URLs. Handles nested dicts/lists and Replicate-style ``urls.get`` poll links.
    """
    if _depth > 6:
        return ""
    if isinstance(payload, str):
        text = payload.strip()
        if _keyed and text:
            return text
        return text if text.startswith("http") else ""
    if isinstance(payload, dict):
        for key in _REF_KEYS:
            if payload.get(key) is not None:
                found = extract_media_ref(payload[key], _depth=_depth + 1, _keyed=True)
                if found:
                    return found
        urls = payload.get("urls")
        if isinstance(urls, dict) and urls.get("get"):
            return str(urls["get"])           # async job: return the poll URL for the caller to follow
        for value in payload.values():
            found = extract_media_ref(value, _depth=_depth + 1)
            if found:
                return found
    if isinstance(payload, list):
        for item in payload:
            found = extract_media_ref(item, _depth=_depth + 1, _keyed=_keyed)
            if found:
                return found
    return ""


__all__ = [
    "MediaProvider",
    "extract_media_ref",
    "get_media_provider",
    "provider_names",
    "register_media_provider",
]
