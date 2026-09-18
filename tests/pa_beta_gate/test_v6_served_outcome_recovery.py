"""pa_beta_gate -- revision-6 served recovery for the corrected outcome contracts.

Every step is a real agent turn (``VoolAgent.run_once``) through the production conversational path,
in one session: served ingress, routing, the operator lane, approval pause and resume, the final claim
guard and session history. MODEL STAND-IN (labelled): the model lane returns the deterministic
stand-in the served workflow tests use, so these prove routing, effects, durable state and recovery --
not model wording. Native Apple Notes goes through an injected runner double (never osascript);
calendar effects go to disposable loopback services. Every test counts the native or provider effects
it caused and reads durable state, not only the answer text.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from tests.pa_beta_gate._json_api_service import start_json_calendar_fixture
from tests.pa_beta_gate.test_served_calendar_notes_workflows import (  # noqa: F401 -- served_env is a fixture, requested via getfixturevalue
    VILNIUS_CAL,
    _approval_id,
    _turn,
    served_env,
)
from tests.pa_beta_gate.test_v6_calendar_unreadable_recovery import _count_posts, _serve_unreadable
from tests.test_turn_attempt_chain import _Harness

GRAPH_CAL = "AAMkAGIyM2Fj"


def _note_texts(workspace) -> list[str]:
    return [path.read_text(encoding="utf-8") for path in (Path(workspace) / "notes").glob("*.md")]


def _journal_operations(workspace) -> list[dict]:
    path = Path(workspace) / "notes" / ".apple-notes-effects.json"
    return list(json.loads(path.read_text(encoding="utf-8"))["operations"].values()) if path.exists() else []


class _NotesBridge:
    """LABELLED native double behind the real bridge: the first call creates a note and then fails as
    scripted; later calls create normally. ``created`` counts notes the double made."""

    def __init__(self, monkeypatch, first_failure):
        from core.operator import apple_notes

        self.calls = 0
        self.created = 0
        self._first_failure = first_failure
        real = apple_notes.create_apple_note
        monkeypatch.setattr(apple_notes, "create_apple_note", lambda **kw: real(**kw, runner=self.run))

    def run(self, command, **kwargs):
        self.calls += 1
        self.created += 1
        if self.calls == 1:
            outcome = self._first_failure
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        return SimpleNamespace(returncode=0, stdout=f"note id x-coredata://served-v6/p{self.calls}", stderr="")


# ---------------------------------------------------------------------------------------------
# Contract 1: a native Notes failure after the bridge started is unresolved, never a refusal
# ---------------------------------------------------------------------------------------------


def test_served_note_terminated_after_create_stays_unresolved_and_the_follow_up_sends_nothing(request, monkeypatch):
    env = request.getfixturevalue("served_env")
    harness, workspace = env["harness"], env["workspace"]
    bridge = _NotesBridge(monkeypatch, SimpleNamespace(returncode=-9, stdout="", stderr=""))
    text = 'save a note to Apple Notes titled "Pump station log" with: inlet pressure 2.1 bar after flush'

    first = _turn(harness, text, workspace)
    assert first.startswith("Note saved: 'Pump station log' at "), first
    assert "destination is NOT resolved (bridge_terminated)" in first and "not established" in first, first
    assert "no note was created" not in first.lower(), first
    assert [op["state"] for op in _journal_operations(workspace)] == ["ambiguous"]

    follow_up = _turn(harness, text, workspace)
    assert "destination is NOT resolved (earlier_attempt_unresolved)" in follow_up, follow_up
    assert (bridge.calls, bridge.created) == (1, 1), "the explicit follow-up must not create a second note"
    assert [op["state"] for op in _journal_operations(workspace)] == ["ambiguous"]
    assert len([t for t in _note_texts(workspace) if "inlet pressure 2.1 bar" in t]) == 2, "one labelled fallback per request"


def test_served_apple_event_timeout_after_create_blocks_a_reworded_same_content_request(request, monkeypatch):
    env = request.getfixturevalue("served_env")
    harness, workspace = env["harness"], env["workspace"]
    bridge = _NotesBridge(monkeypatch, SimpleNamespace(returncode=1, stdout="", stderr="execution error: Notes got an error: AppleEvent timed out. (-1712)"))

    first = _turn(harness, 'save a note to Apple Notes titled "Chiller alarm" with: compressor tripped at 03:40, reset at 03:55', workspace)
    assert "destination is NOT resolved (apple_event_timeout)" in first and "no note was created" not in first.lower(), first

    reworded = _turn(harness, 'please save a note to Apple Notes titled "Chiller alarm" with: compressor tripped at 03:40, reset at 03:55', workspace)
    assert "destination is NOT resolved (earlier_attempt_unresolved)" in reworded, reworded
    assert (bridge.calls, bridge.created) == (1, 1)

    unrelated = _turn(harness, 'save a note to Apple Notes titled "Chiller spares" with: order two relay contactors', workspace)
    assert unrelated.startswith("Note saved in Apple Notes: 'Chiller spares'"), unrelated
    assert (bridge.calls, bridge.created) == (2, 2), "independent content still reaches Notes"
    assert sorted(op["state"] for op in _journal_operations(workspace)) == ["ambiguous", "confirmed"]


# ---------------------------------------------------------------------------------------------
# Contract 2: calendar data the runtime cannot read is never free time, a send or a recovery verdict
# ---------------------------------------------------------------------------------------------


@pytest.fixture
def served_graph_env(tmp_path, monkeypatch):
    """The served harness against the disposable Microsoft Graph loopback service."""
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(workspace))
    from core import runtime_paths

    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", None, raising=False)
    from storage.migrations import run_migrations

    run_migrations()
    from core.user_preferences import save_user_timezone

    assert save_user_timezone("Europe/Berlin")
    server, state, base_url = start_json_calendar_fixture(dialect="graph", calendars={GRAPH_CAL: "Work"})
    monkeypatch.setenv("VOOL_CALENDAR_PROVIDER", "graph")
    monkeypatch.setenv("VOOL_CALENDAR_URL", base_url)
    monkeypatch.setenv("VOOL_CALENDAR_ID", GRAPH_CAL)
    monkeypatch.setenv("VOOL_CALENDAR_TIMEOUT", "10")
    fixed = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
    from core import local_operator_actions

    monkeypatch.setattr(local_operator_actions, "_scheduling_now", lambda: fixed.astimezone(ZoneInfo("Europe/Berlin")))
    harness = _Harness(f"sess-graph-{uuid.uuid4().hex[:8]}")
    yield {"harness": harness, "state": state, "server": server, "workspace": workspace}
    harness.close()
    server.shutdown()
    server.server_close()


def _graph_titled(state, title: str) -> list[str]:
    return [uid for uid, row in state.snapshot()[GRAPH_CAL].items() if row["summary"] == title]


def _approval_row(session: str, action_id: str) -> dict:
    from core.operator.calendar_provider import load_action_any_state

    return load_action_any_state(session_id=session, action_kind="provider_calendar_event", action_id=action_id)


def test_served_graph_availability_over_an_unreadable_event_offers_nothing_until_it_is_readable(request, monkeypatch):
    """ORIGINAL data: the review's unreadable dateTimes ('not-a-date' / 'also-invalid'), served."""
    env = request.getfixturevalue("served_graph_env")
    harness, state, workspace = env["harness"], env["state"], env["workspace"]
    state.seed_event(GRAPH_CAL, "design@fixture", summary="Design review", start=datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc), minutes=60)
    state.seed_event(GRAPH_CAL, "opaque-busy", summary="Opaque busy", start=datetime(2026, 9, 15, 13, 30, tzinfo=timezone.utc), minutes=60)
    broken = _serve_unreadable(monkeypatch, state, {"opaque-busy"})
    posts = _count_posts(monkeypatch, env["server"])

    unreadable = _turn(harness, "Check Tuesday afternoon for a free 30-minute slot", workspace)
    assert "could not be read as calendar data" in unreadable and "Free 30-minute options" not in unreadable, unreadable
    follow_up = _turn(harness, 'option 1, propose "Sprint planning"', workspace)
    assert "approve calendar" not in follow_up, follow_up
    assert posts == [] and _graph_titled(state, "Sprint planning") == []

    broken.clear()
    readable = _turn(harness, "Check Tuesday afternoon for a free 30-minute slot", workspace)
    assert "Free 30-minute options" in readable and "Opaque busy" in readable, readable
    proposal = _turn(harness, 'option 1, propose "Sprint planning"', workspace)
    executed = _turn(harness, f"approve calendar {_approval_id(proposal)}", workspace)
    assert "created on the provider and verified" in executed, executed
    assert len(posts) == 1 and len(_graph_titled(state, "Sprint planning")) == 1


