"""pa_beta_gate -- revision-5 served conversational journeys through the production entry path.

Every step is a REAL agent turn (``VoolAgent.run_once``): served ingress, routing, demand ownership,
operator dispatch, the approval pause and its resume. MODEL STAND-IN (labelled): the model lane is the
same deterministic stand-in the existing served workflow tests use, so these journeys prove routing,
effects, persistence and recovery -- not model wording. Calendar effects execute against the
disposable CalDAV service (a local protocol fixture, not a live account); Apple Notes goes through an
injected runner double and never runs osascript.

The later tests pin the three served-path defects the journeys exposed on their first run
(evidence/served-journeys-01.log): a note body persisted in the routing normalizer's rewrite of the
user's words, a chosen slot proposed at the default 30 minutes instead of its own length, and a
"save a note ..." request kept as a memory before the notes lane could see it -- plus the defect the
second run exposed once that request reached the notes lane (evidence/served-journeys-02.log): a
refused Apple Notes delivery whose fallback note WAS written had its whole answer replaced by the
final claim guard, because the step was filed as a failed tool.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from tests.pa_beta_gate.test_served_calendar_notes_workflows import (  # noqa: F401 -- served_env is a fixture, requested via getfixturevalue
    VILNIUS_CAL,
    _approval_id,
    _turn,
    served_env,
)


def _events(state) -> dict:
    return state.snapshot()[VILNIUS_CAL]


def _titled(state, title: str) -> list[str]:
    return [uid for uid, row in _events(state).items() if row["summary"] == title]


def _row(session: str, kind: str, action_id: str) -> dict:
    from core.operator.calendar_provider import load_action_any_state

    return load_action_any_state(session_id=session, action_kind=kind, action_id=action_id)


def _note_texts(workspace) -> list[str]:
    return [path.read_text(encoding="utf-8") for path in (Path(workspace) / "notes").glob("*.md")]


def test_served_original_journey_note_sourced_proposal_review_link_inspect_rename_and_replay(request):
    env = request.getfixturevalue("served_env")
    harness, state, workspace = env["harness"], env["state"], env["workspace"]
    session = harness.session_id
    state.seed_event(VILNIUS_CAL, "busy-sync@fixture", summary="Standing sync", start=datetime(2026, 9, 15, 11, 0, tzinfo=timezone.utc), minutes=60)

    first = _turn(harness, 'Check Tuesday afternoon for a free 30-minute slot and save a note titled "Supplier review prep" with: '
                           "[action] supplier review Tuesday at 16:00 Europe/Berlin", workspace)
    assert "Standing sync" in first and "Free 30-minute options" in first, first
    note_files = [path for path in (workspace / "notes").glob("*.md") if "supplier review Tuesday" in path.read_text(encoding="utf-8")]
    assert len(note_files) == 1, first

    proposal = _turn(harness, 'schedule the action "supplier review" from my note', workspace)
    action_id = _approval_id(proposal)
    assert "Title: supplier review" in proposal and "Duration: 30 minutes" in proposal and "Attendees: none" in proposal, proposal
    assert _titled(state, "supplier review") == [], "a reviewed proposal is not an event"
    scope = json.loads(_row(session, "provider_calendar_event", action_id)["scope_json"])
    assert (scope["provider"], scope["calendar_id"], scope["start_utc"]) == ("caldav", VILNIUS_CAL, "2026-09-15T13:00:00+00:00")
    assert scope["provider_url"] and scope["note_path"] == str(note_files[0])

    executed = _turn(harness, f"approve calendar {action_id}", workspace)
    assert "created on the provider and verified" in executed and "Linked note" in executed, executed
    (uid,) = _titled(state, "supplier review")
    assert f"linked_event_uid: {uid}" in note_files[0].read_text(encoding="utf-8")
    receipt = json.loads(_row(session, "provider_calendar_event", action_id)["result_json"])
    assert (receipt["uid"], receipt["calendar_id"], receipt["start_utc"]) == (uid, VILNIUS_CAL, "2026-09-15T13:00:00+00:00")

    inspected = _turn(harness, 'show the "supplier review" event', workspace)
    assert "Event: supplier review" in inspected and f"uid {uid}" in inspected, inspected

    rename = _turn(harness, 'rename the "supplier review" event to "Supplier review (final)"', workspace)
    rename_id = _approval_id(rename)
    renamed = _turn(harness, f"approve calendar {rename_id}", workspace)
    assert "Event updated on the provider and verified" in renamed, renamed
    assert _titled(state, "Supplier review (final)") == [uid] and _titled(state, "supplier review") == []

    replayed = _turn(harness, f"approve calendar {rename_id}", workspace)
    assert "Nothing was changed a second time" in replayed, replayed
    assert len([row for row in _events(state).values() if row["summary"].lower().startswith("supplier review")]) == 1

    from core.persistent_memory import recent_conversation_events

    history = " ".join(str(event) for event in recent_conversation_events(session, limit=20))
    assert "rename the" in history and f"approve calendar {rename_id}" in history, "served turns must land in session history"


def test_served_novel_journey_withheld_reply_recovery_then_cancel_keeps_the_note(request, monkeypatch):
    env = request.getfixturevalue("served_env")
    harness, state, workspace = env["harness"], env["state"], env["workspace"]
    session = harness.session_id
    state.seed_event(VILNIUS_CAL, "demo@fixture", summary="Team demo", start=datetime(2026, 9, 17, 6, 30, tzinfo=timezone.utc), minutes=90)

    first = _turn(harness, "Look at Thursday morning on my calendar for a free 20-minute slot and save a note with agenda: generator load test", workspace)
    assert "Team demo" in first and "Free 20-minute options" in first, first

    proposal = _turn(harness, 'option 2, propose "Generator load test"', workspace)
    action_id = _approval_id(proposal)
    assert "Title: Generator load test" in proposal and "Duration: 20 minutes" in proposal, proposal

    monkeypatch.setenv("VOOL_CALENDAR_TIMEOUT", "1")
    state.hang_seconds = 3.0  # the service applies the create, then withholds its reply
    try:
        unknown = _turn(harness, f"approve calendar {action_id}", workspace)
    finally:
        state.hang_seconds = 0.0
        monkeypatch.setenv("VOOL_CALENDAR_TIMEOUT", "10")
    assert "could not be proven" in unknown and "NOT retried" in unknown, unknown
    assert _row(session, "provider_calendar_event", action_id)["status"] == "outcome_unproven"
    assert len(_titled(state, "Generator load test")) == 1, "the withheld create landed"

    recovered = _turn(harness, f"approve calendar {action_id}", workspace)
    assert "created on the provider and verified" in recovered and "nothing was duplicated" in recovered, recovered
    assert len(_titled(state, "Generator load test")) == 1

    cancel = _turn(harness, 'cancel the "Generator load test" event', workspace)
    cancelled = _turn(harness, f"approve calendar {_approval_id(cancel)}", workspace)
    assert "verified gone" in cancelled, cancelled
    assert _titled(state, "Generator load test") == [] and _titled(state, "Team demo") == ["demo@fixture"]
    assert any("generator load test" in path.read_text(encoding="utf-8") for path in (workspace / "notes").glob("*.md"))


def test_served_apple_notes_denied_then_new_request_after_consent_creates_exactly_one_native_note(request, monkeypatch):
    env = request.getfixturevalue("served_env")
    harness, workspace = env["harness"], env["workspace"]
    from core.operator import apple_notes

    calls = []
    real_create = apple_notes.create_apple_note

    def runner(command, **kwargs):  # LABELLED native double: never osascript
        calls.append(command)
        if len(calls) == 1:
            return SimpleNamespace(returncode=1, stdout="", stderr="execution error: Not authorized to send Apple events to Notes. (-1743)")
        return SimpleNamespace(returncode=0, stdout="note id x-coredata://served/p42", stderr="")

    monkeypatch.setattr(apple_notes, "create_apple_note", lambda **kw: real_create(**kw, runner=runner))
    text = 'save a note to Apple Notes titled "Night shift handover" with: boiler pressure steady at 1.4 bar'
    denied = _turn(harness, text, workspace)
    assert denied.startswith("Note saved: 'Night shift handover' at "), denied
    assert "destination is NOT resolved (os_permission_denied)" in denied and "a new request makes one new attempt" in denied, denied
    fallback = [path for path in (workspace / "notes").glob("*.md") if "1.4 bar" in path.read_text(encoding="utf-8")]
    assert len(fallback) == 1 and len(calls) == 1

    created = _turn(harness, text, workspace)
    assert "Note saved in Apple Notes: 'Night shift handover'" in created, created
    assert len(calls) == 2
    journal = json.loads((workspace / "notes" / ".apple-notes-effects.json").read_text(encoding="utf-8"))
    assert sorted(op["state"] for op in journal["operations"].values()) == ["confirmed", "refused"]
    assert len([path for path in Path(workspace / "notes").glob("*.md") if "1.4 bar" in path.read_text(encoding="utf-8")]) == 1


def test_served_note_body_is_persisted_in_the_users_own_words_and_its_action_still_schedules(request):
    env = request.getfixturevalue("served_env")
    harness, workspace = env["harness"], env["workspace"]
    session = harness.session_id
    body = "[action] ask u to bring the north star gauge Friday at 11:00 Europe/Berlin"

    saved = _turn(harness, f'save a note titled "Crane lift plan" with: {body}', workspace)
    assert saved.startswith("Note saved: 'Crane lift plan' at "), saved
    notes = [path for path in (workspace / "notes").glob("*.md") if "gauge" in path.read_text(encoding="utf-8")]
    assert len(notes) == 1 and body in notes[0].read_text(encoding="utf-8"), (saved, _note_texts(workspace))

    proposal = _turn(harness, 'schedule the action "ask u to bring the north star gauge" from my note', workspace)
    action_id = _approval_id(proposal)
    assert "Title: ask u to bring the north star gauge" in proposal, proposal
    scope = json.loads(_row(session, "provider_calendar_event", action_id)["scope_json"])
    assert (scope["title"], scope["start_utc"], scope["note_path"]) == (
        "ask u to bring the north star gauge", "2026-09-11T08:00:00+00:00", str(notes[0]))


def test_served_withdrawn_clause_never_reaches_the_saved_note(request):
    env = request.getfixturevalue("served_env")
    harness, workspace = env["harness"], env["workspace"]
    answer = _turn(harness, 'save a note titled "Draft A" with: first idea for the quay. '
                            'scratch that, create a note titled "Draft B" with: second idea for the quay', workspace)
    assert answer.startswith("Note saved: 'Draft B' at "), answer
    texts = _note_texts(workspace)
    assert len([text for text in texts if "second idea for the quay" in text]) == 1, (answer, texts)
    assert not [text for text in texts if "first idea" in text or "Draft A" in text], texts


def test_served_chosen_slot_length_becomes_the_duration_unless_the_request_names_one(request):
    env = request.getfixturevalue("served_env")
    harness, state, workspace = env["harness"], env["state"], env["workspace"]
    session = harness.session_id
    state.seed_event(VILNIUS_CAL, "drill@fixture", summary="Pilot boarding drill", start=datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc), minutes=60)

    offer = _turn(harness, "Check Wednesday afternoon for a free 45-minute slot", workspace)
    assert "Pilot boarding drill" in offer and "Free 45-minute options" in offer, offer
    named = _turn(harness, 'option 2, propose "Quay walk" for 15 minutes', workspace)
    assert "Title: Quay walk" in named and "Duration: 15 minutes" in named, named

    offer_again = _turn(harness, "Check Wednesday afternoon for a free 45-minute slot", workspace)
    assert "Free 45-minute options" in offer_again, offer_again
    chosen = _turn(harness, 'option 1, propose "Harbour crane inspection"', workspace)
    action_id = _approval_id(chosen)
    assert "Title: Harbour crane inspection" in chosen and "Duration: 45 minutes" in chosen, chosen
    scope = json.loads(_row(session, "provider_calendar_event", action_id)["scope_json"])
    assert datetime.fromisoformat(scope["end_utc"]) - datetime.fromisoformat(scope["start_utc"]) == timedelta(minutes=45)
    assert _titled(state, "Harbour crane inspection") == [], "a proposal is not an event"


def test_served_storage_verb_note_is_a_note_while_a_statement_about_notes_stays_a_memory(request):
    env = request.getfixturevalue("served_env")
    harness, workspace = env["harness"], env["workspace"]

    stored = _turn(harness, 'store a note titled "Pump room" with: [check] valve {B} torque 45 Nm', workspace)
    assert stored.startswith("Note saved: 'Pump room' at "), stored
    assert [text for text in _note_texts(workspace) if "[check] valve {B} torque 45 Nm" in text], (stored, _note_texts(workspace))
    assert "remember" not in stored.lower(), stored

    statement = _turn(harness, "remember that I save a note for the landlord every Friday", workspace)
    assert statement.startswith(("Locked in.", "I already had that in memory.")), statement
    assert not [text for text in _note_texts(workspace) if "landlord" in text]


def test_served_unaddressable_notes_app_still_answers_with_the_fallback_it_wrote(request, monkeypatch):
    env = request.getfixturevalue("served_env")
    harness, workspace = env["harness"], env["workspace"]
    from core.operator import apple_notes

    calls = []
    real_create = apple_notes.create_apple_note

    def runner(command, **kwargs):  # LABELLED native double: never osascript
        calls.append(command)
        return SimpleNamespace(returncode=1, stdout="", stderr="execution error: Notes got an error: Application can't be found. (-2700)")

    monkeypatch.setattr(apple_notes, "create_apple_note", lambda **kw: real_create(**kw, runner=runner))
    answer = _turn(harness, 'save a note to Apple Notes titled "Crane roster" with: rigger shift swap on berth 7', workspace)
    assert answer.startswith("Note saved: 'Crane roster' at "), answer
    assert "destination is NOT resolved (notes_app_unavailable)" in answer and "I cannot verify" not in answer, answer
    assert len([text for text in _note_texts(workspace) if "rigger shift swap on berth 7" in text]) == 1 and len(calls) == 1
