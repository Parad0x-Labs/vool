"""Source-selection development repros; exposed cases are regression only.

These authored sources exercise row/header and list-item binding under
query-frame distractors. All evidence must stay source-attributed and scoped.
"""
from __future__ import annotations

import pytest

from core.context_retrieval import _evidence_clause_windows

LIST_BODY = (
    "Our previous chat about the Copper Observatory expedition covered the party.\n"
    "The Copper Observatory expedition lets the party face a collector first.\n"
    "In the Copper Observatory expedition the party will face a gate trial.\n"
    "Encounters:\n* Clockwork beetles (9)\n* Stone sentries (3)\n"
    "* Glass hawks (6)\n"
    "Remember that the party can adjust the expedition in our previous chat."
)
LIST_ASK = (
    "I'm going back to our previous chat about the Copper Observatory "
    "expedition. Can you remind me how many clockwork beetles the party faces?"
)
TABLE_BODY = (
    "Our previous chat about the lantern workshop rotation covered its schedule.\n"
    "The lantern workshop rotation schedule can be adjusted in our previous chat.\n"
    "You can remind the lantern workshop crew about the rotation schedule.\n"
    "| Day | 06:00-12:00 | 12:00-18:00 |\n"
    "| --- | --- | --- |\n"
    "| Monday | Iris | Tomas |\n"
    "| Tuesday | Tomas | Iris |\n"
    "| Wednesday | Iris | Tomas |\n"
)
TABLE_ASK = (
    "I'm checking our previous chat about the lantern workshop rotation "
    "schedule. Can you remind me what was the rotation for Tomas on Tuesday?"
)


def _windows(question, source):
    return _evidence_clause_windows(question, source)


def test_named_count_item_survives_more_verbose_intro():
    windows = _windows(LIST_ASK, LIST_BODY)
    assert any("Clockwork beetles (9)" in w["text"] for w in windows), windows


def test_table_lookup_keeps_answer_row_and_column_labels():
    windows = _windows(TABLE_ASK, TABLE_BODY)
    rendered = "\n".join(w["text"] for w in windows)
    assert "| Tuesday | Tomas | Iris |" in rendered, windows
    assert "06:00-12:00" in rendered, windows
    assert "12:00-18:00" in rendered, windows


@pytest.fixture()
def source_env(tmp_path, monkeypatch):
    import core.embedding_service as embeddings
    from core import runtime_paths
    from storage.migrations import run_migrations

    home = tmp_path / "source-world"
    home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_CONTEXT_CAPSULE_V2", "1")
    monkeypatch.setenv("VOOL_CAPSULE_DEBUG_DROPS", "1")
    previous = runtime_paths._VOOL_HOME_OVERRIDE
    try:
        runtime_paths.configure_runtime_home(home)
        monkeypatch.setattr(embeddings, "_best_embed_model", lambda: None)
        run_migrations()
        yield home
    finally:
        runtime_paths.configure_runtime_home(previous)


def _store(home, chat, user, assistant, stated=1700000000):
    from core.context_namespace import ensure_chat_namespace
    from core.context_retrieval import store_turn
    from core.memory.entries import resolve_memory_access_policy

    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    receipt = store_turn(chat, user, assistant, access_policy=policy,
                         source_context={"chat_id": chat,
                                         "runtime_home": str(home),
                                         "statement_at": stated})
    assert receipt["status"] in {"stored", "retained"}, receipt


def _recall(home, chat, question):
    from core.context_retrieval import inject_retrieved
    from core.memory.entries import resolve_memory_access_policy

    output = inject_retrieved(
        chat, question, [{"role": "user", "content": question}],
        access_policy=resolve_memory_access_policy(chat_id=chat),
        source_context={"chat_id": chat, "runtime_home": str(home),
                        "surface": "channel", "platform": "api"})
    return "\n".join(str(m.get("content") or "") for m in output
                      if "<retrieved_context>" in str(m.get("content") or ""))


@pytest.mark.parametrize("question,body,expected", [
    (LIST_ASK, LIST_BODY, "Clockwork beetles (9)"),
    (TABLE_ASK, TABLE_BODY, "| Tuesday | Tomas | Iris |"),
])
def test_real_capsule_keeps_bound_structured_answer(source_env, question, body, expected):
    _store(source_env, "structure", "Please draft the plan and schedule.", body)
    capsule = _recall(source_env, "structure", question)
    assert expected in capsule, capsule
    assert "assistant said" in capsule, capsule
    assert not any(expected in line and "user said" in line
                   for line in capsule.splitlines()), capsule


def test_foreign_structure_is_not_served(source_env):
    _store(source_env, "foreign-structure", "Please draft the plan.", LIST_BODY)
    _store(source_env, "asking-structure", "My new telescope is blue.", "Noted.")
    capsule = _recall(source_env, "asking-structure", LIST_ASK)
    assert "Clockwork beetles (9)" not in capsule, capsule


def test_structure_does_not_invent_missing_count():
    body = LIST_BODY.replace("* Clockwork beetles (9)", "* Clockwork beetles")
    windows = _windows(LIST_ASK, body)
    assert not any("Clockwork beetles (9)" in w["text"] for w in windows)


def test_duration_keeps_start_and_finish_events(source_env):
    _store(source_env, "duration", "I started reading The Amber Atlas today.", "Enjoy.", 1704067200)
    _store(source_env, "duration", "I finished reading The Amber Atlas today.", "Well done.", 1704931200)
    capsule = _recall(source_env, "duration", "How many days did it take me to finish The Amber Atlas?")
    assert "started reading The Amber Atlas" in capsule, capsule
    assert "finished reading The Amber Atlas" in capsule, capsule
    assert "2024-01-01" in capsule and "2024-01-11" in capsule, capsule


def test_item_label_outranks_unrelated_item_description():
    body = (LIST_BODY + "\n* Flash attack. You can remind the party about our previous "
            "chat; a clockwork beetle can face any creature within 3 metres.\n")
    windows = _windows(LIST_ASK, body)
    bound = [w for w in windows if w.get("structure_bound")]
    assert bound and "* Clockwork beetles (9)" in bound[0]["text"], windows
