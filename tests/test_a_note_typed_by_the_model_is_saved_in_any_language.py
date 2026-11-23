"""A note the model types out is saved with that content and title, in any language and any lead words.

Mac gate on f3771b6b7: "jot in my notes: buy dog food on the way home" reached operator.save_note as the request as
written only; the English reading of it found no content, and after the approval nothing was saved. "jot down in my
notes that the plumber comes tuesday" was saved under the title "in my notes that the plumber comes tuesday". The note
tool now takes the content and title as typed fields, read before the words.

In process, like tests/test_reminders_the_model_can_set.py. Contents, titles and lead words are drawn at random.
"""
from __future__ import annotations

import random

import pytest

from tests.pa_beta_gate._pc_calendar_rig import prepare_home
from tests.test_reminders_the_model_can_set import _model_call_allowed

NOTES = (
    ("jot in my notes: {c}", "buy dog food on the way home"),
    ("jot down in my notes that {c}", "the plumber comes tuesday"),
    ("Užsirašyk: {c}", "nupirkti šunų maisto grįžtant namo"),
    ("Schreib dir auf: {c}", "Montag den Reifenwechsel buchen"),
    ("apunta en mis notas: {c}", "llamar al veterinario el jueves"),
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    return prepare_home(tmp_path, monkeypatch)


@pytest.fixture(autouse=True)
def _fresh_runtime_state():
    from core import capability_graph as cg
    from core.mode_permission_policy import reset_mode_permission_state

    reset_mode_permission_state()
    cg.reset()
    yield
    reset_mode_permission_state()
    cg.reset()


def _saved(content: str) -> list[dict]:
    from core.operator.notes import search_notes

    return search_notes(query=content.split()[0])


@pytest.mark.parametrize("seed", range(5))
def test_a_typed_note_is_saved_with_its_content(seed, home) -> None:
    rng = random.Random(f"typed-note-{seed}")
    lead, content = rng.choice(NOTES)
    title = rng.choice(["", content.split()[-1].title()])
    arguments = {"content": content, "text": lead.format(c=content), **({"title": title} if title else {})}
    saved = _model_call_allowed("operator.save_note", arguments, session_id=f"chat-note-{seed}")
    assert saved.ok, saved.response_text
    found = _saved(content)
    assert found, saved.response_text
    path = str(found[0].get("path") or found[0].get("note_path") or "")
    from pathlib import Path

    text = Path(path).read_text(encoding="utf-8") if path and Path(path).exists() else str(found[0])
    assert content in text, text
    assert "in my notes" not in str(found[0].get("title") or ""), found[0]
    if title:
        assert str(found[0].get("title") or "") == title, found[0]


def test_the_note_tool_asks_for_the_content() -> None:
    from core.runtime_tool_contracts import runtime_tool_contracts

    [contract] = [c for c in runtime_tool_contracts() if c.intent == "operator.save_note"]
    assert "content" in contract.input_schema
