"""A notes search looks for the ask's topic, never for the words that ask it.

Before: `operator.find_notes` searched every word it was given, and a note matches only when it
holds all of them. The model calls it with the person's own words, so "any notes" was searched as
the terms "any" and "notes", matched nothing, and the reply said no note matched while a note was
sitting in the workspace. The chat parser's "notes about X" query kept the same filler.

Now one reading (`core.operator.notes.notes_search_topic`) takes the ask off both the model's
`query` argument and the parser's query: no topic lists the newest notes with the total count; a
topic searches that topic alone; a miss names the topic. Read only.

These phrasings are this file's own, written for it; none is a measuring ask.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
from core.operator.models import OperatorActionIntent
from core.operator.notes import find_notes_query
from core.tool_intent_executor import execute_tool_intent

OWNER = {"surface": "api", "platform": "api", "_owner_local": True, "_http_ingress": True,
         "runtime_session_id": "s-notes", "turn_id": "t-notes", "action_policy": "allowed"}
REMOTE = {**OWNER, "_owner_local": False}

# What a model (or a person) puts in the query when the ask has no topic.
NO_TOPIC = (
    "any notes",
    "notes",
    "my notes",
    "all my notes",
    "have I got any notes?",
    "are there any saved notes",
    "do I even have notes yet",
    "is there anything in my notes",
    "what notes do I have",
    "show me every note I've got",
    "any notes saved so far?",
    "notes I have stored here",
    "can you check if there are notes",
    "how many notes have I written",
    "do we have any notes right now",
    "list all of the notes please",
    "what's in my notes?",
    "look through my notes",
    "pull up my saved notes",
    "did I save any notes",
    "show my latest notes",
)

# (query as given, the topic it must search)
TOPICS = (
    ("any notes about the plumber", "plumber"),
    ("do I have notes on the boiler service?", "boiler service"),
    ("notes mentioning the wifi password", "wifi password"),
    ("have I got a note about Rasa's birthday", "Rasa's birthday"),
    ("plumber", "plumber"),
    ("search my notes for invoice 4471", "invoice 4471"),
    ('notes containing "spare key"', "spare key"),
    ("is there anything in my notes regarding the dentist", "dentist"),
    ("find the note with the gate code", "gate code"),
    ("any saved notes on garden tomatoes?", "garden tomatoes"),
)


def _seed(workspace: Path, notes: dict[str, str]) -> None:
    folder = workspace / "notes"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.time() - 1000
    for index, (title, body) in enumerate(notes.items()):
        path = folder / f"{title.lower().replace(' ', '-')}.md"
        path.write_text(f"---\ntitle: {title}\nsession: s-seed\n---\n{body}\n", encoding="utf-8")
        os.utime(path, (stamp + index, stamp + index))  # later in the dict = newer


@pytest.fixture
def workspace(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(root))
    return root


def _find(query: str, context: dict = OWNER):
    return execute_tool_intent(
        {"intent": "operator.find_notes", "arguments": {"query": query}},
        task_id="t-notes",
        session_id="s-notes",
        source_context=dict(context),
        hive_activity_tracker=HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None)),
    )


# -- the reading --------------------------------------------------------------------------------


@pytest.mark.parametrize("query", NO_TOPIC)
def test_an_ask_with_no_topic_has_no_search_terms(query: str) -> None:
    from core.operator.notes import notes_search_topic

    assert notes_search_topic(query) == ""
    assert find_notes_query(OperatorActionIntent(kind="find_notes", target_label=query, raw_text=query)) == ""


@pytest.mark.parametrize(("query", "topic"), TOPICS)
def test_a_real_topic_is_searched_alone(query: str, topic: str) -> None:
    from core.operator.notes import notes_search_topic

    assert notes_search_topic(query) == topic


def test_the_chat_parser_reads_its_query_the_same_way() -> None:
    from core.operator.parser import parse_operator_action_intent

    intent = parse_operator_action_intent("find my notes about anything at all")
    assert intent is not None and intent.kind == "find_notes"
    assert find_notes_query(intent) == ""
    intent = parse_operator_action_intent("search my notes for the gate code")
    assert intent is not None and find_notes_query(intent) == "gate code"


# -- the model's tool call, through the real executor ------------------------------------------


@pytest.mark.parametrize("query", NO_TOPIC)
def test_a_no_topic_call_lists_the_newest_notes_with_the_total(query: str, workspace: Path) -> None:
    _seed(workspace, {"Plumber": "the plumber comes on friday at 9", "Groceries": "eggs, oat milk"})
    execution = _find(query)
    assert execution.ok, execution.response_text
    text = execution.response_text
    assert text.startswith("Notes in this workspace (2)"), text
    assert text.index("Groceries") < text.index("Plumber"), "newest first"
    assert "match" not in text


def test_many_notes_show_the_newest_and_count_the_rest(workspace: Path) -> None:
    _seed(workspace, {f"Note {i:02d}": f"body {i}" for i in range(14)})
    text = _find("any notes").response_text
    assert text.startswith("Notes in this workspace (14), newest 10 first:"), text
    assert "Note 13" in text and "Note 04" in text and "Note 03" not in text
    assert "…and 4 older." in text


def test_an_empty_workspace_says_there_are_none_yet(workspace: Path) -> None:
    assert _find("have I got any notes?").response_text == "There are no notes in this workspace yet."


@pytest.mark.parametrize(("query", "topic"), TOPICS)
def test_a_topic_call_finds_only_the_note_about_that_topic(query: str, topic: str, workspace: Path) -> None:
    _seed(workspace, {"About it": f"all about {topic} here", "Other": "nothing related at all"})
    text = _find(query).response_text
    assert text.startswith(f"Notes matching {topic!r} (1):"), text
    assert "Other" not in text


@pytest.mark.parametrize(("query", "topic"), TOPICS)
def test_a_miss_names_the_topic_not_the_ask(query: str, topic: str, workspace: Path) -> None:
    _seed(workspace, {"Other": "nothing related"})
    text = _find(query).response_text
    assert text == f"No notes in this workspace match {topic!r}.", text


def test_a_remote_turn_cannot_read_the_owners_notes(workspace: Path) -> None:
    _seed(workspace, {"Private": "the safe code is 0000"})
    execution = _find("any notes", REMOTE)
    assert not execution.ok
    assert "safe code" not in execution.response_text and "Private" not in execution.response_text


def test_the_search_writes_nothing(workspace: Path) -> None:
    _seed(workspace, {"Plumber": "friday"})
    before = sorted((p.name, p.stat().st_mtime) for p in workspace.rglob("*") if p.is_file())
    _find("any notes")
    _find("notes about the plumber")
    after = sorted((p.name, p.stat().st_mtime) for p in workspace.rglob("*") if p.is_file())
    assert before == after
