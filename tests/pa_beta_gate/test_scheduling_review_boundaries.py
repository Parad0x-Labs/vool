"""Scheduling integration checks: ambiguous input cannot become an unintended effect."""
from datetime import datetime, timedelta, timezone

import pytest

from core.local_operator_actions import dispatch_operator_action, parse_operator_action_intent
from core.operator import reminders
from core.operator.models import OperatorActionIntent
from core.operator.reminder_dispatcher import ReminderDispatcher
from core.operator.when import MAX_FUTURE_DAYS, parse_when_expression
from storage.db import get_connection
from storage.migrations import run_migrations

pytestmark = pytest.mark.pa_beta
NOW = datetime(2026, 9, 12, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize("text", [
    "2026-10-25 03:30 Europe/Berlin",
    "remind me to check the locks on 2026-11-01 01:30 America/New_York",
])
def test_ambiguous_clock_requires_a_choice(text):
    result = parse_when_expression(text, now_fn=lambda: NOW)
    assert not result.ok and not result.due_at_utc
    assert "twice" in result.problem.lower() or "ambiguous" in result.problem.lower()


@pytest.mark.parametrize("text", [
    "2035-09-14 09:30 Europe/Berlin",
    "remind me about the inspection on 2029-01-03 17:20 America/Chicago",
    "in 9999999999999999999999999999999999999999999 weeks",
    "2026-09-14",
    "tomorrow at 13 pm",
    "at 25:70",
    "in -2 minutes",
    "2026-09-14 10:00 Europe/Atlantis",
    "tomorrow at 9:00 Mars/Olympus timezone",
])
def test_invalid_or_incomplete_time_cannot_schedule(text):
    result = parse_when_expression(text, now_fn=lambda: NOW)
    assert not result.ok and result.problem and not result.due_at_utc


def test_horizon_is_relative_to_now_and_preserves_valid_boundaries():
    limit = NOW + timedelta(days=MAX_FUTURE_DAYS)
    assert parse_when_expression(limit.strftime("%Y-%m-%d %H:%M"), now_fn=lambda: NOW).ok
    assert not parse_when_expression((limit + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M"), now_fn=lambda: NOW).ok
    normal = parse_when_expression("tomorrow at 9:15 Europe/Berlin", now_fn=lambda: NOW)
    assert normal.ok and normal.due_at_utc == "2026-09-13T06:15:00+00:00"
    # A path in the note is not a timezone declaration.
    assert parse_when_expression("remind me to archive src/main in 20 minutes", now_fn=lambda: NOW).ok


@pytest.mark.parametrize("text,kind", [
    ("cancel my reminder", "cancel_reminder"),
    ("move my reminder to tomorrow at 9:00", "move_reminder"),
    ("remind me to cancel the meeting tomorrow at 9:00", "schedule_reminder"),
    ("remind me to remove the reminder note in 20 minutes", "schedule_reminder"),
    ("show my reminders", "list_reminders"),
])
def test_request_verb_owns_the_action_not_words_inside_its_subject(text, kind):
    assert parse_operator_action_intent(text).kind == kind


@pytest.mark.parametrize("text", ["cancel it", "move it tomorrow at 9:00", "what is a reminder?"])
def test_context_free_words_do_not_claim_a_scheduling_effect(text):
    intent = parse_operator_action_intent(text)
    assert intent is None or intent.kind not in {"cancel_reminder", "move_reminder", "schedule_reminder"}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    from core import runtime_paths
    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", None)
    run_migrations()


def create(note, session="review", due=None):
    return reminders.schedule_reminder(
        session_id=session, task_id="review-" + note, note=note,
        due_at_utc=(due or NOW - timedelta(minutes=1)).isoformat(),
        tz_name="UTC", get_connection_fn=get_connection,
    )["reminder_id"]


@pytest.mark.parametrize("verb", ["cancel", "move"])
def test_ambiguous_or_foreign_reminder_id_never_falls_back_to_another(store, verb):
    first = create("orchard")
    second = create("harbor")
    foreign = create("other", session="elsewhere")
    for target in (None, foreign, "00000000-0000-0000-0000-000000000000"):
        result = dispatch_operator_action(
            OperatorActionIntent(kind=f"{verb}_reminder", action_id=target,
                                 raw_text=f"{verb} reminder in 20 minutes"),
            task_id="change", session_id="review",
        )
        assert not result.ok
        for rid in (first, second, foreign):
            row = reminders.load_reminder(rid, get_connection_fn=get_connection)
            assert row["status"] == "scheduled" and row["due_at_utc"] == (NOW - timedelta(minutes=1)).isoformat()


def test_displayed_short_id_selects_exact_reminder(store):
    first, second = create("first"), create("second")
    intent = parse_operator_action_intent(f"cancel reminder {second[:8]}")
    result = dispatch_operator_action(intent, task_id="cancel", session_id="review")
    assert result.ok
    assert reminders.load_reminder(first, get_connection_fn=get_connection)["status"] == "scheduled"
    assert reminders.load_reminder(second, get_connection_fn=get_connection)["status"] == "cancelled"


@pytest.mark.parametrize("mode", ["exception_after_effect", "unproven_refusal"])
def test_unknown_delivery_outcome_is_not_replayed(store, mode):
    rid = create("once")
    effects = []
    def deliver(row):
        effects.append(row["reminder_id"])
        if mode == "exception_after_effect":
            raise OSError("acknowledgement lost after effect")
        return {"ok": False, "reason": "outcome unavailable"}
    dispatcher = ReminderDispatcher(get_connection_fn=get_connection, deliver_fn=deliver,
                                    sleep_fn=lambda _: None, now_fn=lambda: NOW.isoformat())
    dispatcher.sweep_once()
    dispatcher.sweep_once()
    assert effects == [rid]
    assert reminders.load_reminder(rid, get_connection_fn=get_connection)["status"] == "delivery_uncertain"


def test_move_between_due_query_and_claim_defers_delivery(store, monkeypatch):
    rid = create("rescheduled")
    original = reminders.due_reminders
    def read_then_move(**kwargs):
        rows = original(**kwargs)
        reminders.move_reminder(rid, due_at_utc=(NOW + timedelta(days=1)).isoformat(), get_connection_fn=get_connection)
        return rows
    monkeypatch.setattr(reminders, "due_reminders", read_then_move)
    effects = []
    dispatcher = ReminderDispatcher(get_connection_fn=get_connection,
                                    deliver_fn=lambda row: effects.append(row) or {"ok": True},
                                    sleep_fn=lambda _: None, now_fn=lambda: NOW.isoformat())
    dispatcher.sweep_once()
    assert effects == []
    assert reminders.load_reminder(rid, get_connection_fn=get_connection)["status"] == "scheduled"


def test_cancel_during_delivery_does_not_promise_the_effect_was_prevented(store):
    rid = create("already in flight")
    cancellations = []
    def deliver(row):
        cancellations.append(reminders.cancel_reminder(rid, get_connection_fn=get_connection))
        return {"ok": True, "effect": "collector"}
    ReminderDispatcher(get_connection_fn=get_connection, deliver_fn=deliver,
                       sleep_fn=lambda _: None, now_fn=lambda: NOW.isoformat()).sweep_once()
    assert not cancellations[0]["ok"]
    assert reminders.load_reminder(rid, get_connection_fn=get_connection)["status"] == "delivered"


@pytest.mark.parametrize("text", ["remind me to stretch in 2 minutes", "cancel the reminder", "move the reminder in 10 minutes"])
def test_scheduling_honors_disabled_local_actions(store, monkeypatch, text):
    from core import policy_engine
    previous = policy_engine.get
    monkeypatch.setattr(policy_engine, "get", lambda key, default=None: False if key == "execution.allow_safe_local_actions" else previous(key, default))
    rid = create("keep")
    result = dispatch_operator_action(parse_operator_action_intent(text), task_id="denied", session_id="review")
    assert not result.ok and result.status == "blocked"
    row = reminders.load_reminder(rid, get_connection_fn=get_connection)
    assert row["status"] == "scheduled" and row["note"] == "keep"
    assert len(reminders.list_reminders(session_id="review", get_connection_fn=get_connection)) == 1


def test_calendar_move_preserves_uid_and_original_duration_on_repeated_edits(store):
    import re
    from pathlib import Path
    from tests.pa_beta_gate.test_reminder_vertical import _create_calendar_draft
    made = _create_calendar_draft("review-calendar")
    assert made.ok
    path = Path(made.details["ics_path"])
    before = path.read_text()
    uid = re.search(r"(?m)^UID:(.*)$", before).group(1)
    def duration(text):
        start = re.search(r"(?m)^DTSTART:(.*)$", text).group(1)
        end = re.search(r"(?m)^DTEND:(.*)$", text).group(1)
        return datetime.strptime(end, "%Y%m%dT%H%M%SZ") - datetime.strptime(start, "%Y%m%dT%H%M%SZ")
    for date in ("2026-09-16 10:15 Europe/Berlin", "2026-09-17 12:40 Europe/Berlin"):
        result = dispatch_operator_action(parse_operator_action_intent("move the meeting to " + date), task_id="move", session_id="review-calendar")
        assert result.ok, result.response_text
        after = path.read_text()
        assert re.search(r"(?m)^UID:(.*)$", after).group(1) == uid
        assert duration(after) == duration(before)


@pytest.mark.parametrize('verb', ['cancel', 'move'])
def test_calendar_mutation_never_guesses_between_two_drafts(store, verb):
    from pathlib import Path
    from tests.pa_beta_gate.test_reminder_vertical import _create_calendar_draft
    first = _create_calendar_draft('two-drafts')
    second = _create_calendar_draft('two-drafts')
    paths = [Path(result.details['ics_path']) for result in (first, second)]
    before = [p.read_bytes() for p in paths]
    suffix = ' to 2026-09-18 10:00 Europe/Berlin' if verb == 'move' else ''
    result = dispatch_operator_action(parse_operator_action_intent(f'{verb} the calendar draft{suffix}'), task_id='ambiguous', session_id='two-drafts')
    assert not result.ok
    assert [p.read_bytes() for p in paths] == before
    selected = dispatch_operator_action(parse_operator_action_intent(f"{verb} calendar draft {first.details['action_id']}{suffix}"), task_id='selected', session_id='two-drafts')
    assert selected.ok, selected.response_text
    assert paths[1].read_bytes() == before[1]


@pytest.mark.parametrize('target', ['00000000-0000-0000-0000-000000000000', '"Dentist"'])
def test_calendar_unknown_explicit_target_never_falls_back(store, target):
    from pathlib import Path
    from tests.pa_beta_gate.test_reminder_vertical import _create_calendar_draft
    made = _create_calendar_draft('explicit-draft')
    path = Path(made.details['ics_path'])
    before = path.read_bytes()
    result = dispatch_operator_action(parse_operator_action_intent(f'cancel calendar draft {target}'), task_id='missing', session_id='explicit-draft')
    assert not result.ok and path.read_bytes() == before


def test_explicit_utc_is_not_interpreted_in_the_host_zone():
    from zoneinfo import ZoneInfo
    now = NOW.astimezone(ZoneInfo('Europe/Berlin'))
    parsed = parse_when_expression('2026-10-25 01:30 UTC', now_fn=lambda: now)
    assert parsed.ok and parsed.due_at_utc == '2026-10-25T01:30:00+00:00'


def test_saved_timezone_owns_future_schedules_across_dst(store, monkeypatch):
    from core.local_operator_actions import _parse_when
    from core.user_preferences import save_user_timezone
    from core.time_authority import TimeAuthority
    from zoneinfo import ZoneInfo
    assert save_user_timezone('Europe/Berlin')
    monkeypatch.setattr(TimeAuthority, 'now_for_timezone', lambda self, name: NOW.astimezone(ZoneInfo(name)))
    parsed = _parse_when('2026-12-12 09:00')
    assert parsed.ok and parsed.due_at_utc == '2026-12-12T07:00:00+00:00'


def test_stale_calendar_selection_cannot_cancel_a_moved_draft(store):
    from pathlib import Path
    from core.local_operator_actions import _load_executed_calendar_draft, _cancel_calendar_artifact
    from tests.pa_beta_gate.test_reminder_vertical import _create_calendar_draft
    made = _create_calendar_draft('stale-draft')
    old = _load_executed_calendar_draft(session_id='stale-draft')
    moved = dispatch_operator_action(parse_operator_action_intent('move the calendar draft to 2026-09-18 10:00 UTC'), task_id='move', session_id='stale-draft')
    assert moved.ok
    path = Path(made.details['ics_path'])
    before = path.read_bytes()
    result = _cancel_calendar_artifact(old)
    assert not result['ok'] and path.read_bytes() == before
