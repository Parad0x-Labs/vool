"""A machine where no local model runs (no Ollama, or local models switched off).

Before: Auto routed every turn to a registered-but-dead local model and refused, a pasted key
ticked the setup step anyway, and the refusal told the owner to certify a model that was not
running. Now setup asks once which cloud model Auto may use while nothing local runs, the
answer is applied only to the owner's own Auto turns, and the refusals name the fix.

The falsifiers pin what must NOT change: a caller who is not the local owner never gets the
saved default, a running local model always wins, and a turn that names a model keeps it.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from core import cloud_only_default, local_model_presence
from core.cloud_only_default import CloudOnlyDefaultError, resolve_auto_turn_model
from tests.first_run_pact_rig import pact_rig

CLOUD_ID = "openai-compatible-remote:cloud-test-model"
AUTO = {"", "vool", "vool:latest"}


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "cloud-only-home"
    (home / "data").mkdir(parents=True)
    monkeypatch.setenv("VOOL_HOME", str(home))
    # Nothing answers here: an Ollama endpoint that refuses connections.
    monkeypatch.setenv("VOOL_RAW_OLLAMA_API_URL", "http://127.0.0.1:9")
    monkeypatch.delenv("VOOL_LOCAL_MODELS_ENABLED", raising=False)
    from core.runtime_paths import configure_runtime_home

    configure_runtime_home(home)
    import storage.db as sdb

    sdb.configure_default_db_path(home / "data" / "vool_web0_v2.db")
    local_model_presence.reset_cache()
    yield home
    local_model_presence.reset_cache()
    configure_runtime_home(None)
    sdb.configure_default_db_path(None)


def _register(provider: str, model: str, base_url: str) -> str:
    from storage.model_provider_manifest import ModelProviderManifest, upsert_provider_manifest

    upsert_provider_manifest(
        ModelProviderManifest(
            provider_name=provider,
            model_name=model,
            source_type="http",
            capabilities=["summarize"],
            runtime_config={"base_url": base_url},
        )
    )
    return f"{provider}:{model}"


def _register_cloud() -> str:
    return _register("openai-compatible-remote", "cloud-test-model", "https://api.example.com/v1")


class _FakeOllama:
    """A loopback endpoint answering /api/tags with the given model rows."""

    def __init__(self, models: list[dict]):
        rows = models

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                body = json.dumps({"models": rows}).encode() if self.path == "/api/tags" else b"{}"
                self.send_response(200 if self.path == "/api/tags" else 404)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


# --- presence ----------------------------------------------------------------------------------


def test_a_refused_ollama_port_reads_as_no_local_model(home) -> None:
    presence = local_model_presence.local_model_presence(refresh=True)
    assert presence.running is False and presence.reason == "no_local_endpoint_answered"


def test_local_models_switched_off_reads_as_disabled(home, monkeypatch) -> None:
    monkeypatch.setenv("VOOL_LOCAL_MODELS_ENABLED", "0")
    presence = local_model_presence.local_model_presence(refresh=True)
    assert presence.running is False and presence.reason == "local_models_disabled"


def test_an_ollama_with_only_an_embedding_model_cannot_answer_a_turn(home, monkeypatch) -> None:
    with _FakeOllama([{"name": "nomic-embed-text:latest", "details": {"families": ["nomic-bert"]}}]) as fake:
        monkeypatch.setenv("VOOL_RAW_OLLAMA_API_URL", fake.url)
        assert local_model_presence.local_model_running(refresh=True) is False


def test_an_ollama_serving_a_chat_model_is_running(home, monkeypatch) -> None:
    with _FakeOllama([{"name": "qwen3:4b", "details": {"families": ["qwen3"]}}]) as fake:
        monkeypatch.setenv("VOOL_RAW_OLLAMA_API_URL", fake.url)
        presence = local_model_presence.local_model_presence(refresh=True)
    assert presence.running is True and presence.endpoint == fake.url


# --- the saved answer --------------------------------------------------------------------------


def test_only_a_registered_cloud_model_can_be_saved(home) -> None:
    with pytest.raises(CloudOnlyDefaultError) as missing:
        cloud_only_default.choose("")
    assert missing.value.code == "model_required"
    with pytest.raises(CloudOnlyDefaultError) as unknown:
        cloud_only_default.choose(CLOUD_ID)
    assert unknown.value.code == "model_not_registered"
    local_id = _register("ollama-local", "qwen3:4b", "http://127.0.0.1:11434")
    with pytest.raises(CloudOnlyDefaultError):
        cloud_only_default.choose(local_id)  # a local lane is never a "cloud default"
    assert [row["id"] for row in cloud_only_default.cloud_model_candidates()] == []
    cloud = _register_cloud()
    assert cloud_only_default.choose(cloud)["model"] == cloud
    assert cloud_only_default.load()["decision"] == "use_cloud"


def test_the_question_is_asked_once(home) -> None:
    _register_cloud()
    assert cloud_only_default.snapshot(refresh=True)["needs_choice"] is True
    cloud_only_default.decline()
    snap = cloud_only_default.snapshot()
    assert snap["needs_choice"] is False and snap["decision"] == "not_now" and snap["model"] == ""
    cloud_only_default.clear()
    assert cloud_only_default.snapshot()["needs_choice"] is True


def test_an_owner_auto_turn_takes_the_saved_cloud_model(home) -> None:
    cloud = _register_cloud()
    cloud_only_default.choose(cloud)
    for alias in AUTO:
        assert resolve_auto_turn_model(alias, auto_aliases=AUTO, owner_local=True) == (cloud, True)


def test_falsifier_a_caller_who_is_not_the_local_owner_never_gets_the_default(home) -> None:
    cloud_only_default.choose(_register_cloud())
    assert resolve_auto_turn_model("", auto_aliases=AUTO, owner_local=False) == ("", False)
    assert resolve_auto_turn_model("vool", auto_aliases=AUTO, owner_local=False) == ("vool", False)


def test_falsifier_a_running_local_model_always_wins(home, monkeypatch) -> None:
    cloud_only_default.choose(_register_cloud())
    with _FakeOllama([{"name": "qwen3:4b", "details": {"families": ["qwen3"]}}]) as fake:
        monkeypatch.setenv("VOOL_RAW_OLLAMA_API_URL", fake.url)
        local_model_presence.reset_cache()
        assert resolve_auto_turn_model("vool", auto_aliases=AUTO, owner_local=True) == ("vool", False)
        assert cloud_only_default.no_route_hint() == ""


def test_falsifier_a_turn_that_names_a_model_keeps_it(home) -> None:
    cloud_only_default.choose(_register_cloud())
    for named in ("ollama-local:qwen3:4b", "openrouter:some/model", "vool-local-only"):
        assert resolve_auto_turn_model(named, auto_aliases=AUTO, owner_local=True) == (named, False)


def test_a_model_removed_after_it_was_saved_is_not_used(home) -> None:
    from storage.model_provider_manifest import set_provider_manifest_enabled

    cloud = _register_cloud()
    cloud_only_default.choose(cloud)
    set_provider_manifest_enabled("openai-compatible-remote", "cloud-test-model", enabled=False)
    assert resolve_auto_turn_model("", auto_aliases=AUTO, owner_local=True) == ("", False)


def test_chat_ingress_passes_the_owner_check_into_the_resolver() -> None:
    """The served /api/chat door is the one caller; it must hand over its own owner check."""
    source = (Path(__file__).resolve().parents[1] / "core" / "web" / "api" / "service.py").read_text(encoding="utf-8")
    call = source.split("requested_model, cloud_only_default_applied = resolve_auto_turn_model(", 1)[1][:400]
    assert "owner_local=owner_local" in call
    assert 'source_context["model_selection"] = "pin"' in source


# --- refusals that name the fix ----------------------------------------------------------------


def test_the_no_route_notice_names_the_fix_and_keeps_the_failure_lead(home) -> None:
    from core.agent_runtime.memory_runtime import chat_surface_honest_degraded_response

    class _Execution:
        source = "no_provider_available"
        details = {}

    no_cloud = chat_surface_honest_degraded_response(None, _Execution())
    assert no_cloud.startswith("I couldn't get a live model response in this run")
    assert "Settings → API Keys" in no_cloud
    _register_cloud()
    with_cloud = chat_surface_honest_degraded_response(None, _Execution())
    assert with_cloud.startswith("I couldn't get a live model response in this run")
    assert "model selector" in with_cloud and "Where should it think?" in with_cloud


def test_the_authorship_refusal_for_a_dead_local_model_says_to_pick_a_cloud_model(home) -> None:
    from core.final_answer_authorship import REASON_BLOCKED_BEFORE_GENERATION, AuthorshipDecision

    local_id = _register("ollama-local", "qwen3:4b", "http://127.0.0.1:11434")
    decision = AuthorshipDecision(
        eligible=False, author_role="final_answer", task_class="general",
        selected_model=local_id, reason=REASON_BLOCKED_BEFORE_GENERATION,
    )
    text = decision.refusal_text()
    assert "no local model is running on this computer" in text and "cloud model" in text
    assert "run the local model tool certification" not in text


def test_the_authorship_refusal_is_unchanged_while_the_local_model_runs(home, monkeypatch) -> None:
    from core.final_answer_authorship import REASON_BLOCKED_BEFORE_GENERATION, AuthorshipDecision

    local_id = _register("ollama-local", "qwen3:4b", "http://127.0.0.1:11434")
    monkeypatch.setattr("core.local_model_presence.local_model_running", lambda **_: True)
    decision = AuthorshipDecision(
        eligible=False, author_role="final_answer", task_class="general",
        selected_model=local_id, reason=REASON_BLOCKED_BEFORE_GENERATION,
    )
    assert "run the local model tool certification" in decision.refusal_text()


# --- setup -------------------------------------------------------------------------------------


def test_a_pasted_key_alone_no_longer_ticks_thinking_when_nothing_local_runs(home) -> None:
    from core import setup_progress

    _register_cloud()
    assert setup_progress.thinking_done() is False
    snap = setup_progress.snapshot()["cloud_default"]
    assert snap["local_model_running"] is False and snap["needs_choice"] is True
    assert [row["id"] for row in snap["candidates"]] == [CLOUD_ID]
    cloud_only_default.choose(CLOUD_ID)
    assert setup_progress.thinking_done() is True


def test_the_served_door_saves_for_the_owner_and_refuses_everyone_else(pact_rig, monkeypatch) -> None:
    monkeypatch.setenv("VOOL_RAW_OLLAMA_API_URL", "http://127.0.0.1:9")
    local_model_presence.reset_cache()
    _register_cloud()
    status, payload = pact_rig.post("/api/onboarding/cloud-default", {"decision": "use_cloud", "model": CLOUD_ID},
                                    client_host="10.9.9.9")
    assert status == 403 and payload.get("error") == "owner_local_required"
    assert cloud_only_default.load()["decision"] == ""
    status, payload = pact_rig.post("/api/onboarding/cloud-default", {"decision": "use_cloud", "model": "nope:nope"})
    assert status == 409 and payload["error"] == "model_not_registered"
    status, payload = pact_rig.post("/api/onboarding/cloud-default", {"decision": "use_cloud", "model": CLOUD_ID})
    assert status == 200, payload
    assert cloud_only_default.load()["model"] == CLOUD_ID
    status, _ = pact_rig.post("/api/onboarding/cloud-default/clear", {})
    assert status == 200 and cloud_only_default.load()["decision"] == ""
    local_model_presence.reset_cache()
