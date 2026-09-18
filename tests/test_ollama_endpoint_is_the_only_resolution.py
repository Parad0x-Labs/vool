"""Local Ollama has ONE resolution in this runtime: `core.ollama_endpoint`.

Eight modules kept their own -- a literal `localhost:11434` parameter default, a module constant
read from one variable, a bare `OLLAMA_HOST` read -- so a daemon whose environment pointed every
endpoint at a dead port still reached the operator's real Ollama through fact extraction, craft
calls, the intent arbiter and the resource governor (measured 2026-09-07: two local models, 12.7 GB,
loaded on a machine whose rule is no local model launches). The census below is the invariant;
the behavioural checks prove the call-time resolvers follow the environment.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parents[1]
OWNER = REPO / "core" / "ollama_endpoint.py"
LITERAL = re.compile(r"[\"'](?:https?://)?(?:127\.0\.0\.1|localhost|0\.0\.0\.0):11434")
DIRECT_READ = re.compile(
    r"environ(?:\.get)?\(\s*[\"'](?:OLLAMA_HOST|VOOL_OLLAMA_URL|VOOL_RAW_OLLAMA_API_URL)[\"']"
    r"|environ\[\s*[\"'](?:OLLAMA_HOST|VOOL_OLLAMA_URL|VOOL_RAW_OLLAMA_API_URL)[\"']\s*\]"
)


def _runtime_sources() -> list[Path]:
    files: list[Path] = []
    for root in ("core", "apps", "tools"):
        files.extend(p for p in (REPO / root).rglob("*.py") if "tests" not in p.parts and "__pycache__" not in p.parts)
    return files


def test_no_module_but_the_owner_names_the_ollama_port() -> None:
    offenders = [
        f"{path.relative_to(REPO)}:{number}"
        for path in _runtime_sources()
        if path != OWNER
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
        if LITERAL.search(line) and not line.lstrip().startswith("#")
    ]
    assert offenders == [], offenders


def test_no_module_but_the_owner_reads_the_base_endpoint_variables() -> None:
    offenders = [
        f"{path.relative_to(REPO)}:{number}"
        for path in _runtime_sources()
        if path != OWNER
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
        if DIRECT_READ.search(line) and not line.lstrip().startswith("#")
    ]
    assert offenders == [], offenders


@pytest.fixture
def dead_ollama(monkeypatch):
    for name in ("VOOL_RAW_OLLAMA_API_URL", "VOOL_OLLAMA_URL", "VOOL_OLLAMA_CHAT_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:9")


def test_fact_extractor_follows_the_environment(dead_ollama) -> None:
    from core.fact_extractor import FactExtractor

    extractor = FactExtractor(memory=mock.Mock())
    assert extractor._ollama_url == "http://127.0.0.1:9"


def test_craft_client_follows_the_environment(dead_ollama) -> None:
    from core import craft_upgrade

    seen: list[str] = []

    def fake_post(url, *args, **kwargs):
        seen.append(str(url))
        raise OSError("dead port, as intended")

    with mock.patch.object(craft_upgrade.requests, "post", side_effect=fake_post):
        client = craft_upgrade.ollama_client("some-model")
        with pytest.raises((OSError, RuntimeError, ValueError)):
            client("hello")
    assert seen and seen[0].startswith("http://127.0.0.1:9/")


def test_builder_and_arbiter_follow_the_environment(dead_ollama) -> None:
    from core.agent_runtime.builder.controller import builder_ollama_base_url
    from core.intent_arbiter import _ollama_chat_url

    assert builder_ollama_base_url({}) == "http://127.0.0.1:9"
    assert _ollama_chat_url() == "http://127.0.0.1:9/api/chat"
