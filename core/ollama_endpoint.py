"""Where local Ollama lives: ONE resolution, from the environment, for every client in the runtime.

Order: ``VOOL_RAW_OLLAMA_API_URL`` (the raw API the runtime fronts), then Ollama's own ``OLLAMA_HOST`` (a bare
``host:port`` is accepted, as Ollama itself accepts it), then ``VOOL_OLLAMA_URL``, then the default. Every module
that talks to Ollama -- provider manifests, embeddings, summaries, fact extraction, inventories -- resolves through
here at call time, so a launch that points these variables at a dead port reaches no local model at all. Found
the hard way on 2026-09-07: two clients carried the literal default and an isolated test build loaded a model on
the operator's Ollama.
"""
from __future__ import annotations

import os
from collections.abc import Mapping

DEFAULT_OLLAMA_BASE = "http://127.0.0.1:11434"
_ENV_ORDER = ("VOOL_RAW_OLLAMA_API_URL", "OLLAMA_HOST", "VOOL_OLLAMA_URL")


def _first_named(env_map: Mapping[str, str]) -> str:
    for name in _ENV_ORDER:
        value = str(env_map.get(name) or "").strip()
        if value:
            return value
    return ""


def ollama_base_url(env: Mapping[str, str] | None = None) -> str:
    """A mapping that names no endpoint follows the PROCESS environment, never the literal default: callers hand
    over curated mappings (install profiles, request contexts) that carry no Ollama variable, and an isolated
    launch fences the process environment -- the fence must reach them too."""
    raw = _first_named(env) if env is not None else ""
    raw = raw or _first_named(os.environ) or DEFAULT_OLLAMA_BASE
    if not raw.startswith(("http://", "https://")):
        raw = f"http://{raw}"
    return raw.rstrip("/")


def ollama_api_url(path: str, env: Mapping[str, str] | None = None) -> str:
    return f"{ollama_base_url(env)}/{str(path or '').lstrip('/')}"


__all__ = ["DEFAULT_OLLAMA_BASE", "ollama_api_url", "ollama_base_url"]
