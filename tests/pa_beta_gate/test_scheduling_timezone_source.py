"""pa_beta_gate -- which timezone "tomorrow at 9am" is read in, on a home set up the product's way.

The scheduling rigs elsewhere call save_user_timezone(), which no product surface does. A real
home carries only the Operator Profile timezone the user told VOOL, so these tests start from a
home with no stored preference and drive the reminder lane through the chat seam.

LABELLED: injected clock (both doors), isolated home; no live provider.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from ._pc_calendar_rig import Clock

pytestmark = [pytest.mark.pa_beta]

T0 = datetime(2026, 10, 24, 7, 0, tzinfo=timezone.utc)  # Saturday; the next day is after the October change


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    from core import operator_profile, runtime_paths

    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", None, raising=False)
    operator_profile.reset_table_cache_for_tests()
    from storage.migrations import run_migrations

    run_migrations()
    # The test database outlives one test (the session conftest owns it), so start and end with
    # no stored preference, no owner timezone item and the profile unpaused.
    _clear_timezone_sources()
    from core.time_authority import CLOCK

    fixed = Clock(T0, monkeypatch)
    monkeypatch.setattr(CLOCK, "now_for_timezone", lambda name: fixed.now.astimezone(ZoneInfo(name)))
    yield tmp_path
    _clear_timezone_sources()
    operator_profile.reset_table_cache_for_tests()


def _clear_timezone_sources() -> None:
    from core import operator_profile
    from core.user_preferences import load_preferences, save_preferences

    prefs = load_preferences()
    prefs.timezone = ""
    save_preferences(prefs)
    operator_profile.set_paused(operator_profile.OWNER_PRINCIPAL, False)
    for item in operator_profile.list_items(operator_profile.OWNER_PRINCIPAL):
        if item.category == "timezone" and item.status == operator_profile.STATUS_ACTIVE:
            operator_profile.forget_item(item.item_id, actor="test", reason="isolate")


def _schedule(text: str) -> dict:
    from core.local_operator_actions import dispatch_operator_action, parse_operator_action_intent
    from core.operator.reminders import list_reminders
    from storage.db import get_connection

    session = f"tz-{uuid.uuid4().hex[:6]}"
    intent = parse_operator_action_intent(text)
    assert intent is not None
    result = dispatch_operator_action(intent, task_id=f"task-{uuid.uuid4().hex[:8]}", session_id=session)
    assert result.ok, result.response_text
    [row] = list_reminders(session_id=session, get_connection_fn=get_connection)
    return row


def _remember_zone(zone: str) -> None:
    from core import operator_profile

    change = operator_profile.remember(operator_profile.OWNER_PRINCIPAL, "timezone", zone, replace=True)
    assert operator_profile.resolve(operator_profile.OWNER_PRINCIPAL, "timezone") is not None, change


def test_the_profile_timezone_the_user_gave_is_the_scheduling_zone(home):
    _remember_zone("Europe/Athens")
    row = _schedule("remind me tomorrow at 9am to call the bank")
    assert row["due_at_utc"].startswith("2026-10-25T07:00"), row["due_at_utc"]  # 09:00 EET
    assert row["tz_name"] == "Europe/Athens"


def test_with_no_timezone_known_the_fallback_stays_utc(home):
    row = _schedule("remind me tomorrow at 9am to call the bank")
    assert row["due_at_utc"].startswith("2026-10-25T09:00"), row["due_at_utc"]


def test_a_stored_preference_still_wins_over_the_profile(home):
    from core.user_preferences import save_user_timezone

    _remember_zone("Europe/Athens")
    assert save_user_timezone("America/New_York")
    row = _schedule("remind me tomorrow at 9am to call the bank")
    assert row["due_at_utc"].startswith("2026-10-25T13:00"), row["due_at_utc"]  # 09:00 EDT


def test_a_paused_profile_is_not_consulted(home):
    from core import operator_profile

    _remember_zone("Europe/Athens")
    assert operator_profile.set_paused(operator_profile.OWNER_PRINCIPAL, True)
    row = _schedule("remind me tomorrow at 9am to call the bank")
    assert row["due_at_utc"].startswith("2026-10-25T09:00"), row["due_at_utc"]


def test_a_zone_named_in_the_request_still_wins(home):
    _remember_zone("Europe/Athens")
    row = _schedule("remind me tomorrow at 9am America/New_York time to call the bank")
    assert row["due_at_utc"].startswith("2026-10-25T13:00"), row["due_at_utc"]
