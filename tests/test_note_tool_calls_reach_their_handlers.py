"""A model's agenda, reminder and Apple Notes calls reach their handlers with what they asked for.

Measured at aeb58465 through `core.tool_intent_executor.execute_tool_intent`, the executor the served tool loop calls
for every model tool call:

* ``operator.show_agenda {"window": "today"}`` asked "Which days should the agenda cover?": the window was dropped.
* ``operator.list_reminders``, ``operator.apple_note_list`` and ``operator.apple_note_read`` answered "That action is
  not wired on this runtime yet." while the chat parser ran the same reads for typed requests.

These run in process rather than on the served daemon (tests/test_note_tool_calls_served.py): the operator family's
expansion does not seat show_agenda or list_reminders on a served turn, and Apple Notes is answered by `FakeNotes`,
which only an in-process test can install. Never osascript. LABELLED: no model, synthetic Notes.
"""
from __future__ import annotations

import copy

import pytest

from tests.pa_beta_gate._pc_calendar_rig import prepare_home
from tests.pa_beta_gate.test_pc_notes_identity import FakeNotes

SESSION = "chat-note-tools"
_SEED = {
    "x-coredata://Note/work-plan": {"title": "Plan", "folder": "Work", "account": "iCloud", "body": "<div>budget draft</div>"},
    "x-coredata://Note/home-plan": {"title": "Plan", "folder": "Personal", "account": "iCloud", "body": "<div>paint the hall</div>"},
    "x-coredata://Note/groceries": {"title": "Groceries", "folder": "Personal", "account": "iCloud", "body": "<div>eggs</div>"},
}


@pytest.fixture
def home(tmp_path, monkeypatch):
    return prepare_home(tmp_path, monkeypatch)


@pytest.fixture
def notes(home, monkeypatch):
    return FakeNotes(copy.deepcopy(_SEED)).install(monkeypatch)


def _model_call(intent: str, arguments: dict):
    from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
    from core.tool_intent_executor import execute_tool_intent

    return execute_tool_intent(
        {"intent": intent, "arguments": arguments},
        task_id="task-note-tools",
        session_id=SESSION,
        source_context={"surface": "openclaw", "platform": "openclaw", "operating_mode": "auto"},
        hive_activity_tracker=HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None)),
    )


def test_an_agenda_call_reads_the_window_it_names(home) -> None:
    named = _model_call("operator.show_agenda", {"window": "today"})
    assert "Which days should the agenda cover" not in named.response_text, named.response_text
    # No calendar is set up here, so the agenda lane answers that, past its window reading.
    assert named.status == "unavailable" and "No calendars are chosen" in named.response_text, named.response_text

    unnamed = _model_call("operator.show_agenda", {})
    assert unnamed.status == "invalid_request" and "Which days should the agenda cover" in unnamed.response_text


def test_a_reminders_call_lists_this_chats_reminders(home) -> None:
    from core.local_operator_actions import dispatch_operator_action, parse_operator_action_intent

    typed = parse_operator_action_intent("remind me to water the plants tomorrow at 9:00")
    assert dispatch_operator_action(typed, task_id="task-seed", session_id=SESSION).ok

    listed = _model_call("operator.list_reminders", {})

    assert listed.ok and listed.mode == "tool_executed", listed.response_text
    assert "not wired" not in listed.response_text
    assert "water the plants" in listed.response_text, listed.response_text


def test_an_apple_note_read_reads_the_title_in_the_folder_it_names(notes) -> None:
    work = _model_call("operator.apple_note_read", {"title": "Plan", "folder": "Work"})
    assert work.ok, work.response_text
    assert "budget draft" in work.response_text and "paint the hall" not in work.response_text

    # Two notes share the title: without a folder the read names both and reads neither.
    sent = len(notes.scripts)
    either = _model_call("operator.apple_note_read", {"title": "Plan"})
    assert not either.ok and "2 notes share that title" in either.response_text, either.response_text
    assert not any("return body of theNote" in script for script in notes.scripts[sent:])


def test_an_apple_note_list_lists_the_folder_it_names(notes) -> None:
    listed = _model_call("operator.apple_note_list", {"folder": "Personal"})

    assert listed.ok, listed.response_text
    assert listed.response_text.splitlines() == ["Apple Notes has 2 note(s) in the Personal folder:", "- Plan", "- Groceries"], (
        listed.response_text)


def test_an_apple_note_list_with_no_folder_says_where_each_note_is(notes) -> None:
    listed = _model_call("operator.apple_note_list", {})

    assert listed.ok, listed.response_text
    assert listed.response_text.splitlines()[1:] == ["- Plan (Work folder)", "- Plan (Personal folder)", "- Groceries (Personal folder)"], (
        listed.response_text)
    assert "x-coredata" not in listed.response_text, "the listing showed Notes' internal ids"
