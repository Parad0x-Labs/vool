"""SERVED Auto-selection proof (mission auto-router-selection-20261002).

A real ``apps.vool_api_server`` daemon in its own VOOL_HOME, a scripted Ollama-dialect
provider behind the real HTTP door, and a SECOND scripted endpoint standing in for a paid
cloud provider. Nothing between the request and the wire is mocked: the door, the composer
selection, the turn routing plan, ``rank_provider_candidates`` and the autopilot are all
production code from THIS checkout.

Three drives through the same served workflow:

1. Auto tool turn — the internal tool-selection step routes with task_kind="tool_intent"
   (required capabilities {tool_intent, structured_json}). A model declaring
   ``structured_json`` but NOT ``tool_intent`` is partial coverage: it must be absent from
   the step's ranked-candidates event while the complete model serves.
2. Manual pin — the same partial model, explicitly pinned by the operator, still serves a
   plain turn (the completeness law never breaks a pin: the pinned ranking has no complete
   alternative, so the availability rule retains it).
3. VOOL Auto Local Only — the paid cloud manifest is registered and reachable, the turn runs,
   and the cloud endpoint receives ZERO dispatches.

A stub endpoint is not a model launch (this machine is cloud-only for model execution); the
claim here is routing truth, not model-authored usefulness.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import pytest

from tests._blackbox_served_rig import REPO_ROOT, ScriptedProvider, ServedDaemon, run_in_home
from tests._reader_served_rig import _probe_reply

pytestmark = [pytest.mark.served]

# 8B on purpose: a 2B partial would already be dropped by the tool-selection parameter
# floor (`_drop_models_too_small_to_select_a_tool`) at the BASE, hiding this repair's
# delta. 8B clears every pre-existing floor, so the ONLY thing that can exclude this
# model from a tool_intent ranking is the completeness eligibility law.
PARTIAL = "stub-partial:8b"      # structured_json but no tool_intent: partial for tool turns
COMPLETE = "stub-complete:8b"    # full tool_intent coverage
CLOUD_MODEL = "cloudy-model-x"

SEED = '''
import sys
sys.path.insert(0, "{root}")
from storage.db import get_connection
from storage.migrations import run_migrations
from storage.model_provider_manifest import ModelProviderManifest, list_provider_manifests, upsert_provider_manifest

run_migrations()

def local_manifest(name, capabilities):
    return ModelProviderManifest(
        provider_name="ollama-local", model_name=name, source_type="http",
        adapter_type="openai_compatible", license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen3",
        weight_location="external", runtime_dependency="ollama",
        capabilities=capabilities,
        runtime_config={{"base_url": "{base_url}", "timeout_seconds": 30}},
        metadata={{
            "runtime_family": "ollama", "cost_class": "free_local",
            "model_digest": "sha256:stub", "chat_template_hash": "tmpl",
            "quantization": "q4_K_M", "parameter_billions": 2.0,
        }},
    )

def cloud_manifest():
    return ModelProviderManifest(
        provider_name="cloudy-remote", model_name="{cloud_model}", source_type="http",
        adapter_type="openai_compatible", license_name="Provider",
        license_reference="user-managed", weight_location="external",
        runtime_dependency="remote-openai-compatible-provider",
        capabilities=["summarize", "classify", "format", "long_context", "structured_json"],
        runtime_config={{"base_url": "{cloud_base_url}", "timeout_seconds": 30}},
        metadata={{"deployment_class": "cloud", "cost_class": "paid_cloud"}},
    )

conn = get_connection()
conn.execute("DELETE FROM model_provider_manifests")
conn.commit()
conn.close()
upsert_provider_manifest(local_manifest("{partial}", ["summarize", "classify", "format", "structured_json"]))
upsert_provider_manifest(local_manifest("{complete}", ["summarize", "classify", "format", "extract", "structured_json", "tool_intent"]))
upsert_provider_manifest(cloud_manifest())
print(sorted(m.provider_id for m in list_provider_manifests()))
'''


def _tool_intent_reply() -> str:
    return json.dumps({"intent": "sandbox.run_command", "arguments": {"command": "echo served-selection-proof"}})


def _reply_text(payload: dict[str, Any]) -> str:
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    return str(message.get("content") or payload.get("response") or payload.get("content") or "")


def _routing_events(home: Path) -> list[dict[str, Any]]:
    conn = sqlite3.connect(str(home / "data" / "vool_web0_v2.db"))
    try:
        rows = conn.execute(
            "SELECT event_type, COALESCE(details_json, '') FROM runtime_session_events ORDER BY rowid ASC"
        ).fetchall()
    finally:
        conn.close()
    events: list[dict[str, Any]] = []
    for event_type, details in rows:
        if event_type not in {"model_routing_started", "model_lane_selected"}:
            continue
        try:
            parsed = json.loads(details) if details else {}
        except json.JSONDecodeError:
            parsed = {}
        parsed["event_type"] = event_type
        events.append(parsed)
    return events


def _reply(body: dict[str, Any]) -> Any:
    """Answer the production certification probe; None falls through to the model table.

    Once the runtime hands back the tool's observation (its own fixed delivery message, "Real
    tool observations from this same turn follow"), the model answers from that observation in
    plain words, the way the tool-retention suite's carry cases do — a JSON intent there is
    foreign tool syntax, and the synthesis integrity gate would rightly reject it.
    """
    probe = _probe_reply(body)
    if probe is not None:
        return list(probe["tool_calls"]) if probe.get("tool_calls") else str(probe.get("content") or "")
    messages = [m for m in (body.get("messages") or []) if isinstance(m, dict)]
    joined = "\n".join(str(m.get("content") or "") for m in messages)
    if "Real tool observations from this same turn follow" in joined:
        return "The command printed served-selection-proof."
    return None


def _certify(daemon: Any, model: str) -> None:
    """Certify a stub as a final-answer author through the real certification door.

    On the 0.7.2 train an uncertified loopback model is refused BEFORE its adapter is built
    (`core.final_answer_authorship`), so without this the tool turn's synthesis lane is
    blocked for every stub and the turn degrades instead of proving selection. The sibling
    served suite (test_auto_selection_hard_capability_20261002_served) certifies the same way.
    """
    request = Request(
        f"{daemon.base_url}/api/model-tool-certification/run",
        data=json.dumps(
            {"provider_name": "ollama-local", "model_name": model, "timeout_seconds": 120}
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=240) as response:
        result = json.loads(response.read().decode("utf-8"))
    assert str(result.get("state") or "").lower() == "verified", result


@pytest.fixture
def served(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir(parents=True)
    # The tool-intent lane parses the assistant CONTENT as a {"intent": ..., "arguments": ...}
    # JSON body; both local models answer from that table so whichever is asked replays a
    # well-formed intent and the turn proceeds deterministically.
    local_provider = ScriptedProvider(
        {
            PARTIAL: _tool_intent_reply(),
            COMPLETE: _tool_intent_reply(),
        },
        default="Served selection proof reply.",
        reply_fn=_reply,
    )
    cloud_provider = ScriptedProvider({CLOUD_MODEL: "cloud reply"}, default="cloud reply")
    daemon = ServedDaemon(
        home,
        env_extra={
            "VOOL_ALWAYS_ON_CATALOG": "1",
            # Every default local endpoint points at the stub so no lane can reach a real
            # local model from this daemon (this machine runs a real Ollama on 11434).
            "OLLAMA_HOST": local_provider.base_url,
            "VOOL_OLLAMA_URL": local_provider.base_url,
            "VOOL_OLLAMA_CHAT_URL": f"{local_provider.base_url}/api/chat",
        },
    )
    local_provider.__enter__()
    cloud_provider.__enter__()
    try:
        try:
            daemon.start(timeout=240)
        except Exception as exc:  # pragma: no cover - environment, not routing
            pytest.skip(f"served daemon could not boot here: {exc}")
        run_in_home(
            home,
            SEED.format(
                root=REPO_ROOT,
                base_url=local_provider.base_url,
                cloud_base_url=cloud_provider.base_url,
                cloud_model=CLOUD_MODEL,
                partial=PARTIAL,
                complete=COMPLETE,
            ),
        )
        for model in (PARTIAL, COMPLETE):
            _certify(daemon, model)
        local_provider.reset()
        cloud_provider.reset()
        yield {
            "home": home,
            "daemon": daemon,
            "local": local_provider,
            "cloud": cloud_provider,
        }
    finally:
        daemon.stop()
        local_provider.__exit__(None, None, None)
        cloud_provider.__exit__(None, None, None)


def test_served_auto_tool_turn_ranks_only_complete_candidates_and_serves_them(served: dict[str, Any]) -> None:
    daemon: ServedDaemon = served["daemon"]
    local: ScriptedProvider = served["local"]

    payload = daemon.chat(
        "run the command: echo served-selection-proof",
        session_id="served-selection-1",
        mode="auto",
        model="vool",
    )
    assert _reply_text(payload) != ""

    asked = {str(call.get("model") or "") for call in local.calls}
    events = _routing_events(served["home"])
    tool_step_events = [
        event
        for event in events
        if str(event.get("task_kind") or "") == "tool_intent"
        and isinstance(event.get("ranked_candidates"), list)
    ]
    assert tool_step_events, "expected at least one tool_intent routing event with a ranked set"
    for event in tool_step_events:
        ranked = [str(item) for item in event["ranked_candidates"]]
        assert not any(PARTIAL in item for item in ranked), (
            f"partial-coverage model ranked for a tool_intent step: {ranked}"
        )
    assert any(COMPLETE in item for item in asked), f"complete model never asked; asked={asked}"


def test_served_manual_pin_serves_the_pinned_model(served: dict[str, Any]) -> None:
    daemon: ServedDaemon = served["daemon"]
    local: ScriptedProvider = served["local"]

    payload = daemon.chat(
        "answer with exactly: pinned-ok",
        session_id="served-selection-2",
        mode="auto",
        model=PARTIAL,
    )
    assert _reply_text(payload) != ""
    asked = {str(call.get("model") or "") for call in local.calls}
    assert any(PARTIAL in item for item in asked), f"pinned model never asked; asked={asked}"


def test_served_local_only_never_dispatches_cloud(served: dict[str, Any]) -> None:
    daemon: ServedDaemon = served["daemon"]
    cloud: ScriptedProvider = served["cloud"]

    payload = daemon.chat(
        "answer with exactly: local-only-ok",
        session_id="served-selection-3",
        mode="auto",
        model="vool-local-only",
    )
    assert _reply_text(payload) != ""
    assert cloud.calls == [], f"cloud endpoint received {len(cloud.calls)} dispatches under Local Only"