def test_served_caldav_object_without_uid_is_unreadable_and_nothing_is_staged(request):
    """NOVEL data: a stored calendar object with no UID inside the requested window, on CalDAV."""
    env = request.getfixturevalue("served_env")
    harness, state, workspace = env["harness"], env["state"], env["workspace"]
    state.put_event(VILNIUS_CAL, "no-uid-object", "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Fixture//CalDAV//EN", "BEGIN:VEVENT", "DTSTAMP:20260910T120000Z",
        "DTSTART:20260917T063000Z", "DTEND:20260917T080000Z", "SUMMARY:Vendor walkthrough", "END:VEVENT", "END:VCALENDAR"]) + "\r\n")
    before = state.snapshot()[VILNIUS_CAL]

    availability = _turn(harness, "Look at Thursday morning on my calendar for a free 20-minute slot", workspace)
    assert "could not be read as calendar data" in availability and "Free 20-minute options" not in availability, availability
    proposal = _turn(harness, 'propose "Generator load test" on 2026-09-17 10:00 Europe/Berlin for 20m', workspace)
    assert "approve calendar" not in proposal and "could not be read as calendar data" in proposal, proposal
    assert state.snapshot()[VILNIUS_CAL] == before, "nothing was written to the calendar"

    state.calendars[VILNIUS_CAL]["events"].pop("no-uid-object")
    readable = _turn(harness, "Look at Thursday morning on my calendar for a free 20-minute slot", workspace)
    assert "Free 20-minute options" in readable, readable


