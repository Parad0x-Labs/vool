"""Every Ollama client in the runtime resolves its endpoint the same way, from the environment, so an isolated launch
can fence them all with one dead port.

Found 2026-09-07 while proving a test build loads no local model: the embedding service and the default-provider
manifest carried the literal 127.0.0.1:11434, so an app launched with OLLAMA_HOST at a dead port still loaded
`nomic-embed-text` on the operator's Ollama and ran its certification chat against an already-loaded model.

The behavioural tests never touch a real Ollama: a trap on urllib records and refuses any request to port 11434, and a
loopback stub plays Ollama at the address the environment names.
"""
from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from core import local_ollama_inventory as _inventory

# captured at import, before the autouse conftest fixture replaces it for every test: this file exercises the
# inventory boundary itself against a loopback stub (the conftest's seal on 127.0.0.1:11434 and the trap below stay in force)
_REAL_INVENTORY_PAYLOAD = _inventory._ollama_models_payload

ROOT = Path(__file__).resolve().parents[1]
LITERAL = re.compile(r"(127\.0\.0\.1|localhost):11434")
ENV_AWARE = re.compile(r"(os\.environ|env_map|env)\.get\(|environ\[")
# Files that may still name the port, each with the reason. Anything else that names it is a hole.
ALLOWED = {
    "core/ollama_endpoint.py": "the one place the default lives",
    "core/craft_upgrade.py": "parameter defaults of a developer-time upgrade helper",
    "core/fact_extractor.py": "parameter default; the runtime constructs it with the resolved URL",
    "core/local_model_admission.py": "a log excerpt in a docstring",
    "core/kernel/repl.py": "developer REPL entry point, not the app",
    "installer/provider_probe.py": "env-aware resolver's final default (VOOL_RAW_OLLAMA_API_URL, OLLAMA_HOST first); doctor-only caller",
    "installer/register_openclaw_agent.py": "env-aware resolver's final default (VOOL_RAW_OLLAMA_API_URL, OLLAMA_HOST first)",
    "installer/bundle/bundle_supervisor.py": "the Windows .exe supervisor's health probe for its bundled ollama.exe; Windows lane (KAS) owns the file; not run by the macOS app",
    "installer/vool_stop.py": "prose in a docstring",
}
SCANNED_TREES = ("core", "installer")


