"""Reminders read the way people write and answer questions about themselves (0.7.8 sweep block 3).
A real schedule-then-list round trip on an isolated runtime home; fresh wording only.
Contributor: sls_0x.
"""
from __future__ import annotations

import pytest

from core.local_operator_actions import dispatch_operator_action
from core.operator.parser import parse_operator_action_intent


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    from core import runtime_paths
    from storage.db import configure_default_db_path, reset_default_connection

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    monkeypatch.setenv("TZ", "Europe/Vilnius")
    runtime_paths.configure_runtime_home(tmp_path)
    configure_default_db_path(tmp_path / "data" / "t.db")
    reset_default_connection()
    from storage.migrations import run_migrations

    run_migrations()
    monkeypatch.setattr("core.user_preferences.load_user_timezone", lambda: "")
    yield
    reset_default_connection()
    configure_default_db_path(None)
    runtime_paths.configure_runtime_home(None)


def _say(text: str, session: str = "rem-chat"):
    intent = parse_operator_action_intent(text)
    assert intent is not None, text
    return intent, dispatch_operator_action(intent, task_id="t", session_id=session)


def test_shorthand_tomorrow_morning_is_tomorrow_local_and_the_title_is_clean():
    intent, result = _say("remind me tmrw 7 in the morning to repot the basil")
    assert intent.kind == "schedule_reminder" and result.ok, result.response_text
    assert "repot the basil" in result.response_text and "tmrw" not in result.response_text
    _, listed = _say("what reminders have i got?")
    assert "repot the basil" in listed.response_text and "EET" in listed.response_text


def test_asking_about_a_reminder_that_does_not_exist_says_so_plainly():
    _say("remind me tmrw 7 in the morning to repot the basil")
    intent, result = _say("did i set up a reminder to renew the parking permit?")
    assert intent.kind == "list_reminders"
    assert "I don't have a reminder about “renew the parking permit”" in result.response_text
    assert "1 other pending reminder" in result.response_text


def test_asking_about_a_reminder_that_exists_shows_it():
    _say("remind me tomorrow 6 in the evening to call the plumber")
    _, result = _say("is there a reminder about the plumber?")
    assert "call the plumber" in result.response_text