def test_served_caldav_event_that_ends_before_it_starts_is_unreadable_not_free(request):
    """NOVEL data: a stored event whose DTEND precedes its DTSTART. The adapter reads it; the operator
    cannot place it, so neither the offer nor the proposal may treat that time as free."""
    env = request.getfixturevalue("served_env")
    harness, state, workspace = env["harness"], env["state"], env["workspace"]
    state.put_event(VILNIUS_CAL, "inverted@fixture", "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Fixture//CalDAV//EN", "BEGIN:VEVENT", "UID:inverted@fixture", "DTSTAMP:20260910T120000Z",
        "DTSTART:20260917T070000Z", "DTEND:20260917T063000Z", "SUMMARY:Inverted hold", "END:VEVENT", "END:VCALENDAR"]) + "\r\n")
    before = state.snapshot()[VILNIUS_CAL]

    availability = _turn(harness, "Look at Thursday morning on my calendar for a free 20-minute slot", workspace)
    assert "could not be read as calendar data" in availability and "Free 20-minute options" not in availability, availability
    proposal = _turn(harness, 'propose "Generator load test" on 2026-09-17 09:15 Europe/Berlin for 60m', workspace)
    assert "approve calendar" not in proposal and "could not be read as calendar data" in proposal, proposal
    assert state.snapshot()[VILNIUS_CAL] == before, "nothing was written to the calendar"


def test_served_graph_recovery_over_unreadable_rows_never_sends_the_create_twice(request, monkeypatch):
    """NOVEL recovery: a withheld create reply, then a recovery read whose rows are unreadable."""
    env = request.getfixturevalue("served_graph_env")
    harness, state, workspace = env["harness"], env["state"], env["workspace"]
    posts = _count_posts(monkeypatch, env["server"])
    action_id = _approval_id(_turn(harness, 'propose "Transformer oil sample" on 2026-09-16 10:00 Europe/Berlin for 30m', workspace))

    monkeypatch.setenv("VOOL_CALENDAR_TIMEOUT", "1")
    state.hang_seconds = 3.0  # the service stores the create, then withholds its reply
    try:
        unknown = _turn(harness, f"approve calendar {action_id}", workspace)
    finally:
        state.hang_seconds = 0.0
        monkeypatch.setenv("VOOL_CALENDAR_TIMEOUT", "10")
    assert "could not be proven" in unknown, unknown
    stored = _graph_titled(state, "Transformer oil sample")
    assert len(stored) == 1 and len(posts) == 1

    broken = _serve_unreadable(monkeypatch, state, set(stored))
    unreadable = _turn(harness, f"approve calendar {action_id}", workspace)
    assert "remains unproven" in unreadable and "created on the provider" not in unreadable, unreadable
    assert len(posts) == 1 and _approval_row(harness.session_id, action_id)["status"] == "outcome_unproven"

    broken.clear()
    recovered = _turn(harness, f"approve calendar {action_id}", workspace)
    assert "created on the provider and verified" in recovered, recovered
    assert len(posts) == 1 and _graph_titled(state, "Transformer oil sample") == stored
    assert _approval_row(harness.session_id, action_id)["status"] == "executed"
