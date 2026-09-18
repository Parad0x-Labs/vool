"""SERVED proof — P0 forgotten memory must never resurrect, on the real /api/chat wire.

A real daemon (``apps.vool_api_server``) in its own ``VOOL_HOME``, a scripted
body-aware Ollama-dialect provider behind a real socket that RECORDS the exact
provider-wire messages (full turn prompt and the ollama-dialect top-level system
field) it was sent, the model CERTIFIED through the daemon's own sealed operator
probe — then the production journey end to end:

    /api/chat "Remember that my sister's bakery is called Flourstone Bakehouse."
    /api/chat "Forget Flourstone Bakehouse."            -> "Forget applied. Removed 1 memory entry."
    COLD RESTART (daemon process killed, new process, same home)
    /api/chat neutral model turns                        -> every provider-wire message
                                                           must contain ZERO canary bytes.

Boundary honesty (measured while building this proof): on this served lane the
bootstrap ``runtime_memory`` item is scope-quarantined by the loader's
``annotate_and_filter`` (the excerpt item carries no chat metadata, so
``infer_scope`` returns an empty scope), so on the UNFIXED base the provider
wire happened to stay clean and the m12 defect surface lived in the memory
FUNCTIONS — ``combined_memory_entries``/``legacy_memory_entries``/
``load_memory_excerpt``/``build_bootstrap_context`` — which step 6 probes on
the real served home after the cold restart (RED on base: the canary returns;
GREEN on the fix). The provider-wire sweep in step 4 is the journey-level
defense-in-depth: whatever lane a future refactor moves memory into, the
forgotten fact must never appear in the exact bytes sent to a provider.

Marked ``served``: boots a daemon (~5-20 s). A skip is not a pass.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import pytest

from tests._blackbox_served_rig import REPO_ROOT, ServedDaemon, run_in_home
from tests._served_sufficiency_provider import (
    MODEL_A,
    PROVIDER_NAME,
    BodyAwareScriptedProvider,
)

pytestmark = [pytest.mark.served]

MODEL = MODEL_A
CANARY = "Flourstone Bakehouse"
FACT_TEXT = "my sister's bakery is called Flourstone Bakehouse."
SESSION = "p0-served-chat"

# Same manifest as the sufficiency rig's SEED, with one addition: a declared
# 32k context window (realistic for the qwen3-class local model the stub stands
# in for). Without it the loader sizes the bootstrap budget from a zero window
# (900 tokens total) and TRIMS the runtime-memory item before the wire — the
# memory bootstrap lane must actually ride for this proof to be non-vacuous.
SEED = '''
import sys
sys.path.insert(0, "{root}")
from storage.db import get_connection
from storage.migrations import run_migrations
from storage.model_provider_manifest import ModelProviderManifest, list_provider_manifests, upsert_provider_manifest

run_migrations()

def manifest(name):
    return ModelProviderManifest(
        provider_name="ollama-local", model_name=name, source_type="http",
        adapter_type="openai_compatible", license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen3",
        weight_location="external", runtime_dependency="ollama",
        capabilities=["summarize", "classify", "format", "extract", "structured_json"],
        runtime_config={{"base_url": "{base_url}", "timeout_seconds": 30}},
        metadata={{
            "runtime_family": "ollama", "cost_class": "free_local",
            "model_digest": "sha256:stub-" + name, "chat_template_hash": "tmpl-" + name,
            "quantization": "q4_K_M", "parameter_billions": 2.0,
            "context_window": 32768,
        }},
    )

conn = get_connection()
conn.execute("DELETE FROM model_provider_manifests")
conn.commit()
conn.close()
for name in {models!r}:
    upsert_provider_manifest(manifest(name))
print(sorted(m.provider_id for m in list_provider_manifests()))
'''


def _reply_text(payload: dict[str, Any]) -> str:
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    return str(message.get("content") or payload.get("response") or payload.get("content") or "")


def _wire_texts(provider: BodyAwareScriptedProvider) -> list[str]:
    return [
        text
        for call in provider.calls
        for text in (str(call.get("system") or ""), str(call.get("full_prompt") or call.get("prompt") or ""))
    ]


def _certify(daemon: ServedDaemon, model: str) -> dict[str, Any]:
    request = Request(
        f"{daemon.base_url}/api/model-tool-certification/run",
        data=json.dumps({"provider_name": PROVIDER_NAME, "model_name": model, "timeout_seconds": 60}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=180) as response:
        return json.loads(response.read().decode("utf-8"))


@pytest.fixture
def served(tmp_path: Path):
    home = tmp_path / "home"
    provider = BodyAwareScriptedProvider(
        {MODEL: "A short rhyme: the harbor lanterns burn, and quiet tides return."}
    )
    daemon = ServedDaemon(
        home,
        env_extra={
            "VOOL_ALWAYS_ON_CATALOG": "1",
            "VOOL_WORKSPACE_ROOT": str(tmp_path / "ws"),
            "VOOL_MODEL_LOAD_FLOOR_GB": "0",
            # This machine is cloud-only for model execution; point EVERY default
            # local endpoint at the stub so no lane reaches a real local model.
            "OLLAMA_HOST": provider.base_url,
            "VOOL_OLLAMA_URL": provider.base_url,
            "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat",
        },
    )
    provider.__enter__()
    served_state: dict[str, Any] = {}
    try:
        try:
            daemon.start(timeout=240)
        except Exception as exc:  # pragma: no cover - environment, not the fix
            pytest.skip(f"served daemon could not boot here: {exc}")
        run_in_home(home, SEED.format(root=REPO_ROOT, base_url=provider.base_url, models=[MODEL]))
        result = _certify(daemon, MODEL)
        assert result.get("state") == "verified", f"model certification failed: {result}"
        provider.reset()
        served_state = {"home": home, "daemon": daemon, "provider": provider}
        yield served_state
    finally:
        # Stop whichever daemon is CURRENT: the cold-restart helper replaces
        # served["daemon"] with a fresh process, and stopping only the
        # original object here leaked the replacement daemon (and its port)
        # after every restart test.
        served_state.get("daemon", daemon).stop()
        daemon.stop()
        provider.__exit__(None, None, None)


def _restart(home: Path, old: ServedDaemon) -> ServedDaemon:
    old.stop()
    fresh = ServedDaemon(home)
    fresh.start(timeout=240)
    return fresh


def test_served_forget_then_cold_restart_provider_wire_has_zero_canary_bytes(served: dict[str, Any]) -> None:
    daemon: ServedDaemon = served["daemon"]
    provider: BodyAwareScriptedProvider = served["provider"]
    home: Path = served["home"]

    # 1. Remember — through the real door, the real memory fast path. One fact
    #    to forget, one that must SURVIVE (the wire-visibility control).
    payload = daemon.chat(
        f"Remember that {FACT_TEXT}", session_id=SESSION, mode="auto"
    )
    reply = _reply_text(payload)
    assert "Locked in" in reply, reply
    payload = daemon.chat(
        "Remember that my harbor lantern is painted signal green.", session_id=SESSION, mode="auto"
    )
    assert "Locked in" in _reply_text(payload)

    # 2. Forget — the production forget authority, acknowledged by the product.
    payload = daemon.chat(f"Forget {CANARY}.", session_id=SESSION, mode="auto")
    reply = _reply_text(payload)
    assert reply.startswith("Forget applied"), reply
    assert "Removed 1 memory entr" in reply, reply

    # 3. Cold restart: kill the daemon process, boot a NEW one on the same home.
    fresh = _restart(home, daemon)
    served["daemon"] = fresh
    provider.reset()

    # 4. Post-restart turns in a FRESH session. The fresh chat's transcript
    #    has no canary (the user only ever typed the token in the FIRST chat),
    #    so any canary byte on this wire can only arrive through a MEMORY
    #    path — the bootstrap runtime_memory excerpt, recall lanes, or
    #    summaries — which is exactly the resurrection this fix forbids.
    #    (The first chat's own transcript legitimately quotes the user's
    #    words; transcripts are not memory and are out of this law's scope.)
    for message in (
        "Tell me a short rhyme about harbor lanterns.",
        "hello again — what kinds of things can you help me with?",
    ):
        payload = fresh.chat(message, session_id=f"{SESSION}-after", mode="auto")
        assert _reply_text(payload).strip(), "turn produced no reply"

    wire = _wire_texts(provider)
    assert wire and any(text.strip() for text in wire), "no provider-wire messages captured — proof would be vacuous"
    leaks = [text[:200] for text in wire if CANARY in text]
    assert not leaks, f"canary bytes reached the provider wire after cold restart: {leaks[:2]}"

    # 5. "What do you remember?" cannot recover the fact through the door either.
    payload = fresh.chat("what do you remember", session_id=SESSION, mode="auto")
    assert CANARY not in _reply_text(payload)

    # 6. The m12 defect surface, probed on the SERVED home in a fresh
    #    interpreter: every model-visible memory path (combined view, legacy
    #    ingest, excerpt, and the full bootstrap assembly with the daemon's
    #    real namespace) must yield zero canary — while the SURVIVING fact
    #    stays visible, proving the probe is not blind.
    state = json.loads(
        run_in_home(
            home,
            "import sys, json\n"
            f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
            "from core.bootstrap_context import build_bootstrap_context\n"
            "from core.context_namespace import list_context_imports\n"
            "from core.context_scope import ContextAccessPolicy\n"
            "from core.human_input_adapter import adapt_user_input\n"
            "from core.identity_manager import load_active_persona\n"
            "from core.memory.entries import combined_memory_entries, load_memory_excerpt\n"
            "from core.memory.files import memory_path\n"
            "from core.task_router import create_task_record\n"
            "from storage.db import get_connection\n"
            "conn = get_connection()\n"
            f"chat = [r[0] for r in conn.execute('SELECT chat_id FROM context_namespaces ORDER BY created_at').fetchall()][0]\n"
            "policy = ContextAccessPolicy(chat_id=chat, project_id='', namespace_state='active', grants=list_context_imports(chat))\n"
            "items = build_bootstrap_context(\n"
            "    persona=load_active_persona('default'),\n"
            "    task=create_task_record('hello again'),\n"
            "    classification={'task_class': 'chat_conversation', 'risk_flags': [], 'confidence_hint': 0.84},\n"
            "    interpretation=adapt_user_input('hello again', session_id=chat),\n"
            "    session_id=chat,\n"
            "    include_private_context=True,\n"
            "    include_user_profile_context=True,\n"
            ")\n"
            "print(json.dumps({\n"
            "  'combined': [str(r.get('text') or '') for r in combined_memory_entries()],\n"
            "  'excerpt': load_memory_excerpt(max_chars=200000),\n"
            "  'mirror': memory_path().read_text(encoding='utf-8', errors='replace'),\n"
            "  'bootstrap': [str(i.content) for i in items],\n"
            "}))",
        ).strip().splitlines()[-1]
    )
    assert not any(CANARY in t for t in state["combined"])
    assert CANARY not in state["excerpt"]
    assert CANARY not in state["mirror"]
    assert not any(CANARY in t for t in state["bootstrap"])
    assert any("signal green" in t for t in state["combined"]), "probe is blind: surviving fact not visible"
    assert "signal green" in state["excerpt"]


def test_served_positive_control_wire_capture_detects_presence(served: dict[str, Any]) -> None:
    """Harness control: the wire capture can detect PRESENCE, not only absence.

    In the SAME session the user's own remembered words ride the hydrated
    transcript to the provider — the one lane that legitimately carries them.
    If this control fails, the zero-canary assertions above would be vacuous.
    """
    daemon: ServedDaemon = served["daemon"]
    provider: BodyAwareScriptedProvider = served["provider"]

    payload = daemon.chat(
        "Remember that my harbor lantern is painted signal green.", session_id="p0-control", mode="auto"
    )
    assert "Locked in" in _reply_text(payload)
    provider.reset()
    payload = daemon.chat(
        "Tell me a short rhyme about harbor lanterns.", session_id="p0-control", mode="auto"
    )
    assert _reply_text(payload).strip()

    wire = _wire_texts(provider)
    assert any(text.strip() for text in wire), "no provider-wire messages captured"
    assert any("signal green" in text for text in wire), "wire capture is blind — zero-canary assertions would be vacuous"
