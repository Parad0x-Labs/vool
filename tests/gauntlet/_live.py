"""Shared helpers for the gauntlet_live lane (on-box, real Ollama).

Not a test module (leading underscore -> pytest does not collect it). Every
gauntlet_live file imports LIVE_GATE and marks itself so the whole file SKIPS in
the default CI lane; on-box it runs against the canonical acceptance profile.

Why a hard gate and not a soft skip: tests/conftest.py AssertionErrors on any
socket to 127.0.0.1:11434 unless VOOL_ALLOW_LIVE_OLLAMA_TESTS=1 (or
VOOL_ALPHA_LIVE_SOAK=1). So a live test that reaches Ollama without the env would
FAIL, not skip — LIVE_GATE (a skipif on the same env) prevents collection-time
execution in the default lane, and require_live_provider() fails loudly on-box if
the pinned model is missing.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from apps.vool_api_server import _ensure_default_provider
from core.model_registry import ModelRegistry

REPO_ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE_PROFILE_PATH = REPO_ROOT / "config" / "acceptance" / "local_ollama_bundle_profile.json"
LIVE_MODEL = str(json.loads(ACCEPTANCE_PROFILE_PATH.read_text(encoding="utf-8"))["model"])
_OPENCLAW = {"surface": "openclaw", "platform": "openclaw", "requested_model": LIVE_MODEL}


def live_enabled() -> bool:
    return (
        os.environ.get("VOOL_ALLOW_LIVE_OLLAMA_TESTS") == "1"
        or os.environ.get("VOOL_ALPHA_LIVE_SOAK") == "1"
    )


# Applied as a module-level pytestmark on every live file, so it skips in default CI.
LIVE_GATE = pytest.mark.skipif(
    not live_enabled(),
    reason="live Ollama lane — set VOOL_ALLOW_LIVE_OLLAMA_TESTS=1 (or VOOL_ALPHA_LIVE_SOAK=1) and run on-box",
)


def require_live_provider() -> None:
    """Fail loudly (not skip) if the pinned model isn't served — a silent model
    swap must not pass as a green live run."""
    probe = subprocess.run(
        ["curl", "-sSf", "http://127.0.0.1:11434/api/tags"],
        capture_output=True, text=True, check=False,
    )
    if probe.returncode != 0:
        pytest.fail(f"live provider probe failed: {probe.stderr or probe.stdout}")
    payload = json.loads(probe.stdout or "{}")
    names = {str(item.get("name") or "").strip() for item in list(payload.get("models") or [])}
    if LIVE_MODEL not in names:
        pytest.fail(f"required live model {LIVE_MODEL} missing from Ollama tags: {sorted(names)}")


def build_live_agent(make_agent):
    """A real agent pinned to LIVE_MODEL, so scoring reflects the shipped model."""
    agent = make_agent()
    registry = ModelRegistry()
    _ensure_default_provider(registry, LIVE_MODEL)
    for manifest in registry.list_manifests(enabled_only=False):
        registry.register_manifest(
            manifest.model_copy(update={"enabled": manifest.model_name == LIVE_MODEL})
        )
    agent.memory_router.registry = registry
    return agent


def run_once_text(agent, prompt: str, session_id: str) -> str:
    result = run_once_result(agent, prompt, session_id)
    return str((result or {}).get("response") or "")


def run_once_result(agent, prompt: str, session_id: str, *, workspace: Path | None = None) -> dict:
    source_context = dict(_OPENCLAW)
    if workspace is not None:
        source_context.update({"workspace": str(workspace), "workspace_root": str(workspace)})
    return agent.run_once(prompt, session_id_override=session_id, source_context=source_context)
