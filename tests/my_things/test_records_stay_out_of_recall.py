"""Saved household records never answer a chat question on their own.

After a chat Forget the forgotten words must not come back from My things through memory recall,
the memory inventory or a notes question: My things is not a memory store and no recall path reads
it. Only the owner's own My things page and its doors, the forget sentence (a count, never the
words) and the owner-local record tools reach the store, and those tools stay hidden from the model
until the forget law is reviewed for them.
"""
from __future__ import annotations

import ast
from pathlib import Path

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


def test_after_forget_no_recall_question_serves_the_words_from_my_things(home: Path) -> None:
    ThingsStore().save({"name": "Pine lamp", "model": "PL-7731", "notes": "PL-7731 bulb is warm white"})
    _ask("Remember that the Pine lamp model is PL-7731")
    handled, reply = _ask("Forget PL-7731")
    assert handled and reply.startswith("Forget applied"), reply

    for question in ("What do you remember", "What do you remember about PL-7731?",
                     "Do you have notes about PL-7731?", "What is the saved model of my Pine lamp?",
                     "Which lamp model did I save?"):
        _handled, answer = _ask(question)
        assert "7731" not in answer, (question, answer)
    # The record itself is still the person's, on their own page.
    assert [t["model"] for t in ThingsStore().list()] == ["PL-7731"]


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
