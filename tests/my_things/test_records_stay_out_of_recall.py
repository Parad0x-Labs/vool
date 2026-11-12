"""Saved household records never answer a chat question on their own.

After a chat Forget the forgotten words must not come back from My things through memory recall,
the memory inventory or a notes question: My things is not a memory store and no recall path reads
it. Only the owner's own My things page and its doors, the forget sentence (a count, never the
words) and the owner-local record tools reach the store, and those tools stay hidden from the model
until the forget law is reviewed for them.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy
from core.memory.files import memory_path
from core.my_things.store import ThingsStore
from core.persistent_memory import maybe_handle_memory_command
from core.runtime_paths import configure_runtime_home

REPO = Path(__file__).resolve().parents[2]
CHAT = "things-recall-chat"

# Every module outside My things that may import it, and what it may import.
ALLOWED_IMPORTERS = {
    "core/persistent_memory.py": {"core.my_things.erasure"},
    "core/runtime_tool_contracts.py": {"core.my_things.tools"},
    "core/runtime_execution_tools.py": {"core.my_things.tools"},
    "core/web/api/service.py": {"core.my_things.api", "core.my_things_fragment"},
    "core/vool_chat_page.py": {"core.my_things_fragment"},
    "core/vool_settings_page.py": {"core.my_things_fragment"},
}


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    runtime = tmp_path / "runtime-home"
    monkeypatch.setenv("VOOL_HOME", str(runtime))
    monkeypatch.setenv("VOOL_CREDENTIAL_STORE", "vault")
    configure_runtime_home(runtime)
    memory_path().parent.mkdir(parents=True, exist_ok=True)
    yield runtime
    configure_runtime_home(None)


def _ask(text: str) -> tuple[bool, str]:
    ensure_chat_namespace(CHAT, grant_confirmed_profile=True)
    return maybe_handle_memory_command(text, session_id=CHAT, access_policy=resolve_memory_access_policy(chat_id=CHAT))


QUESTIONS = ("What do you remember about PL-7731?", "Do you have notes about PL-7731?",
             "What is the saved model of my Pine lamp?", "Which lamp model did I save?")
PLAIN = QUESTIONS[2:]
# Only My things holds this: it must never reach the model, whatever the question.
RECORD_ONLY = "QX93"


def test_after_forget_the_memory_lane_never_answers_from_my_things(home: Path) -> None:
    ThingsStore().save({"name": "Pine lamp", "model": "PL-7731", "notes": f"PL-7731 bulb {RECORD_ONLY} is warm white"})
    _ask("Remember that the Pine lamp model is PL-7731")
    # The probe can see the words: before the forget, memory answers every question with them.
    for question in ("What do you remember", *QUESTIONS):
        handled, answer = _ask(question)
        assert handled and "7731" in answer, (question, answer)

    handled, reply = _ask("Forget PL-7731")
    assert handled and reply.startswith("Forget applied"), reply

    handled, answer = _ask("What do you remember")
    assert handled and "7731" not in answer and RECORD_ONLY not in answer, answer
    for question in QUESTIONS:
        handled, answer = _ask(question)
        # Memory holds no record now and hands the turn on; what the model then sees is proven served below.
        assert "7731" not in answer and RECORD_ONLY not in answer, (question, handled, answer)
    # The record itself is still the person's, on their own page.
    assert [t["model"] for t in ThingsStore().list()] == ["PL-7731"]


def _judge(body: dict) -> str | None:
    """Answer the entity-ambiguity probe as a model would; every other call gets the scripted reply."""
    if "well-known referents" in json.dumps(body.get("messages") or []):
        return '{"ambiguous": false, "referents": [], "clarification": ""}'
    return None


def _reply(payload: dict) -> str:
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    return str(message.get("content") or payload.get("response") or "")


def _owner(daemon, path: str, body: dict | None = None) -> dict:
    """The My things page's own request: same origin, JSON."""
    request = Request(daemon.base_url + path, data=None if body is None else json.dumps(body).encode("utf-8"),
                      headers={"Content-Type": "application/json", "Origin": daemon.base_url},
                      method="GET" if body is None else "POST")
    with urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def test_served_turns_after_forget_never_carry_the_record_to_the_model(tmp_path: Path) -> None:
    from tests._reader_served_rig import CapturingProvider, ServedDaemon, canonical_session

    with CapturingProvider(default="A lamp glows by the window.", reply_fn=_judge) as provider, \
            ServedDaemon(tmp_path / "home", provider=provider) as daemon:
        assert daemon.certify().get("state") == "verified"
        saved = _owner(daemon, "/api/my-things/save", {"confirm": True, "fields": {
            "name": "Pine lamp", "model": "PL-7731", "notes": f"PL-7731 bulb {RECORD_ONLY} is warm white"}})
        assert saved.get("ok") is True, saved
        chat = canonical_session("things-recall-served")
        assert "Locked in" in _reply(daemon.chat("Remember that the Pine lamp model is PL-7731", session_id=chat))
        provider.reset()
        daemon.chat("Write one short line about a lamp by the window.", session_id=chat)
        assert provider.any_payload_has("7731"), "probe is blind: the remembered words never reached the model"
        assert not provider.any_payload_has(RECORD_ONLY), "My things reached the model before any forget"

        reply = _reply(daemon.chat("Forget PL-7731", session_id=chat))
        assert reply.startswith("Forget applied") and "1 saved thing in My things" in reply, reply

        seen = []
        for session in (chat, canonical_session("things-recall-served-another-chat")):
            # The forget law is per chat ("In a new chat I can remember it again"), so in another chat the
            # person may type the code again, and their own turns then carry it to the model. The plain
            # questions come first there: what they prove is that nothing brings the words back by itself.
            for question in (QUESTIONS if session == chat else PLAIN + QUESTIONS[:2]):
                provider.reset()
                answer = _reply(daemon.chat(question, session_id=session))
                seen.append((session == chat, question, bool(provider.payloads()), answer))
                # Whether the forget law refuses the turn itself or the model answers it, the words
                # come back from nowhere: not in the answer, and never in what the model is sent.
                assert "7731" not in answer and RECORD_ONLY not in answer, seen[-1]
                assert not provider.any_payload_has(RECORD_ONLY), seen[-1]
                if question in PLAIN:
                    assert not provider.any_payload_has("7731"), seen[-1]
        # Measured on 28fc78dd: in the forgetting chat both plain questions reach the model; in another chat
        # the memory lane answers some of them ("I don't have that saved") and hands the rest on.
        assert any(called for _same, question, called, _answer in seen if question in PLAIN), \
            ("no plain question reached the model, so nothing was proven about what it sees", seen)
        assert [t["model"] for t in _owner(daemon, "/api/my-things")["things"]] == ["PL-7731"]


def test_only_the_owner_doors_and_the_forget_count_reach_the_records_store() -> None:
    found: dict[str, set[str]] = {}
    for top in ("core", "adapters", "apps", "tools", "storage", "plugins", "skills"):
        for path in sorted((REPO / top).rglob("*.py")):
            rel = path.relative_to(REPO).as_posix()
            if rel.startswith("core/my_things/") or rel == "core/my_things_fragment.py" or "__pycache__" in rel:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "my_things" not in text:
                continue
            modules = set()
            for node in ast.walk(ast.parse(text)):
                if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(("core.my_things",)):
                    modules.add(node.module)
                elif isinstance(node, ast.Import):
                    modules.update(a.name for a in node.names if a.name.startswith("core.my_things"))
            if modules:
                found[rel] = modules
    assert found == ALLOWED_IMPORTERS


def test_record_tools_stay_hidden_from_the_model() -> None:
    # Visible tools would let a model look a forgotten model number up again from a question that
    # never asked for My things. Showing them needs the forget law reviewed for them first.
    from core.my_things.tools import contracts

    assert {c.intent for c in contracts()} == {"things.search", "things.match", "things.prepare"}
    assert not any(c.model_visible for c in contracts())