class _Stub(BaseHTTPRequestHandler):
    seen: list[str] = []

    def log_message(self, *_a):
        pass

    def _send(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        _Stub.seen.append(self.path)
        if self.path.startswith("/api/tags"):
            return self._send({"models": [{"name": "nomic-embed-text:latest", "details": {"parameter_size": "137M"}}, {"name": "qwen3:0.6b", "details": {"parameter_size": "0.6B"}}]})
        self._send({})

    def do_POST(self):
        _Stub.seen.append(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        if self.path.startswith("/api/embed"):
            return self._send({"embeddings": [[0.1] * 768]})
        if self.path.startswith("/api/chat"):
            return self._send({"message": {"role": "assistant", "content": "summary"}})
        self._send({})


@pytest.fixture
def stub():
    _Stub.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def trap(monkeypatch):
    """Refuse -- and record -- any request to the real Ollama port; everything else goes through."""
    attempts: list[str] = []
    real = urllib.request.urlopen

    def guarded(req, *args, **kwargs):
        url = req if isinstance(req, str) else req.full_url
        if ":11434" in url:
            attempts.append(url)
            raise urllib.error.URLError(ConnectionRefusedError("trap: real Ollama port"))
        return real(req, *args, **kwargs)

    monkeypatch.setattr(urllib.request, "urlopen", guarded)
    return attempts


def test_the_resolver_reads_the_environment_in_one_order(monkeypatch):
    from core.ollama_endpoint import DEFAULT_OLLAMA_BASE, ollama_base_url

    for name in ("VOOL_RAW_OLLAMA_API_URL", "OLLAMA_HOST", "VOOL_OLLAMA_URL"):
        monkeypatch.delenv(name, raising=False)
    assert ollama_base_url({}) == DEFAULT_OLLAMA_BASE == "http://127.0.0.1:11434"
    # a curated mapping that names no endpoint follows the process environment (the fence), never the literal
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:9")
    assert ollama_base_url({}) == "http://127.0.0.1:9"
    assert ollama_base_url({"VOOL_PROFILE": "x"}) == "http://127.0.0.1:9"
    assert ollama_base_url({"OLLAMA_HOST": "http://b:2"}) == "http://b:2", "a mapping that names one wins"
    monkeypatch.delenv("OLLAMA_HOST")
    assert ollama_base_url({"OLLAMA_HOST": "127.0.0.1:9"}) == "http://127.0.0.1:9", "Ollama's own bare host:port convention"
    assert ollama_base_url({"VOOL_OLLAMA_URL": "http://127.0.0.1:7/"}) == "http://127.0.0.1:7"
    assert ollama_base_url({"VOOL_RAW_OLLAMA_API_URL": "http://a:1", "OLLAMA_HOST": "http://b:2", "VOOL_OLLAMA_URL": "http://c:3"}) == "http://a:1"
    assert ollama_base_url({"OLLAMA_HOST": "http://b:2", "VOOL_OLLAMA_URL": "http://c:3"}) == "http://b:2"
    from core.web.api import runtime

    assert runtime.ollama_base_url({"OLLAMA_HOST": "http://b:2"}) == "http://b:2", "the runtime API keeps delegating to the leaf"


def test_the_embedding_service_goes_where_the_environment_points(stub, trap, monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", stub)
    monkeypatch.delenv("VOOL_RAW_OLLAMA_API_URL", raising=False)
    monkeypatch.setattr("core.local_model_policy.local_models_enabled", lambda *a, **k: True)
    from core import embedding_service

    embedding_service._best_embed_model.cache_clear()
    vec = embedding_service.embed("one message with two intents")
    assert len(vec) == embedding_service.EMBED_DIM
    assert any(p.startswith("/api/tags") for p in _Stub.seen) and any(p.startswith("/api/embed") for p in _Stub.seen), _Stub.seen
    assert trap == [], f"the embedding service reached for the real Ollama port: {trap}"
    embedding_service._best_embed_model.cache_clear()


def test_a_dead_port_makes_the_embedding_service_fall_back_without_a_network_reach(trap, monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:9")
    monkeypatch.delenv("VOOL_RAW_OLLAMA_API_URL", raising=False)
    monkeypatch.setattr("core.local_model_policy.local_models_enabled", lambda *a, **k: True)
    from core import embedding_service

    embedding_service._best_embed_model.cache_clear()
    vec = embedding_service.embed("still answers, from the hash fallback")
    assert len(vec) == embedding_service.EMBED_DIM and embedding_service.embedding_backend()
    assert trap == []
    embedding_service._best_embed_model.cache_clear()


def test_the_conversation_summarizer_goes_where_the_environment_points(stub, trap, monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", stub)
    monkeypatch.delenv("VOOL_RAW_OLLAMA_API_URL", raising=False)
    monkeypatch.setattr("core.local_model_policy.local_models_enabled", lambda *a, **k: True)
    from core import conversation_summarizer as cs

    picked = cs._pick_model_uncached()
    assert any(p.startswith("/api/tags") for p in _Stub.seen), _Stub.seen
    assert trap == [], f"the summarizer reached for the real Ollama port: {trap}"
    if picked:
        out = cs._call_ollama(picked, [{"role": "user", "content": "hi"}])
        assert out == "summary" and any(p.startswith("/api/chat") for p in _Stub.seen)
        assert trap == []


def test_the_default_local_provider_manifest_takes_its_base_url_from_the_environment(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:9")
    monkeypatch.delenv("VOOL_RAW_OLLAMA_API_URL", raising=False)
    from core import runtime_provider_defaults as rpd

    source = Path(rpd.__file__).read_text(encoding="utf-8")
    assert "http://127.0.0.1:11434" not in source, "the default-provider manifest pins the literal port again"
    assert "ollama_base_url(" in source
    assert rpd.default_local_provider_base_url() == "http://127.0.0.1:9"


def test_the_inventory_resolves_its_endpoint_from_the_mapping_it_is_given(stub, trap, monkeypatch):
    """Found on build 56c14d3c: an isolated app with every Ollama variable at a dead port still listed the operator's
    installed models, because the inventory honoured only VOOL_RAW_OLLAMA_API_URL and otherwise fell to its literal."""
    inv = _inventory
    monkeypatch.setattr(inv, "_ollama_models_payload", _REAL_INVENTORY_PAYLOAD)
    for name in ("VOOL_RAW_OLLAMA_API_URL", "OLLAMA_HOST", "VOOL_OLLAMA_URL"):
        monkeypatch.delenv(name, raising=False)
    names = inv.installed_ollama_model_names(env={"OLLAMA_HOST": stub})
    assert "qwen3:0.6b" in names and any(p.startswith("/api/tags") for p in _Stub.seen), (names, _Stub.seen)
    _Stub.seen.clear()
    loaded = inv.loaded_ollama_model_names(env={"VOOL_OLLAMA_URL": stub})
    assert any(p.startswith("/api/ps") for p in _Stub.seen), _Stub.seen
    assert isinstance(loaded, tuple)
    # a mapping that names nothing falls to the process environment's resolution, never past it to a literal
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:9")
    assert inv.installed_ollama_model_names(env={}) == ()
    assert trap == [], f"the inventory reached for the real Ollama port: {trap}"


def test_the_gpu_probe_default_endpoint_follows_the_environment(monkeypatch):
    from core import gpu_inference_probe as gp

    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:9")
    monkeypatch.delenv("VOOL_RAW_OLLAMA_API_URL", raising=False)
    import inspect

    sig = inspect.signature(gp.verify_gpu_inference)
    assert sig.parameters["base_url"].default is None, "the probe's default must be resolved at call time, not a literal"
    assert "127.0.0.1:11434" not in Path(gp.__file__).read_text(encoding="utf-8")


def test_static_census_every_other_mention_of_the_port_is_env_aware_or_allowlisted():
    holes = []
    for path in sorted(p for tree in SCANNED_TREES for p in (ROOT / tree).rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if line.strip().startswith("#"):
                continue  # a comment is not a client
            if LITERAL.search(line) and not ENV_AWARE.search(line) and rel not in ALLOWED:
                holes.append(f"{rel}:{n}: {line.strip()[:120]}")
    assert holes == [], "\n".join(holes)
    for rel, reason in ALLOWED.items():
        assert (ROOT / rel).exists() and reason.strip(), rel
